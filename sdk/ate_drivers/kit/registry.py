# -*- coding: utf-8 -*-
"""驱动包清单（`driver.json`）：发现 → 校验 → 合并 → **型号派发**

平台侧加载驱动包只需三步（详见 `docs/driver-sdk-guide.md`）：

    manifests = scan_dir("<ATE>/drivers")            # 1. 扫描已安装包
    specs     = merge_specs(BUILTIN_SPECS, manifests) # 2. 与内置规格合并成一张注册表
    spec      = resolve_spec(model, specs)            # 3. 按型号派发（精确匹配，无打分）

**型号派发取代了旧的打分选择**：型号来自数据库设备档案的确定字段，
`resolve_spec()` 只做归一化后的精确匹配；匹配不到就报 `E_NOT_FOUND` 并列出
这张表里所有可用型号，不做"最像的那个"猜测。

兼容性把关全部在 `load_manifest()`：契约 major 不等、清单缺字段、`models` 为空、
接口非网口、entry 不可导入，一律在**加载阶段**拒绝并给出中文原因，
不允许"装上了跑出错"。
"""

from __future__ import annotations

import importlib
import json
import os
import re
from typing import Any, Optional

from ..capabilities import validate
from ..contract import API_VERSION, MANIFEST_SCHEMA, check_compatible
from ..endpoint import (SUPPORTED_BACKENDS, normalize_backend, normalize_interface)
from ..errors import ConfigurationError, ContractMismatch, DriverNotFound

MANIFEST_NAME = "driver.json"
REQUIRED_FIELDS = ("schema", "key", "version", "family", "api", "entry", "models")
OPTIONAL_DEFAULTS = {"label": "", "models": [], "interfaces": ["LAN"],
                     "interface": "LAN", "backends": ["native"], "backend": "native",
                     "capabilities": [],
                     "simulate": True, "min_platform": "", "depends": [],
                     "vendor": "", "signature": "", "source": "local"}

INTERFACES = ("LAN", "SERIAL")  # 支持的连接方式：网口 + 串口
BACKENDS = SUPPORTED_BACKENDS   # 支持的传输后端：原生栈 + VISA


def normalize_model(model: Any) -> str:
    """型号归一化（与本包 `factory.normalize_model` 同规则，避免 kit 依赖运行时层）"""
    return re.sub(r"[^A-Z0-9]", "", str(model or "").upper())


