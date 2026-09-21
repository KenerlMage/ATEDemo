# -*- coding: utf-8 -*-
"""驱动层异常定义（全部继承 DriverError，方便上层统一兜底）"""

from __future__ import annotations


class DriverError(Exception):
    """驱动层通用异常（配置错误、协议错误、仪器返回异常等）"""

    def __init__(self, message: str, *, device_id: str = "", detail: str = ""):
        super().__init__(message)
        self.device_id = device_id
        self.detail = detail

    def to_dict(self) -> dict:
        return {"error": str(self), "device_id": self.device_id, "detail": self.detail}


class DriverNotFound(DriverError):
    """注册库里找不到可用的驱动实现 / 设备未登记"""


class TransportError(DriverError):
    """链路层错误：连不上、超时、写失败、串口打不开"""
