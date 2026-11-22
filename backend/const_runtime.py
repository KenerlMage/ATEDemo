# -*- coding: utf-8 -*-
"""Runner 机器常数运行时：解析 / 模型解析 / 范围校验 / XML 写入与清除。

对应需求：SR02-AR01（TPS 机器常数字段与解析方案）、SR02-AR02（写入与清除）、
SR02-AR03（范围校验）。

设计约定
--------
* TPS 的 ``machine_constants`` 段支持三种写法：数组、``{"bindings": [...]}``
  对象、整段缺失（按空集合处理，不报错）；条目缺 ``pn`` / ``model_key`` 时
  抛 ``ValueError`` 并带 1 起的条目序号。
* 绑定来源 ``source``：``default``（模型默认值）、``version``（指定版本的
  结构与已归档值）、``sn``（按 SN 的标定值，调用方通过 ``values`` 覆盖）。
* 模型库 ``catalog`` 形如 ``{"models": [{pn, model_key, name, version, fields,
  values}]}``；找不到的绑定进入 ``missing``，不抛异常。
* 校验返回 ``{"ok", "errors", "warnings", "violations", ...}``；``strict=False``
  时违规降级为 ``warnings`` 且 ``ok=True``。
* 写入/清除均为「临时文件 + ``os.replace``」原子替换；返回
  ``{"success", "target", "written"|"cleared", "skipped", "checksum"}``，
  出错时 ``success=False`` 且 ``message`` 为中文原因，异常不外抛。
* XML 只用标准库 ``xml.etree.ElementTree`` 读写，缩进 2 空格、UTF-8、
  声明固定为 ``<?xml version="1.0" encoding="utf-8"?>``，结构遵循契约第三节。

HTTP 路由（前缀 ``/api/runner``）
--------------------------------
* ``POST /constants/validate``  请求体 ``{tps_id|bindings, catalog, strict}``
* ``POST /constants/write``     请求体 ``{target_path, bindings, catalog, header, strict}``
* ``POST /constants/clear``     请求体 ``{target_path, keys}``
* ``GET  /constants/resolve``   查询参数 ``tps_id``、可选 ``catalog``（JSON）
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from xml.etree import ElementTree as ET

SCHEMA = "ate.constants.v2"
API_PREFIX = "/api/runner"

__all__ = [
    "parse_bindings",
    "resolve_models",
    "validate_values",
    "write_constants",
    "clear_constants",
    "register_routes",
]

SOURCES = ("default", "version", "sn")
PARSERS = ("xml", "kdb", "python")
FIELD_TYPES = ("float", "int", "str", "bool", "enum")

# register_routes 注入的运行期上下文（纯函数不依赖）
_BASE_DIR: Optional[Path] = None
_DB_PATH: Optional[Path] = None


# ---------------------------------------------------------------- 基础工具


def _now() -> str:
    """契约要求的本地时区 ISO 时间（秒精度）。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _text(value: Any, limit: int = 400) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _nonempty(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _version_key(version: Any) -> tuple:
    """版本号自然排序键：``2026.11.01`` > ``2025.12.01``，``V10`` > ``V9``。"""
    parts = re.split(r"([0-9]+)", str(version or ""))
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts if p)


