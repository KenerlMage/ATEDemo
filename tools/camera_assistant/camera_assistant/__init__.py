"""工业相机助手：Basler 相机调试与光斑测量（PySide2 / PySide6 + MVP）。

分层：
    models.py      数据与状态（无 Qt 依赖）
    analyze.py     光斑检测算法（纯 numpy）
    camera.py      相机后端与采集线程（pypylon 惰性导入）
    presenters.py  事件编排（无 Qt 依赖，可单测）
    mvp.py         View 契约与 Presenter 基类
    views/         控件装配（Qt）
"""

__version__ = "1.0.0"
__all__ = ["models", "analyze", "camera", "presenters", "mvp"]
