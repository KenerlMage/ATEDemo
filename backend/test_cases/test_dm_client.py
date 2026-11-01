# -*- coding: utf-8 -*-
"""装备管理模块（DM）回归用例

单独运行: python -m pytest test_cases/test_dm_client.py -v
覆盖：协议常量、数据面描述符解析、broker 生命周期、连接/调用/数据面/句柄回收。
会在本机起一个 broker 子进程（唯一测试台名 + 小共享内存块），跑完关闭。
"""

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
SDK = BACKEND.parent / "sdk"
for _p in (str(BACKEND), str(SDK)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ate_dm import DMClient, DmError, open_proxy          # noqa: E402
from ate_dm import protocol as proto                     # noqa: E402
from ate_dm.shm import HEADER, DTYPE_MAP, StreamData     # noqa: E402

SCOPE = {"device_id": "scope-test", "alias": "scope", "name": "数字示波器", "model": "MSO54",
         "category": "instrument", "role": "scope", "device_type": "tektronix-mso5",
         "interface": "LAN", "host": "127.0.0.1", "port": 5025}


# ---------------- 纯单元（不依赖进程）----------------


def test_protocol_shapes():
    """控制面帧格式：请求带 id/op/args，错误带 code/message"""
    raw = proto.make_request(7, "call", handle=1, action="identify")
    msg = proto.loads(raw)
    assert msg == {"id": 7, "op": "call", "args": {"handle": 1, "action": "identify"}}

    ok = proto.loads(proto.make_ok(7, {"value": 1}))
    assert ok["ok"] is True and ok["result"] == {"value": 1}

    err = proto.loads(proto.make_err(7, proto.ERR_NO_HANDLE, "句柄无效"))
    assert err["ok"] is False and err["error"]["code"] == proto.ERR_NO_HANDLE


def test_shm_header_layout():
    """共享内存块头 24 字节：magic + seq + nbytes + crc32"""
    assert HEADER.size == 24
    packed = HEADER.pack(b"ATEDM001", 9, 1024, 12345)
    magic, seq, nbytes, crc = HEADER.unpack(packed)
    assert magic == b"ATEDM001" and seq == 9 and nbytes == 1024 and crc == 12345


def test_stream_data_decodes_dtypes():
    """同一段字节按不同 dtype 解出的数值与个数"""
    import array
    buf = array.array("f", [1.5, -2.25, 0.0]).tobytes()
    sd = StreamData(buf, "f", [3])
    assert sd.count == 3 and sd.values() == [1.5, -2.25, 0.0]
    assert StreamData(b"\x01\x02\x03", "B", [3]).values() == [1, 2, 3]
    assert set("fdilBh") <= set(DTYPE_MAP)


def test_proxy_rejects_unknown_dtype_in_client():
    """客户端对未支持的 dtype 由服务端拒绝（此处只验常量覆盖）"""
    assert "q" not in DTYPE_MAP


# ---------------- broker 集成 ----------------


@pytest.fixture(scope="module")
def broker(tmp_path_factory):
    root = tmp_path_factory.mktemp("dm")
    ep = root / "dm_endpoint.json"
    bench = "pytest" + uuid.uuid4().hex[:8]
    proc = subprocess.Popen(
        [sys.executable, "-m", "dm_broker", "--bench", bench, "--endpoint", str(ep),
         "--log", str(root / "req.jsonl"), "--slots", "4", "--block", str(512 * 1024)],
        cwd=str(BACKEND), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    dm = None
    for _ in range(60):
        if ep.exists():
            try:
                dm = DMClient(str(ep), timeout=10, name="pytest")
                break
            except Exception:
                pass
        time.sleep(0.25)
    if dm is None:
        proc.kill()
        pytest.skip("broker 未能启动（本机管道/权限受限）")
    yield {"dm": dm, "proc": proc, "endpoint": ep}
    try:
        dm.close()
    except Exception:
        pass
    if proc.poll() is None:
        proc.kill()


def test_broker_handshake_and_caps(broker):
    dm = broker["dm"]
    assert dm.ping()["pong"] is True
    caps = dm.caps()
    assert caps["protocol"] == proto.PROTOCOL_VERSION
    assert caps["slots"] == 4 and caps["max_block"] > 0
    assert "stream" in caps["ops"]


def test_connect_and_call(broker):
    dm = broker["dm"]
    h = dm.connect(SCOPE, alias="scope", mode="simulate")
    assert h.handle > 0 and h.driver == "tektronix-mso5"
    out = dm.call(h, "identify")
    assert out.get("value")
    meas = dm.call(h, "measure", items=["FREQUENCY"])
    first = (meas.get("value") or [{}])[0]
    assert first.get("type") == "FREQUENCY" and first.get("unit") == "Hz"


def test_stream_uses_shared_memory(broker):
    dm = broker["dm"]
    h = dm.connect(SCOPE, alias="scope-stream", mode="simulate")
    wave = dm.stream(h, "waveform", dtype="f", points=95000)
    assert wave.count == 95000 and wave.nbytes == 380000
    vals = wave.values()
    assert len(vals) == 95000 and any(v != 0 for v in vals)
    assert dm.devices()


def test_stream_rejects_oversize(broker):
    dm = broker["dm"]
    h = dm.connect(SCOPE, alias="scope-big", mode="simulate")
    with pytest.raises(DmError) as ei:
        dm.stream(h, "waveform", dtype="d", points=100000)      # 800 KB > 512 KB 槽
    assert ei.value.code == proto.ERR_TOO_LARGE


def test_invalid_handle_and_action(broker):
    dm = broker["dm"]
    with pytest.raises(DmError) as ei:
        dm.call(999999, "identify")
    assert ei.value.code == proto.ERR_NO_HANDLE

    h = dm.connect(SCOPE, alias="scope-err", mode="simulate")
    with pytest.raises(DmError) as ei2:
        dm.call(h, "no_such_action")
    assert ei2.value.code == proto.ERR_DRIVER


def test_refcount_and_disconnect(broker):
    dm = broker["dm"]
    a = dm.connect(SCOPE, alias="scope-ref", mode="simulate")
    b = dm.connect(SCOPE, alias="scope-ref", mode="simulate")
    slot = [d for d in dm.devices() if d["key"] == "scope-ref"][0]
    assert slot["refs"] == 2 and sorted(slot["handles"]) == sorted([a.handle, b.handle])

    dm.disconnect(a)
    slot = [d for d in dm.devices() if d["key"] == "scope-ref"][0]
    assert slot["refs"] == 1
    dm.disconnect(b)
    assert "scope-ref" not in [d["key"] for d in dm.devices()]


def test_proxy_keeps_driver_usage_style(broker):
    proxy = open_proxy(SCOPE, alias="scope-proxy", mode="simulate", dm=broker["dm"])
    out = proxy.do("measure", items=["PK2PK"])
    assert isinstance(out.get("value"), (list, dict))
    assert int(proxy) > 0
    assert proxy.close()["closed"] is True
    assert proxy.close()["closed"] is True          # 幂等


def test_stats_and_log(broker):
    dm = broker["dm"]
    st = dm.stats()
    assert st["requests"] > 0 and st["protocol"] == proto.PROTOCOL_VERSION
    assert st["ring"]["slots"] == 4
    lines = dm.log(10)
    assert lines and lines[-1]["op"]


def test_shutdown_closes_broker(broker):
    dm, proc, ep = broker["dm"], broker["proc"], broker["endpoint"]
    dm.shutdown()
    dm.close()
    for _ in range(40):
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    assert proc.poll() is not None
    assert not ep.exists()
