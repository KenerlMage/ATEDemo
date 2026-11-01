# -*- coding: utf-8 -*-
"""常驻置顶监视窗的回归用例（不拉起真实窗口，可在 CI 跑）

单独运行: python -m pytest test_cases/test_monitor_hud.py -v
真机联调用 .openclaw/tmp/monitor/test_hud.py（会开关一次 Edge 窗口）。
"""

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import monitor_hud as mh  # noqa: E402


@pytest.fixture()
def ctl(tmp_path):
    return mh.HudController(tmp_path)


def test_available_reports_platform(ctl):
    """环境自检必须给出明确结论：可用 / 不可用及原因"""
    info = ctl.available()
    assert set(info) >= {"ok", "reason", "edge", "win32"}
    if sys.platform == "win32":
        assert info["win32"] is True
        assert info["ok"] in (True, False)
        if info["ok"]:
            assert Path(info["edge"]).exists()
        else:
            assert info["reason"]
    else:
        assert info["ok"] is False and "Windows" in info["reason"]


def test_state_file_roundtrip(ctl):
    """位置与置顶偏好落盘后可读回"""
    ctl.save_state(x=100, y=80, w=600, h=400, topmost=False)
    saved = ctl.load_state()
    assert (saved["x"], saved["y"], saved["w"], saved["h"]) == (100, 80, 600, 400)
    assert saved["topmost"] is False
    state_file = ctl.state_path
    assert state_file.is_file()
    assert json.loads(state_file.read_text(encoding="utf-8"))["w"] == 600


def test_state_file_survives_broken_content(ctl):
    """状态文件损坏时按默认值处理，不能抛错"""
    ctl.state_path.parent.mkdir(parents=True, exist_ok=True)
    ctl.state_path.write_text("{ not json", encoding="utf-8")
    assert ctl.load_state() == {}
    ctl.save_state(x=1, y=2, w=3, h=4)
    assert ctl.load_state()["w"] == 3


def test_default_rect_within_screen(ctl):
    """默认位置贴右上角，尺寸不越界"""
    rect = ctl._default_rect()
    assert rect["w"] >= 320 and rect["h"] >= 200
    if sys.platform == "win32":
        assert rect["x"] > 0 and rect["y"] >= 0


def test_operations_without_window_are_safe(ctl):
    """窗口未运行时的调用一律返回失败而非抛错（执行链路不能被打断）"""
    assert ctl.status()["running"] is False
    assert ctl.set_topmost(True)["success"] is False
    assert ctl.set_visible(True)["success"] is False
    assert ctl.apply_rect(10, 10, 480, 320)["success"] is False
    assert ctl.stop()["success"] is True           # 幂等：未运行也算成功


def test_schedule_stop_noop_without_window(ctl):
    """未运行时时延收尾为空操作"""
    ctl.schedule_stop(0.1)
    assert ctl.alive() is False


def test_auto_start_respects_env(monkeypatch, tmp_path):
    """ATE_HUD_AUTO=0 时不自动拉起（CI / 无桌面环境）"""
    monkeypatch.setenv("ATE_HUD_AUTO", "0")
    assert mh.auto_start("T-1") is None
    monkeypatch.setenv("ATE_HUD_AUTO", "1")
    monkeypatch.setattr(mh, "IS_WIN", False)        # 非 Windows 也不拉起
    assert mh.auto_start("T-1") is None
