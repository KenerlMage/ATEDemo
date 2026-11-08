"""台型清单与 lock 解析（L2 基础设施）。

职责：把「台型声明需要什么能力」+「这台机器锁定哪个版本的包」翻译成可加载的包集合。
不做的事：不建连接、不读设备档案、不碰硬件。
"""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .registry import Manifest, Registry, RegistryError, parse_version, satisfies


class ResolveError(Exception):
    """解析失败：缺包、版本不满足、契约不兼容、lock 与现场不一致。"""


class VersionConflict(ResolveError):
    """同一进程内同一能力被要求加载两个不同版本。"""


@dataclass(frozen=True)
class Requirement:
    capability: str
    package: str | None = None
    version: str = "*"
    optional: bool = False
    channel_map: str | None = None


@dataclass(frozen=True)
class StationType:
    name: str
    requirements: tuple[Requirement, ...]
    path: Path


@dataclass(frozen=True)
class Resolution:
    capability: str
    manifest: Manifest

    @property
    def label(self) -> str:
        return self.manifest.label()


def load_station_type(path: str | Path) -> StationType:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        try:
            import yaml  # 可选依赖
        except ImportError as exc:
            raise ResolveError(f"读取 {p.name} 需要 PyYAML；请改用 .json 台型清单") from exc
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    if "station_type" not in raw or "requires" not in raw:
        raise ResolveError(f"{p.name} 需包含 station_type 与 requires 字段")
    reqs = []
    for item in raw["requires"]:
        if "capability" not in item:
            raise ResolveError(f"{p.name} 的 requires 条目缺 capability: {item}")
        reqs.append(
            Requirement(
                capability=item["capability"],
                package=item.get("package"),
                version=item.get("version", "*"),
                optional=bool(item.get("optional", False)),
                channel_map=item.get("channel_map"),
            )
        )
    return StationType(name=raw["station_type"], requirements=tuple(reqs), path=p)


def load_lock(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise ResolveError(f"缺少 lock 文件: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


_LOADED: dict[str, tuple[str, str]] = {}  # capability -> (package, version)


class ResolutionSet:
    def __init__(self, station: StationType, resolutions: Mapping[str, Resolution]) -> None:
        self.station = station
        self._resolutions = dict(resolutions)

    def __iter__(self):
        return iter(self._resolutions.values())

    def get(self, capability: str) -> Resolution:
        if capability not in self._resolutions:
            raise ResolveError(f"台型 {self.station.name} 未声明能力: {capability}")
        return self._resolutions[capability]

    def load(self, capability: str) -> type:
        """按解析结果导入驱动类。同进程内同一能力只允许一个版本。"""
        res = self.get(capability)
        m = res.manifest
        prev = _LOADED.get(capability)
        if prev is not None and prev != (m.name, m.version):
            raise VersionConflict(
                f"同一进程已加载 {capability} 的 {prev[0]}@{prev[1]}，"
                f"本次要求 {m.label()}；并发多台架请使用多进程隔离"
            )
        pkg_dir = str(m.pkg_dir)
        if pkg_dir not in sys.path:
            sys.path.insert(0, pkg_dir)
        module = importlib.import_module(m.module)
        cls = getattr(module, m.attribute)
        _LOADED[capability] = (m.name, m.version)
        return cls

    def to_lock(self, bench: str, runner: str) -> dict:
        return {
            "bench": bench,
            "station_type": self.station.name,
            "runner": runner,
            "resolved": [
                {
                    "capability": r.capability,
                    "package": r.manifest.name,
                    "version": r.manifest.version,
                    "tree_sha256": r.manifest.tree_sha256,
                }
                for r in sorted(self._resolutions.values(), key=lambda r: r.capability)
            ],
            "locked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }


def resolve(
    station: StationType,
    registry: Registry,
    lock: Mapping | None = None,
    *,
    contract_version: str | None = None,
) -> ResolutionSet:
    """按台型声明解析包；给定 lock 时严格按 lock 校验，不允许漂移到最新版。"""
    if not registry.all():
        registry.load()

    locked: dict[str, dict] = {}
    if lock is not None:
        if lock.get("station_type") and lock["station_type"] != station.name:
            raise ResolveError(f"lock 属于台型 {lock['station_type']}，与 {station.name} 不符")
        for item in lock.get("resolved", []):
            locked[item["capability"]] = item

    out: dict[str, Resolution] = {}
    for req in station.requirements:
        candidates = registry.find(req.capability, req.package, req.version)
        if not candidates:
            if req.optional:
                continue
            raise ResolveError(
                f"没有包满足 {req.capability} {req.package or '*'} {req.version}；"
                f"已装包: {[m.label() for m in registry.all()]}"
            )

        if lock is not None:
            item = locked.get(req.capability)
            if item is None:
                if req.optional:
                    continue
                raise ResolveError(f"lock 未包含必需能力 {req.capability}，需重新锁定")
            match = next((m for m in candidates if m.name == item["package"] and m.version == item["version"]), None)
            if match is None:
                raise ResolveError(
                    f"lock 要求 {item['package']}@{item['version']}，但本机未安装或版本不满足声明"
                )
            if item.get("tree_sha256") and item["tree_sha256"] != match.tree_sha256:
                raise ResolveError(
                    f"{match.label()} 目录内容与 lock 记录不一致（完整性校验失败），"
                    f"请重新导入该包或重新锁定"
                )
            chosen = match
        else:
            chosen = candidates[0]

        if contract_version and not satisfies(contract_version, chosen.contract):
            raise ResolveError(
                f"{chosen.label()} 实现契约 {chosen.contract}，平台契约为 {contract_version}，不兼容"
            )
        out[req.capability] = Resolution(capability=req.capability, manifest=chosen)
    return ResolutionSet(station, out)


def check_runtime(manifest: Manifest, runner_version: str, python_version: str) -> None:
    """安装/启动时校验：平台与解释器版本是否满足包声明。"""
    runner_spec = manifest.runtime.get("runner")
    if runner_spec and not satisfies(runner_version, runner_spec):
        raise ResolveError(f"{manifest.label()} 要求 Runner {runner_spec}，当前 {runner_version}")
    py_spec = manifest.runtime.get("python")
    if py_spec and not satisfies(python_version, py_spec):
        raise ResolveError(f"{manifest.label()} 要求 Python {py_spec}，当前 {python_version}")


__all__ = [
    "Requirement",
    "ResolveError",
    "Resolution",
    "ResolutionSet",
    "StationType",
    "VersionConflict",
    "check_runtime",
    "load_lock",
    "load_station_type",
    "parse_version",
    "resolve",
]
