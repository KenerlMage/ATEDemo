# -*- coding: utf-8 -*-
"""驱动工厂：按注册库里的设备信息挑选驱动实现

选择顺序（先具体、后兜底）：
1. 具体型号规格（如 `model` 含 `mso5` -> 泰克 MSO5 系列示波器驱动）
2. 类别规格（万用表 / 电源 / 电子负载 / 信号源 / 运动控制）
3. 被动设备（夹具等无通讯设备）
4. 通用 SCPI 仪器（最后兜底，保证任何登记过的设备都能被实例化）
"""

from __future__ import annotations

from typing import Any, Optional

from .base import Driver, PassiveDriver
from .errors import DriverNotFound
from .models import DeviceConfig
from .scope import ScopeDriver
from .scpi import AwgDriver, DmmDriver, ELoadDriver, GenericScpiDriver, MotionDriver, PsuDriver

# 每条规格: key / family / label / cls / priority(越大越优先) / match(字段->关键词列表)
DRIVER_SPECS: list[dict] = [
    {
        "key": "tektronix-mso5",
        "family": "scope",
        "label": "数字示波器 · 泰克 MSO5 系列",
        "cls": ScopeDriver,
        "priority": 20,
        "match": {"model": ["mso5", "mso54", "mso64", "dpo5", "tektronix"], "vendor": ["tektronix"]},
    },
    {
        "key": "generic-dmm",
        "family": "dmm",
        "label": "数字万用表",
        "cls": DmmDriver,
        "priority": 12,
        "match": {"category": ["万用表", "dmm", "multimeter"], "role": ["dmm", "万用表"]},
    },
    {
        "key": "generic-psu",
        "family": "psu",
        "label": "可编程直流电源",
        "cls": PsuDriver,
        "priority": 12,
        "match": {"category": ["电源", "psu", "power supply"], "role": ["psu", "电源"]},
    },
    {
        "key": "generic-eload",
        "family": "eload",
        "label": "可编程电子负载",
        "cls": ELoadDriver,
        "priority": 12,
        "match": {"category": ["电子负载", "eload", "load"], "role": ["eload", "负载"]},
    },
    {
        "key": "generic-awg",
        "family": "awg",
        "label": "波形发生器 / 信号源",
        "cls": AwgDriver,
        "priority": 12,
        "match": {"category": ["信号源", "波形", "awg", "generator"], "role": ["awg", "信号源"]},
    },
    {
        "key": "serial-motion",
        "family": "motion",
        "label": "运动控制器（串口）",
        "cls": MotionDriver,
        "priority": 14,
        "match": {"category": ["运动控制", "motion", "转速", "spin"], "interface": ["SERIAL", "RS232", "RS485", "UART", "COM"]},
    },
    {
        "key": "passive-device",
        "family": "passive",
        "label": "被动设备（无通讯）",
        "cls": PassiveDriver,
        "priority": 5,
        "match": {"interface": ["NONE", "MANUAL", "USB", "GPIB"]},
    },
    {
        "key": "generic-scpi",
        "family": "generic",
        "label": "通用 SCPI 仪器",
        "cls": GenericScpiDriver,
        "priority": 1,
        "match": {},
    },
]

DRIVER_SPECS_KEYS = [s["key"] for s in DRIVER_SPECS]

_FIELD_WEIGHT = {"model": 3.0, "vendor": 2.0, "category": 2.0, "role": 1.5, "interface": 1.2, "name": 1.0}

_OPEN_DRIVERS: list[Driver] = []


def _text_of(dev: Any, field: str) -> str:
    cfg = dev if isinstance(dev, DeviceConfig) else DeviceConfig.from_dict(dev or {})
    if field == "interface":
        return str(cfg.interface or "")
    return str(getattr(cfg, field, "") or "")


def _score(spec: dict, dev: Any) -> float:
    score = 0.0
    for field, keywords in (spec.get("match") or {}).items():
        hay = _text_of(dev, field).lower()
        if not hay:
            continue
        for kw in keywords:
            if str(kw).lower() in hay:
                score += _FIELD_WEIGHT.get(field, 1.0)
                break
    return score


def resolve_spec(dev: Any) -> dict:
    """返回命中的驱动规格（找不到则返回通用 SCPI 兜底规格）"""
    best: Optional[dict] = None
    best_score = 0.0
    for spec in DRIVER_SPECS:
        if not spec.get("match"):
            continue
        score = _score(spec, dev)
        if score <= 0:
            continue
        if score > best_score or (score == best_score and best and spec["priority"] > best["priority"]):
            best, best_score = spec, score
    if best:
        return best
    return next(s for s in DRIVER_SPECS if s["key"] == "generic-scpi")


def resolve_driver_key(dev: Any) -> str:
    """设备 -> 驱动规格名（写入 SQLite `device_registry.driver` 列，供运行时核对）"""
    try:
        return resolve_spec(dev)["key"]
    except Exception:
        return "generic-scpi"


def resolve_driver_class(dev: Any) -> type:
    return resolve_spec(dev)["cls"]


def get_driver(
    dev: Any,
    mode: str = "simulate",
    alias: str = "",
    timeout: Optional[float] = None,
    transport=None,
) -> Driver:
    """按设备配置实例化驱动

    * `dev`：`DeviceConfig` / dict（SQLite 行或注册库设备字典）
    * `mode`：`simulate`（默认，无硬件）/ `real`
    """
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


def list_specs() -> list[dict]:
    """驱动规格清单（前端 / 文档 / 排查用）"""
    return [
        {
            "key": s["key"],
            "family": s["family"],
            "label": s["label"],
            "driver_class": s["cls"].__name__,
            "priority": s["priority"],
            "match": s.get("match") or {},
            "actions": sorted(set(s["cls"].ACTIONS)),
        }
        for s in DRIVER_SPECS
    ]
