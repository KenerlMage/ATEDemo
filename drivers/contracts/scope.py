"""示波器能力契约 · contract 1.0

设计约束
--------
1. 只描述「用例会用的动作与语义」，不描述型号、不描述 SCPI 命令、不描述连接方式。
2. 返回值必须是可序列化/可断言的稳定结构，避免把厂商对象泄漏给用例层。
3. 所有异常都属于本模块定义的层级，驱动包不得向外抛厂商库异常。

变更规则
--------
- 新增方法或放宽语义：MINOR 版本（驱动包声明兼容即可）。
- 改方法签名、改返回结构、改变量纲或语义：MAJOR 版本，全部驱动包需重新认证。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping, Sequence

CAPABILITY = "oscilloscope"
CONTRACT_VERSION = "1.0"

#: measure(item) 支持的测量项。语义在契约内固定，各厂商实现不得改变量纲。
#: vpp/vmax/vmin/mean/rms 单位 V；frequency/period 单位 Hz/s；rise_time 单位 s。
MEASURE_ITEMS = frozenset(
    {"vpp", "vmax", "vmin", "mean", "rms", "frequency", "period", "rise_time"}
)


class ScopeError(Exception):
    """示波器契约异常基类。"""


class ScopeNotConnected(ScopeError):
    """会话未建立，或已关闭。"""


class ScopeTimeout(ScopeError):
    """仪器在超时内未响应。"""


class ScopeUnsupported(ScopeError):
    """请求的能力本型号不支持（如未知测量项、非法通道）。"""


class ScopeDataError(ScopeError):
    """返回数据不合法（长度不符、时间轴非单调、二进制块解析失败）。"""


@dataclass(frozen=True)
class InstrumentIdentity:
    vendor: str
    model: str
    serial: str
    firmware: str
    resource: str


@dataclass(frozen=True)
class TriggerSpec:
    """边沿触发。level_v 为触发电平，source_channel 从 1 开始。"""

    source_channel: int = 1
    level_v: float = 0.0
    slope: str = "RISING"  # RISING | FALLING
    mode: str = "NORMAL"  # NORMAL | AUTO | FREE


@dataclass(frozen=True)
class Waveform:
    """一次采集结果。时间轴由 t0_s + dt_s * i 定义，不使用厂商时间数组。"""

    channel: int
    t0_s: float
    dt_s: float
    samples: tuple[float, ...]
    unit: str = "V"

    @property
    def n(self) -> int:
        return len(self.samples)

    @property
    def duration_s(self) -> float:
        return self.dt_s * max(self.n - 1, 0)

    @property
    def vmin(self) -> float:
        return min(self.samples) if self.samples else float("nan")

    @property
    def vmax(self) -> float:
        return max(self.samples) if self.samples else float("nan")

    @property
    def vpp(self) -> float:
        return self.vmax - self.vmin

    @property
    def mean(self) -> float:
        return sum(self.samples) / self.n if self.samples else float("nan")

    @property
    def rms(self) -> float:
        return (sum(v * v for v in self.samples) / self.n) ** 0.5 if self.samples else float("nan")

    def stats(self) -> Mapping[str, float]:
        return {
            "n": float(self.n),
            "t0_s": self.t0_s,
            "dt_s": self.dt_s,
            "duration_s": self.duration_s,
            "vmin": self.vmin,
            "vmax": self.vmax,
            "vpp": self.vpp,
            "mean": self.mean,
            "rms": self.rms,
        }


class ScopeDriver(ABC):
    """能力契约实现基类。

    生命周期：构造 → connect() → configure_* → acquire()/measure() → close()。
    connect() 之后调用方可重复 configure 与 acquire；close() 之后任何操作须抛
    ScopeNotConnected，便于运行时统一处理设备掉线。
    """

    capability: str = CAPABILITY
    contract_version: str = CONTRACT_VERSION

    # -- 生命周期 -------------------------------------------------------
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def identify(self) -> InstrumentIdentity: ...

    # -- 配置 -----------------------------------------------------------
    @abstractmethod
    def configure_timebase(self, *, time_span_s: float) -> None:
        """设置整屏时间跨度（秒）。实现需保证 acquire 的 duration ≈ time_span_s。"""

    @abstractmethod
    def configure_channel(
        self,
        channel: int,
        *,
        scale_v: float,
        offset_v: float = 0.0,
        coupling: str = "DC",
    ) -> None:
        """设置垂直档位。scale_v 为每格电压（V/div），offset_v 为垂直偏移（V）。"""

    @abstractmethod
    def configure_trigger(self, spec: TriggerSpec) -> None: ...

    # -- 数据 -----------------------------------------------------------
    @abstractmethod
    def acquire(self, channels: Sequence[int], *, points: int = 10000) -> Mapping[int, Waveform]:
        """采集指定通道。返回每条通道一个 Waveform，长度等于 points（允许 ±1）。"""

    @abstractmethod
    def measure(self, channel: int, item: str) -> float:
        """按契约语义返回单个测量值。item 不在 MEASURE_ITEMS 内须抛 ScopeUnsupported。"""

    # -- 诊断 -----------------------------------------------------------
    @abstractmethod
    def describe(self) -> Mapping[str, object]:
        """返回可读的连接与配置快照，供现场排障与拓扑展示。字段只增不改。"""
