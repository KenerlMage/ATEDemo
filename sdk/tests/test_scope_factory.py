# -*- coding: utf-8 -*-
"""网口示波器：**型号派发 + 通用顶层接口** 验收用例。

四件事：① 型号是精确派发（不猜、不兜底、不打分）；② 网口约束（真机必须 host/port）；
③ 用户感知不到任何原始命令；④ 两家同一段用例结构一致、下发的命令流确实不同。
"""

from __future__ import annotations

import json
import re

import pytest

from ate_drivers import Scope, factory, open_scope
from ate_drivers.errors import ConfigurationError, DriverError
from ate_drivers.kit import run_conformance
from ate_drivers.transport import VisaTransport, make_transport
from ate_drivers.vendors.rohde_schwarz_mxo.driver import RsMxoScope
from ate_drivers.vendors.tektronix_mso.driver import TekMsoScope

TEK_MODEL, TEK_HOST, TEK_PORT = "MSO54", "192.168.10.41", 4000
RS_MODEL, RS_HOST, RS_PORT = "MXO44", "192.168.10.51", 5025

# 不该出现在公开面上的命令入口（门面只给业务方法）
RAW_ENTRY_POINTS = ("do", "write", "query", "read", "cmd", "simulate_command",
                    "log_lines", "protect_on_close", "COMMANDS")
# 不该出现在顶层接口源码/公开数据里的命令字样
RAW_TOKENS = (":ACQuire", ":WFMOutpre", ":CURVe", ":HORizontal", ":TIMebase",
              ":CHANnel", "*IDN?", ":DATa", "FORMat")


class RecordingTransport:
    """记录下发的命令，并用「同型号仿真驱动」的答复回答（真机换成 Socket 即可）"""

    kind = "record"

    def __init__(self, handler):
        self.handler = handler
        self.sent: list = []
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    def describe(self) -> dict:
        return {"kind": self.kind, "resource": "recording", "opened": self.opened,
                "count": len(self.sent)}

    def write(self, command: str, timeout=None) -> None:
        self.sent.append(command)

    def query(self, command: str, timeout=None) -> str:
        self.sent.append(command)
        return self.handler(command)


def _recorder(model: str, idn: str = "") -> RecordingTransport:
    """造一个记录型网口端点；默认用同型号仿真应答，可指定回读的 IDN（测型号核对）"""
    sim = open_scope(model)
    if idn:
        return RecordingTransport(lambda cmd: idn if cmd.strip().endswith("?") and "IDN" in cmd.upper()
                                 else sim._driver.simulate_command(cmd))
    return RecordingTransport(lambda cmd: sim._driver.simulate_command(cmd))


def _lan(model: str, host: str, port: int, **kw) -> Scope:
    """按设备档案连一台网口示波器（真机链路用记录型端点替代 socket）"""
    return open_scope(model, host=host, port=port, transport=_recorder(model), **kw)


def run_use_case(scope: Scope) -> dict:
    """一段「现场通用」用例：只用顶层业务方法，换型号不改一行"""
    scope.connect()
    out = {"identity": scope.identity()}
    scope.auto_setup(freq_hz=1000.0, volts_pp=2.4)
    scope.set_timebase(seconds_per_div=5e-4)
    scope.set_channel("CH1", volts_per_div=0.5, coupling="DC")
    wave = scope.capture(points=1000, channel="CH1")
    meas = scope.measure(("PK2PK", "FREQUENCY", "MEAN", "RMS", "RISE"), channel="CH1")
    out.update({
        "wave_keys": sorted(wave.keys()), "meas_keys": sorted(meas.keys()),
        "wave_unit": wave["unit"], "points": wave["points"],
        "x_incr_s": wave["seconds_per_sample"], "volts": len(wave["volts"]),
        "meas": [(k, meas[k]["unit"]) for k in sorted(meas)],
        "freq_hz": meas["FREQUENCY"]["value"],
        "run": scope.run()["ok"], "single": scope.single()["ok"], "stop": scope.stop()["ok"],
        "running": scope.status()["running"],
    })
    scope.close()
    return out


# ---------------------------------------------------------------- ① 型号派发（无打分）

def test_registry_dispatches_by_exact_model():
    assert "MSO54" in factory.models() and "MXO44" in factory.models()
    assert factory.driver_class("MSO54") is TekMsoScope
    assert factory.driver_class(" mso-54 ") is TekMsoScope        # 大小写/分隔符归一化
    assert factory.driver_class("MXO4") is RsMxoScope             # 系列俗称作为别名登记
    assert factory.driver_class("mxo44") is RsMxoScope
    assert factory.entry_for("MXO44")["model"] == "MXO44"         # 拿回官方型号名
    for e in factory.entries():
        assert e["model"] and e["interface"] == "LAN"
        assert e["driver"] in ("tektronix-mso5", "rohde-schwarz-mxo4")
    assert factory.self_check()["ok"], factory.self_check()["problems"]


