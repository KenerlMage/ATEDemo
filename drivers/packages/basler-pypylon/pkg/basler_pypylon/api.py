"""pypylon 访问层：把 Basler 的 node 接口收窄成驱动需要的几个动作。

为什么单列一层
--------------
同一台相机在不同固件上，部分 node 可能不存在（如 Gain、TriggerMode 的取值集合）。
把"可选 node 的读写"集中在这里，驱动主体只调用语义化函数，
缺失 node 时降级为「不支持但可继续」，而不是让整条链路崩掉。

标记 VERIFY 的调用需在真机核对返回类型与取值范围后才能进 stable 通道。
"""

from __future__ import annotations

from typing import Iterable

#: 需要在真机核对的节点（写在这里便于逐条勾掉）
VERIFY = (
    "ExposureTime 单位与上下限（ace 系列为微秒）",
    "Gain 是否为 dB 且支持浮点写入",
    "Width/Height 与 OffsetX/OffsetY 的写入顺序与增量步长",
    "StartGrabbing 的 buffer 策略与 RetrieveResult 超时语义",
    "GetArray() 的 numpy dtype 与行序（BGR 与 Mono 的差异）",
)


class NodeError(Exception):
    """node 不存在或不在可写范围。"""


def open_camera(pylon, factory, descriptor):
    """按设备描述创建并打开相机。"""
    device = factory.CreateDevice(descriptor)
    cam = pylon.InstantCamera(device)
    cam.Open()
    return cam


def select_descriptor(descriptors: Iterable, resource: str):
    """按资源名挑设备：serial:<sn> / ip:<addr> / user:<name> / first。"""
    items = list(descriptors)
    if not items:
        raise NodeError("未枚举到任何相机")
    text = str(resource).strip()
    if text in ("", "first", "auto"):
        return items[0]
    kind, _, value = text.partition(":")
    if not value:
        return items[0]
    attr = {"serial": "GetSerialNumber", "ip": "GetIpAddress", "user": "GetUserDefinedName",
            "model": "GetModelName"}.get(kind.lower())
    if attr is None:
        return items[0]
    for d in items:
        getter = getattr(d, attr, None)
        if getter is None:
            continue
        try:
            if str(getter()) == value:
                return d
        except Exception:
            continue
    raise NodeError(f"没有匹配 {resource} 的相机；已发现 {len(items)} 台")


def set_float(cam, node: str, value: float) -> bool:
    """写浮点 node；不存在则返回 False（由调用方决定是否报不支持）。"""
    try:
        cam.GetNodeMap().GetNode(node).SetValue(float(value))
        return True
    except Exception:
        return False


def set_int(cam, node: str, value: int) -> bool:
    try:
        cam.GetNodeMap().GetNode(node).SetValue(int(value))
        return True
    except Exception:
        return False


def set_enum(cam, node: str, value: str) -> bool:
    try:
        cam.GetNodeMap().GetNode(node).SetValue(str(value))
        return True
    except Exception:
        return False


def get_float(cam, node: str, default: float = 0.0) -> float:
    try:
        return float(cam.GetNodeMap().GetNode(node).GetValue())
    except Exception:
        return default


def node_range(cam, node: str) -> tuple[float, float] | None:
    """取 node 的 min/max，用于写之前夹紧（Basler 对超范围写入会抛异常）。"""
    try:
        n = cam.GetNodeMap().GetNode(node)
        return float(n.GetMin()), float(n.GetMax())
    except Exception:
        return None


def apply_roi(cam, width: int, height: int, offset_x: int = 0, offset_y: int = 0) -> tuple[int, int]:
    """先归零偏移再设尺寸，再设偏移——顺序错了会被相机拒绝。"""
    set_int(cam, "OffsetX", 0)
    set_int(cam, "OffsetY", 0)
    set_int(cam, "Width", int(width))
    set_int(cam, "Height", int(height))
    if offset_x or offset_y:
        set_int(cam, "OffsetX", int(offset_x))
        set_int(cam, "OffsetY", int(offset_y))
    return int(get_float(cam, "Width")), int(get_float(cam, "Height"))
