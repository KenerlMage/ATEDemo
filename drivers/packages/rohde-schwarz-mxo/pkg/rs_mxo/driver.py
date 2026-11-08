"""R&S MXO 系列示波器驱动包（共享包示例）。

分层位置
--------
- 本模块是 L2 厂商实现：把 SCPI（通过 RsInstrument 或仿真 IO）翻译成 contracts.scope 的语义。
- 不允许出现「设备档案解析」「文件路径」「端口分配」「用例逻辑」。
- 厂商库（RsInstrument）只在本文件内 import，且为延迟 import：无硬件/无库的构建机上仍可跑仿真与契约测试。

连接策略
--------
mode="hardware"（默认）：必须能用 RsInstrument 建立会话，失败即抛 DriverUnavailable，不静默降级。
mode="simulate"      ：显式仿真，仅用于 CI、联调与培训；站点配置未声明 simulate 时不得启用。
"""

from __future__ import annotations

import math
import struct
from typing import Mapping, Sequence

from contracts.scope import (
    CAPABILITY,
    CONTRACT_VERSION,
    MEASURE_ITEMS,
    InstrumentIdentity,
    ScopeDataError,
    ScopeDriver,
    ScopeError,
    ScopeNotConnected,
    ScopeTimeout,
    ScopeUnsupported,
    TriggerSpec,
    Waveform,
)

from . import scpi
from .simulate import SimIo

__version__ = "1.2.0"


class DriverUnavailable(ScopeError):
    """运行环境不满足：厂商库缺失、资源名不可用、会话建立失败。"""


def _crossings(samples: Sequence[float], level: float) -> list[float]:
    """返回信号穿越 level 的时刻序号（线性插值），用于频率/占空比类计算。"""
    out: list[float] = []
    for i in range(1, len(samples)):
        a, b = samples[i - 1], samples[i]
        if (a - level) == 0:
            out.append(float(i - 1))
        elif (a - level) * (b - level) < 0:
            ratio = (level - a) / (b - a)
            out.append(i - 1 + ratio)
    return out


def _frequency(samples: Sequence[float], dt_s: float) -> float:
    mean = sum(samples) / len(samples)
    rising = _crossings(samples, mean)[::2]
    if len(rising) < 2:
        raise ScopeUnsupported("波形内不足两个上升沿，无法给出频率")
    period_samples = (rising[-1] - rising[0]) / (len(rising) - 1)
    return 1.0 / (period_samples * dt_s)


def _rise_time(samples: Sequence[float], dt_s: float) -> float:
    lo, hi = min(samples), max(samples)
    low_th = lo + 0.1 * (hi - lo)
    high_th = lo + 0.9 * (hi - lo)
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


