"""流程一面板：相机参数设置与曝光控制。"""

from __future__ import annotations

from ..qt_compat import QtCore, QtWidgets, Signal


class CameraPanel(QtWidgets.QWidget):
    """相机调试面板。所有控件变更都汇成一个 dict 抛出，由 Presenter 决定如何下发。"""

    paramsChanged = Signal(dict)
    connectRequested = Signal(bool)
    disconnectRequested = Signal()
    startRequested = Signal()
    stopRequested = Signal()
    saveRequested = Signal()
    message = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        self.setMinimumWidth(360)
        self.setMaximumWidth(430)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        # ---- 状态
        self.state_label = QtWidgets.QLabel("未连接")
        self.state_label.setStyleSheet("font-size:15px;font-weight:bold;color:#8B1A1A;")
        self.mode_label = QtWidgets.QLabel("模式：-")
        self.mode_label.setStyleSheet("color:#4A4A46;font-size:11px;")
        root.addWidget(self.state_label)
        root.addWidget(self.mode_label)

        # ---- 连接与采集
        box_conn = QtWidgets.QGroupBox("连接与采集")
        grid = QtWidgets.QGridLayout(box_conn)
        grid.setContentsMargins(10, 8, 10, 10)
        grid.setSpacing(6)

        self.btn_connect = QtWidgets.QPushButton("连接相机")
        self.btn_disconnect = QtWidgets.QPushButton("断开")
        self.btn_start = QtWidgets.QPushButton("开始采集")
        self.btn_stop = QtWidgets.QPushButton("停止")
        self.btn_save = QtWidgets.QPushButton("保存当前帧")
        for btn in (self.btn_connect, self.btn_disconnect, self.btn_start, self.btn_stop, self.btn_save):
            btn.setMinimumHeight(28)
        grid.addWidget(self.btn_connect, 0, 0, 1, 2)
        grid.addWidget(self.btn_disconnect, 0, 2)
        grid.addWidget(self.btn_start, 1, 0, 1, 2)
        grid.addWidget(self.btn_stop, 1, 2)
        grid.addWidget(self.btn_save, 2, 0, 1, 3)
        root.addWidget(box_conn)

        # ---- 曝光与增益
        box_exp = QtWidgets.QGroupBox("曝光与增益")
        form = QtWidgets.QFormLayout(box_exp)
        form.setContentsMargins(10, 8, 10, 10)
        form.setSpacing(6)

        self.spin_exposure = QtWidgets.QDoubleSpinBox()
        self.spin_exposure.setRange(20.0, 1_000_000.0)
        self.spin_exposure.setSingleStep(100.0)
        self.spin_exposure.setDecimals(0)
        self.spin_exposure.setSuffix(" µs")

        self.slider_exposure = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_exposure.setRange(20, 100_000)
        self.slider_exposure.setSingleStep(100)

        self.check_auto = QtWidgets.QCheckBox("自动曝光")
        self.spin_gain = QtWidgets.QDoubleSpinBox()
        self.spin_gain.setRange(0.0, 48.0)
        self.spin_gain.setSingleStep(0.5)
        self.spin_gain.setDecimals(1)
        self.spin_gain.setSuffix(" dB")

        form.addRow("曝光时间", self.spin_exposure)
        form.addRow("", self.slider_exposure)
        form.addRow("", self.check_auto)
        form.addRow("增益", self.spin_gain)
        root.addWidget(box_exp)

        # ---- 采集参数
        box_acq = QtWidgets.QGroupBox("采集与触发")
        form2 = QtWidgets.QFormLayout(box_acq)
        form2.setContentsMargins(10, 8, 10, 10)
        form2.setSpacing(6)

        self.spin_rate = QtWidgets.QDoubleSpinBox()
        self.spin_rate.setRange(0.5, 200.0)
        self.spin_rate.setDecimals(1)
        self.spin_rate.setSuffix(" fps")
        self.combo_format = QtWidgets.QComboBox()
        self.combo_format.addItems(["Mono8", "Mono12", "Mono12Packed"])
        self.combo_trigger = QtWidgets.QComboBox()
        self.combo_trigger.addItems(["连续采集", "软触发"])
        form2.addRow("帧率", self.spin_rate)
        form2.addRow("像素格式", self.combo_format)
        form2.addRow("触发方式", self.combo_trigger)
        root.addWidget(box_acq)

        # ---- 视野
        box_roi = QtWidgets.QGroupBox("视野（ROI）")
        grid2 = QtWidgets.QGridLayout(box_roi)
        grid2.setContentsMargins(10, 8, 10, 10)
        grid2.setSpacing(6)
        self.spin_w = _spin(32, 4096, 8)
        self.spin_h = _spin(32, 3072, 8)
        self.spin_ox = _spin(0, 4096, 8)
        self.spin_oy = _spin(0, 3072, 8)
        for col, (label, widget) in enumerate((("宽", self.spin_w), ("高", self.spin_h))):
            grid2.addWidget(QtWidgets.QLabel(label), 0, col * 2)
            grid2.addWidget(widget, 0, col * 2 + 1)
        for col, (label, widget) in enumerate((("X 偏移", self.spin_ox), ("Y 偏移", self.spin_oy))):
            grid2.addWidget(QtWidgets.QLabel(label), 1, col * 2)
            grid2.addWidget(widget, 1, col * 2 + 1)
        root.addWidget(box_roi)
        root.addStretch(1)

        note = QtWidgets.QLabel("提示：无相机时自动使用仿真源，参数调整同样生效，便于先调算法再接真机。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#6B6B66;font-size:11px;")
        root.addWidget(note)

        self._wire()

    # ---- 内部
    def _wire(self) -> None:
        self.btn_connect.clicked.connect(lambda: self.connectRequested.emit(True))
        self.btn_disconnect.clicked.connect(self.disconnectRequested)
        self.btn_start.clicked.connect(self.startRequested)
        self.btn_stop.clicked.connect(self.stopRequested)
        self.btn_save.clicked.connect(self.saveRequested)

        self.spin_exposure.valueChanged.connect(self._on_exposure_spin)
        self.slider_exposure.valueChanged.connect(self._on_exposure_slider)
        self.check_auto.toggled.connect(self._on_any)
        self.spin_gain.valueChanged.connect(self._on_any)
        self.spin_rate.valueChanged.connect(self._on_any)
        self.combo_format.currentTextChanged.connect(self._on_any)
        self.combo_trigger.currentTextChanged.connect(self._on_any)
        for spin in (self.spin_w, self.spin_h, self.spin_ox, self.spin_oy):
            spin.valueChanged.connect(self._on_any)

    def _on_exposure_spin(self, value: float) -> None:
        self._loading = True
        self.slider_exposure.setValue(int(min(value, self.slider_exposure.maximum())))
        self._loading = False
        self._on_any()

    def _on_exposure_slider(self, value: int) -> None:
        if self._loading:
            return
        self._loading = True
        self.spin_exposure.setValue(float(value))
        self._loading = False
        self._on_any()

    def _on_any(self, *_args) -> None:
        if self._loading:
            return
        self.paramsChanged.emit(self.collect())

    # ---- 对外
    def collect(self) -> dict:
        return {
            "exposure_us": float(self.spin_exposure.value()),
            "gain_db": float(self.spin_gain.value()),
            "auto_exposure": bool(self.check_auto.isChecked()),
            "frame_rate": float(self.spin_rate.value()),
            "pixel_format": self.combo_format.currentText(),
            "trigger_mode": "software" if self.combo_trigger.currentIndex() == 1 else "continuous",
            "width": int(self.spin_w.value()),
            "height": int(self.spin_h.value()),
            "offset_x": int(self.spin_ox.value()),
            "offset_y": int(self.spin_oy.value()),
        }

    def show_params(self, params) -> None:
        self._loading = True
        self.spin_exposure.setValue(params.exposure_us)
        self.slider_exposure.setValue(int(min(params.exposure_us, self.slider_exposure.maximum())))
        self.spin_gain.setValue(params.gain_db)
        self.check_auto.setChecked(params.auto_exposure)
        self.spin_rate.setValue(params.frame_rate)
        self.combo_format.setCurrentText(params.pixel_format)
        self.combo_trigger.setCurrentIndex(1 if params.trigger_mode == "software" else 0)
        self.spin_w.setValue(params.width)
        self.spin_h.setValue(params.height)
        self.spin_ox.setValue(params.offset_x)
        self.spin_oy.setValue(params.offset_y)
        self._loading = False

    def show_status(self, text: str, connected: bool, grabbing: bool) -> None:
        self.state_label.setText(text)
        self.mode_label.setText(("模式：连续采集中" if grabbing else "模式：已连接未采集") if connected else "模式：-")
        self.btn_connect.setEnabled(not connected)
        self.btn_disconnect.setEnabled(connected)
        self.btn_start.setEnabled(connected and not grabbing)
        self.btn_stop.setEnabled(grabbing)
        self.btn_save.setEnabled(connected)

    def show_message(self, text: str, level: str = "info") -> None:
        self.message.emit(text, level)


def _spin(low: int, high: int, step: int) -> QtWidgets.QSpinBox:
    spin = QtWidgets.QSpinBox()
    spin.setRange(low, high)
    spin.setSingleStep(step)
    return spin
