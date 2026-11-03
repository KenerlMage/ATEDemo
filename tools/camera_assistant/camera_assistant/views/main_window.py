"""主窗口：左侧流程导航 + 中央视图切换 + 状态栏。

只做装配与事件转发，业务判断全部在 Presenter 中完成。
"""

from __future__ import annotations

from ..qt_compat import QtCore, QtGui, QtWidgets
from .adapters import CameraViewAdapter, ProcessViewAdapter
from .camera_panel import CameraPanel
from .process_panel import ProcessPanel
from .stream_view import StreamView

REFRESH_MS = 33          # 界面刷新节拍，约 30 Hz


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("工业相机助手 · Basler 调试与光斑测量")
        self.resize(1440, 880)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav = QtWidgets.QListWidget()
        self.nav.setFixedWidth(176)
        self.nav.addItems(["① 相机调试", "② 图像处理"])
        self.nav.setStyleSheet(
            "QListWidget{border:none;border-right:1px solid #D9D6CD;background:#FAFAF8;"
            "font-size:13px;padding-top:6px;}"
            "QListWidget::item{height:44px;padding-left:14px;}"
            "QListWidget::item:selected{background:#FFFFFF;color:#8B1A1A;"
            "border-left:3px solid #8B1A1A;font-weight:bold;}"
        )
        layout.addWidget(self.nav)

        self.stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack, 1)

        # 流程一：流式画面 + 相机面板
        self.stream_view = StreamView()
        self.camera_panel = CameraPanel()
        page_camera = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(page_camera)
        row.setContentsMargins(10, 8, 10, 10)
        row.setSpacing(10)
        row.addWidget(self.stream_view, 1)
        row.addWidget(self.camera_panel)
        self.stack.addWidget(page_camera)

        # 流程二：图像处理
        self.process_panel = ProcessPanel()
        self.stack.addWidget(self.process_panel)

        self.setCentralWidget(central)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        # 状态栏
        self.status_camera = QtWidgets.QLabel("未连接")
        self.status_camera.setStyleSheet("color:#4A4A46;font-size:11px;padding-left:6px;")
        self.status_msg = QtWidgets.QLabel("就绪")
        self.status_msg.setStyleSheet("color:#1F3F66;font-size:11px;padding-left:12px;")
        self.statusBar().addWidget(self.status_camera)
        self.statusBar().addWidget(self.status_msg, 1)

        self.camera_view = CameraViewAdapter(self)
        self.process_view = ProcessViewAdapter(self)

        self._camera_presenter = None
        self._process_presenter = None
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._on_tick)

        self._build_menu()

    # ---- 菜单
    def _build_menu(self) -> None:
        bar = self.menuBar()
        m_file = bar.addMenu("文件(&F)")
        act_save = QtGui.QAction("保存当前帧(&S)", self)
        act_save.setShortcut("Ctrl+S")
        act_save.triggered.connect(lambda: self._camera_presenter and self._camera_presenter.on_save_frame())
        m_file.addAction(act_save)
        m_file.addSeparator()
        act_quit = QtGui.QAction("退出(&Q)", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_view = bar.addMenu("视图(&V)")
        for index, name in enumerate(("相机调试", "图像处理")):
            action = QtGui.QAction(name, self)
            action.triggered.connect(lambda _checked=False, i=index: self.nav.setCurrentRow(i))
            m_view.addAction(action)

        m_help = bar.addMenu("帮助(&H)")
        act_about = QtGui.QAction("关于", self)
        act_about.triggered.connect(self._about)
        m_help.addAction(act_about)

    def _about(self) -> None:
        QtWidgets.QMessageBox.about(
            self, "关于",
            "工业相机助手\n\n"
            "流程一：相机参数设置与曝光控制\n"
            "流程二：像素图处理与光斑测量\n\n"
            "架构：MVP（Model / View / Presenter）\n"
            "无相机时自动使用仿真源，便于先调算法。",
        )

    # ---- 装配
    def bind(self, camera_presenter, process_presenter) -> None:
        self._camera_presenter = camera_presenter
        self._process_presenter = process_presenter

        panel = self.camera_panel
        panel.connectRequested.connect(lambda prefer_real: camera_presenter.on_connect(prefer_real))
        panel.disconnectRequested.connect(camera_presenter.on_disconnect)
        panel.startRequested.connect(camera_presenter.on_start)
        panel.stopRequested.connect(camera_presenter.on_stop)
        panel.saveRequested.connect(camera_presenter.on_save_frame)
        panel.paramsChanged.connect(lambda changes: camera_presenter.on_params_changed(**changes))
        panel.message.connect(self.set_message)

        proc = self.process_panel
        proc.paramsChanged.connect(lambda changes: process_presenter.on_params_changed(**changes))
        proc.roiChanged.connect(process_presenter.on_roi_changed)
        proc.analyzeOnce.connect(process_presenter.on_analyze_once)
        proc.autoToggled.connect(process_presenter.on_auto_toggled)
        proc.message.connect(self.set_message)

        self.stream_view.roiSelected.connect(self._on_roi_selected)

        camera_presenter.attach()
        self._timer.start(REFRESH_MS)

    def _on_roi_selected(self, x: int, y: int, w: int, h: int) -> None:
        self.process_panel.set_roi(x, y, w, h)
        if self._process_presenter is not None:
            self._process_presenter.on_roi_changed(x, y, w, h)
        self.nav.setCurrentRow(1)
        self.set_message(f"已框选 ROI {w}×{h}，流程二已按此区域截取", "info")

    def _on_tick(self) -> None:
        if self._camera_presenter is not None:
            self._camera_presenter.on_tick()
        if self._process_presenter is not None:
            self._process_presenter.on_tick()

    # ---- 消息
    def set_message(self, text: str, level: str = "info") -> None:
        color = {"info": "#1F3F66", "warn": "#8B6A1A", "error": "#8B1A1A"}.get(level, "#1F3F66")
        self.status_msg.setStyleSheet(f"color:{color};font-size:11px;padding-left:12px;")
        self.status_msg.setText(text)

    def closeEvent(self, event) -> None:      # noqa: N802
        self._timer.stop()
        if self._camera_presenter is not None:
            self._camera_presenter.on_disconnect()
        super().closeEvent(event)
