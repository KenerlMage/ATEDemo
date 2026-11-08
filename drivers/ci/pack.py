"""出制品：校验清单 → 打包 zip → 算 sha256 → 写制品仓索引。

    python ci/pack.py --root .                     # 产出 dist/repo/
    python ci/pack.py --root . --verify            # 校验已装包与索引是否一致
    python ci/pack.py --root . --boundaries        # 检查厂商库是否泄漏到契约/基础设施层

产物是「平台制品仓」而不是离线介质：
- `dist/repo/<name>-<version>.zip` + `dist/repo/index.json`（含 tier / channel / distribution）；
- 台架侧由 kit.repo 按 bench.lock.json 拉取与切换版本，不手工拷文件、不联网装包；
- 发布通道 stable / canary 由 --channel 指定，测台可按 channel 取不同范围的版本。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kit.registry import Registry, RegistryError, tree_sha256  # noqa: E402

VENDOR_HINTS = ("RsInstrument", "pyvisa", "pypylon", "pylibfranka", "import visa")


def build(root: Path, out_dir: Path, channel: str = "stable") -> dict:
    reg = Registry(root)
    reg.load()
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for m in reg.all():
        zip_path = out_dir / f"{m.name}-{m.version}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for f in sorted(m.path.rglob("*")):
                if not f.is_file() or "__pycache__" in f.parts:
                    continue
                z.write(f, (Path(m.name) / f.relative_to(m.path)).as_posix())
        digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        entries.append(
            {
                "name": m.name,
                "version": m.version,
                "tier": m.tier,
                "channel": channel,
                "distribution": m.distribution,
                "capability": m.capability,
                "contract": m.contract,
                "owner": m.owner,
                "simulate": m.simulate,
                "verified_on_hardware": m.verified_on_hardware,
                "tree_sha256": m.tree_sha256,
                "zip_sha256": digest,
                "zip": zip_path.name,
                "size": zip_path.stat().st_size,
            }
        )
        print(f"  built {zip_path.name:<32} {zip_path.stat().st_size:>8} B  sha256={digest[:16]}…")
    index = {
        "schema": 1,
        "channel": channel,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "packages": entries,
    }
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"index.json: {len(entries)} 个包 → {out_dir / 'index.json'}")
    print("台架侧用法：python -c \"from kit.repo import Repo; Repo('dist/repo').apply(Repo('dist/repo').plan('benches/X/bench.lock.json', 'dist/site'), dest='dist/site')\"")
    return index


def verify(root: Path, index_path: Path) -> int:
    reg = Registry(root)
    reg.load()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    installed = {(m.name, m.version): m for m in reg.all()}
    failures = 0
    for item in index["packages"]:
        key = (item["name"], item["version"])
        m = installed.get(key)
        if m is None:
            print(f"  MISSING  {item['name']}@{item['version']}")
            failures += 1
            continue
        if m.tree_sha256 != item["tree_sha256"]:
            print(f"  DRIFT    {item['name']}@{item['version']} 目录内容与索引不一致")
            failures += 1
        else:
            print(f"  OK       {key[0]}@{key[1]}")
    return failures


def boundaries(root: Path) -> int:
    """厂商库只允许出现在 packages/*/pkg/**；契约与基础设施层必须干净。"""
    offenders = []
    for layer in ("contracts", "kit", "runtime"):
        for f in (root / layer).rglob("*.py"):
            text = f.read_text(encoding="utf-8", errors="ignore")
            for hint in VENDOR_HINTS:
                if re.search(rf"\b{re.escape(hint)}\b", text):
                    offenders.append(f"{f.relative_to(root)} → {hint}")
    if offenders:
        print("厂商库边界违规：")
        for o in offenders:
            print("  " + o)
        return len(offenders)
    print("边界检查通过：contracts / kit / runtime 内无厂商库引用")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="dist/repo")
    ap.add_argument("--channel", default="stable", choices=["stable", "canary"])
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--boundaries", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    out_dir = (root / a.out).resolve()

    if a.boundaries:
        return 1 if boundaries(root) else 0
    if a.verify:
        return 1 if verify(root, out_dir / "index.json") else 0
    try:
        build(root, out_dir, a.channel)
    except RegistryError as exc:
        print(f"清单校验失败: {exc}")
        return 2
    return 1 if boundaries(root) else 0


if __name__ == "__main__":
    raise SystemExit(main())
