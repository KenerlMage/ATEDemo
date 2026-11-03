"""数据模型层：只描述状态与数据，不依赖任何 Qt 组件。

MVP 分工：
    Model  —— 相机状态、参数、帧数据、检测结果（本文件）
    View   —— 控件装配与事件转发（views/）
    Presenter —— 事件响应、状态编排（presenters.py）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Optional, Sequence

import numpy as np

# ---------------------------------------------------------------- 相机参数

PIXEL_FORMATS = ("Mono8", "Mono12", "Mono12Packed")
TRIGGER_MODES = ("continuous", "software")


@dataclass(frozen=True)
class CameraParams:
    """相机可调参数。曝光单位微秒，增益单位 dB（Basler 内部为 raw，驱动负责换算）。"""

    exposure_us: float = 8000.0
    gain_db: float = 0.0
    auto_exposure: bool = False
    frame_rate: float = 20.0
    pixel_format: str = "Mono8"
    offset_x: int = 0
    offset_y: int = 0
    width: int = 640
    height: int = 480
    trigger_mode: str = "continuous"

    def clamped(self, max_w: int = 4096, max_h: int = 3072) -> "CameraParams":
        """把越界参数收进合法范围，避免直接把非法值下发到相机。"""
        w = int(max(32, min(int(self.width), max_w)))
        h = int(max(32, min(int(self.height), max_h)))
        ox = int(max(0, min(int(self.offset_x), max_w - w)))
        oy = int(max(0, min(int(self.offset_y), max_h - h)))
        return replace(
            self,
            exposure_us=float(np.clip(self.exposure_us, 20.0, 1_000_000.0)),
            gain_db=float(np.clip(self.gain_db, 0.0, 48.0)),
            frame_rate=float(np.clip(self.frame_rate, 0.5, 200.0)),
            pixel_format=self.pixel_format if self.pixel_format in PIXEL_FORMATS else "Mono8",
            trigger_mode=self.trigger_mode if self.trigger_mode in TRIGGER_MODES else "continuous",
            width=w,
            height=h,
            offset_x=ox,
            offset_y=oy,
        )


@dataclass
class Frame:
    """一帧灰度图。array 为 uint8，形状 (H, W)。"""

    index: int
    timestamp: float
    array: np.ndarray

    @property
    def width(self) -> int:
        return int(self.array.shape[1])

    @property
    def height(self) -> int:
        return int(self.array.shape[0])

    def describe(self) -> str:
        return f"#{self.index} {self.width}×{self.height} @ {self.timestamp:.3f}"


@dataclass
class CameraStatus:
    connected: bool = False
    grabbing: bool = False
    simulated: bool = False
    model: str = "-"
    serial: str = "-"
    frames: int = 0
    fps: float = 0.0
    last_error: str = ""

    def label(self) -> str:
        if not self.connected:
            return "未连接"
        tag = "仿真" if self.simulated else "真机"
        return f"{tag} · {self.model} · {self.fps:0.1f} fps · {self.frames} 帧"


class CameraModel:
    """相机状态机：连接、参数、采集开关、最近一帧。

    采集由外部 CameraWorker 线程写入，本类只做线程安全的引用交换（不复制图像）。
    """

    def __init__(self, params: Optional[CameraParams] = None) -> None:
        self._params = (params or CameraParams()).clamped()
        self._status = CameraStatus()
        self._latest: Optional[Frame] = None
        self._observers: list = []

    # ---- 参数
    @property
    def params(self) -> CameraParams:
        return self._params

    def update_params(self, **changes) -> CameraParams:
        merged = replace(self._params, **{k: v for k, v in changes.items() if hasattr(self._params, k)})
        self._params = merged.clamped()
        return self._params

    # ---- 状态
    @property
    def status(self) -> CameraStatus:
        return self._status

    def set_connected(self, *, connected: bool, simulated: bool = False,
                      model: str = "-", serial: str = "-") -> None:
        self._status.connected = connected
        self._status.simulated = simulated
        self._status.model = model
        self._status.serial = serial
        if not connected:
            self._status.grabbing = False
            self._status.fps = 0.0
        self._notify()

    def set_grabbing(self, grabbing: bool) -> None:
        self._status.grabbing = grabbing
        self._notify()

    def set_error(self, message: str) -> None:
        self._status.last_error = message
        self._notify()

    # ---- 帧
    @property
    def latest(self) -> Optional[Frame]:
        return self._latest

    def push_frame(self, frame: Frame) -> None:
        self._latest = frame
        self._status.frames = frame.index
        self._notify()

    def set_fps(self, fps: float) -> None:
        self._status.fps = float(fps)

    # ---- 观察者（View 刷新用，避免 Presenter 直接依赖 Qt 定时器）
    def subscribe(self, callback) -> None:
        if callback not in self._observers:
            self._observers.append(callback)

    def _notify(self) -> None:
        for cb in list(self._observers):
            try:
                cb(self)
            except Exception:      # 观察者异常不得影响采集
                pass


# ---------------------------------------------------------------- 检测参数


@dataclass(frozen=True)
class AnalyzeParams:
    """光斑检测参数。ROI 为 (x, y, w, h)，以整幅图像左上角为原点。"""

    threshold_mode: str = "otsu"        # otsu | absolute | relative
    threshold_value: float = 60.0       # absolute 用；relative 表示相对峰值比例
    min_area: int = 12
    max_blobs: int = 32
    invert: bool = False
    bg_percentile: float = 20.0
    saturate_level: int = 250
    roi: tuple = (0, 0, 0, 0)           # 宽或高为 0 表示全幅

    def roi_or_full(self, width: int, height: int) -> tuple:
        x, y, w, h = self.roi
        if w <= 0 or h <= 0:
            return (0, 0, width, height)
        x = int(max(0, min(x, width - 1)))
        y = int(max(0, min(y, height - 1)))
        w = int(max(1, min(w, width - x)))
        h = int(max(1, min(h, height - y)))
        return (x, y, w, h)


@dataclass
class Blob:
    """单个光斑的测量结果。坐标为整幅图像坐标系，已加回 ROI 偏移。"""

    index: int
    cx: float              # 亮度中心（灰度加权质心）
    cy: float
    gx: float              # 光斑中心（二值几何质心）
    gy: float
    area: int
    eq_diameter: float     # 等效圆直径 2*sqrt(A/pi)
    bbox: tuple            # (x, y, w, h)
    peak: int
    mean: float
    saturated: bool

    def as_row(self) -> list:
        return [
            self.index,
            f"{self.cx:.2f}",
            f"{self.cy:.2f}",
            f"{self.gx:.2f}",
            f"{self.gy:.2f}",
            f"{self.eq_diameter:.1f}",
            self.area,
            self.peak,
            "是" if self.saturated else "",
        ]


@dataclass
class AnalyzeResult:
    blobs: list = field(default_factory=list)
    threshold: float = 0.0
    mask: Optional[np.ndarray] = None
    roi: tuple = (0, 0, 0, 0)
    elapsed_ms: float = 0.0
    warn: str = ""
    frame_index: int = -1

    @property
    def count(self) -> int:
        return len(self.blobs)

    def summary(self) -> str:
        base = f"光斑 {self.count} 个 · 阈值 {self.threshold:.0f} · {self.elapsed_ms:.1f} ms"
        return base + (f" · {self.warn}" if self.warn else "")


def now() -> float:
    return time.time()


def rows_of(blobs: Sequence[Blob]) -> list:
    return [b.as_row() for b in blobs]
