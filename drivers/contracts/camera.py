"""相机能力契约（capability = camera，contract 1.0）。

位置
----
L3 契约层：用例只依赖本模块，不知道背后是 Basler、海康还是别的品牌。

设计要点
--------
- 相机不进「仪器会话」的同一套 Transport：它是长连接、高带宽、按帧取图，
  且一台柜内只有一台、不需要通道仲裁，因此用独立的 CameraDriver 契约。
- 只承诺「拿到一帧 + 能算统计量」，不承诺 ISP 色彩管线、不做图像算法。
- 统计量口径由契约定义（compute_statistics），换品牌不能换口径。
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping, Sequence

CAPABILITY = "camera"
CONTRACT_VERSION = "1.0"

#: 契约定义的统计项；包必须全部实现，缺一项即一致性不通过。
STATISTICS_ITEMS = (
    "mean_gray",
    "saturation_ratio",
    "snr_db",
    "sharpness",
    "uniformity",
    "defect_ratio",
)

PIXEL_FORMATS = ("Mono8", "Mono12", "RGB8")


@dataclass(frozen=True)
class Frame:
    """一帧图像的契约表示。data 为按行存储的原始字节，不做色彩转换。"""

    width: int
    height: int
    pixel_format: str
    data: bytes
    frame_id: int = 0
    timestamp_ns: int = 0
    exposure_us: float = 0.0
    gain_db: float = 0.0

    @property
    def pixels(self) -> int:
        return self.width * self.height

    @property
    def nbytes(self) -> int:
        return len(self.data)

    def samples(self) -> Sequence[int]:
        """mono8 逐像素取值；其它格式拒绝，避免口径漂移。"""
        if self.pixel_format != "Mono8":
            raise CameraUnsupported(f"统计只支持 Mono8，当前 {self.pixel_format}")
        return self.data


@dataclass(frozen=True)
class CameraIdentity:
    vendor: str
    model: str
    serial: str
    firmware: str
    resource: str


@dataclass(frozen=True)
class RoiSpec:
    offset_x: int = 0
    offset_y: int = 0
    width: int = 0  # 0 表示用满幅
    height: int = 0


@dataclass(frozen=True)
class ExposureSpec:
    exposure_us: float = 10000.0
    gain_db: float = 0.0
    auto: bool = False


class CameraError(Exception):
    """相机错误的基类：用例只需捕获它即可分类处理。"""


class CameraNotConnected(CameraError):
    pass


class CameraUnsupported(CameraError):
    pass


class CameraTimeout(CameraError):
    pass


class CameraDataError(CameraError):
    pass


def compute_statistics(data: Sequence[int], *, width: int = 0) -> dict[str, float]:
    """按契约口径计算统计量。所有品牌的包都必须调用这里，保证口径一致。

    - mean_gray        全图均值（0–255）
    - saturation_ratio 灰度 ≥ 250 的像素占比
    - snr_db           20·log10(均值 / 标准差)，标准差为 0 时记 100 dB
    - sharpness        相邻像素平均绝对差（越大越锐）
    - uniformity       1 − 标准差/均值，衡量照明均匀性
    - defect_ratio     灰度 0 或 255 的像素占比（坏点、过曝钉）
    """
    n = len(data)
    if n == 0:
        raise CameraDataError("空帧，无法计算统计量")
    total = 0
    hist = [0] * 256
    for v in data:
        total += v
        hist[v] += 1
    mean = total / n
    var = sum((v - mean) ** 2 for v in data) / n
    std = math.sqrt(var)

    grad = 0
    pairs = 0
    if width and width < n:
        for i in range(1, n):
            if i % width == 0:
                continue
            grad += abs(data[i] - data[i - 1])
            pairs += 1
    else:
        for i in range(1, n):
            grad += abs(data[i] - data[i - 1])
            pairs += 1

    return {
        "mean_gray": mean,
        "saturation_ratio": sum(hist[250:]) / n,
        "snr_db": 100.0 if std == 0 else 20.0 * math.log10(mean / std) if mean > 0 else 0.0,
        "sharpness": (grad / pairs) if pairs else 0.0,
        "uniformity": 1.0 - (std / mean) if mean > 0 else 0.0,
        "defect_ratio": (hist[0] + hist[255]) / n,
    }


class CameraDriver(ABC):
    """相机包必须实现的接口。实现方负责把厂商 SDK 翻译成这些语义。"""

    capability = CAPABILITY
    contract_version = CONTRACT_VERSION

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def identify(self) -> CameraIdentity: ...

    @abstractmethod
    def configure_exposure(self, spec: ExposureSpec) -> None: ...

    @abstractmethod
    def configure_roi(self, roi: RoiSpec) -> None: ...

    @abstractmethod
    def configure_trigger(self, *, mode: str = "free_run", source: str = "software") -> None: ...

    @abstractmethod
    def grab(self, n_frames: int = 1, *, timeout_ms: int | None = None) -> tuple[Frame, ...]: ...

    @abstractmethod
    def statistics(self, frame: Frame, item: str) -> float: ...

    @abstractmethod
    def describe(self) -> Mapping[str, object]: ...


__all__ = [
    "CAPABILITY",
    "CONTRACT_VERSION",
    "PIXEL_FORMATS",
    "STATISTICS_ITEMS",
    "CameraDataError",
    "CameraDriver",
    "CameraError",
    "CameraIdentity",
    "CameraNotConnected",
    "CameraTimeout",
    "CameraUnsupported",
    "ExposureSpec",
    "Frame",
    "RoiSpec",
    "compute_statistics",
]
