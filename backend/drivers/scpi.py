# -*- coding: utf-8 -*-
"""通用 SCPI 家族驱动：万用表 / 可编程电源 / 电子负载 / 波形发生器 / 串口运动控制

真实模式下发标准 SCPI 命令；仿真模式由 `simulate_command()` 给出**确定性**测量值
（用 `stable_ratio` 生成 ±0.3% 级别的稳定偏差），因此 TPS 用例在无硬件时也能跑通，
且每次运行的数值一致、便于回归。
"""

from __future__ import annotations

from typing import Optional

from .base import Driver, stable_ratio
from .errors import DriverError


def _to_float(text: str, what: str) -> float:
    try:
        return float(str(text).strip().split(",")[0])
    except (TypeError, ValueError) as e:
        raise DriverError(f"{what} 返回值无法解析: {text!r}", detail="仪器未返回数值") from e


class GenericScpiDriver(Driver):
    """兜底驱动：任何 SCPI 仪器都能连上并发命令（无专用解析）"""

    driver_key = "generic-scpi"
    family = "generic"
    label = "通用 SCPI 仪器"

    ACTIONS = {
        "idn": "identify",
        "reset": "reset",
        "query": "query_raw",
        "write": "write_raw",
        "state": "state",
    }

    def query_raw(self, command: str = "", **_) -> dict:
        if not command:
            raise DriverError("query 动作需要 command 参数", device_id=self.device_id)
        if self.simulate:
            return {"value": self.simulate_command(command), "command": command, "simulated": True}
        return {"value": self.query(command), "command": command}

    def write_raw(self, command: str = "", **_) -> dict:
        if not command:
            raise DriverError("write 动作需要 command 参数", device_id=self.device_id)
        if self.simulate:
            self.transport.write(command)
            return {"value": "OK", "command": command, "simulated": True}
        self.write(command)
        return {"value": "OK", "command": command}

    def simulate_command(self, command: str) -> str:
        cmd = command.strip().upper()
        if cmd.startswith("*IDN"):
            return self.sim_idn
        if cmd.startswith("*RST") or cmd.endswith("OPC?"):
            return "1" if cmd.endswith("OPC?") else "OK"
        return "0"


class DmmDriver(GenericScpiDriver):
    """数字万用表（Fluke 8846A / 通用 SCPI 万用表）"""

    driver_key = "generic-dmm"
    family = "dmm"
    label = "数字万用表"
    sim_idn = "FLUKE,8846A,SIM-DMM,1.0"

    # quantity -> (真实 SCPI 查询, 仿真标称值, 单位)
    QUANTITIES = {
        "voltage": ("MEAS:VOLT:DC?", 3.312, "V"),
        "current": ("MEAS:CURR:DC?", 182.4, "mA"),
        "resistance": ("MEAS:RES?", 10203.0, "Ohm"),
        "frequency": ("MEAS:FREQ?", 1000.0, "Hz"),
        "ac_voltage": ("MEAS:VOLT:AC?", 1.204, "V"),
    }

    ACTIONS = {
        "idn": "identify",
        "reset": "reset",
        "measure": "measure_dc_voltage",
        "measure_dc_voltage": "measure_dc_voltage",
        "measure_dc_current": "measure_dc_current",
        "measure_resistance": "measure_resistance",
        "measure_frequency": "measure_frequency",
        "measure_ac_voltage": "measure_ac_voltage",
        "state": "state",
    }

    def _measure(self, quantity: str) -> dict:
        cmd, nominal, unit = self.QUANTITIES[quantity]
        if self.simulate:
            value = nominal * (1.0 + stable_ratio(f"{self.device_id}:{quantity}:{self.cfg.model}", 0.003))
            return {"value": round(value, 6), "quantity": quantity, "unit": unit,
                    "command": cmd, "simulated": True}
        raw = self.query(cmd)
        return {"value": round(_to_float(raw, f"{quantity} 测量"), 6), "quantity": quantity,
                "unit": unit, "command": cmd, "raw": raw}

    def measure_dc_voltage(self, **_) -> dict:
        return self._measure("voltage")

    def measure_dc_current(self, **_) -> dict:
        return self._measure("current")

    def measure_resistance(self, **_) -> dict:
        return self._measure("resistance")

    def measure_frequency(self, **_) -> dict:
        return self._measure("frequency")

    def measure_ac_voltage(self, **_) -> dict:
        return self._measure("ac_voltage")

    def simulate_command(self, command: str) -> str:
        cmd = command.strip().upper()
        for quantity, (real, nominal, _unit) in self.QUANTITIES.items():
            if real.upper() == cmd:
                return f"{nominal * (1.0 + stable_ratio(f'{self.device_id}:{quantity}', 0.003)):.6f}"
        return super().simulate_command(command)


