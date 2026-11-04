"""应用装配：创建模型、服务、窗口与 Presenter，并把两侧接起来。"""

from __future__ import annotations

import sys

from .camera import CameraService
from .models import CameraModel
from .presenters import CameraPresenter, ProcessPresenter
from .qt_compat import QT_API, QtWidgets, exec_app
from .views.main_window import MainWindow


def build(argv=None):
    """构造应用与主窗口，返回 (app, window)。供 main() 与打包入口复用。"""
    app = QtWidgets.QApplication(argv if argv else sys.argv)
    app.setApplicationName("工业相机助手")
    app.setApplicationDisplayName("工业相机助手")
    app.setStyle("Fusion")

    model = CameraModel()
    service = CameraService(model)
    window = MainWindow()

    camera_presenter = CameraPresenter(window.camera_view, model, service)
    process_presenter = ProcessPresenter(window.process_view, model)
    window.bind(camera_presenter, process_presenter)

    window.set_message(f"Qt 绑定：{QT_API}｜无相机时自动使用仿真源", "info")
    return app, window


def main(argv=None) -> int:
    app, window = build(argv)
    window.show()
    return exec_app(app)
