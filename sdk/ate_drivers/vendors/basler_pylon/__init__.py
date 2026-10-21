# -*- coding: utf-8 -*-
"""Basler 厂商驱动包（pylon 8.0.0）

包内只放差异面：GenICam 节点映射、设备枚举与选机、取帧、像素格式归一。
动作语义与统计口径全部继承自 `ate_drivers.family.camera.CameraDriver`。
"""

from .driver import BaslerPylonCamera  # noqa: F401

__all__ = ["BaslerPylonCamera"]
