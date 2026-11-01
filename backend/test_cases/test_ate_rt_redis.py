# -*- coding: utf-8 -*-
"""实时数据存储：Redis 专用后的契约与端到端验证

机器上没有 Redis 服务时，本模块用**进程内的最小 RESP2 服务端**（`FakeRedis`）
替代，验证键空间、写入、快照、曲线与保留点数裁剪；不依赖外网、不依赖第三方包。
同时覆盖三条禁止路径：

* `sqlite://` 配置必须被显式拒绝（后端已停用）；
* Redis 连不上必须抛 `RtUnavailable`，不再回落本地库；
* 用例侧 `Monitor` 在存储不可用时必须降级为空操作，不得中断测试。

单独运行：python -m pytest test_cases/test_ate_rt_redis.py -v
"""

import socket
import sys
import threading
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
SDK = BACKEND.parent / "sdk"
for _p in (str(BACKEND), str(SDK)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ate_rt  # noqa: E402
from ate_rt import store as rt  # noqa: E402


# ==================== 最小 RESP2 服务端（测试替身） ====================


def _bulk(value):
    if value is None:
        return b"$-1\r\n"
    data = value.encode("utf-8") if isinstance(value, str) else value
    return b"$%d\r\n%s\r\n" % (len(data), data)


def _array(items):
    return b"*%d\r\n" % len(items) + b"".join(_bulk(i) for i in items)


class FakeRedis(threading.Thread):
    """只实现 store.py 用到的命令，够验证数据面即可"""

    daemon = True

    def __init__(self) -> None:
        super().__init__(name="fake-redis")
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self.hashes: dict[str, dict] = {}
        self.zsets: dict[str, dict] = {}
        self.expires: dict[str, int] = {}
        self._lock = threading.Lock()
        self._stop = False

    # ---- 服务端生命周期 ----

    def run(self) -> None:
        self._sock.settimeout(0.2)
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def stop(self) -> None:
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass

    def _serve(self, conn: socket.socket) -> None:
        conn.settimeout(5)
        reader = conn.makefile("rb")
        try:
            while True:
                args = self._read_command(reader)
                if args is None:
                    break
                conn.sendall(self._dispatch(args))
        except (OSError, ValueError):
            pass
        finally:
            try:
                reader.close()
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _read_command(reader):
        line = reader.readline()
        if not line:
            return None
        if not line.startswith(b"*"):
            raise ValueError(f"非数组请求: {line[:20]!r}")
        count = int(line[1:-2])
        args = []
        for _ in range(count):
            head = reader.readline()
            length = int(head[1:-2])
            args.append(reader.read(length).decode("utf-8"))
            reader.read(2)
        return args

    # ---- 命令派发 ----

    def _dispatch(self, args) -> bytes:
        cmd = args[0].upper()
        a = args[1:]
        with self._lock:
            try:
                return getattr(self, f"_cmd_{cmd.lower()}")(a)
            except AttributeError:
                return b"-ERR unknown command '%s'\r\n" % cmd.encode()

    def _cmd_ping(self, _a):
        return b"+PONG\r\n"

    def _cmd_select(self, _a):
        return b"+OK\r\n"

    def _cmd_auth(self, _a):
        return b"+OK\r\n"

    def _cmd_exists(self, a):
        key = a[0]
        return b":%d\r\n" % int(key in self.hashes or key in self.zsets)

    def _cmd_hset(self, a):
        key, rest = a[0], a[1:]
        bucket = self.hashes.setdefault(key, {})
        added = 0
        for i in range(0, len(rest) - 1, 2):
            if rest[i] not in bucket:
                added += 1
            bucket[rest[i]] = rest[i + 1]
        return b":%d\r\n" % added

    def _cmd_hgetall(self, a):
        bucket = self.hashes.get(a[0], {})
        items = []
        for k, v in bucket.items():
            items.extend([k, v])
        return _array(items)

    def _cmd_hget(self, a):
        return _bulk(self.hashes.get(a[0], {}).get(a[1]))

    def _cmd_hdel(self, a):
        bucket = self.hashes.get(a[0], {})
        removed = 0
        for field in a[1:]:
            if bucket.pop(field, None) is not None:
                removed += 1
        return b":%d\r\n" % removed

    def _cmd_zadd(self, a):
        key, rest = a[0], a[1:]
        nx = False
        if rest and rest[0].upper() == "NX":
            nx = True
            rest = rest[1:]
        bucket = self.zsets.setdefault(key, {})
        added = 0
        for i in range(0, len(rest) - 1, 2):
            score, member = float(rest[i]), rest[i + 1]
            if nx and member in bucket:
                continue
            if member not in bucket:
                added += 1
            bucket[member] = score
        return b":%d\r\n" % added

    def _cmd_zcard(self, a):
        return b":%d\r\n" % len(self.zsets.get(a[0], {}))

    @staticmethod
    def _slice(items, start, stop):
        n = len(items)
        start = int(start)
        stop = int(stop)
        if start < 0:
            start += n
        if stop < 0:
            stop += n
        start = max(start, 0)
        return items[start:stop + 1] if stop >= start else []

    def _range(self, a, reverse=False):
        key, start, stop = a[0], a[1], a[2]
        with_scores = len(a) > 3 and a[3].upper() == "WITHSCORES"
        bucket = self.zsets.get(key, {})
        items = sorted(bucket.items(), key=lambda kv: (kv[1], kv[0]), reverse=reverse)
        picked = self._slice(items, start, stop)
        if with_scores:
            flat = []
            for member, score in picked:
                flat.extend([member, f"{score:g}"])
            return _array(flat)
        return _array([member for member, _ in picked])

    def _cmd_zrange(self, a):
        return self._range(a)

    def _cmd_zrevrange(self, a):
        return self._range(a, reverse=True)

    def _cmd_zremrangebyrank(self, a):
        key, start, stop = a[0], a[1], a[2]
        bucket = self.zsets.get(key, {})
        items = sorted(bucket.items(), key=lambda kv: (kv[1], kv[0]))
        doomed = self._slice(items, start, stop)
        for member, _score in doomed:
            bucket.pop(member, None)
        return b":%d\r\n" % len(doomed)

    def _cmd_expire(self, a):
        self.expires[a[0]] = int(a[1])
        return b":1\r\n"

    def _cmd_del(self, a):
        n = 0
        for key in a:
            n += int(self.hashes.pop(key, None) is not None)
            n += int(self.zsets.pop(key, None) is not None)
        return b":%d\r\n" % n

    def _cmd_type(self, a):
        key = a[0]
        if key in self.hashes:
            return b"+hash\r\n"
        if key in self.zsets:
            return b"+zset\r\n"
        return b"+none\r\n"


@pytest.fixture()
def redis_server():
    server = FakeRedis()
    server.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", server.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        yield server
    finally:
        server.stop()


@pytest.fixture()
def store(redis_server):
    st = rt.RedisStore(f"redis://127.0.0.1:{redis_server.port}/0", timeout=2.0)
    st.connect()
    try:
        yield st
    finally:
        st.close()


# ==================== 配置解析 ====================


def test_parse_url_handles_user_and_db():
    assert rt.parse_redis_url("redis://10.0.0.5:6380/3") == ("10.0.0.5", 6380, 3, "")
    assert rt.parse_redis_url("redis://:secret@10.0.0.5:6379/1") == ("10.0.0.5", 6379, 1, ":secret")
    assert rt.parse_redis_url("redis://app:secret@h:6380/2") == ("h", 6380, 2, "app:secret")
    assert rt.parse_redis_url("") == ("127.0.0.1", 6379, 0, "")


def test_mask_url_hides_credentials():
    assert rt.mask_url("redis://user:pass@10.0.0.5:6379/0") == "redis://***@10.0.0.5:6379/0"
    assert rt.mask_url("redis://10.0.0.5:6379/0") == "redis://10.0.0.5:6379/0"


# ==================== 禁止路径 ====================


def test_sqlite_url_is_rejected():
    with pytest.raises(rt.RtUnavailable) as ei:
        rt.open_store("sqlite:///D:/tmp/realtime.db")
    assert "SQLite 后端已停用" in str(ei.value)


def test_unreachable_redis_raises_instead_of_falling_back():
    with pytest.raises(rt.RtUnavailable) as ei:
        rt.open_store("redis://127.0.0.1:6399/0", timeout=0.3)
    message = str(ei.value)
    assert "Redis 不可用" in message and "127.0.0.1:6399" in message
    assert "sqlite" not in message.lower()


def test_probe_reports_failure_without_raising():
    info = rt.probe("redis://127.0.0.1:6399/0", timeout=0.3)
    assert info["ok"] is False and info["backend"] == "redis"
    assert "Redis 不可用" in info["error"]


def test_monitor_degrades_to_noop_without_redis(monkeypatch):
    monkeypatch.setenv("ATE_RT_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.delenv("ATE_RT_DISABLE", raising=False)
    mon = ate_rt.Monitor(task="t-offline", name="离线降级")
    try:
        assert mon.store_backend == "none"
        assert mon.degraded is False
        assert mon.last_error
        mon.declare("电压", unit="V").write("电压", 1.23).event("开始")
    finally:
        mon.close()


# ==================== 数据面 ====================


def test_task_is_registered_and_listed(store, redis_server):
    store.upsert_task("t-1", {"name": "老化", "status": "running", "uut": "U-01"})
    store.upsert_task("t-2", {"name": "功能", "status": "done"})
    tasks = store.list_tasks(limit=10)
    assert [t["task_id"] for t in tasks] == ["t-2", "t-1"]
    assert tasks[0]["name"] == "功能"
    assert "ate:rt:task:t-1" in redis_server.hashes
    assert redis_server.expires.get("ate:rt:task:t-1") == rt.SERIES_TTL


def test_channel_declaration_keeps_order_and_metadata(store):
    for name, unit in (("电压", "V"), ("电流", "A"), ("频率", "Hz")):
        store.declare_channel("t-3", name, {"unit": unit, "group": "电学"})
    channels = store.list_channels("t-3")
    assert [c["name"] for c in channels] == ["电压", "电流", "频率"]
    assert channels[0]["unit"] == "V" and channels[0]["group"] == "电学"


def test_write_then_snapshot_and_series(store):
    store.declare_channel("t-4", "电压", {"unit": "V", "lo": 11.4, "hi": 12.6})
    for i in range(5):
        store.write("t-4", "电压", 12.0 + i * 0.01, ts_ms=1_700_000_000_000 + i * 1000)
    snap = store.snapshot("t-4")
    assert len(snap) == 1
    row = snap[0]
    assert row["unit"] == "V" and row["lo"] == 11.4 and row["hi"] == 12.6
    assert abs(float(row["value"]) - 12.04) < 1e-9
    assert row["ts_ms"] == 1_700_000_004_000

    points = store.series("t-4", "电压", limit=10)
    assert len(points) == 5
    assert points == sorted(points, key=lambda p: p["ts_ms"])
    assert abs(points[-1]["v"] - 12.04) < 1e-9
    assert points[0]["ts_ms"] == 1_700_000_000_000


def test_series_keeps_only_latest_points(store):
    store.declare_channel("t-5", "功率", {"unit": "mW"})
    for i in range(12):
        store.write("t-5", "功率", i, ts_ms=1_700_000_100_000 + i * 1000, keep=5)
    points = store.series("t-5", "功率", limit=50)
    assert len(points) == 5
    assert [p["v"] for p in points] == [7.0, 8.0, 9.0, 10.0, 11.0]


def test_text_value_roundtrip(store):
    store.declare_channel("t-6", "状态", {"kind": "text"})
    store.write("t-6", "状态", "PASS", ts_ms=1_700_000_200_000)
    snap = store.snapshot("t-6")
    assert snap[0]["value"] == "PASS"


def test_purge_expires_series_only(store, redis_server):
    store.declare_channel("t-7", "电压", {"unit": "V"})
    store.write("t-7", "电压", 12.0, ts_ms=1_700_000_300_000)
    store.purge("t-7")
    assert redis_server.expires.get("ate:rt:task:t-7:ch:电压:z") == 1
    assert store.get_task("t-7") is not None


def test_monitor_end_to_end_against_redis(redis_server):
    url = f"redis://127.0.0.1:{redis_server.port}/0"
    with ate_rt.Monitor(task="t-e2e", name="端到端", tps="spm_rh_dyn", url=url) as mon:
        assert mon.store_backend == "redis"
        mon.declare("电压", unit="V", lo=11.4, hi=12.6)
        for i in range(3):
            mon.write("电压", 12.0 + i * 0.01)

    reader = rt.RedisStore(url, timeout=2.0)
    reader.connect()
    try:
        task = reader.get_task("t-e2e")
        assert task["status"] == "done" and task["ended_at"]
        snap = reader.snapshot("t-e2e")
        assert len(snap) == 1
        assert abs(float(snap[0]["value"]) - 12.02) < 1e-9
        assert len(reader.series("t-e2e", "电压")) == 3
    finally:
        reader.close()
