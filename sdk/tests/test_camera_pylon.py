# -*- coding: utf-8 -*-
"""Basler pylon 相机驱动测试（仿真为主，真机路径验证报错可读性）

基线：pylon 8.0.0 / pypylon 8.x。测试不依赖相机硬件与 pypylon，可直接在 CI 跑。
覆盖点：
* 能力声明与家族基线一致（门禁口径）
* 仿真取帧的确定性、图样对比度、区域统计
* PNG 落盘结构与 sha256
* 连拍帧率与亮度稳定性
* 型号派发（含别名）与未知型号报错
* 缺 pypylon 时 real 模式的处置建议
* driver.json 与类声明一致
"""
from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path

import pytest

from ate_drivers import Camera, open_camera
from ate_drivers.capabilities import Capability, validate
from ate_drivers.errors import ConfigurationError, DriverError, DriverNotFound
from ate_drivers import camera_factory
from ate_drivers.family.camera import CameraSetup, frame_stats, simulate_frame
from ate_drivers.vendors.basler_pylon.driver import BaslerPylonCamera

MODEL = "acA1920-40gm"
HAS_PYPYLON = importlib.util.find_spec("pypylon") is not None


# ---------------------------------------------------------------- 声明与派发

def test_capabilities_cover_family_baseline():
    missing, unknown, _ = validate(BaslerPylonCamera.CAPABILITIES, BaslerPylonCamera.family)
    assert missing == [], "缺少家族基线能力: %s" % missing
    assert unknown == [], "平台不认识的能力: %s" % unknown
    assert Capability.CAMERA_GRAB in BaslerPylonCamera.CAPABILITIES
    assert BaslerPylonCamera.ACTIONS[Capability.CAMERA_SNAP] == "snap"


def test_factory_registers_every_declared_model():
    for model in BaslerPylonCamera.MODELS:
        assert camera_factory.camera_class(model) is BaslerPylonCamera
    assert camera_factory.self_check()["ok"], camera_factory.self_check()["problems"]


def test_alias_dispatches_to_basler():
    entry = camera_factory.camera_entry("BASLER")
    assert entry["driver"] == "basler-pylon"
    assert entry["alias_of"] == BaslerPylonCamera.MODELS[0]


def test_unknown_model_is_rejected_with_listing():
    with pytest.raises(DriverNotFound) as exc:
        Camera("no-such-camera-9000")
    assert "已登记型号" in (exc.value.detail or "")