def _values_list(raw: Any) -> list[str]:
    """枚举取值归一化：支持 ``["A","B"]``、``"A,B"``、``[{"value": "A"}]``。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    if isinstance(raw, (list, tuple, set)):
        out: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                item = item.get("value", item.get("key", item.get("label", "")))
            text = _text(item, 120)
            if text:
                out.append(text)
        return out
    return [_text(raw, 120)]


def _enum_values(field: dict) -> list[str]:
    """枚举取值；兼容契约 ``values`` 与本地库字段的 ``options``。"""
    raw = field.get("values")
    if raw is None:
        raw = field.get("options")
    return _values_list(raw)


def _to_number(raw: Any) -> Optional[float]:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        num = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


# ---------------------------------------------------------------- 解析绑定


def _norm_parser(raw: Any, where: str) -> dict:
    if raw is None:
        return {"type": "xml", "params": {}}
    if isinstance(raw, str):
        ptype, params = raw.strip().lower(), {}
    elif isinstance(raw, dict):
        ptype = _text(raw.get("type") or raw.get("kind") or "xml", 16).lower()
        params = raw.get("params") or raw.get("options") or raw.get("args") or {}
    else:
        raise ValueError(f"{where} 的 parser 必须是字符串或对象")
    if ptype not in PARSERS:
        raise ValueError(f"{where} 的 parser.type 必须是 {'/'.join(PARSERS)}: {ptype}")
    if not isinstance(params, dict):
        raise ValueError(f"{where} 的 parser.params 必须是对象")
    return {"type": ptype, "params": dict(params)}


def _norm_binding(item: Any, index: int, default_sn: str = "") -> dict:
    where = f"machine_constants 第 {index} 条"
    if not isinstance(item, dict):
        raise ValueError(f"{where} 不是对象")
    pn = _text(item.get("pn") or item.get("part_no") or item.get("partNo"), 64).upper()
    if not pn:
        raise ValueError(f"{where} 缺少 pn")
    model_key = _text(
        item.get("model_key") or item.get("modelKey") or item.get("model") or item.get("key"),
        64,
    )
    if not model_key:
        raise ValueError(f"{where}（PN={pn}）缺少 model_key")
    alias = _text(item.get("alias") or item.get("binding_alias") or item.get("name"), 64) or model_key
    source = _text(item.get("source") or item.get("value_source") or "default", 16).lower()
    if source not in SOURCES:
        raise ValueError(f"{where}（{pn}/{model_key}）的 source 必须是 {'/'.join(SOURCES)}: {source}")
    version = _text(item.get("version") or item.get("model_version"), 48)
    if source == "version" and not version:
        raise ValueError(f"{where}（{pn}/{model_key}）source=version 缺少 version")
    parser = _norm_parser(item.get("parser") or item.get("parser_type"), where)
    if item.get("parser") is None and item.get("parser_params") is not None:
        params = item.get("parser_params")
        if not isinstance(params, dict):
            raise ValueError(f"{where} 的 parser_params 必须是对象")
        parser = {"type": _text(item.get("parser_type") or "xml", 16).lower(), "params": dict(params)}
        if parser["type"] not in PARSERS:
            raise ValueError(f"{where} 的 parser.type 必须是 {'/'.join(PARSERS)}: {parser['type']}")
    raw_values = item.get("values") or item.get("calibrated_values") or {}
    if raw_values and not isinstance(raw_values, dict):
        raise ValueError(f"{where}（{pn}/{model_key}）的 values 必须是 {{字段: 值}} 对象")
    values = {str(k): v for k, v in raw_values.items()} if isinstance(raw_values, dict) else {}
    return {
        "pn": pn,
        "model_key": model_key,
        "alias": alias,
        "source": source,
        "version": version,
        "parser": parser,
        "sn": _text(item.get("sn"), 64) or default_sn,
        "values": values,
    }


def parse_bindings(tps: dict) -> list[dict]:
    """把 TPS 的 ``machine_constants`` 段归一化为绑定列表。

    兼容写法：``machine_constants`` 为数组、为 ``{"bindings": [...]}`` 对象、
    整段缺失（返回 ``[]``）。条目统一为 ``{pn, model_key, alias, source,
    version, parser: {type, params}, sn, values}``。

    缺 ``pn`` / ``model_key`` 或来源、解析器非法时抛 ``ValueError``，消息带
    ``第 N 条``（1 起）定位。
    """
    if tps is None:
        return []
    if isinstance(tps, list):
        section: Any = tps
        default_sn = ""
    elif isinstance(tps, dict):
        if "machine_constants" in tps:
            section = tps.get("machine_constants")
        elif "bindings" in tps and "pn" not in tps and "model_key" not in tps:
            section = tps
        else:
            return []
        default_sn = _text(tps.get("sn") or tps.get("uut"), 64)
    else:
        raise ValueError("TPS 必须是 JSON 对象")

    if section is None:
        return []
    if isinstance(section, list):
        items = section
    elif isinstance(section, dict):
        default_sn = _text(section.get("sn"), 64) or default_sn
        raw_items = section.get("bindings")
        if raw_items is None:
            raw_items = section.get("items")
        if raw_items is None:
            items = [section] if (section.get("pn") or section.get("model_key")) else []
        elif isinstance(raw_items, list):
            items = raw_items
        else:
            raise ValueError("machine_constants.bindings 必须是数组")
    else:
        raise ValueError("machine_constants 必须是数组或含 bindings 的对象")

    return [_norm_binding(item, i + 1, default_sn) for i, item in enumerate(items)]


# ---------------------------------------------------------------- 解析模型


def _catalog_entries(catalog: Any) -> list[dict]:
    if isinstance(catalog, list):
        raw_models = catalog
        catalog_version = ""
    elif isinstance(catalog, dict):
        raw_models = catalog.get("models") or catalog.get("items") or []
        catalog_version = _text(catalog.get("version"), 48)
    else:
        raw_models = []
        catalog_version = ""
    if not isinstance(raw_models, list):
        return []
    entries: list[dict] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        pn = _text(item.get("pn") or item.get("part_no"), 64).upper()
        model_key = _text(item.get("model_key") or item.get("modelKey") or item.get("key"), 64)
        if not pn or not model_key:
            continue
        fields = item.get("fields") or item.get("items") or []
        entry = dict(item)
        entry["pn"] = pn
        entry["model_key"] = model_key
        entry["version"] = _text(item.get("version") or catalog_version, 48)
        entry["fields"] = fields if isinstance(fields, list) else []
        entries.append(entry)
    return entries


def _pick_entry(candidates: list[dict], source: str, version: str) -> tuple[Optional[dict], str]:
    if not candidates:
        return None, ""
    if source == "version":
        matched = [e for e in candidates if _text(e.get("version"), 48) == version]
        if not matched:
            return None, f"catalog 中不存在版本 {version} 的模型结构"
        return matched[0], ""
    active = [
        e for e in candidates if e.get("is_active") in (1, True, "1", "true", "True")
    ]
    pool = active or candidates
    return sorted(pool, key=lambda e: _version_key(e.get("version")), reverse=True)[0], ""


def _cell_value(field: dict, raw: Any) -> str:
    if raw is None:
        return ""
    ftype = (_text(field.get("type"), 16) or "str").lower()
    if ftype == "bool":
        if isinstance(raw, bool):
            return "true" if raw else "false"
    return str(raw)


def resolve_models(bindings: list[dict], catalog: dict) -> dict:
    """按绑定从模型库解析出可写入的模型集合与字段值。

    返回 ``{"models": [{...model, fields: [{...field, value, filled, source}],
    filled_count}], "missing": [{index, pn, model_key, alias, reason}], "counts": {...}}``。

    * ``source=default`` 一律取字段 ``default``；
    * ``source=version`` 取该版本条目的结构及条目内 ``values``（缺省回默认值）；
    * ``source=sn`` 取绑定 ``values`` 覆盖，其次 catalog 条目 ``values``，最后默认值；
      缺 ``sn`` 时进入 ``missing``；有 ``sn`` 但无标定值时回退默认值并在模型上
      标 ``warning``（不静默失败）。
    * 模型不存在只进 ``missing``，不抛异常。
    """
    if isinstance(bindings, dict):
        bindings = bindings.get("bindings") or []
    if not isinstance(bindings, list):
        return {"models": [], "missing": [], "counts": {"model_count": 0, "missing_count": 0},
                "message": "bindings 必须是数组"}
    entries = _catalog_entries(catalog)
    index: dict[tuple[str, str], list[dict]] = {}
    index_ci: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        index.setdefault((entry["pn"], entry["model_key"]), []).append(entry)
        index_ci.setdefault((entry["pn"], entry["model_key"].lower()), []).append(entry)

    models: list[dict] = []
    missing: list[dict] = []
    for pos, binding in enumerate(bindings, start=1):
        if not isinstance(binding, dict):
            missing.append({"index": pos, "pn": "", "model_key": "", "alias": "",
                            "reason": "绑定不是对象"})
            continue
        pn = _text(binding.get("pn"), 64).upper()
        model_key = _text(binding.get("model_key"), 64)
        alias = _text(binding.get("alias"), 64) or model_key
        source = _text(binding.get("source") or "default", 16).lower()
        version = _text(binding.get("version"), 48)
        sn = _text(binding.get("sn"), 64)
        if not pn or not model_key:
            missing.append({"index": pos, "pn": pn, "model_key": model_key, "alias": alias,
                            "reason": "绑定缺少 pn 或 model_key"})
            continue
        if source not in SOURCES:
            missing.append({"index": pos, "pn": pn, "model_key": model_key, "alias": alias,
                            "reason": f"source 非法: {source}"})
            continue
        if source == "sn" and not sn:
            missing.append({"index": pos, "pn": pn, "model_key": model_key, "alias": alias,
                            "reason": "source=sn 需要提供 SN"})
            continue
        candidates = index.get((pn, model_key)) or index_ci.get((pn, model_key.lower())) or []
        if not candidates:
            missing.append({"index": pos, "pn": pn, "model_key": model_key, "alias": alias,
                            "reason": f"catalog 中未找到模型 {pn}/{model_key}"})
            continue
        entry, reason = _pick_entry(candidates, source, version)
        if entry is None:
            missing.append({"index": pos, "pn": pn, "model_key": model_key, "alias": alias,
                            "reason": reason or "catalog 中未找到可用模型结构"})
            continue
        if not entry["fields"]:
            missing.append({"index": pos, "pn": pn, "model_key": model_key, "alias": alias,
                            "reason": f"模型 {pn}/{model_key} 缺少 fields 结构"})
            continue

        cat_values = entry.get("values") or entry.get("calibrated_values") or {}
        if not isinstance(cat_values, dict):
            cat_values = {}
        bind_values = binding.get("values") or {}
        if not isinstance(bind_values, dict):
            bind_values = {}
        if source == "sn" and not bind_values and not cat_values:
            missing.append(
                {
                    "index": pos,
                    "pn": pn,
                    "model_key": model_key,
                    "alias": alias,
                    "reason": "source=sn 缺少标定值（values）",
                }
            )
            continue

        fields_out: list[dict] = []
        for raw_field in entry["fields"]:
            if not isinstance(raw_field, dict):
                continue
            key = _text(raw_field.get("key") or raw_field.get("name"), 64)
            if not key:
                continue
            default = raw_field.get("default")
            field_source = "default"
            raw_value = default
            if source == "default":
                raw_value = default
            elif source == "version":
                if key in cat_values:
                    raw_value, field_source = cat_values[key], "version"
            else:  # sn
                if key in bind_values:
                    raw_value, field_source = bind_values[key], "sn"
                elif key in cat_values:
                    raw_value, field_source = cat_values[key], "version"
            value = _cell_value(raw_field, raw_value)
            field = dict(raw_field)
            field.update(
                {
                    "key": key,
                    "type": (_text(raw_field.get("type"), 16) or "str").lower(),
                    "value": value,
                    "filled": _nonempty(value),
                    "source": field_source,
                }
            )
            fields_out.append(field)

        model = {k: v for k, v in entry.items() if k not in ("fields", "values", "calibrated_values")
                 and not str(k).startswith("_")}
        model.update(
            {
                "pn": entry["pn"],
                "model_key": entry["model_key"],
                "alias": alias,
                "source": source,
                "version": entry.get("version") or version,
                "sn": sn,
                "parser": dict(binding.get("parser") or {"type": "xml", "params": {}}),
                "fields": fields_out,
                "field_count": len(fields_out),
                "filled_count": sum(1 for f in fields_out if f["filled"]),
                "values": {f["key"]: f["value"] for f in fields_out if f["filled"]},
                "binding_index": pos,
            }
        )
        models.append(model)

    return {
        "models": models,
        "missing": missing,
        "counts": {
            "model_count": len(models),
            "missing_count": len(missing),
            "field_count": sum(m["field_count"] for m in models),
            "filled": sum(m["filled_count"] for m in models),
        },
    }


# ---------------------------------------------------------------- 范围校验


def _validate_field(field: dict) -> Optional[dict]:
    key = _text(field.get("key"), 64)
    ftype = (_text(field.get("type"), 16) or "str").lower()
    raw = field.get("value")
    default = field.get("default")
    if not _nonempty(raw):
        if _nonempty(default):
            return None
        return {
            "key": key,
            "value": raw,
            "rule": "required",
            "allowed": "非空",
            "message": f"{key} 未填写且模型无默认值",
        }
    text = str(raw).strip()

    if ftype == "bool":
        if isinstance(raw, bool):
            return None
        if text.lower() not in ("true", "false"):
            return {
                "key": key,
                "value": raw,
                "rule": "bool",
                "allowed": ["true", "false"],
                "message": f"{key}={text} 不是合法布尔值（只允许 true/false）",
            }
        return None

    if ftype in ("float", "int"):
        try:
            num = float(text)
        except (TypeError, ValueError):
            return {
                "key": key,
                "value": raw,
                "rule": "type",
                "allowed": ftype,
                "message": f"{key}={text} 不是合法 {ftype}",
            }
        if not math.isfinite(num) or (ftype == "int" and not num.is_integer()):
            return {
                "key": key,
                "value": raw,
                "rule": "type",
                "allowed": ftype,
                "message": f"{key}={text} 不是合法 {ftype}",
            }
        low = _to_number(field.get("min"))
        high = _to_number(field.get("max"))
        if (low is not None and num < low) or (high is not None and num > high):
            allowed: dict[str, Any] = {}
            if low is not None:
                allowed["min"] = low
            if high is not None:
                allowed["max"] = high
            span = f"[{low if low is not None else '-inf'}, {high if high is not None else 'inf'}]"
            return {
                "key": key,
                "value": raw,
                "rule": "range",
                "allowed": allowed,
                "allowed_text": span,
                "message": f"{key}={text} 超出允许范围 {span}",
            }
        return None

    if ftype == "enum":
        allowed_values = _enum_values(field)
        if not allowed_values:
            return {
                "key": key,
                "value": raw,
                "rule": "enum",
                "allowed": [],
                "message": f"{key} 是枚举字段但模型未定义 values",
            }
        if text not in allowed_values:
            return {
                "key": key,
                "value": raw,
                "rule": "enum",
                "allowed": allowed_values,
                "message": f"{key}={text} 不在枚举取值 {','.join(allowed_values)} 内",
            }
        return None

    # str
    if isinstance(raw, (dict, list, tuple)):
        return {
            "key": key,
            "value": raw,
            "rule": "type",
            "allowed": "str",
            "message": f"{key} 必须是字符串",
        }
    return None


def validate_values(models: dict, strict: bool = True) -> dict:
    """逐字段校验类型、必填、``min``/``max``、枚举与布尔取值。

    返回 ``{"ok", "strict", "errors", "warnings", "violations", "checked",
    "invalid"}``。``strict=True`` 时任一违规即 ``ok=False`` 并在 ``errors`` 列出
    全部违规（每项含 ``key``、``value``、``rule``、``allowed`` 及定位信息）；
    ``strict=False`` 时违规进入 ``warnings`` 且 ``ok=True``。
    """
    payload = models if isinstance(models, dict) else {"models": models or []}
    items = payload.get("models") if isinstance(payload.get("models"), list) else []
    errors: list[dict] = []
    checked = 0
    for model in items:
        if not isinstance(model, dict):
            continue
        for field in model.get("fields") or []:
            if not isinstance(field, dict):
                continue
            checked += 1
            err = _validate_field(field)
            if err:
                err.update(
                    {
                        "pn": model.get("pn"),
                        "model_key": model.get("model_key"),
                        "alias": model.get("alias"),
                        "field": err.get("key"),
                    }
                )
                errors.append(err)
    if strict:
        return {
            "ok": not errors,
            "strict": True,
            "errors": errors,
            "warnings": [],
            "violations": list(errors),
            "checked": checked,
            "invalid": len(errors),
        }
    return {
        "ok": True,
        "strict": False,
        "errors": [],
        "warnings": errors,
        "violations": list(errors),
        "checked": checked,
        "invalid": len(errors),
        "passed": not errors,
    }


# ---------------------------------------------------------------- XML 工具


def _indent(elem: ET.Element, level: int = 0) -> None:
    pad = "\n" + "  " * level
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = pad + "  "
        child = None
        for child in elem:
            _indent(child, level + 1)
        if child is not None and not (child.tail or "").strip():
            child.tail = pad
    if level and not (elem.tail or "").strip():
        elem.tail = pad


def _xml_bytes(root: ET.Element) -> bytes:
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="utf-8"?>\n' + body + "\n").encode("utf-8")


def _xml_value(field: dict, raw: Any) -> str:
    if raw is None:
        return ""
    ftype = (_text(field.get("type"), 16) or "str").lower()
    if ftype == "bool":
        if isinstance(raw, bool):
            return "true" if raw else "false"
        text = str(raw).strip()
        if text.lower() in ("true", "false"):
            return text.lower()
        return text
    return str(raw)


def _field_value(field: dict) -> str:
    # 显式给出 value 时尊重空值（清除后的默认值语义）；完全缺 value 才回退 default。
    value = field.get("value") if "value" in field else field.get("default")
    return _xml_value(field, value)


def _field_element(field: dict, with_value: bool = True) -> ET.Element:
    key = _text(field.get("key"), 64)
    ftype = (_text(field.get("type"), 16) or "str").lower()
    attrs: dict[str, str] = {
        "key": key,
        "label": _text(field.get("label") or key, 120),
        "type": ftype,
        "unit": _text(field.get("unit"), 32),
    }
    default = field.get("default")
    if default is not None and str(default) != "":
        attrs["default"] = _xml_value(field, default)
    if ftype in ("float", "int"):
        for edge in ("min", "max"):
            edge_value = field.get(edge)
            if edge_value is not None and str(edge_value) != "":
                attrs[edge] = str(edge_value)
    if ftype == "enum":
        attrs["values"] = ",".join(_enum_values(field))
    attrs["value"] = _field_value(field) if with_value else ""
    return ET.Element("Field", attrs)


def _model_items(models: Any) -> list[dict]:
    if isinstance(models, dict):
        if isinstance(models.get("models"), list):
            return [m for m in models["models"] if isinstance(m, dict)]
        if models.get("model_key") and isinstance(models.get("fields"), list):
            return [models]
        return []
    if isinstance(models, list):
        return [m for m in models if isinstance(m, dict)]
    return []


def _model_fields(model: dict) -> list[dict]:
    fields = model.get("fields")
    if not isinstance(fields, list):
        return []
    out = []
    for field in fields:
        if isinstance(field, dict) and _text(field.get("key"), 64):
            out.append(field)
    return out


def _pick_model(items: list[dict], header: dict) -> dict:
    model_key = _text(header.get("model_key") or header.get("modelKey"), 64)
    if model_key:
        for model in items:
            if _text(model.get("model_key"), 64) == model_key:
                return model
        raise ValueError(f"models 中没有 model_key={model_key} 的模型")
    if len(items) == 1:
        return items[0]
    raise ValueError(f"目标文件只能写入一个模型，收到 {len(items)} 个；请在 header 指定 model_key")


def _header_get(header: dict, *names: str) -> str:
    for name in names:
        value = header.get(name)
        if _nonempty(value):
            return str(value).strip()
    return ""


def _write_header(model: dict, header: dict) -> dict:
    calib = header.get("calibration")
    if not isinstance(calib, dict):
        calib = {}
    pn = (_header_get(header, "pn") or _text(model.get("pn"), 64)).upper()
    module = _header_get(header, "module", "model_key") or _text(model.get("model_key"), 64)
    if not pn or not module:
        raise ValueError("写入模型缺少 pn 或 model_key")
    model_key = _text(model.get("model_key"), 64)
    if model_key and module != model_key:
        raise ValueError(f"header.module={module} 与模型 model_key={model_key} 不一致")
    sn = _header_get(header, "sn") or _text(calib.get("sn"), 64)
    return {
        "pn": pn,
        "module": module,
        "version": _header_get(header, "version") or _text(model.get("version"), 48),
        "name": _header_get(header, "name") or _text(model.get("name"), 120) or module,
        "product": _header_get(header, "product"),
        "subsystem": _header_get(header, "subsystem"),
        "sn": sn,
        "operator": _header_get(header, "operator") or _text(calib.get("operator"), 64),
        "tps_id": _header_get(header, "tps_id", "tpsId") or _text(calib.get("tpsId"), 64),
        "task_id": _header_get(header, "task_id", "taskId") or _text(calib.get("taskId"), 64),
        "at": _header_get(header, "at", "calibrated_at") or _text(calib.get("at"), 64),
        "generated_at": _header_get(header, "generated_at", "generatedAt") or _now(),
        "calibrated": bool(sn),
    }


def _replace_file(src: str, dst: str) -> None:
    """原子替换入口（独立函数便于测试注入失败）。"""
    os.replace(src, dst)


def _atomic_write(path: Path, data: bytes) -> None:
    """写同目录临时文件后 ``os.replace``；失败时删除临时文件，不动原文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_file(tmp_name, str(path))
        tmp_name = ""
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _write_failure(message: str, target: Path | str = "") -> dict:
    return {
        "success": False,
        "message": message,
        "target": str(target),
        "written": [],
        "skipped": [],
        "checksum": "",
    }


