"""相机层：Basler 真机（pypylon 惰性导入）与仿真源共用同一套接口，采集在独立线程完成。

设计要点：
    1. pypylon 只在真正连接时导入，未装驱动的机器仍可运行仿真模式；
    2. 采集线程只写模型引用，不触碰任何控件——界面用定时器主动拉取，避免跨线程操作；
    3. 真机参数下发失败不阻断连接，失败项收集为告警，由界面提示。
"""

from __future__ import annotations

import math
import struct
import threading
import time
import zlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .models import CameraModel, CameraParams, Frame


class CameraError(RuntimeError):
    """相机连接、取帧、参数下发失败。"""


# ---------------------------------------------------------------- 图像落盘


def write_png(path: str | Path, array: np.ndarray) -> Path:
    """把 uint8 灰度图写成 8 位 PNG，不依赖 PIL / OpenCV。"""
    arr = np.ascontiguousarray(array.astype(np.uint8))
    if arr.ndim != 2:
        raise CameraError("仅支持二维灰度图落盘")
    height, width = arr.shape
    raw = b"".join(b"\x00" + arr[y].tobytes() for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    payload = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR", ihdr)
               + chunk(b"IDAT", zlib.compress(raw, 6))
               + chunk(b"IEND", b""))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


# ---------------------------------------------------------------- 抽象接口


class CameraBackend(ABC):
    """相机后端：真机与仿真实现同一接口，Presenter 不关心差异。"""

    name = "camera"
    simulated = False

    @abstractmethod
    def open(self) -> dict:
        """打开设备，返回 {model, serial}。"""

    @abstractmethod
    def close(self) -> None:
        ...

    @abstractmethod
    def apply(self, params: CameraParams) -> list:
        """下发参数，返回告警列表（空表示全部成功）。"""

    @abstractmethod
    def grab(self) -> np.ndarray:
        """取一帧 uint8 灰度图，失败抛 CameraError。"""


# ---------------------------------------------------------------- 仿真相机


