# -*- coding: utf-8 -*-
"""PI 六足并联定位平台（Hexapod）驱动 —— 能力层 demo。

家族：motion。能力：core + motion.move + motion.read_position + 三个厂商扩展。

命令语言按 PI GCS（General Command Set）写：`SVO` 伺服使能、`MOV` 绝对运动、
`POS?` 读位置、`HLT` 急停、`FRF` 回参考点。不同控制器固件（C-887 / E-816）
个别命令大小写与参数格式有差异，统一收在 `COMMANDS` 表里，换控制器只改表。

单位约定（写死，避免现场把 µm 当 mm）：
    X / Y / Z   -> µm
    U / V / W   -> µrad
"""
from __future__ import annotations

from ...base import InstrumentDriver
from ...errors import ConfigurationError, ProtocolError

__all__ = ["PiHexapod"]

AXES = ("X", "Y", "Z", "U", "V", "W")
LINEAR_AXES = ("X", "Y", "Z")
ROTARY_AXES = ("U", "V", "W")


class PiHexapod(InstrumentDriver):
    driver_key = "pi-hexapod"
    family = "motion"
    vendor = "Physik Instrumente"
    label = "六足并联定位平台 · PI H-811 / H-820 / H-840"
    driver_version = "1.0.0"
    INTERFACES = ("LAN", "SERIAL")
    INTERFACE = "LAN"
    BACKENDS = ("native", "visa")
    MODELS = ("H-811", "H-820", "H-840", "H-850")
    sim_idn = "PI,H-811,SIM-HEXAPOD,1.0.0"

    CAPABILITIES = (
        "identify",
        "reset",
        "state",
        "motion.move",
        "motion.read_position",
        "vendor.pi.home",
        "vendor.pi.stop",
        "vendor.pi.servo",
    )

    ACTIONS = {
        "identify": "identify",
        "reset": "reset",
        "state": "state",
        "motion.move": "move",
        "motion.read_position": "read_position",
        "vendor.pi.home": "home",
        "vendor.pi.stop": "stop",
        "vendor.pi.servo": "set_servo",
        # 口语别名：TPS 里写 move / position 也能落到同一方法
        "move": "move",
        "read_position": "read_position",
        "home": "home",
        "stop": "stop",
        "set_servo": "set_servo",
    }

    COMMANDS = {
        "idn": "*IDN?",
        "reset": "*RST",
        "servo": "SVO {axis} {state}",
        "move_abs": "MOV {axis} {value:.4f}",
        "read_pos": "POS? {axis}",
        "stop": "HLT",
        "reference": "FRF {axis}",
        "error_query": "ERR?",
    }

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        # 仿真初值模拟"已停在中位"的平台；真机不读这个缓存，每次都问设备
        self._last: dict = {"X": 12.5, "Y": -3.25, "Z": 0.0, "U": 0.0, "V": 0.0, "W": 0.0}

    # ---------------------------------------------------------------- 工具
    def _send(self, command: str) -> None:
        """下发写命令。仿真模式下不发链路：仿真传输会给每次 write 入队一个
        应答，随后 query 会读到这个脏值（平台 family 层同样是这个约定）。"""
        if not self.simulate:
            self.write(command)

    @staticmethod
    def _unit_of(axis: str) -> str:
        return "um" if axis in LINEAR_AXES else "urad"

    def _pos(self, axis: str) -> float:
        self.ensure_open()
        if self.simulate:
            return float(self._last.get(axis, 0.0))
        raw = self.query(self.COMMANDS["read_pos"].format(axis=axis))
        try:
            return float(str(raw).strip().split()[0])
        except (ValueError, IndexError) as exc:
            raise ProtocolError(
                f"位置应答无法解析: {raw!r}",
                device_id=self.cfg.device_id,
                detail=f"轴 {axis}；期望形如 12.500",
            ) from exc

    # ---------------------------------------------------------------- 能力实现
    def move(self, x=None, y=None, z=None, u=None, v=None, w=None) -> dict:
        """绝对定位。零参数调用时返回当前位置（便于一致性检查与安全探测）。"""
        self.ensure_open()
        targets = {"X": x, "Y": y, "Z": z, "U": u, "V": v, "W": w}
        wanted = {a: float(val) for a, val in targets.items() if val is not None}
        if not wanted:
            return {"value": self.read_position()["value"], "unit": "um+urad",
                    "moved": False, "detail": "未指定目标，返回当前位置"}

        for axis, val in wanted.items():
            self._send(self.COMMANDS["move_abs"].format(axis=axis, value=val))
        self._last.update(wanted)
        return {
            "value": dict(self._last),
            "unit": "um+urad",
            "moved": True,
            "axes": sorted(wanted),
            "detail": "X/Y/Z 单位 µm，U/V/W 单位 µrad",
        }

    def read_position(self) -> dict:
        self.ensure_open()
        pos = {axis: self._pos(axis) for axis in AXES}
        self._last.update(pos)
        return {
            "value": pos,
            "unit": "um+urad",
            "linear_unit": "um",
            "rotary_unit": "urad",
            "axes": list(AXES),
        }

    def home(self) -> dict:
        self.ensure_open()
        for axis in AXES:
            self._send(self.COMMANDS["reference"].format(axis=axis))
        return {"value": "referenced", "unit": "", "axes": list(AXES)}

    def stop(self) -> dict:
        self.ensure_open()
        self._send(self.COMMANDS["stop"])
        return {"value": "halted", "unit": ""}

    def set_servo(self, enabled: bool = True) -> dict:
        self.ensure_open()
        state = 1 if enabled else 0
        for axis in AXES:
            self._send(self.COMMANDS["servo"].format(axis=axis, state=state))
        return {"value": bool(enabled), "unit": ""}

    # ---------------------------------------------------------------- 仿真
    def simulate_command(self, command: str) -> str:
        cmd = str(command or "").strip()
        if cmd.endswith("*IDN?"):
            return self.sim_idn
        if cmd.startswith("POS?"):
            axis = cmd.split()[-1].upper()
            # 确定性应答：同一轴每次同值，门禁 C12 要求可复现
            table = {"X": "12.500", "Y": "-3.250", "Z": "0.000",
                     "U": "0.000", "V": "0.000", "W": "0.000"}
            return table.get(axis, "0.000")
        if cmd.startswith("ERR?"):
            return "0,No error"
        return "OK"
