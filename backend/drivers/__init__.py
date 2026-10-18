# -*- coding: utf-8 -*-
"""ATE Runner - 仪器驱动层（drivers）

分层（依赖单向）：`tools / TPS 运行时` -> `drivers` -> `drivers.transport`

* 同一个驱动类同时服务**真实仪器**与**离线仿真**，只在 transport 层分叉
  （`SocketTransport` / `SerialTransport` / `SimulateTransport`）。
* 驱动按测试台注册时选定的 `device_type` 直接取实现，不再按型号/类别打分。
  缺失或非法类型直接报错，不回退到相似驱动。
* 零第三方依赖：串口驱动仅在装了 `pyserial` 时可用，未装时给出明确中文提示。
"""

from .errors import DriverError, DriverNotFound, TransportError  # noqa: F401
from .models import DeviceConfig  # noqa: F401
from .base import Driver  # noqa: F401
from .factory import (  # noqa: F401
    DRIVER_TYPE_KEYS,
    DRIVER_TYPE_MAP,
    DRIVER_TYPES,
    default_device_type,
    get_driver,
    list_driver_types,
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
    "list_driver_types",
    "list_specs",
    "DRIVER_TYPES",
    "DRIVER_TYPE_KEYS",
    "DRIVER_TYPE_MAP",
    "default_device_type",
]

__version__ = "1.0.0"