def test_scoring_mechanism_is_gone():
    """打分选择机制已整体删除：没有权重、没有模糊匹配、没有兜底驱动"""
    for name in ("score_spec", "WEIGHTS", "SCOPE_SPECS", "GenericScope", "resolve_spec"):
        assert not hasattr(factory, name), name
    assert "generic-scope" not in factory.models()


def test_unknown_model_errors_with_available_list():
    with pytest.raises(DriverError) as err:
        open_scope("MSO99-不存在")
    assert err.value.code == "E_NOT_FOUND"
    assert "MSO54" in err.value.detail and "MXO44" in err.value.detail
    with pytest.raises(DriverError):
        open_scope("")


# ---------------------------------------------------------------- ② 网口约束

def test_lan_endpoint_required_in_real_mode():
    with pytest.raises(ConfigurationError) as err:
        open_scope(TEK_MODEL, mode="real")
    assert err.value.code == "E_CONFIG"
    with pytest.raises(ConfigurationError):
        open_scope(TEK_MODEL, host=TEK_HOST)        # 少了 port 也不行
    with pytest.raises(ConfigurationError):
        open_scope(TEK_MODEL, port=TEK_PORT)


def test_mode_auto_and_endpoint():
    assert open_scope(TEK_MODEL).mode == "simulate"                    # 无网口端点 → 仿真
    real = _lan(TEK_MODEL, TEK_HOST, TEK_PORT)
    assert real.mode == "real" and real.endpoint == "%s:%d" % (TEK_HOST, TEK_PORT)
    assert open_scope(TEK_MODEL, mode="simulate", host=TEK_HOST, port=TEK_PORT).simulated is True


def test_device_row_from_database_drives_dispatch():
    """型号来自数据库设备档案：dict 或对象都能直接传"""
    row = {"device_id": "SCOPE-TEK", "name": "SPM 读头动态测试台/示波器",
           "model": TEK_MODEL, "host": TEK_HOST, "port": TEK_PORT}
    scope = open_scope(device=row, transport=_recorder(TEK_MODEL))
    assert scope.model == TEK_MODEL and scope.endpoint == "%s:%d" % (TEK_HOST, TEK_PORT)

    class Device:  # noqa: D401  (模拟 ORM 行对象)
        model, host, port = RS_MODEL, RS_HOST, RS_PORT
        device_id, name = "SCOPE-RS", "R&S 示波器"

    other = open_scope(device=Device(), transport=_recorder(RS_MODEL))
    assert other.model == RS_MODEL and other.vendor == "Rohde & Schwarz"


# ---------------------------------------------------------------- ③ 命令不外露

def test_facade_hides_raw_commands():
    src = factory.__dict__.get("__doc__", "") + ""
    import ate_drivers.api as api_mod

    text = open(api_mod.__file__, encoding="utf-8").read()
    for token in RAW_TOKENS:
        assert token not in text, "%s 出现在顶层接口源码里（命令必须关在厂商包内）" % token
    for token in RAW_ENTRY_POINTS:
        assert not hasattr(Scope, token), "顶层对象不该暴露 %s" % token
    for method in ("connect", "close", "identity", "auto_setup", "set_timebase", "set_channel",
                   "configure", "capture", "measure", "run", "stop", "single", "status", "self_test"):
        assert callable(getattr(Scope, method)), method
    assert src  # 模块文档存在（不依赖具体文案）


def test_public_results_carry_no_command_text():
    scope = _lan(TEK_MODEL, TEK_HOST, TEK_PORT)
    scope.connect()
    blob = json.dumps({"status": scope.status(), "identity": scope.identity(),
                       "self_test": scope.self_test(), "metadata": scope.metadata()},
                      ensure_ascii=False, default=str)
    for token in RAW_TOKENS:
        assert token not in blob, token
    scope.close()


# ---------------------------------------------------------------- ④ 两家同一段用例

