"""pipython / GCS 访问层：连接串解析、单位换算与需要真机核对的命令清单。

单位换算
--------
pihexapod 的 C-887 以 µm 与 µrad 表达空间轴，与契约单位一致，因此这里只做
「度/毫米 → 契约单位」的兜底换算，防止某些固件版本以 mm 或 deg 返回。

标记 VERIFY 的命令必须在真机逐条核对后才能进 stable 通道。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 真机核对清单（与 CHANGELOG 的待办对应）
VERIFY = (
    "qIDN 四段内容的实际顺序（vendor / model / serial / firmware）",
    "qTMN? / qTMX? 对六足空间轴是否返回行程（部分固件只支持单轴平台）",
    "MOV 接受 dict 还是必须逐轴下发；多轴是否同步开始",
    "FRF 是否为该控制器正确的回零命令（备选 GOH / REF）",
    "qPOS 返回单位（µm 与 µrad）以及 SPA 1 0x02000200 的单位配置",
    "HLT 是否会立即断使能（某些固件只减速不停）",
)

UM_PER_MM = 1000.0
URAD_PER_DEG = 17453.292519943295


@dataclass(frozen=True)
class Endpoint:
    """连接参数：与 GCSDevice 的三个 Connect* 方法一一对应。"""

    kind: str  # tcpip | usb | serial
    host: str = ""
    port: int = 50000
    serial: str = ""
    comport: str = ""
    baudrate: int = 115200


def parse_resource(resource: str) -> Endpoint:
    """支持 tcpip://ip[:port]、usb:<serial>、serial://COM3[:115200]、裸 IP。"""
    text = str(resource).strip()
    if not text:
        raise ValueError("资源名为空")
    if text.startswith("tcpip://"):
        rest = text[len("tcpip://"):]
        host, _, port = rest.partition(":")
        return Endpoint("tcpip", host=host, port=int(port or 50000))
    if text.startswith("usb:"):
        return Endpoint("usb", serial=text[len("usb:"):])
    if text.startswith("serial://"):
        rest = text[len("serial://"):]
        comport, _, baud = rest.partition(":")
        return Endpoint("serial", comport=comport, baudrate=int(baud or 115200))
    if text.startswith("com") or text.startswith("COM"):
        return Endpoint("serial", comport=text, baudrate=115200)
    return Endpoint("tcpip", host=text, port=50000)


def connect(device, endpoint: Endpoint) -> None:
    """按端点类型连接；异常由调用方翻译成契约异常。"""
    if endpoint.kind == "usb":
        device.ConnectUSB(serialnum=endpoint.serial)
    elif endpoint.kind == "serial":
        device.ConnectRS232(comport=endpoint.comport, baudrate=endpoint.baudrate)
    else:
        device.ConnectTCPIP(ipaddress=endpoint.host, ipport=endpoint.port)


def read_limits(device, axes) -> tuple[dict[str, float], dict[str, float]]:
    """读行程；某轴读不到就跳过（返回的字典可能不全，调用方需容错）。"""
    lower: dict[str, float] = {}
    upper: dict[str, float] = {}
    for axis in axes:
        try:
            upper[axis] = float(device.qTMX(axis)[axis])
            lower[axis] = float(device.qTMN(axis)[axis])
        except Exception:
            continue
    return lower, upper


def to_contract_units(values: dict, *, axis_is_rotary) -> dict[str, float]:
    """把厂商返回的 dict 规整成契约单位（转 float，不做无依据缩放）。"""
    out = {}
    for axis, value in values.items():
        name = str(axis).strip().upper()
        try:
            out[name] = float(value)
        except (TypeError, ValueError):
            continue
    return out
