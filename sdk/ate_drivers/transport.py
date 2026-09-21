# -*- coding: utf-8 -*-
"""传输层契约：驱动只依赖 `Transport` 接口，真实/仿真在 `make_transport()` 一处分叉。

四种链路，**同一套读写接口**（open / close / write / read / query）：

* `SimulateTransport`：把命令交给驱动的 `simulate_command()` 生成应答 —— 无硬件可跑全流程；
* `SocketTransport`：**网口** SCPI socket（VXI-11 / raw socket），stdlib socket，零第三方依赖；
* `SerialTransport`：**串口** SCPI（RS-232 / RS-485 / USB 转串口），默认走 pyserial；
* `VisaTransport`：**PyVISA 后端**，网口与串口都收在一个会话对象里，资源名用规范写法。

这里有两层正交的概念，别混：

| | 回答什么 | 取值 | 谁决定 |
| --- | --- | --- | --- |
| 连接方式 `kind` | 物理怎么接 | `LAN` / `SERIAL` | 设备档案（`endpoint.py`） |
| 传输后端 `backend` | 用哪套客户端栈收发 | `native` / `visa`（调用时还可写 `auto`） | 调用参数 > 档案 `backend` > 默认 `native` |

`backend="visa"` 时链路由 pyvisa 打开：网口资源名 `TCPIP0::host::port::SOCKET`，串口资源名
`ASRL6::INSTR` + 波特率/帧格式作为**会话属性**下发；**没装 pyvisa 就报可读的 `E_CONFIG`，
不静默退回原生栈**（`auto` 才会按可用性挑，且挑中的结果挂在 `describe()` / `Scope.backend` 上）。

第三方库一律**惰性导入**：串口用 pyserial，VISA 用 pyvisa；两者都可以用
`set_serial_factory()` / `set_visa_factory()` 注入自研或测试替身，此时**整库仍是零硬依赖**。
"""


from __future__ import annotations

import socket
import threading
import time
from typing import Any, Callable, Optional

from .endpoint import (
    AUTO,
    BACKEND_CHOICES,
    DEFAULT_BACKEND,
    NATIVE,
    VISA,
    normalize_backend,
    resolve_backend,
    visa_resource_for,
)
from .errors import ConfigurationError, DriverError, DriverTimeout, TransportError

DEFAULT_TIMEOUT = 2.0
MAX_READ_BYTES = 1 << 20

#: 底层串口工厂（签名 `factory(**serial_kwargs) -> serial-like`）；为 None 时用 pyserial
DEFAULT_SERIAL_FACTORY = None


def _pyserial():
    """惰性获取 pyserial；没装返回 None（不 import 失败，交给调用方报可读错误）"""
    try:
        import serial  # noqa: PLC0415

        return serial
    except Exception:
        return None


#: 底层 VISA 会话工厂（签名 `factory(resource, timeout_s, attributes) -> session-like`）；
#: 为 None 时用 pyvisa（进程内复用 ResourceManager）
DEFAULT_VISA_FACTORY = None
_VISA_MANAGERS: dict = {}


def _pyvisa():
    """惰性获取 pyvisa；没装返回 None（不 import 失败，交给调用方报可读错误）"""
    try:
        import pyvisa  # noqa: PLC0415

        return pyvisa
    except Exception:
        return None


def _visa_manager(backend: str = ""):
    """进程内复用 ResourceManager（VISA 库自己也会缓存会话，重复建反而慢）

    `backend` 为空串时交给 pyvisa 自动挑（装了 NI-VISA 用 NI，否则用 pyvisa-py）；
    也可以写 `"@py"`（纯 Python 实现）或 `"@ni"`（NI-VISA）。
    """
    pyvisa = _pyvisa()
    if pyvisa is None:
        return None
    key = str(backend or "")
    if key not in _VISA_MANAGERS:
        _VISA_MANAGERS[key] = pyvisa.ResourceManager(key) if key else pyvisa.ResourceManager()
    return _VISA_MANAGERS[key]


def _apply_visa_attributes(session, timeout_s: float, attributes=None) -> dict:
    """把超时（VISA 用毫秒）与串口帧属性写到会话上；会话不认的属性跳过（网口没有 baud_rate）"""
    attrs = {k: v for k, v in dict(attributes or {}).items()
             if k != "visa_backend" and v not in (None, "")}
    setting = {"timeout": int(float(timeout_s or DEFAULT_TIMEOUT) * 1000)}
    setting.update(attrs)
    for key, value in setting.items():
        try:
            setattr(session, key, value)
        except Exception:   # noqa: BLE001 - 某些 VISA 会话不支持个别属性，跳过即可
            continue
    return setting


