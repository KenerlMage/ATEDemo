# -*- coding: utf-8 -*-
"""能力（Capability）声明与协商 —— 未来兼容性的核心机制

兼容性问题的根源是"平台以为驱动会做 X，驱动其实不会"。解决办法不是把基类做胖，
而是让驱动**显式声明**自己会哪些能力，调用方**先问再做**：

    if dev.supports(Capability.SCOPE_MEASURE):
        dev.do("scope.measure", items=["PK2PK", "FREQUENCY"])
    else:
        report.skip("该驱动不支持测量项，改用等效方法")

命名规则：`<family>.<verb>`，全部小写、下划线分隔。厂商私有扩展必须带厂商前缀
（`vendor.<vendor>.<verb>`），不得占用平台命名空间。
"""

from __future__ import annotations


class Capability:
    """能力名常量表（不要写魔法字符串，用这里的常量）"""

    # —— 核心：所有驱动都必须有 ——
    IDENTIFY = "identify"
    RESET = "reset"
    STATE = "state"

    # —— 示波器家族 ——
    SCOPE_AUTOSET = "scope.autoset"
    SCOPE_TIMEBASE = "scope.timebase"
    SCOPE_CHANNEL = "scope.channel"
    SCOPE_ACQUIRE = "scope.acquire_waveform"
    SCOPE_MEASURE = "scope.measure"
    SCOPE_RUN = "scope.run"
    SCOPE_STOP = "scope.stop"
    SCOPE_SINGLE = "scope.single"

    # —— 万用表家族 ——
    DMM_MEASURE = "dmm.measure"

    # —— 电源家族 ——
    PSU_SET = "psu.set_output"
    PSU_MEASURE = "psu.measure"

    # —— 信号源家族 ——
    AWG_SET = "awg.set_waveform"
    AWG_OUTPUT = "awg.output"

    # —— 运动控制家族 ——
    MOTION_MOVE = "motion.move"
    MOTION_READ_POS = "motion.read_position"


CORE_CAPABILITIES: tuple[str, ...] = (
    Capability.IDENTIFY,
    Capability.RESET,
    Capability.STATE,
)

# 每个家族声明的能力全集（驱动可以只实现其中一部分）
FAMILY_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "scope": (
        Capability.SCOPE_AUTOSET,
        Capability.SCOPE_TIMEBASE,
        Capability.SCOPE_CHANNEL,
        Capability.SCOPE_ACQUIRE,
        Capability.SCOPE_MEASURE,
        Capability.SCOPE_RUN,
        Capability.SCOPE_STOP,
        Capability.SCOPE_SINGLE,
    ),
    "dmm": (Capability.DMM_MEASURE,),
    "psu": (Capability.PSU_SET, Capability.PSU_MEASURE),
    "awg": (Capability.AWG_SET, Capability.AWG_OUTPUT),
    "motion": (Capability.MOTION_MOVE, Capability.MOTION_READ_POS),
    "passive": (),
    "generic": (),
}

# 家族基线：属于该家族的驱动**必须**具备的能力（缺失则一致性检查不通过）
REQUIRED_BY_FAMILY: dict[str, tuple[str, ...]] = {
    "scope": (Capability.SCOPE_ACQUIRE, Capability.SCOPE_MEASURE),
    "dmm": (Capability.DMM_MEASURE,),
    "psu": (Capability.PSU_MEASURE,),
    "awg": (Capability.AWG_SET,),
    "motion": (Capability.MOTION_MOVE,),
    "passive": (),
    "generic": (),
}

KNOWN_CAPABILITIES: frozenset[str] = frozenset(
    CORE_CAPABILITIES + tuple(c for caps in FAMILY_CAPABILITIES.values() for c in caps)
)

VENDOR_PREFIX = "vendor."


def is_known(cap: str) -> bool:
    """平台已知能力，或合法的厂商扩展能力（`vendor.*`）"""
    return cap in KNOWN_CAPABILITIES or cap.startswith(VENDOR_PREFIX)


def matches(pattern: str, cap: str) -> bool:
    """能力匹配：支持 `scope.*` 这种族通配，TPS 里写通配更抗新增"""
    if pattern.endswith(".*"):
        return cap.startswith(pattern[:-1])
    return pattern == cap


def validate(declared, family: str) -> tuple[list[str], list[str], list[str]]:
    """校验驱动的能力声明

    返回 `(缺失的家族基线能力, 平台不认识的能力, 厂商扩展能力)`
    """
    caps = [str(c) for c in (declared or [])]
    missing = [c for c in REQUIRED_BY_FAMILY.get(family, ()) if c not in caps]
    unknown = [c for c in caps if not is_known(c)]
    vendor = [c for c in caps if c.startswith(VENDOR_PREFIX)]
    return missing, unknown, vendor
