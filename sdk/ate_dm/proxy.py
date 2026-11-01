# -*- coding: utf-8 -*-
"""驱动代理：让用例用「和以前一样的写法」走装备管理模块

用途
    `ctx.driver("dmm").do("measure_dc_voltage")` 这类写法在改造前后保持一致，
    差别只在背后是进程内驱动，还是通过装备管理模块远程驱动。上层用例代码零改动。

取舍
    · 只代理用例真正用到的语义（do / write / read / state / identify / close）；
      驱动内部的私有方法一律不暴露——真需要时应该给驱动加动作，而不是穿透代理；
    · 每次调用都是独立请求：不做本地缓存，避免多工位共享设备时读到别人改过的设置；
    · close() 幂等，重复关闭不报错（会话收尾统一释放，不能因为顺序问题影响结论）。
"""

from __future__ import annotations

from typing import Any, Optional

from .client import DMClient, DMHandle, client as default_client
from .protocol import DmError


class DmDriverProxy:
    """把 DM 会话包装成驱动对象的样子"""

    def __init__(self, dm: DMClient, handle: DMHandle, alias: str = "") -> None:
        self._dm = dm
        self._handle = handle
        self._alias = alias or handle.alias
        self._closed = False

    # ---- 元信息 ----
    @property
    def alias(self) -> str:
        return self._alias

    @property
    def handle(self) -> int:
        return int(self._handle)

    def __int__(self) -> int:
        return int(self._handle)

    @property
    def device_id(self) -> str:
        return self._handle.device_id

    @property
    def resource(self) -> str:
        return self._handle.resource

    @property
    def simulate(self) -> bool:
        return bool(self._handle.simulated)

    # ---- 驱动语义 ----
    def do(self, action: str, **params) -> dict:
        if self._closed:
            raise DmError(f"会话已关闭，无法执行 {action}", "no_handle")
        out = self._dm.call(self._handle, action, **params)
        return out if isinstance(out, dict) else {"value": out, "action": action}

    def write(self, command: str) -> None:
        self.do("write", command=command)

    def read(self) -> str:
        out = self.do("read")
        return str((out or {}).get("value") or "")

    def query(self, command: str) -> str:
        self.write(command)
        return self.read()

    def state(self) -> dict:
        try:
            return self._dm.call(self._handle, "state") or {}
        except DmError:
            return {"alias": self._alias, "handle": int(self._handle), "remote": True}

    def identify(self) -> dict:
        out = self.do("identify")
        return out.get("value") if isinstance(out.get("value"), dict) else out

    def close(self) -> dict:
        if self._closed:
            return {"closed": True, "handle": int(self._handle)}
        self._closed = True
        try:
            return self._dm.disconnect(self._handle)
        except DmError:
            return {"closed": True, "handle": int(self._handle), "warning": "会话已被服务端回收"}

    def stream(self, action: str, dtype: str = "f", **params):
        """大数组采集：返回 StreamData（.values() 取值，.data 取原始字节）"""
        return self._dm.stream(self._handle, action, dtype=dtype, **params)

    def __repr__(self) -> str:      # pragma: no cover - 仅调试用
        state = "closed" if self._closed else "open"
        return f"<DmDriverProxy {self._alias} handle={int(self._handle)} {state}>"

    def __enter__(self) -> "DmDriverProxy":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def open_proxy(device: dict, alias: str = "", mode: str = "simulate",
               dm: Optional[DMClient] = None) -> DmDriverProxy:
    """建立代理：优先复用进程级默认客户端"""
    conn = dm or default_client()
    handle = conn.connect(device, alias=alias, mode=mode)
    return DmDriverProxy(conn, handle, alias)
