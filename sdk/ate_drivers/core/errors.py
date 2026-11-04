# -*- coding: utf-8 -*-
"""驱动层异常：全部带**稳定错误码**

上层（TPS 用例、报告、自检清单）按 `code` 判定分支，**不允许解析中文消息文本**——
消息可以改文案，错误码不能改。新增错误码只能追加 `E_*` 常量。
"""

from __future__ import annotations


class DriverError(Exception):
    """驱动层异常基类"""

    code = "E_DRIVER"

    def __init__(self, message: str, *, device_id: str = "", detail: str = "", code: str = ""):
        super().__init__(message)
        self.device_id = device_id
        self.detail = detail
        if code:
            self.code = code

    def to_dict(self) -> dict:
        return {"code": self.code, "error": str(self), "device_id": self.device_id, "detail": self.detail}


class ConfigurationError(DriverError):
    """设备连接参数不完整或非法（缺 IP / 端口 / 串口）"""

    code = "E_CONFIG"


class DriverNotFound(DriverError):
    """注册库里找不到可用的驱动实现，或设备未登记"""

    code = "E_NOT_FOUND"


class TransportError(DriverError):
    """链路层错误：连不上、写失败、串口打不开"""

    code = "E_TRANSPORT"


class DriverTimeout(TransportError):
    """读写超时（与链路错误的处置不同：可重试）"""

    code = "E_TIMEOUT"


class ProtocolError(DriverError):
    """仪器返回不符合协议（应答无法解析、字段缺失）"""

    code = "E_PROTOCOL"


class UnsupportedCapability(DriverError):
    """驱动未声明该能力（调用方应先 `supports()` 再调用）"""

    code = "E_UNSUPPORTED"


class ContractMismatch(DriverError):
    """驱动契约版本与平台不兼容（加载阶段即拒绝）"""

    code = "E_CONTRACT"