def _default_visa_session(resource, timeout_s: float = DEFAULT_TIMEOUT, attributes=None):
    """默认 VISA 会话工厂：pyvisa 打开资源 → 落地超时与串口帧属性"""
    attrs = dict(attributes or {})
    manager = _visa_manager(str(attrs.get("visa_backend") or ""))
    if manager is None:
        raise ConfigurationError("VISA 后端需要 pyvisa（当前环境未安装）",
                                 detail="pip install pyvisa pyvisa-py（或装 NI-VISA / Keysight VISA "
                                        "后 pip install pyvisa）")
    session = manager.open_resource(str(resource))
    _apply_visa_attributes(session, timeout_s, attrs)
    return session


def set_visa_factory(factory=None):
    """注入 VISA 会话工厂（测试注入假会话 / 现场换成自研 VISA 封装）；传 None 恢复默认"""
    global DEFAULT_VISA_FACTORY
    DEFAULT_VISA_FACTORY = factory
    return DEFAULT_VISA_FACTORY


def visa_factory():
    """当前生效的 VISA 会话工厂（未注入且没装 pyvisa 时返回 None）"""
    if DEFAULT_VISA_FACTORY is not None:
        return DEFAULT_VISA_FACTORY
    if _pyvisa() is None:
        return None
    return _default_visa_session


def visa_available() -> bool:
    """当前环境能否走 VISA 后端（装了 pyvisa，或注入过会话工厂）"""
    return visa_factory() is not None


def set_serial_factory(factory=None):
    """注入底层串口工厂（测试注入假串口 / 现场换成自研串口库）；传 None 恢复默认"""
    global DEFAULT_SERIAL_FACTORY
    DEFAULT_SERIAL_FACTORY = factory
    return DEFAULT_SERIAL_FACTORY


def serial_factory():
    """当前生效的串口工厂（未注入时返回 pyserial 的 Serial，未安装则为 None）"""
    if DEFAULT_SERIAL_FACTORY is not None:
        return DEFAULT_SERIAL_FACTORY
    module = _pyserial()
    return getattr(module, "Serial", None)


class Transport:
    """链路基类：open / close / write / read / query"""

    kind = "base"

    def __init__(self, cfg=None, timeout: float = DEFAULT_TIMEOUT, handler: Optional[Callable] = None):
        self.cfg = cfg
        self.timeout = float(timeout or DEFAULT_TIMEOUT)
        self.handler = handler
        self.opened = False
        self.written = 0
        self.read_count = 0
        self.last_error = ""

    # -------- 子类实现 --------
    def _open(self) -> None:
        raise NotImplementedError

    def _close(self) -> None:
        raise NotImplementedError

    def _write(self, data: bytes) -> None:
        raise NotImplementedError

    def _read_line(self, timeout: float) -> str:
        raise NotImplementedError

    # -------- 对外接口 --------
    def open(self) -> "Transport":
        if not self.opened:
            self._open()
            self.opened = True
        return self

    def close(self) -> None:
        if self.opened:
            try:
                self._close()
            finally:
                self.opened = False

    def write(self, command: str) -> None:
        if not self.opened:
            raise TransportError("链路未打开", detail="请先 open()")
        try:
            self._write(str(command).encode("ascii", "ignore") + b"\n")
            self.written += 1
        except (OSError, socket.error) as e:
            self.last_error = str(e)
            raise TransportError(f"写失败: {e}") from e

    def read(self, timeout: Optional[float] = None) -> str:
        if not self.opened:
            raise TransportError("链路未打开", detail="请先 open()")
        try:
            line = self._read_line(float(timeout or self.timeout))
            self.read_count += 1
            return line
        except TimeoutError as e:
            raise DriverTimeout(str(e)) from e
        except (OSError, socket.error) as e:
            self.last_error = str(e)
            raise TransportError(f"读失败: {e}") from e

    def query(self, command: str, timeout: Optional[float] = None) -> str:
        self.write(command)
        return self.read(timeout=timeout)

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "opened": self.opened,
            "written": self.written,
            "read": self.read_count,
            "timeout": self.timeout,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} {self.kind} opened={self.opened}>"


