# -*- coding: utf-8 -*-
"""罗德与施瓦茨 MXO44（MXO 4 系列）示波器驱动（**网口型号**）。

已登记型号（`MODELS`）：MXO44 —— 来自手册 `*IDN?` 示例 `Rohde&Schwarz,MXO44,...`。
系列俗称 `MXO4` 作为别名登记；RTO6 / RTP 等其它平台命令集不同，未核验前不登记。

命令与字段取自 R&S 官方手册（MXO 4 User Manual，1335.5337.02 ─ 19，含远程控制命令章节）：

- 运行控制   `RUN` / `STOP` / `SINGle` / `RUNSingle`
- 自动设置   `AUToset`
- 垂直       `CHANnel<ch>:SCALe` / `:COUPling` / `:STATe` / `:OFFSet`
- 水平       `TIMebase:SCALe` / `TIMebase:RANGe`
- 采集       `ACQuire:POINts` / `ACQuire:SRATe` / `ACQuire:TYPE`
- 数据格式   `FORMat[:DATA]`（ASCii / INT8BIT / INT16BIT）
- 波形       `CHANnel<ch>:DATA:HEADer?` + `CHANnel<ch>:DATA[:VALues]?`（可选 offset, length）
             也可走 `WAVeform:NORMal/AVERage/MAXimum/MINimum:DATA:VALues?`
- 测量       `MEASurement<mg>:MAIN <类型>` + `:MEASurement<mg>:RESult[:ACTual]?`
- 触发       `TRIGger:EVENt<ev>:TYPE` / `:SOURce` / `:LEVel<n>` / `:EDGE:SLOPe`、`TRIGger:FORCe`

**与泰克的关键结构差异（决定了驱动要覆盖什么）**

1. 前导：R&S 的 `:DATA:HEADer?` 只有 **4 个位置值**
   `XStart(s), XStop(s), RecordLength(samples), ValuesPerSample`，
   手册官方示例：`-1E-07,9.980000000000001E-08,1000,1`；
   泰克 `:WFMOutpre?` 是约 20 个具名字段（含 `YMUlt` / `YOFf` 缩放系数）。
2. 电压：R&S 用 `FORMat[:DATA] ASCii` 直接把**伏特**输出（手册示例值就是 -0.125 V 一类的浮点），
   没有 `YMULT/YOFF` 概念；泰克二进制码值必须按 `V=(code-YOFF)*YMULT` 换算。
3. 通道寻址：R&S 是数字后缀 `CHANnel1`，泰克是令牌 `CH1`（由 `channel_token()` 归一）。
4. 测量类型名不同：R&S 用 `PDELta` 表示峰峰值，泰克用 `PK2Pk`。
"""

from __future__ import annotations

import re

from ...errors import DriverError
from ...family.scope import ScopeDriver, Waveform

__all__ = ["RsMxoScope"]


