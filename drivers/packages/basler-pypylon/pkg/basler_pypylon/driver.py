"""Basler 相机驱动包（基于 pypylon）。

分层位置
--------
L1 包层：把 pypylon 的 node 语义翻译成 contracts.camera 的语义。
- 厂商库 `pypylon` 延迟 import，只出现在本文件与 api.py。
- 仿真走能力层（simulate.py），不建会话、不碰 pypylon。
- 不出现设备档案解析、文件路径、用例逻辑。

连接策略
--------
mode="hardware"：必须能枚举到相机并成功 Open()，否则抛 DriverUnavailable，不静默降级。
mode="simulate"：显式声明才启用；用于 CI、无硬件联调与培训，不替代现场验收。
"""

from __future__ import annotations

from typing import Mapping

from contracts.camera import (
    CAPABILITY,
    CONTRACT_VERSION,
    STATISTICS_ITEMS,
    CameraDataError,
    CameraDriver,
    CameraError,
    CameraIdentity,
    CameraNotConnected,
    CameraTimeout,
    CameraUnsupported,
    ExposureSpec,
    Frame,
    RoiSpec,
    compute_statistics,
)

from . import api
from .simulate import SIM_LAYER, CapabilitySimulator

__version__ = "1.0.0"


class DriverUnavailable(CameraError):
    """运行环境不满足：缺 pypylon、枚举不到相机、Open 失败。"""


