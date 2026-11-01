# -*- coding: utf-8 -*-
"""实时数据存储层（KVS，Redis 专用）

设计约束（2026-11 变更）：

* **只保留 Redis 一种后端**。现场不再有"缺服务时落本地库"的兜底路径，
  存储不可用就是不可用，由调用方决定降级行为（用例继续跑，只是不上报数据）。
* 仍然**不引入 redis-py**：产线工控机可能离线，第三方包装不上，
  协议由 `resp.py` 的最小 RESP2 客户端承担。

选择规则（环境变量 `ATE_RT_URL`，形如 redis://[[user:]pass@]host:port/db）：

  * 未设置  -> 使用默认地址 redis://127.0.0.1:6379/0
  * redis:// -> 必须可连通，连不上抛 RtUnavailable
  * sqlite:// -> 显式拒绝（后端已停用），避免旧配置文件静默生效

数据模型（三层：测试用例 -> 数据项 -> 数据点）：

  task    一次执行（task_id 即 Runner 的任务号），带名称 / TPS / UUT / 模式 / 状态
  channel 数据项（传感器），带单位 / 分组 / 类型 / 上下限
  point   数据点（时间戳 + 数值），每通道保留最近 N 个点用于折线图

键空间（前缀 `ate:rt`）：

  ate:rt:tasks                          ZSET  task_id -> 开始时间戳
  ate:rt:task:<id>                      HASH  name/tps_id/uut/status/started_at/updated_at
  ate:rt:task:<id>:channels             ZSET  数据项名 -> 声明顺序
  ate:rt:task:<id>:ch:<name>            HASH  label/unit/group/kind/value/num/ts
  ate:rt:task:<id>:ch:<name>:z          ZSET  时间戳 -> 数据点（保留最近 N 点）
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from .resp import RespClient, RespError

KEY_PREFIX = "ate:rt"
DEFAULT_KEEP = 3600                          # 每通道保留点数（约 1 小时 @1Hz）
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"
DEFAULT_TIMEOUT = 1.5
SERIES_TTL = 86400                           # 任务与曲线的过期时间（秒）


class RtUnavailable(RuntimeError):
    """存储不可用（Redis 连不上 / 配置非法）"""


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
    """redis://[[user]:pass@]host:port/db -> (host, port, db, auth)

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
    """隐去 URL 中的凭据，用于日志与接口回显"""
    if "@" in url:
        head, tail = url.split("@", 1)
        if "//" in head:
            prefix, _cred = head.split("//", 1)
            return f"{prefix}//***@{tail}"
    return url


# ==================== Redis 后端 ====================


class RedisStore:
    """实时数据 KVS：任务 / 数据项 / 数据点三层，全部落在 Redis"""

    backend = "redis"

    def __init__(self, url: str = DEFAULT_REDIS_URL, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.url = url
        host, port, db, password = parse_redis_url(url)
        self.client = RespClient(host=host, port=port, db=db, password=password, timeout=timeout)

    # ---- 连接 ----

    def connect(self) -> "RedisStore":
        self.client.connect()
        return self

    def close(self) -> None:
        self.client.close()

    def ping(self) -> bool:
        return self.client.ping()

    def info(self) -> dict:
        return {
            "backend": self.backend,
            "url": mask_url(self.url),
            "db": self.client.db,
            "prefix": KEY_PREFIX,
            "keep": DEFAULT_KEEP,
            "ttl_s": SERIES_TTL,
            "ok": True,
        }

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
            ("EXPIRE", self._task_key(task_id), str(SERIES_TTL)),
            ("EXPIRE", self._channels_key(task_id), str(SERIES_TTL)),
            ("EXPIRE", self._k("tasks"), str(SERIES_TTL)),
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

    # ---- 数据项 ----

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
            ("EXPIRE", key, str(SERIES_TTL)),
            ("EXPIRE", self._channels_key(task_id), str(SERIES_TTL)),
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
            ("EXPIRE", self._series_key(task_id, name), str(SERIES_TTL)),
            ("EXPIRE", self._ch_key(task_id, name), str(SERIES_TTL)),
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
        """立即让该任务的曲线过期（任务哈希保留，供事后查询）"""
        for ch in self.list_channels(task_id):
            self.client.call("EXPIRE", self._series_key(task_id, ch["name"]), "1")


# ==================== 选择与辅助 ====================


def open_store(url: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT,
               force: str = "") -> RedisStore:
    """打开 Redis 存储；不可用时抛 RtUnavailable，**不再回落本地库**

    `force` 仅为兼容旧调用保留，取值不影响行为。
    """
    text = (url or os.environ.get("ATE_RT_URL") or "").strip()
    if text.lower().startswith("sqlite:"):
        raise RtUnavailable(
            "SQLite 后端已停用：请把 ATE_RT_URL 改为 redis://<host>:<port>/<db>"
        )
    target = text or DEFAULT_REDIS_URL
    store = RedisStore(target, timeout=timeout)
    try:
        store.connect()
        if not store.client.ping():
            raise ConnectionError("PING 失败")
    except (OSError, ConnectionError, RespError) as exc:
        try:
            store.close()
        except Exception:
            pass
        raise RtUnavailable(f"Redis 不可用（{mask_url(target)}）：{exc}") from exc
    return store


def probe(url: Optional[str] = None, timeout: float = 0.8) -> dict:
    """探测存储连通性，不抛异常（供接口与排障命令使用）"""
    text = (url or os.environ.get("ATE_RT_URL") or DEFAULT_REDIS_URL).strip()
    store = None
    try:
        store = open_store(text, timeout=timeout)
        return {"ok": True, "backend": "redis", "url": mask_url(text)}
    except Exception as exc:
        return {"ok": False, "backend": "redis", "url": mask_url(text), "error": str(exc)}
    finally:
        if store is not None:
            store.close()


def describe(store: Any) -> dict:
    """后端信息（供 /api/monitor/info 与日志使用）"""
    info = store.info() if hasattr(store, "info") else {"backend": "?"}
    info.setdefault("ok", True)
    return info


__all__ = [
    "DEFAULT_KEEP",
    "DEFAULT_REDIS_URL",
    "DEFAULT_TIMEOUT",
    "KEY_PREFIX",
    "SERIES_TTL",
    "RedisStore",
    "RtUnavailable",
    "describe",
    "mask_url",
    "now_ms",
    "open_store",
    "parse_redis_url",
    "probe",
]
