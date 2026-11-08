"""契约一致性套件：对任意示波器包执行同一组断言。

使用方式
--------
    python -m kit.conformance --root <repo> --package rohde-schwarz-mxo --mode simulate

设计原则
--------
- 只依赖 contracts 与包本身，不依赖平台业务代码，便于随包发布、现场自检。
- 每个检查项独立报告，失败不中断（末尾汇总），便于一次看到全部偏差。
- 仿真模式下额外校验「配置 → 波形 → 测量」三者的一致性，防止实现用固定值糊弄。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from contracts.scope import (
    CAPABILITY,
    CONTRACT_VERSION,
    MEASURE_ITEMS,
    ScopeNotConnected,
    ScopeUnsupported,
    TriggerSpec,
)

REQUIRED_METHODS = (
    "connect",
    "close",
    "identify",
    "configure_timebase",
    "configure_channel",
    "configure_trigger",
    "acquire",
    "measure",
    "describe",
)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


class _Recorder:
    def __init__(self) -> None:
        self.items: list[Check] = []

    def run(self, name: str, fn: Callable[[], str | None]) -> bool:
        try:
            detail = fn() or ""
            self.items.append(Check(name, True, detail))
            return True
        except AssertionError as exc:
            self.items.append(Check(name, False, str(exc)))
            return False
        except Exception as exc:  # 未预期异常也算失败，并保留类型
            self.items.append(Check(name, False, f"{type(exc).__name__}: {exc}"))
            return False


def run_suite(
    driver_factory: Callable[[], object],
    *,
    expect_mode: str = "simulate",
    points: int = 2000,
    time_span_s: float = 1e-3,
    channel: int = 1,
    vertical_scale_v: float = 0.5,
    expected_cycles: float | None = 2.0,
    verbose: bool = False,
) -> list[Check]:
    rec = _Recorder()

    def new():
        return driver_factory()

    # 1. 元信息
    def c_meta():
        d = new()
        assert getattr(d, "capability", None) == CAPABILITY, f"capability 应为 {CAPABILITY}"
        assert getattr(d, "contract_version", None) == CONTRACT_VERSION, (
            f"contract_version 应为 {CONTRACT_VERSION}，实际 {getattr(d, 'contract_version', None)}"
        )
        return f"capability={CAPABILITY} contract={CONTRACT_VERSION}"

    rec.run("元信息：能力名与契约版本", c_meta)

    def c_methods():
        d = new()
        missing = [m for m in REQUIRED_METHODS if not callable(getattr(d, m, None))]
        assert not missing, f"缺少契约方法: {missing}"
        return f"{len(REQUIRED_METHODS)} 个方法齐备"

    rec.run("契约：方法齐备", c_methods)

    # 2. 生命周期
    state: dict[str, object] = {}

    def c_connect():
        d = new()
        d.connect()
        ident = d.identify()
        for f in ("vendor", "model", "serial", "firmware", "resource"):
            assert getattr(ident, f, ""), f"identify 缺字段 {f}"
        state["driver"] = d
        return f"{ident.vendor} {ident.model} fw={ident.firmware}"

    if not rec.run("生命周期：connect + identify", c_connect):
        return rec.items

    d = state["driver"]  # type: ignore[assignment]

    def c_configure():
        d.configure_timebase(time_span_s=time_span_s)
        d.configure_timebase(time_span_s=time_span_s)  # 幂等
        d.configure_channel(channel, scale_v=vertical_scale_v, offset_v=0.0, coupling="DC")
        d.configure_trigger(TriggerSpec(source_channel=channel, level_v=0.0, slope="RISING", mode="NORMAL"))
        snap = d.describe()
        assert snap.get("connected") is True, "describe.connected 应为 True"
        assert abs(snap["timebase"]["time_span_s"] - time_span_s) < 1e-12, "describe 时基与实际不一致"
        return f"time_span={time_span_s}s scale={vertical_scale_v}V/div"

    rec.run("配置：时基/通道/触发（含幂等）", c_configure)

    def c_acquire():
        wf = d.acquire([channel], points=points)[channel]
        assert abs(wf.n - points) <= 1, f"点数 {wf.n} 与请求 {points} 不符"
        assert wf.dt_s > 0, "时间轴步长必须为正"
        assert all(abs(v) < 1e6 for v in wf.samples[:64]), "波形含异常大值"
        assert abs(wf.duration_s - time_span_s) <= 0.25 * time_span_s, (
            f"采集时长 {wf.duration_s:.3e}s 与设置 {time_span_s:.3e}s 偏差过大"
        )
        state["wf"] = wf
        return f"n={wf.n} dt={wf.dt_s:.3e}s duration={wf.duration_s:.3e}s vpp={wf.vpp:.4f}V"

    rec.run("数据：acquire 长度/时间轴/幅值", c_acquire)

    def c_measure_all():
        wf = state["wf"]
        bad = []
        for item in sorted(MEASURE_ITEMS):
            try:
                v = d.measure(channel, item)
            except ScopeUnsupported as exc:
                bad.append(f"{item}:{exc}")
                continue
            if not isinstance(v, float) or v != v:
                bad.append(f"{item}:非浮点或 NaN({v!r})")
        assert not bad, "; ".join(bad)
        return f"{len(MEASURE_ITEMS)} 个测量项均返回浮点"

    rec.run("测量：全部测量项", c_measure_all)

    def c_measure_consistency():
        wf = state["wf"]
        vpp = d.measure(channel, "vpp")
        vmax = d.measure(channel, "vmax")
        vmin = d.measure(channel, "vmin")
        assert abs((vmax - vmin) - vpp) <= 1e-6 + 1e-6 * abs(vpp), "vpp 与 vmax-vmin 不自洽"
        assert abs(vpp - wf.vpp) <= 1e-6 + 1e-6 * abs(wf.vpp), "measure(vpp) 与最近波形不一致"
        return f"vpp={vpp:.4f}V vmax={vmax:.4f} vmin={vmin:.4f}"

    rec.run("测量：与波形自洽", c_measure_consistency)

    def c_measure_scale_relation():
        wf = state["wf"]
        expect_amp = 0.8 * vertical_scale_v
        assert abs(wf.vpp / 2 - expect_amp) <= 0.15 * expect_amp, (
            f"仿真幅值 {wf.vpp / 2:.4f}V 与档位推算 {expect_amp:.4f}V 偏差过大"
        )
        return f"半幅={wf.vpp / 2:.4f}V 期望={expect_amp:.4f}V"

    if expect_mode == "simulate":
        rec.run("仿真：档位 → 幅值关系", c_measure_scale_relation)

        def c_freq():
            if expected_cycles is None:
                return "跳过"
            f = d.measure(channel, "frequency")
            expect = expected_cycles / time_span_s
            assert abs(f - expect) <= 0.05 * expect, f"频率 {f:.4g}Hz 与期望 {expect:.4g}Hz 偏差 >5%"
            return f"frequency={f:.4g}Hz 期望={expect:.4g}Hz"

        rec.run("仿真：频率与整屏周期数一致", c_freq)

    # 3. 错误语义
    def c_unknown_item():
        try:
            d.measure(channel, "not_a_real_item")
        except ScopeUnsupported:
            return "未知测量项抛 ScopeUnsupported"
        raise AssertionError("未知测量项未抛 ScopeUnsupported")

    rec.run("错误语义：未知测量项", c_unknown_item)

    def c_bad_channel():
        try:
            d.acquire([99], points=64)
        except ScopeNotConnected:
            raise AssertionError("不应在连接状态下抛 ScopeNotConnected")
        except Exception as exc:
            return f"{type(exc).__name__}"
        raise AssertionError("非法通道未报错")

    rec.run("错误语义：非法通道", c_bad_channel)

    # 4. 诊断与关闭
    def c_describe():
        snap = d.describe()
        for key in ("capability", "package", "package_version", "resource", "mode", "connected"):
            assert key in snap, f"describe 缺字段 {key}"
        return f"package={snap['package']}@{snap['package_version']} mode={snap['mode']}"

    rec.run("诊断：describe 字段", c_describe)

    def c_close():
        d.close()
        try:
            d.acquire([channel], points=64)
        except ScopeNotConnected:
            return "关闭后调用抛 ScopeNotConnected"
        raise AssertionError("关闭后仍可采集")

    rec.run("生命周期：close 后拒绝服务", c_close)
    return rec.items


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="驱动包契约一致性检查")
    ap.add_argument("--root", required=True, help="驱动仓库根目录")
    ap.add_argument("--package", required=True, help="包名，如 rohde-schwarz-mxo")
    ap.add_argument("--mode", default="simulate", choices=["simulate", "hardware"])
    ap.add_argument("--resource", default="TCPIP::192.168.10.21::INSTR")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(args.root).resolve()))
    from kit.registry import Registry

    reg = Registry(Path(args.root).resolve())
    reg.load()
    cands = [m for m in reg.all() if m.name == args.package]
    if not cands:
        print(f"未找到包 {args.package}")
        return 2
    manifest = cands[0]

    sys.path.insert(0, str(manifest.pkg_dir))
    module = __import__(manifest.module, fromlist=["*"])
    driver_cls = getattr(module, manifest.attribute)

    def factory():
        return driver_cls(args.resource, mode=args.mode)

    checks = run_suite(factory, expect_mode=args.mode)
    failed = [c for c in checks if not c.ok]
    for c in checks:
        mark = "PASS" if c.ok else "FAIL"
        print(f"[{mark}] {c.name}" + (f" — {c.detail}" if c.detail else ""))
    print(f"\n{len(checks) - len(failed)}/{len(checks)} 通过")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
