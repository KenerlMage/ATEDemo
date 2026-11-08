"""契约一致性套件：对任意能力的驱动包执行同一组断言。

使用方式
--------
    python -m kit.conformance --root <repo> --package <包名> --mode simulate
    python -m kit.conformance --root <repo> --package <包名> --mode hardware --resource <资源名>

设计原则
--------
- 只依赖 contracts 与包本身，不依赖平台业务代码，便于随包发布、现场自检。
- 每个检查项独立报告，失败不中断（末尾汇总），便于一次看到全部偏差。
- 仿真模式下额外校验「配置 → 数据 → 统计」的自洽性，防止实现返回固定值糊弄。
- 新能力只需补一个 suite 并登记到 SUITES，不需要改调用方。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from contracts.camera import (
    CAPABILITY as CAMERA_CAPABILITY,
    CONTRACT_VERSION as CAMERA_CONTRACT_VERSION,
    STATISTICS_ITEMS,
    CameraNotConnected,
    CameraUnsupported,
    ExposureSpec,
    RoiSpec,
    compute_statistics,
)
from contracts.motion import (
    CAPABILITY as HEXAPOD_CAPABILITY,
    CONTRACT_VERSION as HEXAPOD_CONTRACT_VERSION,
    MotionLimitError,
    MotionNotConnected,
    MotionUnsupported,
)
from contracts.scope import (
    CAPABILITY as SCOPE_CAPABILITY,
    CONTRACT_VERSION as SCOPE_CONTRACT_VERSION,
    MEASURE_ITEMS,
    ScopeNotConnected,
    ScopeUnsupported,
    TriggerSpec,
)

SCOPE_METHODS = (
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
#: 兼容旧引用
REQUIRED_METHODS = SCOPE_METHODS

CAMERA_METHODS = (
    "connect",
    "close",
    "identify",
    "configure_exposure",
    "configure_roi",
    "configure_trigger",
    "grab",
    "statistics",
    "describe",
)

MOTION_METHODS = (
    "connect",
    "close",
    "identify",
    "reference",
    "move_to",
    "move_relative",
    "query_position",
    "stop",
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


def _meta_check(rec: _Recorder, factory, capability: str, contract: str) -> None:
    def c_meta():
        d = factory()
        got = getattr(d, "capability", None)
        assert got == capability, f"capability 应为 {capability}，实际 {got}"
        ver = getattr(d, "contract_version", None)
        assert ver == contract, f"contract_version 应为 {contract}，实际 {ver}"
        return f"capability={capability} contract={contract}"

    rec.run("元信息：能力名与契约版本", c_meta)


def _methods_check(rec: _Recorder, factory, methods: tuple[str, ...]) -> None:
    def c_methods():
        d = factory()
        missing = [m for m in methods if not callable(getattr(d, m, None))]
        assert not missing, f"缺少契约方法: {missing}"
        return f"{len(methods)} 个方法齐备"

    rec.run("契约：方法齐备", c_methods)


# =====================================================================
# 示波器
# =====================================================================
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

    _meta_check(rec, new, SCOPE_CAPABILITY, SCOPE_CONTRACT_VERSION)
    _methods_check(rec, new, SCOPE_METHODS)

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


# =====================================================================
# 相机
# =====================================================================
def run_camera_suite(
    driver_factory: Callable[[], object],
    *,
    expect_mode: str = "simulate",
    exposure_us: float = 10_000.0,
    width: int = 160,
    height: int = 120,
) -> list[Check]:
    rec = _Recorder()

    def new():
        return driver_factory()

    _meta_check(rec, new, CAMERA_CAPABILITY, CAMERA_CONTRACT_VERSION)
    _methods_check(rec, new, CAMERA_METHODS)

    state: dict[str, object] = {}

    def c_connect():
        d = new()
        d.connect()
        ident = d.identify()
        for f in ("vendor", "model", "serial", "firmware", "resource"):
            assert getattr(ident, f, ""), f"identify 缺字段 {f}"
        state["driver"] = d
        return f"{ident.vendor} {ident.model} sn={ident.serial}"

    if not rec.run("生命周期：connect + identify", c_connect):
        return rec.items

    d = state["driver"]  # type: ignore[assignment]

    def c_exposure():
        d.configure_exposure(ExposureSpec(exposure_us=exposure_us, gain_db=0.0))
        d.configure_exposure(ExposureSpec(exposure_us=exposure_us, gain_db=0.0))  # 幂等
        snap = d.describe()
        assert snap.get("connected") is True, "describe.connected 应为 True"
        assert abs(float(snap["exposure"]["exposure_us"]) - exposure_us) < 1e-9, "describe 曝光与设置不一致"
        return f"exposure={exposure_us}us"

    rec.run("配置：曝光写入与幂等", c_exposure)

    def c_roi():
        d.configure_roi(RoiSpec(width=width, height=height))
        snap = d.describe()
        assert int(snap["roi"]["width"]) == width and int(snap["roi"]["height"]) == height, (
            f"describe ROI {snap['roi']} 与请求 {width}x{height} 不符"
        )
        return f"roi={width}x{height}"

    rec.run("配置：ROI 生效", c_roi)

    def c_grab():
        frames = d.grab(2)
        assert len(frames) == 2, f"请求 2 帧，返回 {len(frames)}"
        f = frames[0]
        assert f.width == width and f.height == height, f"帧尺寸 {f.width}x{f.height} 与 ROI 不符"
        assert f.nbytes == f.width * f.height, f"帧字节数 {f.nbytes} 与 {f.width}x{f.height} 不符"
        assert f.pixel_format == "Mono8", f"像素格式应为 Mono8，实际 {f.pixel_format}"
        assert frames[0].frame_id != frames[1].frame_id, "连续两帧的 frame_id 相同"
        state["frame"] = f
        return f"frame={f.width}x{f.height} bytes={f.nbytes} id={f.frame_id}"

    rec.run("数据：grab 尺寸/字节数/帧号", c_grab)

    def c_stats_all():
        f = state["frame"]
        bad = []
        for item in STATISTICS_ITEMS:
            try:
                v = d.statistics(f, item)
            except CameraUnsupported as exc:
                bad.append(f"{item}:{exc}")
                continue
            if not isinstance(v, float) or v != v:
                bad.append(f"{item}:非浮点或 NaN({v!r})")
        assert not bad, "; ".join(bad)
        return f"{len(STATISTICS_ITEMS)} 个统计项均返回浮点"

    rec.run("统计：全部统计项", c_stats_all)

    def c_stats_consistency():
        f = state["frame"]
        expect = compute_statistics(f.samples(), width=f.width)
        for item in ("mean_gray", "saturation_ratio", "defect_ratio"):
            got = d.statistics(f, item)
            assert abs(got - expect[item]) <= 1e-6, f"{item} 与契约口径不一致: {got} vs {expect[item]}"
        return f"mean_gray={expect['mean_gray']:.2f} saturation={expect['saturation_ratio']:.4f}"

    rec.run("统计：与契约口径自洽", c_stats_consistency)

    if expect_mode == "simulate":
        def c_exposure_monotonic():
            d.configure_exposure(ExposureSpec(exposure_us=5_000.0))
            low = d.statistics(d.grab(1)[0], "mean_gray")
            d.configure_exposure(ExposureSpec(exposure_us=20_000.0))
            high = d.statistics(d.grab(1)[0], "mean_gray")
            assert high > low + 1.0, f"曝光提高后亮度未上升: {low:.2f} → {high:.2f}"
            d.configure_exposure(ExposureSpec(exposure_us=exposure_us))
            return f"5ms→{low:.2f} 20ms→{high:.2f}"

        rec.run("仿真：曝光 → 亮度方向正确", c_exposure_monotonic)

    def c_unknown_stat():
        try:
            d.statistics(state["frame"], "not_a_stat")
        except CameraUnsupported:
            return "未知统计项抛 CameraUnsupported"
        raise AssertionError("未知统计项未抛 CameraUnsupported")

    rec.run("错误语义：未知统计项", c_unknown_stat)

    def c_bad_roi():
        try:
            d.configure_roi(RoiSpec(width=99999, height=99999))
        except CameraNotConnected:
            raise AssertionError("不应在连接状态下抛 CameraNotConnected")
        except Exception as exc:
            return f"{type(exc).__name__}"
        raise AssertionError("越界 ROI 未报错")

    rec.run("错误语义：ROI 越界", c_bad_roi)

    def c_describe():
        snap = d.describe()
        for key in ("capability", "package", "package_version", "resource", "mode", "connected"):
            assert key in snap, f"describe 缺字段 {key}"
        return f"package={snap['package']}@{snap['package_version']} mode={snap['mode']}"

    rec.run("诊断：describe 字段", c_describe)

    def c_close():
        d.close()
        try:
            d.grab(1)
        except CameraNotConnected:
            return "关闭后调用抛 CameraNotConnected"
        raise AssertionError("关闭后仍可取图")

    rec.run("生命周期：close 后拒绝服务", c_close)
    return rec.items


# =====================================================================
# 六足 / 定位平台
# =====================================================================
def run_motion_suite(
    driver_factory: Callable[[], object],
    *,
    expect_mode: str = "simulate",
    step_um: float = 1000.0,
) -> list[Check]:
    rec = _Recorder()

    def new():
        return driver_factory()

    _meta_check(rec, new, HEXAPOD_CAPABILITY, HEXAPOD_CONTRACT_VERSION)
    _methods_check(rec, new, MOTION_METHODS)

    state: dict[str, object] = {}

    def c_connect():
        d = new()
        d.connect()
        ident = d.identify()
        for f in ("vendor", "model", "serial", "firmware", "resource"):
            assert getattr(ident, f, ""), f"identify 缺字段 {f}"
        assert getattr(ident, "axes", ()), "identify 缺轴清单"
        state["driver"] = d
        return f"{ident.vendor} {ident.model} axes={len(ident.axes)}"

    if not rec.run("生命周期：connect + identify", c_connect):
        return rec.items

    d = state["driver"]  # type: ignore[assignment]

    def c_unreferenced():
        try:
            d.move_to({"X": 0.0})
        except MotionNotConnected:
            raise AssertionError("不应在连接状态下抛 MotionNotConnected")
        except MotionUnsupported:
            return "未回零时拒绝运动（MotionUnsupported）"
        raise AssertionError("未回零却允许运动")

    rec.run("时序：未回零拒绝运动", c_unreferenced)

    def c_reference():
        d.reference()
        pos = d.query_position()
        assert pos.as_dict(), "回零后位姿为空"
        assert d.describe().get("referenced") is True, "describe.referenced 应为 True"
        return f"axes={sorted(pos.values)} X={pos.get('X'):.1f}"

    rec.run("参考：reference + 位姿可读", c_reference)

    def c_move():
        target = {"X": step_um, "Y": -step_um}
        pos = d.move_to(target)
        for axis, want in target.items():
            got = pos.get(axis)
            assert abs(got - want) <= 0.5, f"轴 {axis} 到位误差过大: {got} vs {want}"
        state["pos"] = pos
        return f"X={pos.get('X'):.1f} Y={pos.get('Y'):.1f}"

    rec.run("运动：move_to 到位", c_move)

    def c_idempotent():
        pos1 = d.move_to({"X": step_um, "Y": -step_um})
        pos2 = d.move_to({"X": step_um, "Y": -step_um})
        assert abs(pos1.get("X") - pos2.get("X")) <= 0.5, "重复移动到同一目标后位置漂移"
        return "重复下发同一目标位置稳定"

    rec.run("运动：重复目标幂等", c_idempotent)

    def c_relative():
        before = d.query_position().get("X")
        pos = d.move_relative({"X": -step_um / 2})
        assert abs(pos.get("X") - (before - step_um / 2)) <= 0.5, (
            f"相对移动结果 {pos.get('X')} 与期望 {before - step_um / 2} 不符"
        )
        return f"X {before:.1f} → {pos.get('X'):.1f}"

    rec.run("运动：move_relative 增量正确", c_relative)

    def c_limit():
        try:
            d.move_to({"X": 9e9})
        except MotionLimitError:
            return "超行程抛 MotionLimitError"
        raise AssertionError("超行程未抛 MotionLimitError")

    rec.run("错误语义：超出行程", c_limit)

    def c_bad_axis():
        try:
            d.move_to({"Q": 1.0})
        except MotionUnsupported:
            return "未知轴抛 MotionUnsupported"
        raise AssertionError("未知轴未报错")

    rec.run("错误语义：未知轴", c_bad_axis)

    def c_stop():
        d.stop(["X"])
        return "stop(X) 可用"

    rec.run("运动：stop 可调用", c_stop)

    def c_describe():
        snap = d.describe()
        for key in ("capability", "package", "package_version", "resource", "mode", "connected", "position"):
            assert key in snap, f"describe 缺字段 {key}"
        return f"package={snap['package']}@{snap['package_version']} mode={snap['mode']}"

    rec.run("诊断：describe 字段", c_describe)

    def c_close():
        d.close()
        try:
            d.query_position()
        except MotionNotConnected:
            return "关闭后调用抛 MotionNotConnected"
        raise AssertionError("关闭后仍可读位姿")

    rec.run("生命周期：close 后拒绝服务", c_close)
    return rec.items


SUITES: dict[str, Callable[..., list[Check]]] = {
    SCOPE_CAPABILITY: run_suite,
    CAMERA_CAPABILITY: run_camera_suite,
    HEXAPOD_CAPABILITY: run_motion_suite,
}

DEFAULT_RESOURCE = {
    SCOPE_CAPABILITY: "TCPIP::192.168.10.21::INSTR",
    CAMERA_CAPABILITY: "first",
    HEXAPOD_CAPABILITY: "tcpip://192.168.10.31:50000",
}


def run_package_suite(manifest, driver_cls, *, mode: str = "simulate", resource: str | None = None):
    """按清单声明的能力选套件——新增能力只需登记 SUITES。"""
    suite = SUITES.get(manifest.capability)
    if suite is None:
        raise SystemExit(f"没有为能力 {manifest.capability} 定义一致性套件；可选 {sorted(SUITES)}")
    res = resource or DEFAULT_RESOURCE.get(manifest.capability, "first")
    factory = lambda: driver_cls(res, mode=mode)  # noqa: E731
    return suite(factory, expect_mode=mode)


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="驱动包契约一致性检查")
    ap.add_argument("--root", required=True, help="驱动仓库根目录")
    ap.add_argument("--package", required=True, help="包名，如 rohde-schwarz-mxo")
    ap.add_argument("--mode", default="simulate", choices=["simulate", "hardware"])
    ap.add_argument("--resource", default=None, help="资源名；缺省按能力取示例值")
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

    checks = run_package_suite(manifest, driver_cls, mode=args.mode, resource=args.resource)
    for c in checks:
        print(("[PASS] " if c.ok else "[FAIL] ") + c.name + (f" — {c.detail}" if c.detail else ""))
    failed = [c for c in checks if not c.ok]
    print(f"\n{manifest.label()}：{len(checks) - len(failed)}/{len(checks)} 通过（mode={args.mode}）")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
