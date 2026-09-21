# -*- coding: utf-8 -*-
"""驱动基类：命令收发、动作分发、仿真响应钩子

* 驱动子类只需：①声明 `ACTIONS`（动作名 -> 方法名）②实现动作方法
  ③覆盖 `simulate_command()` 让仿真模式能回答自己的命令。
* 动作方法统一返回“值”或 `{"value":..., ...}` 字典，`do()` 会包装成
  `{"ok": True, "action":..., "value":..., "detail": {...}}` 便于报告/日志直接使用。
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Optional

from .errors import DriverError
from .models import DeviceConfig
from .transport import SimulateTransport, Transport, make_transport


def stable_ratio(seed: str, spread: float = 0.004) -> float:
    """由字符串种子生成**确定性**的微小偏差（±spread），让仿真数据稳定可复现

    用 md5 而不是 random：同一台设备同一次测量永远得到同一个值，
    测试脚本才能断言“仿真结果落在阈值内”，也避免每次运行报告值乱跳。
    """
    h = hashlib.md5(str(seed).encode("utf-8")).hexdigest()
    unit = int(h[:8], 16) / 0xFFFFFFFF  # 0..1
    return (unit - 0.5) * 2 * spread


class Driver:
    """仪器驱动基类"""

    driver_key = "generic-scpi"
    family = "generic"
    label = "通用 SCPI 仪器"
    sim_idn = "ATE,SIM-GENERIC,0,1.0.0"
    ACTIONS: dict[str, str] = {
        "idn": "identify",
        "reset": "reset",
        "state": "state",
    }

    def __init__(
        self,
        cfg: DeviceConfig | dict | None = None,
        mode: str = "simulate",
        timeout: Optional[float] = None,
        transport: Optional[Transport] = None,
        alias: str = "",
    ):
        self.cfg = cfg if isinstance(cfg, DeviceConfig) else DeviceConfig.from_dict(cfg or {})
        self.alias = alias or self.cfg.device_id
        self.mode = "real" if str(mode).lower() == "real" else "simulate"
        self.timeout = float(timeout or self.cfg.timeout or 2.0)
        self.cfg.timeout = self.timeout
        self.driver_key = self.cfg.driver or self.driver_key
        self.transport = transport or make_transport(
            self.cfg, self.mode, timeout=self.timeout, handler=self.simulate_command
        )
        self.opened = False
        self.idn = ""
        self.opened_at = ""
        self.commands: list[str] = []
        self.last_error = ""
        self._teardown: list[str] = []

    # ------------------------------------------------------------ 基本属性

    @property
    def simulate(self) -> bool:
        return self.mode != "real"

    @property
    def device_id(self) -> str:
        return self.cfg.device_id

    @property
    def resource(self) -> str:
        return self.cfg.resource or self.transport.kind

    # ------------------------------------------------------------ 会话

    def open(self) -> dict:
        """建立会话并识别仪器（真实/仿真同一入口）"""
        try:
            self.transport.open()
        except DriverError:
            self.last_error = "链路建立失败"
            raise
        self.opened = True
        self.opened_at = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.idn = self.identify()["value"]
        except DriverError as e:
            self.idn = f"(未识别: {e})"
            self.last_error = str(e)
        return {
            "ok": True,
            "device_id": self.device_id,
            "alias": self.alias,
            "driver": self.driver_key,
            "mode": self.mode,
            "transport": self.transport.kind,
            "resource": self.resource,
            "idn": self.idn,
            "simulated": self.simulate,
        }

    def ensure_open(self) -> "Driver":
        if not self.opened:
            self.open()
        return self

    def close(self) -> dict:
        for cmd in self._teardown:
            try:
                self.write(cmd)
            except DriverError:
                pass
        self._teardown.clear()
        try:
            self.transport.close()
        finally:
            self.opened = False
        return {"ok": True, "device_id": self.device_id, "commands": len(self.commands)}

    def on_close(self, command: str) -> None:
        """登记会话结束时要下发的命令（如输出关闭），保证不会把仪器留在危险状态"""
        self._teardown.append(command)

    # ------------------------------------------------------------ 命令收发

    def write(self, command: str) -> None:
        self.commands.append(command)
        try:
            self.transport.write(command)
        except DriverError as e:
            self.last_error = str(e)
            raise DriverError(f"[{self.alias}] {e}", device_id=self.device_id, detail=getattr(e, "detail", "")) from e

    def query(self, command: str, timeout: Optional[float] = None) -> str:
        self.commands.append(command)
        try:
            return self.transport.query(command, timeout=timeout)
        except DriverError as e:
            self.last_error = str(e)
            raise DriverError(f"[{self.alias}] {e}", device_id=self.device_id, detail=getattr(e, "detail", "")) from e

    def read(self, timeout: Optional[float] = None) -> str:
        try:
            return self.transport.read(timeout=timeout)
        except DriverError as e:
            self.last_error = str(e)
            raise DriverError(f"[{self.alias}] {e}", device_id=self.device_id) from e

    # ------------------------------------------------------------ 动作分发

    def resolve(self, action: str) -> str:
        name = self.ACTIONS.get(action, action)
        if not hasattr(self, name):
            raise DriverError(
                f"驱动 {self.driver_key} 不支持动作: {action}",
                device_id=self.device_id,
                detail=f"可用动作: {', '.join(sorted(self.ACTIONS))}",
            )
        return name

    def do(self, action: str, **params) -> dict:
        """执行一个动作，返回统一结构（失败抛出 DriverError）"""
        method = self.resolve(action)
        result = getattr(self, method)(**params)
        if isinstance(result, dict) and "value" in result:
            payload = dict(result)
            value = payload.pop("value")
        else:
            payload, value = {}, result
        return {
            "ok": True,
            "device_id": self.device_id,
            "alias": self.alias,
            "driver": self.driver_key,
            "action": action,
            "value": value,
            "detail": payload,
            "simulated": self.simulate,
        }

    def actions(self) -> list[str]:
        return sorted(set(self.ACTIONS))

    # ------------------------------------------------------------ 仿真钩子

    def simulate_command(self, command: str) -> str:
        """仿真模式下对任意命令的响应（子类按自己的协议覆盖）"""
        return "0"

    # ------------------------------------------------------------ 通用动作

    def identify(self) -> dict:
        if self.simulate:
            return {"value": self.sim_idn, "simulated": True}
        return {"value": self.query("*IDN?")}

    def reset(self) -> dict:
        if self.simulate:
            return {"value": "OK", "simulated": True}
        return {"value": self.query("*RST;*OPC?")}

    def state(self) -> dict:
        return {
            "device_id": self.device_id,
            "alias": self.alias,
            "name": self.cfg.name,
            "model": self.cfg.model,
            "vendor": self.cfg.vendor,
            "category": self.cfg.category,
            "interface": self.cfg.interface,
            "resource": self.resource,
            "driver": self.driver_key,
            "family": self.family,
            "mode": self.mode,
            "connected": self.opened,
            "idn": self.idn,
            "opened_at": self.opened_at,
            "commands": len(self.commands),
            "last_error": self.last_error,
            "transport": self.transport.describe(),
        }

    # ------------------------------------------------------------ 报告辅助

    def log_lines(self) -> list[str]:
        return list(self.commands)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} {self.device_id} {self.mode}>"


class PassiveDriver(Driver):
    """被动设备（夹具 / 探针组等）：只登记信息，不做任何通讯"""

    driver_key = "passive"
    family = "passive"
    label = "被动设备（无通讯）"
    sim_idn = "ATE,SIM-PASSIVE,0,1.0.0"
    ACTIONS = {"idn": "identify", "state": "state", "describe": "describe_device"}

    def __init__(self, cfg=None, mode: str = "simulate", timeout: Optional[float] = None, **kw):
        _cfg = cfg if isinstance(cfg, DeviceConfig) else DeviceConfig.from_dict(cfg or {})
        super().__init__(
            _cfg,
            mode="simulate",
            timeout=timeout,
            transport=SimulateTransport(_cfg),
            alias=kw.get("alias", ""),
        )

    def describe_device(self) -> dict:
        return {"value": self.cfg.describe()}

    def identify(self) -> dict:
        return {"value": self.sim_idn, "simulated": True}