def _build_template(meta: dict, model: dict) -> ET.Element:
    attrs = {
        "schema": SCHEMA,
        "module": meta["module"],
        "pn": meta["pn"],
        "version": meta["version"],
        "calibrated": "true" if meta["calibrated"] else "false",
        "sn": meta["sn"],
        "generatedAt": meta["generated_at"],
    }
    root = ET.Element("MachineConstants", attrs)
    ET.SubElement(
        root,
        "Meta",
        {"product": meta["product"], "subsystem": meta["subsystem"], "name": meta["name"]},
    )
    fields_el = ET.SubElement(root, "Fields")
    for field in _model_fields(model):
        fields_el.append(_field_element(field, with_value=meta["calibrated"]))
    if meta["calibrated"]:
        ET.SubElement(
            root,
            "Calibration",
            {
                "sn": meta["sn"],
                "operator": meta["operator"],
                "tpsId": meta["tps_id"],
                "taskId": meta["task_id"],
                "at": meta["at"] or meta["generated_at"],
            },
        )
    return root


# ---------------------------------------------------------------- 写入


def write_constants(target: Path, models: dict, header: dict) -> dict:
    """把解析后的模型值写入 ``Module.xml`` / ``<SN>_Module.xml``。

    * 目标不存在：按契约第三节新建结构模板（``Meta`` + ``Fields`` + 可选
      ``Calibration``）。
    * 目标已存在：只更新 ``Field/@value`` 与根节点 ``calibrated`` / ``sn`` /
      ``generatedAt``、``Calibration`` 节点，保留根节点与字段的其它属性，不改
      字段结构（模型里有、文件里没有的字段进 ``skipped``）。
    * ``header`` 含非空 ``sn`` 时按标定实例处理：``calibrated="true"``，且文件内
      每个 ``Field`` 必须有值（缺值拒绝写入）；无 ``sn`` 时按结构模板处理。
    * 先写同目录临时文件再 ``os.replace``，失败不破坏原文件。

    返回 ``{"success", "message", "target", "written", "skipped", "checksum",
    "calibrated", "sn", "pn", "model_key", "version"}``。
    """
    path = Path(target) if target is not None else Path("")
    try:
        if not str(path).strip() or str(path) in (".", ""):
            return _write_failure("缺少目标文件路径")
        if path.suffix.lower() != ".xml":
            return _write_failure(f"目标文件必须是 .xml: {path}", path)
        if path.exists() and path.is_dir():
            return _write_failure(f"目标路径是目录，不能写入: {path}", path)
        items = _model_items(models)
        if not items:
            return _write_failure("没有可写入的模型（models 为空）", path)
        header = header if isinstance(header, dict) else {}
        model = _pick_model(items, header)
        meta = _write_header(model, header)
        if header.get("calibrated") is True and not meta["sn"]:
            return _write_failure("标定实例必须提供 sn", path)

        fields = _model_fields(model)
        if not fields:
            return _write_failure(f"模型 {meta['pn']}/{meta['module']} 没有可写字段", path)

        created = not path.exists()
        if created:
            root = _build_template(meta, model)
            written, skipped = [], []
            for field in fields:
                if meta["calibrated"] and _nonempty(_field_value(field)):
                    written.append(field["key"])
                else:
                    skipped.append(field["key"])
        else:
            try:
                root = ET.parse(str(path)).getroot()
            except ET.ParseError as exc:
                return _write_failure(f"目标文件不是有效 XML: {exc}", path)
            if root.tag != "MachineConstants":
                return _write_failure(f"目标文件根节点不是 MachineConstants: {root.tag}", path)
            fields_el = root.find("Fields")
            if fields_el is None:
                return _write_failure("目标文件缺少 Fields 节点，不是结构模板或标定实例", path)
            schema = _text(root.get("schema"), 64)
            if schema and schema != SCHEMA:
                return _write_failure(f"目标文件 schema={schema}，与 {SCHEMA} 不一致，拒绝覆盖", path)
            old_pn = _text(root.get("pn"), 64).upper()
            if old_pn and old_pn != meta["pn"]:
                return _write_failure(
                    f"目标文件 pn={old_pn} 与写入模型 pn={meta['pn']} 不一致，拒绝写入", path
                )
            old_module = _text(root.get("module"), 64)
            if old_module and old_module != meta["module"]:
                return _write_failure(
                    f"目标文件 module={old_module} 与写入模型 module={meta['module']} 不一致，拒绝写入",
                    path,
                )
            old_sn = _text(root.get("sn"), 64)
            if old_sn and not meta["sn"] and not header.get("allow_reset_sn"):
                return _write_failure(
                    f"目标文件是标定实例（sn={old_sn}），写入时必须提供 sn；如需降级请用清除接口",
                    path,
                )

            root.set("schema", SCHEMA)
            root.set("module", meta["module"])
            root.set("pn", meta["pn"])
            if meta["version"]:
                root.set("version", meta["version"])
            root.set("calibrated", "true" if meta["calibrated"] else "false")
            root.set("sn", meta["sn"])
            root.set("generatedAt", meta["generated_at"])

            meta_el = root.find("Meta")
            if meta_el is None:
                meta_el = ET.Element("Meta")
                root.insert(0, meta_el)
            for name in ("product", "subsystem", "name"):
                if meta[name]:
                    meta_el.set(name, meta[name])

            by_key = {}
            for el in fields_el.findall("Field"):
                key = _text(el.get("key"), 64)
                if key and key not in by_key:
                    by_key[key] = el
            written, skipped = [], []
            for field in fields:
                key = field["key"]
                value = _field_value(field) if meta["calibrated"] else ""
                el = by_key.get(key)
                if el is None:
                    skipped.append(key)
                    continue
                el.set("value", value)
                if _nonempty(value):
                    written.append(key)
                else:
                    skipped.append(key)

        fields_el = root.find("Fields")
        if fields_el is None:
            return _write_failure("目标文件缺少 Fields 节点，无法写入", path)
        if meta["calibrated"]:
            empty = [
                _text(el.get("key"), 64)
                for el in fields_el.findall("Field")
                if not _nonempty(el.get("value"))
            ]
            if empty:
                return _write_failure(
                    f"标定实例存在无值字段，拒绝写入: {', '.join(empty)}", path
                )
            calib_el = root.find("Calibration")
            if calib_el is None:
                calib_el = ET.SubElement(root, "Calibration")
            calib_el.attrib.clear()
            calib_el.set("sn", meta["sn"])
            calib_el.set("operator", meta["operator"])
            calib_el.set("tpsId", meta["tps_id"])
            calib_el.set("taskId", meta["task_id"])
            calib_el.set("at", meta["at"] or meta["generated_at"])
        else:
            for old in root.findall("Calibration"):
                root.remove(old)

        _indent(root)
        data = _xml_bytes(root)
        checksum = _sha256(data)
        _atomic_write(path, data)
        if meta["calibrated"]:
            message = f"已写入 {len(written)} 个字段" + (
                f"，跳过 {len(skipped)} 个" if skipped else ""
            )
        elif created:
            message = f"已生成结构模板：{len(fields)} 个字段（value 留空，取 default）"
        else:
            message = f"已更新结构模板：{len(fields)} 个字段（value 留空，取 default）"
        return {
            "success": True,
            "message": message,
            "target": str(path),
            "written": written,
            "skipped": skipped,
            "checksum": checksum,
            "calibrated": meta["calibrated"],
            "sn": meta["sn"],
            "pn": meta["pn"],
            "model_key": meta["module"],
            "version": meta["version"],
            "created": created,
        }
    except ValueError as exc:
        return _write_failure(str(exc), path)
    except OSError as exc:
        return _write_failure(f"写入失败: {exc}", path)
    except Exception as exc:  # pragma: no cover - 兜底，路由不抛裸异常
        return _write_failure(f"写入失败: {exc}", path)


