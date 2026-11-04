# -*- coding: utf-8 -*-
"""驱动基类：唯一真源在 ``sdk/ate_drivers/base.py``。

驱动实现全部在 SDK 之后，本模块只做重新导出，保留 ``drivers.base`` 路径：
``discovery`` 用它做类型判定，``factory`` 用它取被动设备实现。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SDK = _ROOT / "sdk"
if _SDK.is_dir() and str(_SDK) not in sys.path:
    sys.path.insert(0, str(_SDK))

from ate_drivers.base import InstrumentDriver as Driver  # noqa: E402,F401
from ate_drivers.base import PassiveDriver  # noqa: E402,F401

__all__ = ["Driver", "PassiveDriver"]
