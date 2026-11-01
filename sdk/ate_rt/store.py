"""实时数据存储层（KVS）

两种后端，接口一致：
  * RedisStore —— 首选。测试进程把数据写入 Redis，后端服务从同一实例读取，
                  跨进程、跨机器都可共享；键空间见 README。
  * SqliteStore —— 回退。现场没有 Redis（或临时断连）时落到本地 SQLite（WAL），
                  仍然支持测试进程写、Runner 读，保证功能不因缺服务而不可用。

选择规则（ATE_RT_URL 环境变量，形如 redis://127.0.0.1:6379/0 或 sqlite:///D:/x/rt.db）：
  * 未设置      -> 先试 Redis 默认地址，连不上回落 SQLite（degraded=True）
  * redis://     -> 必须可用；连不上时除非 ATE_RT_STRICT=1 否则回落 SQLite
  * sqlite://    -> 直接使用 SQLite

数据模型（三层：测试用例 -> 数据列表 -> 数据）：
  task    一次执行（task_id 即 Runner 的任务号），带名称 / TPS / UUT / 模式 / 状态
  channel 数据项（传感器），带单位 / 分组 / 类型 / 上下限
  point   数据点（时间戳 + 数值），每通道保留最近 N 个点用于折线图
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from .resp import RespClient, RespError

KEY_PREFIX = "ate:rt"
DEFAULT_KEEP = 3600                          # 每通道保留点数（约 1 小时 @1Hz）
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"


# ==================== 公共工具 ====================


def now_ms() -> int:
    return int(time.time() * 1000)


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _payload(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False)


def _default_db_path() -> Path:
    env = os.environ.get("ATE_RT_DB")
    if env:
        return Path(env)
    # 与 backend 同级的运行期目录，避免污染仓库根
    base = os.environ.get("ATE_BACKEND") or os.environ.get("ATE_DB_PATH", "")
    if base:
        backend = Path(base)
        if backend.suffix.lower() == ".db":
            backend = backend.parent
        return backend / ".ate_rt" / "realtime.db"
    return Path.cwd() / ".ate_rt" / "realtime.db"


# ==================== SQLite 后端 ====================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rt_task (
    task_id    TEXT PRIMARY KEY,
    name       TEXT,
    tps_id     TEXT,
    tps_name   TEXT,
    uut        TEXT,
    mode       TEXT,
    status     TEXT,
    host       TEXT,
    pid        INTEGER,
    started_at REAL,
    updated_at REAL,
    ended_at   REAL
);
CREATE TABLE IF NOT EXISTS rt_channel (
    task_id    TEXT,
    name       TEXT,
    label      TEXT,
    unit       TEXT,
    group_name TEXT,
    kind       TEXT,
    lo         REAL,
    hi         REAL,
    seq        INTEGER,
    PRIMARY KEY (task_id, name)
);
CREATE TABLE IF NOT EXISTS rt_point (
    task_id TEXT,
    name    TEXT,
    ts      REAL,
    num     REAL,
    raw     TEXT
);
CREATE INDEX IF NOT EXISTS ix_rt_point ON rt_point(task_id, name, ts);
CREATE INDEX IF NOT EXISTS ix_rt_task_started ON rt_task(started_at);
"""


