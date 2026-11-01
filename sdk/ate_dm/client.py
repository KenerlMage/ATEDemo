# -*- coding: utf-8 -*-
"""装备管理模块（DM）客户端

用法
    from ate_dm import DMClient

    with DMClient() as dm:
        dev = dm.connect(device_row, alias="scope", mode="simulate")
        idn = dm.call(dev, "identify")
        wave = dm.stream(dev, "capture", dtype="f")     # 大块数据走共享内存
        print(wave.count, wave.values()[:4])

设计要点
    · 传输用 multiprocessing.connection：Windows 下是命名管道，其他平台是本地 TCP，
      两端都由标准库提供，产线离线环境不需要装任何东西；
    · 地址从端点文件读取（backend/.ate_rt/dm_endpoint.json），也可用参数或环境变量直接指定；
    · 一个进程内多线程共享同一个客户端时按线程加锁，请求-响应严格配对；
    · 连接断开不自动重试控制面以外的操作：写出失败立刻抛错，避免"以为测了其实没测"。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from . import protocol as proto
from .shm import DEFAULT_DTYPE, AttachedRing, StreamBlock, StreamData

ENDPOINT_ENV = "ATE_DM_ENDPOINT"
PIPE_ENV = "ATE_DM_PIPE"
CLIENT_VERSION = "1.0.0"


def _read_endpoint(path: str | os.PathLike) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise proto.DmError(f"读取装备管理模块端点失败：{path}（{exc}）", proto.ERR_BUSY) from exc


def _default_endpoint() -> dict:
    """端点解析顺序：环境变量文件 → 环境变量管道名 → 后端默认端点文件"""
    path = os.environ.get(ENDPOINT_ENV)
    if path:
        return _read_endpoint(path)
    pipe = os.environ.get(PIPE_ENV)
    if pipe:
        return {"kind": "pipe", "address": pipe}
    base = os.environ.get("ATE_BACKEND_DIR") or str(Path(__file__).resolve().parents[2] / "backend")
    cand = Path(base) / ".ate_rt" / "dm_endpoint.json"
    if cand.exists():
        return _read_endpoint(cand)
    raise proto.DmError(
        "未找到装备管理模块：请先启动装备管理模块，或设置 ATE_DM_ENDPOINT / ATE_DM_PIPE",
        proto.ERR_BUSY,
    )


def _address_of(endpoint: dict) -> Any:
    kind = endpoint.get("kind") or "pipe"
    addr = endpoint.get("address")
    if not addr:
        raise proto.DmError("端点文件缺少 address 字段", proto.ERR_BAD_REQUEST)
    if kind == "tcp":
        host, _, port = str(addr).rpartition(":")
        return (host or "127.0.0.1", int(port))
    return str(addr)


@dataclass
class DMHandle:
    """一个设备会话句柄：DM 侧绑定驱动实例，客户端只持有编号与元信息"""
    handle: int
    alias: str = ""
    device_id: str = ""
    driver: str = ""
    resource: str = ""
    idn: str = ""
    simulated: bool = False
    opened_at: float = field(default_factory=time.time)

    def __int__(self) -> int:
        return int(self.handle)

    def __repr__(self) -> str:      # pragma: no cover - 仅调试用
        return f"<DMHandle {self.handle} alias={self.alias} driver={self.driver}>"


class DMClient:
    """装备管理模块控制面客户端（线程安全）"""

    def __init__(self, endpoint: str | os.PathLike | None = None, timeout: float = 30.0,
                 name: str = "", autoconnect: bool = True) -> None:
        self._endpoint_path = str(endpoint) if endpoint else ""
        self.timeout = float(timeout)
        self.name = name or f"pid{os.getpid()}"
        self._conn: Any = None
        self._lock = threading.RLock()
        self._seq = 0
        self._rings: dict[str, AttachedRing] = {}
        self._handles: dict[int, DMHandle] = {}
        self.endpoint: dict = {}
        if autoconnect:
            self.connect_transport()

    # ---------------- 传输 ----------------

    def connect_transport(self) -> dict:
        with self._lock:
            if self._conn is not None:
                return self.endpoint
            from multiprocessing.connection import Client
            self.endpoint = (_read_endpoint(self._endpoint_path) if self._endpoint_path
                             else _default_endpoint())
            addr = _address_of(self.endpoint)
            family = "AF_PIPE" if (self.endpoint.get("kind") or "pipe") == "pipe" else "AF_INET"
            key = self.endpoint.get("authkey") or ""
            try:
                self._conn = Client(addr, family=family,
                                    authkey=bytes.fromhex(key) if key else None)
                # 握手：服务端据此在会话列表里显示调用方
                self._conn.send_bytes(proto.dumps({"id": 0, "name": self.name}))
                hello = proto.loads(self._conn.recv_bytes()) or {}
                served = (hello.get("result") or {}).get("protocol")
                if not hello.get("ok") or served != proto.PROTOCOL_VERSION:
                    raise proto.DmError(
                        f"协议版本不匹配：服务端 {served}，客户端 {proto.PROTOCOL_VERSION}",
                        proto.ERR_BAD_REQUEST)
            except proto.DmError:
                raise
            except Exception as exc:
                self._conn = None
                raise proto.DmError(f"连接装备管理模块失败：{exc}", proto.ERR_BUSY) from exc
            info = self._rpc("ping")
            self.endpoint.update({"server": info})
            return self.endpoint

    def close(self) -> None:
        with self._lock:
            for ring in self._rings.values():
                ring.close()
            self._rings.clear()
            self._handles.clear()
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    def __enter__(self) -> "DMClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------- 协议 ----------------

    def _rpc(self, op: str, **args) -> Any:
        with self._lock:
            if self._conn is None:
                raise proto.DmError("装备管理模块未连接", proto.ERR_BUSY)
            self._seq += 1
            req_id = self._seq
            try:
                self._conn.send_bytes(proto.make_request(req_id, op, **args))
                raw = self._conn.recv_bytes()
            except (EOFError, OSError) as exc:
                self._conn = None
                raise proto.DmError(f"与装备管理模块的通道已断开：{exc}", proto.ERR_BUSY) from exc
            msg = proto.loads(raw)
            if int(msg.get("id", -1)) != req_id:
                raise proto.DmError("响应序号不匹配，控制面可能错位", proto.ERR_INTERNAL)
            if not msg.get("ok"):
                err = msg.get("error") or {}
                raise proto.DmError(err.get("message") or "装备管理模块返回失败",
                                    err.get("code") or proto.ERR_INTERNAL, err.get("detail"))
            return msg.get("result")

    # ---------------- 会话 ----------------

    def connect(self, device: dict, alias: str = "", mode: str = "simulate",
                options: Optional[dict] = None) -> DMHandle:
        """建立设备会话；同一别名重复调用由 DM 侧引用计数复用"""
        res = self._rpc("connect", device=device or {}, alias=alias, mode=mode,
                        options=options or {})
        handle = DMHandle(
            handle=int(res["handle"]),
            alias=res.get("alias", "") or alias,
            device_id=res.get("device_id", ""),
            driver=res.get("driver", ""),
            resource=res.get("resource", ""),
            idn=res.get("idn", ""),
            simulated=bool(res.get("simulated")),
        )
        self._handles[handle.handle] = handle
        return handle

    def disconnect(self, handle: DMHandle | int) -> dict:
        hid = int(handle)
        self._handles.pop(hid, None)
        return self._rpc("disconnect", handle=hid)

    def call(self, handle: DMHandle | int, action: str, **params) -> Any:
        return self._rpc("call", handle=int(handle), action=action, params=params)

    def batch(self, calls: Iterable[dict]) -> list:
        """一次往返执行多个动作：calls=[{handle, action, params}]"""
        return self._rpc("batch", calls=list(calls))

    def read(self, handle: DMHandle | int) -> str:
        return self._rpc("call", handle=int(handle), action="read", params={})

    def write(self, handle: DMHandle | int, command: str) -> dict:
        return self._rpc("call", handle=int(handle), action="write", params={"command": command})

    # ---------------- 数据面 ----------------

    def stream(self, handle: DMHandle | int, action: str, dtype: str = DEFAULT_DTYPE,
               **params) -> StreamData:
        """取一块大数组：控制面只回描述符，数据从共享内存读取"""
        res = self._rpc("stream", handle=int(handle), action=action, dtype=dtype, params=params)
        block = StreamBlock.from_dict(res)
        ring = self._rings.get(block.shm)
        if ring is None:
            ring = AttachedRing(block.shm)
            self._rings[block.shm] = ring
        try:
            data = ring.read(block)
        except BufferError as exc:
            raise proto.DmError(str(exc), proto.ERR_STALE) from exc
        finally:
            try:
                self._rpc("release", seq=block.seq, shm=block.shm)
            except proto.DmError:
                pass
        return StreamData(data, block.dtype, block.shape)

    # ---------------- 观测 ----------------

    def ping(self) -> dict:
        return self._rpc("ping")

    def caps(self) -> dict:
        return self._rpc("caps")

    def devices(self) -> list:
        return self._rpc("devices")

    def sessions(self) -> list:
        return self._rpc("sessions")

    def stats(self) -> dict:
        return self._rpc("stats")

    def log(self, limit: int = 50) -> list:
        return self._rpc("log", limit=int(limit))

    def shutdown(self) -> dict:
        return self._rpc("shutdown")


# ---------------------------------------------------------------- 便捷入口

_default: Optional[DMClient] = None
_default_lock = threading.Lock()


def client(endpoint: str | os.PathLike | None = None, **kw) -> DMClient:
    """进程级默认客户端（复用一个通道，减少握手）"""
    global _default
    with _default_lock:
        if endpoint is not None:
            return DMClient(endpoint, **kw)
        if _default is None:
            _default = DMClient(**kw)
        return _default


def open_device(device: dict, alias: str = "", mode: str = "simulate", **kw) -> DMHandle:
    """打开一个设备会话（走默认客户端）"""
    return client(**kw).connect(device, alias=alias, mode=mode)


def available() -> bool:
    """装备管理模块是否在位（用例可据此决定走 DM 还是进程内驱动）"""
    try:
        client(timeout=3.0).ping()
        return True
    except Exception:
        return False