class SimulateTransport(Transport):
    """仿真链路：命令交给 `handler(command)`（通常是驱动的 `simulate_command`）"""

    kind = "simulate"

    def __init__(self, cfg=None, timeout: float = DEFAULT_TIMEOUT, handler: Optional[Callable] = None):
        super().__init__(cfg, timeout=timeout, handler=handler)
        self.queue: list[str] = []
        self.log: list[tuple[str, str]] = []

    def _open(self) -> None:
        self.opened = True

    def _close(self) -> None:
        self.queue.clear()

    def _write(self, data: bytes) -> None:
        command = data.decode("ascii", "ignore").strip()
        answer = ""
        if self.handler is not None:
            answer = str(self.handler(command))
        self.log.append((command, answer))
        if answer:
            self.queue.append(answer)

    def _read_line(self, timeout: float) -> str:
        if not self.queue:
            raise TimeoutError("仿真链路没有待读应答")
        return self.queue.pop(0)


class SocketTransport(Transport):
    """LAN SCPI socket（VXI-11 / raw socket 均可），供驱动开发与产线自测"""

    kind = "socket"

    def __init__(self, cfg=None, timeout: float = DEFAULT_TIMEOUT, handler: Optional[Callable] = None):
        super().__init__(cfg, timeout=timeout, handler=handler)
        self.sock: Optional[socket.socket] = None
        self.buf = b""
        self._lock = threading.Lock()

    def _open(self) -> None:
        host = str(getattr(self.cfg, "host", "") or "")
        port = getattr(self.cfg, "port", None)
        if not host or not port:
            raise ConfigurationError("网络设备缺少 host / port", detail="请检查注册库里的连接参数")
        try:
            self.sock = socket.create_connection((host, int(port)), timeout=self.timeout)
            self.sock.settimeout(self.timeout)
        except (OSError, socket.error) as e:
            raise TransportError(f"连接 {host}:{port} 失败: {e}") from e

    def _close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _write(self, data: bytes) -> None:
        with self._lock:
            assert self.sock is not None
            self.sock.sendall(data)

    def _read_line(self, timeout: float) -> str:
        with self._lock:
            assert self.sock is not None
            deadline = time.time() + timeout
            while b"\n" not in self.buf:
                if time.time() > deadline:
                    raise TimeoutError(f"读取应答超时（{timeout}s）")
                self.sock.settimeout(max(0.05, deadline - time.time()))
                try:
                    chunk = self.sock.recv(4096)
                except socket.timeout as e:  # noqa: UP041
                    raise TimeoutError(f"读取应答超时（{timeout}s）") from e
                if not chunk:
                    break
                self.buf += chunk
                if len(self.buf) > MAX_READ_BYTES:
                    break
            line, _, rest = self.buf.partition(b"\n")
            self.buf = rest
            return line.decode("ascii", "ignore").strip()


class SerialTransport(Transport):
    """串口 SCPI 链路（RS-232 / RS-485 / USB 转串口）——与网口**同一套接口**

    串口参数取自设备档案：`serial_port` / `baudrate`，帧格式（数据位/校验位/停止位）
    由 `endpoint.Endpoint` 写进 `DeviceConfig.extra`。
    """

    kind = "serial"

    def __init__(self, cfg=None, timeout: float = DEFAULT_TIMEOUT,
                 handler: Optional[Callable] = None, port_factory: Optional[Callable] = None):
        super().__init__(cfg, timeout=timeout, handler=handler)
        self.port = None
        self.port_factory = port_factory
        self.buf = b""

    def params(self) -> dict:
        """本次链路要用的串口参数（档案优先，缺项用 8N1 / 115200 默认）"""
        extra = dict(getattr(self.cfg, "extra", {}) or {})
        return {
            "port": str(getattr(self.cfg, "serial_port", "") or ""),
            "baudrate": int(getattr(self.cfg, "baudrate", None)
                            or extra.get("baudrate") or 115200),
            "bytesize": int(extra.get("bytesize", 8)),
            "parity": str(extra.get("parity", "N")).upper(),
            "stopbits": float(extra.get("stopbits", 1)),
            "timeout": self.timeout,
        }

    def _open(self) -> None:
        p = self.params()
        if not p["port"]:
            raise ConfigurationError("串口设备缺少串口名",
                                     detail="请填写档案里的 serial_port，如 COM6 或 /dev/ttyUSB0")
        factory = self.port_factory or serial_factory()
        if factory is None:
            raise ConfigurationError("串口链路需要 pyserial（当前环境未安装）",
                                     detail="pip install pyserial，或调用 "
                                            "ate_drivers.transport.set_serial_factory() 注入串口实现")
        try:
            self.port = factory(**p)
        except Exception as e:   # pyserial 的 SerialException 不是标准库异常，统一收口
            raise TransportError(f"打开串口 {p['port']} 失败: {e}") from e

    def _close(self) -> None:
        port, self.port = self.port, None
        if port is not None:
            try:
                port.close()
            except Exception:   # pragma: no cover - 关闭失败不该掩盖主流程结果
                pass

    def _write(self, data: bytes) -> None:
        assert self.port is not None
        try:
            self.port.write(data)
            flush = getattr(self.port, "flush", None)
            if callable(flush):
                flush()
        except Exception as e:
            raise TransportError(f"串口写失败: {e}") from e

    def _read_line(self, timeout: float) -> str:
        assert self.port is not None
        deadline = time.time() + timeout
        while b"\n" not in self.buf:
            if time.time() > deadline:
                raise TimeoutError(f"读取应答超时（{timeout}s）")
            try:
                chunk = self.port.read(4096)
            except Exception as e:
                raise TransportError(f"串口读失败: {e}") from e
            if not chunk:
                break
            self.buf += chunk if isinstance(chunk, bytes) else bytes(str(chunk), "ascii", "ignore")
            if len(self.buf) > MAX_READ_BYTES:
                break
        line, _, rest = self.buf.partition(b"\n")
        self.buf = rest
        return line.decode("ascii", "ignore").strip()

    def describe(self) -> dict:
        out = super().describe()
        out.update(self.params())
        out["opened"] = self.opened
        return out