class SqliteStore:
    backend = "sqlite"

    def __init__(self, path: Optional[Path | str] = None) -> None:
        self.path = Path(path) if path else _default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._writes = 0
        self._conn = sqlite3.connect(str(self.path), timeout=5.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            pass
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---- 内部 ----

    def _exec(self, sql: str, args: Iterable = ()) -> sqlite3.Cursor:
        cur = self._conn.execute(sql, tuple(args))
        return cur

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass

    def info(self) -> dict:
        return {"backend": self.backend, "path": str(self.path), "ok": True, "degraded": False}

    # ---- 任务 ----

    def upsert_task(self, task_id: str, fields: dict) -> None:
        ts = now_ms() / 1000.0
        allowed = ("name", "tps_id", "tps_name", "uut", "mode", "status", "host", "pid",
                   "started_at", "ended_at")
        # 空字符串视为“未提供”，避免后写者把先写者的信息抹掉（status 例外，允许显式置空）
        data = {}
        for key in allowed:
            if key not in fields:
                continue
            val = fields[key]
            if val in ("", None) and key != "status":
                continue
            data[key] = val
        with self._lock:
            row = self._exec("SELECT task_id FROM rt_task WHERE task_id=?", (task_id,)).fetchone()
            if row:
                sets = ", ".join(f"{k}=?" for k in data)
                if sets:
                    self._exec(f"UPDATE rt_task SET {sets}, updated_at=? WHERE task_id=?",
                               (*data.values(), ts, task_id))
            else:
                cols = ["task_id", "started_at", "updated_at"] + list(data)
                vals = [task_id, fields.get("started_at", ts), ts] + list(data.values())
                self._exec(
                    f"INSERT INTO rt_task ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    vals,
                )
            self._conn.commit()

    def get_task(self, task_id: str) -> dict:
        with self._lock:
            row = self._exec("SELECT * FROM rt_task WHERE task_id=?", (task_id,)).fetchone()
        return dict(row) if row else {}

    def list_tasks(self, limit: int = 50, active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM rt_task"
        args: list = []
        if active_only:
            sql += " WHERE status IN ('running','pending')"
        sql += " ORDER BY started_at DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._exec(sql, args).fetchall()
        return [dict(r) for r in rows]

    # ---- 通道 ----

    def declare_channel(self, task_id: str, name: str, meta: dict) -> None:
        with self._lock:
            row = self._exec(
                "SELECT seq FROM rt_channel WHERE task_id=? AND name=?", (task_id, name)
            ).fetchone()
            if row:
                self._exec(
                    "UPDATE rt_channel SET label=?, unit=?, group_name=?, kind=?, lo=?, hi=? "
                    "WHERE task_id=? AND name=?",
                    (
                        meta.get("label") or name,
                        meta.get("unit", ""),
                        meta.get("group", ""),
                        meta.get("kind", ""),
                        meta.get("lo"),
                        meta.get("hi"),
                        task_id,
                        name,
                    ),
                )
            else:
                nxt = self._exec(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM rt_channel WHERE task_id=?", (task_id,)
                ).fetchone()[0]
                self._exec(
                    "INSERT INTO rt_channel (task_id, name, label, unit, group_name, kind, lo, hi, seq) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (task_id, name, meta.get("label") or name, meta.get("unit", ""),
                     meta.get("group", ""), meta.get("kind", ""), meta.get("lo"), meta.get("hi"), nxt),
                )
            self._conn.commit()

    def list_channels(self, task_id: str) -> list[dict]:
        with self._lock:
            rows = self._exec(
                "SELECT name, label, unit, group_name AS \"group\", kind, lo, hi, seq "
                "FROM rt_channel WHERE task_id=? ORDER BY seq, name",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- 数据点 ----

    def write(self, task_id: str, name: str, value: Any, ts_ms: Optional[int] = None,
              keep: int = DEFAULT_KEEP) -> None:
        ts = (ts_ms if ts_ms is not None else now_ms()) / 1000.0
        num = _as_number(value)
        raw = _payload(value)
        with self._lock:
            self._exec(
                "INSERT INTO rt_point (task_id, name, ts, num, raw) VALUES (?,?,?,?,?)",
                (task_id, name, ts, num, raw),
            )
            self._writes += 1
            if self._writes % 8 == 0:
                self._exec(
                    "DELETE FROM rt_point WHERE task_id=? AND name=? AND rowid NOT IN ("
                    "  SELECT rowid FROM rt_point WHERE task_id=? AND name=? ORDER BY ts DESC LIMIT ?)",
                    (task_id, name, task_id, name, int(keep)),
                )
            self._exec("UPDATE rt_task SET updated_at=? WHERE task_id=?", (time.time(), task_id))
            self._conn.commit()

    def snapshot(self, task_id: str) -> list[dict]:
        """每通道的最新值（一次查询取回，供 1Hz 刷新）"""
        sql = (
            "SELECT c.name, c.label, c.unit, c.group_name AS \"group\", c.kind, c.lo, c.hi, "
            "       p.num, p.raw, p.ts "
            "FROM rt_channel c "
            "LEFT JOIN rt_point p ON p.rowid = ("
            "    SELECT rowid FROM rt_point WHERE task_id=c.task_id AND name=c.name "
            "    ORDER BY ts DESC LIMIT 1) "
            "WHERE c.task_id=? ORDER BY c.seq, c.name"
        )
        with self._lock:
            rows = self._exec(sql, (task_id,)).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["value"] = _decode_raw(item.pop("raw", None))
            item["ts_ms"] = int(item["ts"] * 1000) if item.get("ts") else 0
            item.pop("ts", None)
            out.append(item)
        return out

    def series(self, task_id: str, name: str, limit: int = 600) -> list[dict]:
        with self._lock:
            rows = self._exec(
                "SELECT ts, num, raw FROM rt_point WHERE task_id=? AND name=? "
                "ORDER BY ts DESC LIMIT ?",
                (task_id, name, int(limit)),
            ).fetchall()
        points = []
        for r in reversed(rows):
            points.append({
                "ts_ms": int(r["ts"] * 1000),
                "v": r["num"] if r["num"] is not None else _decode_raw(r["raw"]),
            })
        return points

    def purge(self, task_id: str) -> None:
        with self._lock:
            self._exec("DELETE FROM rt_point WHERE task_id=?", (task_id,))
            self._conn.commit()


# ==================== Redis 后端 ====================


class RedisStore:
    backend = "redis"

    def __init__(self, url: str = DEFAULT_REDIS_URL, timeout: float = 1.5) -> None:
        self.url = url
        host, port, db, password = parse_redis_url(url)
        self.client = RespClient(host=host, port=port, db=db, password=password, timeout=timeout)

    def connect(self) -> None:
        self.client.connect()

    def close(self) -> None:
        self.client.close()

    def info(self) -> dict:
        return {"backend": self.backend, "url": mask_url(self.url), "ok": True, "degraded": False}

    # ---- 键 ----

    def _k(self, *parts: str) -> str:
        return ":".join((KEY_PREFIX, *parts))

    def _task_key(self, task_id: str) -> str:
        return self._k("task", task_id)

    def _ch_key(self, task_id: str, name: str) -> str:
        return self._k("task", task_id, "ch", name)

    def _series_key(self, task_id: str, name: str) -> str:
        return self._k("task", task_id, "ch", name, "z")

    def _channels_key(self, task_id: str) -> str:
        return self._k("task", task_id, "channels")

    # ---- 任务 ----

    def upsert_task(self, task_id: str, fields: dict) -> None:
        now = time.time()
        flat: list[str] = []
        for key, val in fields.items():
            if val is None or (val == "" and key != "status"):
                continue
            flat.extend([key, str(val)])
        flat.extend(["updated_at", f"{now:.3f}"])
        if "started_at" not in fields:
            exists = self.client.call("EXISTS", self._task_key(task_id))
            if not exists:
                flat.extend(["started_at", f"{now:.3f}"])
        cmds = [
            ("HSET", self._task_key(task_id), *flat),
            ("ZADD", self._k("tasks"), f"{now:.3f}", task_id),
            ("EXPIRE", self._task_key(task_id), "86400"),
            ("EXPIRE", self._channels_key(task_id), "86400"),
        ]
        self.client.pipeline(cmds)

    def get_task(self, task_id: str) -> dict:
        data = self.client.call("HGETALL", self._task_key(task_id)) or []
        return _flat_pairs(data)

    def list_tasks(self, limit: int = 50, active_only: bool = False) -> list[dict]:
        ids = self.client.call("ZREVRANGE", self._k("tasks"), 0, max(int(limit) - 1, 0)) or []
        out = []
        for tid in ids:
            task = self.get_task(tid)
            if not task:
                continue
            if active_only and str(task.get("status", "")) not in ("running", "pending"):
                continue
            task["task_id"] = tid
            out.append(task)
        return out

    # ---- 通道 ----

    def declare_channel(self, task_id: str, name: str, meta: dict) -> None:
        key = self._ch_key(task_id, name)
        seq = self.client.call("ZCARD", self._channels_key(task_id)) or 0
        fields = {
            "label": meta.get("label") or name,
            "unit": meta.get("unit", ""),
            "group": meta.get("group", ""),
            "kind": meta.get("kind", ""),
            "lo": meta.get("lo", ""),
            "hi": meta.get("hi", ""),
        }
        flat: list[str] = []
        for k, v in fields.items():
            flat.extend([k, "" if v is None else str(v)])
        self.client.pipeline([
            ("HSET", key, *flat),
            ("ZADD", self._channels_key(task_id), "NX", str(seq + 1), name),
            ("EXPIRE", key, "86400"),
        ])

    def list_channels(self, task_id: str) -> list[dict]:
        names = self.client.call("ZRANGE", self._channels_key(task_id), 0, -1) or []
        out = []
        for name in names:
            data = _flat_pairs(self.client.call("HGETALL", self._ch_key(task_id, name)) or [])
            data.setdefault("label", name)
            data.setdefault("unit", "")
            data["name"] = name
            out.append(data)
        return out

    # ---- 数据点 ----

    def write(self, task_id: str, name: str, value: Any, ts_ms: Optional[int] = None,
              keep: int = DEFAULT_KEEP) -> None:
        ts = ts_ms if ts_ms is not None else now_ms()
        num = _as_number(value)
        raw = _payload(value)
        member = f"{ts}:{num if num is not None else raw}"
        cmds = [
            ("HSET", self._ch_key(task_id, name), "value", raw,
             "num", "" if num is None else repr(num), "ts", str(ts)),
            ("ZADD", self._series_key(task_id, name), str(ts), member),
            ("ZREMRANGEBYRANK", self._series_key(task_id, name), "0", str(-int(keep) - 1)),
            ("EXPIRE", self._series_key(task_id, name), "86400"),
            ("EXPIRE", self._ch_key(task_id, name), "86400"),
            ("HSET", self._task_key(task_id), "updated_at", f"{time.time():.3f}"),
        ]
        self.client.pipeline(cmds)

    def snapshot(self, task_id: str) -> list[dict]:
        out = []
        for ch in self.list_channels(task_id):
            name = ch["name"]
            data = _flat_pairs(self.client.call("HGETALL", self._ch_key(task_id, name)) or [])
            raw = data.get("value")
            ts = data.get("ts") or ""
            out.append({
                "name": name,
                "label": ch.get("label") or name,
                "unit": ch.get("unit", ""),
                "group": ch.get("group", ""),
                "kind": ch.get("kind", ""),
                "lo": _opt_float(ch.get("lo")),
                "hi": _opt_float(ch.get("hi")),
                "value": _decode_raw(raw),
                "ts_ms": int(float(ts)) if ts else 0,
            })
        return out

    def series(self, task_id: str, name: str, limit: int = 600) -> list[dict]:
        raw = self.client.call(
            "ZREVRANGE", self._series_key(task_id, name), 0, max(int(limit) - 1, 0), "WITHSCORES"
        ) or []
        rows = []
        # WITHSCORES 返回 [member, score, member, score, ...]
        for i in range(0, len(raw) - 1, 2):
            member, score = raw[i], raw[i + 1]
            value = member.split(":", 1)[1] if ":" in member else member
            try:
                num = float(value)
            except ValueError:
                num = _decode_raw(value)
            rows.append({"ts_ms": int(float(score)), "v": num})
        rows.reverse()
        return rows

    def purge(self, task_id: str) -> None:
        for ch in self.list_channels(task_id):
            self.client.call("EXPIRE", self._series_key(task_id, ch["name"]), "1")


# ==================== 选择与辅助 ====================


def _flat_pairs(items: list) -> dict:
    out = {}
    for i in range(0, len(items) - 1, 2):
        out[str(items[i])] = items[i + 1]
    return out


def _opt_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _decode_raw(raw: Any) -> Any:
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def parse_redis_url(url: str) -> tuple[str, int, int, str]:
    """redis://[[user]:password@]host:port/db -> (host, port, db, auth)

    auth 原样保留 "@" 之前的部分：既有 "password"，也有 Redis 6 的 "user:password"。
    """
    text = url.strip()
    for scheme in ("redis://", "rediss://"):
        if text.startswith(scheme):
            text = text[len(scheme):]
            break
    auth = ""
    if "@" in text:
        auth, text = text.split("@", 1)
    db = 0
    if "/" in text:
        text, tail = text.split("/", 1)
        tail = tail.strip()
        if tail.isdigit():
            db = int(tail)
    host, _, port = text.partition(":")
    return host or "127.0.0.1", int(port or 6379), db, auth


def mask_url(url: str) -> str:
    if "@" in url:
        head, tail = url.split("@", 1)
        if "//" in head:
            prefix, _cred = head.split("//", 1)
            return f"{prefix}//***@{tail}"
    return url


def open_store(url: Optional[str] = None, *, timeout: float = 1.5, force: str = "") -> Any:
    """按 URL / 环境变量打开存储；Redis 不可用时回落 SQLite"""
    url = (url or os.environ.get("ATE_RT_URL") or "").strip()
    strict = os.environ.get("ATE_RT_STRICT", "") in ("1", "true", "yes")
    if url.startswith("sqlite:"):
        path = url.split("sqlite:", 1)[1].lstrip("/\\") or str(_default_db_path())
        return SqliteStore(path)
    target = url or DEFAULT_REDIS_URL
    try:
        store = RedisStore(target, timeout=timeout)
        store.connect()
        if not store.client.ping():
            raise ConnectionError("PING 失败")
        return store
    except (OSError, ConnectionError, RespError) as exc:
        if strict:
            raise RtUnavailable(f"Redis 不可用且已设置 ATE_RT_STRICT=1: {exc}") from exc
        store = SqliteStore()
        store.degraded = True             # type: ignore[attr-defined]
        store.reason = str(exc)           # type: ignore[attr-defined]
        return store


class RtUnavailable(RuntimeError):
    """严格模式下存储不可用"""


def describe(store: Any) -> dict:
    info = store.info() if hasattr(store, "info") else {"backend": getattr(store, "backend", "?")}
    if getattr(store, "degraded", False):
        info["degraded"] = True
        info["reason"] = getattr(store, "reason", "")
    return info
