# -*- coding: utf-8 -*-
"""驱动库自测：契约规则、清单校验、一致性套件、真机链路解析、注册表选择、CLI 骨架

用平台自带 venv 跑：

    D:\\ATE\\backend\\.venv\\Scripts\\python.exe -m pytest tests -q
"""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "samples")):
    if p not in sys.path:
        sys.path.insert(0, p)

from ate_drivers import (  # noqa: E402
    API_VERSION,
    DeviceConfig,
    DriverError,
    UnsupportedCapability,
    check_compatible,
)
from ate_drivers.capabilities import Capability, validate  # noqa: E402
from ate_drivers.contract import describe_contract  # noqa: E402
from ate_drivers.family.scope import ScopeDriver, measure_samples, sim_samples  # noqa: E402
from ate_drivers.kit import (  # noqa: E402
    FakeTransport,
    load_manifest,
    merge_specs,
    model_conflicts,
    resolve_entry,
    resolve_spec,
    run_conformance,
)
from ate_drivers.kit import cli  # noqa: E402
from ate_drivers.kit.registry import resolve_entry, scan_dir  # noqa: E402
from tek_mso5 import TekMso5Scope  # noqa: E402

SAMPLE_DIR = os.path.join(ROOT, "samples", "tek_mso5")


def _device(**kw):
    base = {
        "device_id": "rh-scope", "name": "数字示波器", "model": "Tektronix MSO54",
        "vendor": "Tektronix", "category": "示波器", "role": "波形观测",
        "interface": "LAN", "host": "192.168.10.41", "port": 4000, "programmable": True,
    }
    base.update(kw)
    return DeviceConfig.from_dict(base)


# ------------------------------------------------------------------ 契约

def test_contract_version_rules():
    assert check_compatible("1.2")[0]
    assert check_compatible("1.0")[0]                     # 老驱动在 1.2 平台上仍可运行
    assert not check_compatible("1.3")[0]                 # 驱动要求更高 minor -> 拒绝
    assert not check_compatible("2.0")[0]                 # major 不等 -> 拒绝
    assert not check_compatible("abc")[0]
    rules = describe_contract()
    assert rules["api_version"] == API_VERSION and rules["result_schema"].endswith("v1")
    assert len(rules["rules"]) >= 4


def test_capability_validation():
    missing, unknown, vendor = validate(ScopeDriver.CAPABILITIES, "scope")
    assert missing == [] and unknown == [] and vendor == []
    missing, unknown, _ = validate(("identify",), "scope")
    assert Capability.SCOPE_ACQUIRE in missing and Capability.SCOPE_MEASURE in missing
    _, unknown, vendor = validate(("identify", "vendor.acme.selfcal"), "generic")
    assert unknown == [] and vendor == ["vendor.acme.selfcal"]


# ------------------------------------------------------------------ 清单与注册表

def test_manifest_load_ok_and_scan():
    m = load_manifest(SAMPLE_DIR)
    assert m["key"] == "tektronix-mso5" and m["api"] == API_VERSION
    assert "兼容" in m["compatible"]
    assert scan_dir(os.path.join(ROOT, "samples"))[0]["key"] == "tektronix-mso5"


def test_manifest_rejects_bad_packages(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "driver.json").write_text(json.dumps({"schema": "ate.driver.manifest.v1", "key": "x"}),
                                     encoding="utf-8")
    with pytest.raises(DriverError):
        load_manifest(str(bad))
    wrong_api = tmp_path / "api"
    wrong_api.mkdir()
    (wrong_api / "driver.json").write_text(json.dumps({
        "schema": "ate.driver.manifest.v1", "key": "x-y", "version": "1.0.0", "family": "scope",
        "api": "9.0", "entry": "x.driver:X", "models": ["X1"]}), encoding="utf-8")
    with pytest.raises(DriverError) as e:
        load_manifest(str(wrong_api))
    assert e.value.code == "E_CONTRACT"


def test_registry_merge_and_dispatch_by_model():
    """型号派发：精确匹配（归一化大小写与分隔符），未登记型号直接报错、不再兜底"""
    builtin = [
        {"key": "generic-dmm", "family": "dmm", "label": "数字万用表", "interface": "LAN",
         "models": ["8808A", "8846A"]},
        {"key": "generic-scope-other", "family": "scope", "label": "其它示波器",
         "interface": "LAN", "models": ["SDS1104X"]},
    ]
    specs = merge_specs(builtin, [load_manifest(SAMPLE_DIR)])
    assert len(specs) == 3
    assert resolve_spec("MSO54", specs)["key"] == "tektronix-mso5"
    assert resolve_spec("mso-54", specs)["key"] == "tektronix-mso5"      # 分隔符/大小写无关
    assert resolve_spec("8808A", specs)["key"] == "generic-dmm"
    assert resolve_spec("SDS1104X", specs)["key"] == "generic-scope-other"
    with pytest.raises(DriverError) as e:
        resolve_spec("SDS9999", specs)
    assert e.value.code == "E_NOT_FOUND"
    assert "MSO54" in e.value.detail and "8808A" in e.value.detail       # 报错要把可用型号列清楚
    assert model_conflicts(specs) == []
    assert resolve_entry(load_manifest(SAMPLE_DIR)) is TekMso5Scope


