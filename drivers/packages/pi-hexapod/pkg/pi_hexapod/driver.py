"""PI 六足平台驱动包（基于 pipython / GCS）。

分层位置
--------
L1 包层：把 GCS 命令翻译成 contracts.motion 的语义（回零、给目标、等到位、读回）。
- `pipython` 延迟 import，只出现在本文件与 gcs.py。
- 仿真走能力层（simulate.py），不连控制器。
- 单位：C-887 空间轴本身以 µm / µrad 表达，与契约一致，不做无依据缩放。

连接策略
--------
mode="hardware"：必须连上控制器并完成身份识别，否则抛 DriverUnavailable，不静默降级。
mode="simulate"：显式声明才启用。
"""

from __future__ import annotations

from typing import Iterable, Mapping

from contracts.motion import (
    AXES,
    CAPABILITY,
    CONTRACT_VERSION,
    MotionDataError,
    MotionError,
    MotionIdentity,
    MotionLimitError,
    MotionLimits,
    MotionNotConnected,
    MotionTimeout,
    MotionUnsupported,
    Position,
    PositionerDriver,
    normalize_axes,
)

from . import gcs
from .simulate import SIM_LAYER, CapabilitySimulator

__version__ = "1.0.0"


class DriverUnavailable(MotionError):
    """运行环境不满足：缺 pipython、控制器连不上、缺 GCS DLL。"""


