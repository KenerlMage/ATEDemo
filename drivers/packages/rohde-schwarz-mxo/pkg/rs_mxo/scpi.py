"""R&S MXO 系列 SCPI 命令表。

集中在一个模块里，便于：
1. 出包前用真机逐条核对（tools/scpi_probe.py）；
2. 换型号时只改这一处；
3. CI 静态检查能一眼看到「命令拼接只出现在包内」。

命令依据 R&S MXO 系列 SCPI 参考手册；标记 VERIFY 的条目需在真机上确认返回格式。
"""

from __future__ import annotations


def channel_data(channel: int) -> str:
    """取通道波形二进制块。VERIFY：返回 float32 小端，需先设 FORMat:DATA REAL,32。"""
    return f"CHANnel{channel}:DATA?"


def channel_data_header(channel: int) -> str:
    """取波形头（x 起点、x 终点、点数）。VERIFY：MXO 返回 'start,stop,n'。"""
    return f"CHANnel{channel}:DATA:HEADer?"


def data_format() -> str:
    return "FORMat:DATA REAL,32;BYTeorder LSBFirst"


def acquire_points(points: int) -> str:
    return f"ACQuire:POINts {int(points)}"


def timebase_scale(seconds_per_div: float) -> str:
    return f"TIMebase:SCALe {seconds_per_div:.9g}"


def timebase_position(seconds: float) -> str:
    return f"TIMebase:POSition {seconds:.9g}"


def channel_state(channel: int, on: bool) -> str:
    return f"CHANnel{channel}:STATe {'ON' if on else 'OFF'}"


def channel_scale(channel: int, volts_per_div: float) -> str:
    return f"CHANnel{channel}:SCALe {volts_per_div:.9g}"


def channel_offset(channel: int, volts: float) -> str:
    return f"CHANnel{channel}:OFFSet {volts:.9g}"


def channel_coupling(channel: int, coupling: str) -> str:
    return f"CHANnel{channel}:COUPling {coupling.upper()}"


def trigger_mode(mode: str) -> str:
    return f"TRIGger:MODE {mode.upper()}"


def trigger_source(channel: int) -> str:
    return f"TRIGger:SOURce CHANnel{channel}"


def trigger_edge_slope(slope: str) -> str:
    return f"TRIGger:EDGE:SLOPe {slope.upper()}"


def trigger_level(volts: float) -> str:
    """VERIFY：MXO 边沿触发电平路径，部分固件为 TRIGger:LEVel:LEVel。"""
    return f"TRIGger:LEVel {volts:.9g}"


def single_acquisition() -> str:
    return "SINGle"


def run_continuous() -> str:
    return "RUN"


def operation_complete() -> str:
    return "*OPC?"


IDENTITY = "*IDN?"
CLEAR_STATUS = "*CLS"
RESET = "*RST"

#: 仪器测量项映射；契约的 item 名 → (测量类别, 子项)
MEASUREMENT_MAP: dict[str, tuple[str, str]] = {
    "vpp": ("VPP", ""),
    "vmax": ("VMAX", ""),
    "vmin": ("VMIN", ""),
    "mean": ("MEAN", ""),
    "rms": ("RMS", ""),
    "frequency": ("FREQuency", ""),
    "period": ("PERiod", ""),
    "rise_time": ("RISetime", ""),
}


def measurement_setup(slot: int, channel: int, item: str) -> list[str]:
    """把契约测量项翻成仪器测量配置。VERIFY：槽位与子项命名按固件可能不同。"""
    category = MEASUREMENT_MAP[item][0]
    return [
        f"MEASurement{slot}:SOURce CHANnel{channel}",
        f"MEASurement{slot}:MAIN {category}",
    ]


def measurement_result(slot: int) -> str:
    return f"MEASurement{slot}:RESult:ACTual?"