class RsMxoScope(ScopeDriver):
    capability = CAPABILITY
    contract_version = CONTRACT_VERSION

    def __init__(
        self,
        resource: str,
        *,
        mode: str = "hardware",
        timeout_ms: int = 15000,
        horizontal_divisions: float = 10.0,
        channel_count: int = 4,
        prefer_instrument_measure: bool = False,
        visa_options: str | None = None,
    ) -> None:
        if mode not in ("hardware", "simulate"):
            raise ValueError(f"未知 mode: {mode}")
        self.resource = resource
        self.mode = mode
        self.timeout_ms = int(timeout_ms)
        self.horizontal_divisions = float(horizontal_divisions)
        self.channel_count = int(channel_count)
        self.prefer_instrument_measure = bool(prefer_instrument_measure)
        self.visa_options = visa_options

        self._io = None
        self._identity: InstrumentIdentity | None = None
        self._time_span_s = 1e-3
        self._channels: dict[int, dict[str, object]] = {}
        self._trigger = TriggerSpec()
        self._last: dict[int, Waveform] = {}
        self._ops = 0

    # -- 生命周期 -------------------------------------------------------
    def connect(self) -> None:
        if self._io is not None:
            return
        if self.mode == "simulate":
            io = SimIo(self.resource)
        else:
            io = self._open_rsinstrument()
        self._io = io
        try:
            self._io.write_str(scpi.data_format())
            self._io.write_str(scpi.CLEAR_STATUS)
            self._identity = self._read_identity()
        except ScopeError:
            self._io = None
            raise
        except Exception as exc:  # 厂商库异常不得外泄
            self._io = None
            raise DriverUnavailable(f"连接初始化失败: {exc}") from exc

    def _open_rsinstrument(self):
        try:
            from RsInstrument import RsInstrument  # 厂商库：仅出现在本包内
        except Exception as exc:
            raise DriverUnavailable(
                "缺少 RsInstrument 厂商库；离线部署请使用随包 vendor 目录，"
                "或在站点配置中显式声明 mode=simulate"
            ) from exc
        try:
            inst = RsInstrument(self.resource, id_query=False, reset=False, options=self.visa_options)
            inst.visa_timeout = self.timeout_ms
            return inst
        except Exception as exc:
            raise DriverUnavailable(f"无法建立会话 {self.resource}: {exc}") from exc

    def close(self) -> None:
        if self._io is not None:
            try:
                self._io.close()
            finally:
                self._io = None
                self._identity = None

    def identify(self) -> InstrumentIdentity:
        self._require()
        assert self._identity is not None
        return self._identity

    def _read_identity(self) -> InstrumentIdentity:
        try:
            idn = self._io.query_str(scpi.IDENTITY)
        except Exception as exc:
            raise ScopeTimeout(f"*IDN? 无响应: {exc}") from exc
        parts = [p.strip() for p in idn.split(",")]
        if len(parts) < 4:
            raise ScopeDataError(f"无法解析 *IDN?: {idn!r}")
        return InstrumentIdentity(
            vendor=parts[0], model=parts[1], serial=parts[2], firmware=parts[3], resource=self.resource
        )

    # -- 配置 -----------------------------------------------------------
    def configure_timebase(self, *, time_span_s: float) -> None:
        self._require()
        if time_span_s <= 0:
            raise ScopeUnsupported(f"time_span_s 必须为正数: {time_span_s}")
        self._write(scpi.timebase_scale(time_span_s / self.horizontal_divisions))
        self._write(scpi.timebase_position(0.0))
        self._time_span_s = float(time_span_s)

    def configure_channel(
        self, channel: int, *, scale_v: float, offset_v: float = 0.0, coupling: str = "DC"
    ) -> None:
        self._require()
        self._check_channel(channel)
        if scale_v <= 0:
            raise ScopeUnsupported(f"非法通道或档位: channel={channel}, scale_v={scale_v}")
        if coupling.upper() not in ("DC", "AC", "GND"):
            raise ScopeUnsupported(f"非法耦合方式: {coupling}")
        self._write(scpi.channel_state(channel, True))
        self._write(scpi.channel_scale(channel, scale_v))
        self._write(scpi.channel_offset(channel, offset_v))
        self._write(scpi.channel_coupling(channel, coupling))
        self._channels[channel] = {"scale_v": float(scale_v), "offset_v": float(offset_v), "coupling": coupling.upper()}

    def configure_trigger(self, spec: TriggerSpec) -> None:
        self._require()
        if spec.slope.upper() not in ("RISING", "FALLING"):
            raise ScopeUnsupported(f"非法边沿: {spec.slope}")
        self._write(scpi.trigger_source(spec.source_channel))
        self._write(scpi.trigger_edge_slope(spec.slope))
        self._write(scpi.trigger_level(spec.level_v))
        self._write(scpi.trigger_mode(spec.mode))
        self._trigger = spec

    # -- 数据 -----------------------------------------------------------
    def acquire(self, channels: Sequence[int], *, points: int = 10000) -> Mapping[int, Waveform]:
        self._require()
        if not channels:
            raise ScopeUnsupported("channels 不能为空")
        if points < 16:
            raise ScopeUnsupported(f"points 过小: {points}")
        self._write(scpi.acquire_points(points))

        result: dict[int, Waveform] = {}
        for ch in channels:
            self._check_channel(ch)
            try:
                raw = self._io.query_bin_block(scpi.channel_data(ch))
            except ScopeError:
                raise
            except Exception as exc:
                raise ScopeTimeout(f"读取 CHANnel{ch} 数据失败: {exc}") from exc
            n = len(raw) // 4
            if n < 2:
                raise ScopeDataError(f"CHANnel{ch} 数据长度异常: {len(raw)} 字节")
            samples = struct.unpack("<%df" % n, raw[: n * 4])
            t0, dt = self._time_axis(ch, n, points)
            wf = Waveform(channel=ch, t0_s=t0, dt_s=dt, samples=tuple(samples))
            self._check_waveform(wf)
            result[ch] = wf
            self._last[ch] = wf
        self._ops += 1
        return result

    def _time_axis(self, channel: int, n: int, points: int):
        try:
            header = self._io.query_str(scpi.channel_data_header(channel))
            start_s, stop_s, n_hdr = (float(v) for v in header.split(",")[:3])
            if n_hdr >= 2 and abs(n_hdr - n) <= 1 and stop_s > start_s:
                return start_s, (stop_s - start_s) / (n_hdr - 1)
        except Exception:
            pass
        # 回退：按当前时基均匀分布（真机 header 不可用时仍给出可断言的时间轴）
        return 0.0, self._time_span_s / max(n - 1, 1)

    @staticmethod
    def _check_waveform(wf: Waveform) -> None:
        if wf.dt_s <= 0:
            raise ScopeDataError("时间轴步长非正")
        for v in wf.samples[: min(len(wf.samples), 512)]:
            if math.isnan(v) or math.isinf(v):
                raise ScopeDataError("波形含 NaN/Inf")

    def measure(self, channel: int, item: str) -> float:
        self._require()
        if item not in MEASURE_ITEMS:
            raise ScopeUnsupported(f"未知测量项: {item}；支持 {sorted(MEASURE_ITEMS)}")
        if self.prefer_instrument_measure and self.mode == "hardware":
            value = self._instrument_measure(channel, item)
            if value is not None:
                return value
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

    def _instrument_measure(self, channel: int, item: str) -> float | None:
        slot = 1
        for cmd in scpi.measurement_setup(slot, channel, item):
            self._write(cmd)
        try:
            value = float(self._io.query_str(scpi.measurement_result(slot)))
        except Exception:
            return None
        return None if math.isnan(value) else value

    # -- 诊断 -----------------------------------------------------------
    def describe(self) -> Mapping[str, object]:
        return {
            "capability": self.capability,
            "contract": self.contract_version,
            "package": "rohde-schwarz-mxo",
            "package_version": __version__,
            "resource": self.resource,
            "mode": self.mode,
            "connected": self._io is not None,
            "identity": None if self._identity is None else {
                "vendor": self._identity.vendor,
                "model": self._identity.model,
                "serial": self._identity.serial,
                "firmware": self._identity.firmware,
            },
            "timebase": {"time_span_s": self._time_span_s, "horizontal_divisions": self.horizontal_divisions},
            "channels": dict(self._channels),
            "trigger": {
                "source_channel": self._trigger.source_channel,
                "level_v": self._trigger.level_v,
                "slope": self._trigger.slope,
                "mode": self._trigger.mode,
            },
            "last_acquire": {
                ch: {"n": wf.n, "dt_s": wf.dt_s, "vpp": wf.vpp} for ch, wf in self._last.items()
            },
            "operations": self._ops,
        }

    # -- 内部 -----------------------------------------------------------
    def _require(self) -> None:
        if self._io is None:
            raise ScopeNotConnected("会话未建立或已关闭")

    def _check_channel(self, channel: int) -> None:
        if not isinstance(channel, int) or channel < 1 or channel > self.channel_count:
            raise ScopeUnsupported(
                f"非法通道 {channel}；本型号可用通道 1..{self.channel_count}"
            )

    def _write(self, command: str) -> None:
        try:
            self._io.write_str(command)
        except ScopeError:
            raise
        except Exception as exc:
            raise ScopeTimeout(f"下发命令失败 {command!r}: {exc}") from exc
