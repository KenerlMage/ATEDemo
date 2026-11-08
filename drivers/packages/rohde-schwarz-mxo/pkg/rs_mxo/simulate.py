"""能力层仿真（1.3.0 起）。

与 1.2.0 的差别
--------------
1.2.0 提供的是 SCPI 文本替身（SimIo）：驱动仍然逐条下发命令、再把返回值解析回契约类型，
本质上是在"链路层"假装一台仪器。1.3.0 去掉这一层——仿真直接实现能力契约：

- 输入是契约参数（时基、垂直档位、触发方式），不是命令字符串；
- 输出是契约结构（Waveform / 测量值 / InstrumentIdentity），不经过任何解析；
- 不建立连接、不寻址、不产生报文，资源名只用于身份串。

因此它验证的是"能力语义是否正确"，而不是"命令拼写是否正确"。真机分支仍走
RsInstrument → SCPI → 解析，两条路径共用上层用例与解析器。

边界
----
- 不模拟噪声带宽、探头衰减、触发抖动等物理特性；
- 数值由固定模型生成，可用于断言配置与结果的关系，但不代表真机精度；
- 不允许作为现场验收依据；真机核对前 verified_on_hardware 保持 false。
"""

from __future__ import annotations

import math
import random
import struct
from typing import Mapping, Sequence

from contracts.scope import (
    CAPABILITY,
    CONTRACT_VERSION,
    MEASURE_ITEMS,
    InstrumentIdentity,
    ScopeNotConnected,
    ScopeUnsupported,
    TriggerSpec,
    Waveform,
)

SIM_LAYER = "capability"


def _crossings(samples: Sequence[float], level: float) -> list[float]:
    out: list[float] = []
    for i in range(1, len(samples)):
        a, b = samples[i - 1], samples[i]
        if a - level == 0:
            out.append(float(i - 1))
        elif (a - level) * (b - level) < 0:
            out.append(i - 1 + (level - a) / (b - a))
    return out


def _frequency(samples: Sequence[float], dt_s: float) -> float:
    mean = sum(samples) / len(samples)
    rising = _crossings(samples, mean)[::2]
    if len(rising) < 2:
        raise ScopeUnsupported("波形内不足两个上升沿，无法给出频率")
    period = (rising[-1] - rising[0]) / (len(rising) - 1)
    return 1.0 / (period * dt_s)


def _rise_time(samples: Sequence[float], dt_s: float) -> float:
    lo, hi = min(samples), max(samples)
    low_th, high_th = lo + 0.1 * (hi - lo), lo + 0.9 * (hi - lo)
    t_low = t_high = None
    for i in range(1, len(samples)):
        a, b = samples[i - 1], samples[i]
        if t_low is None and a <= low_th <= b:
            t_low = (i - 1 + (low_th - a) / (b - a)) * dt_s
        if t_low is not None and a <= high_th <= b:
            t_high = (i - 1 + (high_th - a) / (b - a)) * dt_s
            break
    if t_low is None or t_high is None:
        raise ScopeUnsupported("未找到完整上升沿（10%→90%）")
    return t_high - t_low


