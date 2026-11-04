# -*- coding: utf-8 -*-
"""驱动工厂：按测试台注册时选定的设备类型直接取驱动实现

设备类型是注册记录中的显式字段 `device_type`，不再根据型号、厂商、类别或
接口关键词做打分选型；运行时只做一次精确查表，找不到就报错，不回退到相似驱动。

受控清单 = 内置清单 + 自动发现。新增驱动只需在 `drivers/` 下放一个模块（`Driver`
子类 + `driver_key` / `family` / `label`），进程启动时由 `discovery.scan()` 并入，
无需修改本文件。扫描只发生一次，不监听文件变化，不支持运行时热插拔——新增或替换
驱动仍须随 Runner 安装包发布。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .base import Driver, PassiveDriver
from .errors import DriverNotFound
from .models import DeviceConfig
from .discovery import scan as _scan_drivers
from .sdk_bridge import sdk_driver_class, sdk_status

# 受控设备类型清单。key 同时是注册记录中的 device_type 与 driver_key。
#: 内置受控类型（随 Runner 安装包发布）
_BUILTIN_TYPES: list[dict] = [
    {
        "key": "tektronix-mso5",
        "family": "scope",
        "label": "数字示波器 · 泰克 MSO5 系列",
        "cls": sdk_driver_class("ate_drivers.vendors.tektronix_mso.driver:TekMsoScope", "scope"),
        "description": "泰克 MSO5/DPO5 系列，LAN 或串口",
    },
    {
        "key": "generic-dmm",
        "family": "dmm",
        "label": "数字万用表",
        "cls": sdk_driver_class("ate_drivers.generic.scpi:DmmDriver", "dmm"),
        "description": "支持 SCPI 的台式/系统万用表",
    },
    {
        "key": "generic-psu",
        "family": "psu",
        "label": "可编程直流电源",
        "cls": sdk_driver_class("ate_drivers.generic.scpi:PsuDriver", "psu"),
        "description": "单路/多路可编程直流电源",
    },
    {
        "key": "generic-eload",
        "family": "eload",
        "label": "可编程电子负载",
        "cls": sdk_driver_class("ate_drivers.generic.scpi:ELoadDriver", "eload"),
        "description": "支持恒流/恒压/恒阻/恒功率的电子负载",
    },
    {
        "key": "generic-awg",
        "family": "awg",
        "label": "波形发生器 / 信号源",
        "cls": sdk_driver_class("ate_drivers.generic.scpi:AwgDriver", "awg"),
        "description": "函数发生器、任意波形发生器或射频信号源",
    },
    {
        "key": "serial-motion",
        "family": "motion",
        "label": "运动控制器（串口）",
        "cls": sdk_driver_class("ate_drivers.generic.scpi:MotionDriver", "motion"),
        "description": "通过串口控制转速、位置或扫描运动",
    },
    {
        "key": "basler-pylon",
        "family": "camera",
        "label": "工业相机 · Basler pylon 8.0.0",
        "cls": sdk_driver_class("ate_drivers.vendors.basler_pylon.driver:BaslerPylonCamera", "camera"),
        "description": "Basler GigE / USB3 相机，pylon 8.0.0（pypylon，惰性加载）",
    },
    {
        "key": "passive-device",
        "family": "passive",
        "label": "被动设备（无通讯）",
        "cls": sdk_driver_class("ate_drivers.base:PassiveDriver", "passive"),
        "description": "夹具、工装、手动设备等无通讯对象",
    },
    {
        "key": "generic-scpi",
        "family": "generic",
        "label": "通用 SCPI 仪器",
        "cls": sdk_driver_class("ate_drivers.generic.scpi:GenericScpiDriver", "generic"),
        "description": "未归入上述家族、但支持 SCPI 的仪器",
    },
]

#: SDK 加载失败（cls 为 None）的类型不进入受控清单——驱动实现只认 SDK 一份
_BUILTIN_TYPES = [item for item in _BUILTIN_TYPES if item.get("cls") is not None]

#: 启动时扫描一次：新增驱动模块自动并入受控清单（与内置同 key 时内置优先）
_DISCOVERY = _scan_drivers({item["key"] for item in _BUILTIN_TYPES})

#: 受控类型清单（内置 + 自动发现），运行期唯一数据源
DRIVER_TYPES: list[dict] = (
    [dict(item, source=str(item.get("source") or "builtin")) for item in _BUILTIN_TYPES]
    + _DISCOVERY["specs"]
)

DRIVER_TYPE_MAP: dict[str, dict] = {item["key"]: item for item in DRIVER_TYPES}
DRIVER_TYPE_KEYS = [item["key"] for item in DRIVER_TYPES]

# 旧记录迁移用的精确类别映射，只在补齐缺失字段时使用，不参与运行时选型。
LEGACY_CATEGORY_TYPE: dict[str, str] = {
    "示波器": "tektronix-mso5",
    "万用表": "generic-dmm",
    "电源": "generic-psu",
    "电子负载": "generic-eload",
    "信号源": "generic-awg",
    "波形": "generic-awg",
    "运动控制": "serial-motion",
    "串口": "generic-scpi",
    "相机": "basler-pylon",
    "工业相机": "basler-pylon",
    "视觉": "basler-pylon",
    "夹具": "passive-device",
    "台架": "passive-device",
}

_OPEN_DRIVERS: list[Driver] = []


def default_device_type(dev: Any) -> str:
    """旧记录/旧 BOM 缺失 device_type 时的精确类别映射。

    不做关键词打分，也不看型号；未命中的类别统一落到通用 SCPI。
    """
    cfg = dev if isinstance(dev, DeviceConfig) else DeviceConfig.from_dict(dev or {})
    return LEGACY_CATEGORY_TYPE.get(str(cfg.category or "").strip(), "generic-scpi")


def resolve_spec(dev: Any) -> dict:
    """按 device_type 精确取驱动类型；缺失或非法直接报错。"""
    cfg = dev if isinstance(dev, DeviceConfig) else DeviceConfig.from_dict(dev or {})
    key = str(cfg.device_type or "").strip()
    if not key:
        raise DriverNotFound(
            "设备未指定 device_type，请在测试台注册时选择设备类型",
            device_id=cfg.device_id,
        )
    spec = DRIVER_TYPE_MAP.get(key)
    if not spec:
        raise DriverNotFound(
            f"未知设备类型: {key}",
            device_id=cfg.device_id,
            detail="可选类型: " + ", ".join(DRIVER_TYPE_KEYS),
        )
    return spec


def resolve_driver_key(dev: Any) -> str:
    """设备 -> 设备类型 key（写入 SQLite `device_registry.driver` 列）。"""
    return resolve_spec(dev)["key"]


def resolve_driver_class(dev: Any) -> type:
    return resolve_spec(dev)["cls"]


def get_driver(
    dev: Any,
    mode: str = "simulate",
    alias: str = "",
    timeout: Optional[float] = None,
    transport=None,
) -> Driver:
    """按注册时选定的设备类型实例化驱动。"""
    cfg = dev if isinstance(dev, DeviceConfig) else DeviceConfig.from_dict(dev or {})
    if not cfg.device_id:
        raise DriverNotFound("设备配置缺少 device_id", detail="请检查测试台注册信息")
    cls = resolve_driver_class(cfg)
    try:
        driver = cls(cfg, mode=mode, timeout=timeout, transport=transport, alias=alias)
    except TypeError:  # 自定义驱动实现可能不接受 alias/transport
        driver = cls(cfg, mode=mode, timeout=timeout)
    _OPEN_DRIVERS.append(driver)
    return driver


def release_all() -> int:
    """关闭本次进程内创建过的全部驱动会话（conftest 会话结束时调用）"""
    closed = 0
    while _OPEN_DRIVERS:
        driver = _OPEN_DRIVERS.pop()
        try:
            if driver.opened:
                driver.close()
                closed += 1
        except Exception:
            pass
    return closed


def registry_report() -> dict:
    """受控类型清单的来源与健康状态（发布自检 / 现场排查用）"""
    return {
        "builtin": len(_BUILTIN_TYPES),
        "discovered": len(_DISCOVERY["specs"]),
        "total": len(DRIVER_TYPES),
        "modules_scanned": _DISCOVERY["scanned"],
        "discovered_keys": [item["key"] for item in _DISCOVERY["specs"]],
        "duplicates": [dict(d) for d in _DISCOVERY["duplicates"]],
        "errors": [dict(e) for e in _DISCOVERY["errors"]],
        "ok": not _DISCOVERY["errors"] and not _DISCOVERY["duplicates"],
    }


def self_check() -> dict:
    """驱动契约自检：key 唯一合法、必填元数据齐全、动作指向真实方法

    发布前必须 `ok=True`。新增驱动最常犯的两类错误——key 与已有类型冲突、
    `ACTIONS` 里写了不存在的方法名——都在这里拦下。
    """
    problems: list[str] = []
    seen: dict = {}
    for item in DRIVER_TYPES:
        key = str(item.get("key") or "")
        cls = item.get("cls")
        module = str(item.get("module") or getattr(cls, "__module__", ""))
        if not re.match(r"^[a-z0-9][a-z0-9._-]*$", key):
            problems.append(f"类型 key 非法: {key!r}（应为小写字母、数字、点、下划线或连字符）")
        if key in seen:
            problems.append(f"类型 key 重复: {key}（{seen[key]} 与 {module}）")
        seen[key] = module
        for field in ("family", "label"):
            if not str(getattr(cls, field, "") or "").strip():
                problems.append(f"{key} 缺少 {field}")
        actions = dict(getattr(cls, "ACTIONS", {}) or {})
        if not actions:
            problems.append(f"{key} 没有声明 ACTIONS")
        for action, method in actions.items():
            if not callable(getattr(cls, method, None)):
                problems.append(f"{key} 的动作 {action} 指向不存在的方法 {method}")
    return {"ok": not problems, "types": len(DRIVER_TYPES),
            "builtin": len(_BUILTIN_TYPES), "discovered": len(_DISCOVERY["specs"]),
            "problems": problems}


def list_driver_types() -> list[dict]:
    """注册页设备类型下拉框的数据源。"""
    return [
        {
            "key": item["key"],
            "family": item["family"],
            "label": item["label"],
            "description": item["description"],
            "actions": sorted(set(item["cls"].ACTIONS)),
            "source": item.get("source", "builtin"),
        }
        for item in DRIVER_TYPES
    ]


def list_specs() -> list[dict]:
    """兼容旧排查接口：返回类型清单，不再包含 priority/match。"""
    return [
        {
            "key": item["key"],
            "family": item["family"],
            "label": item["label"],
            "driver_class": item["cls"].__name__,
            "actions": sorted(set(item["cls"].ACTIONS)),
        }
        for item in DRIVER_TYPES
    ]