def test_same_use_case_same_schema_on_both_models():
    tek = run_use_case(_lan(TEK_MODEL, TEK_HOST, TEK_PORT))
    rs = run_use_case(_lan(RS_MODEL, RS_HOST, RS_PORT))

    for field in ("wave_keys", "meas_keys", "meas"):
        assert tek[field] == rs[field], field
    assert tek["meas"] == [("FREQUENCY", "Hz"), ("MEAN", "V"), ("PK2PK", "V"),
                           ("RISE", "s"), ("RMS", "V")]
    assert tek["wave_unit"] == rs["wave_unit"] == "V"
    # 记录型端点回的是各家固定帧（泰克 64 点、R&S 1000 点），所以这里只比结构与非空；
    # “点数是否听从请求”由仿真链路用例 test_capture_honours_points_in_simulate 保证
    assert tek["points"] > 0 and rs["points"] > 0
    assert tek["volts"] > 0 and rs["volts"] > 0
    assert tek["x_incr_s"] > 0 and rs["x_incr_s"] > 0
    for t in (tek, rs):
        # 记录型端点回的是各家固定帧（点数与请求窗口不一致），频值不作物理判据；
        # “同一物理结论（~1 kHz）”由仿真链路用例 test_capture_honours_points_in_simulate 验证
        assert t["freq_hz"] > 0
        assert t["run"] and t["single"] and t["stop"]        # 三条采集控制都执行成功
        assert t["running"] is False                        # 停采后状态为未运行
    assert tek["identity"]["model"] == "MSO54"
    assert rs["identity"]["model"] == "MXO44"
    assert tek["identity"]["vendor"] != rs["identity"]["vendor"]


def test_dispatch_is_deterministic():
    assert run_use_case(_lan(TEK_MODEL, TEK_HOST, TEK_PORT)) == run_use_case(_lan(TEK_MODEL, TEK_HOST, TEK_PORT))
    assert run_use_case(_lan(RS_MODEL, RS_HOST, RS_PORT)) == run_use_case(_lan(RS_MODEL, RS_HOST, RS_PORT))


def test_dialect_command_streams_differ():
    """结构一致不等于命令一致：两家的命令流必须各不相同，且互不出现对方命令"""
    stream = {}
    for tag, (model, host, port) in (("tek", (TEK_MODEL, TEK_HOST, TEK_PORT)),
                                     ("rs", (RS_MODEL, RS_HOST, RS_PORT))):
        rec = _recorder(model)
        run_use_case(open_scope(model, host=host, port=port, transport=rec))
        stream[tag] = " | ".join(rec.sent).upper()

    tek, rs = stream["tek"], stream["rs"]
    for token in (":HORIZONTAL:MAIN:SCALE", ":WFMOUTPRE?", ":CURVE?", ":DATA:SOURCE"):
        assert token in tek, token
    for token in (":TIMEBASE:SCALE", ":CHANNEL1:DATA:HEADER?", ":CHANNEL1:DATA?", "RUN"):
        assert token in rs, token
    for token in (":WFMOUTPRE", ":CURVE", ":DATA:SOURCE", ":HORIZONTAL"):
        assert token not in rs, token
    for token in (":TIMEBASE", ":CHANNEL1:DATA"):
        assert token not in tek, token


def test_model_verification_catches_wrong_device():
    """档案写 MSO54、实际连上 R&S：必须报出来，而不是默默按泰克命令去问"""
    wrong = _recorder(TEK_MODEL, idn="Rohde&Schwarz,MXO44,SIM0001,6.00")
    scope = open_scope(TEK_MODEL, host=TEK_HOST, port=TEK_PORT, transport=wrong)
    with pytest.raises(ConfigurationError) as err:
        scope.connect()
    assert "不符" in str(err.value)
    scope.close()
    ok = open_scope(TEK_MODEL, host=TEK_HOST, port=TEK_PORT, transport=wrong, verify_model=False)
    assert ok.connect()["connected"] is True        # 明确关掉校验才允许
    ok.close()


# ---------------------------------------------------------------- 顶层方法细节

def test_capture_and_measure_details():
    scope = _lan(RS_MODEL, RS_HOST, RS_PORT)
    scope.connect()
    wave = scope.capture(points=500, channel="CH1")
    assert wave["points"] == 500 and len(wave["volts"]) == 500
    assert all(isinstance(v, float) for v in wave["volts"][:10])
    assert wave["unit"] == "V" and wave["first_sample_seconds"] is not None
    meas = scope.measure(("VPP", "频率"))           # 别名与中文
    assert set(meas) == {"PK2PK", "FREQUENCY"}
    assert meas["PK2PK"]["unit"] == "V"
    assert scope.supported_measurements()[:2] == ("PK2PK", "AMPLITUDE")
    scope.close()


