"""能力层仿真：不建会话、不下发命令、不碰厂商 SDK。

仿真器接收契约参数（曝光、增益、ROI），直接产出契约结构（Frame + 统计量）。
图像是确定性模型：亮度随曝光与增益单调变化，含一条竖直亮条用于锐度，
高曝光时中心出现饱和斑 —— 用来断言「参数变化 → 统计量按预期方向变化」。
"""

from __future__ import annotations

from typing import Mapping

from contracts.camera import (
    CameraIdentity,
    CameraUnsupported,
    ExposureSpec,
    Frame,
    RoiSpec,
    compute_statistics,
)

SIM_LAYER = "capability"


class CapabilitySimulator:
    capability = "camera"
    contract_version = "1.0"

    def __init__(
        self,
        resource: str,
        *,
        max_width: int = 320,
        max_height: int = 240,
        pixel_format: str = "Mono8",
    ) -> None:
        self.resource = resource
        self.max_width = int(max_width)
        self.max_height = int(max_height)
        self.pixel_format = pixel_format
        self._open = False
        self._exposure = ExposureSpec()
        self._roi = RoiSpec(width=self.max_width, height=self.max_height)
        self._trigger = {"mode": "free_run", "source": "software"}
        self._frame_id = 0

    # -- 生命周期 -------------------------------------------------------
    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def connected(self) -> bool:
        return self._open

    def identify(self) -> CameraIdentity:
        if not self._open:
            raise CameraUnsupported("仿真器未打开")
        return CameraIdentity(
            vendor="Basler",
            model="acA1920-40gm",
            serial=_serial_from(self.resource),
            firmware="sim-1.0.0",
            resource=self.resource,
        )

    # -- 配置 -----------------------------------------------------------
    def configure_exposure(self, spec: ExposureSpec) -> None:
        if spec.exposure_us <= 0:
            raise CameraUnsupported(f"曝光时间必须为正: {spec.exposure_us}")
        if spec.exposure_us > 1_000_000:
            raise CameraUnsupported(f"曝光超出相机能力: {spec.exposure_us}us")
        self._exposure = ExposureSpec(
            exposure_us=float(spec.exposure_us), gain_db=float(spec.gain_db), auto=bool(spec.auto)
        )

    def configure_roi(self, roi: RoiSpec) -> None:
        w = roi.width or self.max_width
        h = roi.height or self.max_height
        if roi.offset_x < 0 or roi.offset_y < 0 or w <= 0 or h <= 0:
            raise CameraUnsupported(f"非法 ROI: {roi}")
        if roi.offset_x + w > self.max_width or roi.offset_y + h > self.max_height:
            raise CameraUnsupported(f"ROI 超出传感器 {self.max_width}x{self.max_height}: {roi}")
        self._roi = RoiSpec(roi.offset_x, roi.offset_y, w, h)

    def configure_trigger(self, *, mode: str = "free_run", source: str = "software") -> None:
        if mode not in ("free_run", "software", "line1"):
            raise CameraUnsupported(f"未知触发模式: {mode}")
        self._trigger = {"mode": mode, "source": source}

    # -- 取图 -----------------------------------------------------------
    def grab(self, n_frames: int = 1, *, timeout_ms: int | None = None) -> tuple[Frame, ...]:
        if not self._open:
            raise CameraUnsupported("仿真器未打开")
        if n_frames < 1:
            raise CameraUnsupported(f"n_frames 必须 ≥1: {n_frames}")
        out = []
        for _ in range(n_frames):
            self._frame_id += 1
            out.append(self._frame())
        return tuple(out)

    def _frame(self) -> Frame:
        w, h = self._roi.width, self._roi.height
        base = self._level()
        bar_x0 = (self._roi.offset_x + w // 3) % max(w, 1)
        bar_x1 = bar_x0 + max(w // 10, 1)
        sat_r = max(min(w, h) // 8, 0) if self._exposure.exposure_us > 60_000 else 0
        cx, cy = w // 2, h // 2
        buf = bytearray(w * h)
        idx = 0
        for y in range(h):
            for x in range(w):
                v = int(base)
                if bar_x0 <= x < bar_x1:
                    v += 45
                if sat_r and (x - cx) ** 2 + (y - cy) ** 2 <= sat_r * sat_r:
                    v = 255
                buf[idx] = 255 if v > 255 else (0 if v < 0 else int(v))
                idx += 1
        return Frame(
            width=w,
            height=h,
            pixel_format=self.pixel_format,
            data=bytes(buf),
            frame_id=self._frame_id,
            timestamp_ns=self._frame_id * 33_000_000,
            exposure_us=self._exposure.exposure_us,
            gain_db=self._exposure.gain_db,
        )

    def _level(self) -> float:
        """亮度模型：随曝光与增益单调上升，上限压到 250 以下（饱和由中心斑模拟）。"""
        return min(20.0 + self._exposure.exposure_us * 0.0045 + self._exposure.gain_db * 5.0, 240.0)

    # -- 统计 -----------------------------------------------------------
    def statistics(self, frame: Frame, item: str) -> float:
        stats = compute_statistics(frame.samples(), width=frame.width)
        if item not in stats:
            raise CameraUnsupported(f"未知统计项 {item}；可用 {sorted(stats)}")
        return float(stats[item])

    def describe(self) -> Mapping[str, object]:
        return {
            "mode": "simulate",
            "simulation_layer": SIM_LAYER,
            "connected": self._open,
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
            "frames": self._frame_id,
        }


def _serial_from(resource: str) -> str:
    text = str(resource)
    if text.startswith("serial:"):
        return text.split(":", 1)[1] or "SIM0"
    if "::" in text:
        return text.rsplit("::", 2)[-2] or "SIM0"
    return "SIM0"
