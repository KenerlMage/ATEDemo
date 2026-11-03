"""Presenter 测试：用假 View 验证事件编排，不需要启动 Qt 界面。"""

from __future__ import annotations

import time

import numpy as np
import pytest

from camera_assistant import presenters
from camera_assistant.camera import CameraService, SimulatedCamera
from camera_assistant.models import AnalyzeParams, CameraModel, CameraParams
from camera_assistant.mvp import CameraView, ProcessView
from camera_assistant.presenters import CameraPresenter, ProcessPresenter


class FakeCameraView(CameraView):
    def __init__(self) -> None:
        self.messages: list = []
        self.status = None
        self.params = None
        self.frames = 0

    def show_message(self, text: str, level: str = "info") -> None:
        self.messages.append((text, level))

    def show_status(self, text: str, connected: bool, grabbing: bool) -> None:
        self.status = (text, connected, grabbing)

    def show_params(self, params) -> None:
        self.params = params

    def show_stream(self, array) -> None:
        self.frames += 1


class FakeProcessView(ProcessView):
    def __init__(self) -> None:
        self.messages: list = []
        self.rows = None
        self.summary = ""
        self.results: list = []

    def show_message(self, text: str, level: str = "info") -> None:
        self.messages.append((text, level))

    def render_result(self, result, array) -> None:
        self.results.append(result)

    def show_table(self, rows: list) -> None:
        self.rows = rows

    def show_summary(self, text: str) -> None:
        self.summary = text


@pytest.fixture()
def rig():
    model = CameraModel(CameraParams(width=320, height=240, frame_rate=60.0))
    service = CameraService(model, backend_factory=lambda p: SimulatedCamera(p))
    view = FakeCameraView()
    presenter = CameraPresenter(view, model, service)
    presenter.attach()
    yield presenter, view, model, service
    service.disconnect()


def test_connect_turns_on_grabbing_and_reports_simulated(rig):
    presenter, view, model, service = rig
    presenter.on_connect()
    assert view.status[1] is True and view.status[2] is True
    assert model.status.simulated is True
    assert view.messages and "仿真" in view.messages[-1][0]


def test_param_change_updates_model(rig):
    presenter, view, model, _ = rig
    presenter.on_connect()
    presenter.on_params_changed(exposure_us=24000.0, gain_db=3.0)
    assert model.params.exposure_us == 24000.0
    assert model.params.gain_db == 3.0
    assert view.params.exposure_us == 24000.0


def test_tick_pushes_new_frames(rig):
    presenter, view, model, _ = rig
    presenter.on_connect()
    deadline = time.time() + 2.0
    while model.latest is None and time.time() < deadline:
        presenter.on_tick()
        time.sleep(0.02)
    assert model.latest is not None
    before = view.frames
    presenter.on_tick()
    assert view.frames >= before


def test_save_frame_writes_png(rig, monkeypatch, tmp_path):
    presenter, view, model, _ = rig
    monkeypatch.setattr(presenters, "_snapshot_path", lambda: tmp_path / "shot.png")
    presenter.on_connect()
    deadline = time.time() + 2.0
    while model.latest is None and time.time() < deadline:
        time.sleep(0.02)
    path = presenter.on_save_frame()
    assert path is not None and path.exists()
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_process_presenter_requires_frame():
    model = CameraModel()
    view = FakeProcessView()
    presenter = ProcessPresenter(view, model, AnalyzeParams())
    presenter.on_analyze_once()
    assert view.messages and view.messages[-1][1] == "warn"


def test_process_presenter_analyzes_and_fills_table(rig):
    camera_presenter, _, model, _ = rig
    camera_presenter.on_connect()
    deadline = time.time() + 2.0
    while model.latest is None and time.time() < deadline:
        time.sleep(0.02)

    view = FakeProcessView()
    presenter = ProcessPresenter(view, model, AnalyzeParams(min_area=8), max_hz=50.0)
    presenter.on_tick()

    assert presenter.last_result is not None
    assert len(view.results) == 1
    assert view.rows is not None and len(view.rows) == presenter.last_result.count
    assert "光斑" in view.summary


def test_process_presenter_skips_same_frame(rig):
    camera_presenter, _, model, _ = rig
    camera_presenter.on_connect()
    deadline = time.time() + 2.0
    while model.latest is None and time.time() < deadline:
        time.sleep(0.02)
    camera_presenter.on_stop()          # 冻结帧号

    view = FakeProcessView()
    presenter = ProcessPresenter(view, model, AnalyzeParams(min_area=8), max_hz=1000.0)
    presenter.on_tick()
    presenter.on_tick()
    assert len(view.results) == 1


def test_process_presenter_auto_toggle(rig):
    _, _, model, _ = rig
    view = FakeProcessView()
    presenter = ProcessPresenter(view, model)
    presenter.on_auto_toggled(False)
    assert presenter.auto is False
    presenter.on_auto_toggled(True)
    assert presenter.auto is True


def test_roi_change_propagates(rig):
    _, _, model, _ = rig
    view = FakeProcessView()
    presenter = ProcessPresenter(view, model)
    presenter.on_roi_changed(40, 30, 120, 90)
    assert presenter.params.roi == (40, 30, 120, 90)