def test_context_manager_and_self_test():
    with _lan(TEK_MODEL, TEK_HOST, TEK_PORT) as scope:
        report = scope.self_test()
        assert report["ok"], report
        assert [c["name"] for c in report["checks"]][:2] == ["会话已建立", "可用于采集"]
        assert report["identity"]["model"] == "MSO54"
        md = scope.metadata()
        assert md["model"] == "MSO54" and md["interface"] == "LAN" and md["contract_api"]
    assert scope.status()["connected"] is False     # 退出后连接已关闭


# ---------------------------------------------------------------- 门禁（两家 + 骨架）

@pytest.mark.parametrize("clazz", [TekMsoScope, RsMxoScope])
def test_vendor_packages_pass_gate(clazz):
    report = run_conformance(clazz)
    assert report["ok"], "\n".join("%s %s：%s" % (r["id"], r["title"], r["detail"])
                                   for r in report["checks"] if not r["ok"])
    assert report["total"] == 19
    assert any(r["id"] == "C17" and r["ok"] for r in report["checks"])
    assert any(r["id"] == "C18" and r["ok"] for r in report["checks"])
    assert any(r["id"] == "C19" and r["ok"] for r in report["checks"])


def test_capture_honours_points_in_simulate():
    """仿真链路（无网口端点）：请求多少点就返回多少点，两家一致"""
    for model in (TEK_MODEL, RS_MODEL):
        with open_scope(model) as scope:
            scope.auto_setup(freq_hz=1000.0, volts_pp=2.4)
            wave = scope.capture(points=500)
            assert wave["points"] == 500 and len(wave["volts"]) == 500, model
            assert wave["unit"] == "V" and wave["seconds_per_sample"] > 0
            assert wave["simulated"] is True
            freq = scope.measure(("FREQUENCY",))["FREQUENCY"]
            assert freq["unit"] == "Hz", model
            assert abs(freq["value"] - 1000.0) / 1000.0 < 0.05, model   # 同一物理结论：~1 kHz


# ================================================================== 连接方式：网口 / 串口
SERIAL_NAME = "COM6"


class FakeSerialPort:
    """最小串口替身（pyserial 的 Serial 同款接口）：按行收命令，问号命令给应答"""

    def __init__(self, port=None, baudrate=None, bytesize=None, parity=None, stopbits=None,
                 timeout=None):
        self.params = {"port": port, "baudrate": baudrate, "bytesize": bytesize,
                       "parity": parity, "stopbits": stopbits, "timeout": timeout}
        self.sent: list = []
        self.buf = b""
        self.closed = False
        self.answer = None          # 命令 -> 应答（测试里指向驱动的 simulate_command）

    def write(self, data: bytes) -> None:
        command = data.decode("ascii", "ignore").strip()
        self.sent.append(command)
        if self.answer is not None and command.endswith("?"):
            text = str(self.answer(command) or "0")
            self.buf += text.encode("ascii", "ignore") + b"\n"

    def read(self, size: int = 4096) -> bytes:
        chunk, self.buf = self.buf[:size], self.buf[size:]
        return chunk

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _serial_device(**over):
    device = {"device_id": "scope-serial", "name": "串口示波器", "model": TEK_MODEL,
              "serial_port": SERIAL_NAME, "baudrate": 115200}
    device.update(over)
    return device


def test_endpoint_resolves_lan_serial_and_none():
    from ate_drivers.endpoint import endpoint_from

    lan = endpoint_from({"host": TEK_HOST, "port": TEK_PORT, "protocol": "VXI-11"})
    assert lan.kind == "LAN" and lan.label == "%s:%d" % (TEK_HOST, TEK_PORT)
    ser = endpoint_from(_serial_device())
    assert ser.kind == "SERIAL" and ser.label == "COM6@115200,8N1"
    assert ser.describe()["serial_port"] == SERIAL_NAME and ser.bytesize == 8
    assert endpoint_from({"model": TEK_MODEL}) is None                 # 无端点 → 仿真
    assert endpoint_from({"serial_port": "ttyUSB0"}, interface="rs232").kind == "SERIAL"
    assert endpoint_from({"host": TEK_HOST, "port": TEK_PORT}, interface="NET").kind == "LAN"
    # 转档案后链路类型与 VISA 资源名都对
    cfg = ser.to_device_config(model=TEK_MODEL, vendor="Tektronix")
    assert cfg.transport_kind() == "serial" and cfg.resource == "ASRL::COM6::115200::INSTR"


