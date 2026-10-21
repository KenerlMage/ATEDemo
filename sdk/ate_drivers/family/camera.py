# -*- coding: utf-8 -*-
"""相机家族契约：工业相机（Basler pylon 等机器视觉设备）

与 SCPI 家族的差别：相机没有「命令—应答」文本协议。控制面是**节点参数**
（曝光、增益、ROI、像素格式、触发、帧率），数据面是**帧**（图像）。因此本家族把
动作收敛成 7 个能力，并统一「帧」与「统计量」的口径，保证不同厂商的相机在 TPS 里
可以互换：

    camera.enumerate  枚举在线相机
    camera.configure  设置采集参数（曝光 / 增益 / ROI / 像素格式 / 触发 / 帧率）
    camera.grab       取一帧，返回尺寸、统计量与可选缩略图
    camera.snap       取一帧并落盘（PNG），返回路径、字节数与 sha256
    camera.sequence   连续取 N 帧，返回逐帧统计与帧率
    camera.stats      对指定区域计算统计量（均值 / 极值 / 标准差 / 饱和率）
    camera.trigger    软触发（或启动一次单帧采集）

**为什么统计量要进契约**：ATE 里相机最常见的判据不是「看图」，而是
「均值、对比度、饱和率、亮区面积是否在阈值内」。把这些量做成驱动层输出，
TPS 就不用自己读像素、也不必依赖 numpy。

**仿真模型**：6 种图样 × 亮度 × 噪声，按 `(setup, frame_id)` 决定性生成，
同一参数永远得到同一帧 —— TPS 才能对统计量写阈值断言，报告也不会每次抖动。
"""

from __future__ import annotations

import hashlib
import math
import random
import struct
import time
import zlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from ..base import InstrumentDriver
from ..capabilities import Capability
from ..errors import ConfigurationError, DriverError
from ..transport import Transport

__all__ = [
    "CameraChannel",
    "CameraDriver",
    "CameraSetup",
    "FrameInfo",
    "PATTERNS",
    "downsample",
    "frame_stats",
    "simulate_frame",
    "write_png",
]

#: 仿真图样：覆盖 ATE 常见的四类光学判据
#: flat（亮度）/ gradient（均匀性）/ checker（解析力）/ edge（边界定位）/
#: spot（光斑能量）/ noise（信噪比）
PATTERNS = ("flat", "gradient", "checker", "edge", "spot", "noise")

MAX_DIMENSION = 20000
MAX_FRAMES = 10000


# ---------------------------------------------------------------- 采集参数


