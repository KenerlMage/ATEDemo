# -*- coding: utf-8 -*-
"""L5 · 设备簿：别名 → 档案 → 句柄。

一台台架上可能挂两台**同型号**设备，光凭型号无法区分。现场约定是给每台设备
起一个别名（`device_registry.device_id`，例如 ``scope_main`` / ``scope_aux``），
别名与连接参数一起存进数据库；用例只认别名：

```python
from ate_drivers import connect

def test_rise_time():
    scope = connect("scope_main")     # 用别名取句柄，连接参数来自档案
    ...
```

分工是死的：

* **后端 / Runner** 负责从库里读出设备行，调 :func:`load_devices` 灌进来；
* **SDK** 只持有内存里的簿子，不连数据库、不读配置文件的路径；
* **用例** 只调 :func:`connect`，不关心 host/port。

同一别名重复 ``connect`` 返回**同一个句柄**（省去重复建链），
:func:`disconnect` 之后才会重新打开。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

from ..core.errors import ConfigurationError, DriverNotFound
from ..core.models import DeviceConfig

__all__ = [
    "connect",
    "device_aliases",
    "device_config",
    "disconnect",
    "disconnect_all",
    "handle_of",
    "handles",
    "load_devices",
    "register_device",
    "register_family",
    "registered_families",
    "unload_devices",
]

_CONFIGS: Dict[str, DeviceConfig] = {}      # 规范键 → 档案
_ALIASES: Dict[str, List[str]] = {}         # 别名（小写）→ 规范键列表（跨台架可同名）
_HANDLES: Dict[str, Any] = {}            # 规范键 → 已打开句柄
_OPENERS: Dict[str, Callable] = {}       # 家族名（小写）→ 打开函数

#: 家族识别：先看档案的 category / role，再看 extra.device_type
_FAMILY_HINTS = (
    ("camera", ("camera", "cam", "相机")),
    ("scope", ("scope", "oscope", "oscilloscope", "waveform", "示波器")),
)


# --------------------------------------------------------------------- 工具
def _canonical(bench_id: str, alias: str) -> str:
    return "%s::%s" % (bench_id or "", alias or "")


def _alias_of(cfg: DeviceConfig) -> str:
    return str(cfg.device_id or cfg.name or "").strip()


def _family_of(cfg: DeviceConfig) -> str:
    """按 category / role / extra 判定家族；已登记的家族名优先命中。"""
    extra = cfg.extra if isinstance(cfg.extra, dict) else {}
    texts = [cfg.category, cfg.role, extra.get("device_type"), extra.get("family")]
    lows = [str(t or "").strip().lower() for t in texts]
    for low in lows:                       # 1) 显式登记过的家族名（含自定义家族）
        if low and low in _OPENERS:
            return low
    for low in lows:                       # 2) 现场叫法的别名匹配
        if not low:
            continue
        for name, hints in _FAMILY_HINTS:
            if any(h in low for h in hints):
                return name
    return ""


# --------------------------------------------------------------------- 注册
def register_family(name: str, opener: Callable[..., Any]) -> None:
    """登记「家族名 → 打开函数」。

    opener 的签名约定为 ``opener(cfg, *, mode, timeout, open)``，返回驱动实例。
    新增设备类型时（电源、电子负载、运动台……）在这里登记即可，
    设备簿不需要认识具体家族。
    """
    key = str(name or "").strip().lower()
    if not key:
        raise ConfigurationError("家族名不能为空")
    if not callable(opener):
        raise ConfigurationError("opener 必须可调用", detail=repr(opener))
    _OPENERS[key] = opener


def registered_families() -> tuple:
    return tuple(sorted(_OPENERS))


def register_device(config: Any, *, replace: bool = False) -> DeviceConfig:
    """登记一台设备的档案（dict / dataclass / sqlite3.Row 都行）。

    规范键是 ``bench_id::device_id``；``name`` 若与 ``device_id`` 不同，
    额外登记成别名，方便用现场叫法取设备。
    """
    cfg = DeviceConfig.from_dict(config)
    alias = _alias_of(cfg)
    if not alias:
        raise ConfigurationError("设备档案缺少 device_id / name", detail="别名是取设备的唯一凭据")
    key = _canonical(cfg.bench_id, alias)
    if key in _CONFIGS and not replace:
        raise ConfigurationError(
            "别名重复: %s" % alias,
            detail="同一台架下 device_id 必须唯一；确认是否传错了 bench_id",
        )
    _CONFIGS[key] = cfg
    for text in (alias, str(cfg.name or "").strip()):
        if not text:
            continue
        bucket = _ALIASES.setdefault(text.lower(), [])
        if key not in bucket:
            bucket.append(key)
    return cfg


def load_devices(rows: Iterable[Any], *, replace: bool = False) -> int:
    """批量装载设备档案，返回装载条数；重复调用按同名覆盖（replace=True）。"""
    count = 0
    for row in rows or ():
        try:
            register_device(row, replace=replace)
            count += 1
        except ConfigurationError:
            if not replace:
                raise
    return count


def unload_devices(*, bench_id: str = "") -> int:
    """清空设备簿；给 bench_id 时只清该台架。"""
    keys = [k for k in _CONFIGS if not bench_id or k.startswith("%s::" % bench_id)]
    for key in keys:
        cfg = _CONFIGS.pop(key, None)
        _HANDLES.pop(key, None)
        if cfg is not None:
            for alias, keys_of in list(_ALIASES.items()):
                if key in keys_of:
                    keys_of.remove(key)
                if not keys_of:
                    _ALIASES.pop(alias, None)
    return len(keys)


# --------------------------------------------------------------------- 查找
def _resolve(alias: Any, bench_id: str = "") -> str:
    text = str(alias or "").strip()
    if not text:
        raise ConfigurationError("别名不能为空")
    if bench_id:
        key = _canonical(bench_id, text)
        if key in _CONFIGS:
            return key
        # 也允许直接用 name 作别名
        for target in _ALIASES.get(text.lower()) or []:
            if target.startswith("%s::" % bench_id):
                return target
        raise DriverNotFound(
            "设备簿里没有别名 %s" % text,
            detail="台架 %s 未登记该设备；先 load_devices() 灌入档案" % bench_id,
        )
    keys = _ALIASES.get(text.lower()) or []
    if len(keys) == 1:
        return keys[0]
    if len(keys) > 1:
        raise ConfigurationError(
            "别名 %s 在多个台架重复" % text,
            detail="已登记于 %s；用 connect(alias, bench_id=...) 指定台架"
            % ", ".join(sorted(k.split("::", 1)[0] or "（无台架）" for k in keys)),
        )
    raise DriverNotFound(
        "设备簿里没有别名 %s" % text,
        detail="已登记: %s；若这是型号，请用 open_scope() / open_camera()"
        % (", ".join(device_aliases()) or "（空）"),
    )


def device_config(alias: Any, *, bench_id: str = "") -> DeviceConfig:
    """按别名取档案（已登记则直接返回，不会建链）"""
    return _CONFIGS[_resolve(alias, bench_id)]


def device_aliases(*, bench_id: str = "") -> tuple:
    """当前簿里可用别名（按登记顺序的字典序）"""
    names = []
    for canonical_key, cfg in _CONFIGS.items():
        if bench_id and not canonical_key.startswith("%s::" % bench_id):
            continue
        alias = _alias_of(cfg)
        if alias:
            names.append(alias)
    return tuple(sorted(names))


# --------------------------------------------------------------------- 句柄
def connect(alias: Any, *, bench_id: str = "", mode: str = "auto",
            timeout: Optional[float] = None, open: bool = True,
            refresh: bool = False) -> Any:
    """按别名打开设备并缓存句柄（同别名多次调用返回同一实例）。

    * ``mode`` 为空/``auto`` 时用家族默认（示波器真机优先、相机默认仿真）；
    * ``refresh=True`` 关掉旧句柄重新建链（换机、掉线重连时用）；
    * 别名未登记 → :class:`DriverNotFound`；家族未登记 → :class:`ConfigurationError`。
    """
    key = _resolve(alias, bench_id)
    if not refresh and key in _HANDLES:
        return _HANDLES[key]
    if refresh:
        disconnect(alias, bench_id=bench_id)

    cfg = _CONFIGS[key]
    family = _family_of(cfg)
    opener = _OPENERS.get(family)
    if opener is None:
        raise ConfigurationError(
            "设备 %s 的家族无法判定或未登记" % (_alias_of(cfg) or alias),
            detail="category/role/extra.device_type 要能认出 scope 或 camera；"
                   "新家族请先 register_family()",
        )
    handle = opener(cfg, mode=mode, timeout=timeout, open=open)
    if open:
        _HANDLES[key] = handle
    return handle


def handle_of(alias: Any, *, bench_id: str = "") -> Any:
    """取已打开的句柄；没有则返回 None（不建链）"""
    return _HANDLES.get(_resolve(alias, bench_id))


def handles() -> Dict[str, Any]:
    """当前已打开的句柄表（规范键 → 实例）"""
    return dict(_HANDLES)


def disconnect(alias: Any, *, bench_id: str = "") -> bool:
    """关掉某个别名的句柄，返回是否真的关过"""
    key = _resolve(alias, bench_id)
    handle = _HANDLES.pop(key, None)
    if handle is None:
        return False
    closer = getattr(handle, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:  # 关闭失败不该掩盖掉业务异常
            pass
    return True


def disconnect_all() -> int:
    """关掉全部句柄（夹具退出、台架释放时调用）"""
    keys: List[str] = list(_HANDLES)
    for key in keys:
        handle = _HANDLES.pop(key, None)
        closer = getattr(handle, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
    return len(keys)


# --------------------------------------------------------------------- 默认家族
def _scope_opener(cfg: DeviceConfig, *, mode: str, timeout, open: bool) -> Any:
    from .scopes import open_scope

    real_mode = str(mode or "").strip().lower()
    if real_mode in ("", "auto"):
        real_mode = "auto"      # 有端点走真机，没有走仿真
    return open_scope(
        cfg.model,
        device=cfg,
        mode=real_mode,
        timeout=timeout if timeout is not None else cfg.timeout,
        alias=_alias_of(cfg),
        open=open,
    )


def _camera_opener(cfg: DeviceConfig, *, mode: str, timeout, open: bool) -> Any:
    from .cameras import open_camera

    real_mode = str(mode or "").strip().lower()
    if real_mode in ("", "auto"):
        real_mode = "real" if (cfg.serial_number if hasattr(cfg, "serial_number") else "") else "simulate"
    return open_camera(
        cfg.model,
        serial_number=str(cfg.extra.get("serial_number", "") or "") if isinstance(cfg.extra, dict) else "",
        mode=real_mode if real_mode in ("real", "simulate") else "simulate",
        timeout=timeout if timeout is not None else cfg.timeout,
        device_id=cfg.device_id,
        name=cfg.name,
        open=open,
    )


register_family("scope", _scope_opener)
register_family("camera", _camera_opener)
