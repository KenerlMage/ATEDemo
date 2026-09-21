# -*- coding: utf-8 -*-
"""示波器家族契约（ScopeDriver）

分工（这是驱动库最重要的一条设计约定）：

| 谁 | 负责 |
| --- | --- |
| **家族基类（本文件）** | 动作名、参数校验、**单位**、返回结构、测量算法、默认仿真模型、安全退出 |
| **型号驱动（子类）** | `COMMANDS` 命令表、`_decode_preamble()` 应答解析、`_sim_signal()` 仿真模型差异 |

因此"换一个品牌的示波器"只需实现三处差异，用例侧**零改动**：动作名、单位、返回结构
全部由家族保证一致。测量项一律**由采样点自算**（不依赖各家的 `:MEASure` 命令），
所以不同品牌算出的口径一致，跨设备对比才有意义。
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from typing import Optional

from ..base import InstrumentDriver
from ..capabilities import Capability
from ..contract import WAVEFORM_SCHEMA
from ..errors import DriverError, ProtocolError

WAVEFORM_KINDS = ("sine", "square", "triangle", "sawtooth", "dc", "noise")
MEASURE_ITEMS = ("PK2PK", "AMPLITUDE", "FREQUENCY", "PERIOD", "MEAN", "RMS", "RISE", "FALL")
MEASURE_UNITS = {"FREQUENCY": "Hz", "PERIOD": "s", "RISE": "s", "FALL": "s"}
V_DIV_STEPS = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)
COUPLINGS = ("DC", "AC", "GND")
DEFAULT_PREAMBLE = "0;2;1;0;0;0;0;1.0E-3;0.0;128.0;1"


# ============================================================ 数据模型（单位写死在结构里）

@dataclass(frozen=True)
class TimebaseSetup:
    """时基：`scale_s_per_div` 单位 s/div（SI 秒，不用 ms/us 混着写）"""

    scale_s_per_div: float
    divisions: int = 10
    offset_s: float = 0.0

    @property
    def window_s(self) -> float:
        return self.scale_s_per_div * self.divisions

    def to_dict(self) -> dict:
        return dict(asdict(self), window_s=self.window_s, unit="s/div")


@dataclass(frozen=True)
class ChannelSetup:
    """通道：`scale_v_per_div` 单位 V/div，`offset_v` 单位 V"""

    name: str = "CH1"
    enabled: bool = True
    scale_v_per_div: float = 0.5
    coupling: str = "DC"
    offset_v: float = 0.0
    probe_ratio: float = 1.0

    def to_dict(self) -> dict:
        return dict(asdict(self), unit="V/div")


@dataclass(frozen=True)
class Waveform:
    """一段采样波形：`samples` 单位 V，时间轴由 `x_start_s` + `x_incr_s` 唯一定义"""

    channel: str
    samples: tuple[float, ...]
    x_incr_s: float
    x_start_s: float = 0.0
    unit: str = "V"
    source: str = "simulate"

    @property
    def points(self) -> int:
        return len(self.samples)

    def to_dict(self) -> dict:
        return {
            "schema": WAVEFORM_SCHEMA,
            "channel": self.channel,
            "points": self.points,
            "samples": list(self.samples),
            "x_start_s": self.x_start_s,
            "x_incr_s": self.x_incr_s,
            "unit": self.unit,
            "source": self.source,
        }


@dataclass(frozen=True)
class Measurement:
    """一个测量项：`kind` 用固定枚举，`unit` 必填"""

    kind: str
    value: Optional[float]
    unit: str
    quality: str = "good"

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================ 家族基类

class ScopeDriver(InstrumentDriver):
    """数字示波器家族契约（型号驱动继承它）"""

    driver_key = "generic-scope"
    family = "scope"
    label = "数字示波器（家族契约）"
    driver_version = "1.0.0"
    sim_idn = "ATE,SIM-SCOPE,0,1.0.0"

    CAPABILITIES = (
        Capability.IDENTIFY,
        Capability.RESET,
        Capability.STATE,
        Capability.SCOPE_AUTOSET,
        Capability.SCOPE_TIMEBASE,
        Capability.SCOPE_CHANNEL,
        Capability.SCOPE_ACQUIRE,
        Capability.SCOPE_MEASURE,
        Capability.SCOPE_RUN,
        Capability.SCOPE_STOP,
        Capability.SCOPE_SINGLE,
    )

    ACTIONS = {
        "identify": "identify",
        "reset": "reset",
        "state": "state",
        Capability.SCOPE_AUTOSET: "autoset",
        Capability.SCOPE_TIMEBASE: "timebase",
        Capability.SCOPE_CHANNEL: "channel",
        Capability.SCOPE_ACQUIRE: "acquire_waveform",
        Capability.SCOPE_MEASURE: "measure",
        Capability.SCOPE_RUN: "run",
        Capability.SCOPE_STOP: "stop",
        Capability.SCOPE_SINGLE: "single",
    }

    # 历史 TPS 里的短名仍然可用（平台启动时会打弃用告警，逐步换成能力名）
    ALIASES = {
        "idn": "identify",
        "autoset": "scope.autoset",
        "timebase": "scope.timebase",
        "channel": "scope.channel",
        "waveform": "scope.acquire_waveform",
        "measure": "scope.measure",
        "run": "scope.run",
        "stop": "scope.stop",
        "single": "scope.single",
    }

    # ---- 命令表：型号驱动只覆盖需要的键，键名不得改（平台按这些键做能力映射）----
    COMMANDS = {
        "idn": "*IDN?",
        "reset": "*RST;*OPC?",
        "autoset": ":AUTOSET EXECUTE",
        "timebase_set": ":HOR:SCA {scale:g}",
        "timebase_get": ":HOR:SCA?",
        "channel_enable": ":{channel}:DISP {state}",
        "channel_scale": ":{channel}:SCA {scale:g}",
        "channel_coupling": ":{channel}:COUP {coupling}",
        "acquire_setup": ":DAT:SOU {channel};:DAT:ENC RIB;:WFMOUTPRE:BYT_NR 2",
        "waveform_preamble": ":WFMOUTPRE?",
        "waveform_data": ":CURVE?",
        "run": ":ACQ:STATE ON",
        "stop": ":ACQ:STATE OFF",
        "single": ":ACQ:STATE ON;:ACQ:STOPA SEQ;:ACQ:SEQ SINGLE",
        "error_query": ":SYST:ERR?",
    }
    REQUIRED_COMMANDS = ("idn", "reset", "autoset", "timebase_set", "timebase_get",
                         "channel_enable", "channel_scale", "channel_coupling",
                         "acquire_setup", "waveform_preamble", "waveform_data",
                         "run", "stop", "single")
    DEFAULT_CHANNEL = "CH1"
    # 波形前导字段顺序（型号驱动按手册填写；填了它就按名字取值，不再靠位置猜）
    PREAMBLE_FIELDS: tuple[str, ...] = ()

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.timebase_setup = TimebaseSetup(scale_s_per_div=5e-4, divisions=10)
        self.channel_setups: dict[str, ChannelSetup] = {
            self.DEFAULT_CHANNEL: ChannelSetup(self.DEFAULT_CHANNEL)
        }
        self.running = False
        self.single_flag = False
        self._sim_signal_state = {"kind": "sine", "freq_hz": 1000.0, "vpp_v": 2.4,
                                  "offset_v": 0.0, "noise": 0.02, "phase": 0.0}

    # ------------------------------------------------------------ 工具

    def channel_token(self, channel: str) -> str:
        """通道令牌归一：返回家族动作里 `channel` 参数在命令串中的写法。

        泰克用令牌 `CH1`（默认原样返回）；按数字后缀寻址的厂商覆盖它（R&S -> `1`）。
        命令表里用 `{channel}` 或 `{n}` 引用，两者都会带上，模板各取所需。
        """
        return str(channel)

    def cmd(self, key: str, **params) -> str:
        """按命令表取命令（型号驱动覆盖 COMMANDS 即可，动作代码不动）"""
        template = self.COMMANDS.get(key)
        if not template:
            raise ProtocolError(f"命令表缺少 {key}", device_id=self.device_id,
                                detail="请检查驱动 COMMANDS 是否覆盖了家族要求的键")
        try:
            return template.format(**params) if params else template
        except KeyError as e:
            raise ProtocolError(f"命令模板 {key} 缺少参数 {e}", device_id=self.device_id) from e

    def missing_commands(self) -> list[str]:
        return [k for k in self.REQUIRED_COMMANDS if k not in self.COMMANDS]

    # ------------------------------------------------------------ 配置类动作

    def autoset(self, kind: Optional[str] = None, freq_hz: Optional[float] = None,
                vpp_v: Optional[float] = None, **_) -> dict:
        if kind is not None or freq_hz is not None or vpp_v is not None:
            self._set_sim_signal(kind=kind, freq_hz=freq_hz, vpp_v=vpp_v)
        if not self.simulate:
            self.write(self.cmd("autoset"))
        return {
            "value": {"timebase": self.timebase_setup.to_dict(),
                      "sim_signal": dict(self._sim_signal_state) if self.simulate else None},
            "quality": "simulated" if self.simulate else "good",
        }

    def _set_sim_signal(self, kind=None, freq_hz=None, vpp_v=None, offset_v=None, noise=None) -> dict:
        sig = self._sim_signal_state
        if kind is not None:
            k = str(kind).lower()
            if k not in WAVEFORM_KINDS:
                raise DriverError(f"不支持的波形类型: {kind}", device_id=self.device_id,
                                  detail="可选: " + ", ".join(WAVEFORM_KINDS), code="E_PARAM")
            sig["kind"] = k
        if freq_hz is not None:
            f = float(freq_hz)
            if f <= 0:
                raise DriverError("频率必须大于 0", device_id=self.device_id, code="E_PARAM")
            sig["freq_hz"] = f
            self.timebase_setup = TimebaseSetup(max(1e-9, 5.0 / (10.0 * f)), self.timebase_setup.divisions)
        if vpp_v is not None:
            v = float(vpp_v)
            if v <= 0:
                raise DriverError("峰峰值必须大于 0", device_id=self.device_id, code="E_PARAM")
            sig["vpp_v"] = v
            ch = self.channel_setups[self.DEFAULT_CHANNEL]
            self.channel_setups[self.DEFAULT_CHANNEL] = ChannelSetup(
                ch.name, ch.enabled, self.auto_v_per_div(v), ch.coupling, ch.offset_v, ch.probe_ratio
            )
        if offset_v is not None:
            sig["offset_v"] = float(offset_v)
        if noise is not None:
            sig["noise"] = float(noise)
        return dict(sig)

    @staticmethod
    def auto_v_per_div(vpp_v: float) -> float:
        want = max(float(vpp_v) / 6.0, 1e-4)
        return min(V_DIV_STEPS, key=lambda s: abs(math.log(s / want)))

    def timebase(self, scale_s_per_div: Optional[float] = None, divisions: Optional[int] = None,
                 offset_s: Optional[float] = None, **_) -> dict:
        tb = self.timebase_setup
        if scale_s_per_div is not None:
            s = float(scale_s_per_div)
            if s <= 0:
                raise DriverError("时基必须大于 0", device_id=self.device_id, code="E_PARAM")
            tb = TimebaseSetup(s, tb.divisions, tb.offset_s)
            if not self.simulate:
                self.write(self.cmd("timebase_set", scale=s))
        if divisions is not None:
            d = int(divisions)
            if d < 2:
                raise DriverError("屏内格数至少为 2", device_id=self.device_id, code="E_PARAM")
            tb = TimebaseSetup(tb.scale_s_per_div, d, tb.offset_s)
        if offset_s is not None:
            tb = TimebaseSetup(tb.scale_s_per_div, tb.divisions, float(offset_s))
        self.timebase_setup = tb
        return {"value": tb.to_dict(), "quality": "simulated" if self.simulate else "good"}

    def channel(self, name: str = DEFAULT_CHANNEL, enabled: Optional[bool] = None,
                scale_v_per_div: Optional[float] = None, coupling: Optional[str] = None,
                offset_v: Optional[float] = None, probe_ratio: Optional[float] = None, **_) -> dict:
        ch_name = str(name).upper()
        cur = self.channel_setups.get(ch_name, ChannelSetup(ch_name))
        new = ChannelSetup(
            ch_name,
            cur.enabled if enabled is None else bool(enabled),
            cur.scale_v_per_div if scale_v_per_div is None else float(scale_v_per_div),
            cur.coupling if coupling is None else str(coupling).upper(),
            cur.offset_v if offset_v is None else float(offset_v),
            cur.probe_ratio if probe_ratio is None else float(probe_ratio),
        )
        if scale_v_per_div is not None and new.scale_v_per_div <= 0:
            raise DriverError("垂直档位必须大于 0", device_id=self.device_id, code="E_PARAM")
        if coupling is not None and new.coupling not in COUPLINGS:
            raise DriverError(f"不支持的耦合方式: {coupling}", device_id=self.device_id,
                              detail="可选: " + ", ".join(COUPLINGS), code="E_PARAM")
        self.channel_setups[ch_name] = new
        if not self.simulate:
            kw = {"channel": ch_name, "n": self.channel_token(ch_name)}
            self.write(self.cmd("channel_enable", state="ON" if new.enabled else "OFF", **kw))
            self.write(self.cmd("channel_scale", scale=new.scale_v_per_div, **kw))
            self.write(self.cmd("channel_coupling", coupling=new.coupling, **kw))
        return {"value": new.to_dict(), "quality": "simulated" if self.simulate else "good"}

    # ------------------------------------------------------------ 采集类动作

    def acquire_waveform(self, points: int = 1000, channel: str = DEFAULT_CHANNEL, **_) -> dict:
        n = max(10, min(int(points or 1000), 100000))
        ch = str(channel).upper()
        tb = self.timebase_setup
        if self.simulate:
            samples = self._sim_signal(n, tb.window_s)
            wave = Waveform(ch, tuple(samples), tb.window_s / max(1, n - 1),
                            unit="V", source="simulate")
        else:
            if ch not in self.channel_setups:
                self.channel(name=ch)
            self.write(self.cmd("acquire_setup", channel=ch,
                                n=self.channel_token(ch), points=n))
            wave = self._read_real_waveform(ch, n)
        return {
            "value": wave.to_dict(),
            "channel": ch,
            "unit": wave.unit,
            "timebase": tb.to_dict(),
            "sim_signal": dict(self._sim_signal_state) if self.simulate else None,
            "quality": "simulated" if self.simulate else "good",
        }

    def _read_real_waveform(self, channel: str, points: int) -> Waveform:
        """真实取波形；解析细节交给型号驱动的 `_decode_preamble` / `_parse_curve`"""
        kw = {"channel": channel, "n": self.channel_token(channel), "points": points}
        preamble = self.query(self.cmd("waveform_preamble", **kw))
        x_incr, y_mult, y_off, y_zero = self._decode_preamble(preamble)
        raw = self.query(self.cmd("waveform_data", **kw))
        samples = self._parse_curve(raw, points)
        volts = tuple(round((v - y_off) * y_mult + y_zero, 6) for v in samples)
        return Waveform(channel, volts, x_incr, unit="V", source="instrument")

    def _decode_preamble(self, text: str) -> tuple[float, float, float, float]:
        """波形前导 -> (x_incr_s, y_mult, y_off, y_zero)

        优先按型号驱动声明的 `PREAMBLE_FIELDS` **按名字取值**（推荐，各家电平顺序不同，
        位置猜法迟早出错）；未声明时退化为通用位置启发式。
        """
        parts = [p.strip() for p in str(text).replace(",", ";").split(";") if p.strip()]
        if self.PREAMBLE_FIELDS:
            # 泰克 WFId 本身就形如 `CH1,CH1`，按逗号切会多出一个字段 -> 在 wfid 位置合并回来
            if len(parts) > len(self.PREAMBLE_FIELDS) and "wfid" in self.PREAMBLE_FIELDS:
                i = self.PREAMBLE_FIELDS.index("wfid")
                parts = parts[:i] + [parts[i] + "," + parts[i + 1]] + parts[i + 2:]
            data = dict(zip(self.PREAMBLE_FIELDS, parts))
            try:
                return (float(data["x_incr"]), float(data["y_mult"]),
                        float(data["y_off"]), float(data.get("y_zero") or 0.0))
            except (KeyError, ValueError) as e:
                raise ProtocolError(
                    "波形前导信息无法按字段名解析", device_id=self.device_id,
                    detail=f"需要 x_incr / y_mult / y_off；应答片段: {str(text)[:120]}",
                ) from e
        try:
            if len(parts) >= 10:
                return float(parts[0]), float(parts[7]), float(parts[8]), float(parts[9])
            if len(parts) >= 3:
                return float(parts[0]), float(parts[1]), float(parts[2]), 0.0
        except ValueError:
            pass
        raise ProtocolError("波形前导信息无法解析", device_id=self.device_id,
                            detail=f"应答片段: {str(text)[:120]}")

    def _parse_curve(self, text: str, points: int) -> list[float]:
        """CURVE 应答 -> 原始码值列表（型号驱动可覆盖，如二进制编码）"""
        values: list[float] = []
        for token in str(text).replace("\n", "").split(","):
            token = token.strip()
            if not token:
                continue
            try:
                values.append(float(token))
            except ValueError:
                continue
            if len(values) >= points:
                break
        if not values:
            raise ProtocolError("波形数据为空", device_id=self.device_id)
        return values

    def measure(self, items: Optional[list] = None, channel: str = DEFAULT_CHANNEL,
                points: int = 2000, **_) -> dict:
        wanted = [str(i).upper() for i in (items or ["PK2PK", "FREQUENCY", "MEAN", "RMS"])]
        bad = [i for i in wanted if i not in MEASURE_ITEMS]
        if bad:
            raise DriverError(f"不支持的测量项: {', '.join(bad)}", device_id=self.device_id,
                              detail="可选: " + ", ".join(MEASURE_ITEMS), code="E_PARAM")
        wave = self.acquire_waveform(points=points, channel=channel)["value"]
        computed = measure_samples(wave["samples"], wave["x_incr_s"],
                                   self._sim_signal_state["freq_hz"] if self.simulate else 0.0)
        out = [
            {"type": k, "value": computed.get(k), "unit": MEASURE_UNITS.get(k, "V"),
             "source": "simulate" if self.simulate else "instrument"}
            for k in wanted
        ]
        return {"value": out, "measurements": out, "channel": str(channel).upper(),
                "quality": "simulated" if self.simulate else "good"}

    # ------------------------------------------------------------ 采集控制

    def run(self, **_) -> dict:
        self.running, self.single_flag = True, False
        self.protect_on_close(self.cmd("stop"))
        if not self.simulate:
            self.write(self.cmd("run"))
        return {"value": True, "quality": "simulated" if self.simulate else "good"}

    def stop(self, **_) -> dict:
        self.running, self.single_flag = False, False
        if not self.simulate:
            self.write(self.cmd("stop"))
        return {"value": False, "quality": "simulated" if self.simulate else "good"}

    def single(self, **_) -> dict:
        self.single_flag, self.running = True, True
        self.protect_on_close(self.cmd("stop"))
        if not self.simulate:
            self.write(self.cmd("single"))
        return {"value": True, "single": True, "quality": "simulated" if self.simulate else "good"}

    # ------------------------------------------------------------ 状态与仿真

    def state(self) -> dict:
        out = super().state()
        out.update({
            "timebase": self.timebase_setup.to_dict(),
            "channels": {k: v.to_dict() for k, v in self.channel_setups.items()},
            "running": self.running,
            "single": self.single_flag,
            "sim_signal": dict(self._sim_signal_state) if self.simulate else None,
            "missing_commands": self.missing_commands(),
        })
        return out

    def _sim_signal(self, points: int, window_s: float) -> list[float]:
        """默认仿真模型：确定性波形（同参数永远同样结果）。

        型号驱动可覆盖它来模拟自家特有的行为（带宽限制、噪声底、编码方式）。
        """
        sig = self._sim_signal_state
        return sim_samples(sig["kind"], sig["freq_hz"], sig["vpp_v"], sig["offset_v"],
                           points, window_s, sig["noise"], sig["phase"])

    def simulate_command(self, command: str) -> str:
        cmd = str(command).strip().upper()
        if cmd.startswith("*IDN"):
            return self.sim_idn
        if cmd.startswith(":AUTOSET") or cmd.startswith(":HOR:SCA"):
            return f"{self.timebase_setup.scale_s_per_div:g}"
        if cmd.startswith(":WFMOUTPRE"):
            return DEFAULT_PREAMBLE
        if cmd.startswith(":CURVE"):
            samples = self._sim_signal(64, self.timebase_setup.window_s)
            return ",".join(f"{v:.4f}" for v in samples)
        return super().simulate_command(command)


# ============================================================ 家族级算法（跨品牌一致，型号驱动不要改）

def sim_samples(kind: str, freq_hz: float, vpp_v: float, offset_v: float, points: int,
                window_s: float, noise: float, phase: float = 0.0) -> list[float]:
    """确定性仿真波形：种子由参数派生，同参数永远同样结果（测试才能断言）"""
    amp = vpp_v / 2.0
    rng = random.Random(f"{kind}:{freq_hz}:{vpp_v}:{offset_v}:{points}:{noise:.4f}")
    out = []
    for i in range(points):
        t = window_s * i / max(1, points - 1)
        x = ((t * freq_hz) % 1.0 + phase) % 1.0 if freq_hz > 0 else 0.0
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
        else:
            y = rng.uniform(-1.0, 1.0)
        value = offset_v + amp * y
        if noise and kind != "dc":
            value += rng.uniform(-1.0, 1.0) * amp * noise
        out.append(round(value, 6))
    return out


def measure_samples(samples, x_incr_s: float, freq_hint_hz: float = 0.0) -> dict:
    """由采样点自算测量项（8 项，单位见 MEASURE_UNITS）

    用带迟滞的过中点检测算周期：抗噪、跨品牌一致，不依赖仪器的 `:MEASure` 命令。
    """
    data = list(samples or [])
    if len(data) < 3:
        return {k: None for k in MEASURE_ITEMS}
    vmax, vmin = max(data), min(data)
    vpp = vmax - vmin
    mid = (vmax + vmin) / 2.0
    span = vpp or 1e-9
    mean = sum(data) / len(data)
    rms = math.sqrt(sum(v * v for v in data) / len(data))
    lo, hi = mid - span * 0.15, mid + span * 0.15
    crossings: list[int] = []
    armed = data[0] < lo
    for i in range(1, len(data)):
        v = data[i]
        if armed and v >= hi:
            crossings.append(i)
            armed = False
        elif not armed and v <= lo:
            armed = True
    period = None
    if len(crossings) >= 2:
        gaps = sorted(crossings[i + 1] - crossings[i] for i in range(len(crossings) - 1))
        period = gaps[len(gaps) // 2] * x_incr_s
    elif freq_hint_hz:
        period = 1.0 / freq_hint_hz
    freq = (1.0 / period) if period else float(freq_hint_hz or 0.0)
    band = max(1, len(data) // 20)
    srt = sorted(data)
    base = sum(srt[:band]) / band
    top = sum(srt[-band:]) / band
    rise = None
    r_lo, r_hi = vmin + 0.1 * vpp, vmin + 0.9 * vpp
    for i in range(1, len(data)):
        if data[i - 1] < r_lo <= data[i]:
            j = i
            while j < len(data) and data[j] < r_hi:
                j += 1
            if j < len(data):
                rise = (j - i) * x_incr_s
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
