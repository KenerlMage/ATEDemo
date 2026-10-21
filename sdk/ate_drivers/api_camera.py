# -*- coding: utf-8 -*-
"""相机通用顶层对象：型号直连，GenICam 节点不外露

用户代码只需要这一层：

    from ate_drivers import open_camera

    with open_camera("acA1920-40gm", mode="simulate") as cam:
        cam.configure(exposure_us=8000, pattern="checker")
        stats = cam.grab()["value"]["stats"]
        assert stats["contrast"] > 100, stats
        cam.snap("run_001/cam_checker.png")

设计约束与示波器一致：

* **型号决定驱动**（无打分、无模糊匹配）；
* 方法返回统一结构 `{ok, action, value, detail, quality, simulated, elapsed_ms}`；
* 仿真与真机**同一个入口**，TPS 不必写两套分支；
* 参数名一律用业务语义（`exposure_us` / `pattern`），不暴露 `SetValue` 这类节点调用。
"""

from __future__ import annotations

from typing import Any, Optional

from .errors import ConfigurationError, DriverError
from .models import DeviceConfig

__all__ = ["Camera"]


class Camera:
    """一台工业相机的通用顶层对象"""

    kind = "camera"

    def __init__(self, model: Any = None, *, serial_number: str = "", user_name: str = "",
                 index: Any = 0, mode: str = "simulate", timeout: Optional[float] = 3.0,
                 setup: Optional[dict] = None, transport: Any = None, alias: str = "",
                 device_id: str = "", name: str = "", open: bool = False) -> None:
        if not model:
            raise ConfigurationError(
                "必须指定相机型号",
                detail="型号来自设备档案的 model 字段；未登记型号会直接报错并列出现有型号")
        from . import camera_factory

        self._entry = camera_factory.camera_entry(model)      # 未登记在此报错
        self._cls = camera_factory.camera_class(model)
        self._model = self._entry["model"]
        self._mode = "real" if str(mode).lower() == "real" else "simulate"

        extra: dict = {}
        if serial_number:
            extra["serial_number"] = str(serial_number)
        if user_name:
            extra["user_name"] = str(user_name)
        if index is not None:
            extra["index"] = index
        if setup:
            extra["camera"] = dict(setup)

        cfg = DeviceConfig(
            device_id=device_id or self._model,
            name=name or self._model,
            model=self._model,
            vendor=self._entry["vendor"],
            category=self._entry["label"] or "camera",
            role="camera",
            interface=(self._entry.get("interfaces") or ("USB",))[0],
            programmable=True,
            timeout=float(timeout if timeout is not None else 3.0),
            extra=extra,
        )
        self._driver = self._cls(cfg, mode=self._mode, timeout=cfg.timeout,
                                 transport=transport, alias=alias, setup=setup)
        if open:
            self.connect()

    # ------------------------------------------------------------ 元信息

    @property
    def model(self) -> str:
        return self._model

    @property
    def vendor(self) -> str:
        return self._entry["vendor"]

    @property
    def label(self) -> str:
        return self._entry["label"]

    @property
    def driver(self) -> str:
        return self._entry["driver"]

    @property
    def interfaces(self) -> tuple:
        return tuple(self._entry.get("interfaces") or ())

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def simulated(self) -> bool:
        return self._mode != "real"

    @property
    def setup(self) -> dict:
        return self._driver.setup.to_dict()

    def metadata(self) -> dict:
        return {
            "model": self._model,
            "vendor": self.vendor,
            "label": self.label,
            "driver": self.driver,
            "version": self._entry["version"],
            "interfaces": list(self.interfaces),
            "mode": self._mode,
            "simulated": self.simulated,
            "capabilities": list(self._entry.get("capabilities") or ()),
            "setup": self.setup,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return "<Camera %s %s mode=%s>" % (self._model, self.driver, self._mode)

    # ------------------------------------------------------------ 会话

    def connect(self) -> dict:
        return self._driver.open()

    def close(self) -> dict:
        return self._driver.close()

    def __enter__(self) -> "Camera":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.close()
        except DriverError:
            pass

    # ------------------------------------------------------------ 动作

    def identity(self) -> dict:
        return self._do("identify")

    def reset(self) -> dict:
        return self._do("reset")

    def enumerate(self) -> dict:
        """枚举在线相机（真机需要 pypylon；仿真返回两台示例设备）"""
        return self._do("camera.enumerate")

    def configure(self, **params: Any) -> dict:
        """设置采集参数：width/height/offset_x/offset_y/pixel_format/exposure_us/
        gain_db/gain_raw/frame_rate/trigger_mode/trigger_source/timeout_ms/
        pattern/level/noise"""
        return self._do("camera.configure", **params)

    def grab(self, timeout_ms: Optional[int] = None, thumbnail: bool = True,
             region: Any = None) -> dict:
        return self._do("camera.grab", timeout_ms=timeout_ms, thumbnail=thumbnail, region=region)

    def snap(self, path: str, format: str = "png", timeout_ms: Optional[int] = None,
             region: Any = None) -> dict:
        return self._do("camera.snap", path=path, format=format,
                        timeout_ms=timeout_ms, region=region)

    def sequence(self, count: int = 5, interval_ms: float = 0.0, save_dir: str = "",
                 prefix: str = "frame", region: Any = None) -> dict:
        return self._do("camera.sequence", count=count, interval_ms=interval_ms,
                        save_dir=save_dir, prefix=prefix, region=region)

    def stats(self, region: Any = None) -> dict:
        return self._do("camera.stats", region=region)

    def trigger(self, timeout_ms: Optional[int] = None) -> dict:
        return self._do("camera.trigger", timeout_ms=timeout_ms)

    def status(self) -> dict:
        return {
            "model": self._model,
            "driver": self.driver,
            "mode": self._mode,
            "state": self._driver.state(),
        }

    # ------------------------------------------------------------ 自检

    def self_test(self) -> dict:
        """轻量自检：识别 → 配置 → 取帧 → 统计，全流程走一遍（不落盘）"""
        checks: list = []

        def run(name: str, fn) -> None:
            try:
                value = fn()
                checks.append({"check": name, "ok": True, "value": value})
            except DriverError as e:
                checks.append({"check": name, "ok": False, "error": str(e),
                               "detail": getattr(e, "detail", "")})

        run("identify", lambda: self.identity()["value"])
        run("configure", lambda: self.configure(pattern="checker", level=160)["value"]["pattern"])
        run("grab", lambda: self.grab(thumbnail=False)["value"]["stats"]["mean"])
        run("stats", lambda: self.stats(region="center")["value"]["mean"])
        return {
            "ok": all(c["ok"] for c in checks),
            "model": self._model,
            "driver": self.driver,
            "mode": self._mode,
            "checks": checks,
        }

    # ------------------------------------------------------------ 内部

    def _do(self, action: str, **params: Any) -> dict:
        return self._driver.do(action, **params)
