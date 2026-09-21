# -*- coding: utf-8 -*-
"""设备连接参数对象（驱动只认这个对象，不认平台的 SQLite 行 / 注册库字典）

`from_dict()` 兼容平台注册库的字段名（`id` → `device_id`），因此平台侧不需要为
驱动库改数据结构；驱动侧也不需要 import 平台任何模块 —— 这是"可独立开发"的前提。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

NET_INTERFACES = frozenset({"LAN", "ETHERNET", "TCP", "IP"})
SERIAL_INTERFACES = frozenset({"SERIAL", "RS232", "RS485", "UART", "COM"})


def _as_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class DeviceConfig:
    """一台设备的连接参数与角色信息（与注册库一条设备记录一一对应）"""

    bench_id: str = ""
    device_id: str = ""
    name: str = ""
    model: str = ""
    vendor: str = ""
    category: str = ""
    role: str = ""
    interface: str = "NONE"
    protocol: str = ""
    host: str = ""
    port: Optional[int] = None
    serial_port: str = ""
    baudrate: Optional[int] = None
    address: str = ""
    channel: str = ""
    programmable: bool = False
    required: bool = True
    configured: bool = False
    timeout: float = 2.0
    note: str = ""
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict | None) -> "DeviceConfig":
        d = dict(data or {})
        return cls(
            bench_id=str(d.get("bench_id") or ""),
            device_id=str(d.get("device_id") or d.get("id") or ""),
            name=str(d.get("name") or ""),
            model=str(d.get("model") or ""),
            vendor=str(d.get("vendor") or ""),
            category=str(d.get("category") or ""),
            role=str(d.get("role") or ""),
            interface=str(d.get("interface") or "NONE").upper(),
            protocol=str(d.get("protocol") or ""),
            host=str(d.get("host") or ""),
            port=_as_int(d.get("port")),
            serial_port=str(d.get("serial_port") or ""),
            baudrate=_as_int(d.get("baudrate")),
            address=str(d.get("address") or ""),
            channel=str(d.get("channel") or ""),
            programmable=bool(d.get("programmable")),
            required=bool(d.get("required", True)),
            configured=bool(d.get("configured")),
            timeout=_as_float(d.get("timeout"), 2.0) or 2.0,
            note=str(d.get("note") or ""),
            extra=dict(d.get("extra") or {}),
        )

    @classmethod
    def from_row(cls, row: Any) -> "DeviceConfig":
        """sqlite3.Row / dict -> DeviceConfig"""
        if row is None:
            return cls()
        if isinstance(row, dict):
            return cls.from_dict(row)
        return cls.from_dict({k: row[k] for k in row.keys()})

    @property
    def alias(self) -> str:
        return self.device_id

    @property
    def is_net(self) -> bool:
        return self.interface in NET_INTERFACES

    @property
    def is_serial(self) -> bool:
        return self.interface in SERIAL_INTERFACES

    @property
    def resource(self) -> str:
        """VISA 风格资源描述（报告与日志用，不参与连接）"""
        if self.is_net and self.host:
            return f"TCPIP0::{self.host}::{self.port or ''}::SOCKET"
        if self.is_serial and self.serial_port:
            return f"ASRL::{self.serial_port}::{self.baudrate or 9600}::INSTR"
        return self.address or ""

    @property
    def visa_resource(self) -> str:
        """规范 VISA 资源名（`backend="visa"` 时才真正参与连接）

        `resource` 保留作报告与日志用的可读描述；真正连 VISA 时用这个：
        网口 `TCPIP0::host::port::SOCKET`，串口 `ASRL6::INSTR`（波特率与帧格式走会话属性）。
        """
        from .endpoint import visa_resource_for

        kind = "LAN" if self.is_net else ("SERIAL" if self.is_serial else "")
        return visa_resource_for(kind, self.host or "", self.port, self.serial_port or "")

    @property
    def backend(self) -> str:
        """档案里登记的传输后端（`extra.backend`，没写就是 native）"""
        from .endpoint import DEFAULT_BACKEND, normalize_backend

        return normalize_backend((self.extra or {}).get("backend")) or DEFAULT_BACKEND

    def transport_kind(self) -> str:
        if self.is_net and self.host and self.port:
            return "socket"
        if self.is_serial and self.serial_port:
            return "serial"
        return "none"

    def describe(self) -> dict:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "model": self.model,
            "vendor": self.vendor,
            "category": self.category,
            "interface": self.interface,
            "backend": self.backend,
            "resource": self.resource,
            "visa_resource": self.visa_resource,
            "programmable": self.programmable,
        }
