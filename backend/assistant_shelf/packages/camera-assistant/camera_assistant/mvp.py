"""MVP 契约：View 需要实现的方法集合，以及 Presenter 基类。

该模块不导入任何 Qt 组件，因此单元测试可以用假 View 直接验证 Presenter 的编排逻辑。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseView(ABC):
    """所有 View 的公共部分：消息提示。"""

    @abstractmethod
    def show_message(self, text: str, level: str = "info") -> None:
        """level 取值 info / warn / error。"""


class CameraView(BaseView):
    """流程一：相机调试视图。"""

    @abstractmethod
    def show_status(self, text: str, connected: bool, grabbing: bool) -> None:
        ...

    @abstractmethod
    def show_params(self, params) -> None:
        ...

    @abstractmethod
    def show_stream(self, array) -> None:
        ...


class ProcessView(BaseView):
    """流程二：图像处理视图。"""

    @abstractmethod
    def render_result(self, result, array) -> None:
        ...

    @abstractmethod
    def show_table(self, rows: list) -> None:
        ...

    @abstractmethod
    def show_summary(self, text: str) -> None:
        ...


class Presenter:
    """Presenter 基类：持有 View 与模型，负责把界面事件翻译为状态变更。"""

    def __init__(self, view) -> None:
        self.view = view

    def attach(self) -> None:
        """界面构造完成后调用，用于首屏回填。"""