class CapabilitySimulator:
    """能力契约的仿真实现：只在契约语义上生成数据。"""

    capability = CAPABILITY
    contract_version = CONTRACT_VERSION
    layer = SIM_LAYER

    def __init__(
        self,
        resource: str = "SIM::MXO44::INSTR",
        *,
        horizontal_divisions: float = 10.0,
        channel_count: int = 4,
    ) -> None:
        self.resource = resource
        self.horizontal_divisions = float(horizontal_divisions)
        self.channel_count = int(channel_count)
        self.time_span_s = 1e-3
        self.channels: dict[int, dict[str, object]] = {}
        self.trigger = TriggerSpec()
        self._last: dict[int, Waveform] = {}
        self._open = False
        self.operations = 0

    # -- 生命周期（无链路，仅状态开关）------------------------------------
    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def connected(self) -> bool:
        return self._open

    def identify(self) -> InstrumentIdentity:
        self._require()
        return InstrumentIdentity(
            vendor="Rohde&Schwarz",
            model="MXO44",
            serial="SIM-000000",
            firmware="sim-1.3.0",
            resource=self.resource,
        )

    # -- 契约参数 → 内部状态 --------------------------------------------
    def configure_timebase(self, *, time_span_s: float) -> None:
        self._require()
        if time_span_s <= 0:
            raise ScopeUnsupported(f"time_span_s 必须为正数: {time_span_s}")
        self.time_span_s = float(time_span_s)

    def configure_channel(
        self, channel: int, *, scale_v: float, offset_v: float = 0.0, coupling: str = "DC"
    ) -> None:
        self._require()
        self._check_channel(channel)
        if scale_v <= 0:
            raise ScopeUnsupported(f"非法通道或档位: channel={channel}, scale_v={scale_v}")
        if coupling.upper() not in ("DC", "AC", "GND"):
            raise ScopeUnsupported(f"非法耦合方式: {coupling}")
        self.channels[channel] = {
            "scale_v": float(scale_v),
            "offset_v": float(offset_v),
            "coupling": coupling.upper(),
        }

    def configure_trigger(self, spec: TriggerSpec) -> None:
        self._require()
        if spec.slope.upper() not in ("RISING", "FALLING"):
            raise ScopeUnsupported(f"非法边沿: {spec.slope}")
        self.trigger = spec

    # -- 能力：采集与测量 -----------------------------------------------
    def acquire(self, channels: Sequence[int], *, points: int = 10000) -> Mapping[int, Waveform]:
        self._require()
        if not channels:
            raise ScopeUnsupported("channels 不能为空")
        if points < 16:
            raise ScopeUnsupported(f"points 过小: {points}")
        dt = self.time_span_s / max(points - 1, 1)
        result: dict[int, Waveform] = {}
        for ch in channels:
            self._check_channel(ch)
            cfg = self.channels.get(ch)
            if cfg is None:
                raise ScopeUnsupported(f"通道 {ch} 未配置档位，仿真无法生成波形")
            samples = self._samples(ch, int(points), dt, float(cfg["scale_v"]), float(cfg["offset_v"]))
            wf = Waveform(channel=ch, t0_s=0.0, dt_s=dt, samples=samples)
            self._last[ch] = wf
            result[ch] = wf
        self.operations += 1
        return result

    def measure(self, channel: int, item: str) -> float:
        self._require()
        if item not in MEASURE_ITEMS:
            raise ScopeUnsupported(f"未知测量项: {item}；支持 {sorted(MEASURE_ITEMS)}")
        self._check_channel(channel)
        wf = self._last.get(channel)
        if wf is None:
            wf = self.acquire([channel])[channel]
        if item == "vpp":
            return wf.vpp
        if item == "vmax":
            return wf.vmax
        if item == "vmin":
            return wf.vmin
        if item == "mean":
            return wf.mean
        if item == "rms":
            return wf.rms
        if item == "frequency":
            return _frequency(wf.samples, wf.dt_s)
        if item == "period":
            return 1.0 / _frequency(wf.samples, wf.dt_s)
        return _rise_time(wf.samples, wf.dt_s)

    def describe(self) -> Mapping[str, object]:
        return {
            "layer": SIM_LAYER,
            "connected": self._open,
            "timebase": {"time_span_s": self.time_span_s, "horizontal_divisions": self.horizontal_divisions},
            "channels": dict(self.channels),
            "trigger": {
                "source_channel": self.trigger.source_channel,
                "level_v": self.trigger.level_v,
                "slope": self.trigger.slope,
                "mode": self.trigger.mode,
            },
            "last_acquire": {ch: {"n": wf.n, "dt_s": wf.dt_s, "vpp": wf.vpp} for ch, wf in self._last.items()},
            "operations": self.operations,
        }

    # -- 内部 -----------------------------------------------------------
    def _require(self) -> None:
        if not self._open:
            raise ScopeNotConnected("仿真未开启或已关闭")

    def _check_channel(self, channel: int) -> None:
        if not isinstance(channel, int) or channel < 1 or channel > self.channel_count:
            raise ScopeUnsupported(f"非法通道 {channel}；本型号可用通道 1..{self.channel_count}")

    def _samples(self, channel: int, points: int, dt: float, scale: float, offset: float) -> tuple[float, ...]:
        amplitude = 0.8 * scale          # 8 格屏，占 80%
        cycles = 2.0                     # 整屏两个周期，便于频率断言
        freq = cycles / self.time_span_s
        rng = random.Random(f"{self.resource}|{channel}|{points}|{scale}")
        noise = amplitude * 1e-4
        out = []
        for i in range(points):
            t = i * dt
            out.append(offset + amplitude * math.sin(2 * math.pi * freq * t) + rng.uniform(-noise, noise))
        return tuple(out)

    # 兼容旧工具：直接取二进制块（不做 SCPI 解析）
    def waveform_bytes(self, channel: int) -> bytes:
        wf = self._last.get(channel)
        if wf is None:
            raise ScopeNotConnected(f"通道 {channel} 尚无采集结果")
        return struct.pack("<%df" % wf.n, *wf.samples)