# ---------------------------------------------------------------- 清除


def _norm_keys(keys: Any) -> list[str]:
    if keys is None:
        return []
    if isinstance(keys, str):
        raw: Iterable[Any] = keys.split(",")
    elif isinstance(keys, (list, tuple, set)):
        raw = keys
    else:
        raw = [keys]
    out: list[str] = []
    seen = set()
    for item in raw:
        key = _text(item, 64)
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def clear_constants(target: Path, keys: list[str] | None = None) -> dict:
    """清除目标文件的常数值，恢复默认值语义。

    * ``keys`` 为空：所有 ``Field/@value`` 置空、``calibrated="false"``、``sn=""``、
      删除 ``Calibration`` 节点（``default`` 属性保留，取值回落到默认值）。
    * ``keys`` 非空：只清指定字段；其余字段保留。清除后文件不再是完整标定实例，
      因此同样置 ``calibrated="false"`` 并删除已失效的 ``Calibration``（``sn`` 保留
      以标识原实例）。

    返回 ``{"success", "message", "target", "cleared", "keys", "skipped",
    "checksum", "calibrated", "sn"}``。原子写入。
    """
    path = Path(target) if target is not None else Path("")
    failure = {
        "success": False,
        "message": "",
        "target": str(target) if target else "",
        "cleared": [],
        "keys": [],
        "skipped": [],
        "checksum": "",
    }
    try:
        if not str(path).strip():
            return {**failure, "message": "缺少目标文件路径"}
        if not path.is_file():
            return {**failure, "message": f"目标文件不存在: {path}"}
        wanted = _norm_keys(keys)
        try:
            root = ET.parse(str(path)).getroot()
        except ET.ParseError as exc:
            return {**failure, "message": f"目标文件不是有效 XML: {exc}"}
        if root.tag != "MachineConstants":
            return {**failure, "message": f"目标文件根节点不是 MachineConstants: {root.tag}"}
        fields_el = root.find("Fields")
        if fields_el is None:
            return {**failure, "message": "目标文件缺少 Fields 节点"}

        field_els = fields_el.findall("Field")
        by_key: dict[str, ET.Element] = {}
        for el in field_els:
            key = _text(el.get("key"), 64)
            if key and key not in by_key:
                by_key[key] = el
        if wanted:
            selected = [by_key[k] for k in wanted if k in by_key]
            missing = [k for k in wanted if k not in by_key]
            if not selected:
                return {**failure, "message": f"目标文件中未找到字段: {', '.join(wanted)}"}
        else:
            selected = field_els
            missing = []

        for el in selected:
            el.set("value", "")
        cleared = [_text(el.get("key"), 64) for el in selected]
        root.set("calibrated", "false")
        if not wanted:
            root.set("sn", "")
        for old in root.findall("Calibration"):
            root.remove(old)
        root.set("generatedAt", _now())

        _indent(root)
        data = _xml_bytes(root)
        checksum = _sha256(data)
        _atomic_write(path, data)
        message = f"已清除 {len(cleared)} 个字段" + (f"，{len(missing)} 个未找到" if missing else "")
        return {
            "success": True,
            "message": message,
            "target": str(path),
            "cleared": cleared,
            "keys": cleared,
            "skipped": missing,
            "checksum": checksum,
            "calibrated": False,
            "sn": root.get("sn") or "",
        }
    except OSError as exc:
        return {**failure, "message": f"清除失败: {exc}"}
    except Exception as exc:  # pragma: no cover - 兜底
        return {**failure, "message": f"清除失败: {exc}"}


