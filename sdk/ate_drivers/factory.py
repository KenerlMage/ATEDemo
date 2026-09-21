# -*- coding: utf-8 -*-
"""示波器型号注册表：按数据库里的型号直接派发厂商驱动。

**与旧版「打分选择」的区别（该机制已整体删除）**

* 没有权重、没有关键字命中、没有 `priority` 抢位；
* 没有「最像的那个」兜底猜测——型号查不到就报 `E_NOT_FOUND`，并把已登记型号列出来；
* 型号是数据库设备档案里的**确定字段**，`open_scope()` 只按它派发。

**登记新型号**（平台代码不动）

1. 厂商包 `driver.py` 声明 `MODELS = ("MXO44",)`；
2. 厂商包 `driver.json` 的 `models` 列表写同一组型号（上架门禁 C17 会校验两处一致）；
3. `ate_drivers.vendors.VENDORS` 里挂上驱动类（内置包）或放进 `<ATE>/drivers` 由清单扫描加载。

两家示波器都是**网口（LAN）**型号：连接参数只有 `host` 与 `port`。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Optional, Type

from .errors import ConfigurationError, DriverNotFound
from .endpoint import (SUPPORTED_INTERFACES, endpoint_from, interfaces_of,
                       LAN as INTERFACE_LAN, SERIAL as INTERFACE_SERIAL,
    SUPPORTED_BACKENDS, backends_of,
)
from .family.scope import ScopeDriver

#: 支持的连接方式（LAN = 网口，SERIAL = 串口）——由 endpoint 层统一定义
LAN_ALIAS = INTERFACE_LAN

_MODEL_CLEAN = re.compile(r"[^A-Z0-9]")


def normalize_model(model: Any) -> str:
    """型号归一化：去大小写与分隔符差异（`mxo-44` / `MXO44` / ` MXO 44 ` 等价）"""
    return _MODEL_CLEAN.sub("", str(model or "").upper())


class _Reg:
    """一条注册项（型号 → 驱动类）"""

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
            "driver": getattr(cls, "driver_key", ""),
            "vendor": getattr(cls, "vendor", ""),
            "family": getattr(cls, "family", ""),
            "label": getattr(cls, "label", ""),
            "version": getattr(cls, "driver_version", ""),
            "api": getattr(cls, "api_version", ""),
            "interfaces": list(interfaces_of(cls)),
            "interface": (interfaces_of(cls) or ("",))[0],
            "backends": list(backends_of(cls)),
            "backend": (backends_of(cls) or ("",))[0],
            "simulate": bool(getattr(cls, "simulate_supported", True)),
            "entry": _entry_of(cls),
            "source": self.source,
        }


_registry: dict[str, _Reg] = {}


def _entry_of(cls: Any) -> str:
    return "%s:%s" % (getattr(cls, "__module__", ""), getattr(cls, "__name__", ""))


def register_model(model: str, cls: Any, *, replace: bool = False, alias_of: str = "",
                   source: str = "builtin") -> str:
    """登记一个型号（幂等：同一型号同一驱动重复登记不报错）"""
    key = normalize_model(model)
    if not key:
        raise ConfigurationError("型号不能为空", detail="数据库设备档案的 model 字段为必填")
    old = _registry.get(key)
    if old is not None and old.cls is not cls and not replace:
        raise ConfigurationError(
            "型号 %s 已被 %s 登记，不能再给 %s" % (model, getattr(old.cls, "driver_key", "?"),
                                                getattr(cls, "driver_key", "?")),
            detail="跨厂商同名型号必须显式 replace=True，或改用厂商前缀型号串")
    _registry[key] = _Reg(str(model), cls, alias_of=alias_of, source=source)
    return key


def register_driver(cls: Any, models: Optional[Iterable[str]] = None,
                    aliases: Iterable[str] = (), *, replace: bool = False,
                    source: str = "builtin") -> list[str]:
    """把一个驱动类登记到它声明的全部型号（`models` 缺省取类属性 `MODELS`）

    别名取类属性 `MODEL_ALIASES`（**不是** `ALIASES`——那是家族层的动作别名表）。
    """
    listed = tuple(models if models is not None else (getattr(cls, "MODELS", ()) or ()))
    aliases = tuple(aliases) or tuple(getattr(cls, "MODEL_ALIASES", ()) or ())
    if not listed:
        raise ConfigurationError(
            "驱动 %s 没有声明 MODELS" % getattr(cls, "driver_key", cls),
            detail="型号派发要求每个驱动显式列出它支持的型号（上架门禁 C17）")
    keys = [register_model(m, cls, replace=replace, source=source) for m in listed]
    for a in aliases:
        keys.append(register_model(a, cls, replace=replace, alias_of=str(listed[0]), source=source))
    return keys


def unregister_driver(cls: Any) -> int:
    """注销一个驱动类登记的全部型号（测试/热插拔用）"""
    gone = [k for k, r in _registry.items() if r.cls is cls]
    for k in gone:
        _registry.pop(k, None)
    return len(gone)


def driver_class(model: Any) -> Type[Any]:
    """按型号派发驱动类；未登记则列出已登记型号（**不猜、不兜底**）"""
    key = normalize_model(model)
    reg = _registry.get(key)
    if reg is None:
        known = ", ".join(models()) or "(空)"
        raise DriverNotFound(
            "未登记的示波器型号：%r" % (model,),
            detail="已登记型号：%s —— 型号派发不做模糊匹配，请在设备档案里写确定的型号，"
                   "或按厂商包规范登记新型号" % known)
    return reg.cls


def entry_for(model: Any) -> dict:
    """型号 → 注册项摘要（含官方型号名与 entry）"""
    key = normalize_model(model)
    reg = _registry.get(key)
    if reg is None:
        driver_class(model)  # 复用同一套报错
    return reg.describe()


def models() -> tuple:
    """已登记的**正式**型号（不含别名），按字母序"""
    return tuple(sorted(r.model for r in _registry.values() if not r.alias_of))


def aliases() -> dict:
    """别名 → 正式型号"""
    return {r.model: r.alias_of for r in _registry.values() if r.alias_of}


def entries() -> list:
    """注册表全量摘要（前端「驱动库」页 / 设备档案型号下拉框用）"""
    return [r.describe() for r in sorted(_registry.values(), key=lambda r: (r.alias_of != "", r.model))]


def vendors() -> tuple:
    """已登记的厂商名（去重保序）"""
    seen, out = set(), []
    for r in _registry.values():
        v = getattr(r.cls, "vendor", "")
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return tuple(out)


def describe() -> str:
    """人读摘要（CLI / 排查用）"""
    lines = ["型号注册表（共 %d 个型号）:" % len(models())]
    for e in entries():
        tag = "（别名 → %s）" % e["alias_of"] if e["alias_of"] else ""
        lines.append("  %-12s %s%s" % (e["model"], e["label"] or e["driver"], tag))
        lines.append("  %-12s 厂商 %s · 驱动 %s v%s · 连接方式 %s · 仿真 %s"
                     % ("", e["vendor"], e["driver"], e["version"],
                        "/".join(e["interfaces"]) or "-",
                        "支持" if e["simulate"] else "不支持"))
    return "\n".join(lines)


def self_check() -> dict:
    """注册表自检：型号唯一、接口一致、型号与清单一致（CI / 门禁用）"""
    problems = []
    seen: dict = {}
    for e in entries():
        key = normalize_model(e["model"])
        if key in seen and not e["alias_of"]:
            problems.append("型号重复：%s" % e["model"])
        seen[key] = e
        if not e["interfaces"]:
            problems.append("%s 没有声明连接方式（应为 %s 组合）"
                            % (e["model"], SUPPORTED_INTERFACES))
        for kind in e["interfaces"]:
            if kind not in SUPPORTED_INTERFACES:
                problems.append("%s 的连接方式 %r 不在 %s 内"
                                % (e["model"], kind, SUPPORTED_INTERFACES))
        if not e.get("backends"):
            problems.append("%s 没有声明传输后端（应为 %s 组合）" % (e["model"], SUPPORTED_BACKENDS))
        for backend in e.get("backends") or ():
            if backend not in SUPPORTED_BACKENDS:
                problems.append("%s 的传输后端 %r 不在 %s 内"
                                % (e["model"], backend, SUPPORTED_BACKENDS))
        if not e["model"]:
            problems.append("%s 没有声明型号" % e["driver"])
        if e["alias_of"] and normalize_model(e["alias_of"]) not in _registry:
            problems.append("%s 的别名指向不存在的型号 %s" % (e["model"], e["alias_of"]))
    return {"ok": not problems, "models": len(models()), "aliases": len(aliases()),
            "problems": problems}


def open_scope(model: Any = None, host: Optional[str] = None, port: Optional[int] = None, *,
               device: Any = None, interface: Any = None, serial_port: Any = None,
               baudrate: Any = None, bytesize: Any = None, parity: Any = None,
               stopbits: Any = None, mode: str = "auto", timeout: Optional[float] = 2.0,
               transport: Any = None, alias: str = "", verify_model: bool = True,
               open: bool = False, backend: Any = None) -> Any:
    """按型号打开一台示波器，返回通用顶层对象 `ate_drivers.api.Scope`。

    * `device=` 可以直接把数据库里的设备档案（dict 或对象）传进来，自动读取
      `model` / `host` / `port` / `serial_port` / `baudrate` / `interface` / `name` / `device_id`；
    * 连接方式：网口（host + port）或串口（serial_port + baudrate），也可以显式传 `interface=`；
      档案里两种参数都填了又没写 `interface` → `ConfigurationError`（不猜）；
    * `mode="auto"`：有端点就走真机（网口 / 串口），没有就走仿真；
    * `backend=`：`native`（默认，标准库）/ `visa`（PyVISA）/ `auto`（装了 pyvisa 就走 VISA），
      也可以写进档案的 `backend` 字段；型号没声明的后端 → `ConfigurationError`；
    * 型号未登记 → `DriverNotFound`；型号不支持该连接方式 → `ConfigurationError`。
    """
    from .api import Scope  # 局部导入避免与 api 的循环依赖

    model = model if model is not None else _pick(device, "model")
    backend = backend if backend not in ("", None) else _pick(device, "backend")
    name = _pick(device, "name") or ""
    device_id = _pick(device, "device_id") or _pick(device, "id") or ""
    endpoint = endpoint_from(device, interface=interface, host=host, port=port,
                             serial_port=serial_port, baudrate=baudrate, bytesize=bytesize,
                             parity=parity, stopbits=stopbits, timeout=timeout)
    return Scope(model, endpoint=endpoint, mode=mode, timeout=timeout, transport=transport,
                 alias=alias, device_id=device_id, name=name, verify_model=verify_model, open=open,
                 backend=backend)


def _pick(device: Any, field: str) -> Any:
    if device is None:
        return None
    if isinstance(device, dict):
        val = device.get(field)
    else:
        val = getattr(device, field, None)
    return val if val not in ("", None) else None


def _register_builtin() -> tuple:
    """把内置厂商包自动登记进注册表（导入时执行一次）"""
    from .vendors import DRIVERS
    for cls in DRIVERS:
        register_driver(cls, source="builtin")
    return DRIVERS


DRIVERS = _register_builtin()

__all__ = [
    "DRIVERS",
    "INTERFACE_LAN",
    "INTERFACE_SERIAL",
    "SUPPORTED_BACKENDS",
    "SUPPORTED_INTERFACES",
    "aliases",
    "describe",
    "driver_class",
    "entries",
    "entry_for",
    "models",
    "normalize_model",
    "open_scope",
    "register_driver",
    "register_model",
    "self_check",
    "unregister_driver",
    "vendors",
]
