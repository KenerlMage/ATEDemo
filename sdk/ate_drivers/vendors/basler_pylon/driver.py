# -*- coding: utf-8 -*-
"""Basler 工业相机驱动（pylon 8.0.0 / pypylon 8.x）

**只填差异面**：节点名与置值顺序、设备枚举、取帧方式、像素格式处理。
动作语义、统计口径、仿真模型、返回结构全部来自家族层 `CameraDriver`。

## 版本基线

* 运行时：Basler pylon Software Suite **8.0.0**（pylon Viewer 能看到的设备本驱动都能列）；
* Python 绑定：`pypylon`（与运行时同基线，8.x）。驱动启动时把两个版本都记进 `state()`；
* 兼容策略：节点缺失只记 `warnings`，不抛错（如部分 dart/boost 型号没有 `Gain`）；
  但**取帧失败、设备被占用、pypylon 缺失**属于硬错误，一律抛出带处置建议的 `DriverError`。

## 为什么不用 SCPI 链路

相机不是「命令—应答」设备：控制面是 GenICam 节点（`Width` / `ExposureTime` / `Gain`），
数据面是帧缓冲。因此本驱动不经过 `make_transport()`，而是直接持有 `InstantCamera`；
`CameraChannel` 只承担动作日志，保证报告里能看到「配置了什么、取了几帧」。

## 置值顺序（真机上很关键）

1. 先关自动（`ExposureAuto` / `GainAuto` = Off），否则手动置值会被自动算法覆盖；
2. 再把 ROI 偏移归零，**先设宽高、后设偏移**（偏移不归零时大尺寸会超传感器边界）；
3. 然后才是像素格式、曝光、增益；
4. 触发相关设置放在最后（`TriggerSelector=FrameStart` 先选帧起始）；
5. 帧率使能要在尺寸稳定之后，否则 pylon 会按旧尺寸算最大帧率。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ...errors import DriverError
from ...family.camera import CameraDriver, write_png

__all__ = ["BaslerPylonCamera"]

#: pylon 运行时与 pypylon 的目标基线
PYLON_BASELINE = "8.0.0"


class BaslerPylonCamera(CameraDriver):
    """Basler 工业相机（GigE / USB3），基于 pylon 8.0.0 的 pypylon 绑定"""

    driver_key = "basler-pylon"
    family = "camera"
    vendor = "Basler"
    label = "工业相机 · Basler pylon 8.0.0"
    driver_version = "1.0.0"
    INTERFACES = ("USB", "GIGE")
    BACKENDS = ("native",)
    sim_idn = f"BASLER,acA1920-40gm,SIM0001,PYLON-{PYLON_BASELINE}"

    #: 型号派发唯一依据（与 driver.json 的 models 必须一致，上架门禁会校验）
    MODELS = (
        "acA640-90gm", "acA1300-60gm", "acA1920-40gm", "acA1920-48gm",
        "acA2440-75um", "acA2500-14gm",
        "a2A1920-160umBAS", "a2A2590-60umBAS", "a2A3840-45umBAS",
        "daA1920-160um", "daA2500-60um", "boA1936-400um",
    )
    MODEL_ALIASES = ("BASLER",)

    def __init__(self, *args: Any, **kw: Any):
        super().__init__(*args, **kw)
        self._pylon: Any = None
        self._cam: Any = None

    # ------------------------------------------------------------ pylon 装载

    def _import_pylon(self) -> Any:
        """惰性导入 pypylon：仿真模式完全不碰它，装了没装都不影响跑流程"""
        if self._pylon is not None:
            return self._pylon
        try:
            from pypylon import pylon  # type: ignore
        except ImportError as e:
            raise DriverError(
                "未安装 pypylon（Basler 相机需要 pylon 8.0.0 运行时 + pypylon Python 包）",
                device_id=self.device_id,
                detail="① 安装 Basler pylon Software Suite 8.0.0（含驱动与 pylon Viewer）；"
                       "② pip install pypylon（版本与运行时同基线）；"
                       "③ 用 pylon Viewer 确认能看到相机；④ 以 mode=\"real\" 打开。"
                       "当前环境未检测到 pypylon，仿真模式不受影响。",
            ) from e
        self._pylon = pylon
        return pylon

    def _version_info(self) -> dict:
        try:
            pylon = self._import_pylon()
        except DriverError:
            return {"pypylon": "", "pylon_runtime": "", "baseline": PYLON_BASELINE}
        return {
            "pypylon": str(getattr(pylon, "__version__", "") or ""),
            "pylon_runtime": str(getattr(pylon, "PYLON_VERSION_STRING", "")
                                 or getattr(pylon, "PYLON_VERSION", "") or ""),
            "baseline": PYLON_BASELINE,
        }

    # ------------------------------------------------------------ 会话钩子

    def _open_device(self) -> dict:
        if self.simulate:                      # 仿真模式永不触碰 pypylon
            return super()._open_device()
        pylon = self._import_pylon()
        tl = pylon.TlFactory.GetInstance()
        try:
            infos = list(tl.EnumerateDevices())
        except Exception as e:
            raise DriverError(f"枚举相机失败: {e}", device_id=self.device_id,
                              detail="检查 GigE / USB3 连接、PoE 供电、网卡巨帧与防火墙") from e
        if not infos:
            raise DriverError(
                "未发现 Basler 相机",
                device_id=self.device_id,
                detail="依次检查：① 网线/PoE 供电 ② 网卡 IP 与巨帧 ③ pylon Viewer 能否看到设备 "
                       "④ 是否被其他进程（pylon Viewer / 上一轮未退出的 Runner）占用",
            )
        info = self._select(infos)
        try:
            cam = pylon.InstantCamera(tl.CreateDevice(info))
            cam.Open()
        except Exception as e:
            raise DriverError(
                f"打开相机失败: {e}", device_id=self.device_id,
                detail="常见原因：设备被其他进程占用、pylon 运行时与 pypylon 版本不匹配、"
                       "GigE 相机心跳超时（检查网卡驱动与巨型帧设置）",
            ) from e
        self._cam = cam
        self.grabbing = False
        self.warnings.extend(self._apply(self.setup))
        return self._device_info(info, infos.index(info))

    def _close_device(self) -> None:
        if self.simulate:
            return super()._close_device()
        cam = self._cam
        self._cam = None
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
        self.grabbing = False

    # ------------------------------------------------------------ 枚举与选机

    def _enumerate(self) -> list:
        if self.simulate:
            return super()._enumerate()
        pylon = self._import_pylon()
        try:
            infos = list(pylon.TlFactory.GetInstance().EnumerateDevices())
        except Exception as e:
            raise DriverError(f"枚举相机失败: {e}",
                              detail="检查 GigE / USB3 连接、PoE 供电、网卡巨帧与防火墙") from e
        return [self._device_info(info, i) for i, info in enumerate(infos)]

    def _device_info(self, info: Any, index: int = 0) -> dict:
        """把 pylon 的 DeviceInfo 归一成普通字典（任何字段取不到都不抛错）"""
        def pick(fn_name: str) -> str:
            fn = getattr(info, fn_name, None)
            if not callable(fn):
                return ""
            try:
                value = fn()
                return "" if value is None else str(value)
            except Exception:
                return ""

        return {
            "index": index,
            "model": pick("GetModelName"),
            "serial_number": pick("GetSerialNumber"),
            "user_name": pick("GetUserDefinedName"),
            "friendly_name": pick("GetFriendlyName"),
            "device_class": pick("GetDeviceClass"),
            "interface": pick("GetInterfaceClass") or ("GigE" if pick("GetIpAddress") else ""),
            "ip": pick("GetIpAddress"),
            "mac": pick("GetMacAddress"),
        }

    def _select(self, infos: list) -> Any:
        """按 序列号 → 用户自定义名 → 序号 选机（不做模糊匹配）"""
        sel = self.selector or {}
        serial = str(sel.get("serial_number") or "").strip()
        user_name = str(sel.get("user_name") or "").strip()
        index = sel.get("index")
        if serial:
            for i, info in enumerate(infos):
                if self._device_info(info, i)["serial_number"] == serial:
                    return info
            found = ", ".join(self._device_info(i2, i)["serial_number"] for i, i2 in enumerate(infos))
            raise DriverError(f"未找到序列号为 {serial} 的相机",
                              detail=f"当前在线相机序列号: {found}")
        if user_name:
            for i, info in enumerate(infos):
                if self._device_info(info, i)["user_name"] == user_name:
                    return info
            raise DriverError(f"未找到用户自定义名为 {user_name} 的相机",
                              detail="可在 pylon Viewer 里给相机设置 UserDefinedName 后重试")
        idx = 0 if index in (None, "") else int(index)
        if not (0 <= idx < len(infos)):
            raise DriverError(f"相机序号越界: {idx}",
                              detail=f"当前在线 {len(infos)} 台，序号范围 0..{len(infos) - 1}")
        return infos[idx]

    # ------------------------------------------------------------ 参数下发

    def _apply(self, setup) -> list:
        """把 `CameraSetup` 翻译成 GenICam 节点（顺序见模块文档）"""
        warnings: list = []
        cam = self._cam
        if cam is None:
            return warnings

        self._set("ExposureAuto", setup.exposure_auto or "Off", warnings)
        self._set("GainAuto", setup.gain_auto or "Off", warnings)

        # ROI：偏移先归零 → 宽高 → 再按需设偏移
        self._set("OffsetX", 0, warnings)
        self._set("OffsetY", 0, warnings)
        self._set("Width", setup.width, warnings)
        self._set("Height", setup.height, warnings)
        if setup.offset_x or setup.offset_y:
            self._set("OffsetX", setup.offset_x, warnings)
            self._set("OffsetY", setup.offset_y, warnings)

        self._set("PixelFormat", setup.pixel_format, warnings)
        self._set("ExposureTime", float(setup.exposure_us), warnings)
        if setup.gain_db is not None:
            if not self._set("Gain", float(setup.gain_db), warnings):
                self._set("GainRaw", float(setup.gain_db), warnings)
        if setup.frame_rate is not None:
            self._set("AcquisitionFrameRateEnable", True, warnings)
            self._set("AcquisitionFrameRate", float(setup.frame_rate), warnings)

        # 触发：先选帧起始，再决定开关与源
        self._set("TriggerSelector", "FrameStart", warnings)
        self._set("TriggerMode", setup.trigger_mode or "Off", warnings)
        if str(setup.trigger_mode).lower() == "on":
            self._set("TriggerSource", setup.trigger_source or "Software", warnings)
        return warnings

    def _set(self, name: str, value: Any, warnings: list) -> bool:
        """安全置值：节点不存在或拒绝置值都只记警告（不同机型能力不同）"""
        if value is None:
            return False
        try:
            node = getattr(self._cam, name)
        except AttributeError:
            warnings.append(f"{name} 节点不存在（本型号不支持）")
            return False
        except Exception as e:
            warnings.append(f"{name} 取节点失败: {e}")
            return False
        try:
            if isinstance(value, bool):
                node.SetValue(value)
            elif isinstance(value, (int, float)):
                node.SetValue(value)
            else:
                node.SetValue(str(value))
            return True
        except Exception as e:
            warnings.append(f"{name}={value} 置值失败: {e}")
            return False

    # ------------------------------------------------------------ 取帧

    def _fetch(self, setup, timeout_ms: int) -> tuple:
        if self.simulate:
            return super()._fetch(setup, timeout_ms)
        pylon = self._pylon
        cam = self._cam
        if pylon is None or cam is None:
            raise DriverError("相机尚未打开", device_id=self.device_id,
                              detail="先 open()，或用 with open_camera(...) as cam 的写法")
        try:
            if not cam.IsGrabbing():
                cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
                self.grabbing = True
            grab = cam.RetrieveResult(int(timeout_ms), pylon.TimeoutHandling_ThrowException)
        except Exception as e:
            raise DriverError(
                f"取帧超时或失败: {e}", device_id=self.device_id,
                detail=f"timeout={timeout_ms} ms；检查曝光时间是否长于超时、触发模式是否在等外部信号、"
                       "网卡是否丢包",
            ) from e
        try:
            if not grab.GrabSucceeded():
                raise DriverError("取帧失败: " + str(grab.GetErrorDescription()),
                                  device_id=self.device_id)
            meta = {
                "width": int(grab.GetWidth()),
                "height": int(grab.GetHeight()),
                "pixel_format": str(grab.GetPixelType()),
                "timestamp": _now_ms(),
                "exposure_us": float(setup.exposure_us),
                "gain_db": setup.gain_db,
                "frame_number": int(grab.GetFrameNumber()) if hasattr(grab, "GetFrameNumber") else 0,
                "stride": int(grab.GetStride()) if hasattr(grab, "GetStride") else 0,
            }
            return grab.GetArray(), meta
        finally:
            try:
                grab.Release()
            except Exception:
                pass

    def _save(self, path: Path, pixels: Any, fmt: str) -> None:
        """落盘：真机帧可能是 uint16 / Bayer，先归一到 8 位再写 PNG"""
        array = pixels
        try:
            import numpy as np  # type: ignore
            if isinstance(array, np.ndarray):
                if array.dtype != np.uint8:
                    top = float(array.max()) or 1.0
                    array = (array.astype("float32") / top * 255.0).clip(0, 255).astype("uint8")
                if array.ndim == 3 and array.shape[2] >= 3:
                    array = array[:, :, :3]
        except Exception:  # numpy 不可用或转换失败：退回原样写（至少不丢帧）
            pass
        write_png(path, array)

    def _software_trigger(self) -> None:
        cam = self._cam
        if cam is None:
            raise DriverError("相机尚未打开", device_id=self.device_id)
        if str(self.setup.trigger_mode).lower() != "on":
            raise DriverError(
                "软件触发前需先打开触发模式",
                device_id=self.device_id,
                detail='先 configure(trigger_mode="On", trigger_source="Software")，再 trigger()',
            )
        try:
            cam.TriggerSoftware.Execute()
        except Exception as e:
            raise DriverError(f"软件触发失败: {e}", device_id=self.device_id,
                              detail="确认 TriggerSelector=FrameStart 且 TriggerSource=Software") from e

    # ------------------------------------------------------------ 状态补充

    def state(self) -> dict:
        out = super().state()
        out["pylon"] = self._version_info()
        out["models"] = list(self.MODELS)
        return out

    def identify(self) -> dict:
        if self.simulate:
            return {"value": self.sim_idn, "quality": "simulated",
                    "device": {"model": self.cfg.model or "SIM-CAMERA",
                               "serial_number": "SN-SIM-0001",
                               "vendor": f"Basler(SIM pylon {PYLON_BASELINE})"},
                    "pylon": {"pypylon": "", "pylon_runtime": "SIM", "baseline": PYLON_BASELINE}}
        return {"value": self._identity_real(), "quality": "good",
                "device": dict(self.device), "pylon": self._version_info()}


def _now_ms() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