# ---------------------------------------------------------------- 运行期取数


def _load_tps(tps_id: str) -> dict:
    """读取 TPS：Controller/缓存优先，其次本地 testresource 兜底。"""
    tps_id = _text(tps_id, 120)
    if not tps_id:
        raise ValueError("缺少 tps_id")
    last_error = ""
    try:
        import tps_source  # 延迟导入，纯函数测试无需 Controller

        info = tps_source.get_tps(tps_id)
        if isinstance(info, dict) and isinstance(info.get("tps"), dict):
            return {
                "tps": info["tps"],
                "source": info.get("source") or "",
                "error": info.get("error") or "",
            }
        last_error = _text((info or {}).get("error"), 200) if isinstance(info, dict) else ""
    except Exception as exc:  # noqa: BLE001 - 离线兜底
        last_error = str(exc)

    if _BASE_DIR:
        candidates = [
            _BASE_DIR / "testresource" / tps_id / "tps.json",
            _BASE_DIR / "testresource" / f"{tps_id}.json",
            _BASE_DIR / "tps_cache" / "tps" / tps_id / "tps.json",
        ]
        for candidate in candidates:
            if candidate.is_file():
                try:
                    tps = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ValueError(f"本地 TPS 缓存不是有效 JSON: {candidate}（{exc}）")
                if isinstance(tps, dict):
                    return {"tps": tps, "source": "local", "error": last_error}
    raise ValueError(f"TPS 不存在或不可达：{tps_id}")


