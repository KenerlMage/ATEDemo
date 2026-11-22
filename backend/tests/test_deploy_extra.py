"""装备助手部署/卸载与下位机部署错误路径（SR02-AR05 ~ AR07）。"""
import json
from pathlib import Path

import pytest

import assistant_shelf
import deploy_extra


@pytest.fixture()
def shelf(tmp_path, monkeypatch):
    installed = tmp_path / "installed"
    packages = tmp_path / "packages"
    installed.mkdir()
    packages.mkdir()
    monkeypatch.setattr(assistant_shelf, "INSTALLED_DIR", installed)
    monkeypatch.setattr(assistant_shelf, "PACKAGES_DIR", packages)
    return {"installed": installed, "packages": packages}


def _assistant(shelf, aid="scope", scripts=None, deployed=False):
    dest = shelf["installed"] / aid
    dest.mkdir(parents=True)
    (dest / "run.bat").write_text("@echo off\n", encoding="utf-8")
    manifest = {
        "id": aid, "name": aid, "version": "1.0.0", "needs_deploy": True, "deployed": deployed,
        "scripts": scripts if scripts is not None else {
            "probe": "scripts/probe.bat", "install": "scripts/install.bat",
            "uninstall": "scripts/uninstall.bat",
        },
    }
    (dest / "assistant.json").write_text(json.dumps(manifest), encoding="utf-8")
    (dest / "scripts").mkdir()
    for name, code in (("probe", 10), ("install", 0), ("uninstall", 0)):
        (dest / "scripts" / f"{name}.bat").write_text(f"@echo off\r\nexit /b {code}\r\n", encoding="utf-8")
    # 安装包目录（成功部署后应被清理）
    (shelf["packages"] / aid).mkdir(parents=True)
    (shelf["packages"] / aid / "setup.zip").write_bytes(b"PK")
    return dest


def test_deploy_runs_install_and_cleans_package(shelf):
    _assistant(shelf)
    result = deploy_extra.deploy_assistant("scope")
    assert result["success"], result
    assert result["deployed"] is True
    assert not (shelf["packages"] / "scope").exists()  # 成功清包
    meta = json.loads((shelf["installed"] / "scope" / "assistant.json").read_text(encoding="utf-8"))
    assert meta["deployed"] is True


def test_deploy_failure_keeps_package(shelf):
    dest = _assistant(shelf)
    (dest / "scripts" / "install.bat").write_text("@echo off\r\nexit /b 3\r\n", encoding="utf-8")
    result = deploy_extra.deploy_assistant("scope")
    assert result["success"] is False
    assert result["error_code"] == "script_failed"
    assert (shelf["packages"] / "scope").exists()  # 失败保留


def test_probe_zero_skips_install(shelf):
    dest = _assistant(shelf)
    (dest / "scripts" / "probe.bat").write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    result = deploy_extra.deploy_assistant("scope")
    assert result["success"] is True and result["skipped"] is True


def test_undeploy_runs_uninstall(shelf):
    _assistant(shelf, deployed=True)
    result = deploy_extra.undeploy_assistant("scope")
    assert result["success"], result
    assert result["deployed"] is False
    meta = json.loads((shelf["installed"] / "scope" / "assistant.json").read_text(encoding="utf-8"))
    assert meta["deployed"] is False


def test_undeploy_without_script_is_error(shelf):
    _assistant(shelf, scripts={"install": "scripts/install.bat"})
    result = deploy_extra.undeploy_assistant("scope")
    assert result["success"] is False and result["error_code"] == "script_missing"


def test_status_only_after_download(shelf):
    assert deploy_extra.assistant_status("nope")["downloaded"] is False
    _assistant(shelf)
    status = deploy_extra.assistant_status("scope")
    assert status["downloaded"] is True and status["deployed"] is False


def test_remote_deploy_without_target_config():
    tps = {"deploy": [{"key": "d", "package": "pkg@1.0", "target": {"id": "dmm"}}],
           "device_config": {"dmm": {"role": "dmm"}}}
    result = deploy_extra.remote_deploy(tps, "t1", "dmm")
    assert result["success"] is False
    assert result["error_code"] == "remote_not_configured"


def test_remote_deploy_unknown_target():
    tps = {"deploy": [{"key": "d", "package": "pkg@1.0", "target": {"id": "dmm"}}],
           "device_config": {"dmm": {"role": "dmm"}}}
    result = deploy_extra.remote_deploy(tps, "t1", "ghost")
    assert result["error_code"] == "remote_not_configured"