def transport_class(backend: str, kind: str = "") -> Optional[type]:
    """按后端 + 链路类型给出链路类（门禁与文档用；不建连接、不 import 任何第三方库）

    `kind` 用 `transport_kind()` 的取值（`socket` / `serial`）。
    """
    name = normalize_backend(backend) or str(backend or "")
    if name == NATIVE:
        return {"socket": SocketTransport, "serial": SerialTransport}.get(str(kind).lower())
    if name == VISA:
        return VisaTransport
    return None


class VisaTransport(Transport):
    """VISA 链路（网口与串口共用一个会话对象）：由 `backend="visa"` 选中

    与原生链路**同一套读写接口**，差别只有两处、且都是 VISA 的语义：

    1. 资源名用规范写法：网口 `TCPIP0::host::port::SOCKET`、串口 `ASRL6::INSTR`；
    2. 串口波特率与帧格式（数据位/校验位/停止位）作为**会话属性**下发，
       不写进资源名（`ASRL::COM6::115200::INSTR` 那种写法不是 VISA 规范资源名）。

    pyvisa 惰性导入：没装时报可读 `E_CONFIG`；也可以用 `set_visa_factory()`
    注入自研/测试用的会话对象，整库仍是零硬依赖。
    """

    kind = VISA

    def __init__(self, cfg=None, timeout: float = DEFAULT_TIMEOUT,
                 handler: Optional[Callable] = None,
                 session_factory: Optional[Callable] = None, resource: str = ""):
        super().__init__(cfg, timeout=timeout, handler=handler)
        self.session = None
        self.session_factory = session_factory
        self._resource = resource
        self.writes: list[str] = []

    # -------- 端点与属性 --------
    def resource(self) -> str:
        """规范 VISA 资源名（显式给定优先，否则按档案里的连接参数推出）"""
        if self._resource:
            return str(self._resource)
        cfg = self.cfg
        kind = {"socket": "LAN", "serial": "SERIAL"}.get(
            str(getattr(cfg, "transport_kind", lambda: "")()), "")
        extra = dict(getattr(cfg, "extra", {}) or {})
        return visa_resource_for(kind, getattr(cfg, "host", "") or "",
                                 getattr(cfg, "port", None),
                                 extra.get("port_name") or getattr(cfg, "serial_port", "") or "")

    def attributes(self) -> dict:
        """会话属性：串口才带帧参数（网口会话没有 baud_rate 一类的属性）"""
        cfg = self.cfg
        out: dict = {}
        if str(getattr(cfg, "transport_kind", lambda: "")()) == "serial":
            extra = dict(getattr(cfg, "extra", {}) or {})
            out.update({
                "baud_rate": int(getattr(cfg, "baudrate", None) or extra.get("baudrate") or 115200),
                "data_bits": int(extra.get("bytesize", 8)),
                "parity": str(extra.get("parity", "N")).upper(),
                "stop_bits": float(extra.get("stopbits", 1)),
            })
        extra = dict(getattr(cfg, "extra", {}) or {})
        if extra.get("visa_backend"):
            out["visa_backend"] = str(extra["visa_backend"])
        return out

    def params(self) -> dict:
        return {"kind": self.kind, "resource": self.resource(),
                "timeout_s": self.timeout, "attributes": self.attributes()}

    # -------- 链路 --------
    def _open(self) -> None:
        resource = self.resource()
        if not resource:
            raise ConfigurationError("VISA 端点不完整：缺少资源名",
                                     detail="网口要 host + port；串口要 serial_port（如 COM6）")
        factory = self.session_factory or visa_factory()
        if factory is None:
            raise ConfigurationError("VISA 后端需要 pyvisa（当前环境未安装）",
                                     detail="pip install pyvisa pyvisa-py；或调用 "
                                            "ate_drivers.transport.set_visa_factory() 注入 VISA 会话实现")
        try:
            self.session = factory(resource, self.timeout, self.attributes())
        except DriverError:
            raise
        except Exception as e:      # pyvisa 的异常不属标准库，统一收口
            raise TransportError("VISA 打开 %s 失败: %s" % (resource, e)) from e

    def _close(self) -> None:
        session, self.session = self.session, None
        if session is not None:
            try:
                session.close()
            except Exception:   # pragma: no cover - 关闭失败不该掩盖主流程结果
                pass

    # VISA 会话自己管终结符，所以直接走文本接口，不再经由基类的字节路径
    def write(self, command: str) -> None:
        if not self.opened or self.session is None:
            raise TransportError("链路未打开", detail="请先 open()")
        text = str(command)
        try:
            self.session.write(text)
        except DriverError:
            raise
        except Exception as e:
            self.last_error = str(e)
            raise TransportError("VISA 写失败: %s" % e) from e
        self.writes.append(text)
        self.written += 1

    def read(self, timeout: Optional[float] = None) -> str:
        if not self.opened or self.session is None:
            raise TransportError("链路未打开", detail="请先 open()")
        wait = float(timeout or self.timeout)
        for key, value in (("timeout", int(wait * 1000)),):
            try:
                setattr(self.session, key, value)
            except Exception:   # pragma: no cover
                pass
        try:
            text = self.session.read()
        except DriverError:
            raise
        except Exception as e:
            self.last_error = str(e)
            if "timeout" in type(e).__name__.lower() or "timeout" in str(e).lower():
                raise DriverTimeout("读取应答超时（%ss）" % wait) from e
            raise TransportError("VISA 读失败: %s" % e) from e
        self.read_count += 1
        return str(text or "").strip()

    def _read_line(self, timeout: float) -> str:
        """基类字节路径的兼容实现（VISA 会话没有"读到换行"的概念，直接透传）"""
        return self.read(timeout)

    def query(self, command: str, timeout: Optional[float] = None) -> str:
        self.write(command)
        return self.read(timeout=timeout)

    def describe(self) -> dict:
        out = super().describe()
        out.update({"resource": self.resource(), "timeout_s": self.timeout,
                    "attributes": self.attributes(), "writes": len(self.writes)})
        return out


