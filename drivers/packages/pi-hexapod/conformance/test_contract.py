"""随包发布的契约测试：现场无平台、无硬件时也能自检本包是否符合定位平台契约。

    python packages/pi-hexapod/conformance/test_contract.py

有 pytest 时可直接 `pytest packages/pi-hexapod/conformance`。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PKG_DIR = HERE.parents[1]
REPO_ROOT = HERE.parents[3]
for p in (str(REPO_ROOT), str(PKG_DIR / "pkg")):
    if p not in sys.path:
        sys.path.insert(0, p)

from kit.conformance import run_motion_suite  # noqa: E402
from pi_hexapod import PiHexapod  # noqa: E402


def factory() -> PiHexapod:
    return PiHexapod("tcpip://192.168.10.31:50000", mode="simulate")


def test_contract() -> None:
    checks = run_motion_suite(factory, expect_mode="simulate")
    failed = [c for c in checks if not c.ok]
    assert not failed, "; ".join(f"{c.name}: {c.detail}" for c in failed)


if __name__ == "__main__":
    checks = run_motion_suite(factory, expect_mode="simulate")
    for c in checks:
        print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name}" + (f" — {c.detail}" if c.detail else ""))
    bad = [c for c in checks if not c.ok]
    print(f"\n{len(checks) - len(bad)}/{len(checks)} 通过")
    raise SystemExit(1 if bad else 0)
