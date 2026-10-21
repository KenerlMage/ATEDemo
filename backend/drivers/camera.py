# -*- coding: utf-8 -*-
"""工业相机驱动（Basler pylon 8.0.0）—— Runner 运行时层

与示波器/万用表不同，相机不是「命令—应答」设备：控制面是 GenICam 节点
（`Width` / `ExposureTime` / `Gain` / `TriggerMode`），数据面是**帧**。
因此本驱动不走 SCPI 链路，而是直接持有 `pypylon` 的 `InstantCamera`；
`CameraChannel` 只承担动作日志，保证报告里能看到「配了什么、取了几帧」。

真机依赖：Basler pylon Software Suite 8.0.0 + `pypylon`（惰性导入，缺失时
仿真模式完全不受影响）。仿真模式生成**确定性**图像（同参数同帧序号永远同结果），
TPS 才能对均值/对比度/饱和率写阈值断言。

动作：
    idn / reset / state / enumerate / configure / grab / snap / sequence / stats / trigger
"""

from __future__ import annotations

import hashlib
import math
import random
import struct
import time
import zlib
from pathlib import Path
from typing import Any, Optional

from .base import Driver
from .errors import DriverError
from .models import DeviceConfig
from .transport import Transport

PATTERNS = ("flat", "gradient", "checker", "edge", "spot", "noise")
PYLON_BASELINE = "8.0.0"


class CameraChannel(Transport):
    """相机无文本协议：这条链路只把动作记进日志"""

    kind = "camera"

    def __init__(self, cfg: Any = None):
        super().__init__(cfg, 2.0)
        self.log: list = []

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
    if region in (None, "", "full"):
        return 0, 0, width, height
    if region == "center":
        w, h = width // 2, height // 2
        return (width - w) // 2, (height - h) // 2, (width + w) // 2, (height + h) // 2
    if isinstance(region, dict):
        x, y = int(region.get("x", 0)), int(region.get("y", 0))
        w = int(region.get("w", width - x))
        h = int(region.get("h", height - y))
    elif isinstance(region, (list, tuple)) and len(region) == 4:
        x, y, w, h = (int(v) for v in region)
    else:
        raise DriverError(f"无法解析区域: {region!r}",
                          detail='可用 "full" / "center" / {x,y,w,h} / (x,y,w,h)')
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    w = max(1, min(w, width - x))
    h = max(1, min(h, height - y))
    return x, y, x + w, y + h


