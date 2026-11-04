# -*- coding: utf-8 -*-
"""光功率计（自动发现样例）。

驱动实现位于 ``sdk/ate_drivers/generic/power_meter.py``；本文件保留一个**本地子类**
作为注册点，使 ``discovery.scan()`` 仍能在 ``drivers/`` 目录里发现它——自动发现只
接受定义在当前模块中的类，这一约束用于防止 re-export 被误判为新增驱动。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SDK = _ROOT / "sdk"
if _SDK.is_dir() and str(_SDK) not in sys.path:
    sys.path.insert(0, str(_SDK))

from ate_drivers.generic.power_meter import (  # noqa: E402
    SIM_BASE_MW,
    WAVELENGTH_RANGE,
    PowerMeterDriver as _SdkPowerMeter,
)


class PowerMeterDriver(_SdkPowerMeter):
    """光功率计：实现继承自 SDK，本类仅作为发现与注册的本地入口。"""


__all__ = ["PowerMeterDriver", "WAVELENGTH_RANGE", "SIM_BASE_MW"]