class PsuDriver(GenericScpiDriver):
    """可编程直流电源（ITECH IT6300 系列 / 通用 SCPI 电源）"""

    driver_key = "generic-psu"
    family = "psu"
    label = "可编程直流电源"
    sim_idn = "ITECH,IT6332A,SIM-PSU,1.0"

    ACTIONS = {
        "idn": "identify",
        "reset": "reset",
        "set_voltage": "set_voltage",
        "set_current": "set_current",
        "output": "output",
        "output_on": "output_on",
        "output_off": "output_off",
        "measure_voltage": "measure_voltage",
        "measure_current": "measure_current",
        "state": "state",
    }

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.setpoint = {"voltage": 0.0, "current": 0.0}
        self.output_on_flag = False
        self.on_close("OUTP OFF")

    # ---- 设置 ----
    def _channel(self, channel: Optional[str]) -> str:
        ch = str(channel or self.cfg.channel or "1").strip() or "1"
        return ch if ch.isdigit() else "1"

    def set_voltage(self, voltage: float = 0.0, channel: Optional[str] = None, **_) -> dict:
        value = float(voltage)
        self.setpoint["voltage"] = value
        cmd = f"VOLT {value:g}" if channel is None and not self.cfg.channel else f"INST:NSEL {self._channel(channel)};VOLT {value:g}"
        if self.simulate:
            self.transport.write(cmd)
            return {"value": value, "unit": "V", "command": cmd, "simulated": True}
        self.write(cmd)
        return {"value": value, "unit": "V", "command": cmd}

    def set_current(self, current: float = 0.0, channel: Optional[str] = None, **_) -> dict:
        value = float(current)
        self.setpoint["current"] = value
        cmd = f"CURR {value:g}"
        if self.simulate:
            self.transport.write(cmd)
            return {"value": value, "unit": "A", "command": cmd, "simulated": True}
        self.write(cmd)
        return {"value": value, "unit": "A", "command": cmd}

    def _set_output(self, on: bool) -> dict:
        cmd = "OUTP ON" if on else "OUTP OFF"
        self.output_on_flag = on
        if self.simulate:
            self.transport.write(cmd)
            return {"value": "ON" if on else "OFF", "command": cmd, "simulated": True}
        self.write(cmd)
        return {"value": "ON" if on else "OFF", "command": cmd}

    def output(self, on: bool = True, **_) -> dict:
        return self._set_output(bool(on))

    def output_on(self, **_) -> dict:
        return self._set_output(True)

    def output_off(self, **_) -> dict:
        return self._set_output(False)

    # ---- 测量 ----
    def measure_voltage(self, **_) -> dict:
        if self.simulate:
            base = self.setpoint["voltage"] or 3.3
            value = base * (1.0 + stable_ratio(f"{self.device_id}:v", 0.002)) if self.output_on_flag else 0.0
            return {"value": round(value, 4), "unit": "V", "simulated": True, "output": self.output_on_flag}
        return {"value": _to_float(self.query("MEAS:VOLT?"), "电源电压"), "unit": "V",
                "output": self.output_on_flag}

    def measure_current(self, **_) -> dict:
        if self.simulate:
            base = self.setpoint["current"] or 0.12
            value = base * (1.0 + stable_ratio(f"{self.device_id}:i", 0.004)) if self.output_on_flag else 0.0
            return {"value": round(value, 4), "unit": "A", "simulated": True, "output": self.output_on_flag}
        return {"value": _to_float(self.query("MEAS:CURR?"), "电源电流"), "unit": "A",
                "output": self.output_on_flag}

    def state(self) -> dict:
        out = super().state()
        out["setpoint"] = dict(self.setpoint)
        out["output"] = self.output_on_flag
        return out

    def simulate_command(self, command: str) -> str:
        cmd = command.strip().upper()
        if cmd.startswith("MEAS:VOLT"):
            base = self.setpoint["voltage"] or 3.3
            return f"{base if self.output_on_flag else 0.0:.4f}"
        if cmd.startswith("MEAS:CURR"):
            base = self.setpoint["current"] or 0.12
            return f"{base if self.output_on_flag else 0.0:.4f}"
        return super().simulate_command(command)


