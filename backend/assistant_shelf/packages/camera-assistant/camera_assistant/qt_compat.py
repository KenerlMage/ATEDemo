"""Qt 绑定兼容层。

工程按 PySide2 API 编写（Python ≤ 3.10 的产线环境常用），在 Python ≥ 3.11 上
PySide2 不再提供官方轮子，因此运行期允许回退到 PySide6；两者 API 在本工程用到的
范围内一致，差异集中在本文件内消化。
"""

from __future__ import annotations

import os

QT_API = ""
QT6 = False

try:                                    # 首选 PySide2
    from PySide2 import QtCore, QtGui, QtWidgets          # noqa: F401
    from PySide2.QtCore import QObject, Qt, Signal, Slot  # noqa: F401
    QT_API = "PySide2"
    QT6 = False
except ImportError:                     # 回退 PySide6
    try:
        from PySide6 import QtCore, QtGui, QtWidgets          # noqa: F401
        from PySide6.QtCore import QObject, Qt, Signal, Slot  # noqa: F401
        QT_API = "PySide6"
        QT6 = True
    except ImportError as exc:          # 两者都没有：给出可执行的修复提示
        raise ImportError(
            "未找到 Qt 绑定。安装方式：\n"
            "  Python 3.8–3.10：pip install PySide2\n"
            "  Python 3.11+  ：pip install PySide6（本工程自动兼容）"
        ) from exc

os.environ.setdefault("QT_API", QT_API.lower())

# matplotlib 选后端前必须知道用哪个绑定
MPL_QT_BACKEND = "QtAgg" if QT6 else "Qt5Agg"


def exec_app(app) -> int:
    """QApplication 的主循环入口，屏蔽 exec_ 与 exec 的命名差异。"""
    runner = getattr(app, "exec", None) or getattr(app, "exec_")
    return int(runner())


def to_qimage(array):
    """uint8 灰度 ndarray → QImage（copy 以脱离 numpy 缓冲区生命周期）。"""
    if array.ndim != 2:
        raise ValueError("仅支持二维灰度图")
    height, width = array.shape
    image = QtGui.QImage(array.data, width, height, int(array.strides[0]),
                         QtGui.QImage.Format_Grayscale8)
    return image.copy()
