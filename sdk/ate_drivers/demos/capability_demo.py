# -*- coding: utf-8 -*-
"""能力层演示：示波器 / PI 六足 / 自准直仪。

演示的是"先问再做"这条规矩：调用方先看 `supports()`，再做 `do()`，
不支持的路径要能优雅跳过，而不是抛异常或偷偷降级。

运行（工作目录 = sdk/）：
    python -m ate_drivers.demos.capability_demo
    python -m ate_drivers.demos.capability_demo --json
"""
from __future__ import annotations

import argparse
import json
import sys

from ..vendors.autocollimator_ascii.driver import AsciiAutocollimator
from ..vendors.pi_hexapod.driver import PiHexapod
from ..vendors.tektronix_mso.driver import TekMsoScope

DEVICES = (
    ("示波器", TekMsoScope, {
        "device_id": "SCOPE-01", "device_type": "tektronix-mso5",
        "model": "MSO54", "interface": "LAN", "host": "127.0.0.1", "port": 5025,
    }),
    ("六足平台", PiHexapod, {
        "device_id": "HEX-01", "device_type": "pi-hexapod",
        "model": "H-811", "interface": "LAN", "host": "192.168.1.51", "port": 50000,
    }),
    ("自准直仪", AsciiAutocollimator, {
        "device_id": "AC-01", "device_type": "autocollimator-ascii",
        "model": "AC-100", "interface": "SERIAL", "serial_port": "COM4", "baudrate": 9600,
    }),
)


def run_scope(dev) -> dict:
    """示波器：时基 + 采集 + 测量，全部走能力名，不认厂商命令。"""
    out: dict = {}
    if dev.supports("scope.timebase"):
        out["timebase"] = dev.do("scope.timebase", scale_s_per_div=0.001)["value"]
    if dev.supports("scope.channel"):
        out["channel"] = dev.do("scope.channel", name="CH1", scale_v_per_div=1.0)["value"]
    if dev.supports("scope.acquire_waveform"):
        wf = dev.do("scope.acquire_waveform", points=200)["value"]
        out["waveform"] = {"points": wf["points"], "x_incr_s": wf["x_incr_s"],
                           "first3": wf["samples"][:3]}
    if dev.supports("scope.measure"):
        items = dev.do("scope.measure", items=["PK2PK", "FREQUENCY"])["value"]
        # 家族契约里 measure 返回的是测量项列表，每项自带 type/value/unit
        out["measure"] = {m["type"]: (round(float(m["value"]), 6), m["unit"])
                          for m in items if m.get("value") is not None}
    if dev.supports("scope.stop"):
        dev.do("scope.stop")
    return out


def run_hexapod(dev) -> dict:
    out: dict = {}
    if dev.supports("motion.read_position"):
        out["position"] = dev.do("motion.read_position")["value"]
    if dev.supports("motion.move"):
        out["moved_to"] = dev.do("motion.move", x=1.5, y=-0.5)["value"]
        out["move_noop"] = dev.do("motion.move")["value"]  # 零参数 = 只读回当前位置
    if dev.supports("vendor.pi.servo"):
        out["servo"] = dev.do("vendor.pi.servo", enabled=True)["value"]
    if dev.supports("vendor.pi.stop"):
        out["stop"] = dev.do("vendor.pi.stop")["value"]
    return out


def run_autocollimator(dev) -> dict:
    out: dict = {}
    if dev.supports("vendor.autocollimator.measure"):
        r = dev.do("vendor.autocollimator.measure")
        # do() 把非标准键收进 detail，读数要从那里取
        det = r.get("detail") or {}
        out["angle"] = {"x": det.get("x_arcsec"), "y": det.get("y_arcsec"),
                        "magnitude": round(float(det.get("magnitude_arcsec") or 0.0), 6),
                        "unit": det.get("unit") or r.get("unit")}
    if dev.supports("vendor.autocollimator.mode"):
        out["mode"] = dev.do("vendor.autocollimator.mode", mode="angle")["value"]
    if dev.supports("vendor.autocollimator.zero"):
        out["zoom"] = dev.do("vendor.autocollimator.zero")["value"]
    return out


RUNNERS = (run_scope, run_hexapod, run_autocollimator)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report, failed = [], 0
    for (label, cls, cfg), runner in zip(DEVICES, RUNNERS):
        dev = cls(cfg, mode="simulate", alias=label)
        try:
            idn = dev.identify().get("value")
            caps = dev.capabilities()
            run = runner(dev)
            report.append({
                "device": label, "driver_key": cls.driver_key, "family": cls.family,
                "models": list(cls.MODELS), "idn": idn, "capabilities": caps,
                "actions": len(dev.actions()), "result": run,
            })
        except Exception as exc:  # noqa: BLE001
            failed += 1
            report.append({"device": label, "driver_key": cls.driver_key,
                           "error": f"{type(exc).__name__}: {exc}"})
        finally:
            try:
                dev.close()
            except Exception:
                pass

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for r in report:
            if "error" in r:
                print(f"[FAIL] {r['device']:<8} {r['driver_key']:<22} {r['error']}")
                continue
            print(f"[OK]   {r['device']:<8} {r['driver_key']:<22} "
                  f"family={r['family']:<7} caps={len(r['capabilities']):<2} "
                  f"actions={r['actions']:<2} idn={r['idn']}")
            for k, v in r["result"].items():
                print(f"         {k:<12} {v}")
        print(f"\n{len(report) - failed}/{len(report)} devices ok")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
