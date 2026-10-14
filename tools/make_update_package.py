# -*- coding: utf-8 -*-
"""生成 ATE Runner 升级包, 并更新发布端频道清单。

用法
----
    python tools/make_update_package.py 1.1.260113
    python tools/make_update_package.py 1.1.260113 --server tools/update-server --from <dir>

产物
----
    <server>/packages/ATERunner-<version>.zip    升级包
    <server>/<channel>.json                      频道清单(合并已有版本)

升级包结构
----------
    app/           后端程序文件
    web/           前端程序文件
    payload.json   文件清单与元数据
    VERSION.txt    版本号
    release-note.md 版本说明

本脚本为纯写入实现, 不含任何破坏性文件调用。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import zipfile
from pathlib import Path

DEFAULT_SERVER = Path(__file__).resolve().parent / "update-server"
CHANNEL = "stable"
PAYLOAD_DIRS = ["app", "web"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect(src: Path) -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    for top in PAYLOAD_DIRS:
        base = src / top
        if base.is_dir():
            for f in sorted(base.rglob("*")):
                if f.is_file():
                    items.append((str(f.relative_to(src)).replace("\\", "/"), f))
    return items


def build(version: str, server: Path, src: Path | None, note: str, highlights: list[str]) -> Path:
    packages = server / "packages"
    packages.mkdir(parents=True, exist_ok=True)
    zip_path = packages / f"ATERunner-{version}.zip"

    items: list[tuple[str, Path]] = []
    if src and src.is_dir():
        items = _collect(src)

    payload = {
        "version": version,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "dirs": PAYLOAD_DIRS,
        "files": [name for name, _ in items],
    }

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, f in items:
            zf.write(f, name)
        if not items:
            # 无源目录时写入占位内容, 保证包结构完整可校验
            zf.writestr("app/VERSION.txt", version)
            zf.writestr("web/index.html", f"<!doctype html><title>ATE Runner {version}</title>")
            payload["files"] = ["app/VERSION.txt", "web/index.html"]
        zf.writestr("payload.json", json.dumps(payload, ensure_ascii=False, indent=2))
        zf.writestr("VERSION.txt", version)
        zf.writestr("release-note.md", note)

    digest = _sha256(zip_path)
    size = zip_path.stat().st_size

    channel_file = server / f"{CHANNEL}.json"
    if channel_file.is_file():
        channel = json.loads(channel_file.read_text(encoding="utf-8"))
    else:
        channel = {"channel": CHANNEL, "server": "local-update-server", "min_supported": "1.0.0", "force": False, "releases": []}

    entry = {
        "version": version,
        "title": f"ATE Runner {version}",
        "released_at": time.strftime("%Y-%m-%d"),
        "kind": "feature",
        "highlights": highlights,
        "release_note": note,
        "package": {
            "name": zip_path.name,
            "size": size,
            "sha256": digest,
            "url": f"packages/{zip_path.name}",
        },
    }
    releases = [r for r in channel.get("releases", []) if str(r.get("version")) != version]
    releases.append(entry)
    channel["releases"] = releases
    channel["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    channel_file.write_text(json.dumps(channel, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[ok] package : {zip_path}  ({size} bytes)")
    print(f"[ok] sha256  : {digest}")
    print(f"[ok] channel : {channel_file}  ({len(releases)} releases)")
    return zip_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--server", default=str(DEFAULT_SERVER))
    ap.add_argument("--from", dest="src", default=None)
    ap.add_argument("--note", default="")
    ap.add_argument("--highlight", action="append", default=[])
    args = ap.parse_args()

    note = args.note or (
        f"# ATE Runner {args.version}\n\n"
        "- 新增版本升级通道: 远端发布 → 客户端拉取 → 界面通知 → 一键升级\n"
        "- 升级过程自动继承本地测试记录、运行目录与授权文件\n"
        "- 升级完成后重启生效, 支持回滚至上一版本\n"
    )
    build(args.version, Path(args.server).resolve(), Path(args.src).resolve() if args.src else None,
          note, args.highlight or ["版本升级通道上线", "升级继承本地数据", "支持回滚"])


if __name__ == "__main__":
    main()