# ------------------------------------------------------------------ 一致性套件

def test_sample_driver_passes_conformance():
    report = run_conformance(TekMso5Scope, root_package="tek_mso5")
    assert report["ok"], "\n".join(f"{r['id']} {r['title']}：{r['detail']}"
                                   for r in report["checks"] if not r["ok"])
    assert report["total"] == 19


def test_skeleton_generated_by_cli_passes_conformance(tmp_path):
    assert cli.main(["new", "acme-scope-3000", "--dir", str(tmp_path), "--force"]) == 0
    pkg = tmp_path / "acme_scope_3000"          # 目录名是合法模块名，key 仍带连字符
    assert (pkg / "driver.py").exists() and (pkg / "driver.json").exists()
    code = cli.main(["check", str(pkg)])
    assert code == 0, "新生成的骨架必须开箱通过全部一致性检查"
    assert cli.main(["list", str(tmp_path)]) == 0
    assert cli.main(["contract"]) == 0


# ------------------------------------------------------------------ 行为

def test_scope_simulate_measurements_are_grounded():
    dev = TekMso5Scope(_device(), mode="simulate")
    dev.do("scope.autoset", freq_hz=1000.0, vpp_v=2.4)
    wave = dev.do("scope.acquire_waveform", points=4000)["value"]
    assert wave["schema"].endswith("v1") and wave["unit"] == "V" and wave["points"] == 4000
    items = {i["type"]: i for i in dev.do("scope.measure", items=["PK2PK", "FREQUENCY"])["value"]}
    assert abs(items["PK2PK"]["value"] - 2.4) < 0.15
    assert abs(items["FREQUENCY"]["value"] - 1000.0) / 1000.0 < 0.05
    assert items["FREQUENCY"]["unit"] == "Hz" and items["PK2PK"]["unit"] == "V"
    assert items["FREQUENCY"]["source"] == "simulate"
    dev.close()


def test_legacy_alias_and_unknown_action():
    dev = TekMso5Scope(_device(), mode="simulate")
    legacy = dev.do("waveform", points=100)          # 历史 TPS 里的短名
    assert legacy["action"] == "scope.acquire_waveform" and legacy["requested"] == "waveform"
    with pytest.raises(UnsupportedCapability):
        dev.do("no.such.action")
    assert not dev.supports("dmm.measure") and dev.supports("scope.*")
    dev.close()


def test_real_mode_parses_preamble_by_field_names():
    transport = FakeTransport()
    transport.expect("*IDN?", "TEKTRONIX,MSO54,SN123456,1.2.3")
    transport.expect(":WFMOUTPre?", "2;8;RIBINARY;RI;MSB;CH1;3;Y;1e-05;0;0;0.0;0.01;0.0;TIME;ANALOG")
    transport.expect(":CURVe?", "128,130,132")
    dev = TekMso5Scope(_device(), mode="real", transport=transport)
    dev.open()
    assert dev.idn == "TEKTRONIX,MSO54,SN123456,1.2.3"
    wave = dev.do("scope.acquire_waveform", points=3)["value"]
    assert wave["source"] == "instrument" and wave["x_incr_s"] == 1e-05
    assert wave["samples"] == [1.28, 1.3, 1.32]
    assert ":WFMOUTPre?" in dev.commands and ":CURVe?" in dev.commands
    dev.close()


def test_safety_teardown_and_measurement_math():
    dev = TekMso5Scope(_device(), mode="simulate")
    dev.do("scope.run")
    assert dev._teardown, "启动采集后必须登记安全退出命令"
    dev.close()
    assert ":ACQuire:STATE OFF" in dev.commands
    # 1 kHz / 2 Vpp 正弦，采样 10 点/周期 —— 测量算法需至少两个过中点
    x_incr = 1e-4
    samples = sim_samples("sine", 1000.0, 2.0, 0.0, 200, 200 * x_incr, 0.0)
    values = measure_samples(samples, x_incr, 1000.0)
    assert abs(values["PK2PK"] - 2.0) < 0.05
    assert abs(values["FREQUENCY"] - 1000.0) / 1000.0 < 0.03