def test_manifest_matches_class_declaration():
    manifest_path = (Path(__file__).resolve().parents[1]
                     / "ate_drivers" / "vendors" / "basler_pylon" / "driver.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["family"] == BaslerPylonCamera.family
    assert manifest["key"] == BaslerPylonCamera.driver_key
    assert [m for m in manifest["models"]] == list(BaslerPylonCamera.MODELS)
    assert set(manifest["capabilities"]) == set(BaslerPylonCamera.CAPABILITIES)
    module_name, _, cls_name = manifest["entry"].partition(":")
    assert module_name.endswith("basler_pylon.driver") and cls_name == "BaslerPylonCamera"


# ---------------------------------------------------------------- 仿真会话

def test_simulate_open_identify_and_state():
    with open_camera(MODEL, mode="simulate") as cam:
        ident = cam.identity()
        assert ident["ok"] and "BASLER" in ident["value"]
        assert ident["simulated"] is True
        state = cam.status()["state"]
        assert state["family"] == "camera"
        assert state["setup"]["pixel_format"] == "Mono8"
        assert state["pylon"]["baseline"] == "8.0.0"


def test_enumerate_returns_simulated_devices():
    with open_camera(MODEL, mode="simulate") as cam:
        res = cam.enumerate()
        assert res["value"] and len(res["value"]) == 2
        assert res["value"][0]["model"].startswith("acA")


def test_configure_validates_parameters():
    cam = Camera(MODEL, mode="simulate")
    with pytest.raises(ConfigurationError):
        cam.configure(width=4)                      # 低于下限 16
    with pytest.raises(ConfigurationError):
        cam.configure(pattern="hologram")           # 未知图样
    with pytest.raises(ConfigurationError):
        cam.configure(exposure_us=0)                # 曝光不允许 0


def test_grab_is_deterministic_for_same_setup():
    def first_frame() -> dict:
        with open_camera(MODEL, mode="simulate", setup={"pattern": "checker", "level": 160}) as cam:
            return cam.grab(thumbnail=False)["value"]["stats"]

    a, b = first_frame(), first_frame()
    assert a == b, "同参数同帧序号的仿真帧必须一致（TPS 才能写阈值断言）"
    assert a["contrast"] > 80


def test_pattern_changes_optical_signature():
    with open_camera(MODEL, mode="simulate") as cam:
        flat = cam.configure(pattern="flat", level=120, noise=0.0)
        assert flat["value"]["pattern"] == "flat"
        s_flat = cam.grab(thumbnail=False)["value"]["stats"]
        cam.configure(pattern="edge", level=200, noise=0.0)
        s_edge = cam.grab(thumbnail=False)["value"]["stats"]
    assert s_flat["std"] < 1.0, s_flat
    assert s_edge["contrast"] > 100, s_edge
    assert s_edge["mean"] < s_edge["max"]


def test_stats_region_narrows_area():
    with open_camera(MODEL, mode="simulate", setup={"pattern": "spot", "level": 240}) as cam:
        full = cam.stats(region="full")["value"]
        center = cam.stats(region="center")["value"]
    assert center["pixels"] < full["pixels"]
    assert center["mean"] > full["mean"], "光斑在中心，中心区域均值必须更高"


def test_snap_writes_readable_png(tmp_path):
    target = tmp_path / "cam" / "frame_0001.png"
    with open_camera(MODEL, mode="simulate", setup={"width": 64, "height": 48, "level": 200}) as cam:
        res = cam.snap(str(target))
    value = res["value"]
    assert target.exists() and value["bytes"] > 100
    assert len(value["sha256"]) == 64
    data = target.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (64, 48)
    assert value["stats"]["mean"] == pytest.approx(200, abs=8)


def test_sequence_reports_fps_and_stability():
    with open_camera(MODEL, mode="simulate", setup={"level": 120, "noise": 1.0}) as cam:
        res = cam.sequence(count=6, interval_ms=5)["value"]
    assert res["count"] == 6 and len(res["frames"]) == 6
    assert 0 < res["fps"] < 1000
    assert res["mean_spread"] < 12, "同一稳定光源下连拍均值不应大幅漂移"


def test_trigger_in_simulate_advances_frame():
    with open_camera(MODEL, mode="simulate") as cam:
        cam.connect()
        first = cam.trigger()["value"]["frame_id"]
        second = cam.trigger()["value"]["frame_id"]
    assert second == first + 1


def test_api_self_test_passes():
    with open_camera(MODEL, mode="simulate") as cam:
        report = cam.self_test()
    assert report["ok"], report
    assert [c["check"] for c in report["checks"]] == ["identify", "configure", "grab", "stats"]


def test_context_manager_closes():
    cam = Camera(MODEL, mode="simulate")
    with cam:
        cam.grab(thumbnail=False)
    assert cam.status()["state"]["connected"] is False


# ---------------------------------------------------------------- 真机路径

@pytest.mark.skipif(HAS_PYPYLON, reason="本机已安装 pypylon，无法验证缺失时的报错路径")
def test_real_mode_without_pypylon_gives_actionable_error():
    cam = Camera(MODEL, mode="real")
    with pytest.raises(DriverError) as exc:
        cam.connect()
    message = str(exc.value)
    detail = exc.value.detail or ""
    assert "pypylon" in message
    assert "8.0.0" in detail and "pylon Viewer" in detail


def test_hook_contract_requires_vendor_implementation():
    """家族基类不实现真机钩子：漏实现时必须显式报错而不是静默通过"""
    from ate_drivers.family.camera import CameraDriver

    cam = CameraDriver({"device_id": "X", "model": "generic"}, mode="real")
    with pytest.raises(NotImplementedError):
        cam._open_device()


# ---------------------------------------------------------------- 图像工具

def test_simulate_frame_and_stats_are_pure_python_safe():
    setup = CameraSetup(width=96, height=64, pattern="checker", level=100, noise=0.0).validate()
    pixels = simulate_frame(setup, 0)
    assert len(pixels) == 64 and len(pixels[0]) == 96
    stats = frame_stats(pixels)
    assert stats["pixels"] == 96 * 64
    assert stats["min"] < stats["max"]
    assert stats["contrast"] == pytest.approx(stats["max"] - stats["min"], abs=0.01)