class ELoadDriver(GenericScpiDriver):
    """可编程电子负载（ITECH IT8500 系列 / 通用 SCPI 负载）"""

    driver_key = "generic-eload"
    family = "eload"
    label = "可编程电子负载"
    sim_idn = "ITECH,IT8512A,SIM-ELOAD,1.0"

    ACTIONS = {
        "idn": "identify",
        "reset": "reset",
        "set_current": "set_current",
        "set_mode": "set_mode",
        "input": "input",
        "input_on": "input_on",
        "input_off": "input_off",
        "measure_voltage": "measure_voltage",
        "measure_current": "measure_current",
        "state": "state",
    }

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.current = 0.0
        self.mode = "CC"
        self.input_on_flag = False
        self.on_close("INP OFF")

    def set_current(self, current: float = 0.0, **_) -> dict:
        self.current = float(current)
        cmd = f"CURR {self.current:g}"
        if self.simulate:
            self.transport.write(cmd)
            return {"value": self.current, "unit": "A", "command": cmd, "simulated": True}
        self.write(cmd)
        return {"value": self.current, "unit": "A", "command": cmd}

    def set_mode(self, mode: str = "CC", **_) -> dict:
        self.mode = str(mode).upper()
        cmd = f"MODE {self.mode}"
        if self.simulate:
            self.transport.write(cmd)
            return {"value": self.mode, "command": cmd, "simulated": True}
        self.write(cmd)
        return {"value": self.mode, "command": cmd}

    def _set_input(self, on: bool) -> dict:
        cmd = "INP ON" if on else "INP OFF"
        self.input_on_flag = on
        if self.simulate:
            self.transport.write(cmd)
            return {"value": "ON" if on else "OFF", "command": cmd, "simulated": True}
        self.write(cmd)
        return {"value": "ON" if on else "OFF", "command": cmd}

    def input(self, on: bool = True, **_) -> dict:
        return self._set_input(bool(on))

    def input_on(self, **_) -> dict:
        return self._set_input(True)

    def input_off(self, **_) -> dict:
        return self._set_input(False)

    def measure_voltage(self, **_) -> dict:
        if self.simulate:
            value = (3.3 * (1.0 + stable_ratio(f"{self.device_id}:v", 0.002))) if self.input_on_flag else 0.0
            return {"value": round(value, 4), "unit": "V", "simulated": True, "input": self.input_on_flag}
        return {"value": _to_float(self.query("MEAS:VOLT?"), "负载电压"), "unit": "V",
                "input": self.input_on_flag}

    def measure_current(self, **_) -> dict:
        if self.simulate:
            value = (self.current or 0.5) if self.input_on_flag else 0.0
            return {"value": round(value, 4), "unit": "A", "simulated": True, "input": self.input_on_flag}
        return {"value": _to_float(self.query("MEAS:CURR?"), "负载电流"), "unit": "A",
                "input": self.input_on_flag}

    def state(self) -> dict:
        out = super().state()
        out.update({"mode": self.mode, "current": self.current, "input": self.input_on_flag})
        return out


