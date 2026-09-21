# -*- coding: utf-8 -*-
"""脚本化传输：给一致性检查与单元测试用（不依赖硬件、也不依赖驱动的仿真模型）

用法：

    t = FakeTransport()
    t.expect("*IDN?", "TEKTRONIX,MSO54,SN001,1.2.3")
    t.expect_re(r":HOR:SCA\\?", "5e-05")
    t.default = lambda cmd: "0"
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from ..transport import Transport


class FakeTransport(Transport):
    kind = "fake"

    def __init__(self, cfg=None, timeout: float = 2.0, handler: Optional[Callable] = None):
        super().__init__(cfg, timeout=timeout, handler=handler)
        self.exact: dict[str, str] = {}
        self.patterns: list[tuple[str, str]] = []
        self.default: Optional[Callable] = None
        self.log: list[str] = []
        self.fail_on: dict[str, str] = {}          # 命令 -> 抛错类型（"timeout" / "transport"）
        self.queue: list[str] = []

    # ---- 配置 ----
    def expect(self, command: str, response: str) -> "FakeTransport":
        self.exact[str(command).strip()] = str(response)
        return self

    def expect_re(self, pattern: str, response: str) -> "FakeTransport":
        self.patterns.append((str(pattern), str(response)))
        return self

    def fail(self, command: str, kind: str = "timeout") -> "FakeTransport":
        self.fail_on[str(command).strip()] = kind
        return self

    # ---- 链路实现 ----
    def _open(self) -> None:
        self.opened = True

    def _close(self) -> None:
        self.queue.clear()

    def _write(self, data: bytes) -> None:
        cmd = data.decode("ascii", "ignore").strip()
        self.log.append(cmd)
        kind = self.fail_on.get(cmd)
        if kind == "timeout":
            from ..errors import DriverTimeout

            raise DriverTimeout(f"（测试注入）{cmd} 超时")
        if kind == "transport":
            from ..errors import TransportError

            raise TransportError(f"（测试注入）{cmd} 写失败")
        answer = ""
        if cmd in self.exact:
            answer = self.exact[cmd]
        else:
            for pattern, resp in self.patterns:
                if re.search(pattern, cmd):
                    answer = resp
                    break
            else:
                if self.default is not None:
                    answer = str(self.default(cmd))
                elif self.handler is not None:
                    answer = str(self.handler(cmd))
        if answer:
            self.queue.append(answer)

    def _read_line(self, timeout: float) -> str:
        if not self.queue:
            raise TimeoutError("FakeTransport 没有待读应答（请先 expect()）")
        return self.queue.pop(0)
