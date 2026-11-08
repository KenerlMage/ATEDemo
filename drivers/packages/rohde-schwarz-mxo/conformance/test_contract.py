"""随包发布的契约测试：现场无平台、无硬件时也能自检本包是否符合契约。

    python packages/rohde-schwarz-mxo/conformance/test_contract.py

有 pytest 时可直接 `pytest packages/rohde-schwarz-mxo/conformance`。
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

from kit.conformance import run_suite  # noqa: E402
from rs_mxo import RsMxoScope  # noqa: E402


def factory() -> RsMxoScope:
    return RsMxoScope("SIM::MXO44::DEMO", mode="simulate")


def test_contract() -> None:
    checks = run_suite(factory, expect_mode="simulate")
    failed = [c for c in checks if not c.ok]
    assert not failed, "; ".join(f"{c.name}: {c.detail}" for c in failed)


if __name__ == "__main__":
    checks = run_suite(factory, expect_mode="simulate")
    for c in checks:
        print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name}" + (f" — {c.detail}" if c.detail else ""))
    bad = [c for c in checks if not c.ok]
    print(f"\n{len(checks) - len(bad)}/{len(checks)} 通过")
    raise SystemExit(1 if bad else 0)
