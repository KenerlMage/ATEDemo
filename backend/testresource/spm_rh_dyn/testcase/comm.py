# -*- coding: utf-8 -*-
"""运动控制 / 回放信号 / 负载类测试用例"""

from __future__ import annotations

import time


def spin_speed(ctx, motion_alias="spin", rpm=3600, settle=0.05, **_):
    """转速闭环：握手 -> 设定转速 -> 回读（阈值 TC_RPM / TC_MOTION_HS）"""
    t0 = time.perf_counter()
    ack = ctx.command(motion_alias, "handshake")
    handshake_ms = round((time.perf_counter() - t0) * 1000, 3)  # 只统计握手往返，不含稳定等待
    ctx.log(f"运动控制握手应答: {ack}（{handshake_ms} ms）")
    ctx.command(motion_alias, "set_speed", rpm=rpm)
    time.sleep(settle)
    measured = ctx.command(motion_alias, "read_speed")
    return {"RPM": measured, "HS_MS": handshake_ms}


def playback_signal(ctx, scope_alias="scope", signal="sine", freq=1000, vpp=2.4, points=2000, **_):
    """回放信号：按设定激励自动设置示波器并测量幅度与频率（阈值 TC_SIG_VPP / TC_SIG_FREQ）"""
    ctx.command(scope_alias, "autoset", kind=signal, freq=freq, vpp=vpp)
    items = ctx.command(scope_alias, "measure", items=["PK2PK", "FREQUENCY", "MEAN", "RMS"], points=points)
    measured = {m["type"]: m["value"] for m in (items or [])}
    ctx.log(f"示波器测量: {measured}")
    return {"VPP": measured.get("PK2PK"), "FREQ": measured.get("FREQUENCY"),
            "MEAN": measured.get("MEAN"), "RMS": measured.get("RMS")}


def load_voltage(ctx, eload_alias="eload", current=0.5, **_):
    """负载验证：电子负载 CC 模式加载后测量负载端电压（阈值 TC_ELOAD_V）"""
    ctx.command(eload_alias, "set_mode", mode="CC")
    ctx.command(eload_alias, "set_current", current=current)
    ctx.command(eload_alias, "input_on")
    value = ctx.command(eload_alias, "measure_voltage")
    return {"VLOAD": value}
