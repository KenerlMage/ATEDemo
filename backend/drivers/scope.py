# -*- coding: utf-8 -*-
"""示波器驱动（泰克 MSO5 系列 / 通用 SCPI 示波器）

真实模式下访问 LAN socket 上的 SCPI 端口；仿真模式用确定性算法生成波形
（默认 1 kHz 正弦、2.4 Vpp、叠加少量噪声），并自带测量项计算，供 TPS 用例
在无硬件时验证阈值的上下限逻辑。
"""

from __future__ import annotations

import math
import random
from typing import Optional

from .base import Driver, stable_ratio
from .errors import DriverError

WAVEFORM_KINDS = ("sine", "square", "triangle", "sawtooth", "dc", "noise")
MEASURE_ITEMS = ("PK2PK", "AMPLITUDE", "FREQUENCY", "PERIOD", "MEAN", "RMS", "RISE", "FALL")

V_DIV_STEPS = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)


def _sim_samples(kind: str, freq: float, vpp: float, offset: float, points: int,
                 window: float, noise: float, phase: float = 0.0) -> list[float]:
    """生成一段确定性波形（同参数永远得到同样结果）"""
    amp = vpp / 2.0
    rng = random.Random(f"{kind}:{freq}:{vpp}:{offset}:{points}:{noise:.4f}")
    samples = []
    for i in range(points):
        t = window * i / max(1, points - 1)
        x = (t / (1.0 / freq)) % 1.0 if freq > 0 else 0.0
        x = (x + phase) % 1.0
        if kind == "sine":
            y = math.sin(2 * math.pi * x)
        elif kind == "square":
            y = 1.0 if x < 0.5 else -1.0
        elif kind == "triangle":
            y = 1.0 - 4.0 * abs(x - 0.5)
        elif kind == "sawtooth":
            y = 2.0 * x - 1.0
        elif kind == "dc":
            y = 0.0
        else:  # noise
            y = rng.uniform(-1.0, 1.0)
        value = offset + amp * y
        if noise and kind != "dc":
            value += rng.uniform(-1.0, 1.0) * amp * noise
        samples.append(round(value, 6))
    return samples