def load_manifest(path: str, platform_api: str = API_VERSION) -> dict:
    """读取并校验一个驱动包清单；返回带 `path` / `dir` 的清单字典"""
    target = os.path.join(path, MANIFEST_NAME) if os.path.isdir(path) else path
    if not os.path.isfile(target):
        raise ConfigurationError(f"找不到驱动包清单：{target}", detail=f"驱动包目录应含 {MANIFEST_NAME}")
    try:
        raw = json.load(open(target, encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigurationError(f"驱动包清单无法解析：{e}", detail=target) from e
    if not isinstance(raw, dict):
        raise ConfigurationError("驱动包清单必须是 JSON 对象", detail=target)
    if raw.get("schema") != MANIFEST_SCHEMA:
        raise ConfigurationError(f"清单 schema 应为 {MANIFEST_SCHEMA}，实际 {raw.get('schema')!r}", detail=target)
    missing = [k for k in REQUIRED_FIELDS if not raw.get(k)]
    if missing:
        raise ConfigurationError(f"清单缺少必填字段: {missing}", detail=target)
    if not re.match(r"^[a-z0-9][a-z0-9._-]*$", str(raw["key"])):
        raise ConfigurationError(f"驱动 key 非法: {raw['key']!r}", detail="应形如 vendor-model")
    if not re.match(r"^\d+\.\d+\.\d+$", str(raw["version"])):
        raise ConfigurationError(f"驱动版本非法: {raw['version']!r}", detail="应为 x.y.z")
    ok, reason = check_compatible(str(raw["api"]), platform_api)
    if not ok:
        raise ContractMismatch(reason, detail=target)
    if ":" not in str(raw["entry"]):
        raise ConfigurationError(f"entry 非法: {raw['entry']!r}", detail="应为 'module:ClassName'")
    listed = raw["models"]
    if not isinstance(listed, list):
        raise ConfigurationError(f"models 必须是数组，实际 {type(listed).__name__}", detail=target)
    if not listed:
        raise ConfigurationError("models 不能为空", detail="型号派发要求显式列出支持的型号，例如 [\"MSO54\"]")
    empty = [m for m in listed if not str(m).strip()]
    if empty:
        raise ConfigurationError(f"models 含空项: {listed!r}", detail=target)
    if len({normalize_model(m) for m in listed}) != len(listed):
        raise ConfigurationError(f"models 内有重复型号: {listed!r}", detail=target)
    declared = raw.get("interfaces")
    if declared is None:
        declared = [raw.get("interface") or "LAN"]
    if isinstance(declared, str):
        declared = [declared]
    if not isinstance(declared, list) or not declared:
        raise ConfigurationError(f"interfaces 必须是数组，实际 {declared!r}",
                                 detail=f"应为 {INTERFACES} 的任意组合，如 [\"LAN\", \"SERIAL\"]")
    kinds = []
    for item in declared:
        kind = normalize_interface(item)
        if not kind:
            raise ConfigurationError(f"interfaces 含非法连接方式: {item!r}",
                                     detail=f"应为 {INTERFACES} 之一")
        if kind not in kinds:
            kinds.append(kind)
    iface = kinds[0]
    declared_backends = raw.get("backends")
    if declared_backends is None:
        declared_backends = [raw.get("backend") or "native"]
    if isinstance(declared_backends, str):
        declared_backends = [declared_backends]
    if not isinstance(declared_backends, list) or not declared_backends:
        raise ConfigurationError(f"backends 必须是数组，实际 {declared_backends!r}",
                                 detail=f"应为 {BACKENDS} 的任意组合，如 [\"native\", \"visa\"]")
    backends = []
    for item in declared_backends:
        backend = normalize_backend(item)
        if backend not in BACKENDS:
            raise ConfigurationError(f"backends 含非法传输后端: {item!r}",
                                     detail=f"应为 {BACKENDS} 之一（auto 不是可声明的能力）")
        if backend not in backends:
            backends.append(backend)
    manifest = dict(OPTIONAL_DEFAULTS)
    manifest.update(raw)
    manifest["models"] = [str(m) for m in listed]
    manifest["interfaces"] = kinds
    manifest["interface"] = iface
    manifest["backends"] = backends
    manifest["backend"] = backends[0]
    manifest["path"] = target
    manifest["dir"] = os.path.dirname(os.path.abspath(target))
    manifest["compatible"] = reason
    missing_caps, unknown_caps, _ = validate(manifest.get("capabilities") or [], str(manifest["family"]))
    manifest["missing_capabilities"] = missing_caps
    manifest["unknown_capabilities"] = unknown_caps
    return manifest


def scan_dir(root: str, max_depth: int = 3) -> list[dict]:
    """扫描目录下所有驱动包清单（`<root>/<key>/driver.json`）"""
    found: list[dict] = []
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return found
    base_depth = root.rstrip(os.sep).count(os.sep)
    for cur, dirs, files in os.walk(root):
        if cur.count(os.sep) - base_depth >= max_depth:
            dirs[:] = []
        if MANIFEST_NAME in files:
            try:
                found.append(load_manifest(os.path.join(cur, MANIFEST_NAME)))
            except Exception as e:  # 单个包坏了不影响其他包（降级而非中断）
                found.append({"key": os.path.basename(cur), "error": str(e),
                              "path": os.path.join(cur, MANIFEST_NAME), "dir": cur,
                              "loadable": False})
    for m in found:
        m.setdefault("loadable", "error" not in m)
    return found


def resolve_entry(manifest: dict, base_dir: Optional[str] = None):
    """按 entry 导入驱动类（`"pkg.driver:TekMso5Scope"`）"""
    module_name, _, class_name = str(manifest["entry"]).partition(":")
    import sys

    base = os.path.abspath(base_dir or manifest.get("dir") or ".")
    if base not in sys.path:
        sys.path.insert(0, base)
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise DriverNotFound(f"驱动模块无法导入：{module_name}（{e}）", detail=base) from e
    cls = getattr(module, class_name, None)
    if cls is None:
        raise DriverNotFound(f"驱动类不存在：{module_name}:{class_name}")
    return cls


def spec_models(spec: dict) -> list[str]:
    """规格项声明的型号（兼容旧字段 `match.model`，便于老包平滑迁移）"""
    models = list(spec.get("models") or ())
    if not models:
        models = list((spec.get("match") or {}).get("model") or ())
    return [str(m) for m in models]


def available_models(specs: list[dict]) -> list[str]:
    """这张注册表里可派发的全部型号（去重，保留声明顺序）"""
    seen, out = set(), []
    for spec in specs:
        if not spec.get("loadable", True):
            continue
        for m in spec_models(spec):
            key = normalize_model(m)
            if key and key not in seen:
                seen.add(key)
                out.append(m)
    return out


def resolve_spec(model: Any, specs: list[dict]) -> dict:
    """按型号派发规格项（**精确匹配，无打分、无兜底**）

    型号来自数据库设备档案；查不到就报错并列出可用型号。
    """
    want = normalize_model(model)
    if not want:
        raise DriverNotFound("型号为空，无法派发驱动", detail="请检查设备档案的 model 字段")
    for spec in specs:
        if not spec.get("loadable", True):
            continue
        for m in spec_models(spec):
            if normalize_model(m) == want:
                out = dict(spec)
                out["model"] = str(m)
                out["matched_by"] = "model"
                return out
    known = ", ".join(available_models(specs)) or "(这张表里没有任何型号)"
    raise DriverNotFound(
        f"未登记的型号：{model!r}",
        detail=f"可用型号：{known} —— 型号派发不做模糊匹配，请把档案型号写成其中之一，"
               f"或登记新型号驱动包")


def model_conflicts(specs: list[dict]) -> list[dict]:
    """型号冲突检测：同一型号被两个包声明（装载多个驱动包时必须为空）"""
    seen: dict = {}
    conflicts = []
    for spec in specs:
        if not spec.get("loadable", True):
            continue
        for m in spec_models(spec):
            key = normalize_model(m)
            prev = seen.get(key)
            if prev is not None and prev.get("key") != spec.get("key"):
                conflicts.append({"model": m, "a": prev.get("key"), "b": spec.get("key")})
            else:
                seen[key] = spec
    return conflicts


def merge_specs(builtin_specs: list[dict], manifests: list[dict]) -> list[dict]:
    """内置规格 + 已装驱动包清单合并成一张注册表

    同 key 冲突时**已装包优先**（现场侧载要能覆盖内置版本），但会记 `overridden` 供审计。
    """
    merged: list[dict] = []
    by_key: dict[str, dict] = {}
    for spec in builtin_specs:
        item = dict(spec, source="builtin")
        merged.append(item)
        by_key[item["key"]] = item
    for m in manifests:
        if not m.get("loadable", True):
            continue
        item = {
            "key": m["key"],
            "family": m["family"],
            "label": m.get("label") or m["key"],
            "entry": m["entry"],
            "version": m["version"],
            "api": m["api"],
            "models": spec_models(m),
            "interface": str(m.get("interface") or "LAN").upper(),
            "capabilities": m.get("capabilities") or [],
            "simulate": bool(m.get("simulate", True)),
            "manifest": m,
            "source": m.get("source", "local"),
        }
        old = by_key.get(item["key"])
        if old is not None and old.get("source") == "builtin":
            item["overridden"] = old.get("version", "")
            merged = [x for x in merged if x is not old]
            for x in merged:
                if x["key"] == item["key"]:
                    x["overridden"] = True
        by_key[item["key"]] = item
        merged.append(item)
    return merged


def describe_registry(specs: list[dict]) -> list[dict]:
    """注册表摘要（设备档案型号下拉框 / 前端"驱动库"页 / 排查用）"""
    rows = [
        {
            "key": s["key"],
            "family": s.get("family", ""),
            "label": s.get("label", ""),
            "version": s.get("version", "-"),
            "api": s.get("api", "-"),
            "vendor": s.get("vendor", ""),
            "interface": str(s.get("interface") or "LAN").upper(),
            "models": spec_models(s),
            "source": s.get("source", "builtin"),
            "capabilities": s.get("capabilities") or [],
        }
        for s in specs
    ]
    return rows


__all__ = ["BACKENDS", "MANIFEST_NAME", "available_models", "describe_registry", "load_manifest",
           "merge_specs", "model_conflicts", "normalize_model", "resolve_entry",
           "resolve_spec", "scan_dir", "spec_models"]
