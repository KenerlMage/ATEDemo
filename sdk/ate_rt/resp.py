"""最小 RESP2 客户端（零第三方依赖）

只实现实时数据通道用到的命令，避免在产线工控机上引入 redis-py 这类额外依赖。
支持：AUTH / SELECT / PING / TYPE / EXISTS / HSET / HGET / HGETALL / HDEL
      SADD / SMEMBERS / ZADD / ZRANGE / ZREVRANGE / ZREMRANGEBYRANK / ZCARD / EXPIRE

用法:
    with RespClient(host="127.0.0.1", port=6379, db=0) as c:
        c.call("HSET", "k", "f", "v")
        c.call("HGETALL", "k")     # -> [b"f", b"v"]
"""

from __future__ import annotations

import socket


class RespError(Exception):
    """服务端返回的错误（-ERR ...）"""


class RespClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        db: int = 0,
        password: str = "",
        timeout: float = 1.5,
        encoding: str = "utf-8",
    ) -> None:
        self.host = host
        self.port = int(port)
        self.db = int(db)
        self.password = password or ""
        self.timeout = timeout
        self.encoding = encoding
        self._sock: socket.socket | None = None
        self._buf = b""

    # ---------------- 连接 ----------------

    def connect(self) -> "RespClient":
        if self._sock is not None:
            return self
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        self._sock = sock
        self._buf = b""
        if self.password:
            # 形如 "user:pass" 时按 Redis 6 ACL 的两参数形式认证
            if ":" in self.password:
                user, _, pwd = self.password.partition(":")
                self.call("AUTH", user, pwd)
            else:
                self.call("AUTH", self.password)
        if self.db:
            self.call("SELECT", str(self.db))
        return self

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            self._buf = b""

    def __enter__(self) -> "RespClient":
        return self.connect()

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---------------- 协议 ----------------

    def _encode(self, args) -> bytes:
        out = [b"*%d\r\n" % len(args)]
        for arg in args:
            if isinstance(arg, bytes):
                data = arg
            else:
                data = str(arg).encode(self.encoding)
            out.append(b"$%d\r\n" % len(data))
            out.append(data)
            out.append(b"\r\n")
        return b"".join(out)

    def _read_line(self) -> bytes:
        while b"\r\n" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("Redis 连接被关闭")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\r\n", 1)
        return line

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("Redis 连接被关闭")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def _read_reply(self):
        line = self._read_line()
        if not line:
            raise ConnectionError("Redis 返回空响应")
        kind, rest = line[:1], line[1:]
        if kind == b"+":
            return rest.decode(self.encoding, "replace")
        if kind == b"-":
            raise RespError(rest.decode(self.encoding, "replace"))
        if kind == b":":
            return int(rest)
        if kind == b"$":
            length = int(rest)
            if length < 0:
                return None
            data = self._read_exact(length + 2)[:-2]
            return data.decode(self.encoding, "replace")
        if kind == b"*":
            count = int(rest)
            if count < 0:
                return None
            return [self._read_reply() for _ in range(count)]
        raise RespError(f"无法识别的响应类型: {line[:32]!r}")

    def call(self, *args):
        if self._sock is None:
            self.connect()
        try:
            self._sock.sendall(self._encode(args))
            return self._read_reply()
        except (OSError, ConnectionError):
            # 连接异常后置空，下次调用自动重连（幂等写入场景可接受）
            self.close()
            raise

    def pipeline(self, commands: list[tuple]) -> list:
        """批量发送，按序返回；任一命令报错会抛出（其余结果丢弃）"""
        if self._sock is None:
            self.connect()
        payload = b"".join(self._encode(cmd) for cmd in commands)
        try:
            self._sock.sendall(payload)
            return [self._read_reply() for _ in commands]
        except (OSError, ConnectionError):
            self.close()
            raise

    # ---------------- 便捷判断 ----------------

    def ping(self) -> bool:
        try:
            return self.call("PING") in ("PONG", b"PONG")
        except Exception:
            return False