def test_endpoint_rejects_half_lan_and_ambiguous_archive():
    from ate_drivers.endpoint import endpoint_from

    with pytest.raises(ConfigurationError) as e1:
        endpoint_from({"host": TEK_HOST})                              # 只给 host
    assert "成对" in str(e1.value)
    with pytest.raises(ConfigurationError) as e2:
        endpoint_from({"port": TEK_PORT})                              # 只给 port
    assert "成对" in str(e2.value)
    both = {"host": TEK_HOST, "port": TEK_PORT, "serial_port": SERIAL_NAME, "baudrate": 115200}
    with pytest.raises(ConfigurationError) as e3:
        endpoint_from(both)                                            # 两种都填又没写明 → 不猜
    assert "不明确" in str(e3.value)
    assert endpoint_from(both, interface="SERIAL").kind == "SERIAL"    # 写明就按用户说的走
    assert endpoint_from(both, interface="LAN").kind == "LAN"
    with pytest.raises(ConfigurationError):
        endpoint_from({"serial_port": SERIAL_NAME}, baudrate=-1)
    with pytest.raises(ConfigurationError):
        endpoint_from({"serial_port": ""}, interface="SERIAL")         # 串口名缺失


def test_serial_transport_via_injected_factory():
    from ate_drivers.endpoint import endpoint_from
    from ate_drivers.transport import SerialTransport, set_serial_factory

    cfg = endpoint_from(_serial_device()).to_device_config(model=TEK_MODEL)
    port = FakeSerialPort()

    def factory(**kw):
        port.params.update(kw)      # 假串口把 pyserial 同款参数接住，便于断言
        return port

    link = SerialTransport(cfg, port_factory=factory)
    port.answer = lambda cmd: "TEKTRONIX,MSO54,SER0001,1.2.3"
    link.open()
    assert port.params["baudrate"] == 115200 and port.params["port"] == SERIAL_NAME
    assert port.params["parity"] == "N" and port.params["bytesize"] == 8
    assert link.query("*IDN?").startswith("TEKTRONIX,MSO54")
    link.write("RUN")
    assert port.sent == ["*IDN?", "RUN"]
    assert link.describe()["baudrate"] == 115200
    link.close()
    assert port.closed is True
    # make_transport 也认串口（未装 pyserial 时靠注入的工厂照样跑）
    set_serial_factory(lambda **kw: FakeSerialPort(**kw))
    try:
        auto = make_transport(cfg, mode="real")
        assert auto.kind == "serial"
        auto.open()
        auto.write("STOP")
        auto.close()
    finally:
        set_serial_factory(None)


def test_serial_without_pyserial_gives_readable_error(monkeypatch):
    from ate_drivers import transport as tp
    from ate_drivers.endpoint import endpoint_from

    cfg = endpoint_from(_serial_device()).to_device_config(model=TEK_MODEL)
    monkeypatch.setattr(tp, "_pyserial", lambda: None)
    monkeypatch.setattr(tp, "DEFAULT_SERIAL_FACTORY", None)
    with pytest.raises(ConfigurationError) as e:
        tp.SerialTransport(cfg).open()
    assert "pyserial" in str(e.value)


def test_same_business_code_runs_over_serial():
    """同一段业务代码换到串口链路：身份 / 档位 / 跑停 / 状态照常，命令确实从串口出去"""
    from ate_drivers.endpoint import endpoint_from
    from ate_drivers.transport import SerialTransport

    ep = endpoint_from(_serial_device())
    cfg = ep.to_device_config(model=TEK_MODEL)
    port = FakeSerialPort()

    def factory(**kw):
        port.params.update(kw)
        return port

    link = SerialTransport(cfg, port_factory=factory)
    scope = Scope(TEK_MODEL, endpoint=ep, transport=link)
    port.answer = scope._driver.simulate_command      # 串口回环：收到的命令交给驱动仿真函数
    with scope:
        info = scope.identity()
        assert info["model"] == TEK_MODEL and info["serial"] == "SIM0001", info
        scope.set_timebase(seconds_per_div=5e-4)
        scope.set_channel("CH1", volts_per_div=0.5, coupling="DC")
        scope.run()
        scope.stop()
        st = scope.status()
        assert st["connected"] is True and st["running"] is False
        assert scope.self_test()["ok"] is True
        assert port.closed is False                     # 会话内链路还开着
    assert scope.interface == "SERIAL" and scope.interface_kind == "SERIAL"
    assert scope.endpoint == "COM6@115200,8N1"
    assert any("SCAle" in c for c in port.sent), port.sent      # 档位命令确实走串口
    assert port.closed is True
    # 元信息里能看出走的是哪条链路
    md = scope.metadata()
    assert md["interface_kind"] == "SERIAL" and md["endpoint_detail"]["baudrate"] == 115200


