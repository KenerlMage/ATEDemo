# -*- coding: utf-8 -*-
"""驱动层异常：唯一真源在 ``sdk/ate_drivers/core/errors.py``。

驱动实现统一走 SDK 之后，异常类型也必须唯一，否则 ``except DriverError``
在本模块捕获不到 SDK 抛出的 ``ConfigurationError`` / ``TransportError``。
本文件只做重新导出，并保持历史模块路径 ``drivers.errors`` 可用。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SDK = _ROOT / "sdk"
if _SDK.is_dir() and str(_SDK) not in sys.path:
    sys.path.insert(0, str(_SDK))

from ate_drivers.core.errors import (  # noqa: E402,F401
    ConfigurationError,
    ContractMismatch,
    DriverError,
    DriverNotFound,
    DriverTimeout,
    ProtocolError,
    TransportError,
    UnsupportedCapability,
)

__all__ = [
    "DriverError",
    "ConfigurationError",
    "DriverNotFound",
    "DriverTimeout",
    "TransportError",
    "ProtocolError",
    "UnsupportedCapability",
    "ContractMismatch",
]
