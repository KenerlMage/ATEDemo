# -*- coding: utf-8 -*-
"""新增驱动的发现、自检与用例侧调用（不需要数据库、外设或网络）

验证三件事：
1. 只在 `drivers/` 放一个模块，设备类型就出现在受控清单里（不改 factory.py）；
2. 来源报告与契约自检处于健康状态，撞 key 与坏模块都能被隔离；
3. 用例侧的调用方式（别名 → 实例 → do(action)）对新驱动同样成立。

单独运行：python -m pytest test_cases/test_driver_registry.py -v
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import drivers  # noqa: E402
from drivers import discovery  # noqa: E402
from drivers.factory import (  # noqa: E402
    DRIVER_TYPE_KEYS,
    DRIVER_TYPE_MAP,
    get_driver,
    registry_report,
    self_check,
)

NEW_KEY = "generic-power-meter"

_HEADER = "from drivers.base import Driver\n"


def _mod(key: str, label: str = "临时驱动") -> str:
    return (
        _HEADER
        + "class Tmp(Driver):\n"
        + f"    driver_key = {key!r}\n"
        + "    family = 'demo'\n"
        + f"    label = {label!r}\n"
        + "    ACTIONS = {'idn': 'identify'}\n"
    )


def _row(**kw) -> dict:
    row = {"device_id": "pm-1", "name": "光功率计", "device_type": NEW_KEY,
           "interface": "NONE", "model": "PM-1310"}
    row.update(kw)
    return row


# ------------------------------------------------------------------ 发现

def test_new_module_is_discovered_without_editing_factory():
    """新增驱动只靠放模块就进入受控清单"""
    assert NEW_KEY in DRIVER_TYPE_KEYS
    spec = DRIVER_TYPE_MAP[NEW_KEY]
    assert spec["source"] == "discovered"
    assert spec["family"] == "power"
    assert spec["module"].endswith("power_meter")


def test_builtin_types_are_not_overwritten():
    """同 key 时内置优先：内置类型仍标记 builtin"""
    assert DRIVER_TYPE_MAP["generic-dmm"]["source"] == "builtin"
    assert DRIVER_TYPE_MAP["tektronix-mso5"]["source"] == "builtin"


def test_registry_report_is_healthy():
    report = registry_report()
    assert report["errors"] == []
    assert report["duplicates"] == []
    assert report["ok"] is True
    assert report["builtin"] + report["discovered"] == report["total"]
    assert NEW_KEY in report["discovered_keys"]
    # 驱动实现已统一到 SDK，backend 侧可扫描模块随之减少；只校验扫描确实发生
    assert report["modules_scanned"] >= 1


def test_self_check_has_no_problems():
    result = self_check()
    assert result["ok"] is True, result["problems"]
    assert result["types"] == len(DRIVER_TYPE_KEYS)


def test_scan_isolates_duplicate_key_and_broken_module(tmp_path):
    """撞 key 只记重复不并入；坏模块只记错误，其余驱动仍被收下"""
    (tmp_path / "clash_a.py").write_text(_mod("tmp-clash", "A"), encoding="utf-8")
    (tmp_path / "clash_b.py").write_text(_mod("tmp-clash", "B"), encoding="utf-8")
    (tmp_path / "good_mod.py").write_text(_mod("tmp-good", "可用"), encoding="utf-8")
    (tmp_path / "broken_mod.py").write_text("raise RuntimeError('坏模块')\n", encoding="utf-8")

    result = discovery.scan(builtin_keys={"generic-dmm"}, root=tmp_path)

    # 撞 key 的第二个模块不并入清单（先到者保留），只记一条重复
    assert [s["key"] for s in result["specs"]] == ["tmp-clash", "tmp-good"]
    assert [d["key"] for d in result["duplicates"]] == ["tmp-clash"]
    assert result["duplicates"][0]["module"].endswith("clash_b")
    assert len(result["errors"]) == 1
    assert result["errors"][0]["module"].endswith("broken_mod")
    assert "坏模块" in result["errors"][0]["error"]


def test_scan_skips_modules_without_driver_class(tmp_path):
    (tmp_path / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    result = discovery.scan(root=tmp_path)
    assert result["specs"] == []
    assert result["skipped"] and result["skipped"][0].endswith("helper")


# ------------------------------------------------------------------ 驱动行为

def test_every_declared_action_points_to_a_real_method():
    cls = DRIVER_TYPE_MAP[NEW_KEY]["cls"]
    assert cls.ACTIONS
    for action, method in cls.ACTIONS.items():
        assert callable(getattr(cls, method, None)), f"{action} -> {method} 不存在"


def test_simulation_is_deterministic_and_bounded():
    d = get_driver(_row(), mode="simulate", alias="pm")
    info = d.open()
    assert info["driver"] == NEW_KEY
    assert info["simulated"] is True
    try:
        d.do("set_wavelength", nm=1550)
        d.do("set_average", count=8)
        first = d.do("read_power")["value"]
        second = d.do("read_power")["value"]
        assert first == second, "同一配置的仿真读数必须可复现"
        assert 0.5 < first < 3.0
    finally:
        d.close()


def test_wavelength_guard_rejects_out_of_range():
    d = get_driver(_row(), mode="simulate", alias="pm")
    d.open()
    try:
        with pytest.raises(drivers.DriverError) as ei:
            d.do("set_wavelength", nm=400)
        assert "测量范围" in str(ei.value)
    finally:
        d.close()


def test_case_side_usage_by_alias_only():
    """用例侧写法：别名 -> 实例 -> do(action) -> value，不出现驱动类名"""
    devices = {"pm": _row()}

    def driver(alias):
        return get_driver(devices[alias], mode="simulate", alias=alias)

    pm = driver("pm")
    pm.open()
    try:
        assert pm.do("set_wavelength", nm=1310)["value"] == 1310.0
        out = pm.do("read_power", unit="W")
        assert out["detail"]["unit"] == "W"
        assert out["value"] < 1.0, "瓦特读数应为毫瓦读数的千分之一量级"
    finally:
        pm.close()


def test_unknown_device_type_lists_available_keys():
    with pytest.raises(drivers.DriverNotFound) as ei:
        get_driver({"device_id": "x", "device_type": "no-such-type"}, mode="simulate")
    assert "未知设备类型" in str(ei.value)
    assert NEW_KEY in ei.value.detail
