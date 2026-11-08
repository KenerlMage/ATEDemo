"""包注册表与清单校验（L2 基础设施，不含任何厂商库）。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REQUIRED_FIELDS = (
    "name",
    "version",
    "capability",
    "contract",
    "entry",
    "owner",
    "runtime",
)

_VERSION_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?$")


class RegistryError(Exception):
    """清单缺失、字段不全、目录与清单不一致。"""


def parse_version(text: str, *, strict: bool = False) -> tuple[int, int, int]:
    """解析版本。strict=True 用于包版本（必须三段），strict=False 用于约束表达式（允许 1 / 1.2）。"""
    m = _VERSION_RE.match(str(text).strip())
    if not m or (strict and m.group(3) is None):
        raise RegistryError(f"版本号不合规（要求 MAJOR.MINOR.PATCH）: {text!r}")
    return tuple(int(g) if g is not None else 0 for g in m.groups())  # type: ignore[return-value]


def satisfies(version: str, spec: str) -> bool:
    """支持 ^1.2.3 / ~1.2 / >=1.2 <2.0 / 1.4.* / ==1.4.2 / * 。"""
    spec = (spec or "*").strip()
    if spec in ("*", ""):
        return True
    cur = parse_version(version)
    for part in spec.split(","):
        for token in part.split():
            token = token.strip()
            if not token:
                continue
            if token.startswith("^"):
                base = parse_version(token[1:])
                if cur[0] != base[0] or cur < base:
                    return False
            elif token.startswith("~"):
                base = parse_version(token[1:])
                if (cur[0], cur[1]) != (base[0], base[1]) or cur < base:
                    return False
            elif token.startswith(">="):
                if cur < parse_version(token[2:]):
                    return False
            elif token.startswith("<="):
                if cur > parse_version(token[2:]):
                    return False
            elif token.startswith(">"):
                if cur <= parse_version(token[1:]):
                    return False
            elif token.startswith("<"):
                if cur >= parse_version(token[1:]):
                    return False
            elif token.startswith("=="):
                if cur != parse_version(token[2:]):
                    return False
            elif token.endswith(".*"):
                base = parse_version(token[:-2] + ".0")
                if cur[:2] != base[:2]:
                    return False
            else:
                if cur != parse_version(token):
                    return False
    return True


def tree_sha256(path: Path) -> str:
    """目录内容哈希：按相对路径排序后逐文件累加，用于 lock 的完整性校验。"""
    h = hashlib.sha256()
    for f in sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        h.update(f.relative_to(path).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(f.read_bytes())
    return h.hexdigest()


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    capability: str
    contract: str
    entry: str
    owner: str
    runtime: dict
    simulate: bool
    verified_on_hardware: bool
    vendor_deps: tuple[str, ...]
    path: Path
    tree_sha256: str
    raw: dict = field(default_factory=dict, compare=False)

    @property
    def pkg_dir(self) -> Path:
        return self.path / "pkg"

    @property
    def module(self) -> str:
        return self.entry.split(":")[0]

    @property
    def attribute(self) -> str:
        return self.entry.split(":")[1] if ":" in self.entry else "Driver"

    def label(self) -> str:
        return f"{self.name}@{self.version}"


class Registry:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.packages_dir = self.root / "packages"
        self._manifests: list[Manifest] = []

    def load(self) -> list[Manifest]:
        self._manifests = []
        if not self.packages_dir.is_dir():
            raise RegistryError(f"缺少 packages 目录: {self.packages_dir}")
        for manifest_path in sorted(self.packages_dir.glob("*/manifest.json")):
            self._manifests.append(self._parse(manifest_path))
        names = [m.label() for m in self._manifests]
        if len(names) != len(set(names)):
            raise RegistryError(f"存在重复包版本: {names}")
        return self._manifests

    def _parse(self, manifest_path: Path) -> Manifest:
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RegistryError(f"清单不是合法 JSON: {manifest_path}: {exc}") from exc
        missing = [k for k in REQUIRED_FIELDS if k not in raw]
        if missing:
            raise RegistryError(f"{manifest_path} 缺字段: {missing}")
        parse_version(raw["version"], strict=True)
        pkg_dir = manifest_path.parent / "pkg"
        if not pkg_dir.is_dir():
            raise RegistryError(f"{manifest_path} 缺少 pkg/ 目录")
        if ":" not in raw["entry"]:
            raise RegistryError(f"{manifest_path} entry 需为 'module:Class' 形式")
        return Manifest(
            name=raw["name"],
            version=raw["version"],
            capability=raw["capability"],
            contract=str(raw["contract"]),
            entry=raw["entry"],
            owner=raw["owner"],
            runtime=raw.get("runtime", {}),
            simulate=bool(raw.get("simulate", False)),
            verified_on_hardware=bool(raw.get("verified_on_hardware", False)),
            vendor_deps=tuple(raw.get("vendor_deps", [])),
            path=manifest_path.parent,
            tree_sha256=tree_sha256(manifest_path.parent),
            raw=raw,
        )

    def find(self, capability: str, name: str | None = None, version_spec: str = "*") -> list[Manifest]:
        pool = [
            m
            for m in self._manifests
            if m.capability == capability and (name is None or m.name == name) and satisfies(m.version, version_spec)
        ]
        return sorted(pool, key=lambda m: parse_version(m.version), reverse=True)

    def all(self) -> Iterable[Manifest]:
        return tuple(self._manifests)