def make_transport(cfg=None, mode: str = "simulate", timeout: float = DEFAULT_TIMEOUT,
                   handler: Optional[Callable] = None, transport: Optional[Transport] = None,
                   port_factory: Optional[Callable] = None, backend: Any = None,
                   session_factory: Optional[Callable] = None,
                   visa_resource: str = "") -> Transport:
    """按模式与后端选择链路；显式传入 `transport` 时优先使用（测试可注入 FakeTransport）

    * `backend`：`native`（默认）/ `visa` / `auto`（装了 pyvisa 就走 VISA）；
    * 仿真模式与后端无关（不 import 任何第三方库，没有硬件也能跑全流程）；
    * `backend="visa"` 但环境没有 pyvisa → `E_CONFIG`，**不静默退回原生栈**。
    """
    if transport is not None:
        return transport
    if str(mode).lower() != "real":
        return SimulateTransport(cfg, timeout=timeout, handler=handler)
    kind = cfg.transport_kind() if cfg is not None else "none"
    if kind not in ("socket", "serial"):
        raise ConfigurationError("设备没有可用的连接方式",
                                 detail="interface / host / port / serial_port 至少缺一项")
    _requested, used = resolve_backend(backend, None, visa_available())
    if used == VISA:
        if session_factory is None and visa_factory() is None:
            raise ConfigurationError("VISA 后端需要 pyvisa（当前环境未安装）",
                                     detail="pip install pyvisa pyvisa-py；或调用 "
                                            "ate_drivers.transport.set_visa_factory() 注入 VISA 会话实现")
        return VisaTransport(cfg, timeout=timeout, handler=handler,
                             session_factory=session_factory, resource=visa_resource)
    if used != NATIVE:
        raise ConfigurationError("传输后端不在支持范围：%r" % (used,),
                                 detail="应为 %s 之一" % (BACKEND_CHOICES,))
    if kind == "socket":
        return SocketTransport(cfg, timeout=timeout, handler=handler)
    return SerialTransport(cfg, timeout=timeout, handler=handler, port_factory=port_factory)
