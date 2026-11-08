"""仿真 IO 后端。

用途不是"假数据好看"，而是让契约一致性测试、CI 回归、TPS 联调在没有硬件时也能跑。
行为约束：
- 与真机走同一条驱动代码路径，只替换 IO 层（query_str / write_str / query_bin_block）。
- 波形由已下发的时基与垂直档位决定，因此 acquire 与 measure 的关系可被断言。
- 不静默替代硬件：只有站点配置显式要求 simulate 时才启用。
"""

from __future__ import annotations

import math
import random
import re
import struct

_CH_RE = re.compile(r"^CHANnel(\d+)", re.IGNORECASE)


class SimIo:
    """最小 SCPI 状态机，覆盖本包用到的命令子集。"""

    def __init__(self, resource: str = "SIM::MXO44::INSTR") -> None:
        self.resource = resource
        self.points = 10000
        self.time_span_s = 1e-3
        self.channels = {i: {"on": False, "scale": 0.5, "offset": 0.0, "coupling": "DC"} for i in range(1, 5)}
        self.trigger = {"mode": "NORMAL", "source": 1, "slope": "RISING", "level": 0.0}
        self._closed = False

    # -- IO 接口 --------------------------------------------------------
    def write_str(self, command: str) -> None:
        c = command.strip()
        up = c.upper()
        if up.startswith("FORMat".upper()) or up.startswith("ACQuire:POINts".upper()):
            if up.startswith("ACQ"):
                self.points = int(c.split()[-1])
            return
        if up.startswith("TIMebase:SCALe".upper()):
            self.time_span_s = float(c.split()[-1]) * 10.0
            return
        if up.startswith("TIMebase:POSition".upper()):
            return
        if up.startswith("CHANNEL"):
            m = _CH_RE.match(c)
            if not m:
                return
            num = int(m.group(1))
            key = c.split()[0].split(":")[-1].lower()
            val = c.split()[-1]
            ch = self.channels.setdefault(num, {"on": True, "scale": 0.5, "offset": 0.0, "coupling": "DC"})
            if key == "state":
                ch["on"] = val.upper() == "ON"
            elif key == "scale":
                ch["scale"] = float(val)
            elif key == "offset":
                ch["offset"] = float(val)
            elif key == "coupling":
                ch["coupling"] = val.upper()
            return
        if up.startswith("TRIGger".upper()):
            if up.startswith("TRIGGER:MODE"):
                self.trigger["mode"] = c.split()[-1].upper()
            elif up.startswith("TRIGGER:SOURCE"):
                self.trigger["source"] = int(c.split()[-1].upper().replace("CHANNEL", ""))
            elif up.startswith("TRIGGER:EDGE:SLOPE"):
                self.trigger["slope"] = c.split()[-1].upper()
            elif up.startswith("TRIGGER:LEVEL"):
                self.trigger["level"] = float(c.split()[-1])
            return
        # *CLS / *RST / SINGle / RUN 等无副作用
        return

    def query_str(self, query: str) -> str:
        q = query.strip()
        up = q.upper()
        if up == "*IDN?":
            return "Rohde&Schwarz,MXO44,1325.4154K44/101234,SIM-1.0.0"
        if up == "*OPC?":
            return "1"
        if "HEADER" in up:
            m = _CH_RE.match(q)
            if not m:
                return ""
            ch = 0
            dt = self.time_span_s / max(self.points - 1, 1)
            return f"{0.0:.9g},{dt * (self.points - 1):.9g},{self.points}"
        if "MEASUREMENT" in up and "RESULT" in up:
            # 由 measure() 前置配置推算：本包默认走波形推算，此路径仅在 prefer_instrument 时使用
            return "nan"
        return ""

    def query_bin_block(self, query: str) -> bytes:
        q = query.strip()
        up = q.upper()
        if up.startswith("CHANNEL") and up.endswith("DATA?"):
            m = _CH_RE.match(q)
            if not m:
                raise ValueError(f"无法解析通道: {query}")
            return self._waveform_bytes(int(m.group(1)))
        raise ValueError(f"SimIo 未实现该查询: {query}")

    def close(self) -> None:
        self._closed = True

    # -- 波形生成 -------------------------------------------------------
    def _waveform_bytes(self, channel: int) -> bytes:
        cfg = self.channels.get(channel, {"scale": 0.5, "offset": 0.0})
        scale = float(cfg["scale"])
        offset = float(cfg["offset"])
        amplitude = 0.8 * scale            # 8 格屏，占 80%
        cycles = 2.0                        # 整屏两个周期，便于 frequency 断言
        freq = cycles / self.time_span_s
        rng = random.Random(f"{self.resource}|{channel}|{self.points}|{scale}")
        noise = amplitude * 1e-4
        out = []
        for i in range(self.points):
            t = i * self.time_span_s / max(self.points - 1, 1)
            v = offset + amplitude * math.sin(2 * math.pi * freq * t)
            out.append(v + rng.uniform(-noise, noise))
        return struct.pack("<%df" % len(out), *out)
