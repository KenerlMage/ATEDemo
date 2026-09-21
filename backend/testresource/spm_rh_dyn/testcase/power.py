# -*- coding: utf-8 -*-
"""供电 / 电流类测试用例"""

from __future__ import annotations


def power_on_voltage(ctx, psu_alias="psu", dmm_alias="dmm", voltage=3.3, **_):
    """读头上电电压：先供电再用数字万用表读电压（阈值 TC_VDD）"""
    ctx.command(psu_alias, "set_voltage", voltage=voltage)
    ctx.command(psu_alias, "output_on")
    value = ctx.command(dmm_alias, "measure_dc_voltage")
    return {"VDD": value}


def read_head_current(ctx, dmm_alias="dmm", spin_alias="spin", rpm=3600, **_):
    """读头工作电流：转速稳定后测量（阈值 TC_IDD）"""
    if rpm:
        ctx.command(spin_alias, "set_speed", rpm=rpm)
    value = ctx.command(dmm_alias, "measure_dc_current")
    return {"IDD": value}


def resistance_check(ctx, dmm_alias="dmm", limit_key="TC_RES", **_):
    """读头绝缘/回路电阻（示例：用 ctx.expect 直接在用例内判定）"""
    value = ctx.command(dmm_alias, "measure_resistance")
    ctx.expect(limit_key, value)
    return {"RES": value}
