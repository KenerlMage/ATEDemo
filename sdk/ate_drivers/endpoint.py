# -*- coding: utf-8 -*-
"""连接方式层：一台仪器走**网口**还是**串口**，由这里解析成 `Endpoint` 再交给链路层。

职责分得很清：

* 型号派发回答"这台设备该用哪个驱动"（`factory` / `Scope`）；
* 本模块回答"这条链子该怎么接"——`LAN`（host + port）还是 `SERIAL`（串口名 + 波特率 + 帧格式）；
* 具体收发在 `transport`（`SocketTransport` / `SerialTransport` / `VisaTransport`）。

**两件事分开看**：`kind`（连接方式）回答"物理怎么接"，`backend`（传输后端）回答"用哪套客户端栈收发"。

| 后端 | 网口 | 串口 | 依赖 |
| --- | --- | --- | --- |
| `native`（默认） | 标准库 socket | pyserial（惰性导入） | 零硬依赖 |
| `visa` | pyvisa 资源 `TCPIP0::host::port::SOCKET` | pyvisa 资源 `ASRL6::INSTR`，帧格式走会话属性 | pyvisa（惰性导入） |

后端解析顺序与连接方式同规矩——**显式参数 > 档案字段 > 默认**；`auto` 表示"装了 pyvisa 就走 VISA"，
**实际生效的后端永远可查**（`Scope.backend` / `status()["backend"]`），不做静默切换。

解析顺序（**不猜**）：显式 `interface=` > 设备档案里的 `interface` 字段 > 端口填充情况。
档案里网口与串口参数都填了却没有显式声明 → 报 `E_CONFIG`「连接方式不明确」，
要求把档案写清楚，而不是替用户挑一条。

    from ate_drivers.endpoint import endpoint_from

    ep = endpoint_from({"model": "MSO54", "serial_port": "COM6", "baudrate": 115200})
    ep.kind        # 'SERIAL'
    ep.label       # 'COM6@115200,8N1'
    cfg = ep.to_device_config(model="MSO54")   # 交给驱动/链路层
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .errors import ConfigurationError

LAN = "LAN"
SERIAL = "SERIAL"
SUPPORTED_INTERFACES = (LAN, SERIAL)

#: 档案里 interface 字段的常见写法 → 规范值
_INTERFACE_ALIASES = {
    "LAN": LAN, "NET": LAN, "NETWORK": LAN, "ETHERNET": LAN, "ENET": LAN,
    "TCP": LAN, "TCPIP": LAN, "IP": LAN, "VXI11": LAN, "VXI-11": LAN, "SOCKET": LAN,
    "SERIAL": SERIAL, "SER": SERIAL, "COM": SERIAL, "RS232": SERIAL, "RS-232": SERIAL,
    "RS485": SERIAL, "RS-485": SERIAL, "UART": SERIAL, "TTL": SERIAL, "ASRL": SERIAL,
}

#: 表示"没有链路"（仿真 / 不接链路的设备档案）
NO_LINK = ("", "NONE", "PASSIVE", "NA", "N/A", "-")

#: 串口默认参数（档案没填时用；8N1 是仪器串口最常见配置）
SERIAL_DEFAULTS = {"baudrate": 115200, "bytesize": 8, "parity": "N", "stopbits": 1}
_PARITIES = ("N", "E", "O", "M", "S")
_STOPBITS = (1, 1.5, 2)
_BYTESIZES = (5, 6, 7, 8)


# ---------------------------------------------------------------- 传输后端
#: 传输后端：`native` = 标准库（socket / pyserial）；`visa` = PyVISA（NI-VISA / Keysight VISA / pyvisa-py）
NATIVE = "native"
VISA = "visa"
AUTO = "auto"
SUPPORTED_BACKENDS = (NATIVE, VISA)          # 驱动可声明的后端（不含 auto）
BACKEND_CHOICES = (NATIVE, VISA, AUTO)       # 调用时可传的取值
DEFAULT_BACKEND = NATIVE

#: 后端写法 → 规范值（现场文档里各种叫法很多，归一化后只认两个）
_BACKEND_ALIASES = {
    "NATIVE": NATIVE, "STDLIB": NATIVE, "STD": NATIVE, "SOCKET": NATIVE, "RAW": NATIVE,
    "PYSERIAL": NATIVE, "SERIAL": NATIVE, "NONE": NATIVE, "NO": NATIVE, "": NATIVE,
    "VISA": VISA, "PYVISA": VISA, "PYVISA-PY": VISA, "VISAPY": VISA, "NI": VISA,
    "NI-VISA": VISA, "NIVISA": VISA, "KEYSIGHT": VISA, "IVI": VISA, "TMC": VISA,
    "AUTO": AUTO,
}


def normalize_backend(value: Any) -> str:
    """把后端写法归一化为 `native` / `visa` / `auto`；认不出返回空串（交给调用方报错）"""
    text = str(value or "").strip().upper().replace(" ", "").replace("_", "-")
    return _BACKEND_ALIASES.get(text, "")


def backends_of(cls: Any) -> tuple:
    """读驱动声明的传输后端：优先 `BACKENDS`（可多值），兼容旧写法 `BACKEND`（单值）。

    未声明时回落到 `(\"native\",)`——原生栈永远可用，所以旧驱动不会因此失效。
    声明里不许写 `auto`（"自动"不是一种能力，是调用时的选择）。
    """
    raw = getattr(cls, "BACKENDS", None)
    if raw is None:
        single = getattr(cls, "BACKEND", "") or ""
        raw = (single,) if single else ()
    elif isinstance(raw, str):
        raw = (raw,)
    out = []
    for item in raw or ():
        backend = normalize_backend(item)
        if backend in SUPPORTED_BACKENDS and backend not in out:
            out.append(backend)
    return tuple(out) or (DEFAULT_BACKEND,)


def resolve_backend(explicit: Any = None, archive: Any = None, visa_available: bool = False) -> tuple:
    """解析传输后端，返回 `(请求值, 实际值)`。

    * 显式 `backend=` > 档案里的 `backend` 字段 > 默认 `native`；
    * `auto`：装了 pyvisa（或注入过会话工厂）就用 `visa`，否则用 `native`；
    * 认不出的取值 → `E_CONFIG`，**不静默退回原生**（现场写错了要当场看见）。
    """
    raw = explicit if explicit not in ("", None) else _pick(archive, "backend")
    if raw in ("", None):
        return DEFAULT_BACKEND, DEFAULT_BACKEND
    requested = normalize_backend(raw)
    if not requested:
        raise ConfigurationError("传输后端不在支持范围：%r" % (raw,),
                                 detail="应为 %s 之一" % (BACKEND_CHOICES,))
    if requested == AUTO:
        return AUTO, (VISA if visa_available else NATIVE)
    return requested, requested


def visa_resource_for(kind: str, host: str = "", port: Any = None, port_name: str = "") -> str:
    """本端点的**规范 VISA 资源名**（只有 `backend=\"visa\"` 时才参与连接）

    * 网口：`TCPIP0::<host>::<port>::SOCKET`（raw socket 形式）；
    * 串口：`ASRL<n>::INSTR`（`COM6` → `ASRL6::INSTR`，VISA 规范写法）；
      非 COM 形式（如 `/dev/ttyUSB0`）用 pyvisa-py 的路径写法 `ASRL::/dev/ttyUSB0::INSTR`。
      **波特率与帧格式不是资源名的一部分**，由会话属性下发（`baud_rate` / `data_bits` / `parity` / `stop_bits`）。

    真机上若用 NI-VISA 与 pyvisa-py 的路径写法有差异，按实际 VISA 实现调整档案里的串口名即可。
    """
    if kind == LAN and host:
        return "TCPIP0::%s::%s::SOCKET" % (host, "" if port in (None, "") else port)
    if kind == SERIAL and port_name:
        found = re.match(r"^COM(\d+)$", str(port_name).strip().upper())
        if found:
            return "ASRL%s::INSTR" % found.group(1)
        return "ASRL::%s::INSTR" % port_name
    return ""


def normalize_interface(value: Any) -> str:
    """把档案/驱动里的连接方式写法归一化为 `LAN` / `SERIAL`；认不出返回空串"""
    text = str(value or "").strip().upper().replace(" ", "")
    return _INTERFACE_ALIASES.get(text, "")


def interfaces_of(cls: Any) -> tuple:
    """读驱动声明的连接方式：优先 `INTERFACES`（可多值），兼容旧写法 `INTERFACE`（单值）"""
    raw = getattr(cls, "INTERFACES", None)
    if raw is None:
        single = getattr(cls, "INTERFACE", "") or ""
        raw = (single,) if single else ()
    elif isinstance(raw, str):
        raw = (raw,)
    out = []
    for item in raw or ():
        kind = normalize_interface(item)
        if kind and kind not in out:
            out.append(kind)
    return tuple(out)


def _pick(source: Any, fieldname: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        value = source.get(fieldname)
    else:
        value = getattr(source, fieldname, None)
    return None if value in ("", None) else value


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return default if value in ("", None) else int(float(value))
    except (TypeError, ValueError):
        return default


@dataclass
class Endpoint:
    """一条链路的端点描述（网口或串口）；`kind` 只取 LAN / SERIAL"""

    kind: str = ""
    # 网口
    host: str = ""
    port: Optional[int] = None
    protocol: str = ""
    # 串口
    port_name: str = ""
    baudrate: Optional[int] = None
    bytesize: int = SERIAL_DEFAULTS["bytesize"]
    parity: str = SERIAL_DEFAULTS["parity"]
    stopbits: float = SERIAL_DEFAULTS["stopbits"]
    # 公共
    device_id: str = ""
    timeout: float = 2.0
    extra: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- 判定
    @property
    def is_lan(self) -> bool:
        return self.kind == LAN

    @property
    def is_serial(self) -> bool:
        return self.kind == SERIAL

    @property
    def is_real(self) -> bool:
        """是否指向一条真实链路（仿真时为 False）"""
        return self.kind in SUPPORTED_INTERFACES

    @property
    def label(self) -> str:
        """人能读的端点：`192.168.10.41:4000` / `COM6@115200,8N1`"""
        if self.is_lan:
            return "%s:%s" % (self.host, self.port if self.port is not None else "")
        if self.is_serial:
            stop = float(self.stopbits)
            stop_text = str(int(stop)) if stop == int(stop) else str(stop)
            return "%s@%s,%s%s%s" % (self.port_name, self.baudrate,
                                     int(self.bytesize), str(self.parity), stop_text)
        return ""

    @property
    def visa_resource(self) -> str:
        """规范 VISA 资源名（`backend="visa"` 时真正参与连接；`resource` 只用于报告与日志）"""
        return visa_resource_for(self.kind, self.host, self.port, self.port_name)

    def describe(self) -> dict:
        base = {"kind": self.kind, "label": self.label, "device_id": self.device_id}
        if self.is_lan:
            base.update({"host": self.host, "port": self.port, "protocol": self.protocol})
        elif self.is_serial:
            base.update({"serial_port": self.port_name, "baudrate": self.baudrate,
                         "bytesize": self.bytesize, "parity": self.parity,
                         "stopbits": self.stopbits})
        return base

    # ---------------------------------------------------------------- 转档案
    def to_device_config(self, model: str = "", vendor: str = "", category: str = "",
                         role: str = "", device_id: str = "", name: str = "",
                         timeout: Optional[float] = None, extra: Optional[dict] = None) -> Any:
        """转成驱动/链路层用的 `DeviceConfig`（连接参数按本端点填写）"""
        from .models import DeviceConfig

        common = dict(
            device_id=device_id or self.device_id or model, name=name or model,
            model=model, vendor=vendor, category=category, role=role,
            interface=self.kind or "NONE", programmable=True,
            timeout=float(self.timeout if timeout is None else timeout),
        )
        if self.is_lan:
            common.update({"host": self.host, "port": self.port,
                           "protocol": self.protocol or "TCP"})
        elif self.is_serial:
            common.update({"serial_port": self.port_name, "baudrate": self.baudrate,
                           "protocol": "SCPI (串口)",
                           "extra": {"bytesize": self.bytesize, "parity": self.parity,
                                     "stopbits": self.stopbits}})
        if extra:
            merged = dict(common.get("extra") or {})
            merged.update(extra)
            common["extra"] = merged
        return DeviceConfig(**common)


def endpoint_from(source: Any = None, *, interface: Any = None, host: Any = None, port: Any = None,
                  serial_port: Any = None, baudrate: Any = None, bytesize: Any = None,
                  parity: Any = None, stopbits: Any = None, device_id: str = "",
                  timeout: Optional[float] = 2.0) -> Optional[Endpoint]:
    """从设备档案（dict / ORM 行 / DeviceConfig）或显式参数解析连接方式。

    * 网口参数不完整（只给 host 或只给 port）→ `E_CONFIG`
    * 串口参数不完整（没给串口名）→ `E_CONFIG`
    * 档案里两种参数都填了且未显式声明 `interface` → `E_CONFIG`「连接方式不明确」
    * 什么都没给 → `None`（表示没有真实链路，走仿真）
    """
    src = source
    host = host if host is not None else _pick(src, "host")
    port = port if port is not None else _pick(src, "port")
    serial_port = serial_port if serial_port is not None else _pick(src, "serial_port")
    baudrate = baudrate if baudrate is not None else _pick(src, "baudrate")
    device_id = device_id or str(_pick(src, "device_id") or _pick(src, "id") or "")

    if interface not in ("", None) and not normalize_interface(interface):
        raise ConfigurationError("连接方式非法：%r" % (interface,),
                                 detail="应为 %s 之一" % (SUPPORTED_INTERFACES,))
    declared = normalize_interface(interface)
    if not declared:
        raw = _pick(src, "interface")
        declared = normalize_interface(raw)
        if not declared and str(raw or "").strip().upper() not in NO_LINK:
            raise ConfigurationError("连接方式不在支持范围：%r" % (raw,),
                                     detail="档案里的 interface 应为 %s 之一；"
                                            "不接链路的档案写 NONE" % (SUPPORTED_INTERFACES,))

    has_lan = bool(host) or port not in (None, "")
    has_serial = bool(serial_port)
    if bool(host) != bool(port not in (None, "")):
        raise ConfigurationError("网口端点不完整：host 与 port 必须成对给",
                                 detail="缺少 %s（形如 host=\"192.168.10.41\", port=4000）"
                                        % ("port" if host else "host"))

    if declared == SERIAL:
        pass                        # 显式指定串口：即使网口参数也在，也按用户说的走
    elif declared == LAN:
        has_serial, serial_port, baudrate = False, None, None
    elif has_lan and has_serial:
        raise ConfigurationError("连接方式不明确：网口与串口参数都填了",
                                 detail="请在档案里写明 interface 为 %s 之一，或调用时显式传 "
                                        "interface=\"LAN\" / interface=\"SERIAL\"" % (SUPPORTED_INTERFACES,))
    elif not declared and not has_lan and not has_serial:
        return None
    if declared == SERIAL or (not declared and has_serial and not has_lan):
        port_name = str(serial_port or "")
        if not port_name:
            raise ConfigurationError("串口端点不完整：缺少串口名",
                                     detail="请填写档案里的 serial_port，如 COM6 或 /dev/ttyUSB0")
        baud = _as_int(baudrate, SERIAL_DEFAULTS["baudrate"]) or SERIAL_DEFAULTS["baudrate"]
        if baud <= 0:
            raise ConfigurationError("波特率非法：%r" % (baudrate,), detail="应为正整数，如 115200")
        size = _as_int(bytesize, SERIAL_DEFAULTS["bytesize"])
        if size not in _BYTESIZES:
            raise ConfigurationError("数据位非法：%r" % (bytesize,), detail="应为 %s 之一" % (_BYTESIZES,))
        par = str(parity or SERIAL_DEFAULTS["parity"]).upper()
        if par not in _PARITIES:
            raise ConfigurationError("校验位非法：%r" % (parity,), detail="应为 %s 之一" % (_PARITIES,))
        stop = float(stopbits if stopbits not in ("", None) else SERIAL_DEFAULTS["stopbits"])
        if stop not in _STOPBITS:
            raise ConfigurationError("停止位非法：%r" % (stopbits,), detail="应为 %s 之一" % (_STOPBITS,))
        return Endpoint(kind=SERIAL, port_name=port_name, baudrate=baud, bytesize=size,
                        parity=par, stopbits=stop, device_id=device_id,
                        timeout=float(timeout if timeout is not None else 2.0))

    if not (host and port not in (None, "")):
        return None
    proto = str(_pick(src, "protocol") or "TCP")
    return Endpoint(kind=LAN, host=str(host), port=_as_int(port), protocol=proto,
                    device_id=device_id, timeout=float(timeout if timeout is not None else 2.0))


__all__ = ["AUTO", "BACKEND_CHOICES", "DEFAULT_BACKEND", "LAN", "NATIVE", "NO_LINK",
           "SERIAL", "SERIAL_DEFAULTS", "SUPPORTED_BACKENDS", "SUPPORTED_INTERFACES", "VISA",
           "Endpoint", "backends_of", "endpoint_from", "interfaces_of", "normalize_backend",
           "normalize_interface", "resolve_backend", "visa_resource_for"]
