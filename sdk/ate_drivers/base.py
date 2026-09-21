# -*- coding: utf-8 -*-
"""驱动基类：会话管理、命令收发、动作分发、能力协商、仿真钩子

驱动子类只需要做三件事：

1. 声明类属性：`driver_key` / `family` / `label` / `driver_version` / `CAPABILITIES` / `ACTIONS`；
2. 实现动作方法（返回"值"或 `{"value": ..., 其他字段}`）；
3. 覆盖 `simulate_command()`，让仿真模式能回答自己的命令。

其余（会话缓存、打开/关闭、仿真/真实分叉、动作分发、统一返回结构、安全退出、能力检查）
全部由基类提供 —— 这就是"新驱动不会各自发明一套行为"的保证。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .capabilities import KNOWN_CAPABILITIES, matches, validate
from .contract import API_VERSION, RESULT_SCHEMA, check_compatible, deprecated  # noqa: F401
from .errors import DriverError, UnsupportedCapability
from .models import DeviceConfig
from .endpoint import backends_of, resolve_backend  # noqa: E402
from .transport import SimulateTransport, Transport, make_transport, visa_available

CORE_ACTIONS = {"identify": "identify", "reset": "reset", "state": "state"}


class InstrumentDriver:
    """仪器驱动基类（所有驱动都必须继承它）"""

    # ---- 元数据（子类必须覆盖前四项）----
    driver_key = "generic-scpi"
    family = "generic"
    label = "通用 SCPI 仪器"
    driver_version = "0.1.0"
    api_version = API_VERSION
    sim_idn = "ATE,SIM-GENERIC,0,1.0.0"
    built_with = "ate_drivers"

    # ---- 能力与动作 ----
    CAPABILITIES: tuple[str, ...] = ("identify", "reset", "state")
    ACTIONS: dict[str, str] = dict(CORE_ACTIONS)
    # 旧动作名 -> 新能力名（保住历史 TPS 的调用；新代码不允许再使用左边这些名字）
    ALIASES: dict[str, str] = {}
    SIMULATE_SUPPORTED = True

    def __init__(
        self,
        cfg: DeviceConfig | dict | None = None,
        mode: str = "simulate",
        timeout: Optional[float] = None,
        transport: Optional[Transport] = None,
        alias: str = "",
        backend: Any = None,
    ):
        self.cfg = cfg if isinstance(cfg, DeviceConfig) else DeviceConfig.from_dict(cfg or {})
        self.alias = alias or self.cfg.device_id
        self.mode = "real" if str(mode).lower() == "real" else "simulate"
        self.timeout = float(timeout or self.cfg.timeout or 2.0)
        self.cfg.timeout = self.timeout
        # 传输后端：显式参数 > 档案 extra.backend > native；型号没声明的后端当场拒绝
        self.backend_requested, self.backend = resolve_backend(
            backend, self.cfg, visa_available()
        )
        self.declared_backends = backends_of(type(self))
        if self.backend not in self.declared_backends:
            raise ConfigurationError(
                f"驱动 {self.driver_key} 不支持 {self.backend} 后端",
                device_id=self.cfg.device_id,
                detail="该驱动声明的后端：" + " / ".join(self.declared_backends),
            )
        self.transport = transport or make_transport(
            self.cfg, self.mode, timeout=self.timeout, handler=self.simulate_command,
            backend=self.backend
        )
        self.opened = False
        self.idn = ""
        self.opened_at = ""
        self.commands: list[str] = []
        self.warnings: list[str] = []
        self.last_error = ""
        self._teardown: list[str] = []
        compatible, reason = check_compatible(self.api_version)
        if not compatible:
            from .errors import ContractMismatch

            raise ContractMismatch(reason, device_id=self.device_id, detail=f"驱动契约 {self.api_version}")

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

    @property
    def visa_resource(self) -> str:
        """规范 VISA 资源名（`backend="visa"` 时才参与连接）"""
        return self.cfg.visa_resource or self.transport.kind

    def capabilities(self) -> list[str]:
        return sorted(set(self.CAPABILITIES))

    def supports(self, capability: str) -> bool:
        """支持 `scope.*` 这类通配（新能力加进来时，通配调用不会失效）"""
        return any(matches(capability, c) for c in self.CAPABILITIES)

    def require(self, capability: str) -> None:
        if not self.supports(capability):
            raise UnsupportedCapability(
                f"驱动 {self.driver_key} 不支持能力 {capability}",
                device_id=self.device_id,
                detail="已声明能力: " + (", ".join(self.capabilities()) or "(无)"),
            )

    def validate_declaration(self) -> tuple[list[str], list[str], list[str]]:
        """一致性检查用：`(缺失的家族基线能力, 平台不认识的能力, 厂商扩展能力)`"""
        return validate(self.CAPABILITIES, self.family)

    def meta(self) -> dict:
        """驱动自描述（清单校验、报告落盘、诊断页都用它）"""
        return {
            "schema": "ate.driver.manifest.v1",
            "key": self.driver_key,
            "version": self.driver_version,
            "api": self.api_version,
            "family": self.family,
            "label": self.label,
            "capabilities": self.capabilities(),
            "actions": sorted(set(self.ACTIONS) | set(self.ALIASES)),
            "simulate": bool(self.SIMULATE_SUPPORTED),
            "backends": list(self.declared_backends),
            "class": type(self).__name__,
        }

    # ------------------------------------------------------------ 会话

    def open(self) -> dict:
        try:
            self.transport.open()
        except DriverError:
            self.last_error = "链路建立失败"
            raise
        self.opened = True
        self.opened_at = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.idn = str(self.identify()["value"])
        except DriverError as e:
            self.idn = f"(未识别: {e})"
            self.last_error = str(e)
        return {
            "ok": True,
            "device_id": self.device_id,
            "alias": self.alias,
            "driver": self.driver_key,
            "driver_version": self.driver_version,
            "api": self.api_version,
            "mode": self.mode,
            "transport": self.transport.kind,
            "backend": self.backend,
            "resource": self.resource,
            "idn": self.idn,
            "simulated": self.simulate,
        }

    def ensure_open(self) -> "InstrumentDriver":
        if not self.opened:
            self.open()
        return self

    def close(self) -> dict:
        """关闭会话：先下发安全退出命令（不把仪器留在危险状态），再关链路"""
        for cmd in list(self._teardown):
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

    def protect_on_close(self, command: str) -> None:
        """登记会话结束时要下发的命令（如关闭输出、停止采集）"""
        if command and command not in self._teardown:
            self._teardown.append(command)

    # ------------------------------------------------------------ 命令收发

    def write(self, command: str) -> None:
        self.commands.append(command)
        try:
            self.transport.write(command)
        except DriverError as e:
            self.last_error = str(e)
            raise DriverError(f"[{self.alias}] {e}", device_id=self.device_id,
                              detail=getattr(e, "detail", ""), code=e.code) from e

    def query(self, command: str, timeout: Optional[float] = None) -> str:
        self.commands.append(command)
        try:
            return self.transport.query(command, timeout=timeout)
        except DriverError as e:
            self.last_error = str(e)
            raise DriverError(f"[{self.alias}] {e}", device_id=self.device_id,
                              detail=getattr(e, "detail", ""), code=e.code) from e

    def read(self, timeout: Optional[float] = None) -> str:
        try:
            return self.transport.read(timeout=timeout)
        except DriverError as e:
            self.last_error = str(e)
            raise DriverError(f"[{self.alias}] {e}", device_id=self.device_id, code=e.code) from e

    # ------------------------------------------------------------ 动作分发

    def normalize_action(self, action: str) -> str:
        """旧名 -> 规范能力名（`waveform` → `scope.acquire_waveform`）"""
        name = str(action or "").strip()
        return self.ALIASES.get(name, name)

    def resolve(self, action: str) -> str:
        capability = self.normalize_action(action)
        method = self.ACTIONS.get(capability)
        if not method or not hasattr(self, method):
            raise UnsupportedCapability(
                f"驱动 {self.driver_key} 不支持动作: {action}",
                device_id=self.device_id,
                detail="可用动作: " + ", ".join(sorted(set(self.ACTIONS) | set(self.ALIASES))),
            )
        return method

    def do(self, action: str, **params) -> dict:
        """执行一个动作，返回**统一结构**（失败抛 DriverError）"""
        capability = self.normalize_action(action)
        method = self.resolve(action)
        started = time.time()
        result = getattr(self, method)(**params)
        if isinstance(result, dict) and "value" in result:
            payload = dict(result)
            value = payload.pop("value")
        else:
            payload, value = {}, result
        quality = str(payload.pop("quality", "simulated" if self.simulate else "good"))
        warnings = list(payload.pop("warnings", []) or [])
        return {
            "ok": True,
            "schema": RESULT_SCHEMA,
            "device_id": self.device_id,
            "alias": self.alias,
            "driver": self.driver_key,
            "driver_version": self.driver_version,
            "api": self.api_version,
            "action": capability,
            "requested": str(action),
            "value": value,
            "detail": payload,
            "quality": quality,
            "source": "simulate" if self.simulate else "instrument",
            "simulated": self.simulate,
            "elapsed_ms": int((time.time() - started) * 1000),
            "warnings": warnings,
        }

    def actions(self) -> list[str]:
        return sorted(set(self.ACTIONS) | set(self.ALIASES))

    def contract_report(self) -> dict:
        """一致性测试套件与诊断页要的契约快照"""
        missing, unknown, vendor = self.validate_declaration()
        return {
            "driver": self.driver_key,
            "version": self.driver_version,
            "api": self.api_version,
            "family": self.family,
            "capabilities": self.capabilities(),
            "missing_required": missing,
            "unknown": unknown,
            "vendor_extensions": vendor,
            "known_capabilities": sorted(KNOWN_CAPABILITIES),
            "simulate": bool(self.SIMULATE_SUPPORTED),
        }

    # ------------------------------------------------------------ 仿真钩子

    def simulate_command(self, command: str) -> str:
        """仿真模式下对任意命令的应答（型号驱动按自己的协议覆盖）"""
        return "0"

    # ------------------------------------------------------------ 通用动作

    def identify(self) -> dict:
        if self.simulate:
            return {"value": self.sim_idn, "quality": "simulated"}
        return {"value": self.query("*IDN?")}

    def reset(self) -> dict:
        if self.simulate:
            return {"value": "OK", "quality": "simulated"}
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
            "driver_version": self.driver_version,
            "api": self.api_version,
            "family": self.family,
            "mode": self.mode,
            "connected": self.opened,
            "idn": self.idn,
            "opened_at": self.opened_at,
            "commands": len(self.commands),
            "last_error": self.last_error,
            "capabilities": self.capabilities(),
            "transport": self.transport.describe(),
        }

    # ------------------------------------------------------------ 报告辅助

    def log_lines(self) -> list[str]:
        return list(self.commands)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} {self.device_id} {self.mode} api={self.api_version}>"


class PassiveDriver(InstrumentDriver):
    """被动设备（夹具 / 探针组）：只登记信息，不做任何通讯"""

    driver_key = "passive-device"
    family = "passive"
    label = "被动设备（无通讯）"
    sim_idn = "ATE,SIM-PASSIVE,0,1.0.0"
    CAPABILITIES = ("identify", "reset", "state", "passive.describe")
    ACTIONS = dict(CORE_ACTIONS, **{"passive.describe": "describe_device"})

    def __init__(self, cfg=None, mode: str = "simulate", timeout: Optional[float] = None, **kw):
        _cfg = cfg if isinstance(cfg, DeviceConfig) else DeviceConfig.from_dict(cfg or {})
        super().__init__(_cfg, mode="simulate", timeout=timeout,
                         transport=SimulateTransport(_cfg, handler=self.simulate_command),
                         alias=kw.get("alias", ""))

    def describe_device(self) -> dict:
        return {"value": self.cfg.describe(), "quality": "good"}

    def identify(self) -> dict:
        return {"value": self.sim_idn, "quality": "simulated"}