class RsMxoScope(ScopeDriver):
    driver_key = "rohde-schwarz-mxo4"
    family = "scope"
    vendor = "Rohde & Schwarz"
    label = "数字示波器 · 罗德与施瓦茨 MXO44（MXO 4 系列）"
    driver_version = "1.6.0"
    INTERFACES = ("LAN", "SERIAL")   # 连接方式：网口 + 串口
    BACKENDS = ("native", "visa")    # 传输后端：标准库收发 / PyVISA
   # 连接方式：网口 + 串口（命令集相同，只换链路）
    INTERFACE = "LAN"                # 兼容旧写法：首选连接方式
    MODELS = ("MXO44",)        # 型号派发唯一依据（手册 *IDN? 示例：MXO44）
    MODEL_ALIASES = ("MXO4",)  # 系列俗称（注意：不要用 ALIASES——那是家族层的动作别名表）
    sim_idn = "Rohde&Schwarz,MXO44,SIM0001,6.00"

    # ① 命令表
    COMMANDS = dict(
        ScopeDriver.COMMANDS,
        **{
            "idn": "*IDN?",
            "reset": "*RST;*CLS;*OPC?",
            "autoset": ":AUToset",
            "timebase_set": ":TIMebase:SCALe {scale:g}",
            "timebase_get": ":TIMebase:SCALe?",
            "channel_enable": ":CHANnel{n}:STATe {state}",
            "channel_scale": ":CHANnel{n}:SCALe {scale:g}",
            "channel_coupling": ":CHANnel{n}:COUPling {coupling}",
            # 注：手册里的 `FORMat[:DATA]` / `CHANnel<ch>:DATA[:VALues]?` 是写法说明，
            # 真下发不能带方括号（可选节点省略即可）
            "acquire_setup": ":ACQuire:POINts {points:g};FORMat ASCii",
            "waveform_preamble": ":CHANnel{n}:DATA:HEADer?",
            "waveform_data": ":CHANnel{n}:DATA?",
            "run": "RUN",
            "stop": "STOP",
            "single": "SINGle",
            "error_query": "SYSTem:ERRor?",
        },
    )

    # ④ 枚举映射：统一测量项 -> R&S MEASurement 类型（含振幅/时间与周期统计类）
    MEASURE_TYPES = {
        "PK2PK": "PDELta", "AMPLITUDE": "AMPLitude", "FREQUENCY": "FREQuency",
        "PERIOD": "PERiod", "MEAN": "MEAN", "RMS": "RMS", "RISE": "RTIMe",
        "FALL": "FTIMe", "MAX": "MAXimum", "MIN": "MINimum", "HIGH": "HIGH",
        "LOW": "LOW", "POS_WIDTH": "PPULse", "NEG_WIDTH": "NPULse",
        "POS_DUTY": "PDCYcle", "NEG_DUTY": "NDCYcle", "POS_OVERSHOOT": "POVershoot",
        "NEG_OVERSHOOT": "NOVershoot", "AREA": "AREA", "CYCLE_AREA": "CYCarea",
        "CYCLE_MEAN": "CYCMean", "CYCLE_RMS": "CYCRms", "DELAY": "DELay",
        "BURST_WIDTH": "BWIDth", "PHASE": "PHASe",
    }
    # DC / AC 已登记；GND 在 MXO4 上不可选（枚举待现场核对），遇到就报参数错误而不是悄悄改物理量
    COUPLING_MAP = {"DC": "DC", "AC": "AC"}

    # ------------------------------------------------------------ 差异 2/3：解码与寻址

    def channel_token(self, channel: str) -> str:
        """`CH1` -> `1`（R&S 通道是数字后缀）"""
        digits = re.sub(r"\D", "", str(channel))
        return digits or "1"

    def _decode_header(self, text: str) -> tuple[float, float, float, float, float]:
        """R&S 4 值头 -> (x_incr_s, y_mult, y_off, y_zero, x_start_s)

        手册示例：`-1E-07,9.980000000000001E-08,1000,1`
        = XStart(-100 ns), XStop(99.8 ns), RecordLength(1000), ValuesPerSample(1)

        `FORMat[:DATA] ASCii` 下数据本身就是伏特，所以缩放退化为恒等（y_mult=1, y_off=0）。
        """
        parts = [p.strip() for p in str(text).replace(";", ",").split(",") if p.strip()]
        if len(parts) < 3:
            raise DriverError("R&S 波形头解析失败", device_id=self.device_id, code="E_PROTOCOL",
                              detail=f"需要 XStart,XStop,RecordLength[,ValuesPerSample]；应答: {str(text)[:120]}")
        nums = []
        for p in parts[:4]:
            try:
                nums.append(float(p))
            except ValueError:
                nums.append(0.0)
        x_start, x_stop, n_pt = nums[0], nums[1], int(nums[2]) if nums[2] else 0
        span = (x_stop - x_start) / max(1, n_pt - 1) if n_pt > 1 else 0.0
        return (abs(span), 1.0, 0.0, 0.0, x_start)

    def _decode_preamble(self, text: str) -> tuple[float, float, float, float]:
        x_incr, y_mult, y_off, y_zero, _ = self._decode_header(text)
        return x_incr, y_mult, y_off, y_zero

    def _read_real_waveform(self, channel: str, points: int) -> Waveform:
        """真实取波形：R&S 的头里带绝对起止时间，保留 `x_start_s` 不丢"""
        kw = {"channel": channel, "n": self.channel_token(channel), "points": points}
        head = self.query(self.cmd("waveform_preamble", **kw))
        x_incr, y_mult, y_off, y_zero, x_start = self._decode_header(head)
        raw = self.query(self.cmd("waveform_data", **kw))
        samples = self._parse_curve(raw, points)
        volts = tuple(round((v - y_off) * y_mult + y_zero, 6) for v in samples)
        return Waveform(channel, volts, x_incr, x_start_s=x_start, unit="V",
                        source="instrument")

    # ------------------------------------------------------------ 采集参数（记录长度/采样率）

    def acquisition(self, mode: str | None = None, averages: int | None = None,
                    stop_after: str | None = None, sample_rate_sa_s: float | None = None,
                    record_length: int | None = None, **_) -> dict:
        """R&S 采集设置：`ACQuire:POINts` / `ACQuire:SRATe` / `ACQuire:TYPE`"""
        if record_length is not None:
            self._record_length = max(10, int(record_length))
        if sample_rate_sa_s is not None:
            self._sample_rate = float(sample_rate_sa_s)
        if not self.simulate:
            if record_length is not None:
                self.write(f":ACQuire:POINts {int(record_length):g}")
            if sample_rate_sa_s is not None:
                self.write(f":ACQuire:SRATe {float(sample_rate_sa_s):g}")
            if mode in ("sample", "average", "peak", "highres"):
                self.write(f":ACQuire:TYPE {mode}")
            if averages is not None:
                self.write(f":ACQuire:COUNt {int(averages):g}")
        return {
            "value": {
                "mode": mode or "sample",
                "averages": int(averages or getattr(self, "_averages", 1)),
                "stop_after": stop_after or ("single" if self.single_flag else "run_stop"),
                "sample_rate_sa_s": float(sample_rate_sa_s or getattr(self, "_sample_rate", 0.0)),
                "record_length": int(record_length or getattr(self, "_record_length", 0)),
            },
            "quality": "simulated" if self.simulate else "good",
        }

    # ------------------------------------------------------------ ⑤ 仿真模型

    def _sim_signal(self, points: int, window_s: float) -> list[float]:
        """MXO4 仿真：12 bit 前端 + 较低噪声底（型号特性，家族算法不动）"""
        samples = super()._sim_signal(points, window_s)
        ch = self.channel_setups[self.DEFAULT_CHANNEL]
        lsb = max(1e-9, ch.scale_v_per_div * 8.0 / 4096.0)   # 8 格 / 12 bit
        return [round(round(v / lsb) * lsb, 6) for v in samples]

    def simulate_command(self, command: str) -> str:
        cmd = str(command).strip().upper()
        if "DATA:HEADER" in cmd or cmd.startswith(":WAVEFORM") and "HEADER" in cmd:
            tb = self.timebase_setup
            n = max(2, int(getattr(self, "_record_length", 0) or 1000))
            x_incr = tb.window_s / max(1, n - 1)
            return f"-{x_incr * (n - 1) / 2:.9E},{x_incr * (n - 1) / 2:.9E},{n},1"
        if "DATA" in cmd and cmd.endswith("?"):
            n = max(2, int(getattr(self, "_record_length", 0) or 1000))
            samples = self._sim_signal(n, self.timebase_setup.window_s)
            return ",".join(f"{v:.6E}" for v in samples)
        if cmd.startswith(":ACQUIRE") and cmd.endswith("?"):
            return f"{getattr(self, '_record_length', 1000):g}"
        if cmd.startswith("SYST") and cmd.endswith("?"):
            return '0,"No error"'
        return super().simulate_command(command)

    def channel(self, name: str = ScopeDriver.DEFAULT_CHANNEL, enabled=None,
                scale_v_per_div=None, coupling=None, offset_v=None,
                probe_ratio=None, **_) -> dict:
        """耦合枚举按 R&S 收敛：GND 在 MXO4 上不可选，直接报 E_PARAM（不悄悄改物理量）"""
        if coupling is not None:
            key = str(coupling).upper()
            if key not in self.COUPLING_MAP:
                raise DriverError(
                    f"{self.label} 不支持耦合方式 {coupling}",
                    device_id=self.device_id, code="E_PARAM",
                    detail="本驱动登记: " + ", ".join(sorted(self.COUPLING_MAP))
                           + "（手册枚举需现场核对）",
                )
            coupling = self.COUPLING_MAP[key]
        return super().channel(name=name, enabled=enabled, scale_v_per_div=scale_v_per_div,
                               coupling=coupling, offset_v=offset_v,
                               probe_ratio=probe_ratio, **_)
