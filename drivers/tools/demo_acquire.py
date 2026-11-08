"""端到端演示：台型清单 + lock → 解析包 → 建会话 → 配置 → 采集 → 测量。

    python tools/demo_acquire.py --mode simulate
    python tools/demo_acquire.py --mode hardware --resource TCPIP::192.168.10.21::INSTR

演示刻意只走「平台公开路径」：不直接 import 驱动类，不读 packages 目录内部文件。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.registry import Registry  # noqa: E402
from kit.resolve import load_lock, load_station_type, resolve  # noqa: E402
from runtime.session import DeviceRecord, DriverSession  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="simulate", choices=["simulate", "hardware"])
    ap.add_argument("--resource", default=None)
    ap.add_argument("--bench", default="BENCH-DEMO")
    ap.add_argument("--station", default=str(ROOT / "station-types" / "scope-bench.json"))
    ap.add_argument("--channel", type=int, default=1)
    ap.add_argument("--points", type=int, default=2000)
    a = ap.parse_args()

    reg = Registry(ROOT)
    reg.load()
    station = load_station_type(a.station)

    bench_dir = ROOT / "benches" / a.bench
    lock_path = bench_dir / "bench.lock.json"
    lock = load_lock(lock_path) if lock_path.is_file() else None
    print(f"[1] 台型 {station.name} · lock {'已加载 ' + lock_path.name if lock else '缺失（按最新版解析）'}")

    resolutions = resolve(station, reg, lock, contract_version="1.0")
    for r in sorted(resolutions, key=lambda x: x.capability):
        print(f"    解析 {r.capability:<20} → {r.label}  sha256={r.manifest.tree_sha256[:12]}…")

    devices_raw = json.loads((bench_dir / "devices.json").read_text(encoding="utf-8"))
    devices = {
        cap: DeviceRecord(
            capability=cap,
            resource=(a.resource or d["resource"]),
            mode=a.mode,
            timeout_ms=int(d.get("timeout_ms", 15000)),
        )
        for cap, d in devices_raw["devices"].items()
        if cap in [r.capability for r in resolutions]
    }
    print(f"[2] 设备档案投影 {a.bench} → " + ", ".join(f"{k}:{v.mode}" for k, v in devices.items()))

    with DriverSession(resolutions, devices, bench=a.bench, timeout_s=20.0, retries=1) as session:
        print("[3] 会话建立")
        with session.operation("oscilloscope") as scope:
            ident = scope.identify()
            print(f"    {ident.vendor} {ident.model} serial={ident.serial} fw={ident.firmware}")

            scope.configure_timebase(time_span_s=1e-3)
            scope.configure_channel(a.channel, scale_v=0.5, offset_v=0.0, coupling="DC")
            from contracts.scope import TriggerSpec

            scope.configure_trigger(TriggerSpec(source_channel=a.channel, level_v=0.0, slope="RISING"))

            wf = scope.acquire([a.channel], points=a.points)[a.channel]
            print(
                f"[4] 采集 ch{a.channel}: n={wf.n} dt={wf.dt_s:.3e}s "
                f"duration={wf.duration_s:.3e}s vpp={wf.vpp:.4f}V"
            )

            for item in ("vpp", "mean", "rms", "frequency", "rise_time"):
                print(f"    measure({item:<10}) = {scope.measure(a.channel, item):.6g}")

        print("[5] 拓扑快照")
        print(json.dumps(session.describe(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