class BaslerCamera(CameraDriver):
    capability = CAPABILITY
    contract_version = CONTRACT_VERSION

    def __init__(
        self,
        resource: str,
        *,
        mode: str = "hardware",
        timeout_ms: int = 5000,
        pixel_format: str = "Mono8",
        width: int = 320,
        height: int = 240,
        max_width: int = 320,
        max_height: int = 240,
    ) -> None:
        if mode not in ("hardware", "simulate"):
            raise ValueError(f"未知 mode: {mode}")
        self.resource = resource
        self.mode = mode
        self.timeout_ms = int(timeout_ms)
        self.pixel_format = pixel_format
        self.width = int(width)
        self.height = int(height)
        self.max_width = int(max_width)
        self.max_height = int(max_height)

        self._cam = None
        self._sim: CapabilitySimulator | None = None
        self._identity: CameraIdentity | None = None
        self._exposure = ExposureSpec()
        self._roi = RoiSpec(width=self.width, height=self.height)
        self._trigger = {"mode": "free_run", "source": "software"}
        self._frames = 0

    # -- 生命周期 -------------------------------------------------------
    def connect(self) -> None:
        if self._cam is not None or self._sim is not None:
            return
        if self.mode == "simulate":
            sim = CapabilitySimulator(
                self.resource,
                max_width=self.max_width,
                max_height=self.max_height,
                pixel_format=self.pixel_format,
            )
            sim.open()
            sim.configure_exposure(self._exposure)
            sim.configure_roi(self._roi)
            self._sim = sim
            self._identity = sim.identify()
            return

        cam = self._open()
        self._cam = cam
        try:
            api.set_enum(cam, "PixelFormat", self.pixel_format)
            api.set_enum(cam, "ExposureAuto", "Off")
            api.set_enum(cam, "GainAuto", "Off")
            if not api.set_float(cam, "ExposureTime", self._exposure.exposure_us):
                raise CameraUnsupported("相机不支持 ExposureTime 节点")
            api.apply_roi(cam, self.width, self.height)
            self._identity = self._read_identity()
        except CameraError:
            self._cam = None
            raise
        except Exception as exc:
            self._cam = None
            raise DriverUnavailable(f"相机初始化失败: {exc}") from exc

    def _open(self):
        try:
            from pypylon import pylon  # 厂商库：仅本包内出现
        except Exception as exc:
            raise DriverUnavailable(
                "缺少 pypylon 厂商库；离线部署请使用包内 vendor 目录，"
                "或在台架装备属性中显式声明 mode=simulate"
            ) from exc
        try:
            factory = pylon.TlFactory.GetInstance()
            descriptor = api.select_descriptor(factory.EnumerateDevices(), self.resource)
            return api.open_camera(pylon, factory, descriptor)
        except DriverUnavailable:
            raise
        except Exception as exc:
            raise DriverUnavailable(f"无法打开相机 {self.resource}: {exc}") from exc

    def close(self) -> None:
        if self._sim is not None:
            self._sim.close()
            self._sim = None
        if self._cam is not None:
            try:
                if self._cam.IsGrabbing():
                    self._cam.StopGrabbing()
                self._cam.Close()
            finally:
                self._cam = None
        self._identity = None

    def identify(self) -> CameraIdentity:
        self._require()
        assert self._identity is not None
        return self._identity

    def _read_identity(self) -> CameraIdentity:
        cam = self._cam
        def node(name: str, attr: str) -> str:
            try:
                return str(getattr(cam.GetNodeMap().GetNode(name), attr)())
            except Exception:
                return ""
        model = node("DeviceModelName", "GetValue")
        firmware = node("DeviceFirmwareVersion", "GetValue") or node("DeviceVersion", "GetValue")
        serial = node("DeviceSerialNumber", "GetValue") or str(self.resource)
        if not model:
            raise CameraDataError("无法读取 DeviceModelName，无法确认设备身份")
        return CameraIdentity(
            vendor="Basler",
            model=model,
            serial=serial,
            firmware=firmware or "unknown",
            resource=self.resource,
        )

    # -- 配置 -----------------------------------------------------------
    def configure_exposure(self, spec: ExposureSpec) -> None:
        self._require()
        if spec.exposure_us <= 0:
            raise CameraUnsupported(f"曝光时间必须为正: {spec.exposure_us}")
        if self._sim is not None:
            self._sim.configure_exposure(spec)
            self._exposure = spec
            return
        rng = api.node_range(self._cam, "ExposureTime")
        value = float(spec.exposure_us)
        if rng is not None:
            lo, hi = rng
            if value < lo or value > hi:
                raise CameraUnsupported(
                    f"曝光 {value}us 超出相机范围 [{lo:.1f}, {hi:.1f}]us"
                )
        if not api.set_float(self._cam, "ExposureTime", value):
            raise CameraUnsupported("写入 ExposureTime 失败")
        if spec.gain_db:
            api.set_float(self._cam, "Gain", spec.gain_db)
        self._exposure = ExposureSpec(value, float(spec.gain_db), bool(spec.auto))

    def configure_roi(self, roi: RoiSpec) -> None:
        self._require()
        if self._sim is not None:
            self._sim.configure_roi(roi)
            self._roi = roi
            return
        w = roi.width or self.width
        h = roi.height or self.height
        cfg_w, cfg_h = api.apply_roi(self._cam, w, h, roi.offset_x, roi.offset_y)
        self._roi = RoiSpec(roi.offset_x, roi.offset_y, cfg_w, cfg_h)

    def configure_trigger(self, *, mode: str = "free_run", source: str = "software") -> None:
        self._require()
        if mode not in ("free_run", "software", "line1"):
            raise CameraUnsupported(f"未知触发模式: {mode}")
        if self._sim is not None:
            self._sim.configure_trigger(mode=mode, source=source)
            self._trigger = {"mode": mode, "source": source}
            return
        if mode == "free_run":
            api.set_enum(self._cam, "TriggerMode", "Off")
        else:
            api.set_enum(self._cam, "TriggerMode", "On")
            api.set_enum(self._cam, "TriggerSource", "Software" if mode == "software" else "Line1")
        self._trigger = {"mode": mode, "source": source}

    # -- 取图 -----------------------------------------------------------
    def grab(self, n_frames: int = 1, *, timeout_ms: int | None = None) -> tuple[Frame, ...]:
        self._require()
        if n_frames < 1:
            raise CameraUnsupported(f"n_frames 必须 ≥1: {n_frames}")
        if self._sim is not None:
            frames = self._sim.grab(n_frames, timeout_ms=timeout_ms)
            self._frames += len(frames)
            return frames

        timeout = int(timeout_ms or self.timeout_ms)
        cam = self._cam
        out: list[Frame] = []
        try:
            cam.StartGrabbing(pylon_strategy(cam))
            for _ in range(n_frames):
                result = cam.RetrieveResult(timeout, timeout_handling(cam))
                if not result.GrabSucceeded():
                    raise CameraTimeout(f"取图失败: {result.GetErrorCode()} {result.GetErrorDescription()}")
                arr = result.GetArray()
                data = arr.tobytes() if hasattr(arr, "tobytes") else bytes(arr)
                w = int(result.GetWidth())
                h = int(result.GetHeight())
                if len(data) < w * h:
                    raise CameraDataError(f"帧数据长度异常: {len(data)} < {w}x{h}")
                self._frames += 1
                out.append(
                    Frame(
                        width=w,
                        height=h,
                        pixel_format=self.pixel_format,
                        data=data[: w * h],
                        frame_id=self._frames,
                        timestamp_ns=int(getattr(result, "GetTimeStamp", lambda: 0)() or 0),
                        exposure_us=self._exposure.exposure_us,
                        gain_db=self._exposure.gain_db,
                    )
                )
                result.Release()
        except CameraError:
            raise
        except Exception as exc:
            raise CameraTimeout(f"取图异常: {exc}") from exc
        finally:
            try:
                if cam.IsGrabbing():
                    cam.StopGrabbing()
            except Exception:
                pass
        return tuple(out)

    # -- 统计 -----------------------------------------------------------
    def statistics(self, frame: Frame, item: str) -> float:
        if item not in STATISTICS_ITEMS:
            raise CameraUnsupported(f"未知统计项 {item}；可用 {list(STATISTICS_ITEMS)}")
        if frame.width * frame.height != len(frame.data):
            raise CameraDataError(
                f"帧尺寸 {frame.width}x{frame.height} 与数据长度 {len(frame.data)} 不符"
            )
        stats = compute_statistics(frame.samples(), width=frame.width)
        return float(stats[item])

    # -- 诊断 -----------------------------------------------------------
    def describe(self) -> Mapping[str, object]:
        base = {
            "capability": self.capability,
            "contract": self.contract_version,
            "package": "basler-pypylon",
            "package_version": __version__,
            "resource": self.resource,
            "mode": self.mode,
            "connected": self._cam is not None or self._sim is not None,
            "simulation": {"layer": SIM_LAYER, "active": self._sim is not None},
            "identity": None
            if self._identity is None
            else {
                "vendor": self._identity.vendor,
                "model": self._identity.model,
                "serial": self._identity.serial,
                "firmware": self._identity.firmware,
            },
            "roi": {
                "offset_x": self._roi.offset_x,
                "offset_y": self._roi.offset_y,
                "width": self._roi.width,
                "height": self._roi.height,
            },
            "exposure": {
                "exposure_us": self._exposure.exposure_us,
                "gain_db": self._exposure.gain_db,
                "auto": self._exposure.auto,
            },
            "trigger": dict(self._trigger),
            "frames": self._frames,
        }
        if self._sim is not None:
            base["simulation_state"] = self._sim.describe()
        return base

    # -- 内部 -----------------------------------------------------------
    def _require(self) -> None:
        if self._cam is None and self._sim is None:
            raise CameraNotConnected("会话未建立或已关闭")


def pylon_strategy(cam):
    """取 LatestImageOnly 策略；老版本 pypylon 无此常量时退回默认。"""
    from pypylon import pylon

    return getattr(pylon, "GrabStrategy_LatestImageOnly", None)


def timeout_handling(cam):
    from pypylon import pylon

    return getattr(pylon, "TimeoutHandling_ThrowException", None)
