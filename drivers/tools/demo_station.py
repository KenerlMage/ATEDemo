"""端到端演示：解析 → 会话 → 示波器采集 → 相机取图统计 → 六足定位。

    python tools/demo_station.py --station station-types/standard-bench.json --bench BENCH-CAB

默认走仿真；加 --mode hardware 会真实加载厂商库（RsInstrument / pypylon / pipython），
缺库或连不上设备时抛 DriverUnavailable，不静默降级。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.camera import ExposureSpec, RoiSpec  # noqa: E402
from kit.registry import Registry  # noqa: E402
from kit.resolve import load_lock, load_station_type, resolve  # noqa: E402
from runtime.session import DeviceRecord, DriverSession  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="标准柜综合台端到端演示")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--station", default="station-types/standard-bench.json")
    ap.add_argument("--bench", default="BENCH-CAB")
    ap.add_argument("--mode", default="simulate", choices=["simulate", "hardware"])
    ap.add_argument("--skip-lock", action="store_true", help="不校验 lock 完整性，按台型声明取最新版")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    reg = Registry(root)
    reg.load()
    station = load_station_type(root / args.station)

    lock = None if args.skip_lock else load_lock(root / "benches" / args.bench / "bench.lock.json")
    rs = resolve(station, reg, lock, contract_version="1.0")
    print("解析结果：")
    for r in rs:
        print(f"  {r.capability:<14} {r.label}")

    devices_path = root / "benches" / args.bench / "devices.json"
    raw = json.loads(devices_path.read_text(encoding="utf-8"))["devices"]
    devices = {
        cap: DeviceRecord(
            capability=cap,
            resource=item["resource"],
            mode=args.mode,
            timeout_ms=int(item.get("timeout_ms", 15000)),
            extra={k: v for k, v in item.items() if k not in ("resource", "mode", "timeout_ms")},
        )
        for cap, item in raw.items()
        if cap in {r.capability for r in rs}
    }

    with DriverSession(rs, devices, bench=args.bench) as session:
        if "oscilloscope" in devices:
            with session.operation("oscilloscope") as scope:
                scope.configure_timebase(time_span_s=1e-3)
                scope.configure_channel(1, scale_v=0.5)
                wf = scope.acquire([1], points=1000)[1]
                print(
                    f"示波器：n={wf.n} vpp={wf.vpp:.4f}V "
                    f"freq={scope.measure(1, 'frequency'):.1f}Hz rise={scope.measure(1, 'rise_time') * 1e6:.1f}us"
                )

        if "camera" in devices:
            with session.operation("camera") as cam:
                cam.configure_exposure(ExposureSpec(exposure_us=9000, gain_db=3.0))
                cam.configure_roi(RoiSpec(width=160, height=120))
                frame = cam.grab(1)[0]
                stats = {k: cam.statistics(frame, k) for k in
                         ("mean_gray", "saturation_ratio", "snr_db", "sharpness", "uniformity", "defect_ratio")}
                print(
                    f"相机：{frame.width}x{frame.height} mean={stats['mean_gray']:.1f} "
                    f"sat={stats['saturation_ratio']:.4f} snr={stats['snr_db']:.2f}dB "
                    f"sharp={stats['sharpness']:.2f} unif={stats['uniformity']:.3f} defect={stats['defect_ratio']:.4f}"
                )

        if "hexapod" in devices:
            with session.operation("hexapod") as stage:
                stage.reference()
                pos = stage.move_to({"X": 1200.0, "Y": -800.0, "Z": 300.0})
                print(
                    "六足："
                    + " ".join(f"{axis}={pos.get(axis):.1f}" for axis in ("X", "Y", "Z"))
                    + f"  到位判定={stage.query_position().distance_to(pos):.3f}um"
                )

        snap = session.describe()
        print("会话拓扑：")
        for d in snap["devices"]:
            print(f"  {d['capability']:<14} {d['package']}@{d['version']} mode={d['mode']} connected={d['connected']}")
        print(f"事件数：{snap['events']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