def test_real_mode_requires_connection_parameters():
    with pytest.raises(ConfigurationError) as e:
        Scope(TEK_MODEL, mode="real")
    assert "连接参数" in str(e.value)


def test_driver_without_serial_rejects_serial_endpoint():
    """驱动只声明 LAN 时，给串口端点必须被拦下（不能带病上产线）"""
    class LanOnly(TekMsoScope):
        driver_key = "lan-only-scope"
        INTERFACES = ("LAN",)
        MODELS = ("LAN-ONLY-1",)

    factory.register_driver(LanOnly, models=("LAN-ONLY-1",), replace=True)
    try:
        with pytest.raises(ConfigurationError) as e:
            Scope("LAN-ONLY-1", serial_port=SERIAL_NAME, baudrate=115200)
        assert "不支持" in str(e.value) and "SERIAL" in str(e.value)
        with Scope("LAN-ONLY-1", host=TEK_HOST, port=TEK_PORT, mode="simulate",
                   verify_model=False) as sc:
            assert sc.interface == "LAN"
    finally:
        factory.unregister_driver(LanOnly)


def test_gate_c18_flags_missing_connection_declaration():
    class NoIface(TekMsoScope):
        driver_key = "no-iface-scope"
        INTERFACES = ()
        INTERFACE = ""
        MODELS = ("NO-IFACE-1",)

    report = run_conformance(NoIface)
    assert report["total"] == 19
    c18 = [r for r in report["checks"] if r["id"] == "C18"][0]
    assert c18["ok"] is False and "未声明连接方式" in c18["detail"]


def test_gate_c18_flags_unknown_connection_kind():
    class UsbOnly(TekMsoScope):
        driver_key = "usb-only-scope"
        INTERFACES = ("USB",)
        MODELS = ("USB-ONLY-1",)

    report = run_conformance(UsbOnly)
    c18 = [r for r in report["checks"] if r["id"] == "C18"][0]
    assert c18["ok"] is False and "不在支持范围" in c18["detail"]


def test_manifest_accepts_interfaces_list_and_legacy_interface(tmp_path):
    from ate_drivers.kit import load_manifest

    base = {"schema": "ate.driver.manifest.v1", "key": "x-scope", "version": "0.0.1",
            "family": "scope", "api": "1.2", "entry": "x.driver:X", "models": ["X1"]}
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "c").mkdir()
    (tmp_path / "a" / "driver.json").write_text(
        json.dumps(dict(base, interfaces=["LAN", "SERIAL"])), encoding="utf-8")
    (tmp_path / "b" / "driver.json").write_text(
        json.dumps(dict(base, interface="SERIAL")), encoding="utf-8")
    (tmp_path / "c" / "driver.json").write_text(
        json.dumps(dict(base, interfaces=["USB"])), encoding="utf-8")
    m1 = load_manifest(str(tmp_path / "a"))
    assert m1["interfaces"] == ["LAN", "SERIAL"] and m1["interface"] == "LAN"
    assert load_manifest(str(tmp_path / "b"))["interfaces"] == ["SERIAL"]      # 兼容旧写法
    with pytest.raises(ConfigurationError):
        load_manifest(str(tmp_path / "c"))                                     # 非法连接方式


# ---------------------------------------------------------------- ⑤ 传输后端（native / visa）

class FakeVisaSession:
    """最小 VISA 会话替身（pyvisa 的 Resource 同款接口）：收命令、给应答、记属性"""

    def __init__(self, resource, timeout_s=2.0, attributes=None, handler=None):
        self.resource = str(resource)
        self.timeout = int(float(timeout_s) * 1000)
        self.attrs = dict(attributes or {})
        self.log: list = []
        self.queue: list = []
        self.closed = False
        self.handler = handler

    def write(self, command):
        self.log.append(str(command))
        if str(command).strip().endswith("?") and self.handler is not None:
            self.queue.append(str(self.handler(str(command))))

    def read(self):
        if not self.queue:
            raise TimeoutError("VISA 会话没有待读应答")
        return self.queue.pop(0)

    def close(self):
        self.closed = True


@pytest.fixture()
def visa_sessions(monkeypatch):
    """注入假 VISA 会话工厂（应答来自同型号仿真命令表），用例结束后自动复位"""
    import ate_drivers.transport as tp

    sim = open_scope(TEK_MODEL)
    handler = sim._driver.simulate_command
    made: list = []

    def factory(resource, timeout_s, attributes):
        session = FakeVisaSession(resource, timeout_s, attributes, handler=handler)
        made.append(session)
        return session

    monkeypatch.setattr(tp, "DEFAULT_VISA_FACTORY", factory)
    return made


