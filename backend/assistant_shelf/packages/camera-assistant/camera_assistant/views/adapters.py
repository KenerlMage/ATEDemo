"""视图适配器：把界面上分散的控件聚合成 Presenter 需要的 View 接口。"""

from __future__ import annotations

from ..mvp import CameraView, ProcessView


class CameraViewAdapter(CameraView):
    """流程一的视图聚合：面板状态、状态栏、流式画面。"""

    def __init__(self, window) -> None:
        self.window = window

    def show_message(self, text: str, level: str = "info") -> None:
        self.window.set_message(text, level)

    def show_status(self, text: str, connected: bool, grabbing: bool) -> None:
        self.window.camera_panel.show_status(text, connected, grabbing)
        self.window.status_camera.setText(text)

    def show_params(self, params) -> None:
        self.window.camera_panel.show_params(params)

    def show_stream(self, array) -> None:
        self.window.stream_view.show_frame(array)


class ProcessViewAdapter(ProcessView):
    """流程二的视图聚合：画布、表格、摘要。"""

    def __init__(self, window) -> None:
        self.window = window

    def show_message(self, text: str, level: str = "info") -> None:
        self.window.set_message(text, level)

    def render_result(self, result, array) -> None:
        self.window.process_panel.render_result(result, array)

    def show_table(self, rows: list) -> None:
        self.window.process_panel.show_table(rows)

    def show_summary(self, text: str) -> None:
        self.window.process_panel.show_summary(text)
