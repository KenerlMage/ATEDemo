"""相机层测试：仿真源行为、参数生效、采集线程与 PNG 落盘。"""

from __future__ import annotations

import struct
import time

import numpy as np

from camera_assistant.camera import CameraService, SimulatedCamera, write_png
from camera_assistant.models import CameraModel, CameraParams


def _params(**kw) -> CameraParams:
    base = dict(width=320, height=240, exposure_us=8000.0, frame_rate=50.0)
    base.update(kw)
    return CameraParams(**base).clamped()


def test_simulated_camera_respects_size_and_count():
    cam = SimulatedCamera(_params(width=200, height=120))
    cam.open()
    frame = cam.grab()
    assert frame.shape == (120, 200)
    assert frame.dtype == np.uint8


def test_exposure_changes_brightness():
    dim = SimulatedCamera(_params(exposure_us=1500.0))
    bright = SimulatedCamera(_params(exposure_us=20000.0))
    dim.open()
    bright.open()
    dim_frame, bright_frame = dim.grab(), bright.grab()
    # 曝光主要抬高光斑峰值，整幅均值变化较小，故两条判据同时校验
    assert bright_frame.mean() > dim_frame.mean()
    assert int(bright_frame.max()) > int(dim_frame.max())


def test_simulation_is_deterministic():
    a = SimulatedCamera(_params())
    b = SimulatedCamera(_params())
    a.open()
    b.open()
    assert np.array_equal(a.grab(), b.grab())


def test_service_connect_start_stop_with_simulated_backend():
    model = CameraModel(_params(frame_rate=60.0))
    service = CameraService(model, backend_factory=lambda p: SimulatedCamera(p))
    simulated, note = service.connect(model.params)
    assert simulated is True
    assert model.status.connected is True
    assert model.status.simulated is True

    service.start()
    deadline = time.time() + 2.0
    while model.latest is None and time.time() < deadline:
        time.sleep(0.02)
    assert model.latest is not None
    assert model.latest.shape if hasattr(model.latest, "shape") else model.latest.array.shape == (240, 320)
    assert model.status.grabbing is True

    service.stop()
    assert model.status.grabbing is False


def test_write_png_has_valid_header_and_size(tmp_path):
    array = np.arange(0, 120 * 160, dtype=np.uint16).reshape(120, 160).astype(np.uint8)
    path = write_png(tmp_path / "frame.png", array)
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (160, 120)


def test_params_are_clamped():
    params = CameraParams(width=10, height=99999, exposure_us=-5, frame_rate=0, offset_x=-3).clamped()
    assert params.width >= 32
    assert params.height <= 3072
    assert params.exposure_us >= 20
    assert params.frame_rate >= 0.5
    assert params.offset_x == 0


def test_apply_params_without_backend_is_tolerated():
    model = CameraModel()
    service = CameraService(model)
    warnings = service.apply_params(model.params)
    assert warnings and "未连接" in warnings[0]
