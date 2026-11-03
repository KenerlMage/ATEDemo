"""流式图像显示控件：等比缩放显示整幅图像，支持鼠标拖拽框选 ROI。"""

from __future__ import annotations

import numpy as np

from ..qt_compat import QtCore, QtGui, QtWidgets, Signal, Qt, to_qimage


class _ImageCanvas(QtWidgets.QWidget):
    """纯绘制控件：画位图 + 当前选择框，负责屏幕坐标与图像坐标的换算。"""

    selectionFinished = Signal(int, int, int, int)      # x, y, w, h（图像坐标系）

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(480, 360)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._pixmap: QtGui.QPixmap | None = None
        self._image_size = (0, 0)
        self._origin = QtCore.QPointF(0, 0)
        self._scale = 1.0
        self._anchor: QtCore.QPointF | None = None
        self._current: QtCore.QRectF | None = None
        self.setMouseTracking(True)

    # ---- 图像
    def set_image(self, array: np.ndarray) -> None:
        image = to_qimage(array)
        self._image_size = (image.width(), image.height())
        self._pixmap = QtGui.QPixmap.fromImage(image)
        self.update()

    def clear(self) -> None:
        self._pixmap = None
        self._current = None
        self.update()

    # ---- 坐标换算
    def _layout(self) -> None:
        if self._pixmap is None:
            self._scale, self._origin = 1.0, QtCore.QPointF(0, 0)
            return
        iw, ih = self._image_size
        scale = min(self.width() / iw, self.height() / ih)
        self._scale = scale
        self._origin = QtCore.QPointF((self.width() - iw * scale) / 2.0,
                                      (self.height() - ih * scale) / 2.0)

    def _to_image(self, point: QtCore.QPointF) -> QtCore.QPointF:
        self._layout()
        return QtCore.QPointF((point.x() - self._origin.x()) / max(self._scale, 1e-9),
                              (point.y() - self._origin.y()) / max(self._scale, 1e-9))

    # ---- 绘制
    def paintEvent(self, event) -> None:      # noqa: N802
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#101010"))
        if self._pixmap is None:
            painter.setPen(QtGui.QColor("#8A8A8A"))
            painter.drawText(self.rect(), Qt.AlignCenter, "尚无图像：请先连接相机并开始采集")
            return

        self._layout()
        iw, ih = self._image_size
        target = QtCore.QRectF(self._origin.x(), self._origin.y(), iw * self._scale, ih * self._scale)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(target, self._pixmap, QtCore.QRectF(self._pixmap.rect()))

        if self._current is not None and self._scale > 0:
            rect = QtCore.QRectF(self._origin.x() + self._current.x() * self._scale,
                                 self._origin.y() + self._current.y() * self._scale,
                                 self._current.width() * self._scale,
                                 self._current.height() * self._scale)
            painter.setPen(QtGui.QPen(QtGui.QColor("#F0B429"), 1.4, Qt.DashLine))
            painter.drawRect(rect)
            painter.fillRect(rect, QtGui.QColor(240, 180, 41, 28))

    # ---- 交互
    def mousePressEvent(self, event) -> None:      # noqa: N802
        if self._pixmap is None or event.button() != Qt.LeftButton:
            return
        self._anchor = event.position() if hasattr(event, "position") else event.localPos()
        self._current = QtCore.QRectF(self._anchor, self._anchor)
        self.update()

    def mouseMoveEvent(self, event) -> None:       # noqa: N802
        if self._anchor is None:
            return
        point = event.position() if hasattr(event, "position") else event.localPos()
        self._current = QtCore.QRectF(self._anchor, point).normalized()
        self.update()

    def mouseReleaseEvent(self, event) -> None:    # noqa: N802
        if self._anchor is None:
            return
        self._anchor = None
        rect = self._current
        if rect is None or rect.width() < 6 or rect.height() < 6:
            self._current = None
            self.update()
            return

        top_left = self._to_image(rect.topLeft())
        bottom_right = self._to_image(rect.bottomRight())
        iw, ih = self._image_size
        x = int(max(0, min(top_left.x(), iw - 1)))
        y = int(max(0, min(top_left.y(), ih - 1)))
        w = int(max(1, min(bottom_right.x() - x, iw - x)))
        h = int(max(1, min(bottom_right.y() - y, ih - y)))
        self.selectionFinished.emit(x, y, w, h)


class StreamView(QtWidgets.QWidget):
    """带标题栏与提示的流式显示视图。"""

    roiSelected = Signal(int, int, int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("实时图像")
        title.setStyleSheet("font-weight:bold;color:#8B1A1A;font-size:13px;")
        hint = QtWidgets.QLabel("左键拖拽可框选 ROI，供流程二截取")
        hint.setStyleSheet("color:#6B6B66;font-size:11px;")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(hint)
        layout.addLayout(header)

        self.canvas = _ImageCanvas(self)
        layout.addWidget(self.canvas, 1)
        self.canvas.selectionFinished.connect(self.roiSelected)

    def show_frame(self, array: np.ndarray) -> None:
        self.canvas.set_image(array)
