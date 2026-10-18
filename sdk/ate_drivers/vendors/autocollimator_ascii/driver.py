# -*- coding: utf-8 -*-
"""自准直仪驱动（ASCII 串口）—— 能力层 demo。

家族：generic（平台没有 autocollimator 家族，角度测量属于厂商语义，
因此能力一律挂在 `vendor.autocollimator.*` 下，不占用平台命名空间）。

被测对象是角度：`x` 为方位角、`y` 为俯仰角，单位 arcsec。
ASCII 命令按常见自准直仪（Möller-Wedel ELCOMAT / Trioptics 系列）整理，
不同厂商的分隔符与量纲收在 `COMMANDS` 与 `SCALE` 里，换型号只改这两处。
"""
from __future__ import annotations

from ...base import InstrumentDriver
from ...errors import ConfigurationError, ProtocolError

__all__ = ["AsciiAutocollimator"]

MODES = ("angle", "tilt", "continuous")


class AsciiAutocollimator(InstrumentDriver):
    driver_key = "autocollimator-ascii"
    family = "generic"
    vendor = "Generic"
    label = "自准直仪 · ASCII 串口系列"
    driver_version = "1.0.0"
    INTERFACES = ("SERIAL",)
    INTERFACE = "SERIAL"
    BACKENDS = ("native",)
    MODELS = ("AC-100", "AC-200", "ELCOMAT-3000")
    sim_idn = "AUTOCOLLIMATOR,AC-100,SIM-AC,1.0.0"

    CAPABILITIES = (
        "identify",
        "reset",
        "state",
        "vendor.autocollimator.measure",
        "vendor.autocollimator.zero",
        "vendor.autocollimator.mode",
    )

    ACTIONS = {
        "identify": "identify",
        "reset": "reset",
        "state": "state",
        "vendor.autocollimator.measure": "measure_angle",
        "vendor.autocollimator.zero": "zero",
        "vendor.autocollimator.mode": "set_mode",
        "measure_angle": "measure_angle",
        "zero": "zero",
        "set_mode": "set_mode",
    }

    COMMANDS = {
        "idn": "*IDN?",
        "reset": "*RST",
        "measure": "M?",
        "zero": "Z",
        "mode": "MODE {mode}",
        "status": "ST?",
        "error_query": "E?",
    }

    # 应答单位换算：设备若返回 µrad，乘该系数得到 arcsec
    SCALE = {"arcsec": 1.0, "urad": 4.84813681109536e-1}
    UNIT = "arcsec"

    def _send(self, command: str) -> None:
        """仿真模式不发链路，避免给下一次 query 留下脏应答。"""
        if not self.simulate:
            self.write(command)

    def measure_angle(self) -> dict:
        """读一次角度，返回 x/y 与合成偏差。"""
        self.ensure_open()
        raw = self.query(self.COMMANDS["measure"])
        parts = [p for p in str(raw).replace(",", ";").split(";") if p.strip()]
        if len(parts) < 2:
            raise ProtocolError(
                f"角度应答需两个分量，实际: {raw!r}",
                device_id=self.cfg.device_id,
                detail="期望形如 +0.0012;-0.0008（arcsec）",
            )
        try:
            x = float(parts[0]) * self.SCALE[self.UNIT]
            y = float(parts[1]) * self.SCALE[self.UNIT]
        except (TypeError, ValueError) as exc:
            raise ProtocolError(
                f"角度应答无法解析: {raw!r}",
                device_id=self.cfg.device_id,
            ) from exc
        return {
            "value": x,
            "unit": self.UNIT,
            "x_arcsec": x,
            "y_arcsec": y,
            "magnitude_arcsec": (x * x + y * y) ** 0.5,
        }

    def zero(self) -> dict:
        self.ensure_open()
        self._send(self.COMMANDS["zero"])
        return {"value": True, "unit": "", "detail": "当前姿态置为零位"}

    def set_mode(self, mode: str = "angle") -> dict:
        self.ensure_open()
        m = str(mode or "angle").strip().lower()
        if m not in MODES:
            raise ConfigurationError(
                f"不支持的模式: {mode}",
                device_id=self.cfg.device_id,
                detail="可选：" + " / ".join(MODES),
            )
        self._send(self.COMMANDS["mode"].format(mode=m.upper()))
        return {"value": m, "unit": "", "modes": list(MODES)}

    def state(self) -> dict:
        base = super().state()
        base.update({"mode": "angle", "unit": self.UNIT})
        return base

    # ---------------------------------------------------------------- 仿真
    def simulate_command(self, command: str) -> str:
        cmd = str(command or "").strip().upper()
        if cmd.endswith("*IDN?"):
            return self.sim_idn
        if cmd.startswith("M?"):
            # 确定性应答，重复读取同值
            return "+0.0012;-0.0008"
        if cmd.startswith("ST?"):
            return "READY;ANGLE"
        if cmd.startswith("E?"):
            return "0"
        return "OK"
