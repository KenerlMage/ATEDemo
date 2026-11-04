# -*- coding: utf-8 -*-
"""光功率计驱动（SDK 侧）：波长设置、平均次数、归零与功率读取

本驱动是「新增驱动」的落地样例，用于验证一条约定：**新增驱动只需新增本文件**，
不需要修改 `drivers/factory.py`；设备类型由 `discovery.scan()` 自动并入受控清单，
用例侧继续按别名调用，代码不变。

真实模式下发标准 SCPI 命令（`:SENS:POW:WAV` / `:SENS:POW:AVER` / `:CAL:ZERO` /
`:READ:POW?`），功率按瓦特读取后换算为毫瓦。仿真值由 `stable_ratio()` 生成，
同一设备、同一波长、同一平均次数永远得到同一个读数，便于对阈值断言做回归。
"""

from __future__ import annotations

import math
from typing import Optional

from ..base import InstrumentDriver as Driver
from ..errors import DriverError
from .util import stable_ratio

#: 光功率计的波长测量范围（nm）
WAVELENGTH_RANGE = (800.0, 1700.0)

#: 仿真基准功率（mW），对应 1310 nm、4 次平均
SIM_BASE_MW = 1.2345


class PowerMeterDriver(Driver):
    """光功率计：波长设置、平均次数、归零与功率读取"""

    driver_key = "generic-power-meter"
    family = "power"
    label = "光功率计"
    sim_idn = "ATE,SIM-PM,0,1.0.0"

    ACTIONS = {
        "idn": "identify",
        "reset": "reset",
        "state": "state",
        "set_wavelength": "set_wavelength",
        "set_average": "set_average",
        "set_range": "set_range",
        "zero": "zero",
        "read_power": "read_power",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.wavelength_nm = 1310.0
        self.average = 4
        self.range_w = "auto"
        self.zeroed = False

    # ---------------- 配置类动作 ----------------

    def set_wavelength(self, nm: float = 1310.0, **_) -> dict:
        value = float(nm)
        lo, hi = WAVELENGTH_RANGE
        if not lo <= value <= hi:
            raise DriverError(
                f"波长超出测量范围: {value:g} nm",
                device_id=self.device_id,
                detail=f"支持 {lo:g} ~ {hi:g} nm",
            )
        self.wavelength_nm = value
        if not self.simulate:
            self.write(f":SENS:POW:WAV {value:.2f}NM")
        return {"value": value, "unit": "nm"}

    def set_average(self, count: int = 4, **_) -> dict:
        value = max(1, min(int(count), 1024))
        self.average = value
        if not self.simulate:
            self.write(f":SENS:POW:AVER {value}")
        return {"value": value, "unit": "frames"}

    def set_range(self, range_w="auto", **_) -> dict:
        """量程：`auto` 或具体瓦数；真机下发 `:SENS:POW:RANG`"""
        if isinstance(range_w, str) and range_w.strip().lower() == "auto":
            self.range_w = "auto"
            if not self.simulate:
                self.write(":SENS:POW:RANG:AUTO ON")
            return {"value": "auto", "unit": "W"}
        value = float(range_w)
        if value <= 0:
            raise DriverError(f"量程必须为正数: {range_w!r}", device_id=self.device_id)
        self.range_w = value
        if not self.simulate:
            self.write(f":SENS:POW:RANG {value:.9f}W")
        return {"value": value, "unit": "W"}

    def zero(self, **_) -> dict:
        """归零：真机下发后返回 OK；仿真直接置位"""
        self.zeroed = True
        if self.simulate:
            return {"value": "OK", "unit": ""}
        return {"value": self.query(":CAL:ZERO;*OPC?"), "unit": ""}

    # ---------------- 测量类动作 ----------------

    def read_power(self, unit: str = "mW", **_) -> dict:
        mw = self._read_mw()
        want = str(unit or "mW").strip().lower()
        if want in ("w", "watt", "watts"):
            value, out_unit = mw / 1000.0, "W"
        elif want in ("dbm",):
            value = 10.0 * math.log10(mw) if mw > 0 else float("-inf")
            out_unit = "dBm"
        else:
            value, out_unit = mw, "mW"
        return {
            "value": round(float(value), 9),
            "unit": out_unit,
            "wavelength_nm": self.wavelength_nm,
            "average": self.average,
            "simulated": self.simulate,
        }

    def _read_mw(self) -> float:
        """读功率（mW）：仿真走确定性模型，真机按 SCPI 返回瓦特换算"""
        if self.simulate:
            seed = f"{self.device_id}:{self.wavelength_nm:.2f}:{self.average}"
            drift = stable_ratio(seed, spread=0.03)
            spectral = 1.0 + (1310.0 - self.wavelength_nm) / 1000.0 * 0.2
            return SIM_BASE_MW * spectral * (1.0 + drift)
        try:
            watts = float(str(self.query(":READ:POW?")).strip())
        except (TypeError, ValueError) as e:
            raise DriverError(
                "光功率读数无法解析", device_id=self.device_id,
                detail="期望仪器返回瓦特为单位的数值（:READ:POW?）",
            ) from e
        return watts * 1000.0

    # ---------------- 仿真钩子 ----------------

    def simulate_command(self, command: str) -> str:
        upper = str(command or "").upper()
        if "READ:POW" in upper:
            return f"{self._read_mw() / 1000.0:.9E}"
        if "CAL:ZERO" in upper:
            return "1"
        if "SENS:POW:WAV" in upper and "?" not in upper:
            return "1"
        if "SENS:POW:AVER" in upper and "?" not in upper:
            return "1"
        return super().simulate_command(command)

    def state(self) -> dict:
        info = dict(super().state())
        info.update({
            "wavelength_nm": self.wavelength_nm,
            "average": self.average,
            "range_w": self.range_w,
            "zeroed": self.zeroed,
        })
        return info


__all__ = ["PowerMeterDriver", "SIM_BASE_MW", "WAVELENGTH_RANGE"]
