"""流程二面板：图像处理界面（matplotlib 画布 + 光斑数据表格）。"""

from __future__ import annotations

import numpy as np

from ..qt_compat import QtCore, QtWidgets, Signal

#: matplotlib 缺失时降级为提示页：流程一（相机调试）与整体窗口启动不受影响
MPL_ERROR = ""
try:
    import matplotlib

    matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False

    from matplotlib.figure import Figure                                # noqa: E402
    from matplotlib.patches import Rectangle                            # noqa: E402

    try:                                                                # noqa: E402
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    except ImportError:                                                 # PySide2 环境
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
except Exception as _exc:                                                # pragma: no cover
    matplotlib = None
    Figure = None
    Rectangle = None
    FigureCanvas = None
    MPL_ERROR = f"{type(_exc).__name__}: {_exc}"

COLUMNS = ["#", "亮度中心X", "亮度中心Y", "光斑中心X", "光斑中心Y", "等效直径", "面积", "峰值", "饱和"]
THRESHOLD_MODES = [("otsu", "Otsu 自动"), ("absolute", "绝对阈值"), ("relative", "相对峰值 %")]


class ProcessPanel(QtWidgets.QWidget):
    """图像处理界面。画布显示截取后的 ROI 与掩膜，右侧表格显示逐个光斑的测量值。"""

    paramsChanged = Signal(dict)
    roiChanged = Signal(int, int, int, int)
    analyzeOnce = Signal()
    autoToggled = Signal(bool)
    message = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loading = False

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(10)

        # ---- 左：画布
        left = QtWidgets.QVBoxLayout()
        head = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("像素图处理")
        title.setStyleSheet("font-weight:bold;color:#8B1A1A;font-size:13px;")
        self.summary = QtWidgets.QLabel("等待图像")
        self.summary.setStyleSheet("color:#6B6B66;font-size:11px;")
        head.addWidget(title)
        head.addSpacing(12)
        head.addWidget(self.summary, 1)
        left.addLayout(head)

        if Figure is not None and FigureCanvas is not None:
            self.figure = Figure(figsize=(6.0, 3.6), dpi=100, facecolor="#FFFFFF")
            self.canvas = FigureCanvas(self.figure)
            self.canvas.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            left.addWidget(self.canvas, 1)
        else:
            self.figure = None
            self.canvas = None
            missing = QtWidgets.QLabel(
                "缺少绘图依赖，本页暂不可用。\n"
                f"matplotlib 加载失败：{MPL_ERROR}\n"
                "安装后重启助手即可：pip install matplotlib"
            )
            missing.setWordWrap(True)
            missing.setAlignment(QtCore.Qt.AlignCenter)
            missing.setStyleSheet("color:#8B1A1A;font-size:12px;background:#FAFAF8;")
            left.addWidget(missing, 1)
        root.addLayout(left, 3)

        # ---- 右：表格与参数
        right = QtWidgets.QWidget()
        right.setMinimumWidth(400)
        right.setMaximumWidth(520)
        rbox = QtWidgets.QVBoxLayout(right)
        rbox.setContentsMargins(0, 0, 0, 0)
        rbox.setSpacing(8)

        bar = QtWidgets.QHBoxLayout()
        self.check_auto = QtWidgets.QCheckBox("连续分析")
        self.check_auto.setChecked(True)
        self.btn_once = QtWidgets.QPushButton("分析一帧")
        bar.addWidget(self.check_auto)
        bar.addStretch(1)
        bar.addWidget(self.btn_once)
        rbox.addLayout(bar)

        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        rbox.addWidget(self.table, 1)

        rbox.addWidget(self._build_params())
        root.addWidget(right, 2)

        self._wire()

    # ---- 参数区
    def _build_params(self) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("检测参数")
        form = QtWidgets.QFormLayout(box)
        form.setContentsMargins(10, 8, 10, 10)
        form.setSpacing(6)

        self.combo_mode = QtWidgets.QComboBox()
        for _, label in THRESHOLD_MODES:
            self.combo_mode.addItem(label)
        self.spin_threshold = QtWidgets.QDoubleSpinBox()
        self.spin_threshold.setRange(0.0, 255.0)
        self.spin_threshold.setDecimals(1)
        self.spin_threshold.setValue(60.0)
        self.spin_min_area = QtWidgets.QSpinBox()
        self.spin_min_area.setRange(1, 100_000)
        self.spin_min_area.setValue(12)
        self.spin_max_blobs = QtWidgets.QSpinBox()
        self.spin_max_blobs.setRange(1, 100)
        self.spin_max_blobs.setValue(32)
        self.check_invert = QtWidgets.QCheckBox("反相（亮背景上的暗斑）")

        form.addRow("阈值模式", self.combo_mode)
        form.addRow("阈值", self.spin_threshold)
        form.addRow("最小面积", self.spin_min_area)
        form.addRow("最多光斑", self.spin_max_blobs)
        form.addRow("", self.check_invert)

        roi_row = QtWidgets.QWidget()
        roi_grid = QtWidgets.QGridLayout(roi_row)
        roi_grid.setContentsMargins(0, 0, 0, 0)
        roi_grid.setSpacing(4)
        self.spin_rx, self.spin_ry = _spin(0, 8192, 1), _spin(0, 8192, 1)
        self.spin_rw, self.spin_rh = _spin(0, 8192, 1), _spin(0, 8192, 1)
        roi_grid.addWidget(QtWidgets.QLabel("X"), 0, 0)
        roi_grid.addWidget(self.spin_rx, 0, 1)
        roi_grid.addWidget(QtWidgets.QLabel("Y"), 0, 2)
        roi_grid.addWidget(self.spin_ry, 0, 3)
        roi_grid.addWidget(QtWidgets.QLabel("宽"), 1, 0)
        roi_grid.addWidget(self.spin_rw, 1, 1)
        roi_grid.addWidget(QtWidgets.QLabel("高"), 1, 2)
        roi_grid.addWidget(self.spin_rh, 1, 3)
        self.btn_full = QtWidgets.QPushButton("全幅")
        roi_grid.addWidget(self.btn_full, 1, 4)
        form.addRow("ROI", roi_row)
        return box

    def _wire(self) -> None:
        self.check_auto.toggled.connect(self.autoToggled)
        self.btn_once.clicked.connect(self.analyzeOnce)
        self.combo_mode.currentIndexChanged.connect(self._on_mode_changed)
        self.spin_threshold.valueChanged.connect(self._on_any)
        self.spin_min_area.valueChanged.connect(self._on_any)
        self.spin_max_blobs.valueChanged.connect(self._on_any)
        self.check_invert.toggled.connect(self._on_any)
        for spin in (self.spin_rx, self.spin_ry, self.spin_rw, self.spin_rh):
            spin.valueChanged.connect(self._on_roi)
        self.btn_full.clicked.connect(self._on_full)
        self._on_mode_changed(0)

    def _on_mode_changed(self, index: int) -> None:
        self._loading = True
        if index == 0:
            self.spin_threshold.setRange(0.0, 255.0)
            self.spin_threshold.setSuffix("")
            self.spin_threshold.setValue(0.0)
            self.spin_threshold.setEnabled(False)
        elif index == 1:
            self.spin_threshold.setRange(0.0, 255.0)
            self.spin_threshold.setSuffix(" 灰阶")
            self.spin_threshold.setValue(60.0)
            self.spin_threshold.setEnabled(True)
        else:
            self.spin_threshold.setRange(1.0, 99.0)
            self.spin_threshold.setSuffix(" %")
            self.spin_threshold.setValue(55.0)
            self.spin_threshold.setEnabled(True)
        self._loading = False
        self._on_any()

    def _on_any(self, *_args) -> None:
        if self._loading:
            return
        self.paramsChanged.emit(self.collect())

    def _on_roi(self, *_args) -> None:
        if self._loading:
            return
        self.roiChanged.emit(int(self.spin_rx.value()), int(self.spin_ry.value()),
                             int(self.spin_rw.value()), int(self.spin_rh.value()))

    def _on_full(self) -> None:
        self._loading = True
        for spin in (self.spin_rx, self.spin_ry, self.spin_rw, self.spin_rh):
            spin.setValue(0)
        self._loading = False
        self.roiChanged.emit(0, 0, 0, 0)

    # ---- 对外
    def collect(self) -> dict:
        return {
            "threshold_mode": THRESHOLD_MODES[self.combo_mode.currentIndex()][0],
            "threshold_value": float(self.spin_threshold.value()),
            "min_area": int(self.spin_min_area.value()),
            "max_blobs": int(self.spin_max_blobs.value()),
            "invert": bool(self.check_invert.isChecked()),
            "roi": (int(self.spin_rx.value()), int(self.spin_ry.value()),
                    int(self.spin_rw.value()), int(self.spin_rh.value())),
        }

    def set_roi(self, x: int, y: int, w: int, h: int) -> None:
        """由流程一的框选同步过来。"""
        self._loading = True
        self.spin_rx.setValue(x)
        self.spin_ry.setValue(y)
        self.spin_rw.setValue(w)
        self.spin_rh.setValue(h)
        self._loading = False

    def show_summary(self, text: str) -> None:
        self.summary.setText(text)

    def show_table(self, rows: list) -> None:
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = QtWidgets.QTableWidgetItem(str(value))
                if c >= 1:
                    item.setTextAlignment(int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter))
                self.table.setItem(r, c, item)

    def show_message(self, text: str, level: str = "info") -> None:
        self.message.emit(text, level)

    # ---- 绘制
    def render_result(self, result, array: np.ndarray) -> None:
        if self.figure is None or self.canvas is None:
            self.summary.setText("未安装 matplotlib，无法绘图")
            return
        self.figure.clear()
        ax_img = self.figure.add_subplot(1, 2, 1)
        ax_mask = self.figure.add_subplot(1, 2, 2)

        x, y, w, h = result.roi
        crop = array[y:y + h, x:x + w] if array is not None else None
        if crop is not None and crop.size:
            ax_img.imshow(crop, cmap="gray", vmin=0, vmax=255, interpolation="nearest")
        ax_img.set_title(f"ROI {w}×{h}", fontsize=9, color="#141414")
        ax_img.set_xticks([])
        ax_img.set_yticks([])

        for blob in result.blobs:
            bx, by, bw, bh = blob.bbox
            ax_img.add_patch(Rectangle((bx - x - 0.5, by - y - 0.5), bw, bh,
                                       fill=False, edgecolor="#8B1A1A", linewidth=0.8))
            ax_img.plot(blob.cx - x, blob.cy - y, "+", color="#1B5EA8", markersize=6,
                        markeredgewidth=1.0)
            ax_img.annotate(str(blob.index), (bx - x, by - y - 3), color="#8B1A1A", fontsize=7)

        if result.mask is not None:
            ax_mask.imshow(result.mask, cmap="gray", interpolation="nearest")
        ax_mask.set_title(f"掩膜 · 阈值 {result.threshold:.0f}", fontsize=9, color="#141414")
        ax_mask.set_xticks([])
        ax_mask.set_yticks([])

        self.figure.subplots_adjust(left=0.03, right=0.98, top=0.90, bottom=0.05, wspace=0.08)
        self.canvas.draw_idle()


def _spin(low: int, high: int, step: int) -> QtWidgets.QSpinBox:
    spin = QtWidgets.QSpinBox()
    spin.setRange(low, high)
    spin.setSingleStep(step)
    return spin