def simulate_frame(width: int, height: int, pattern: str, level: int,
                   noise: float, frame_id: int = 0) -> list:
    """确定性合成帧（灰度）"""
    level = max(0, min(255, int(level)))
    rng = random.Random(f"{pattern}:{level}:{noise:.3f}:{width}x{height}:{frame_id}")
    rows = []
    cx, cy = width / 2.0, height / 2.0
    two_r2 = 2.0 * max(4.0, min(width, height) / 6.0) ** 2
    for y in range(height):
        row = bytearray(width)
        for x in range(width):
            if pattern == "gradient":
                base = int(level * x / max(1, width - 1))
            elif pattern == "checker":
                base = level if ((x // 32) + (y // 32)) % 2 == 0 else level // 4
            elif pattern == "edge":
                base = level if x >= width // 2 else level // 5
            elif pattern == "spot":
                base = int(level * math.exp(-(((x - cx) ** 2 + (y - cy) ** 2) / two_r2)))
            else:
                base = level
            if noise:
                base += int(round(rng.uniform(-1.0, 1.0) * noise))
            row[x] = 0 if base < 0 else (255 if base > 255 else base)
        rows.append(row)
    return rows


def frame_stats(pixels: Any, region: Any = None) -> dict:
    """区域统计（numpy 快路径 + 纯 Python 兜底）"""
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
            mean, std = float(roi.mean()), float(roi.std())
            vmin, vmax = float(roi.min()), float(roi.max())
            return {
                "region": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
                "pixels": int(roi.size), "mean": round(mean, 3),
                "min": round(vmin, 3), "max": round(vmax, 3), "std": round(std, 3),
                "contrast": round(vmax - vmin, 3),
                "saturated_pct": round(float((roi >= 250).sum()) / total * 100.0, 3),
                "bright_pct": round(float((roi >= mean + std).sum()) / total * 100.0
                                    if std > 0 else 0.0, 3),
            }
    except Exception:
        pass

    if hasattr(pixels, "tolist"):
        pixels = pixels.tolist()
    rows = []
    for row in pixels:
        if row and isinstance(row[0], (list, tuple)):
            rows.append([(int(v[0]) + int(v[1]) + int(v[2])) // 3 for v in row])
        else:
            rows.append(list(row))
    h = len(rows)
    w = len(rows[0]) if h else 0
    if not h or not w:
        return {"region": {"x": 0, "y": 0, "w": 0, "h": 0}, "pixels": 0, "mean": 0.0,
                "min": 0.0, "max": 0.0, "std": 0.0, "contrast": 0.0,
                "saturated_pct": 0.0, "bright_pct": 0.0}
    x0, y0, x1, y1 = _region_box(w, h, region)
    total = sq = 0.0
    count = saturated = 0
    vmin, vmax = 255.0, 0.0
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
    std = math.sqrt(max(0.0, sq / count - mean * mean))
    bright = 0
    if std > 0:
        for y in range(y0, y1):
            row = rows[y]
            for x in range(x0, x1):
                if row[x] >= mean + std:
                    bright += 1
    return {
        "region": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
        "pixels": count, "mean": round(mean, 3), "min": round(vmin, 3),
        "max": round(vmax, 3), "std": round(std, 3), "contrast": round(vmax - vmin, 3),
        "saturated_pct": round(saturated / count * 100.0, 3),
        "bright_pct": round(bright / count * 100.0, 3),
    }


def write_png(path: Any, pixels: Any) -> int:
    """零依赖 PNG 写出（灰度 / RGB）"""
    if hasattr(pixels, "tolist"):
        pixels = pixels.tolist()
    h = len(pixels)
    w = len(pixels[0]) if h else 0
    if not h or not w:
        raise DriverError("空帧无法保存", detail="像素数据为空，检查取帧是否成功")
    color = isinstance(pixels[0][0], (list, tuple))
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        if color:
            for px in row:
                raw.extend((int(px[0]) & 0xFF, int(px[1]) & 0xFF, int(px[2]) & 0xFF))
        else:
            raw.extend(int(v) & 0xFF for v in row)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2 if color else 0, 0, 0, 0)
    blob = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _chunk(b"IEND", b""))
    target = Path(str(path))
    if not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(blob)
    return len(blob)


def downsample(pixels: Any, target_w: int = 32, target_h: int = 24) -> list:
    if hasattr(pixels, "tolist") and not isinstance(pixels, list):
        pixels = pixels.tolist()
    h = len(pixels)
    w = len(pixels[0]) if h else 0
    if not h or not w:
        return []
    out = []
    for ty in range(target_h):
        row = pixels[min(h - 1, int(ty * h / target_h))]
        line = []
        for tx in range(target_w):
            v = row[min(w - 1, int(tx * w / target_w))]
            if isinstance(v, (list, tuple)):
                v = (int(v[0]) + int(v[1]) + int(v[2])) // 3
            line.append(int(v))
        out.append(line)
    return out


# ---------------------------------------------------------------- 驱动


class CameraDriver(Driver):
    """Basler 工业相机（GigE / USB3，pylon 8.0.0）"""

    driver_key = "basler-pylon"
    family = "camera"
    label = "工业相机 · Basler pylon 8.0.0"
    sim_idn = f"BASLER,acA1920-40gm,SIM0001,PYLON-{PYLON_BASELINE}"

    ACTIONS = {
        "idn": "identify",
        "reset": "reset",
        "state": "state",
        "enumerate": "enumerate_devices",
        "configure": "configure",
        "grab": "grab",
        "snap": "snap",
        "sequence": "sequence",
        "stats": "stats",
        "trigger": "trigger",
    }

    DEFAULTS = {
        "width": 320, "height": 240, "offset_x": 0, "offset_y": 0,
        "pixel_format": "Mono8", "exposure_us": 5000.0, "exposure_auto": "Off",
        "gain_db": None, "gain_auto": "Off", "frame_rate": None,
        "trigger_mode": "Off", "trigger_source": "Software", "timeout_ms": 5000,
        "pattern": "flat", "level": 128, "noise": 2.0,
    }

    def __init__(self, cfg: Any = None, mode: str = "simulate", timeout: Optional[float] = None,
                 transport: Optional[Transport] = None, alias: str = "", setup: Optional[dict] = None):
        _cfg = cfg if isinstance(cfg, DeviceConfig) else DeviceConfig.from_dict(cfg or {})
        super().__init__(_cfg, mode=mode, timeout=timeout,
                         transport=transport or CameraChannel(_cfg), alias=alias)
        extra = dict(getattr(_cfg, "extra", {}) or {})
        raw = dict(self.DEFAULTS)
        if isinstance(extra.get("camera"), dict):
            raw.update(extra["camera"])
        raw.update(setup or {})
        self.setup = self._validate(raw)
        self.selector = {"serial_number": str(extra.get("serial_number") or ""),
                         "user_name": str(extra.get("user_name") or ""),
                         "index": extra.get("index")}
        self.device: dict = {}
        self.frame_count = 0
        self.last_stats: dict = {}
        self.grabbing = False
        self.warnings: list = []
        self._pylon: Any = None
        self._cam: Any = None

    # ------------------------------------------------------------ 会话

    def open(self) -> dict:
        self.device = dict(self._open_device() or {})
        self.opened = True
        self.opened_at = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.idn = str(self.identify()["value"])
        except DriverError as e:
            self.idn = f"(未识别: {e})"
            self.last_error = str(e)
        return {"ok": True, "device_id": self.device_id, "alias": self.alias,
                "driver": self.driver_key, "mode": self.mode,
                "transport": self.transport.kind, "idn": self.idn,
                "device": dict(self.device), "setup": dict(self.setup),
                "simulated": self.simulate}

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

    def identify(self) -> dict:
        if self.simulate:
            return {"value": self.sim_idn, "simulated": True,
                    "device": {"model": self.cfg.model or "SIM-CAMERA",
                               "serial_number": "SN-SIM-0001",
                               "vendor": f"Basler(SIM pylon {PYLON_BASELINE})"}}
        model = self.device.get("model") or self.cfg.model or "UNKNOWN"
        return {"value": f"BASLER,{model},{self.device.get('serial_number', '')},"
                         f"PYLON-{PYLON_BASELINE}"}

    # ------------------------------------------------------------ 动作

    def enumerate_devices(self) -> dict:
        devices = self._enumerate()
        return {"value": devices, "count": len(devices)}

    def configure(self, **params) -> dict:
        changed = {}
        for key, value in params.items():
            if key in self.DEFAULTS and value is not None and self.setup.get(key) != value:
                self.setup[key] = value
                changed[key] = value
        self.setup = self._validate(self.setup)
        warnings = [] if self.simulate else self._apply(self.setup)
        self.warnings.extend(warnings)
        self.commands.append(f"Configure {changed}")
        return {"value": dict(self.setup), "changed": changed, "warnings": warnings}

    def grab(self, timeout_ms: Optional[int] = None, thumbnail: bool = True,
             region: Any = None) -> dict:
        self.ensure_open()
        pixels, meta = self._fetch(self.setup, int(timeout_ms or self.setup["timeout_ms"]))
        stats = frame_stats(pixels, region)
        self.frame_count += 1
        self.last_stats = stats
        value = {
            "frame_id": self.frame_count,
            "width": int(meta.get("width") or len(pixels[0])),
            "height": int(meta.get("height") or len(pixels)),
            "pixel_format": str(meta.get("pixel_format") or self.setup["pixel_format"]),
            "timestamp": meta.get("timestamp") or time.strftime("%Y-%m-%d %H:%M:%S"),
            "exposure_us": float(meta.get("exposure_us") or self.setup["exposure_us"]),
            "gain_db": meta.get("gain_db", self.setup.get("gain_db")),
            "stats": stats,
            "source": "simulate" if self.simulate else "instrument",
        }
        if thumbnail:
            value["thumbnail"] = downsample(pixels, 32, 24)
        return {"value": value, "stats": stats, "simulated": self.simulate}

    def snap(self, path: str = "", format: str = "png", timeout_ms: Optional[int] = None,
             region: Any = None) -> dict:
        if not str(path or "").strip():
            raise DriverError("snap 需要给出落盘路径",
                              detail='TPS 里通常写运行目录：snap(path=f"{run_dir}/cam.png")')
        self.ensure_open()
        pixels, meta = self._fetch(self.setup, int(timeout_ms or self.setup["timeout_ms"]))
        target = Path(str(path)).expanduser()
        self._save(target, pixels, str(format or "png"))
        data = target.read_bytes()
        stats = frame_stats(pixels, region)
        self.frame_count += 1
        self.last_stats = stats
        self.commands.append(f"Snap {target.name}")
        return {"value": {"path": str(target), "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest(),
                          "width": int(meta.get("width") or len(pixels[0])),
                          "height": int(meta.get("height") or len(pixels)),
                          "pixel_format": str(meta.get("pixel_format") or self.setup["pixel_format"]),
                          "frame_id": self.frame_count, "stats": stats}}

    def sequence(self, count: int = 5, interval_ms: float = 0.0, save_dir: str = "",
                 prefix: str = "frame", region: Any = None) -> dict:
        n = int(count or 1)
        if not (1 <= n <= 10000):
            raise DriverError(f"连拍帧数非法: {count}", detail="取值 1..10000")
        self.ensure_open()
        gap = max(0.0, float(interval_ms or 0.0))
        frames, saved, means = [], [], []
        started = time.perf_counter()
        for i in range(n):
            pixels, _ = self._fetch(self.setup, self.setup["timeout_ms"])
            st = frame_stats(pixels, region)
            self.frame_count += 1
            means.append(st["mean"])
            item = {"frame_id": self.frame_count, "index": i, "stats": st}
            if save_dir:
                target = Path(str(save_dir)).expanduser() / f"{prefix}_{i:04d}.png"
                self._save(target, pixels, "png")
                item["path"] = str(target)
                saved.append(str(target))
            if len(frames) < 50:
                frames.append(item)
            if gap:
                time.sleep(gap / 1000.0)
        elapsed = (time.perf_counter() - started) * 1000.0
        self.last_stats = frames[-1]["stats"] if frames else {}
        return {"value": {"count": n, "elapsed_ms": round(elapsed, 2),
                          "fps": round(1000.0 * n / elapsed, 2) if elapsed > 0 else 0.0,
                          "mean_of_means": round(sum(means) / len(means), 3) if means else 0.0,
                          "mean_spread": round(max(means) - min(means), 3) if means else 0.0,
                          "frames": frames, "truncated": n > len(frames), "saved": saved}}

    def stats(self, region: Any = None, timeout_ms: Optional[int] = None) -> dict:
        res = self.grab(timeout_ms=timeout_ms, thumbnail=False, region=region)
        return {"value": res["value"]["stats"], "frame_id": res["value"]["frame_id"]}

    def trigger(self, timeout_ms: Optional[int] = None) -> dict:
        self.ensure_open()
        if self.simulate:
            self.frame_count += 1
            return {"value": {"triggered": True, "frame_id": self.frame_count,
                              "source": "Software", "simulated": True}}
        self._software_trigger()
        pixels, _ = self._fetch(self.setup, int(timeout_ms or self.setup["timeout_ms"]))
        st = frame_stats(pixels)
        self.frame_count += 1
        self.last_stats = st
        return {"value": {"triggered": True, "frame_id": self.frame_count,
                          "source": self.setup["trigger_source"], "stats": st}}

    def reset(self) -> dict:
        self.setup = self._validate(dict(self.DEFAULTS))
        warnings = [] if self.simulate else self._apply(self.setup)
        self.frame_count = 0
        self.last_stats = {}
        return {"value": dict(self.setup), "warnings": warnings}

    def state(self) -> dict:
        out = super().state()
        out.update({"device": dict(self.device), "selector": dict(self.selector),
                    "setup": dict(self.setup), "frames": self.frame_count,
                    "grabbing": self.grabbing, "last_stats": dict(self.last_stats),
                    "warnings": list(self.warnings[-10:]),
                    "pylon": {"baseline": PYLON_BASELINE,
                              "pypylon": getattr(self._pylon, "__version__", "") if self._pylon else ""}})
        return out

    # ------------------------------------------------------------ 参数校验

    @staticmethod
    def _validate(raw: dict) -> dict:
        setup = dict(CameraDriver.DEFAULTS)
        setup.update({k: v for k, v in (raw or {}).items() if k in CameraDriver.DEFAULTS})
        setup["width"], setup["height"] = int(setup["width"]), int(setup["height"])
        if not (16 <= setup["width"] <= 20000) or not (16 <= setup["height"] <= 20000):
            raise DriverError(f"采集尺寸非法: {setup['width']}x{setup['height']}",
                              detail="宽高需在 16..20000 之间")
        if not (10.0 <= float(setup["exposure_us"]) <= 60_000_000.0):
            raise DriverError(f"曝光时间非法: {setup['exposure_us']} us",
                              detail="合理范围 10 us .. 60 s")
        pattern = str(setup["pattern"]).lower()
        if pattern not in PATTERNS:
            raise DriverError(f"未知仿真图样: {setup['pattern']}",
                              detail="可选: " + ", ".join(PATTERNS))
        setup["pattern"] = pattern
        setup["level"] = max(0, min(255, int(setup["level"])))
        setup["noise"] = max(0.0, min(64.0, float(setup["noise"])))
        setup["timeout_ms"] = int(setup["timeout_ms"])
        return setup

    # ------------------------------------------------------------ pylon 真机

    def _import_pylon(self) -> Any:
        if self._pylon is not None:
            return self._pylon
        try:
            from pypylon import pylon  # type: ignore
        except ImportError as e:
            raise DriverError(
                "未安装 pypylon（Basler 相机需要 pylon 8.0.0 运行时 + pypylon Python 包）",
                device_id=self.device_id,
                detail="① 安装 Basler pylon Software Suite 8.0.0（含驱动与 pylon Viewer）；"
                       "② pip install pypylon（与运行时同基线）；③ pylon Viewer 确认识别到相机；"
                       "④ 以 mode=\"real\" 打开。仿真模式不受影响。",
            ) from e
        self._pylon = pylon
        return pylon

    def _open_device(self) -> dict:
        if self.simulate:
            self.commands.append("Open(SIM)")
            return {"model": self.cfg.model or "SIM-CAMERA", "serial_number": "SN-SIM-0001",
                    "vendor": f"Basler(SIM pylon {PYLON_BASELINE})", "interface": "SIM"}
        pylon = self._import_pylon()
        tl = pylon.TlFactory.GetInstance()
        try:
            infos = list(tl.EnumerateDevices())
        except Exception as e:
            raise DriverError(f"枚举相机失败: {e}", device_id=self.device_id,
                              detail="检查 GigE / USB3 连接、PoE 供电、网卡巨帧与防火墙") from e
        if not infos:
            raise DriverError(
                "未发现 Basler 相机", device_id=self.device_id,
                detail="依次检查：① 网线/PoE 供电 ② 网卡 IP 与巨帧 ③ pylon Viewer 能否看到设备 "
                       "④ 是否被其他进程占用",
            )
        info = self._select(infos)
        try:
            cam = pylon.InstantCamera(tl.CreateDevice(info))
            cam.Open()
        except Exception as e:
            raise DriverError(
                f"打开相机失败: {e}", device_id=self.device_id,
                detail="常见原因：设备被占用、pylon 运行时与 pypylon 版本不匹配、GigE 心跳超时",
            ) from e
        self._cam = cam
        self.warnings.extend(self._apply(self.setup))
        return self._device_info(info, infos.index(info))

    def _close_device(self) -> None:
        cam, self._cam = self._cam, None
        if cam is None:
            return
        try:
            if cam.IsGrabbing():
                cam.StopGrabbing()
        except Exception:
            pass
        try:
            cam.Close()
        except Exception:
            pass

    def _enumerate(self) -> list:
        if self.simulate:
            return [{"index": 0, "model": "acA1920-40gm", "serial_number": "SIM-0001",
                     "user_name": "SIM-CAM-0", "interface": "GigE", "ip": "192.168.1.10"},
                    {"index": 1, "model": "acA2440-75um", "serial_number": "SIM-0002",
                     "user_name": "SIM-CAM-1", "interface": "USB3", "ip": ""}]
        pylon = self._import_pylon()
        try:
            infos = list(pylon.TlFactory.GetInstance().EnumerateDevices())
        except Exception as e:
            raise DriverError(f"枚举相机失败: {e}") from e
        return [self._device_info(info, i) for i, info in enumerate(infos)]

    @staticmethod
    def _device_info(info: Any, index: int = 0) -> dict:
        def pick(name: str) -> str:
            fn = getattr(info, name, None)
            if not callable(fn):
                return ""
            try:
                return str(fn() or "")
            except Exception:
                return ""

        return {"index": index, "model": pick("GetModelName"),
                "serial_number": pick("GetSerialNumber"),
                "user_name": pick("GetUserDefinedName"),
                "friendly_name": pick("GetFriendlyName"),
                "interface": pick("GetInterfaceClass") or ("GigE" if pick("GetIpAddress") else ""),
                "ip": pick("GetIpAddress"), "mac": pick("GetMacAddress")}

    def _select(self, infos: list) -> Any:
        sel = self.selector or {}
        serial = str(sel.get("serial_number") or "").strip()
        user_name = str(sel.get("user_name") or "").strip()
        index = sel.get("index")
        if serial:
            for i, info in enumerate(infos):
                if self._device_info(info, i)["serial_number"] == serial:
                    return info
            raise DriverError(f"未找到序列号为 {serial} 的相机",
                              detail="在线相机：" + ", ".join(
                                  self._device_info(i2, i)["serial_number"]
                                  for i, i2 in enumerate(infos)))
        if user_name:
            for i, info in enumerate(infos):
                if self._device_info(info, i)["user_name"] == user_name:
                    return info
            raise DriverError(f"未找到用户自定义名为 {user_name} 的相机")
        idx = 0 if index in (None, "") else int(index)
        if not (0 <= idx < len(infos)):
            raise DriverError(f"相机序号越界: {idx}",
                              detail=f"当前在线 {len(infos)} 台，序号范围 0..{len(infos) - 1}")
        return infos[idx]

    def _apply(self, setup: dict) -> list:
        warnings: list = []
        cam = self._cam
        if cam is None:
            return warnings
        self._set("ExposureAuto", setup["exposure_auto"], warnings)
        self._set("GainAuto", setup["gain_auto"], warnings)
        self._set("OffsetX", 0, warnings)
        self._set("OffsetY", 0, warnings)
        self._set("Width", setup["width"], warnings)
        self._set("Height", setup["height"], warnings)
        if setup.get("offset_x") or setup.get("offset_y"):
            self._set("OffsetX", setup["offset_x"], warnings)
            self._set("OffsetY", setup["offset_y"], warnings)
        self._set("PixelFormat", setup["pixel_format"], warnings)
        self._set("ExposureTime", float(setup["exposure_us"]), warnings)
        if setup.get("gain_db") is not None:
            if not self._set("Gain", float(setup["gain_db"]), warnings):
                self._set("GainRaw", float(setup["gain_db"]), warnings)
        if setup.get("frame_rate") is not None:
            self._set("AcquisitionFrameRateEnable", True, warnings)
            self._set("AcquisitionFrameRate", float(setup["frame_rate"]), warnings)
        self._set("TriggerSelector", "FrameStart", warnings)
        self._set("TriggerMode", setup["trigger_mode"], warnings)
        if str(setup["trigger_mode"]).lower() == "on":
            self._set("TriggerSource", setup["trigger_source"], warnings)
        return warnings

    def _set(self, name: str, value: Any, warnings: list) -> bool:
        if value is None:
            return False
        try:
            node = getattr(self._cam, name)
        except Exception:
            warnings.append(f"{name} 节点不存在（本型号不支持）")
            return False
        try:
            node.SetValue(value)
            return True
        except Exception as e:
            warnings.append(f"{name}={value} 置值失败: {e}")
            return False

    def _fetch(self, setup: dict, timeout_ms: int) -> tuple:
        if self.simulate:
            self.commands.append(f"Grab({setup['pattern']},{setup['width']}x{setup['height']})")
            pixels = simulate_frame(setup["width"], setup["height"], setup["pattern"],
                                    setup["level"], setup["noise"], self.frame_count)
            return pixels, {"width": setup["width"], "height": setup["height"],
                            "pixel_format": setup["pixel_format"],
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "exposure_us": setup["exposure_us"],
                            "gain_db": setup.get("gain_db")}
        pylon = self._pylon
        cam = self._cam
        if pylon is None or cam is None:
            raise DriverError("相机尚未打开", device_id=self.device_id)
        try:
            if not cam.IsGrabbing():
                cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
                self.grabbing = True
            grab = cam.RetrieveResult(int(timeout_ms), pylon.TimeoutHandling_ThrowException)
        except Exception as e:
            raise DriverError(f"取帧超时或失败: {e}", device_id=self.device_id,
                              detail=f"timeout={timeout_ms} ms；检查曝光时长、触发模式与网卡丢包") from e
        try:
            if not grab.GrabSucceeded():
                raise DriverError("取帧失败: " + str(grab.GetErrorDescription()),
                                  device_id=self.device_id)
            return grab.GetArray(), {
                "width": int(grab.GetWidth()), "height": int(grab.GetHeight()),
                "pixel_format": str(grab.GetPixelType()),
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "exposure_us": float(setup["exposure_us"]),
                "gain_db": setup.get("gain_db"),
                "frame_number": int(grab.GetFrameNumber()) if hasattr(grab, "GetFrameNumber") else 0,
            }
        finally:
            try:
                grab.Release()
            except Exception:
                pass

    def _save(self, path: Path, pixels: Any, fmt: str) -> None:
        array = pixels
        try:
            import numpy as np  # type: ignore
            if isinstance(array, np.ndarray):
                if array.dtype != np.uint8:
                    top = float(array.max()) or 1.0
                    array = (array.astype("float32") / top * 255.0).clip(0, 255).astype("uint8")
                if array.ndim == 3 and array.shape[2] >= 3:
                    array = array[:, :, :3]
        except Exception:
            pass
        write_png(path, array)

    def _software_trigger(self) -> None:
        if self._cam is None:
            raise DriverError("相机尚未打开", device_id=self.device_id)
        if str(self.setup["trigger_mode"]).lower() != "on":
            raise DriverError("软件触发前需先打开触发模式", device_id=self.device_id,
                              detail='先 configure(trigger_mode="On", trigger_source="Software")')
        try:
            self._cam.TriggerSoftware.Execute()
        except Exception as e:
            raise DriverError(f"软件触发失败: {e}") from e


#: 便于工厂与文档引用的别名
BaslerPylonCamera = CameraDriver
