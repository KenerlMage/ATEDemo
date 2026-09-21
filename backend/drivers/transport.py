# -*- coding: utf-8 -*-
"""传输层：真实链路（TCP socket / 串口）与仿真链路

驱动只依赖 `Transport` 接口，真实/仿真在 `make_transport()` 一处分叉：
* `mode="real"`   -> SocketTransport（LAN） / SerialTransport（串口，需要 pyserial）
* `mode="simulate"` -> SimulateTransport（把命令交给驱动的 `simulate_command()` 生成响应）
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Optional

from .errors import TransportError

DEFAULT_TIMEOUT = 2.0
MAX_READ_BYTES = 1 << 20


class Transport:
    """链路基类：open / close / write / read / query"""

    kind = "base"

    def __init__(self, cfg=None, timeout: float = DEFAULT_TIMEOUT):
        self.cfg = cfg
        self.timeout = float(timeout or DEFAULT_TIMEOUT)
        self.opened = False
        self.written = 0
        self.read_count = 0
        self.last_error = ""

    # -------- 子类实现 --------
    def _open(self) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    def _close(self) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    def _write(self, data: bytes) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    def _read_line(self, timeout: float) -> str:  # pragma: no cover - 抽象
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
            self.open()
        self.written += 1
        self._write(self.encode(command))

    def read(self, timeout: Optional[float] = None) -> str:
        if not self.opened:
            self.open()
        text = self._read_line(float(timeout or self.timeout))
        self.read_count += 1
        return text

    def query(self, command: str, timeout: Optional[float] = None) -> str:
        self.write(command)
        return self.read(timeout)

    @staticmethod
    def encode(command: str) -> bytes:
        return (command.rstrip("\r\n") + "\n").encode("ascii", errors="replace")

    @staticmethod
    def decode(data: bytes) -> str:
        return data.decode("utf-8", errors="replace").strip()

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "opened": self.opened,
            "written": self.written,
            "read": self.read_count,
            "timeout": self.timeout,
            "last_error": self.last_error,
        }


class SocketTransport(Transport):
    """LAN / TCP 原始 socket（SCPI-RAW / VXI-11 socket 端口）"""

    kind = "socket"

    def __init__(self, host: str, port: int, timeout: float = DEFAULT_TIMEOUT):
        super().__init__(None, timeout)
        self.host = host
        self.port = int(port)
        self.sock: Optional[socket.socket] = None

    def _open(self) -> None:
        t0 = time.time()
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self.sock.settimeout(self.timeout)
            try:
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
        except OSError as e:
            self.last_error = str(e)
            raise TransportError(
                f"无法连接 {self.host}:{self.port}（{e}）",
                detail=f"耗时 {time.time() - t0:.2f}s",
            ) from e

    def _close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _write(self, data: bytes) -> None:
        assert self.sock is not None
        try:
            self.sock.sendall(data)
        except OSError as e:
            self.last_error = str(e)
            raise TransportError(f"写入失败: {e}") from e

    def _read_line(self, timeout: float) -> str:
        assert self.sock is not None
        deadline = time.time() + timeout
        chunks = bytearray()
        self.sock.settimeout(timeout)
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                break
            try:
                self.sock.settimeout(remain)
                data = self.sock.recv(4096)
            except socket.timeout as e:
                self.last_error = "读取超时"
                raise TransportError(f"读取超时（{timeout:.1f}s 内无响应）") from e
            except OSError as e:
                self.last_error = str(e)
                raise TransportError(f"读取失败: {e}") from e
            if not data:
                break
            chunks.extend(data)
            if b"\n" in data or len(chunks) > MAX_READ_BYTES:
                break
        return self.decode(bytes(chunks))

    def describe(self) -> dict:
        out = super().describe()
        out.update({"host": self.host, "port": self.port})
        return out


class SerialTransport(Transport):
    """串口（RS232/RS485）：需要 pyserial，未安装时给出明确提示"""

    kind = "serial"

    def __init__(self, port: str, baudrate: int = 9600, timeout: float = DEFAULT_TIMEOUT):
        super().__init__(None, timeout)
        self.port = port
        self.baudrate = int(baudrate or 9600)
        self.ser = None

    def _open(self) -> None:
        try:
            import serial  # type: ignore
        except ImportError as e:
            self.last_error = "pyserial 未安装"
            raise TransportError(
                f"串口设备 {self.port} 需要 pyserial（执行 pip install pyserial 后重试）",
                detail="未安装第三方库 pyserial",
            ) from e
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        except Exception as e:  # pyserial.SerialException
            self.last_error = str(e)
            raise TransportError(f"无法打开串口 {self.port}（{e}）") from e

    def _close(self) -> None:
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def _write(self, data: bytes) -> None:
        assert self.ser is not None
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        try:
            self.ser.write(data)
        except Exception as e:
            self.last_error = str(e)
            raise TransportError(f"串口写入失败: {e}") from e

    def _read_line(self, timeout: float) -> str:
        assert self.ser is not None
        self.ser.timeout = timeout
        try:
            data = self.ser.read_until(b"\n")
        except Exception as e:
            self.last_error = str(e)
            raise TransportError(f"串口读取失败: {e}") from e
        return self.decode(data or b"")

    def describe(self) -> dict:
        out = super().describe()
        out.update({"port": self.port, "baudrate": self.baudrate})
        return out


class SimulateTransport(Transport):
    """仿真链路：命令交给驱动的 `simulate_command()` 生成确定性响应（无需硬件）"""

    kind = "simulate"

    def __init__(self, cfg=None, handler: Optional[Callable[[str], str]] = None, timeout: float = DEFAULT_TIMEOUT):
        super().__init__(cfg, timeout)
        self.handler = handler or (lambda cmd: "0")
        self.command_log: list[str] = []
        self._replies: list[str] = []

    def _open(self) -> None:
        pass

    def _close(self) -> None:
        pass

    def _write(self, data: bytes) -> None:
        cmd = self.decode(data)
        self.command_log.append(cmd)
        try:
            reply = self.handler(cmd)
        except Exception as e:  # 仿真不该因为协议细节炸掉
            reply = f"SIM-ERROR:{e}"
        self._replies.append("" if reply is None else str(reply))

    def _read_line(self, timeout: float) -> str:
        return self._replies.pop(0) if self._replies else ""

    def describe(self) -> dict:
        out = super().describe()
        out["commands"] = len(self.command_log)
        return out


def make_transport(
    cfg,
    mode: str = "simulate",
    timeout: float = DEFAULT_TIMEOUT,
    handler: Optional[Callable[[str], str]] = None,
) -> Transport:
    """按设备配置与运行模式创建链路

    * `mode="simulate"`（默认）：仿真链路，没有任何硬件也能完整跑通
    * `mode="real"`：LAN 走 socket，串口走 pyserial；接口不支持时报 TransportError
    """
    mode = (mode or "simulate").lower()
    if mode != "real":
        return SimulateTransport(cfg, handler=handler, timeout=timeout)
    kind = cfg.transport_kind() if cfg is not None else "none"
    if kind == "socket":
        return SocketTransport(cfg.host, cfg.port, timeout=timeout)
    if kind == "serial":
        return SerialTransport(cfg.serial_port, cfg.baudrate or 9600, timeout=timeout)
    raise TransportError(
        f"设备 {getattr(cfg, 'device_id', '')} 缺少可用链路（IP/端口 或 串口）",
        device_id=getattr(cfg, "device_id", ""),
        detail="可在「装备属性配置」页右键该设备补全连接参数",
    )


_LOCK = threading.Lock()
