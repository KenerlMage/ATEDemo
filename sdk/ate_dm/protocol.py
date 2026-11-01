# -*- coding: utf-8 -*-
"""ate_dm 控制面协议

帧格式（控制面）
    multiprocessing.connection 负责长度前缀与字节流，本层只负责消息语义：
    请求  {"id": 1, "op": "call", "args": {...}}
    响应  {"id": 1, "ok": true, "result": {...}} 或 {"id": 1, "ok": false, "error": {"code","message"}}

约定
    · 控制面只走小消息（参数、结果、统计），不得传大块数组；
    · 大块采样数据走共享内存（见 shm.py），控制面只传描述符；
    · 版本不匹配时服务端拒绝连接，避免半新半旧混跑。
"""

from __future__ import annotations

import json
from typing import Any

PROTOCOL_VERSION = 1

# 错误码：客户端按码分流，不解析文本
ERR_BAD_REQUEST = "bad_request"
ERR_UNKNOWN_OP = "unknown_op"
ERR_NO_DEVICE = "no_device"
ERR_NO_HANDLE = "no_handle"
ERR_DRIVER = "driver_error"
ERR_STREAM = "stream_error"
ERR_STALE = "stale_block"
ERR_TOO_LARGE = "payload_too_large"
ERR_BUSY = "busy"
ERR_INTERNAL = "internal"

OPS = (
    "ping", "caps", "connect", "disconnect", "call", "batch", "stream",
    "release", "devices", "sessions", "stats", "log", "shutdown",
)


class DmError(RuntimeError):
    """装备管理模块返回的错误（客户端侧统一异常）"""

    def __init__(self, message: str, code: str = ERR_INTERNAL, detail: Any = None):
        super().__init__(message)
        self.code = code
        self.detail = detail


def dumps(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def loads(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


def make_request(req_id: int, op: str, **args) -> bytes:
    return dumps({"id": int(req_id), "op": op, "args": args})


def make_ok(req_id: int, result: Any) -> bytes:
    return dumps({"id": int(req_id), "ok": True, "result": result})


def make_err(req_id: int, code: str, message: str, detail: Any = None) -> bytes:
    err = {"code": code, "message": message}
    if detail is not None:
        err["detail"] = detail
    return dumps({"id": int(req_id), "ok": False, "error": err})
