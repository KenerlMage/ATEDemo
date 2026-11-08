"""平台制品仓客户端（取代"离线介质导入"）。

分工
----
- 代码仓（本仓库）：契约、包实现、conformance、CI 脚本。走 PR + 门禁，有评审。
- 制品仓（`dist/repo/`）：打包产物 zip + `index.json`。由 CI 发布，台架只读拉取。

管理颗粒度
----------
最小单元是「包 + 版本」，不是整仓、也不是文件级：
- 拉取以包为单位（一个 zip 一个版本一个目录）；
- 生效以台架为单位（`bench.lock.json` 决定这台装哪个版本）；
- 回滚以版本为单位（把 lock 指回旧版本再 update，不动其它包）。

三个动作
--------
- 下载 `fetch(name, version)`：读索引 → 校验 zip 的 sha256 → 解压 → 校验目录 tree_sha256。
- 推送 `publish(zip)`：CI 侧发布条目；台架侧不推送，只拉取（单向），避免现场回写制品仓。
- 更新 `plan(lock)` / `apply(plan)`：对比 lock 与本机已装版本，产出 install / switch / ok，
  再按计划拉取。回滚 = 把 lock 里的版本改回旧值，再跑一次 update。

设计取舍
--------
- 不装依赖、不编译、不联网装包：制品是"可解压即用"的目录，vendor 目录随包。
- 校验失败一律拒绝，不做"降级使用本地缓存"：现场最贵的事故是装了个来路不明的包。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from kit.registry import tree_sha256


class RepoError(Exception):
    """索引缺失、条目不存在、哈希不一致、目标目录冲突。"""


@dataclass(frozen=True)
class Entry:
    name: str
    version: str
    tier: str
    channel: str
    capability: str
    contract: str
    owner: str
    zip: str
    zip_sha256: str
    tree_sha256: str
    size: int = 0

    def label(self) -> str:
        return f"{self.name}@{self.version}"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _local_version(package_dir: Path) -> str | None:
    manifest = package_dir / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8"))["version"])
    except Exception:
        return None


class Repo:
    """只读为主：fetch / plan / apply 面向台架；publish 面向 CI。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # -- 索引 -----------------------------------------------------------
    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    def index(self) -> list[Entry]:
        if not self.index_path.is_file():
            raise RepoError(f"制品仓缺少索引: {self.index_path}")
        raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        entries = []
        for item in raw.get("packages", []):
            entries.append(
                Entry(
                    name=item["name"],
                    version=item["version"],
                    tier=item.get("tier", "shared"),
                    channel=item.get("channel", "stable"),
                    capability=item.get("capability", ""),
                    contract=str(item.get("contract", "")),
                    owner=item.get("owner", ""),
                    zip=item["zip"],
                    zip_sha256=item["zip_sha256"],
                    tree_sha256=item["tree_sha256"],
                    size=int(item.get("size", 0)),
                )
            )
        return entries

    def candidates(
        self, *, name: str | None = None, channel: str | None = "stable", tier: str | None = None
    ) -> list[Entry]:
        pool = [
            e
            for e in self.index()
            if (name is None or e.name == name)
            and (channel is None or e.channel == channel)
            and (tier is None or e.tier == tier)
        ]
        return sorted(pool, key=lambda e: e.version, reverse=True)

    def resolve_entry(self, name: str, version: str | None = None, *, channel: str | None = "stable") -> Entry:
        pool = self.candidates(name=name, channel=channel)
        if not pool:
            raise RepoError(f"制品仓无可用条目: {name} channel={channel}")
        if version is None:
            return pool[0]
        for e in pool:
            if e.version == version:
                return e
        raise RepoError(f"制品仓无 {name}@{version}（channel={channel}）")

    # -- 下载 -----------------------------------------------------------
    def fetch(
        self,
        name: str,
        version: str | None = None,
        *,
        dest: str | Path,
        channel: str | None = "stable",
        force: bool = False,
    ) -> Path:
        entry = self.resolve_entry(name, version, channel=channel)
        zpath = self.root / entry.zip
        if not zpath.is_file():
            raise RepoError(f"索引指向的包体不存在: {zpath}")

        digest = _sha256_file(zpath)
        if digest != entry.zip_sha256:
            raise RepoError(f"{entry.label()} 包体 sha256 与索引不一致: {digest[:16]}…")

        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        staging = dest / ".staging" / f"{entry.name}-{entry.version}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(staging)

        src = staging / entry.name
        if not src.is_dir():
            raise RepoError(f"{entry.label()} 包内缺少顶层目录 {entry.name}/")
        if tree_sha256(src) != entry.tree_sha256:
            raise RepoError(f"{entry.label()} 解压后目录哈希与索引不一致（内容被改动）")

        target = dest / entry.name
        if target.exists():
            local = _local_version(target)
            if local == entry.version:
                shutil.rmtree(staging, ignore_errors=True)
                return target
            if not force:
                raise RepoError(f"{target} 已存在版本 {local}，切换版本需 force=True")
            shutil.rmtree(target)
        shutil.move(str(src), str(target))
        shutil.rmtree(staging, ignore_errors=True)
        return target

    # -- 更新计划 -------------------------------------------------------
    def plan(self, lock_path: str | Path, installed_root: str | Path) -> list[dict]:
        lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
        installed_root = Path(installed_root)
        known = {(e.name, e.version) for e in self.index()}
        out: list[dict] = []
        for item in lock.get("resolved", []):
            name, version = item["package"], item["version"]
            target_dir = installed_root / name
            local = _local_version(target_dir) if target_dir.is_dir() else None
            if local is None:
                action = "install"
            elif local == version:
                action = "ok"
            else:
                action = "switch"
            out.append(
                {
                    "package": name,
                    "capability": item.get("capability"),
                    "target": version,
                    "installed": local,
                    "action": action,
                    "in_repo": (name, version) in known,
                }
            )
        return out

    def apply(self, plan: Sequence[dict], *, dest: str | Path, channel: str | None = "stable") -> list[str]:
        done: list[str] = []
        for row in plan:
            if row["action"] == "ok":
                continue
            if not row["in_repo"]:
                raise RepoError(f"{row['package']}@{row['target']} 不在制品仓，拒绝更新")
            self.fetch(row["package"], row["target"], dest=dest, channel=channel, force=True)
            done.append(f"{row['package']}@{row['target']}")
        return done

    # -- 发布（CI 侧）---------------------------------------------------
    def publish(self, zip_path: str | Path, *, channel: str = "stable") -> Entry:
        zip_path = Path(zip_path)
        if not zip_path.is_file():
            raise RepoError(f"待发布包体不存在: {zip_path}")
        with zipfile.ZipFile(zip_path) as z:
            top = {Path(n).parts[0] for n in z.namelist() if n.strip("/")}
            if len(top) != 1:
                raise RepoError(f"包体应只有一个顶层目录: {sorted(top)}")
            name = top.pop()
            staging = self.root / ".publish" / name
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True, exist_ok=True)
            z.extractall(staging)
        manifest = json.loads((staging / name / "manifest.json").read_text(encoding="utf-8"))
        entry = Entry(
            name=manifest["name"],
            version=manifest["version"],
            tier=manifest.get("tier", "shared"),
            channel=channel,
            capability=manifest.get("capability", ""),
            contract=str(manifest.get("contract", "")),
            owner=manifest.get("owner", ""),
            zip=zip_path.name,
            zip_sha256=_sha256_file(zip_path),
            tree_sha256=tree_sha256(staging / name),
            size=zip_path.stat().st_size,
        )
        shutil.rmtree(staging, ignore_errors=True)

        raw = {"schema": 1, "generated_at": "", "packages": []}
        if self.index_path.is_file():
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        rows: list[dict] = [
            r for r in raw.get("packages", []) if not (r["name"] == entry.name and r["version"] == entry.version)
        ]
        rows.append(
            {
                "name": entry.name,
                "version": entry.version,
                "tier": entry.tier,
                "channel": entry.channel,
                "capability": entry.capability,
                "contract": entry.contract,
                "owner": entry.owner,
                "zip": entry.zip,
                "zip_sha256": entry.zip_sha256,
                "tree_sha256": entry.tree_sha256,
                "size": entry.size,
            }
        )
        raw["packages"] = sorted(rows, key=lambda r: (r["name"], r["version"]))
        self.index_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        return entry
