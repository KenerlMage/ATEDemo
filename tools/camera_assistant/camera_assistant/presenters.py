"""Presenter 层：事件响应与状态编排，不导入 Qt，便于单元测试。

流程一（相机调试）：连接、参数下发、采集启停、单帧落盘、状态刷新。
流程二（图像处理）：按设定频率取最新帧分析，把结果推给视图（画布 + 表格）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from .analyze import analyze
from .camera import CameraError, CameraService
from .models import AnalyzeParams, CameraModel, CameraParams, rows_of
from .mvp import CameraView, Presenter, ProcessView


class CameraPresenter(Presenter):
    """流程一：相机参数设置与曝光控制。"""

    def __init__(self, view: CameraView, model: CameraModel, service: CameraService) -> None:
        super().__init__(view)
        self.model = model
        self.service = service
        self._last_frame = -1
        self._last_status_at = 0.0

    # ---- 生命周期
    def attach(self) -> None:
        self.view.show_params(self.model.params)
        self._push_status()

    # ---- 事件
    def on_connect(self, prefer_real: bool = True) -> None:
        try:
            simulated, note = self.service.connect(self.model.params, prefer_real=prefer_real)
        except CameraError as exc:
            self.view.show_message(f"连接失败：{exc}", "error")
            self._push_status()
            return

        self.view.show_params(self.model.params)
        self.view.show_message(note or ("已连接相机（仿真）" if simulated else "已连接相机"), "warn" if simulated else "info")
        try:
            self.service.start()
        except CameraError as exc:
            self.view.show_message(f"采集启动失败：{exc}", "error")
        self._push_status()

    def on_disconnect(self) -> None:
        self.service.disconnect()
        self.view.show_message("已断开相机", "info")
        self._push_status()

    def on_start(self) -> None:
        try:
            self.service.start()
        except CameraError as exc:
            self.view.show_message(f"采集启动失败：{exc}", "error")
            self._push_status()
            return
        self.view.show_message("已开始连续采集", "info")
        self._push_status()

    def on_stop(self) -> None:
        self.service.stop()
        self.view.show_message("已停止采集", "info")
        self._push_status()

    def on_params_changed(self, **changes) -> None:
        params = self.model.update_params(**changes)
        warnings = self.service.apply_params(params)
        # 回填一次：模型可能对越界值做了收拢，界面需要反映真实生效值
        self.view.show_params(params)
        if warnings:
            self.view.show_message("；".join(warnings[:3]), "warn")
        self._push_status()

    def on_save_frame(self) -> Optional[Path]:
        try:
            path = self.service.save_frame(_snapshot_path())
        except CameraError as exc:
            self.view.show_message(f"保存失败：{exc}", "error")
            return None
        self.view.show_message(f"已保存 {path.name}", "info")
        return path

    # ---- 刷新（由界面定时器驱动）
    def on_tick(self) -> None:
        frame = self.model.latest
        if frame is not None and frame.index != self._last_frame:
            self._last_frame = frame.index
            self.view.show_stream(frame.array)
        now = time.time()
        if now - self._last_status_at >= 0.25:
            self._push_status()

    def _push_status(self) -> None:
        self._last_status_at = time.time()
        status = self.model.status
        self.view.show_status(status.label(), status.connected, status.grabbing)


class ProcessPresenter(Presenter):
    """流程二：图像处理与光斑测量。"""

    def __init__(self, view: ProcessView, model: CameraModel,
                 params: Optional[AnalyzeParams] = None, max_hz: float = 5.0) -> None:
        super().__init__(view)
        self.model = model
        self.params = params or AnalyzeParams()
        self.max_hz = max_hz
        self.auto = True
        self._last_index = -1
        self._last_at = 0.0
        self.last_result = None

    # ---- 事件
    def on_params_changed(self, **changes) -> None:
        merged = {**self.params.__dict__}
        merged.update({k: v for k, v in changes.items() if hasattr(self.params, k)})
        self.params = AnalyzeParams(**merged)
        self.run_once()

    def on_roi_changed(self, x: int, y: int, w: int, h: int) -> None:
        self.on_params_changed(roi=(int(x), int(y), int(w), int(h)))

    def on_auto_toggled(self, enabled: bool) -> None:
        self.auto = bool(enabled)
        if self.auto:
            self._last_index = -1
            self.view.show_message("已开启连续分析", "info")

    def on_analyze_once(self) -> None:
        self.run_once(force=True)

    # ---- 核心
    def run_once(self, force: bool = False) -> None:
        frame = self.model.latest
        if frame is None:
            self.view.show_message("尚无图像，请先在流程一启动采集", "warn")
            return
        self._analyze_and_show(frame, force=force)

    def on_tick(self) -> None:
        if not self.auto:
            return
        frame = self.model.latest
        if frame is None or frame.index == self._last_index:
            return
        now = time.time()
        if now - self._last_at < 1.0 / max(self.max_hz, 0.1):
            return
        self._analyze_and_show(frame)

    def _analyze_and_show(self, frame, force: bool = False) -> None:
        result = analyze(frame.array, self.params, frame.index)
        self.last_result = result
        self._last_index = frame.index
        self._last_at = time.time()
        self.view.render_result(result, frame.array)
        self.view.show_table(rows_of(result.blobs))
        self.view.show_summary(result.summary())


def _snapshot_path() -> Path:
    """截图落盘路径：工程目录下 snapshots/，文件名带时间戳。"""
    root = Path(__file__).resolve().parent.parent / "snapshots"
    return root / time.strftime("frame_%Y%m%d_%H%M%S.png")
