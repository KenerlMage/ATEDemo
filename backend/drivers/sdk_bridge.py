# -*- coding: utf-8 -*-
"""SDK 驱动的运行期桥接层（Runner → ``sdk/ate_drivers``）。

驱动实现的唯一真源在 ``sdk/ate_drivers``。Runner 侧只保留装配职责，本模块解决
两件事：

1. **加载与参数转换**：按 ``module:Class`` 导入 SDK 驱动类；把 Runner 的
   ``DeviceConfig``（dataclass，多出 ``device_type`` / ``driver`` 两列）转成
   SDK 侧 ``DeviceConfig.from_dict()`` 认得的 dict。
2. **动作别名补齐**：Runner 的 TPS 沿用裸动作名（``grab`` / ``snap`` /
   ``measure``），SDK 的规范名是能力名（``camera.grab`` / ``scope.measure``）。
   示波器家族的 ``ALIASES`` 已覆盖裸名，相机家族没有，故在此统一补齐。

设计约束：

* **导入失败不阻断启动**：返回 ``None``，由工厂回落到内置实现，原因记在
  ``sdk_status()["errors"]``，可通过 ``registry_report()`` 查。
* **不改变动作语义**：只补别名，不改 ``ACTIONS`` 的方法指向。
* **不引入版本矩阵**：SDK 与 Runner 同包发布，不做 ``check_compatible`` 拦截。
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import Any, Optional

__all__ = ["sdk_driver_class", "sdk_status", "payload_of", "adapt"]

_SDK_READY = False
_SDK_ERROR = ""
_CACHE: dict[str, Optional[type]] = {}

#: 家族默认别名：Runner 裸动作名 -> SDK 能力名。仅对已声明的动作生效。
_FAMILY_ALIASES: dict[str, dict[str, str]] = {
    "camera": {
        "idn": "identify",
        "enumerate": "camera.enumerate",
        "configure": "camera.configure",
        "grab": "camera.grab",
        "snap": "camera.snap",
        "sequence": "camera.sequence",
        "stats": "camera.stats",
        "trigger": "camera.trigger",
    },
    "scope": {
        "idn": "identify",
        "autoset": "scope.autoset",
        "timebase": "scope.timebase",
        "channel": "scope.channel",
        "waveform": "scope.acquire_waveform",
        "measure": "scope.measure",
        "run": "scope.run",
        "stop": "scope.stop",
        "single": "scope.single",
    },
    "passive": {
        "idn": "identify",
        "describe": "passive.describe",
        "state": "state",
    },
}


def _sdk_root() -> Path:
    """``backend/drivers/`` 的上两级即仓库根，SDK 位于其 ``sdk/`` 子目录。"""
    return Path(__file__).resolve().parents[2] / "sdk"


def _ensure_ready() -> bool:
    """把 ``sdk/`` 加入 ``sys.path`` 并验证 ``ate_drivers`` 可导入（只做一次）。"""
    global _SDK_READY, _SDK_ERROR
    if _SDK_READY:
        return True
    if _SDK_ERROR:
        return False
    root = _sdk_root()
    if not root.is_dir():
        _SDK_ERROR = f"未找到 SDK 目录: {root}"
        return False
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        import ate_drivers  # noqa: F401  仅验证可导入
    except Exception as exc:  # pragma: no cover - 环境缺失时才会走到
        _SDK_ERROR = f"导入 ate_drivers 失败: {type(exc).__name__}: {exc}"
        return False
    _SDK_READY = True
    return True


def payload_of(cfg: Any) -> dict:
    """把 Runner 的设备配置转成 SDK 认的 dict。

    接受 ``DeviceConfig`` 实例、``sqlite3.Row``、普通 dict 与 ``None``。
    ``extra`` 内的 ``backend`` 会被 SDK 用作传输后端选择。
    """
    if cfg is None:
        return {}
    if isinstance(cfg, dict):
        return dict(cfg)
    if dataclasses.is_dataclass(cfg) and not isinstance(cfg, type):
        try:
            return dataclasses.asdict(cfg)
        except Exception:
            pass
    if hasattr(cfg, "keys"):  # sqlite3.Row
        try:
            return {key: cfg[key] for key in cfg.keys()}
        except Exception:
            pass
    data: dict[str, Any] = {}
    for name in (
        "bench_id", "device_id", "name", "model", "vendor", "category", "role",
        "device_type", "interface", "protocol", "host", "port", "serial_port",
        "baudrate", "address", "channel", "programmable", "required",
        "configured", "timeout", "driver", "note", "extra",
    ):
        if hasattr(cfg, name):
            data[name] = getattr(cfg, name)
    return data


def _init_with_payload(base: type, family: str):
    """生成 ``__init__``：先把 Runner 配置转 dict，再交给 SDK 基类。"""

    def __init__(self, cfg=None, mode: str = "simulate", timeout=None,
                 transport=None, alias: str = "", **kw):
        payload = payload_of(cfg)
        if family == "camera" and "setup" not in kw:
            setup = (payload.get("extra") or {}).get("setup") if isinstance(payload.get("extra"), dict) else None
            if setup:
                kw["setup"] = dict(setup)
        base.__init__(self, payload, mode=mode, timeout=timeout,
                      transport=transport, alias=alias, **kw)

    return __init__


def _setup_map_property():
    """相机适配子类的 ``setup_map`` 属性：把 SDK 的 ``CameraSetup`` 暴露成 dict。

    Runner 侧的 ``camera_tools`` 按字典访问采集参数；SDK 侧是 dataclass。
    本属性只做只读视图，写入仍走 ``configure()``，避免两份状态。
    """

    def getter(self):
        raw = getattr(self, "setup", None)
        if isinstance(raw, dict):
            return dict(raw)
        try:
            return dataclasses.asdict(raw)
        except Exception:
            return dict(getattr(raw, "__dict__", {}) or {})

    return property(getter)


def _camera_configure_compat(base: type):
    """相机适配子类的 ``configure``：保留 Runner 侧「亮度/噪声夹取」约定。

    SDK 对越界值一律拒绝（更严格也更好），但 Runner 的历史契约是夹取，
    前端滑块与旧脚本都可能传越界值。夹取只在相机家族生效，不污染其它家族。
    """

    def configure(self, **params):
        if params.get("level") is not None:
            params["level"] = max(0, min(255, int(params["level"])))
        if params.get("noise") is not None:
            params["noise"] = max(0.0, float(params["noise"]))
        return base.configure(self, **params)

    return configure


def adapt(cls: type, family: str) -> type:
    """为 SDK 驱动类生成 Runner 侧适配子类（别名 + 构造参数转换）。"""
    declared = set(getattr(cls, "ACTIONS", {}) or {})
    aliases = dict(getattr(cls, "ALIASES", {}) or {})
    for source, target in _FAMILY_ALIASES.get(family, {}).items():
        # 仅在 SDK 确实声明了该能力时才建别名，避免把不存在的能力塞进表里
        if target in declared and source not in aliases:
            aliases[source] = target
    name = f"Runner{cls.__name__}"
    attrs = {
        "__module__": cls.__module__,
        "__qualname__": name,
        "__doc__": f"Runner 桥接子类：{cls.__name__}（family={family}）",
        "ALIASES": aliases,
        "__init__": _init_with_payload(cls, family),
    }
    if family == "camera":
        attrs["setup_map"] = _setup_map_property()
        attrs["configure"] = _camera_configure_compat(cls)
    return type(name, (cls,), attrs)


def sdk_driver_class(entry: str, family: str, fallback: Optional[type] = None) -> Optional[type]:
    """按 ``module:Class`` 取 SDK 驱动类并适配；不可用时返回 ``fallback``。

    ``entry`` 与 ``driver.json`` 的 ``entry`` 字段同格式，便于清单与代码同源。
    """
    if entry in _CACHE:
        return _CACHE[entry] or fallback
    if not _ensure_ready():
        _CACHE[entry] = None
        return fallback
    module_name, _, class_name = entry.partition(":")
    try:
        module = __import__(module_name, fromlist=[class_name])
        cls = getattr(module, class_name)
    except Exception as exc:
        _CACHE[entry] = None
        _SDK_ERROR_LIST.append(f"{entry}: {type(exc).__name__}: {exc}")
        return fallback
    adapted = adapt(cls, family)
    _CACHE[entry] = adapted
    return adapted


_SDK_ERROR_LIST: list[str] = []


def sdk_status() -> dict:
    """SDK 桥接健康状态（供 ``registry_report()`` 汇总）。"""
    return {
        "ready": _SDK_READY,
        "root": str(_sdk_root()),
        "error": _SDK_ERROR,
        "loaded": sorted(k for k, v in _CACHE.items() if v is not None),
        "errors": list(_SDK_ERROR_LIST),
    }
