# -*- coding: utf-8 -*-
"""泰克 MSO5 / MSO6 系列示波器驱动（**网口型号**）。

已登记型号（`MODELS`）：MSO54 / MSO56 / MSO58 / MSO64 —— 型号来自设备档案，
由 `factory` 按型号直接派发到本驱动（无打分、无模糊匹配）。
4 系列（MSO44 / MSO46）与 DPO7000 命令体系相近但未逐一核验，
按厂商包规范补登 `MODELS` 与 `driver.json` 后再开放。

命令与字段取自泰克手册体系（tm_devices 官方命令文档 + 官方 FAQ）：
- 采集控制 `:ACQuire:STATE` / `:ACQuire:STOPAfter`
- 垂直     `:CH<x>:SCAle` / `:COUPling` / `:OFFSet`
- 水平     `:HORizontal:MAIN:SCAle`
- 数据     `:DATa:SOUrce` / `:DATa:ENCdg` + `:WFMOutpre?` + `:CURVe?`
- 换算     Voltage = (Digitizing Level - YOFF) * YMULT
"""

from __future__ import annotations

from ...family.scope import ScopeDriver

__all__ = ["TekMsoScope"]


class TekMsoScope(ScopeDriver):
    driver_key = "tektronix-mso5"
    family = "scope"
    vendor = "Tektronix"
    label = "数字示波器 · 泰克 MSO5 / MSO6 系列"
    driver_version = "1.6.0"
    INTERFACES = ("LAN", "SERIAL")   # 连接方式：网口 + 串口（命令集相同，只换链路）
    INTERFACE = "LAN"                # 兼容旧写法：首选连接方式
    BACKENDS = ("native", "visa")    # 传输后端：标准库收发 / PyVISA（命令与返回结构不变）
    MODELS = ("MSO54", "MSO56", "MSO58", "MSO64")  # 型号派发唯一依据
    sim_idn = "TEKTRONIX,MSO54,SIM0001,1.2.3"

    # ① 命令表：差异面之一
    COMMANDS = dict(
        ScopeDriver.COMMANDS,
        **{
            "idn": "*IDN?",
            "reset": "*RST;*CLS;*OPC?",
            "autoset": ":AUTOSet EXECute",
            "timebase_set": ":HORizontal:MAIN:SCAle {scale:g}",
            "timebase_get": ":HORizontal:MAIN:SCAle?",
            "channel_enable": ":{channel}:DISplay {state}",
            "channel_scale": ":{channel}:SCAle {scale:g}",
            "channel_coupling": ":{channel}:COUPling {coupling}",
            "acquire_setup": ":DATa:SOUrce {channel};:DATa:ENCdg RIBinary;:DATa:RESOlution FULL",
            "waveform_preamble": ":WFMOutpre?",
            "waveform_data": ":CURVe?",
            "run": ":ACQuire:STATE RUN",
            "stop": ":ACQuire:STATE STOP",
            "single": ":ACQuire:STOPAfter SEQuence;:ACQuire:STATE RUN",
            "error_query": ":SYSTem:ERRor:ALL?",
        },
    )

    # ② 前导字段顺序：WFMOutpre 应答的字段序（按名取值，不按位置猜）
    PREAMBLE_FIELDS = (
        "byt_nr", "bit_nr", "encdg", "bn_fmt", "byt_or", "wfid", "nr_pt", "pt_fmt",
        "x_incr", "x_zero", "pt_off", "y_off", "y_mult", "y_zero", "domain", "wf_type",
    )

    # ④ 枚举映射：统一测量项 -> 泰克 MEASUrement 类型（手册 29 项枚举）
    MEASURE_TYPES = {
        "PK2PK": "PK2Pk", "AMPLITUDE": "AMPlitude", "FREQUENCY": "FREQuency",
        "PERIOD": "PERIod", "MEAN": "MEAN", "RMS": "RMS", "RISE": "RISe",
        "FALL": "FALL", "MAX": "MAXimum", "MIN": "MINImum", "HIGH": "HIGH",
        "LOW": "LOW", "POS_WIDTH": "PWIdth", "NEG_WIDTH": "NWIdth",
        "POS_DUTY": "PDUty", "NEG_DUTY": "NDUty", "POS_OVERSHOOT": "POVershoot",
        "NEG_OVERSHOOT": "NOVershoot", "AREA": "AREa", "CYCLE_AREA": "CARea",
        "CYCLE_MEAN": "CMEan", "CYCLE_RMS": "CRMs", "DELAY": "DELay",
        "BURST_WIDTH": "BURst", "PHASE": "PHAse",
    }
    COUPLING_MAP = {"DC": "DC", "AC": "AC", "GND": "GND"}

    # ⑤ 仿真模型：叠加 MSO5 前端 8 bit 垂直量化
    def _sim_signal(self, points: int, window_s: float) -> list[float]:
        samples = super()._sim_signal(points, window_s)
        ch = self.channel_setups[self.DEFAULT_CHANNEL]
        lsb = max(1e-9, ch.scale_v_per_div * 8.0 / 256.0)   # 8 格 / 8 bit
        return [round(round(v / lsb) * lsb, 6) for v in samples]

    def simulate_command(self, command: str) -> str:
        cmd = str(command).strip().upper()
        if cmd.startswith(":WFMOUTPRE"):
            ch = self.channel_setups[self.DEFAULT_CHANNEL]
            y_mult = ch.scale_v_per_div * 8.0 / 256.0
            x_incr = self.timebase_setup.window_s / 1000.0
            return (f"2;8;RIBINARY;RI;MSB;CH1,{ch.name};1000;Y;"
                    f"{x_incr:.6g};0.0;0;0.0;{y_mult:.6g};0.0;TIME;ANALOG")
        if cmd.startswith(":SYST") and cmd.endswith("?"):
            return '0,"No events to report."'
        return super().simulate_command(command)