@dataclass
class CameraSetup:
    """一台相机的采集参数（配置面）

    取值与 Basler pylon 的节点名一一对应，`configure()` 只改这里，
    厂商包负责把字段翻译成具体节点（`Width` / `ExposureTime` / `Gain` …）。
    """

    width: int = 320                 # 仿真默认小尺寸，真机需按传感器能力下发
    height: int = 240
    offset_x: int = 0
    offset_y: int = 0
    pixel_format: str = "Mono8"      # Mono8 / Mono12 / BayerRG8 / BGR8 …
    exposure_us: float = 5000.0      # 曝光时间（微秒）
    exposure_auto: str = "Off"       # Off / Once / Continuous
    gain_db: Optional[float] = None  # 增益（dB），与 gain_raw 二选一
    gain_raw: Optional[float] = None # 增益（原始值），老 ace 机型只有 GainRaw
    gain_auto: str = "Off"
    frame_rate: Optional[float] = None   # 目标帧率（fps），None 表示不限制
    trigger_mode: str = "Off"        # Off / On
    trigger_source: str = "Software" # Software / Line1 / Line2 …
    timeout_ms: int = 5000           # 单帧取图超时
    # —— 仿真图样（真机忽略）——
    pattern: str = "flat"
    level: int = 128
    noise: float = 2.0

    def validate(self) -> "CameraSetup":
        """边界校验：越界直接报 ConfigurationError，不把非法参数送到设备"""
        if not (16 <= int(self.width) <= MAX_DIMENSION) or not (16 <= int(self.height) <= MAX_DIMENSION):
            raise ConfigurationError(
                f"采集尺寸非法: {self.width}x{self.height}",
                detail=f"宽高需在 16..{MAX_DIMENSION} 之间（真机还受传感器分辨率限制）")
        if int(self.offset_x) < 0 or int(self.offset_y) < 0:
            raise ConfigurationError("ROI 偏移不能为负", detail="offset_x / offset_y 从 0 开始")
        if not (10.0 <= float(self.exposure_us) <= 60_000_000.0):
            raise ConfigurationError(
                f"曝光时间非法: {self.exposure_us} us",
                detail="合理范围 10 us .. 60 s（超出范围真机会拒绝置值）")
        if not (0 <= int(self.level) <= 255):
            raise ConfigurationError("仿真亮度必须在 0..255 之间")
        if not (0.0 <= float(self.noise) <= 64.0):
            raise ConfigurationError("仿真噪声必须在 0..64 之间")
        if str(self.pattern).lower() not in PATTERNS:
            raise ConfigurationError(
                f"未知仿真图样: {self.pattern}", detail="可选: " + ", ".join(PATTERNS))
        if int(self.timeout_ms) <= 0:
            raise ConfigurationError("timeout_ms 必须大于 0")
        self.pattern = str(self.pattern).lower()
        self.width, self.height = int(self.width), int(self.height)
        self.offset_x, self.offset_y = int(self.offset_x), int(self.offset_y)
        self.timeout_ms = int(self.timeout_ms)
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "CameraSetup":
        data = dict(data or {})
        known = {f for f in cls.__dataclass_fields__}  # 忽略未知字段，便于档案里混写
        return cls(**{k: v for k, v in data.items() if k in known and v is not None})

    def merge(self, **params) -> dict:
        """把动作参数合并进当前参数（None 视为「不改」），返回变更项"""
        changed = {}
        for key, value in params.items():
            if value is None or key not in self.__dataclass_fields__:
                continue
            if getattr(self, key) != value:
                setattr(self, key, value)
                changed[key] = value
        return changed


@dataclass
class FrameInfo:
    """一帧的元数据（与统计量分开，便于报告直接引用）"""

    frame_id: int
    width: int
    height: int
    pixel_format: str
    timestamp: str = ""
    exposure_us: float = 0.0
    gain_db: Optional[float] = None
    trigger: str = ""
    source: str = "instrument"
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- 链路（仅日志）


class CameraChannel(Transport):
    """相机不跑文本协议：这条链路只把动作记进日志，供报告与排障使用"""

    kind = "camera"

    def __init__(self, cfg: Any = None, timeout: float = 2.0):
        super().__init__(cfg, timeout)
        self.log: list[str] = []

    def _open(self) -> None:
        pass

    def _close(self) -> None:
        pass

    def _write(self, data: bytes) -> None:
        self.log.append(self.decode(data))

    def _read_line(self, timeout: float) -> str:
        return ""

    def describe(self) -> dict:
        out = super().describe()
        out["actions"] = len(self.log)
        return out


# ---------------------------------------------------------------- 图像工具


def _region_box(width: int, height: int, region: Any) -> tuple:
    """区域归一化：`None`/`"full"` 全幅，`"center"` 居中 50%，dict 或四元组自定义"""
    if region in (None, "", "full"):
        return 0, 0, width, height
    if region == "center":
        w, h = width // 2, height // 2
        return (width - w) // 2, (height - h) // 2, (width + w) // 2, (height + h) // 2
    if isinstance(region, dict):
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        w = int(region.get("w", width - x))
        h = int(region.get("h", height - y))
    elif isinstance(region, (list, tuple)) and len(region) == 4:
        x, y, w, h = (int(v) for v in region)
    else:
        raise ConfigurationError(f"无法解析区域: {region!r}",
                                 detail='可用 "full" / "center" / {x,y,w,h} / (x,y,w,h)')
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    w = max(1, min(w, width - x))
    h = max(1, min(h, height - y))
    return x, y, x + w, y + h


