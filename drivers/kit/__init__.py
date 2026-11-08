"""驱动仓库基础设施：注册表、解析器、契约一致性套件。"""

from .registry import Manifest, Registry, RegistryError, parse_version, satisfies, tree_sha256
from .resolve import (
    Requirement,
    Resolution,
    ResolutionSet,
    ResolveError,
    StationType,
    VersionConflict,
    check_runtime,
    load_lock,
    load_station_type,
    resolve,
)

__all__ = [
    "Manifest",
    "Registry",
    "RegistryError",
    "Requirement",
    "Resolution",
    "ResolutionSet",
    "ResolveError",
    "StationType",
    "VersionConflict",
    "check_runtime",
    "load_lock",
    "load_station_type",
    "parse_version",
    "resolve",
    "satisfies",
    "tree_sha256",
]