class AwgDriver(GenericScpiDriver):
    """波形发生器（Keysight 33500B 系列 / 通用 SCPI 信号源）"""

    driver_key = "generic-awg"
    family = "awg"
    label = "波形发生器"
    sim_idn = "Keysight,33500B,SIM-AWG,1.0"

    ACTIONS = {
        "idn": "identify",
        "reset": "reset",
        "set_waveform": "set_waveform",
        "set_frequency": "set_frequency",
        "set_amplitude": "set_amplitude",
        "output": "output",
        "output_on": "output_on",
        "output_off": "output_off",
        "state": "state",
    }

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.waveform = {"kind": "SIN", "frequency": 1000.0, "amplitude": 2.4, "offset": 0.0}
        self.output_on_flag = False
        self.on_close("OUTP OFF")

    def set_waveform(self, kind: str = "SIN", frequency: Optional[float] = None,
                     amplitude: Optional[float] = None, offset: Optional[float] = None, **_) -> dict:
        self.waveform["kind"] = str(kind).upper()
        if frequency is not None:
            self.waveform["frequency"] = float(frequency)
        if amplitude is not None:
            self.waveform["amplitude"] = float(amplitude)
        if offset is not None:
            self.waveform["offset"] = float(offset)
        w = self.waveform
        cmd = f"SOUR:FUNC {w['kind']};SOUR:FREQ {w['frequency']:g};SOUR:VOLT {w['amplitude']:g}"
        if self.simulate:
            self.transport.write(cmd)
            return {"value": dict(w), "command": cmd, "simulated": True}
        self.write(cmd)
        return {"value": dict(w), "command": cmd}

    def set_frequency(self, frequency: float = 1000.0, **_) -> dict:
        return self.set_waveform(frequency=frequency)

    def set_amplitude(self, amplitude: float = 1.0, **_) -> dict:
        return self.set_waveform(amplitude=amplitude)

    def _set_output(self, on: bool) -> dict:
        cmd = "OUTP ON" if on else "OUTP OFF"
        self.output_on_flag = on
        if self.simulate:
            self.transport.write(cmd)
            return {"value": "ON" if on else "OFF", "command": cmd, "simulated": True}
        self.write(cmd)
        return {"value": "ON" if on else "OFF", "command": cmd}

    def output(self, on: bool = True, **_) -> dict:
        return self._set_output(bool(on))

    def output_on(self, **_) -> dict:
        return self._set_output(True)

    def output_off(self, **_) -> dict:
        return self._set_output(False)

    def state(self) -> dict:
        out = super().state()
        out.update({"waveform": dict(self.waveform), "output": self.output_on_flag})
        return out


class MotionDriver(GenericScpiDriver):
    """读头转速控制盒（串口 ASCII 协议）"""

    driver_key = "serial-motion"
    family = "motion"
    label = "运动控制器（串口）"
    sim_idn = "ATEMOTION,SIM-SPIN,COM,1.0"

    ACTIONS = {
        "idn": "identify",
        "handshake": "handshake",
        "set_speed": "set_speed",
        "read_speed": "read_speed",
        "home": "home",
        "stop": "stop",
        "state": "state",
    }

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.speed = 0.0
        self.on_close("STOP")

    def handshake(self, **_) -> dict:
        if self.simulate:
            self.transport.write("HS?")
            return {"value": "OK:ATEMOTION,V1", "simulated": True}
        return {"value": self.query("HS?")}

    def set_speed(self, rpm: float = 0.0, **_) -> dict:
        self.speed = float(rpm)
        cmd = f"SPD {self.speed:g}"
        if self.simulate:
            self.transport.write(cmd)
            return {"value": self.speed, "unit": "rpm", "command": cmd, "simulated": True}
        self.write(cmd)
        return {"value": self.speed, "unit": "rpm", "command": cmd}

    def read_speed(self, **_) -> dict:
        if self.simulate:
            value = self.speed * (1.0 + stable_ratio(f"{self.device_id}:rpm", 0.004))
            return {"value": round(value, 3), "unit": "rpm", "simulated": True}
        return {"value": _to_float(self.query("SPD?"), "转速"), "unit": "rpm"}

    def home(self, **_) -> dict:
        cmd = "HOME"
        if self.simulate:
            self.transport.write(cmd)
            return {"value": "OK", "simulated": True}
        self.write(cmd)
        return {"value": self.query("HS?")}

    def stop(self, **_) -> dict:
        cmd = "STOP"
        self.speed = 0.0
        if self.simulate:
            self.transport.write(cmd)
            return {"value": "OK", "simulated": True}
        self.write(cmd)
        return {"value": "OK"}

    def state(self) -> dict:
        out = super().state()
        out["speed"] = self.speed
        out["port"] = self.cfg.serial_port
        out["baudrate"] = self.cfg.baudrate
        return out

    def simulate_command(self, command: str) -> str:
        cmd = command.strip().upper()
        if cmd.startswith("HS"):
            return "OK:ATEMOTION,V1"
        if cmd.startswith("SPD?"):
            return f"{self.speed:.3f}"
        if cmd.startswith("SPD"):
            return "OK"
        if cmd.startswith("STOP") or cmd.startswith("HOME"):
            return "OK"
        return super().simulate_command(command)
