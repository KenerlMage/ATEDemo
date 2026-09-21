# -*- coding: utf-8 -*-
"""设备家族契约层

一个家族 = 一类仪器的**语义契约**：能力集合、动作名、参数、单位、返回结构、一致性要求。
型号驱动继承家族类，只实现"命令表 + 应答解析 + 仿真模型"三处差异。

新增家族（如 `dmm` / `psu` / `awg` / `motion`）时，在 `capabilities.FAMILY_CAPABILITIES`
与 `REQUIRED_BY_FAMILY` 里登记能力，然后在本目录加一个模块导出家族基类即可。
"""

from .scope import ChannelSetup, Measurement, ScopeDriver, TimebaseSetup, Waveform  # noqa: F401

__all__ = ["ChannelSetup", "Measurement", "ScopeDriver", "TimebaseSetup", "Waveform"]