def test_visa_resource_names_are_canonical():
    """VISA 资源名用规范写法：网口 TCPIP0::…::SOCKET、串口 ASRL6::INSTR"""
    from ate_drivers.endpoint import visa_resource_for

    assert visa_resource_for("LAN", "192.168.10.41", 4000) == "TCPIP0::192.168.10.41::4000::SOCKET"
    assert visa_resource_for("SERIAL", port_name="COM6") == "ASRL6::INSTR"
    assert visa_resource_for("SERIAL", port_name="COM12") == "ASRL12::INSTR"
    assert visa_resource_for("SERIAL", port_name="/dev/ttyUSB0") == "ASRL::/dev/ttyUSB0::INSTR"
    assert visa_resource_for("", "") == ""

    lan = open_scope(TEK_MODEL, host=TEK_HOST, port=TEK_PORT)
    assert lan.visa_resource == "TCPIP0::192.168.10.41::4000::SOCKET"
    ser = open_scope(TEK_MODEL, serial_port="COM6", baudrate=115200)
    assert ser.visa_resource == "ASRL6::INSTR"


def test_visa_backend_runs_the_same_use_case(visa_sessions):
    """换后端不换结果：同一段用例走 VISA 与走原生链路，返回与命令流必须逐项一致"""
    visa_scope = open_scope(TEK_MODEL, host=TEK_HOST, port=TEK_PORT, backend="visa")
    assert visa_scope.backend == "visa"
    visa_out = run_use_case(visa_scope)

    native_recorder = _recorder(TEK_MODEL)
    native_scope = open_scope(TEK_MODEL, host=TEK_HOST, port=TEK_PORT,
                              transport=native_recorder, backend="native")
    native_out = run_use_case(native_scope)

    assert visa_out == native_out                       # 业务结果一致
    assert visa_out["wave_unit"] == "V" and visa_out["points"] > 0
    session = visa_sessions[0]
    assert session.resource == "TCPIP0::192.168.10.41::4000::SOCKET"
    assert session.timeout == 2000 and session.closed is True
    assert session.log == native_recorder.sent          # 命令流一致（只换栈，不换命令）
    assert len(session.log) >= 11
    assert any("IDN" in c.upper() for c in session.log)
    assert visa_scope.status()["backend"] == "visa"
    assert visa_scope.metadata()["visa_resource"].startswith("TCPIP0::")


def test_visa_serial_backend_sets_frame_attributes(visa_sessions):
    """串口走 VISA：波特率与帧格式是**会话属性**，不写进资源名"""
    scope = open_scope(TEK_MODEL, serial_port="COM6", baudrate=115200, backend="visa")
    scope.connect()
    session = visa_sessions[0]
    assert session.resource == "ASRL6::INSTR"
    assert session.attrs["baud_rate"] == 115200
    assert session.attrs["data_bits"] == 8
    assert session.attrs["parity"] == "N"
    assert session.attrs["stop_bits"] == 1
    assert scope.backend_detail()["resource"] == "ASRL6::INSTR"


def test_backend_auto_follows_availability(visa_sessions, monkeypatch):
    """auto：装了 pyvisa（或注入过工厂）走 VISA，否则走原生；实际值永远可查"""
    import ate_drivers.transport as tp

    auto_visa = open_scope(TEK_MODEL, host=TEK_HOST, port=TEK_PORT, backend="auto")
    assert auto_visa.backend == "visa"
    assert auto_visa.backend_detail() == {"requested": "auto", "used": "visa",
                                          "declared": ["native", "visa"],
                                          "visa_available": True,
                                          "resource": "TCPIP0::192.168.10.41::4000::SOCKET"}

    monkeypatch.setattr(tp, "DEFAULT_VISA_FACTORY", None)
    monkeypatch.setattr(tp, "_pyvisa", lambda: None)
    auto_native = open_scope(TEK_MODEL, host=TEK_HOST, port=TEK_PORT, backend="auto",
                             transport=_recorder(TEK_MODEL))
    assert auto_native.backend == "native"
    assert auto_native.mode == "real"


def test_unknown_backend_is_rejected():
    """后端写法不合法 → E_CONFIG（列出可选值），不猜也不静默退回"""
    with pytest.raises(ConfigurationError) as e:
        open_scope(TEK_MODEL, host=TEK_HOST, port=TEK_PORT, backend="usb-tmc")
    assert "传输后端" in str(e.value) and "usb-tmc" in str(e.value)
    assert "native" in e.value.detail and "visa" in e.value.detail
    assert e.value.code == "E_CONFIG"


