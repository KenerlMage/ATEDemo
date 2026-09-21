# -*- coding: utf-8 -*-
"""ATE Runner - 仪器驱动层（drivers）

分层（依赖单向）：`tools / TPS 运行时` -> `drivers` -> `drivers.transport`

* 同一个驱动类同时服务**真实仪器**与**离线仿真**，只在 transport 层分叉
  （`SocketTransport` / `SerialTransport` / `SimulateTransport`）。
* 驱动按“注册库里的设备信息”挑选：先具体型号规格（如泰克 MSO5 系列示波器），
  再按类别（万用表 / 电源 / 电子负载 / 信号源 / 运动控制），最后兜底通用 SCPI。
* 零第三方依赖：串口驱动仅在装了 `pyserial` 时可用，未装时给出明确中文提示。
"""

from .errors import DriverError, DriverNotFound, TransportError  # noqa: F401
from .models import DeviceConfig  # noqa: F401
from .base import Driver  # noqa: F401
from .factory import (  # noqa: F401
    DRIVER_SPECS_KEYS,
    get_driver,
    list_specs,
    release_all,
    resolve_driver_class,
    resolve_driver_key,
)

__all__ = [
    "Driver",
    "DeviceConfig",
    "DriverError",
    "DriverNotFound",
    "TransportError",
    "get_driver",
    "release_all",
    "resolve_driver_class",
    "resolve_driver_key",
    "list_specs",
    "DRIVER_SPECS_KEYS",
]

__version__ = "1.0.0"
