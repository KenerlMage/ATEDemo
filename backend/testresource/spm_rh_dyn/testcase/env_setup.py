# -*- coding: utf-8 -*-
"""环境初始化 / 终止用例（TPS 清单 setup / teardown 段）"""

from __future__ import annotations


def init_environment(ctx, psu_alias="psu", awg_alias="awg", spin_alias="spin",
                     scope_alias="scope", fixture_alias="fixture", voltage=3.3, **_):
    """上电 -> 设置回放激励 -> 读头复位 -> 示波器自检 -> 登记夹具信息"""
    ctx.log(f"环境初始化: 读头供电 {voltage} V")
    ctx.command(psu_alias, "set_voltage", voltage=voltage)
    ctx.command(psu_alias, "set_current", current=0.5)
    ctx.command(psu_alias, "output_on")

    ctx.log("环境初始化: 回放激励 1 kHz / 2.4 Vpp 正弦")
    ctx.command(awg_alias, "set_waveform", kind="SIN", frequency=1000, amplitude=2.4)
    ctx.command(awg_alias, "output_on")

    ctx.log("环境初始化: 转速控制盒握手并复位")
    ctx.command(spin_alias, "handshake")
    ctx.command(spin_alias, "home")

    ctx.log("环境初始化: 示波器自动设置与时基")
    ctx.command(scope_alias, "autoset")
    ctx.command(scope_alias, "timebase", scale=5e-4)
    ctx.command(scope_alias, "channel", name="CH1", display=True, scale=0.5, coupling="DC")

    # 被动设备（夹具/探针组）只登记信息，不参与通讯
    info = ctx.command(fixture_alias, "describe")
    ctx.log(f"夹具信息: {info}")
    return None


def teardown_environment(ctx, psu_alias="psu", awg_alias="awg", spin_alias="spin", **_):
    """关闭输出并停止运动，把装备留在安全状态"""
    ctx.log("环境终止: 关闭供电与激励输出")
    ctx.command(psu_alias, "output_off")
    ctx.command(awg_alias, "output_off")
    ctx.command(spin_alias, "stop")
    return None