class SimulatedCamera(CameraBackend):
    """确定性仿真源：背景梯度 + 三个漂移高斯光斑 + 固定种子噪声。

    同一帧号的内容完全一致，便于算法回归；曝光时间改变整体亮度，用于验证曝光控制链路。
    """

    simulated = True
    name = "simulated"

    def __init__(self, params: CameraParams, spots: int = 3) -> None:
        self._params = params
        self._spots = spots
        self._index = 0
        self._open = False

    def open(self) -> dict:
        self._open = True
        return {"model": "Simulated Basler acA640-120gm", "serial": "SIM-0001"}

    def close(self) -> None:
        self._open = False

    def apply(self, params: CameraParams) -> list:
        self._params = params
        return []

    def grab(self) -> np.ndarray:
        if not self._open:
            raise CameraError("仿真相机未打开")
        p = self._params
        h, w = int(p.height), int(p.width)
        idx = self._index
        self._index += 1

        rng = np.random.default_rng(1000 + idx)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

        amp = float(np.clip(14.0 + 232.0 * (p.exposure_us / 8000.0) ** 0.65, 8.0, 255.0))
        img = 14.0 + 10.0 * (xx / max(w - 1, 1)) + (2.0 * p.gain_db)

        for k in range(self._spots):
            cx = w * (0.26 + 0.20 * k) + w * 0.05 * math.sin(0.05 * idx + 2.1 * k)
            cy = h * (0.48 + 0.06 * math.cos(0.042 * idx + 1.7 * k))
            sx = 9.0 + 3.0 * k
            sy = 11.0 - 2.0 * k
            a = amp * (0.92 - 0.16 * k)
            img += a * np.exp(-(((xx - cx) ** 2) / (2.0 * sx * sx)
                                + ((yy - cy) ** 2) / (2.0 * sy * sy)))

        img += rng.normal(0.0, 2.6, size=(h, w))
        return np.clip(img, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- Basler 真机


class PylonCamera(CameraBackend):
    """Basler 相机（pypylon）。当前仅按 Mono8 采集，其它像素格式会回退并给出告警。"""

    name = "pylon"
    simulated = False

    def __init__(self) -> None:
        self._pylon = None
        self._cam = None
        self._info = {"model": "-", "serial": "-"}

    # ---- 内部
    def _import(self):
        if self._pylon is not None:
            return self._pylon
        try:
            from pypylon import pylon  # 惰性导入：无驱动环境仍可运行仿真
        except Exception as exc:  # noqa: BLE001
            raise CameraError("未安装 pypylon（pip install pypylon），无法使用真机") from exc
        self._pylon = pylon
        return pylon

    def _set(self, node: str, value, warnings: list) -> None:
        try:
            getattr(self._cam, node).SetValue(value)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{node}={value} 未生效：{exc}")

    # ---- 接口
    def open(self) -> dict:
        pylon = self._import()
        try:
            factory = pylon.TlFactory.GetInstance()
            devices = factory.EnumerateDevices()
            if not devices:
                raise CameraError("未发现 Basler 相机（检查网线、供电与 IP 配置）")
            device = devices[0]
            self._info = {"model": device.GetModelName(), "serial": device.GetSerialNumber()}
            self._cam = pylon.InstantCamera(factory.CreateDevice(device))
            self._cam.Open()
        except CameraError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CameraError(f"打开 Basler 相机失败：{exc}") from exc
        return dict(self._info)

    def close(self) -> None:
        if self._cam is None:
            return
        try:
            if self._cam.IsGrabbing():
                self._cam.StopGrabbing()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._cam.Close()
        except Exception:  # noqa: BLE001
            pass
        self._cam = None

    def apply(self, params: CameraParams) -> list:
        if self._cam is None:
            raise CameraError("相机未打开")
        warnings: list = []
        p = params.clamped()

        # 几何：先尺寸后偏移，越界项单独降级
        self._set("Width", p.width, warnings)
        self._set("Height", p.height, warnings)
        self._set("OffsetX", p.offset_x, warnings)
        self._set("OffsetY", p.offset_y, warnings)

        if p.pixel_format != "Mono8":
            warnings.append(f"像素格式 {p.pixel_format} 暂不支持，按 Mono8 采集")
        self._set("PixelFormat", "Mono8", warnings)

        # 曝光与增益：先关自动，再写目标值
        self._set("ExposureAuto", "Off" if not p.auto_exposure else "Continuous", warnings)
        if not p.auto_exposure:
            self._set("ExposureTime", float(p.exposure_us), warnings)
        self._set("GainAuto", "Off", warnings)
        self._set("Gain", float(p.gain_db), warnings)

        # 帧率与触发
        self._set("AcquisitionFrameRateEnable", True, warnings)
        self._set("AcquisitionFrameRate", float(p.frame_rate), warnings)
        if p.trigger_mode == "software":
            self._set("TriggerMode", "On", warnings)
            self._set("TriggerSource", "Software", warnings)
        else:
            self._set("TriggerMode", "Off", warnings)
        return warnings

    def grab(self) -> np.ndarray:
        if self._cam is None:
            raise CameraError("相机未打开")
        pylon = self._import()
        if not self._cam.IsGrabbing():
            self._cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
        if self._cam.WaitForFrameTriggerReady(200, pylon.TimeoutHandling_Return) is False:
            pass
        result = self._cam.RetrieveResult(2000, pylon.TimeoutHandling_Return)
        if result is None:
            raise CameraError("取帧超时（2000 ms）")
        try:
            if not result.GrabSucceeded():
                raise CameraError(f"取帧失败：{result.GetErrorCode()} {result.GetErrorDescription()}")
            width, height = int(result.GetWidth()), int(result.GetHeight())
            buffer = result.GetBuffer()
            return np.frombuffer(buffer, dtype=np.uint8, count=width * height).reshape(height, width).copy()
        finally:
            result.Release()


# ---------------------------------------------------------------- 采集线程


class CameraWorker(threading.Thread):
    """采集线程：持续取帧写入模型；仿真后端按设定帧率限速，真机由硬件节拍控制。"""

    def __init__(self, backend: CameraBackend, model: CameraModel) -> None:
        super().__init__(name="camera-grabber", daemon=True)
        self._backend = backend
        self._model = model
        self._stop = threading.Event()
        self._index = 0
        self._stamps: list = []

    def stop(self) -> None:
        self._stop.set()

    def _fps(self) -> float:
        now = time.time()
        self._stamps = [t for t in self._stamps if now - t <= 1.0]
        return float(len(self._stamps))

    def run(self) -> None:
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                array = self._backend.grab()
                self._index += 1
                self._model.push_frame(Frame(index=self._index, timestamp=time.time(), array=array))
                self._stamps.append(time.time())
                self._model.set_fps(self._fps())
            except Exception as exc:  # noqa: BLE001
                self._model.set_error(str(exc))
                time.sleep(0.2)
                continue

            if self._backend.simulated:
                budget = 1.0 / max(self._model.params.frame_rate, 0.5)
                remain = budget - (time.perf_counter() - started)
                if remain > 0:
                    self._stop.wait(remain)


# ---------------------------------------------------------------- 服务编排


class CameraService:
    """连接、参数下发、采集启停、落盘。Presenter 只与本类交互。"""

    def __init__(self, model: CameraModel,
                 backend_factory: Optional[Callable[[CameraParams], CameraBackend]] = None) -> None:
        self._model = model
        self._factory = backend_factory
        self._backend: Optional[CameraBackend] = None
        self._worker: Optional[CameraWorker] = None

    @property
    def backend(self) -> Optional[CameraBackend]:
        return self._backend

    @property
    def running(self) -> bool:
        return bool(self._worker and self._worker.is_alive())

    def connect(self, params: CameraParams, prefer_real: bool = True) -> tuple:
        """连接相机。真机失败自动降级仿真，返回 (是否仿真, 提示信息)。"""
        self.disconnect()
        note = ""
        backend: Optional[CameraBackend] = None

        if self._factory is not None:
            backend = self._factory(params)
            try:
                backend.open()
            except Exception as exc:  # noqa: BLE001
                raise CameraError(f"后端打开失败：{exc}") from exc
        elif prefer_real:
            real = PylonCamera()
            try:
                real.open()
                real.apply(params)
                backend = real
            except CameraError as exc:
                note = f"真机不可用，已切换仿真：{exc}"
                real.close()

        if backend is None:
            backend = SimulatedCamera(params)
            backend.open()
            if not note:
                note = "当前为仿真模式，帧数据由内置模型生成"

        try:
            backend.apply(params)
        except Exception as exc:  # noqa: BLE001
            backend.close()
            raise CameraError(f"参数下发失败：{exc}") from exc

        info = getattr(backend, "_info", None) or {}
        model_name = info.get("model") or ("Simulated Basler acA640-120gm" if backend.simulated else "-")
        serial = info.get("serial") or ("SIM-0001" if backend.simulated else "-")
        self._backend = backend
        self._model.set_connected(connected=True, simulated=backend.simulated,
                                  model=model_name, serial=serial)
        return backend.simulated, note

    def disconnect(self) -> None:
        self.stop()
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception:  # noqa: BLE001
                pass
        self._backend = None
        self._model.set_connected(connected=False)

    def start(self) -> None:
        if self._backend is None:
            raise CameraError("尚未连接相机")
        if self.running:
            return
        if self._backend is not None and not self._backend.simulated:
            p = self._model.params
            if p.trigger_mode == "software":
                self._backend.apply(p)
        self._worker = CameraWorker(self._backend, self._model)
        self._worker.start()
        self._model.set_grabbing(True)

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker.join(timeout=2.0)
            self._worker = None
        self._model.set_grabbing(False)

    def apply_params(self, params: CameraParams) -> list:
        if self._backend is None:
            return ["未连接相机，参数仅保存于界面"]
        return self._backend.apply(params)

    def save_frame(self, path: str | Path) -> Path:
        frame = self._model.latest
        if frame is None:
            raise CameraError("尚无可用帧")
        return write_png(path, frame.array)