def test_explicit_visa_without_pyvisa_gives_readable_error(monkeypatch):
    """显式要 VISA 但环境没有 pyvisa → 可读 E_CONFIG（真机模式才拦，仿真不需要后端）"""
    import ate_drivers.transport as tp

    monkeypatch.setattr(tp, "DEFAULT_VISA_FACTORY", None)
    monkeypatch.setattr(tp, "_pyvisa", lambda: None)
    with pytest.raises(ConfigurationError) as e:
        open_scope(TEK_MODEL, host=TEK_HOST, port=TEK_PORT, backend="visa")
    assert "pyvisa" in str(e.value)

    cfg = open_scope(TEK_MODEL, host=TEK_HOST, port=TEK_PORT)._driver.cfg
    with pytest.raises(ConfigurationError) as e2:
        make_transport(cfg, mode="real", backend="visa", timeout=1.0)
    assert "pyvisa" in str(e2.value)
    direct = VisaTransport(cfg, session_factory=None)      # 直接构造时在 open() 处报错
    with pytest.raises(ConfigurationError) as e3:
        direct.open()
    assert "pyvisa" in str(e3.value)
    assert make_transport(cfg, mode="simulate", backend="visa").kind == "simulate"


def test_driver_backends_declaration_is_enforced():
    """型号没声明的后端 → E_CONFIG（声明了才给用，避免现场"以为走的是 VISA"）"""
    class NativeOnlyScope(TekMsoScope):
        driver_key = "native-only-scope"
        label = "只声明原生后端的示波器"
        BACKENDS = ("native",)
        MODELS = ("NATIVE-ONLY-1",)

    factory.register_driver(NativeOnlyScope, source="test")
    try:
        ok = open_scope("NATIVE-ONLY-1", host=TEK_HOST, port=TEK_PORT,
                        transport=_recorder("MSO54"))
        assert ok.backend == "native" and ok.backends == ("native",)
        with pytest.raises(ConfigurationError) as e:
            open_scope("NATIVE-ONLY-1", host=TEK_HOST, port=TEK_PORT, backend="visa")
        assert "不支持 visa 后端" in str(e.value)
    finally:
        factory.unregister_driver(NativeOnlyScope)


def test_gate_c19_flags_missing_and_unknown_backend(tmp_path):
    """C19：没声明后端 / 后端写法不合法 / 与清单不一致，都要被门禁拦下"""
    class NoBackend(TekMsoScope):
        driver_key = "no-backend-scope"
        BACKENDS = ()
        MODELS = ("NO-BACKEND-1",)

    class UsbBackend(TekMsoScope):
        driver_key = "usb-backend-scope"
        BACKENDS = ("usb",)
        MODELS = ("USB-BACKEND-1",)

    class AutoBackend(TekMsoScope):
        driver_key = "auto-backend-scope"
        BACKENDS = ("native", "auto")
        MODELS = ("AUTO-BACKEND-1",)

    c19 = lambda cls: [r for r in run_conformance(cls)["checks"] if r["id"] == "C19"][0]  # noqa: E731
    assert not c19(NoBackend)["ok"] and "未声明传输后端" in c19(NoBackend)["detail"]
    assert not c19(UsbBackend)["ok"] and "不在支持范围" in c19(UsbBackend)["detail"]
    assert not c19(AutoBackend)["ok"] and "auto" in c19(AutoBackend)["detail"]
    assert c19(TekMsoScope)["ok"] and "native / visa" in c19(TekMsoScope)["detail"]


def test_manifest_accepts_backends_list_and_legacy_backend(tmp_path):
    """清单兼容：`backends` 数组优先，旧写法单值 `backend` 仍可读；非法值报错"""
    from ate_drivers.kit import load_manifest

    base = {"schema": "ate.driver.manifest.v1", "key": "x-scope", "version": "0.0.1",
            "family": "scope", "api": "1.2", "entry": "x.driver:X", "models": ["X1"],
            "interfaces": ["LAN"]}
    cases = {
        "a": {"backends": ["native", "visa"]},
        "b": {"backend": "visa"},
        "c": {"backends": ["usb"]},
    }
    for name, extra in cases.items():
        folder = tmp_path / name
        folder.mkdir()
        (folder / "driver.json").write_text(json.dumps(dict(base, **extra)), encoding="utf-8")
    assert load_manifest(str(tmp_path / "a"))["backends"] == ["native", "visa"]
    assert load_manifest(str(tmp_path / "b"))["backends"] == ["visa"]      # 兼容旧写法
    with pytest.raises(ConfigurationError):
        load_manifest(str(tmp_path / "c"))                                 # 非法传输后端
