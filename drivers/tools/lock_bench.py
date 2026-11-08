"""为某台测试台生成 / 刷新 bench.lock.json。

用法：
    python tools/lock_bench.py --root . --station station-types/scope-bench.json \
        --bench BENCH-DEMO --runner 1.7.3

lock 是部署契约：现场安装与回滚只改这个文件，不重装平台。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kit.registry import Registry  # noqa: E402
from kit.resolve import load_station_type, resolve  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--station", required=True)
    ap.add_argument("--bench", required=True)
    ap.add_argument("--runner", default="1.7.3")
    ap.add_argument("--repo", default="dist/repo", help="平台制品仓路径（写入 lock，供现场按 lock 拉取）")
    ap.add_argument("--channel", default="stable", choices=["stable", "canary"])
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    root = Path(a.root).resolve()
    reg = Registry(root)
    reg.load()
    station = load_station_type(a.station)
    rs = resolve(station, reg, contract_version="1.0")
    lock = rs.to_lock(bench=a.bench, runner=a.runner)
    lock["distribution"] = {"mode": "platform-repo", "repo": a.repo, "channel": a.channel}

    out = Path(a.out) if a.out else root / "benches" / a.bench / "bench.lock.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(lock, ensure_ascii=False, indent=2))
    print(f"\n已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
