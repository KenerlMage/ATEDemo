# -*- coding: utf-8 -*-
"""示例驱动：泰克 MSO5 系列数字示波器

这个包演示了"型号驱动只填三处差异"：`COMMANDS` 命令表、`PREAMBLE_FIELDS` 应答字段顺序、
`_sim_signal()` 仿真模型。动作名、参数、单位、返回结构、测量算法全部由 `ScopeDriver` 家族保证，
所以 TPS 用例在 MSO5 与后续别的品牌示波器之间是同一份代码。
"""

from __future__ import annotations

from ate_drivers import ScopeDriver


class TekMso5Scope(ScopeDriver):
    driver_key = "tektronix-mso5"
    family = "scope"
    vendor = "Tektronix"
    label = "数字示波器 · 泰克 MSO5 系列（示例包）"
    driver_version = "1.5.0"
    INTERFACES = ("LAN", "SERIAL")   # 连接方式：网口 + 串口
    BACKENDS = ("native", "visa")    # 传输后端：标准库收发 / PyVISA

    MODELS = ("MSO54",)
    sim_idn = "TEKTRONIX,MSO54,SIM0001,1.2.3"

    # ① 命令表：MSO5 的实际 SCPI（与家族默认表不同的键在这里覆盖）
    COMMANDS = dict(
        ScopeDriver.COMMANDS,
        **{
            "reset": "*RST;*CLS;*OPC?",
            "autoset": ":AUTOSet EXECute",
            "timebase_set": ":HORizontal:SCAle {scale:g}",
            "timebase_get": ":HORizontal:SCAle?",
            "channel_enable": ":{channel}:DISplay {state}",
            "channel_scale": ":{channel}:SCAle {scale:g}",
            "channel_coupling": ":{channel}:COUPling {coupling}",
            "acquire_setup": ":DATa:SOUrce {channel};:DATa:ENCdg RIBinary;:WFMOUTPre:BYT_Nr 2",
            "waveform_preamble": ":WFMOUTPre?",
            "waveform_data": ":CURVe?",
            "run": ":ACQuire:STATE ON",
            "stop": ":ACQuire:STATE OFF",
            "single": ":ACQuire:STATE ON;:ACQuire:STOPAfter SEQuence;:ACQuire:SEQuence SINGle",
            "error_query": ":SYSTem:ERRor:ALL?",
        },
    )

    # ② 应答字段顺序：MSO5 的 WFMOUTPre 顺序（按名字取值，不靠位置猜）
    PREAMBLE_FIELDS = (
        "byt_nr", "bit_nr", "encdg", "bn_fmt", "byt_or", "wfid", "nr_pt", "pt_fmt",
        "x_incr", "x_zero", "pt_off", "y_off", "y_mult", "y_zero", "domain", "wf_type",
    )

    # ③ 仿真模型：在家族默认模型上叠加"8 bit 垂直量化"（MSO5 前端特性）
    def _sim_signal(self, points: int, window_s: float) -> list[float]:
        samples = super()._sim_signal(points, window_s)
        ch = self.channel_setups[self.DEFAULT_CHANNEL]
        lsb = max(1e-9, ch.scale_v_per_div * 8.0 / 256.0)   # 8 格 / 8 bit
        return [round(round(v / lsb) * lsb, 6) for v in samples]

    def simulate_command(self, command: str) -> str:
        cmd = str(command).strip().upper()
        if cmd.startswith(":WFMOUTPRE"):
            # 顺序与 PREAMBLE_FIELDS 一致；ymult 按当前垂直档位换算
            ch = self.channel_setups[self.DEFAULT_CHANNEL]
            y_mult = ch.scale_v_per_div * 8.0 / 256.0
            x_incr = self.timebase_setup.window_s / 1000.0
            return (f"2;8;RIBINARY;RI;MSB;CH1,{ch.name};1000;Y;"
                    f"{x_incr:.6g};0.0;0;0.0;{y_mult:.6g};0.0;TIME;ANALOG")
        if cmd.startswith(":SYST") and cmd.endswith("?"):
            return '0,"No events to report."'
        return super().simulate_command(command)