class PiHexapod(PositionerDriver):
    capability = CAPABILITY
    contract_version = CONTRACT_VERSION

    def __init__(
        self,
        resource: str,
        *,
        mode: str = "hardware",
        timeout_ms: int = 30000,
        model: str = "C-887",
        axes: Iterable[str] = AXES,
        limits: Mapping[str, Mapping[str, float]] | None = None,
    ) -> None:
        if mode not in ("hardware", "simulate"):
            raise ValueError(f"未知 mode: {mode}")
        self.resource = resource
        self.mode = mode
        self.timeout_ms = int(timeout_ms)
        self.model = model
        self.axes = tuple(str(a).upper() for a in axes)
        for axis in self.axes:
            if axis not in AXES:
                raise MotionUnsupported(f"未知轴 {axis!r}；可用 {AXES}")
        self._limits_override = limits

        self._dev = None
        self._sim: CapabilitySimulator | None = None
        self._identity: MotionIdentity | None = None
        self._referenced = False
        self._limits: MotionLimits | None = None
        self._position = {axis: 0.0 for axis in self.axes}
        self._moves = 0

    # -- 生命周期 -------------------------------------------------------
    def connect(self) -> None:
        if self._dev is not None or self._sim is not None:
            return
        if self.mode == "simulate":
            sim = CapabilitySimulator(self.resource, limits=self._limits_from_override())
            sim.open()
            self._sim = sim
            self._identity = sim.identify()
            self._limits = sim.limits
            self._position = sim.query_position().as_dict()
            return

        dev = self._open()
        self._dev = dev
        try:
            self._identity = self._read_identity()
            self._limits = self._read_limits()
            self._position = self._read_position()
        except MotionError:
            self._dev = None
            raise
        except Exception as exc:
            self._dev = None
            raise DriverUnavailable(f"控制器初始化失败: {exc}") from exc

    def _open(self):
        try:
            from pipython import GCSDevice  # 厂商库：仅本包内出现
        except Exception as exc:
            raise DriverUnavailable(
                "缺少 pipython 厂商库；离线部署请使用包内 vendor 目录，"
                "或在台架装备属性中显式声明 mode=simulate"
            ) from exc
        try:
            endpoint = gcs.parse_resource(self.resource)
        except ValueError as exc:
            raise MotionUnsupported(str(exc)) from exc
        dev = GCSDevice(self.model)
        try:
            gcs.connect(dev, endpoint)
        except Exception as exc:
            try:
                dev.CloseConnection()
            except Exception:
                pass
            hint = ""
            if "GCS2" in str(exc) or "dll" in str(exc).lower():
                hint = "；控制器需要 PI GCS2 DLL，请确认已安装 PI 软件或随包 vendor 目录就位"
            raise DriverUnavailable(f"无法连接 {self.resource}: {exc}{hint}") from exc
        return dev

    def close(self) -> None:
        if self._sim is not None:
            self._sim.close()
            self._sim = None
        if self._dev is not None:
            try:
                self._dev.CloseConnection()
            finally:
                self._dev = None
        self._identity = None
        self._referenced = False

    def identify(self) -> MotionIdentity:
        self._require()
        assert self._identity is not None
        return self._identity

    def _read_identity(self) -> MotionIdentity:
        try:
            raw = self._dev.qIDN()
        except Exception as exc:
            raise MotionTimeout(f"qIDN 无响应: {exc}") from exc
        text = ", ".join(str(v) for v in (raw.values() if isinstance(raw, dict) else [raw]))
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) < 2:
            raise MotionDataError(f"无法解析 qIDN: {text!r}")
        vendor = parts[0]
        model = parts[1] if len(parts) > 1 else self.model
        serial = parts[2] if len(parts) > 2 else str(self.resource)
        firmware = parts[3] if len(parts) > 3 else "unknown"
        return MotionIdentity(
            vendor=vendor, model=model, serial=serial, firmware=firmware, resource=self.resource, axes=self.axes
        )

    # -- 参考 -----------------------------------------------------------
    def reference(self, *, timeout_s: float = 60.0) -> None:
        self._require()
        if self._sim is not None:
            self._sim.reference(timeout_s=timeout_s)
            self._referenced = True
            return
        try:
            for axis in self.axes:
                self._dev.SVO(axis, True)
            # FRF 为 C-887 的快速参考；若固件不支持再退回 GOH（见 gcs.VERIFY）
            for axis in self.axes:
                try:
                    self._dev.FRF(axis)
                except Exception:
                    self._dev.GOH(axis)
        except MotionError:
            raise
        except Exception as exc:
            raise MotionTimeout(f"回零失败: {exc}") from exc
        self._wait(timeout_s, what="回零")
        self._referenced = True

    # -- 运动 -----------------------------------------------------------
    def move_to(
        self, target: Mapping[str, float], *, wait: bool = True, timeout_s: float = 30.0
    ) -> Position:
        self._require()
        self._check_referenced()
        clean = self._clean(target)
        if self._limits is not None:
            self._limits.check(clean)
        if self._sim is not None:
            pos = self._sim.move_to(clean, wait=wait, timeout_s=timeout_s)
            self._position = pos.as_dict()
            self._moves += 1
            return pos
        try:
            self._dev.MOV(clean)
        except Exception as exc:
            raise MotionLimitError(f"控制器拒绝目标 {clean}: {exc}") from exc
        if wait:
            self._wait(timeout_s, what=f"移动到 {clean}")
        self._moves += 1
        self._position = self._read_position()
        return Position(dict(self._position))

    def move_relative(
        self, delta: Mapping[str, float], *, wait: bool = True, timeout_s: float = 30.0
    ) -> Position:
        self._require()
        self._check_referenced()
        clean = self._clean(delta)
        if self._sim is not None:
            pos = self._sim.move_relative(clean, wait=wait, timeout_s=timeout_s)
            self._position = pos.as_dict()
            self._moves += 1
            return pos
        if self._limits is not None:
            target = {axis: self._position.get(axis, 0.0) + clean[axis] for axis in clean}
            self._limits.check(target)
        try:
            self._dev.MVR(clean)
        except Exception as exc:
            raise MotionLimitError(f"控制器拒绝相对移动 {clean}: {exc}") from exc
        if wait:
            self._wait(timeout_s, what=f"相对移动到 {clean}")
        self._moves += 1
        self._position = self._read_position()
        return Position(dict(self._position))

    def query_position(self) -> Position:
        self._require()
        if self._sim is not None:
            return self._sim.query_position()
        self._position = self._read_position()
        return Position(dict(self._position))

    def stop(self, axes: Iterable[str] | None = None) -> None:
        self._require()
        names = normalize_axes(axes)
        if self._sim is not None:
            self._sim.stop(names)
            return
        try:
            self._dev.HLT(names[0] if len(names) == 1 else ",".join(names))
        except Exception as exc:
            raise MotionTimeout(f"停止失败: {exc}") from exc

    # -- 诊断 -----------------------------------------------------------
    def describe(self) -> Mapping[str, object]:
        base = {
            "capability": self.capability,
            "contract": self.contract_version,
            "package": "pi-hexapod",
            "package_version": __version__,
            "resource": self.resource,
            "mode": self.mode,
            "connected": self._dev is not None or self._sim is not None,
            "simulation": {"layer": SIM_LAYER, "active": self._sim is not None},
            "identity": None
            if self._identity is None
            else {
                "vendor": self._identity.vendor,
                "model": self._identity.model,
                "serial": self._identity.serial,
                "firmware": self._identity.firmware,
            },
            "referenced": self._referenced,
            "position": dict(self._position),
            "limits": None
            if self._limits is None
            else {"lower": dict(self._limits.lower), "upper": dict(self._limits.upper)},
            "moves": self._moves,
        }
        if self._sim is not None:
            base["simulation_state"] = self._sim.describe()
        return base

    # -- 内部 -----------------------------------------------------------
    def _read_position(self) -> dict[str, float]:
        try:
            raw = self._dev.qPOS()
        except Exception as exc:
            raise MotionTimeout(f"qPOS 无响应: {exc}") from exc
        values = gcs.to_contract_units(raw, axis_is_rotary=True)
        missing = [a for a in self.axes if a not in values]
        if missing:
            raise MotionDataError(f"qPOS 缺轴 {missing}；返回 {sorted(values)}")
        return {a: values[a] for a in self.axes}

    def _read_limits(self) -> MotionLimits:
        override = self._limits_from_override()
        if override is not None:
            return override
        lower, upper = gcs.read_limits(self._dev, self.axes)
        if len(lower) != len(self.axes):
            # 部分固件不返回六足空间轴行程；此时不做软件夹紧，交给控制器拒绝
            return MotionLimits(
                lower={a: float("-inf") for a in self.axes}, upper={a: float("inf") for a in self.axes}
            )
        return MotionLimits(lower=lower, upper=upper)

    def _limits_from_override(self) -> MotionLimits | None:
        if not self._limits_override:
            return None
        raw = {k: dict(v) for k, v in self._limits_override.items()}
        if "lower" not in raw or "upper" not in raw:
            raise MotionUnsupported("limits 需形如 {lower: {...}, upper: {...}}")
        lower = {str(k).upper(): float(v) for k, v in raw["lower"].items()}
        upper = {str(k).upper(): float(v) for k, v in raw["upper"].items()}
        return MotionLimits(lower=lower, upper=upper)

    def _wait(self, timeout_s: float, *, what: str) -> None:
        try:
            from pipython import pitools

            pitools.waitontarget(self._dev, timeout=timeout_s)
        except ImportError as exc:  # 理论上不会发生：pipython 已 import 成功
            raise DriverUnavailable(str(exc)) from exc
        except Exception as exc:
            raise MotionTimeout(f"{what} 超时（{timeout_s}s）: {exc}") from exc

    def _clean(self, target: Mapping[str, float]) -> dict[str, float]:
        if not target:
            raise MotionUnsupported("目标为空")
        out = {}
        for axis, value in target.items():
            name = str(axis).strip().upper()
            if name not in self.axes:
                raise MotionUnsupported(f"未知轴 {axis!r}；本台可用 {self.axes}")
            try:
                out[name] = float(value)
            except (TypeError, ValueError) as exc:
                raise MotionUnsupported(f"轴 {axis} 的目标不是数值: {value!r}") from exc
        return out

    def _check_referenced(self) -> None:
        if not self._referenced:
            raise MotionUnsupported("平台未回零，拒绝运动；先调用 reference()")

    def _require(self) -> None:
        if self._dev is None and self._sim is None:
            raise MotionNotConnected("会话未建立或已关闭")
