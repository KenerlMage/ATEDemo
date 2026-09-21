# -*- coding: utf-8 -*-
"""设备配置模型：把 SQLite `device_registry` 行 / 注册库设备字典归一化成驱动可用的对象"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

NET_INTERFACES = {"LAN", "ETHERNET", "TCP", "IP"}
SERIAL_INTERFACES = {"SERIAL", "RS232", "RS485", "UART", "COM"}


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
    """一台可编程（或被动）设备的连接与角色信息"""

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
    driver: str = ""
    note: str = ""
    extra: dict = field(default_factory=dict)

    # ---------- 构造 ----------

    @classmethod
    def from_dict(cls, data: dict) -> "DeviceConfig":
        data = dict(data or {})
        iface = str(data.get("interface") or "NONE").upper()
        cfg = cls(
            bench_id=str(data.get("bench_id") or ""),
            device_id=str(data.get("device_id") or data.get("id") or ""),
            name=str(data.get("name") or ""),
            model=str(data.get("model") or ""),
            vendor=str(data.get("vendor") or ""),
            category=str(data.get("category") or ""),
            role=str(data.get("role") or ""),
            interface=iface,
            protocol=str(data.get("protocol") or ""),
            host=str(data.get("host") or ""),
            port=_as_int(data.get("port")),
            serial_port=str(data.get("serial_port") or ""),
            baudrate=_as_int(data.get("baudrate")),
            address=str(data.get("address") or ""),
            channel=str(data.get("channel") or ""),
            programmable=bool(data.get("programmable")),
            required=bool(data.get("required", True)),
            configured=bool(data.get("configured")),
            timeout=_as_float(data.get("timeout"), 2.0) or 2.0,
            driver=str(data.get("driver") or ""),
            note=str(data.get("note") or ""),
            extra=dict(data.get("extra") or {}),
        )
        if not cfg.timeout:
            cfg.timeout = 2.0
        return cfg

    @classmethod
    def from_row(cls, row: Any) -> "DeviceConfig":
        """sqlite3.Row / dict -> DeviceConfig"""
        if row is None:
            return cls()
        if isinstance(row, dict):
            return cls.from_dict(row)
        return cls.from_dict({k: row[k] for k in row.keys()})

    # ---------- 便捷属性 ----------

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
        """VISA 风格资源描述：用于日志与报告展示"""
        if self.is_net and self.host:
            return f"TCPIP0::{self.host}::{self.port or ''}::SOCKET"
        if self.is_serial and self.serial_port:
            return f"ASRL::{self.serial_port}::{self.baudrate or 9600}::INSTR"
        return self.address or ""

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
            "resource": self.resource,
            "programmable": self.programmable,
            "driver": self.driver,
        }