def _measure(samples: list[float], x_incr: float, freq_hint: float = 0.0) -> dict:
    """从样本自算常用测量项（与示波器习惯一致：频偏用带迟滞的过中点检测）"""
    if not samples:
        return {}
    vmax, vmin = max(samples), min(samples)
    vpp = vmax - vmin
    mid = (vmax + vmin) / 2.0
    span = vpp or 1e-9
    mean = sum(samples) / len(samples)
    rms = math.sqrt(sum(v * v for v in samples) / len(samples))
    lo, hi = mid - span * 0.15, mid + span * 0.15
    crossings: list[int] = []
    armed = samples[0] < lo
    for i in range(1, len(samples)):
        v = samples[i]
        if armed and v >= hi:
            crossings.append(i)
            armed = False
        elif not armed and v <= lo:
            armed = True
    period = None
    if len(crossings) >= 2:
        gaps = [crossings[i + 1] - crossings[i] for i in range(len(crossings) - 1)]
        gaps.sort()
        med = gaps[len(gaps) // 2]
        period = med * x_incr
    elif freq_hint:
        period = 1.0 / freq_hint
    freq = (1.0 / period) if period else float(freq_hint or 0.0)
    top_n = max(1, len(samples) // 20)
    srt = sorted(samples)
    base = sum(srt[:top_n]) / top_n
    top = sum(srt[-top_n:]) / top_n
    # 10%/90% 上升时间
    rise = None
    r_lo, r_hi = vmin + 0.1 * vpp, vmin + 0.9 * vpp
    for i in range(1, len(samples)):
        if samples[i - 1] < r_lo <= samples[i]:
            j = i
            while j < len(samples) and samples[j] < r_hi:
                j += 1
            if j < len(samples):
                rise = (j - i) * x_incr
            break
    return {
        "PK2PK": round(vpp, 6),
        "AMPLITUDE": round(top - base, 6),
        "FREQUENCY": round(freq, 6),
        "PERIOD": round(period, 12) if period else None,
        "MEAN": round(mean, 6),
        "RMS": round(rms, 6),
        "RISE": round(rise, 12) if rise is not None else None,
        "FALL": round(rise, 12) if rise is not None else None,
    }


class ScopeDriver(Driver):
    """数字示波器驱动"""

    driver_key = "tektronix-mso5"
    family = "scope"
    label = "数字示波器（泰克 MSO5 系列方案）"
    sim_idn = "TEKTRONIX,MSO54,SIM-SCOPE,1.0.0"

    ACTIONS = {
        "idn": "identify",
        "reset": "reset",
        "autoset": "autoset",
        "timebase": "timebase",
        "channel": "channel",
        "waveform": "waveform",
        "measure": "measure",
        "run": "run",
        "stop": "stop",
        "single": "single",
        "state": "state",
    }

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.timebase_scale = 5e-4              # s/div
        self.horizontals = 10             # 屏内格数
        self.channels = {"CH1": {"display": True, "scale": 0.5, "coupling": "DC", "offset": 0.0}}
        self.running = True
        self.single_flag = False
        self.signal = {"kind": "sine", "freq": 1000.0, "vpp": 2.4, "offset": 0.0, "noise": 0.02, "phase": 0.0}

    # ---------------- 配置类动作 ----------------

    def autoset(self, kind: Optional[str] = None, freq: Optional[float] = None, vpp: Optional[float] = None, **_) -> dict:
        if kind:
            self.set_signal(kind=kind, freq=freq, vpp=vpp)
        if not self.simulate:
            self.write(":AUTOSET EXECUTE")
        return {"value": {"timebase": self.timebase_scale, "signal": dict(self.signal)}, "simulated": self.simulate}

    def set_signal(self, kind: Optional[str] = None, freq: Optional[float] = None,
                   vpp: Optional[float] = None, offset: Optional[float] = None,
                   noise: Optional[float] = None) -> dict:
        sig = self.signal
        if kind:
            k = str(kind).lower()
            if k not in WAVEFORM_KINDS:
                raise DriverError(f"不支持的波形类型: {kind}", device_id=self.device_id,
                                  detail="可选: " + ", ".join(WAVEFORM_KINDS))
            sig["kind"] = k
        if freq is not None:
            f = float(freq)
            if f <= 0:
                raise DriverError("频率必须大于 0", device_id=self.device_id)
            sig["freq"] = f
            self.timebase_scale = max(1e-9, 5.0 / (10.0 * f))  # 屏内 5 个周期
        if vpp is not None:
            sig["vpp"] = float(vpp)
            self.channels["CH1"]["scale"] = self._auto_v_div(float(vpp))
        if offset is not None:
            sig["offset"] = float(offset)
        if noise is not None:
            sig["noise"] = float(noise)
        return {"value": dict(sig), "timebase": self.timebase_scale}

    @staticmethod
    def _auto_v_div(vpp: float) -> float:
        want = max(vpp / 6.0, 1e-4)
        return min(V_DIV_STEPS, key=lambda s: abs(math.log(s / want)))

    def timebase(self, scale: Optional[float] = None, **_) -> dict:
        if scale is not None:
            s = float(scale)
            if s <= 0:
                raise DriverError("时基必须大于 0", device_id=self.device_id)
            self.timebase_scale = s
            if not self.simulate:
                self.write(f":HOR:SCA {s:g}")
        return {"value": self.timebase_scale, "unit": "s/div", "simulated": self.simulate}

    def channel(self, name: str = "CH1", display: Optional[bool] = None, scale: Optional[float] = None,
                coupling: Optional[str] = None, offset: Optional[float] = None, **_) -> dict:
        ch = self.channels.setdefault(str(name).upper(), {"display": True, "scale": 0.5, "coupling": "DC", "offset": 0.0})
        if display is not None:
            ch["display"] = bool(display)
        if scale is not None:
            ch["scale"] = float(scale)
        if coupling is not None:
            ch["coupling"] = str(coupling).upper()
        if offset is not None:
            ch["offset"] = float(offset)
        if not self.simulate:
            if display is not None:
                self.write(f":{str(name).upper()}:DISP {'ON' if display else 'OFF'}")
            if scale is not None:
                self.write(f":{str(name).upper()}:SCA {float(scale):g}")
            if coupling is not None:
                self.write(f":{str(name).upper()}:COUP {str(coupling).upper()}")
        return {"value": dict(ch), "channel": str(name).upper(), "simulated": self.simulate}

    # ---------------- 采集类动作 ----------------

    def waveform(self, points: int = 1000, channel: str = "CH1", **_) -> dict:
        n = max(10, min(int(points or 1000), 100000))
        window = self.timebase_scale * self.horizontals
        if self.simulate:
            samples = _sim_samples(self.signal["kind"], self.signal["freq"], self.signal["vpp"],
                                   self.signal["offset"], n, window, self.signal["noise"], self.signal["phase"])
        else:
            samples = self._read_real_waveform(n)
        return {
            "value": {
                "channel": str(channel).upper(),
                "points": len(samples),
                "samples": samples,
                "x_incr": window / max(1, len(samples) - 1),
                "timebase": self.timebase_scale,
                "window": window,
                "signal_type": self.signal["kind"],
                "signal_freq": self.signal["freq"] if self.simulate else None,
                "source": "simulate" if self.simulate else "instrument",
            }
        }

    def _read_real_waveform(self, points: int) -> list[float]:
        """真实取波形：用 WFMOUTPRE + CURVE? 读取并换算为电压（无硬件时不会走到这里）"""
        self.write(":DAT:SOU CH1;:DAT:ENC RIB;:WFMOUTPRE:BYT_NR 2")
        head = self.query(":WFMOUTPRE?")
        parts = [p.strip() for p in head.split(";")]
        ymult, yoff, yzero = 1.0, 0.0, 0.0
        if len(parts) >= 9:
            try:
                ymult, yoff, yzero = float(parts[7]), float(parts[8]), float(parts[9]) if len(parts) > 9 else 0.0
            except (ValueError, IndexError):
                pass
        raw = self.query(f":CURVE?")
        nums = [float(x) for x in raw.replace("\n", "").split(",") if x.strip().lstrip("-").replace(".", "").isdigit()]
        return [round((v - yoff) * ymult + yzero, 6) for v in nums[:points]]

    def measure(self, items: Optional[list] = None, channel: str = "CH1", points: int = 2000, **_) -> dict:
        wanted = [str(i).upper() for i in (items or ["PK2PK", "FREQUENCY", "MEAN", "RMS"])]
        wave = self.waveform(points=points, channel=channel)["value"]
        values = _measure(wave["samples"], wave["x_incr"], wave["signal_freq"] or 0.0)
        out = [
            {"type": k, "value": values.get(k), "unit": _unit_of(k),
             "source": "simulate" if self.simulate else "instrument"}
            for k in wanted
        ]
        return {"value": out, "measurements": out}

    def run(self, **_) -> dict:
        self.running, self.single_flag = True, False
        if not self.simulate:
            self.write(":ACQ:STATE ON")
        return {"value": True, "simulated": self.simulate}

    def stop(self, **_) -> dict:
        self.running, self.single_flag = False, False
        if not self.simulate:
            self.write(":ACQ:STATE OFF")
        return {"value": False, "simulated": self.simulate}

    def single(self, **_) -> dict:
        self.single_flag = True
        self.running = True
        if not self.simulate:
            self.write(":ACQ:STATE ON;:ACQ:STOPA SEQ;:ACQ:SEQ SINGLE")
        return {"value": True, "single": True, "simulated": self.simulate}

    # ---------------- 状态 ----------------

    def state(self) -> dict:
        out = super().state()
        out.update({
            "timebase": self.timebase_scale,
            "channels": {k: dict(v) for k, v in self.channels.items()},
            "running": self.running,
            "single": self.single_flag,
            "sim_signal": dict(self.signal) if self.simulate else None,
        })
        return out

    def simulate_command(self, command: str) -> str:
        cmd = command.strip().upper()
        if cmd.startswith("*IDN"):
            return self.sim_idn
        if cmd.startswith(":AUTOSET") or cmd.startswith(":HOR:SCA"):
            return f"{self.timebase_scale:g}"
        if cmd.startswith(":WFMOUTPRE"):
            return "0;2;1;0;0;0;0;1.0E-3;0.0;128.0;1"
        if cmd.startswith(":CURVE"):
            pts = _sim_samples(self.signal["kind"], self.signal["freq"], self.signal["vpp"],
                               self.signal["offset"], 64, self.timebase_scale * self.horizontals, self.signal["noise"])
            return ",".join(f"{v:.4f}" for v in pts)
        return super().simulate_command(command)


def _unit_of(item: str) -> str:
    return {"FREQUENCY": "Hz", "PERIOD": "s", "RISE": "s", "FALL": "s"}.get(item.upper(), "V")
