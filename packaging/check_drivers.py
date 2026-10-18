# -*- coding: utf-8 -*-
"""打包/升级前的驱动门禁。

用途：在所有驱动随 Runner 包发布的前提下，确认"包里每个设备类型都能实例化、
动作可解析、仿真能握手"，并交叉核对现场注册库里用到的类型是否都还在包内。

运行：
    python packaging/check_drivers.py            # 以仓库根为工作目录
    python packaging/check_drivers.py --json     # 机器可读输出

退出码：0 全部通过；1 有失败项（打包脚本据此中止）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def use_backend(path):
    """把门禁指向另一个后端目录（打包后校验 payload 下的 app）。"""
    global BACKEND
    if not path:
        return
    cand = Path(path).resolve()
    BACKEND = cand
    if str(cand) not in sys.path:
        sys.path.insert(0, str(cand))


def _fail(msg: str) -> None:
    print("  [FAIL] " + msg)


def check_types() -> tuple[list[dict], list[str]]:
    from drivers.factory import DRIVER_TYPES, get_driver

    rows: list[dict] = []
    errors: list[str] = []

    for spec in DRIVER_TYPES:
        key = spec["key"]
        row = {"key": key, "family": spec.get("family", ""), "label": spec.get("label", ""),
               "ok": False, "actions": 0, "idn": "", "error": ""}
        try:
            dev = {"device_id": "GATE-01", "device_type": key, "category": spec.get("label", ""),
                   "model": "GATE", "interface": "LAN", "host": "127.0.0.1", "port": 5025}
            drv = get_driver(dev, mode="simulate", alias="gate")
            try:
                actions = drv.actions()
                missing = [a for a in actions if not hasattr(drv, drv.ACTIONS.get(a, a))]
                if not actions:
                    raise RuntimeError("actions() 为空")
                if missing:
                    raise RuntimeError("动作无实现: " + ", ".join(missing[:3]))
                idn = drv.identify()
                idn = idn.get("value") if isinstance(idn, dict) else idn
                row.update(ok=True, actions=len(actions), idn=str(idn)[:48])
            finally:
                try:
                    drv.close()
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
            errors.append(f"{key} -> {row['error']}")
        rows.append(row)
    return rows, errors


def check_registry(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """核对已注册测试台里用到的 device_type 是否都还在包内。"""
    reg = BACKEND / "testresource" / "testbenches.json"
    known = {r["key"] for r in rows}
    out: list[dict] = []
    errors: list[str] = []
    if not reg.is_file():
        return out, errors
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return out, [f"注册库无法解析: {type(exc).__name__}: {exc}"]

    benches = data.get("testbenches") or data.get("benches") or []
    for bench in benches:
        bid = bench.get("bench_id") or bench.get("id") or "?"
        for dev in bench.get("devices") or []:
            dt = str(dev.get("device_type") or "").strip()
            did = dev.get("device_id") or "?"
            ok = bool(dt) and dt in known
            out.append({"bench": bid, "device": did, "device_type": dt, "ok": ok})
            if not ok:
                errors.append(f"{bid}/{did} 的 device_type={dt or '(空)'} 不在包内")
    return out, errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--backend", default="", help="backend dir; default repo/backend")
    args = ap.parse_args()
    use_backend(args.backend)

    rows, errs = check_types()
    reg_rows, reg_errs = check_registry(rows)
    all_errs = errs + reg_errs

    if args.json:
        print(json.dumps({"types": rows, "registry": reg_rows, "errors": all_errs},
                         ensure_ascii=False, indent=2))
    else:
        print("driver gate: %d types" % len(rows))
        for r in rows:
            flag = "OK  " if r["ok"] else "FAIL"
            print(f"  [{flag}] {r['key']:<18} actions={r['actions']:<3} {r['idn'] or r['error']}")
        if reg_rows:
            bad = [r for r in reg_rows if not r["ok"]]
            print("registry: %d devices, %d unresolved" % (len(reg_rows), len(bad)))
            for r in bad:
                print(f"  [FAIL] {r['bench']}/{r['device']} device_type={r['device_type'] or '(空)'}")
        if all_errs:
            print("gate FAILED: %d issue(s)" % len(all_errs))
        else:
            print("gate PASSED")

    return 0 if not all_errs else 1


if __name__ == "__main__":
    raise SystemExit(main())