def simulate_frame(setup: CameraSetup, frame_id: int = 0) -> list:
    """按图样生成一帧灰度图（确定性：同参数同 frame_id 永远同结果）"""
    w, h = int(setup.width), int(setup.height)
    level = max(0, min(255, int(round(float(setup.level)))))
    noise = max(0.0, float(setup.noise))
    pattern = str(setup.pattern).lower()
    rng = random.Random(f"{pattern}:{level}:{noise:.3f}:{w}x{h}:{frame_id}")
    rows: list = []
    cx, cy = w / 2.0, h / 2.0
    radius = max(4.0, min(w, h) / 6.0)
    two_r2 = 2.0 * radius * radius
    for y in range(h):
        row = bytearray(w)
        for x in range(w):
            if pattern == "gradient":
                base = int(level * x / max(1, w - 1))
            elif pattern == "checker":
                base = level if ((x // 32) + (y // 32)) % 2 == 0 else level // 4
            elif pattern == "edge":
                base = level if x >= w // 2 else level // 5
            elif pattern == "spot":
                d2 = (x - cx) ** 2 + (y - cy) ** 2
                base = int(level * math.exp(-d2 / two_r2))
            else:  # flat / noise
                base = level
            if noise:
                base += int(round(rng.uniform(-1.0, 1.0) * noise))
            row[x] = 0 if base < 0 else (255 if base > 255 else base)
        rows.append(row)
    return rows


def _as_gray_rows(pixels: Any) -> list:
    """统一成「行列表」：numpy 数组转为 Python 列表，彩色转灰度"""
    if hasattr(pixels, "tolist"):
        pixels = pixels.tolist()
    rows = []
    for row in pixels:
        if row and isinstance(row[0], (list, tuple)):
            rows.append([(int(v[0]) + int(v[1]) + int(v[2])) // 3 for v in row])
        else:
            rows.append(list(row))
    return rows


def downsample(pixels: Any, target_w: int = 32, target_h: int = 24) -> list:
    """最近邻抽样出缩略图（只给报告嵌入用，不参与判定）"""
    if hasattr(pixels, "tolist"):
        pixels = pixels.tolist()
    h = len(pixels)
    w = len(pixels[0]) if h else 0
    if not h or not w:
        return []
    out = []
    for ty in range(target_h):
        sy = min(h - 1, int(ty * h / target_h))
        row = pixels[sy]
        line = []
        for tx in range(target_w):
            sx = min(w - 1, int(tx * w / target_w))
            v = row[sx]
            if isinstance(v, (list, tuple)):
                v = (int(v[0]) + int(v[1]) + int(v[2])) // 3
            line.append(int(v))
        out.append(line)
    return out


def frame_stats(pixels: Any, region: Any = None) -> dict:
    """区域统计：均值 / 极值 / 标准差 / 饱和率 / 亮区面积占比

    numpy 数组走向量化快路径（真机 2 MP 帧也只需毫秒级）；纯 list 走单遍循环。
    """
    try:
        import numpy as np  # type: ignore
        if isinstance(pixels, np.ndarray):
            arr = pixels
            if arr.ndim == 3:
                arr = arr.reshape(arr.shape[0], arr.shape[1], -1).mean(axis=2)
            h, w = int(arr.shape[0]), int(arr.shape[1])
            x0, y0, x1, y1 = _region_box(w, h, region)
            roi = arr[y0:y1, x0:x1].astype("float64")
            total = float(roi.size) or 1.0
            mean = float(roi.mean())
            std = float(roi.std())
            vmin, vmax = float(roi.min()), float(roi.max())
            saturated = float((roi >= 250).sum()) / total * 100.0
            bright = float((roi >= mean + std).sum()) / total * 100.0 if std > 0 else 0.0
            return {
                "region": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
                "pixels": int(roi.size),
                "mean": round(mean, 3),
                "min": round(vmin, 3),
                "max": round(vmax, 3),
                "std": round(std, 3),
                "contrast": round(vmax - vmin, 3),
                "saturated_pct": round(saturated, 3),
                "bright_pct": round(bright, 3),
            }
    except Exception:  # numpy 缺失或形状异常 → 走纯 Python
        pass

    rows = _as_gray_rows(pixels)
    h = len(rows)
    w = len(rows[0]) if h else 0
    if not h or not w:
        return {"region": {"x": 0, "y": 0, "w": 0, "h": 0}, "pixels": 0,
                "mean": 0.0, "min": 0.0, "max": 0.0, "std": 0.0,
                "contrast": 0.0, "saturated_pct": 0.0, "bright_pct": 0.0}
    x0, y0, x1, y1 = _region_box(w, h, region)
    total = 0.0
    sq = 0.0
    count = 0
    vmin, vmax = 255.0, 0.0
    saturated = 0
    for y in range(y0, y1):
        row = rows[y]
        for x in range(x0, x1):
            v = float(row[x])
            total += v
            sq += v * v
            count += 1
            if v < vmin:
                vmin = v
            if v > vmax:
                vmax = v
            if v >= 250:
                saturated += 1
    mean = total / count
    var = max(0.0, sq / count - mean * mean)
    std = math.sqrt(var)
    bright = 0
    if std > 0:
        for y in range(y0, y1):
            row = rows[y]
            for x in range(x0, x1):
                if row[x] >= mean + std:
                    bright += 1
    return {
        "region": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
        "pixels": count,
        "mean": round(mean, 3),
        "min": round(vmin, 3),
        "max": round(vmax, 3),
        "std": round(std, 3),
        "contrast": round(vmax - vmin, 3),
        "saturated_pct": round(saturated / count * 100.0, 3),
        "bright_pct": round(bright / count * 100.0, 3),
    }


def write_png(path: Any, pixels: Any) -> int:
    """把灰度/彩色帧写成 PNG（仅标准库：zlib + struct），返回文件字节数

    不依赖 Pillow / numpy：ATE 现场机器装 PIL 不一定成功，而 PNG 只在
    报告与复核时用到，用三十行代码换来「零依赖」是值得的。
    """
    if hasattr(pixels, "tolist"):
        pixels = pixels.tolist()
    h = len(pixels)
    w = len(pixels[0]) if h else 0
    if not h or not w:
        raise DriverError("空帧无法保存", detail="像素数据为空，检查取帧是否成功")
    color = isinstance(pixels[0][0], (list, tuple))
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # 每行滤波类型 0（None）
        if color:
            for px in row:
                raw.extend((int(px[0]) & 0xFF, int(px[1]) & 0xFF, int(px[2]) & 0xFF))
        else:
            raw.extend(int(v) & 0xFF for v in row)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2 if color else 0, 0, 0, 0)
    payload = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
               + _chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _chunk(b"IEND", b""))
    target = Path(str(path))
    if not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return len(payload)


# ---------------------------------------------------------------- 家族驱动


class CameraDriver(InstrumentDriver):
    """工业相机家族基类

    厂商包只需实现 6 个钩子：`_open_device` / `_close_device` / `_enumerate` /
    `_apply` / `_fetch` / `_save`，动作语义、统计口径、仿真模型、日志与返回结构
    全部由家族层提供。
    """

    driver_key = "generic-camera"
    family = "camera"
    label = "通用工业相机"
    driver_version = "1.0.0"
    sim_idn = "BASLER,SIM-CAMERA,SN-SIM-0001,PYLON-SIM"
    INTERFACES = ("USB", "GIGE")
    BACKENDS = ("native",)

    CAPABILITIES = (
        Capability.IDENTIFY,
        Capability.RESET,
        Capability.STATE,
        Capability.CAMERA_ENUMERATE,
        Capability.CAMERA_CONFIGURE,
        Capability.CAMERA_GRAB,
        Capability.CAMERA_SNAP,
        Capability.CAMERA_SEQUENCE,
        Capability.CAMERA_STATS,
        Capability.CAMERA_TRIGGER,
    )

    ACTIONS = {
        Capability.IDENTIFY: "identify",
        Capability.RESET: "reset",
        Capability.STATE: "state",
        Capability.CAMERA_ENUMERATE: "enumerate_devices",
        Capability.CAMERA_CONFIGURE: "configure",
        Capability.CAMERA_GRAB: "grab",
        Capability.CAMERA_SNAP: "snap",
        Capability.CAMERA_SEQUENCE: "sequence",
        Capability.CAMERA_STATS: "stats",
        Capability.CAMERA_TRIGGER: "trigger",
    }

    def __init__(self, cfg: Any = None, mode: str = "simulate", timeout: Optional[float] = None,
                 transport: Optional[Transport] = None, alias: str = "",
                 setup: Optional[dict] = None, **kw: Any):
        from ..models import DeviceConfig

        _cfg = cfg if isinstance(cfg, DeviceConfig) else DeviceConfig.from_dict(cfg or {})
        super().__init__(_cfg, mode=mode, timeout=timeout,
                         transport=transport or CameraChannel(_cfg), alias=alias, **_kw_only(kw))
        raw: dict = {}
        extra = dict(getattr(_cfg, "extra", {}) or {})
        if isinstance(extra.get("camera"), dict):
            raw.update(extra["camera"])
        raw.update(setup or {})
        self.setup = CameraSetup.from_dict(raw).validate()
        # 设备选择器（真机用）：序列号 > 用户自定义名 > 序号
        self.selector = {
            "serial_number": str(extra.get("serial_number") or ""),
            "user_name": str(extra.get("user_name") or ""),
            "index": extra.get("index"),
        }
        self.device: dict = {}
        self.frame_count = 0
        self.last_stats: dict = {}
        self.last_frame_at = ""
        self.grabbing = False
        self.warnings: list = []

    # ------------------------------------------------------------ 会话

    def open(self) -> dict:
        try:
            info = self._open_device()
        except DriverError:
            self.last_error = "相机打开失败"
            raise
        self.device = dict(info or {})
        self.opened = True
        self.opened_at = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.idn = str(self.identify()["value"])
        except DriverError as e:
            self.idn = f"(未识别: {e})"
            self.last_error = str(e)
        return {
            "ok": True,
            "device_id": self.device_id,
            "alias": self.alias,
            "driver": self.driver_key,
            "driver_version": self.driver_version,
            "mode": self.mode,
            "transport": self.transport.kind,
            "idn": self.idn,
            "device": dict(self.device),
            "setup": self.setup.to_dict(),
            "simulated": self.simulate,
        }

    def close(self) -> dict:
        try:
            self._close_device()
        finally:
            self.grabbing = False
            try:
                self.transport.close()
            finally:
                self.opened = False
        return {"ok": True, "device_id": self.device_id,
                "frames": self.frame_count, "actions": len(self.commands)}

    # ------------------------------------------------------------ 身份

    def identify(self) -> dict:
        if self.simulate:
            return {"value": self.sim_idn, "quality": "simulated",
                    "device": {"model": self.cfg.model or "SIM-CAMERA",
                               "serial_number": "SN-SIM-0001", "vendor": "Basler(SIM)"}}
        return {"value": self._identity_real(), "quality": "good", "device": dict(self.device)}

    def _identity_real(self) -> str:
        model = self.device.get("model") or self.cfg.model or "UNKNOWN"
        serial = self.device.get("serial_number") or ""
        return f"BASLER,{model},{serial},{self.driver_version}"

    # ------------------------------------------------------------ 动作：枚举

    def enumerate_devices(self) -> dict:
        devices = self._enumerate()
        return {"value": devices, "count": len(devices),
                "quality": "simulated" if self.simulate else "good"}

    # ------------------------------------------------------------ 动作：配置

    def configure(self, **params: Any) -> dict:
        changed = self.setup.merge(**params)
        self.setup.validate()
        warnings = self._apply(self.setup) if not self.simulate else []
        if warnings:
            self.warnings.extend(warnings)
        self._log(f"Configure {changed}")
        return {"value": self.setup.to_dict(), "changed": changed,
                "applied": not self.simulate, "warnings": warnings,
                "quality": "simulated" if self.simulate else "good"}

    # ------------------------------------------------------------ 动作：取帧

    def grab(self, timeout_ms: Optional[int] = None, thumbnail: bool = True,
             region: Any = None) -> dict:
        self.ensure_open()
        pixels, meta = self._fetch(self.setup, int(timeout_ms or self.setup.timeout_ms))
        stats = frame_stats(pixels, region)
        self.frame_count += 1
        self.last_stats = stats
        self.last_frame_at = time.strftime("%Y-%m-%d %H:%M:%S")
        info = FrameInfo(
            frame_id=self.frame_count,
            width=int(meta.get("width") or len(pixels[0])),
            height=int(meta.get("height") or len(pixels)),
            pixel_format=str(meta.get("pixel_format") or self.setup.pixel_format),
            timestamp=str(meta.get("timestamp") or self.last_frame_at),
            exposure_us=float(meta.get("exposure_us") or self.setup.exposure_us),
            gain_db=meta.get("gain_db", self.setup.gain_db),
            trigger=str(self.setup.trigger_mode),
            source="simulate" if self.simulate else "instrument",
            extra={k: v for k, v in meta.items()
                   if k not in ("width", "height", "pixel_format", "timestamp",
                                "exposure_us", "gain_db")},
        )
        value = info.to_dict()
        value["stats"] = stats
        if thumbnail:
            value["thumbnail"] = downsample(pixels, 32, 24)
        return {"value": value, "stats": stats,
                "quality": "simulated" if self.simulate else "good"}

    def snap(self, path: str = "", format: str = "png", timeout_ms: Optional[int] = None,
             region: Any = None) -> dict:
        if not str(path or "").strip():
            raise ConfigurationError("snap 需要给出落盘路径",
                                     detail='TPS 里通常写到运行目录：snap(path=f"{run_dir}/cam.png")')
        self.ensure_open()
        pixels, meta = self._fetch(self.setup, int(timeout_ms or self.setup.timeout_ms))
        target = Path(str(path)).expanduser()
        self._save(target, pixels, str(format or "png"))
        try:
            data = target.read_bytes()
        except OSError as e:  # 理论上不会发生，除非权限/磁盘问题
            raise DriverError(f"写入后读取失败: {target}（{e}）") from e
        stats = frame_stats(pixels, region)
        self.frame_count += 1
        self.last_stats = stats
        self.last_frame_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self._log(f"Snap {target.name}")
        return {"value": {
            "path": str(target),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "width": int(meta.get("width") or len(pixels[0])),
            "height": int(meta.get("height") or len(pixels)),
            "pixel_format": str(meta.get("pixel_format") or self.setup.pixel_format),
            "frame_id": self.frame_count,
            "stats": stats,
            "source": "simulate" if self.simulate else "instrument",
        }, "stats": stats, "quality": "simulated" if self.simulate else "good"}

    def sequence(self, count: int = 5, interval_ms: float = 0.0, save_dir: str = "",
                 prefix: str = "frame", region: Any = None, detail_limit: int = 50) -> dict:
        n = int(count or 1)
        if not (1 <= n <= MAX_FRAMES):
            raise ConfigurationError(f"连拍帧数非法: {count}", detail=f"取值 1..{MAX_FRAMES}")
        self.ensure_open()
        gap = max(0.0, float(interval_ms or 0.0))
        frames: list = []
        stats_list: list = []
        saved: list = []
        started = time.perf_counter()
        for i in range(n):
            pixels, meta = self._fetch(self.setup, self.setup.timeout_ms)
            st = frame_stats(pixels, region)
            self.frame_count += 1
            stats_list.append(st)
            item = {"frame_id": self.frame_count, "index": i,
                    "timestamp": time.strftime("%H:%M:%S"), "stats": st}
            if save_dir:
                target = Path(str(save_dir)).expanduser() / f"{prefix}_{i:04d}.png"
                self._save(target, pixels, "png")
                item["path"] = str(target)
                saved.append(str(target))
            if len(frames) < int(detail_limit):
                frames.append(item)
            if gap:
                time.sleep(gap / 1000.0)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        means = [s["mean"] for s in stats_list]
        self.last_stats = stats_list[-1] if stats_list else {}
        self.last_frame_at = time.strftime("%Y-%m-%d %H:%M:%S")
        return {"value": {
            "count": n,
            "elapsed_ms": round(elapsed_ms, 2),
            "fps": round(1000.0 * n / elapsed_ms, 2) if elapsed_ms > 0 else 0.0,
            "mean_of_means": round(sum(means) / len(means), 3) if means else 0.0,
            "mean_spread": round(max(means) - min(means), 3) if means else 0.0,
            "frames": frames,
            "truncated": n > len(frames),
            "saved": saved,
            "source": "simulate" if self.simulate else "instrument",
        }, "quality": "simulated" if self.simulate else "good"}

    def stats(self, region: Any = None, timeout_ms: Optional[int] = None) -> dict:
        res = self.grab(timeout_ms=timeout_ms, thumbnail=False, region=region)
        return {"value": res["value"]["stats"], "frame_id": res["value"]["frame_id"],
                "quality": res.get("quality", "good")}

    def trigger(self, timeout_ms: Optional[int] = None) -> dict:
        self.ensure_open()
        if self.simulate:
            self.frame_count += 1
            return {"value": {"triggered": True, "frame_id": self.frame_count,
                              "source": "Software", "simulated": True},
                    "quality": "simulated"}
        self._software_trigger()
        pixels, meta = self._fetch(self.setup, int(timeout_ms or self.setup.timeout_ms))
        st = frame_stats(pixels)
        self.frame_count += 1
        self.last_stats = st
        return {"value": {"triggered": True, "frame_id": self.frame_count,
                          "source": self.setup.trigger_source, "stats": st},
                "quality": "good"}

    def reset(self) -> dict:
        self.setup = CameraSetup()
        warnings = [] if self.simulate else self._apply(self.setup)
        self.frame_count = 0
        self.last_stats = {}
        return {"value": self.setup.to_dict(), "warnings": warnings,
                "quality": "simulated" if self.simulate else "good"}

    # ------------------------------------------------------------ 状态

    def state(self) -> dict:
        out = super().state()
        out.update({
            "device": dict(self.device),
            "selector": dict(self.selector),
            "setup": self.setup.to_dict(),
            "frames": self.frame_count,
            "grabbing": self.grabbing,
            "last_frame_at": self.last_frame_at,
            "last_stats": dict(self.last_stats),
            "warnings": list(self.warnings[-10:]),
        })
        return out

    # ------------------------------------------------------------ 钩子（厂商实现）

    def _open_device(self) -> dict:
        """打开设备并返回设备信息；仿真模式返回模拟信息"""
        if self.simulate:
            self._log("Open(SIM)")
            return {"model": self.cfg.model or "SIM-CAMERA", "serial_number": "SN-SIM-0001",
                    "vendor": "Basler(SIM)", "interface": "SIM", "width": self.setup.width,
                    "height": self.setup.height}
        raise NotImplementedError("厂商驱动必须实现 _open_device()")

    def _close_device(self) -> None:
        if self.simulate:
            self._log("Close(SIM)")

    def _enumerate(self) -> list:
        if self.simulate:
            return [
                {"index": 0, "model": "acA1920-40gm", "serial_number": "SIM-0001",
                 "user_name": "SIM-CAM-0", "interface": "GigE", "ip": "192.168.1.10"},
                {"index": 1, "model": "acA2440-75um", "serial_number": "SIM-0002",
                 "user_name": "SIM-CAM-1", "interface": "USB3", "ip": ""},
            ]
        raise NotImplementedError("厂商驱动必须实现 _enumerate()")

    def _apply(self, setup: CameraSetup) -> list:
        return []

    def _fetch(self, setup: CameraSetup, timeout_ms: int) -> tuple:
        if self.simulate:
            self._log(f"Grab({setup.pattern},{setup.width}x{setup.height})")
            pixels = simulate_frame(setup, self.frame_count)
            return pixels, {"width": setup.width, "height": setup.height,
                            "pixel_format": setup.pixel_format,
                            "timestamp": _now_ms(),
                            "exposure_us": setup.exposure_us, "gain_db": setup.gain_db}
        raise NotImplementedError("厂商驱动必须实现 _fetch()")

    def _save(self, path: Path, pixels: Any, fmt: str) -> None:
        write_png(path, pixels)

    def _software_trigger(self) -> None:
        raise NotImplementedError("厂商驱动必须实现 _software_trigger()")

    # ------------------------------------------------------------ 内部

    def _log(self, action: str) -> None:
        self.commands.append(action)

    def simulate_command(self, command: str) -> str:  # 相机无文本协议，保留空实现
        return ""


def _now_ms() -> str:
    """毫秒级时间戳（strftime 不支持 %f，统一用 datetime）"""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _kw_only(kw: dict) -> dict:
    """只把基类认识的键传下去（厂商包可能多传 backend 等）"""
    allowed = {"backend"}
    return {k: v for k, v in (kw or {}).items() if k in allowed}
