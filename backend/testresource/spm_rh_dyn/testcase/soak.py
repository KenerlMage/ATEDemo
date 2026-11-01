# -*- coding: utf-8 -*-
"""连续运行稳定性（soak）用例：运行约 90 s，过程中逐秒上传数据

阶段划分（默认参数，可通过 TPS 清单的 params 调整）：

    阶段 1  转速斜坡   30 s   1200 rpm -> 3600 rpm，每 2 s 提升一档
    阶段 2  稳态运行   45 s   3600 rpm 连续运行
    阶段 3  加载验证   15 s   电子负载 CC 0.5 A 投入，读取负载端电压

数据上传：

    * 电压 / 电流由数字万用表逐秒测量，转速由运动控制盒逐秒回读，
      回放幅度与频率每 3 s 由示波器测量一次，加载段逐秒读负载端电压；
    * 每组数据经 `ate_rt` 写入实时数据通道（Redis KVS，仅支持 Redis），
      Runner 的「实时监视」窗口可在测试进行中看到曲线；
    * 运行环境没有 `ate_rt`（早期 SDK）时自动退化为只测量不上传，不影响判定。

判定口径：各阶段统计量汇总后由清单 `checks` 映射到 testconfig 阈值，用例本身不写死阈值。
"""

from __future__ import annotations

import time
from typing import Any, Optional

PHASE1_S = 30.0      # 转速斜坡
PHASE2_S = 45.0      # 稳态运行
PHASE3_S = 15.0      # 加载验证
RAMP_START = 1200    # 斜坡起始转速 rpm
RAMP_END = 3600      # 斜坡目标转速 rpm
RAMP_STEP_S = 2.0    # 每档保持时间 s
SCOPE_EVERY = 3      # 示波器取样间隔（按采样点计数）


def _rt() -> Optional[Any]:
    """惰性获取 ate_rt；不可用时返回 None（用例退化为只测量）"""
    try:
        import ate_rt  # noqa: F401
        return ate_rt
    except Exception:  # pragma: no cover - 取决于运行环境
        return None


def _declare(rt) -> None:
    if rt is None:
        return
    rt.declare("vdd", label="供电电压", unit="V", group="供电", lo=3.0, hi=3.6)
    rt.declare("idd", label="工作电流", unit="mA", group="供电", lo=100, hi=300)
    rt.declare("power", label="输入功率", unit="W", group="供电", lo=0, hi=2)
    rt.declare("rpm", label="实际转速", unit="rpm", group="运动", lo=0, hi=3800)
    rt.declare("rpm_target", label="目标转速", unit="rpm", group="运动", lo=0, hi=3800)
    rt.declare("vpp", label="回放幅度", unit="V", group="信号", lo=2.0, hi=2.8)
    rt.declare("freq", label="回放频率", unit="Hz", group="信号", lo=980, hi=1020)
    rt.declare("load_v", label="负载端电压", unit="V", group="负载", lo=3.0, hi=3.6)
    rt.declare("stage_no", label="测试阶段", unit="", group="流程", lo=1, hi=3)


def _value(drv, action: str, **params):
    """直接调用驱动并取 value（不经 ctx.command，避免逐秒轮询刷满执行日志）"""
    out = drv.do(action, **params) or {}
    return out.get("value")


