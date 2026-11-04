# -*- coding: utf-8 -*-
"""RsInstrument 链路适配（**L3 厂商层内部实现**）。

RsInstrument 是罗德与施瓦茨官方发布的 Python 库：它既建会话（底层 pyvisa），
又封装 R&S 自己的命令风格与错误处理。它的层位与 NI-VISA、Basler Pylon 相同——
**厂商 SDK**，因此只能出现在 L3，不能下沉到 L0 传输层：一旦 ``transport.py``
认识它，所有驱动都被迫依赖某一家厂商的库。

对外形态与 :class:`ate_drivers.core.transport.Transport` 完全一致
（``open`` / ``close`` / ``write`` / ``query``），家族层与门面层感知不到差别。
没装 RsInstrument 时由驱动回退到标准库链路，行为与原来一致。
"""

from __future__ import annotations

from typing import Optional

from ...core.endpoint import visa_resource_for
from ...core.errors import ConfigurationError, ProtocolError, TransportError
from ...core.transport import Transport

__all__ = ["RsInstrumentLink", "rsinstrument_available"]


def rsinstrument_available() -> bool:
    """RsInstrument 是否可用（缺它时驱动退回标准库链路）"""
    try:
        import RsInstrument  # noqa: F401
    except Exception:
        return False
    return True


def resource_name_for(cfg) -> str:
    """把设备档案换算成 RsInstrument 认的资源名（网口 raw socket / 串口 ASRL）"""
    if cfg is None:
        return ""
    explicit = str(getattr(cfg, "visa_resource", "") or "").strip()
    if explicit:
        return explicit
    return visa_resource_for(
        str(getattr(cfg, "interface", "") or ""),
        str(getattr(cfg, "host", "") or ""),
        getattr(cfg, "port", None),
        str(getattr(cfg, "serial_port", "") or ""),
    )


class RsInstrumentLink(Transport):
    """用 RsInstrument 会话收发命令；接口与其它链路一致，可整替。"""

    kind = "rsinstrument"

    def __init__(self, cfg=None, timeout: float = 2.0, handler=None):
        super().__init__(cfg, timeout=timeout, handler=handler)
        self.resource_name = resource_name_for(cfg)
        self.instrument = None
        self.idn_string = ""

    # -------- 会话 --------
    def _open(self) -> None:
        if not self.resource_name:
            raise ConfigurationError(
                "RsInstrument 缺少连接参数",
                detail="档案里没有 host/port，也没有 serial_port 或 visa_resource",
            )
        try:
            from RsInstrument import RsInstrument
        except ImportError as e:  # pragma: no cover - 缺包时由驱动回退
            raise ConfigurationError(
                "未安装 RsInstrument", detail="pip install RsInstrument"
            ) from e
        try:
            # id_query=False：型号校验由驱动自己按 *IDN? 做，避免构造期多发一次查询
            self.instrument = RsInstrument(self.resource_name, id_query=False, reset=False)
            self.instrument.visa_timeout = int(max(0.1, self.timeout) * 1000)
            self.idn_string = str(getattr(self.instrument, "idn_string", "") or "")
        except Exception as e:
            self.instrument = None
            raise TransportError(
                f"RsInstrument 打开会话失败: {e}",
                detail=(
                    "资源名 %s；RsInstrument 默认调用 R&S VISA（RsVisa32.dll），"
                    "该机未装时把设备档案的 extra.rsinstrument 设为 off，"
                    "即可回落到标准库 socket / 串口链路" % self.resource_name
                ),
            ) from e

    def _close(self) -> None:
        inst, self.instrument = self.instrument, None
        if inst is not None:
            try:
                inst.close()
            except Exception:  # 关闭失败不影响上层清理
                pass

    # -------- 收发：走 RsInstrument 文本接口，不经基类字节路径 --------
    def write(self, command: str) -> None:
        if not self.opened or self.instrument is None:
            raise TransportError("链路未打开", detail="请先 open()")
        try:
            self.instrument.write_str(str(command))
            self.written += 1
        except Exception as e:
            self.last_error = str(e)
            raise TransportError(f"写失败: {e}") from e

    def query(self, command: str, timeout: Optional[float] = None) -> str:
        if not self.opened or self.instrument is None:
            raise TransportError("链路未打开", detail="请先 open()")
        if timeout:
            self.instrument.visa_timeout = int(max(0.1, float(timeout)) * 1000)
        try:
            text = self.instrument.query_str(str(command))
            self.written += 1
            self.read_count += 1
            return text
        except Exception as e:
            self.last_error = str(e)
            raise TransportError(f"查询失败: {e}") from e

    def _read_line(self, timeout: float) -> str:
        # RsInstrument 只提供「写读一体」的查询接口，单独读没有对应语义
        raise ProtocolError(
            "RsInstrument 链路不支持单独读取", detail="把写与读合并成一次 query"
        )
