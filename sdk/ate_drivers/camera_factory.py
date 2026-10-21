# -*- coding: utf-8 -*-
"""相机型号注册表：按设备档案里的 `model` 直接派发厂商驱动

与示波器注册表（`factory.py`）**分开维护**，原因有二：

1. 相机的连接方式不是 `LAN` / `SERIAL`，而是 `USB3` / `GigE`；混进示波器的门禁
   会互相误判（`self_check` 会报「连接方式不在支持范围」）；
2. 相机没有「端口」概念，选机靠**序列号 / 用户自定义名 / 序号**，端点模型不同。

两者的共同原则完全一致：**不做打分、不做模糊匹配**。型号查不到就报错并列出
已登记型号，避免「跑错驱动还把结果算通过」这种最难查的问题。

登记新型号（平台代码不动）：

1. 厂商包 `driver.py` 声明 `MODELS = ("acA1920-40gm",)`；
2. 同目录 `driver.json` 的 `models` 写同一组（上架门禁 C17 校验两处一致）；
3. `ate_drivers.vendors.CAMERA_DRIVERS` 里挂上驱动类。
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .errors import ConfigurationError, DriverNotFound

#: 相机合法连接方式（USB3 / GigE Vision / Camera Link）
CAMERA_INTERFACES = ("USB", "USB3", "GIGE", "GIGE-V", "CAMERALINK")

CAMERA_BACKENDS = ("native",)

_registry: dict = {}


class _Entry:
    """一条相机注册项（型号 → 驱动类）"""

    __slots__ = ("model", "cls", "alias_of", "source")

    def __init__(self, model: str, cls: Any, alias_of: str = "", source: str = "builtin"):
        self.model = model
        self.cls = cls
        self.alias_of = alias_of
        self.source = source

    def describe(self) -> dict:
        cls = self.cls
        return {
            "model": self.model,
            "alias_of": self.alias_of,
            "family": getattr(cls, "family", ""),
            "driver": getattr(cls, "driver_key", ""),
            "vendor": getattr(cls, "vendor", ""),
            "label": getattr(cls, "label", ""),
            "version": getattr(cls, "driver_version", ""),
            "capabilities": sorted(set(getattr(cls, "CAPABILITIES", ()))),
            "interfaces": list(getattr(cls, "INTERFACES", ()) or ()),
            "backends": list(getattr(cls, "BACKENDS", ()) or ()),
            "simulate": bool(getattr(cls, "SIMULATE_SUPPORTED", True)),
            "entry": "%s:%s" % (getattr(cls, "__module__", ""), getattr(cls, "__name__", "")),
            "source": self.source,
        }


def normalize_model(model: Any) -> str:
    """型号归一：只保留字母数字并大写（`acA1920-40gm` 与 `ACA1920 40GM` 等价）"""
    return "".join(ch for ch in str(model or "").upper() if ch.isalnum())


def register_camera_model(model: Any, cls: Any, *, replace: bool = False,
                          alias_of: str = "", source: str = "builtin") -> str:
    key = normalize_model(model)
    if not key:
        raise ConfigurationError("相机型号不能为空", detail="型号来自设备档案的 model 字段")
    old = _registry.get(key)
    if old is not None and old.cls is not cls and not replace:
        raise ConfigurationError(
            "型号 %s 已被 %s 登记，不能再给 %s"
            % (model, getattr(old.cls, "driver_key", "?"), getattr(cls, "driver_key", "?")),
            detail="跨厂商同名型号必须显式 replace=True，或在档案里写厂商前缀型号串")
    _registry[key] = _Entry(str(model), cls, alias_of=alias_of, source=source)
    return key


def register_camera_driver(cls: Any, models: Optional[Iterable[str]] = None,
                           aliases: Iterable[str] = (), *, replace: bool = False,
                           source: str = "builtin") -> list:
    """把驱动类登记到它声明的全部型号（缺省取类属性 `MODELS`）"""
    listed = tuple(models if models is not None else (getattr(cls, "MODELS", ()) or ()))
    aliases = tuple(aliases) or tuple(getattr(cls, "MODEL_ALIASES", ()) or ())
    if not listed:
        raise ConfigurationError(
            "驱动 %s 没有声明 MODELS" % getattr(cls, "driver_key", cls),
            detail="相机型号派发要求每个驱动显式列出支持的型号（与 driver.json 一致）")
    keys = [register_camera_model(m, cls, replace=replace, source=source) for m in listed]
    for a in aliases:
        keys.append(register_camera_model(a, cls, replace=replace,
                                          alias_of=str(listed[0]), source=source))
    return keys


def unregister_camera_driver(cls: Any) -> int:
    gone = [k for k, e in _registry.items() if e.cls is cls]
    for k in gone:
        _registry.pop(k, None)
    return len(gone)


def camera_class(model: Any):
    """按型号派发驱动类；未登记则列出已登记型号（不猜、不兜底）"""
    key = normalize_model(model)
    entry = _registry.get(key)
    if entry is None:
        known = ", ".join(camera_models()) or "(空)"
        raise DriverNotFound(
            "未登记的相机型号：%r" % (model,),
            detail="已登记型号：%s —— 相机派发不做模糊匹配，请按厂商包规范登记型号"
                   "（或在档案里写 \"BASLER\" 走厂商通用别名）" % known)
    return entry.cls


def camera_entry(model: Any) -> dict:
    key = normalize_model(model)
    entry = _registry.get(key)
    if entry is None:
        camera_class(model)  # 复用同一套报错
    return entry.describe()


def camera_models() -> tuple:
    return tuple(sorted(e.model for e in _registry.values() if not e.alias_of))


def camera_aliases() -> dict:
    return {e.model: e.alias_of for e in _registry.values() if e.alias_of}


def camera_entries() -> list:
    return [e.describe() for e in sorted(_registry.values(), key=lambda x: (x.alias_of != "", x.model))]


def camera_vendors() -> tuple:
    seen, out = set(), []
    for e in _registry.values():
        v = getattr(e.cls, "vendor", "")
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return tuple(out)


def self_check() -> dict:
    """注册表自检：型号唯一、连接方式合法、清单与类声明一致（CI / 门禁用）"""
    problems = []
    seen: dict = {}
    for e in camera_entries():
        key = normalize_model(e["model"])
        if key in seen and not e["alias_of"]:
            problems.append("型号重复：%s" % e["model"])
        seen[key] = e
        if not e["interfaces"]:
            problems.append("%s 没有声明连接方式（应为 %s 组合）"
                            % (e["model"], CAMERA_INTERFACES))
        for kind in e["interfaces"]:
            if kind not in CAMERA_INTERFACES:
                problems.append("%s 的连接方式 %r 不在 %s 内" % (e["model"], kind, CAMERA_INTERFACES))
        if not e["backends"]:
            problems.append("%s 没有声明传输后端" % e["model"])
        for backend in e["backends"]:
            if backend not in CAMERA_BACKENDS:
                problems.append("%s 的后端 %r 不在 %s 内"
                                % (e["model"], backend, CAMERA_BACKENDS))
        if e["alias_of"] and normalize_model(e["alias_of"]) not in _registry:
            problems.append("%s 的别名指向不存在的型号 %s" % (e["model"], e["alias_of"]))
    return {"ok": not problems, "models": len(camera_models()),
            "aliases": len(camera_aliases()), "problems": problems}


def describe() -> str:
    lines = ["相机型号注册表（共 %d 个型号）:" % len(camera_models())]
    for e in camera_entries():
        tag = "（别名 → %s）" % e["alias_of"] if e["alias_of"] else ""
        lines.append("  %-18s %s%s" % (e["model"], e["label"] or e["driver"], tag))
        lines.append("  %-18s 厂商 %s · 驱动 %s v%s · 连接 %s"
                     % ("", e["vendor"], e["driver"], e["version"],
                        "/".join(e["interfaces"]) or "-"))
    return "\n".join(lines)


def open_camera(model: Any = None, *, serial_number: str = "", user_name: str = "",
                index: Any = 0, mode: str = "simulate", timeout: Optional[float] = 3.0,
                setup: Optional[dict] = None, transport: Any = None, alias: str = "",
                device_id: str = "", name: str = "", open: bool = False) -> Any:
    """按型号打开一台相机，返回 `ate_drivers.api_camera.Camera`

    * `mode="simulate"`（默认）：合成图像，无硬件可跑通流程与阈值断言；
    * `mode="real"`：需要 pylon 8.0.0 运行时 + pypylon，且至少要有一台在线相机；
    * 选机：`serial_number` > `user_name` > `index`（默认 0，即第一台）。
    """
    from .api_camera import Camera

    return Camera(model, serial_number=serial_number, user_name=user_name, index=index,
                  mode=mode, timeout=timeout, setup=setup, transport=transport, alias=alias,
                  device_id=device_id, name=name, open=open)


def _register_builtin() -> tuple:
    from .vendors import CAMERA_DRIVERS

    for cls in CAMERA_DRIVERS:
        register_camera_driver(cls, source="builtin")
    return CAMERA_DRIVERS


CAMERA_DRIVERS = _register_builtin()

__all__ = [
    "CAMERA_BACKENDS",
    "CAMERA_DRIVERS",
    "CAMERA_INTERFACES",
    "camera_aliases",
    "camera_class",
    "camera_entries",
    "camera_entry",
    "camera_models",
    "camera_vendors",
    "describe",
    "normalize_model",
    "open_camera",
    "register_camera_driver",
    "register_camera_model",
    "self_check",
    "unregister_camera_driver",
]
