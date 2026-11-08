"""能力层仿真：不连控制器、不下发 GCS 命令。

模型：线性伺服 + 行程限位 + 到位时间估算。
- 位置状态由契约单位（µm / µrad）表达，不经过任何换算。
- 未回零时拒绝运动（真机同样会拒绝），用于验证调用方的时序假设。
- 到位时间按速度估算写进 describe，让上层能核对等待时长是否合理。
"""

from __future__ import annotations

from typing import Iterable, Mapping

from contracts.motion import (
    AXES,
    MotionIdentity,
    MotionLimitError,
    MotionNotConnected,
    MotionUnsupported,
    MotionLimits,
    Position,
    normalize_axes,
)

SIM_LAYER = "capability"

DEFAULT_LIMITS = MotionLimits(
    lower={"X": -25000.0, "Y": -25000.0, "Z": -12500.0, "U": -20000.0, "V": -20000.0, "W": -20000.0},
    upper={"X": 25000.0, "Y": 25000.0, "Z": 12500.0, "U": 20000.0, "V": 20000.0, "W": 20000.0},
)

#: 契约单位/秒；线性轴 µm/s，旋转轴 µrad/s
DEFAULT_VELOCITY = {"X": 5000.0, "Y": 5000.0, "Z": 2500.0, "U": 20000.0, "V": 20000.0, "W": 20000.0}


class CapabilitySimulator:
    capability = "hexapod"
    contract_version = "1.0"

    def __init__(
        self,
        resource: str,
        *,
        limits: MotionLimits | None = None,
        velocity: Mapping[str, float] | None = None,
    ) -> None:
        self.resource = resource
        self.limits = limits or DEFAULT_LIMITS
        self.velocity = dict(velocity or DEFAULT_VELOCITY)
        self._open = False
        self._referenced = False
        self._position = {axis: 0.0 for axis in AXES}
        self._moving: tuple[str, ...] = ()
        self._moves = 0
        self._last_settle_s = 0.0

    # -- 生命周期 -------------------------------------------------------
    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False
        self._moving = ()

    @property
    def connected(self) -> bool:
        return self._open

    def identify(self) -> MotionIdentity:
        if not self._open:
            raise MotionNotConnected("仿真器未打开")
        return MotionIdentity(
            vendor="Physik Instrumente",
            model="C-887.52",
            serial=_serial_from(self.resource),
            firmware="sim-1.0.0",
            resource=self.resource,
            axes=AXES,
        )

    # -- 参考 -----------------------------------------------------------
    def reference(self, *, timeout_s: float = 60.0) -> None:
        self._require()
        self._referenced = True
        self._position = {axis: 0.0 for axis in AXES}
        self._last_settle_s = min(float(timeout_s), 1.0)

    # -- 运动 -----------------------------------------------------------
    def move_to(
        self, target: Mapping[str, float], *, wait: bool = True, timeout_s: float = 30.0
    ) -> Position:
        self._require()
        self._check_referenced()
        clean = self._clean(target)
        self.limits.check(clean)
        self._apply(clean, timeout_s=timeout_s, wait=wait)
        return self.query_position()

    def move_relative(
        self, delta: Mapping[str, float], *, wait: bool = True, timeout_s: float = 30.0
    ) -> Position:
        self._require()
        self._check_referenced()
        clean = self._clean(delta)
        target = {axis: self._position[axis] + clean[axis] for axis in clean}
        self.limits.check(target)
        self._apply(target, timeout_s=timeout_s, wait=wait)
        return self.query_position()

    def query_position(self) -> Position:
        self._require()
        return Position(dict(self._position))

    def stop(self, axes: Iterable[str] | None = None) -> None:
        self._require()
        self._moving = ()

    # -- 内部 -----------------------------------------------------------
    def _apply(self, target: Mapping[str, float], *, timeout_s: float, wait: bool) -> None:
        distance = max(
            (abs(target[a] - self._position[a]) / self.velocity[a] for a in target),
            default=0.0,
        )
        if wait and distance > timeout_s:
            raise MotionUnsupported(
                f"按速度估算需 {distance:.2f}s，超过 timeout {timeout_s}s；请提高 timeout 或分段移动"
            )
        self._last_settle_s = distance
        for axis, value in target.items():
            self._position[axis] = float(value)
        self._moving = tuple(target) if (distance > 0 and not wait) else ()
        self._moves += 1

    def _clean(self, target: Mapping[str, float]) -> dict[str, float]:
        if not target:
            raise MotionUnsupported("目标为空")
        out = {}
        for axis, value in target.items():
            name = str(axis).strip().upper()
            if name not in AXES:
                raise MotionUnsupported(f"未知轴 {axis!r}；可用 {AXES}")
            out[name] = float(value)
        return out

    def _check_referenced(self) -> None:
        if not self._referenced:
            raise MotionUnsupported("平台未回零，拒绝运动；先调用 reference()")

    def _require(self) -> None:
        if not self._open:
            raise MotionNotConnected("仿真器未打开或已关闭")

    def describe(self) -> Mapping[str, object]:
        return {
            "mode": "simulate",
            "simulation_layer": SIM_LAYER,
            "connected": self._open,
            "referenced": self._referenced,
            "position": dict(self._position),
            "limits": {"lower": dict(self.limits.lower), "upper": dict(self.limits.upper)},
            "velocity": dict(self.velocity),
            "moving": list(self._moving),
            "moves": self._moves,
            "last_settle_s": self._last_settle_s,
        }


def _serial_from(resource: str) -> str:
    text = str(resource)
    for prefix in ("usb:", "serial:"):
        if text.startswith(prefix):
            return text.split(":", 1)[1] or "SIM0"
    if "://" in text:
        return text.split("://", 1)[1] or "SIM0"
    return "SIM0"
