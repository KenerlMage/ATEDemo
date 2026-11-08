"""定位平台能力契约（capability = hexapod，contract 1.0）。

位置
----
L3 契约层。六足平台（PI C-887 / H-811 等）与单轴位移台共用同一套语义：
「给目标位姿、等它到位、读回实测」，不承诺具体机构与坐标变换。

单位约定（契约级，不随品牌变）
------------------------------
- 平移轴 X / Y / Z：微米（µm）
- 旋转轴 U / V / W：微弧度（µrad）

厂商库若以毫米或度为单位，由包层换算，用例永远看到契约单位。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, Mapping

CAPABILITY = "hexapod"
CONTRACT_VERSION = "1.0"

AXES = ("X", "Y", "Z", "U", "V", "W")
LINEAR_AXES = ("X", "Y", "Z")
ROTARY_AXES = ("U", "V", "W")

UNITS = {axis: ("um" if axis in LINEAR_AXES else "urad") for axis in AXES}


@dataclass(frozen=True)
class Position:
    """位姿：键为轴名，值为契约单位下的浮点数。"""

    values: Mapping[str, float]

    def get(self, axis: str) -> float:
        try:
            return float(self.values[axis])
        except KeyError as exc:
            raise MotionUnsupported(f"位姿里没有轴 {axis}；本台可用 {sorted(self.values)}") from exc

    def as_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.values.items()}

    def distance_to(self, other: "Position") -> float:
        """欧氏距离，仅用于线性轴的近似到位判断。"""
        keys = [a for a in LINEAR_AXES if a in self.values and a in other.values]
        return sum((self.get(a) - other.get(a)) ** 2 for a in keys) ** 0.5


@dataclass(frozen=True)
class MotionLimits:
    lower: Mapping[str, float]
    upper: Mapping[str, float]

    def check(self, target: Mapping[str, float]) -> None:
        for axis, value in target.items():
            if axis not in self.lower or axis not in self.upper:
                raise MotionUnsupported(f"轴 {axis} 没有行程定义")
            if not (self.lower[axis] <= value <= self.upper[axis]):
                raise MotionLimitError(
                    f"轴 {axis} 目标 {value} 超出行程 [{self.lower[axis]}, {self.upper[axis]}]"
                )


@dataclass(frozen=True)
class MotionIdentity:
    vendor: str
    model: str
    serial: str
    firmware: str
    resource: str
    axes: tuple[str, ...] = AXES


class MotionError(Exception):
    """定位平台错误基类。"""


class MotionNotConnected(MotionError):
    pass


class MotionUnsupported(MotionError):
    pass


class MotionTimeout(MotionError):
    pass


class MotionLimitError(MotionError):
    pass


class MotionDataError(MotionError):
    pass


class PositionerDriver(ABC):
    capability = CAPABILITY
    contract_version = CONTRACT_VERSION

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def identify(self) -> MotionIdentity: ...

    @abstractmethod
    def reference(self, *, timeout_s: float = 60.0) -> None:
        """回零/参考。未完成的平台不允许运动，调用方必须处理异常。"""

    @abstractmethod
    def move_to(
        self, target: Mapping[str, float], *, wait: bool = True, timeout_s: float = 30.0
    ) -> Position: ...

    @abstractmethod
    def move_relative(
        self, delta: Mapping[str, float], *, wait: bool = True, timeout_s: float = 30.0
    ) -> Position: ...

    @abstractmethod
    def query_position(self) -> Position: ...

    @abstractmethod
    def stop(self, axes: Iterable[str] | None = None) -> None: ...

    @abstractmethod
    def describe(self) -> Mapping[str, object]: ...


def normalize_axes(axes: Iterable[str] | None) -> tuple[str, ...]:
    """校验轴名，避免把 X/Y/Z 拼错成小写后静默不动。"""
    if axes is None:
        return AXES
    out = []
    for axis in axes:
        name = str(axis).strip().upper()
        if name not in AXES:
            raise MotionUnsupported(f"未知轴 {axis!r}；可用 {AXES}")
        out.append(name)
    return tuple(out) or AXES


__all__ = [
    "AXES",
    "CAPABILITY",
    "CONTRACT_VERSION",
    "LINEAR_AXES",
    "ROTARY_AXES",
    "UNITS",
    "MotionDataError",
    "MotionError",
    "MotionIdentity",
    "MotionLimitError",
    "MotionLimits",
    "MotionNotConnected",
    "MotionTimeout",
    "MotionUnsupported",
    "Position",
    "PositionerDriver",
    "normalize_axes",
]