def _num(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _avg(values: list) -> Optional[float]:
    nums = [v for v in (_num(x) for x in values) if v is not None]
    return round(sum(nums) / len(nums), 6) if nums else None


def _peak(values: list) -> Optional[float]:
    nums = [v for v in (_num(x) for x in values) if v is not None]
    return round(max(nums), 6) if nums else None


def continuous_run_soak(ctx, psu_alias="psu", dmm_alias="dmm", spin_alias="spin",
                        scope_alias="scope", eload_alias="eload",
                        voltage=3.3, rpm=RAMP_END, load_current=0.5,
                        phase1_s=PHASE1_S, phase2_s=PHASE2_S, phase3_s=PHASE3_S,
                        sample_s=1.0, **_):
    """连续运行稳定性：转速斜坡 -> 稳态运行 -> 加载验证，全程逐秒上传数据"""
    total_s = float(phase1_s) + float(phase2_s) + float(phase3_s)
    ctx.log(f"连续运行开始：共 {total_s:.0f} s（斜坡 {phase1_s:.0f} s / 稳态 {phase2_s:.0f} s / "
            f"加载 {phase3_s:.0f} s），采样周期 {sample_s:.1f} s")

    # 供电与激励就位（环境初始化已做过，这里做幂等确认，便于单独重跑本用例）
    ctx.command(psu_alias, "set_voltage", voltage=voltage)
    ctx.command(psu_alias, "output_on")

    dmm = ctx.driver(dmm_alias)
    spin = ctx.driver(spin_alias)
    scope = ctx.driver(scope_alias)
    eload = ctx.driver(eload_alias)

    rt = _rt()
    _declare(rt)
    if rt is None:
        ctx.log("实时数据通道不可用（未找到 ate_rt），本用例只测量不上传")

    ramp_target = RAMP_START
    ctx.command(spin_alias, "set_speed", rpm=ramp_target)
    ctx.command(scope_alias, "autoset", kind="sine", freq=1000, vpp=2.4)

    bucket: dict = {1: [], 2: [], 3: []}   # 阶段 -> [(vdd, idd, rpm, vpp, freq, load)]
    samples = 0
    ramp_err_max = 0.0
    stage = 1
    loaded = False
    t0 = time.monotonic()
    next_at = t0
    if rt is not None:
        rt.event(f"连续运行开始（{total_s:.0f} s）")

    while True:
        now = time.monotonic()
        elapsed = now - t0
        if elapsed >= total_s:
            break

        # ---------------- 阶段切换 ----------------
        want = 1 if elapsed < phase1_s else (2 if elapsed < phase1_s + phase2_s else 3)
        if want != stage:
            stage = want
            if stage == 2:
                ramp_target = float(rpm)
                ctx.command(spin_alias, "set_speed", rpm=ramp_target)
                ctx.log(f"进入稳态运行：转速 {ramp_target:.0f} rpm")
                if rt is not None:
                    rt.event("进入稳态运行")
            elif stage == 3:
                ctx.command(eload_alias, "set_mode", mode="CC")
                ctx.command(eload_alias, "set_current", current=load_current)
                ctx.command(eload_alias, "input_on")
                loaded = True
                ctx.log(f"进入加载验证：电子负载 CC {load_current} A 投入")
                if rt is not None:
                    rt.event(f"加载投入 {load_current} A")

        # ---------------- 阶段 1：转速斜坡 ----------------
        if stage == 1:
            step_no = int((elapsed) // RAMP_STEP_S) + 1
            want_rpm = min(RAMP_START + (step_no - 1) * ((RAMP_END - RAMP_START) / 4.0), RAMP_END)
            if want_rpm != ramp_target:
                ramp_target = want_rpm
                ctx.command(spin_alias, "set_speed", rpm=ramp_target)

        # ---------------- 采集 ----------------
        vdd = _num(_value(dmm, "measure_dc_voltage"))
        idd = _num(_value(dmm, "measure_dc_current"))
        measured_rpm = _num(_value(spin, "read_speed"))
        vpp = freq = None
        if samples % SCOPE_EVERY == 0:
            items = _value(scope, "measure", items=["PK2PK", "FREQUENCY"], points=2000) or []
            measured = {m.get("type"): _num(m.get("value")) for m in items if isinstance(m, dict)}
            vpp, freq = measured.get("PK2PK"), measured.get("FREQUENCY")
        else:
            prev = bucket[stage][-1] if bucket[stage] else None
            vpp = prev[3] if prev else None
            freq = prev[4] if prev else None
        load_v = _num(_value(eload, "measure_voltage")) if loaded else None

        if stage == 1 and measured_rpm is not None:
            ramp_err_max = max(ramp_err_max, abs(measured_rpm - ramp_target))

        bucket[stage].append((vdd, idd, measured_rpm, vpp, freq, load_v))

        # ---------------- 上传 ----------------
        if rt is not None:
            payload = {"vdd": vdd, "idd": idd, "rpm": measured_rpm,
                       "rpm_target": ramp_target, "stage_no": stage}
            if vdd is not None and idd is not None:
                payload["power"] = round(vdd * idd / 1000.0, 6)
            if vpp is not None:
                payload["vpp"] = vpp
            if freq is not None:
                payload["freq"] = freq
            if load_v is not None:
                payload["load_v"] = load_v
            rt.write_many({k: v for k, v in payload.items() if v is not None})

        samples += 1
        if samples % 10 == 0:
            ctx.log(f"连续运行 {elapsed:.0f}/{total_s:.0f} s，阶段 {stage}，"
                    f"已上传 {samples} 组（电压 {vdd} V / 电流 {idd} mA / 转速 {measured_rpm} rpm）")

        next_at += float(sample_s)
        delay = next_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    if rt is not None:
        rt.event("连续运行结束")

    # ---------------- 统计 ----------------
    def column(index: int, phases=(1, 2, 3)) -> list:
        out = []
        for ph in phases:
            out.extend(row[index] for row in bucket[ph])
        return out

    steady_vdd = _avg(column(0, (2,)))
    steady_rpm = _avg(column(2, (2,)))
    result = {
        "VDD_STEADY": steady_vdd,
        "IDD_MAX": _peak(column(1)),
        "RPM_STEADY": steady_rpm,
        "RAMP_ERR_MAX": round(ramp_err_max, 6),
        "VPP_STEADY": _avg(column(3)),
        "LOAD_V": _avg(column(5, (3,))),
        "SAMPLES": samples,
    }
    ctx.log(f"连续运行统计：{result}（共 {samples} 组数据，{total_s:.0f} s）")
    return result