def _catalog_from_db() -> dict:
    """从本地 ate.db 的 constant_model / constant_value 组出运行期模型库。"""
    if not _DB_PATH:
        return {"models": [], "source": "none"}
    path = Path(_DB_PATH)
    if not path.is_file():
        return {"models": [], "source": "none"}
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT version, pn, product, subsystem, model_key, name, fields_json "
                "FROM constant_model"
            ).fetchall()
            active_row = conn.execute(
                "SELECT version FROM constant_version WHERE is_active = 1 LIMIT 1"
            ).fetchone()
            active_version = active_row["version"] if active_row else ""
            values: dict[tuple, dict] = {}
            for row in conn.execute(
                "SELECT version, pn, model_key, field_key, value FROM constant_value"
            ):
                key = (row["version"], row["pn"], row["model_key"])
                values.setdefault(key, {})[row["field_key"]] = row["value"]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"models": [], "source": "db_error", "message": f"读取本地常数库失败: {exc}"}

    models = []
    for row in rows:
        try:
            fields = json.loads(row["fields_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            fields = []
        models.append(
            {
                "version": row["version"],
                "pn": row["pn"],
                "product": row["product"] or "",
                "subsystem": row["subsystem"] or "",
                "model_key": row["model_key"],
                "name": row["name"] or row["model_key"],
                "fields": fields if isinstance(fields, list) else [],
                "values": values.get((row["version"], row["pn"], row["model_key"]), {}),
                "is_active": 1 if active_version and row["version"] == active_version else 0,
            }
        )
    return {"models": models, "source": "db"}


def _catalog_from_payload(payload: dict) -> tuple[Any, str]:
    catalog = payload.get("catalog")
    if isinstance(catalog, (dict, list)):
        return catalog, "request"
    if isinstance(catalog, str) and catalog.strip():
        try:
            parsed = json.loads(catalog)
        except json.JSONDecodeError as exc:
            raise ValueError(f"catalog 不是有效 JSON: 第 {exc.lineno} 行第 {exc.colno} 列")
        return parsed, "request"
    return _catalog_from_db(), "db"


def _bindings_from_payload(payload: dict) -> list[dict]:
    if payload.get("bindings") is not None:
        return parse_bindings({"machine_constants": payload.get("bindings")})
    tps_id = _text(payload.get("tps_id") or payload.get("tpsId"), 120)
    if not tps_id:
        raise ValueError("缺少 bindings 或 tps_id")
    info = _load_tps(tps_id)
    return parse_bindings(info["tps"])


def _models_from_payload(payload: dict) -> tuple[dict, str, str]:
    """返回 ``(models, tps_source, catalog_source)``；允许直接传 models。"""
    if payload.get("models") is not None:
        return (
            {"models": _model_items(payload.get("models")), "missing": []},
            "",
            "request",
        )
    bindings = _bindings_from_payload(payload)
    catalog, catalog_source = _catalog_from_payload(payload)
    resolved = resolve_models(bindings, catalog)
    tps_source_name = "request" if payload.get("bindings") is not None else "tps"
    return resolved, tps_source_name, catalog_source


# ---------------------------------------------------------------- HTTP 路由


def register_routes(app, base_dir: Path, db_path=None) -> None:
    """挂载 ``/api/runner/constants/*`` 路由（异常一律包成中文 message）。"""
    global _BASE_DIR, _DB_PATH
    _BASE_DIR = Path(base_dir) if base_dir else None
    _DB_PATH = Path(db_path) if db_path else None

    from fastapi import Body

    @app.post(API_PREFIX + "/constants/validate")
    def runner_constants_validate(payload: dict = Body(...)):
        """校验 TPS 绑定解析出的常数值，返回逐字段结论。"""
        try:
            payload = payload if isinstance(payload, dict) else {}
            resolved, _, catalog_source = _models_from_payload(payload)
            missing = resolved.get("missing") or []
            if not (resolved.get("models") or []) and missing:
                return {
                    "success": False,
                    "ok": False,
                    "message": f"未找到任何模型：{len(missing)} 个绑定缺失",
                    "errors": [],
                    "warnings": [],
                    "violations": [],
                    "models": [],
                    "missing": missing,
                    "counts": resolved.get("counts") or {},
                    "catalog_source": catalog_source,
                    "checked": 0,
                    "invalid": 0,
                }
            strict = payload.get("strict", True) is not False
            result = validate_values(resolved, strict=strict)
            ok = bool(result.get("ok"))
            return {
                "success": ok,
                **result,
                "models": resolved.get("models") or [],
                "missing": resolved.get("missing") or [],
                "counts": resolved.get("counts") or {},
                "catalog_source": catalog_source,
                "message": (
                    f"校验通过（{result.get('checked', 0)} 个字段）"
                    + (f"；{len(missing)} 个绑定缺失" if missing else "")
                    if ok
                    else f"校验未通过：{result.get('invalid', 0)} 项违规"
                ),
            }
        except ValueError as exc:
            return {"success": False, "ok": False, "message": str(exc), "errors": []}
        except Exception as exc:  # pragma: no cover - 兜底
            return {"success": False, "ok": False, "message": f"校验失败: {exc}", "errors": []}

    @app.post(API_PREFIX + "/constants/write")
    def runner_constants_write(payload: dict = Body(...)):
        """写入前先做范围校验，未通过则拒绝写入。"""
        try:
            payload = payload if isinstance(payload, dict) else {}
            target = payload.get("target_path") or payload.get("target")
            if not _nonempty(target):
                return {"success": False, "message": "缺少 target_path", "written": [], "skipped": []}
            resolved, _, catalog_source = _models_from_payload(payload)
            missing = resolved.get("missing") or []
            if missing:
                first = missing[0]
                return {
                    "success": False,
                    "ok": False,
                    "blocked": True,
                    "message": (
                        f"有 {len(missing)} 个绑定未找到模型或缺少标定值，已拒绝写入："
                        f"{first.get('pn')}/{first.get('model_key')}（{first.get('reason')}）"
                    ),
                    "errors": [],
                    "warnings": [],
                    "violations": [],
                    "models": resolved.get("models") or [],
                    "missing": missing,
                    "catalog_source": catalog_source,
                    "written": [],
                    "skipped": [],
                    "checksum": "",
                }
            strict = payload.get("strict", True) is not False
            result = validate_values(resolved, strict=strict)
            if not result.get("ok"):
                return {
                    "success": False,
                    "ok": False,
                    "blocked": True,
                    "message": f"范围校验未通过，已拒绝写入：{result.get('invalid', 0)} 项违规",
                    "errors": result.get("errors") or [],
                    "warnings": result.get("warnings") or [],
                    "violations": result.get("violations") or [],
                    "models": resolved.get("models") or [],
                    "missing": resolved.get("missing") or [],
                    "catalog_source": catalog_source,
                    "written": [],
                    "skipped": [],
                    "checksum": "",
                }
            header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
            out = write_constants(Path(str(target)), resolved, header)
            out.update(
                {
                    "ok": out.get("success", False),
                    "errors": [],
                    "warnings": result.get("warnings") or [],
                    "violations": result.get("violations") or [],
                    "missing": resolved.get("missing") or [],
                    "catalog_source": catalog_source,
                }
            )
            if out.get("success") and out.get("missing"):
                out["message"] = f"{out.get('message')}；{len(out['missing'])} 个绑定未找到模型"
            return out
        except ValueError as exc:
            return {"success": False, "message": str(exc), "written": [], "skipped": []}
        except Exception as exc:  # pragma: no cover - 兜底
            return {"success": False, "message": f"写入失败: {exc}", "written": [], "skipped": []}

    @app.post(API_PREFIX + "/constants/clear")
    def runner_constants_clear(payload: dict = Body(...)):
        """清除目标文件的常数值（全部或按字段）。"""
        try:
            payload = payload if isinstance(payload, dict) else {}
            target = payload.get("target_path") or payload.get("target")
            if not _nonempty(target):
                return {"success": False, "message": "缺少 target_path", "cleared": [], "skipped": []}
            out = clear_constants(Path(str(target)), payload.get("keys"))
            return out
        except Exception as exc:  # pragma: no cover - 兜底
            return {"success": False, "message": f"清除失败: {exc}", "cleared": [], "skipped": []}

    @app.get(API_PREFIX + "/constants/resolve")
    def runner_constants_resolve(tps_id: str = "", catalog: str = ""):
        """解析 TPS 的 machine_constants：返回模型、字段与值。"""
        try:
            info = _load_tps(tps_id)
            bindings = parse_bindings(info["tps"])
            catalog_data, catalog_source = _catalog_from_payload({"catalog": catalog})
            resolved = resolve_models(bindings, catalog_data)
            counts = resolved.get("counts") or {}
            if not counts.get("model_count") and not counts.get("missing_count"):
                message = "TPS 未声明 machine_constants（按空集合处理）"
            else:
                message = (
                    f"解析完成：{counts.get('model_count', 0)} 个模型，"
                    f"{counts.get('missing_count', 0)} 个缺失"
                )
            return {
                "success": True,
                "message": message,
                "tps_id": tps_id,
                "tps_source": info.get("source") or "",
                "catalog_source": catalog_source,
                "bindings": bindings,
                **resolved,
            }
        except ValueError as exc:
            return {"success": False, "message": str(exc), "models": [], "missing": []}
        except Exception as exc:  # pragma: no cover - 兜底
            return {"success": False, "message": f"解析失败: {exc}", "models": [], "missing": []}
