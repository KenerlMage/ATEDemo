# -*- coding: utf-8 -*-
"""相机驱动与装备助手的回归用例

单独运行: python -m pytest test_cases/test_camera_tools.py -v
覆盖三类曾经出问题的地方：
  ① 参数批量设置必须原子：非法值不能把会话状态改坏；
  ② 仿真帧是确定性数据，统计口径要稳定；
  ③ 落盘与导出只能发生在指定目录内。
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from drivers import get_driver  # noqa: E402
from drivers.errors import DriverError  # noqa: E402


def make_camera(mode: str = "simulate", **extra):
    row = {
        "device_id": "camera-test",
        "name": "Basler 工业相机",
        "model": "acA1920-40gm",
        "category": "camera",
        "role": "camera",
        "device_type": "basler-pylon",
        "interface": "GigE",
        "extra": extra or {},
    }
    return get_driver(row, mode=mode, alias="camera")


# ---------------- 参数设置 ----------------


def test_configure_is_atomic_on_invalid_input():
    """非法尺寸抛错，但已有参数保持不变（历史缺陷：setup 被写成 4x480）"""
    cam = make_camera()
    cam.open()
    before = dict(cam.setup)
    with pytest.raises(DriverError):
        cam.configure(width=4)
    assert cam.setup["width"] == before["width"]
    assert cam.setup["height"] == before["height"]
    cam.close()


def test_configure_applies_valid_batch():
    """合法批量设置全部生效，并记录 changed"""
    cam = make_camera()
    cam.open()
    res = cam.configure(width=640, height=480, pattern="checker", level=180, noise=0.0)
    assert res["value"]["width"] == 640
    assert res["value"]["pattern"] == "checker"
    assert set(res["changed"]) >= {"width", "height", "pattern", "level", "noise"}
    cam.close()


def test_configure_clamps_level_and_noise():
    """亮度与噪声按驱动约定夹取，不报错"""
    cam = make_camera()
    cam.open()
    res = cam.configure(level=999, noise=-5)
    assert res["value"]["level"] == 255
    assert res["value"]["noise"] == 0.0
    cam.close()


def test_exposure_range_is_validated():
    """曝光时长超出 10 µs..60 s 必须被拒"""
    cam = make_camera()
    cam.open()
    with pytest.raises(DriverError):
        cam.configure(exposure_us=1)
    with pytest.raises(DriverError):
        cam.configure(exposure_us=61_000_000)
    cam.close()


# ---------------- 采集与落盘 ----------------


def test_simulate_grab_is_deterministic_and_sized():
    """同参数重复抓帧的统计一致，尺寸与设置一致"""
    cam = make_camera()
    cam.open()
    cam.configure(width=64, height=48, pattern="flat", level=120, noise=0.0)
    first = cam.grab()["value"]
    second = cam.grab()["value"]
    assert (first["width"], first["height"]) == (64, 48)
    assert first["stats"]["mean"] == pytest.approx(120.0, abs=0.01)
    assert first["stats"]["mean"] == pytest.approx(second["stats"]["mean"], abs=0.01)
    cam.close()


def test_snap_writes_png(tmp_path):
    """snap 落盘的确实是 PNG（PNG 魔数 + 非空）"""
    cam = make_camera()
    cam.open()
    cam.configure(width=48, height=32)
    target = tmp_path / "cam.png"
    res = cam.snap(path=str(target))["value"]
    assert target.is_file()
    assert target.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert res["bytes"] == target.stat().st_size
    cam.close()


def test_sequence_saves_requested_count(tmp_path):
    """连拍按请求帧数落盘，文件名带序号"""
    cam = make_camera()
    cam.open()
    cam.configure(width=32, height=24)
    res = cam.sequence(count=3, interval_ms=0, save_dir=str(tmp_path), prefix="reg")["value"]
    assert res["count"] == 3
    assert len(res["saved"]) == 3
    assert (tmp_path / "reg_0000.png").is_file()
    cam.close()


# ---------------- 装备助手会话 ----------------


def test_session_frames_stay_inside_root(tmp_path, monkeypatch):
    """抓帧、导出都落在 FRAME_ROOT 内，越界路径被拒"""
    import camera_tools as ct

    monkeypatch.setattr(ct, "FRAME_ROOT", tmp_path, raising=False)
    sess = ct.CameraSession(mode="simulate")
    sess.open()
    frame = sess.grab(note="回归")
    target = Path(tmp_path, frame["path"])
    assert target.is_file()
    assert frame["url"].startswith("/api/tools/camera/frame?path=")

    exported = tmp_path / "exports" / "PN0001.png"
    exported.parent.mkdir(parents=True, exist_ok=True)
    exported.write_bytes(target.read_bytes())
    assert exported.is_file()
    with pytest.raises(ValueError):
        ct._frame_path("../../backend/main.py")
    sess.close()


def test_auto_expose_keeps_exposure_when_frame_ignores_it(tmp_path, monkeypatch):
    """仿真帧亮度与曝光无关：自动曝光应停止拉高并保留原曝光"""
    import camera_tools as ct

    monkeypatch.setattr(ct, "FRAME_ROOT", tmp_path, raising=False)
    sess = ct.CameraSession(mode="simulate")
    sess.open()
    sess.exposure(exposure_us=5000)
    out = sess.auto_expose(target=200, max_rounds=4)
    assert out["responsive"] is False
    assert out["converged"] is False
    assert out["final_exposure_us"] == pytest.approx(5000.0, abs=0.5)
    sess.close()
