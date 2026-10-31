# -*- coding: utf-8 -*-
"""ATE Runner - 机器常数模型（Machine Constants）

语义
----
* **常数模型 = 结构**。一个模型由若干字段（key / 名称 / 类型 / 单位 / 范围 / 默认值）组成，
  不含具体数值；结构由标定前定义，随版本管理。
* **常数 = 结构 + 标定值**。整机标定完成后按「PN 维度」回填数值，数值入库，
  供 TPS 执行时填充。
* **版本** = 一次全量导入。同一版本再次导入视为覆盖（默认保留仍存在字段的标定值）。
* **归属** 按装备树组织：产品 -> 子系统 -> PN -> 常数模型。

落库表（见 `ate_db.SCHEMA_SQL`）
--------------------------------
* `constant_version` 版本头
* `constant_model`   模型结构（版本 + PN + model_key 唯一）
* `constant_value`   标定值（版本 + PN + 模型 + 字段 唯一）

对外
----
* `register_routes(app, base_dir, tps_dir, db_path)` —— 挂载 HTTP 路由
* `write_run_xml(task, tps, report_dir)` —— 测试完成后按 TPS 绑定生成常数 XML
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from xml.etree import ElementTree as ET

import ate_db

_LOCK = threading.RLock()

PN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,47}$")

FIELD_TYPES = ("float", "int", "str", "bool", "enum")

# 运行期由 register_routes 注入
_BASE_DIR: Optional[Path] = None
_TPS_DIR: Optional[Path] = None
_DB_PATH: Optional[Path] = None

# 设备类型不可用、或数据目录缺失时的兜底分组名
UNCLASSIFIED = "_unclassified"


# ---------------------------------------------------------------- 基础工具


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _text(value: Any, limit: int = 400) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _connect() -> sqlite3.Connection:
    return ate_db.connect(_DB_PATH)


def _checksum(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _bbox_norm(pn: str) -> str:
    return pn.strip().upper()


# ---------------------------------------------------------------- 结构规范化


def _norm_field(raw: Any, index: int, *, pn: str, model_key: str) -> dict:
    """规范化单个字段定义；不合规直接抛 ValueError（不做静默丢弃）。"""
    where = f"{pn}/{model_key} 第 {index + 1} 个字段"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} 不是对象")
    key = _text(raw.get("key") or raw.get("id") or raw.get("name"), 64)
    if not key:
        raise ValueError(f"{where} 缺少 key")
    if not KEY_RE.match(key):
        raise ValueError(f"{where} 的 key 不合法（字母/数字/下划线，最长 64）: {key}")
    ftype = _text(raw.get("type"), 16).lower() or "float"
    if ftype not in FIELD_TYPES:
        raise ValueError(f"{where} 的 type 必须是 {'/'.join(FIELD_TYPES)}: {ftype}")
    field = {
        "key": key,
        "label": _text(raw.get("label") or raw.get("title") or key, 120),
        "type": ftype,
        "unit": _text(raw.get("unit"), 32),
        "default": raw.get("default"),
        "min": raw.get("min"),
        "max": raw.get("max"),
        "options": raw.get("options") if isinstance(raw.get("options"), list) else [],
        "calibrated_by": _text(raw.get("calibrated_by") or raw.get("stage"), 120),
        "desc": _text(raw.get("desc") or raw.get("description"), 300),
    }
    # 类型与默认值一致性（有默认值才校验）
    default = field["default"]
    if default is not None:
        try:
            if ftype == "float":
                field["default"] = float(default)
            elif ftype == "int":
                field["default"] = int(default)
            elif ftype == "bool":
                field["default"] = bool(default)
            elif ftype == "str":
                field["default"] = str(default)
        except (TypeError, ValueError):
            raise ValueError(f"{where} 的 default 与 type={ftype} 不匹配: {default!r}")
    for edge in ("min", "max"):
        if field[edge] is None:
            continue
        try:
            field[edge] = float(field[edge])
        except (TypeError, ValueError):
            raise ValueError(f"{where} 的 {edge} 不是数值: {field[edge]!r}")
    return field


def _norm_model(raw: Any, *, pn: str, index: int) -> dict:
    where = f"{pn} 第 {index + 1} 个常数模型"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} 不是对象")
    model_key = _text(raw.get("model_key") or raw.get("key") or raw.get("id"), 64)
    if not model_key:
        raise ValueError(f"{where} 缺少 model_key")
    if not KEY_RE.match(model_key):
        raise ValueError(f"{where} 的 model_key 不合法: {model_key}")
    fields_raw = raw.get("fields")
    if not isinstance(fields_raw, list) or not fields_raw:
        raise ValueError(f"{where} 的 fields 必须是非空数组")
    fields = [_norm_field(f, i, pn=pn, model_key=model_key) for i, f in enumerate(fields_raw)]
    seen = set()
    for f in fields:
        if f["key"] in seen:
            raise ValueError(f"{where} 存在重复字段 key: {f['key']}")
        seen.add(f["key"])
    # 可选：结构与标定值同包提供（标定程序一次性导出时使用）
    values_raw = raw.get("values") or raw.get("calibrated_values") or {}
    values: dict[str, str] = {}
    if values_raw:
        if not isinstance(values_raw, dict):
            raise ValueError(f"{where} 的 values 必须是 {{字段: 值}} 对象")
        for key, val in values_raw.items():
            key = _text(key, 64)
            if key not in seen:
                raise ValueError(f"{where} 的 values 含未定义字段: {key}")
            if val is None:
                continue
            values[key] = str(val)

    return {
        "model_key": model_key,
        "name": _text(raw.get("name") or model_key, 120),
        "category": _text(raw.get("category"), 48),
        "description": _text(raw.get("description") or raw.get("desc"), 400),
        "unit": _text(raw.get("unit"), 32),
        "fields": fields,
        "values": values,
    }


def parse_bundle(text: str | bytes | dict) -> dict:
    """解析全量导入包，返回规范化结构。

    支持的形态（JSON）：
    1. `{"version": "V1.0.0", "groups": [{product, subsystem, pn, models: [...]}]}`
    2. `{"version": "...", "pn_groups": [...]}`（同 1 的别名）
    3. `{"version": "...", "models": [{pn, model_key, fields: [...]}]}`（扁平列表）
    4. 顶层 JSON 数组（等价扁平列表，version 默认取 `default`）
    """
    raw: Any
    if isinstance(text, dict):
        raw = text
    else:
        try:
            if isinstance(text, bytes):
                text = text.decode("utf-8-sig")
            raw = json.loads(text)
        except UnicodeDecodeError:
            raise ValueError("常数模型文件不是 UTF-8 编码")
        except json.JSONDecodeError as exc:
            raise ValueError(f"常数模型文件不是有效 JSON: 第 {exc.lineno} 行第 {exc.colno} 列")

    if isinstance(raw, list):
        raw = {"models": raw}
    if not isinstance(raw, dict):
        raise ValueError("常数模型包必须是 JSON 对象或数组")

    version = _text(raw.get("version") or raw.get("constant_version") or raw.get("ver"), 48)
    if version and not VERSION_RE.match(version):
        raise ValueError(f"版本号不合法（字母/数字/._+- ，最长 48）: {version}")
    version = version or "default"

    label = _text(raw.get("label") or raw.get("name"), 120)
    source = _text(raw.get("source") or raw.get("calibration_source"), 200)
    note = _text(raw.get("note") or raw.get("description"), 400)

    groups_raw = raw.get("groups") or raw.get("pn_groups") or raw.get("pnGroups")
    normalized: list[dict] = []

    if isinstance(groups_raw, list) and groups_raw:
        for gi, g in enumerate(groups_raw):
            if not isinstance(g, dict):
                raise ValueError(f"groups[{gi}] 不是对象")
            pn = _bbox_norm(_text(g.get("pn") or g.get("part_no") or g.get("part_number"), 64))
            if not pn:
                raise ValueError(f"groups[{gi}] 缺少 pn")
            if not PN_RE.match(pn):
                raise ValueError(f"groups[{gi}] 的 pn 不合法: {pn}")
            models_raw = g.get("models") or g.get("constants") or []
            if not isinstance(models_raw, list) or not models_raw:
                raise ValueError(f"groups[{gi}]（PN={pn}）缺少 models")
            normalized.append(
                {
                    "pn": pn,
                    "product": _text(g.get("product") or g.get("product_id"), 64),
                    "subsystem": _text(g.get("subsystem") or g.get("subsystem_id"), 64),
                    "models": [_norm_model(m, pn=pn, index=i) for i, m in enumerate(models_raw)],
                }
            )
    else:
        models_raw = raw.get("models") or raw.get("constants") or raw.get("items") or []
        if not isinstance(models_raw, list) or not models_raw:
            raise ValueError("常数模型包缺少 models / groups，无法导入")
        by_pn: dict[str, dict] = {}
        for i, m in enumerate(models_raw):
            if not isinstance(m, dict):
                raise ValueError(f"models[{i}] 不是对象")
            pn = _bbox_norm(_text(m.get("pn") or m.get("part_no") or raw.get("pn"), 64))
            if not pn:
                raise ValueError(f"models[{i}] 缺少 pn（PN 号是常数模型的管理维度）")
            if not PN_RE.match(pn):
                raise ValueError(f"models[{i}] 的 pn 不合法: {pn}")
            slot = by_pn.setdefault(
                pn,
                {
                    "pn": pn,
                    "product": _text(m.get("product"), 64) or _text(raw.get("product"), 64),
                    "subsystem": _text(m.get("subsystem"), 64) or _text(raw.get("subsystem"), 64),
                    "models": [],
                },
            )
            slot["models"].append(_norm_model(m, pn=pn, index=len(slot["models"])))
        normalized = list(by_pn.values())

    # 同包内重复 model_key 校验
    for group in normalized:
        seen = set()
        for m in group["models"]:
            if m["model_key"] in seen:
                raise ValueError(f"PN={group['pn']} 存在重复 model_key: {m['model_key']}")
            seen.add(m["model_key"])

    return {
        "version": version,
        "label": label,
        "source": source,
        "note": note,
        "groups": normalized,
        "stats": {
            "pn_count": len(normalized),
            "model_count": sum(len(g["models"]) for g in normalized),
            "field_count": sum(len(m["fields"]) for g in normalized for m in g["models"]),
        },
    }


# ---------------------------------------------------------------- 导入


def _resolve_tree_scope(pn: str, product: str, subsystem: str) -> tuple[str, str]:
    """PN 未显式给出产品/子系统时，按装备树推断（PN 前缀命中子系统 id 则挂载）。"""
    if product and subsystem:
        return product, subsystem
    try:
        store = _load_tree_store()
    except Exception:
        return product, subsystem
    products = store.get("products") or []
    subsystems = store.get("subsystems") or []
    sub_ids = {(_text(s.get("id")) or "").upper(): s for s in subsystems}
    prefix = pn.split("-")[0].upper()
    inferred_sub = subsystem or (sub_ids.get(prefix, {}).get("id") or "")
    if not inferred_sub and not subsystem:
        # PN 前缀能命中子系统 id（如 SPM-xxx 归到 SPM）
        inferred_sub = sub_ids.get(prefix, {}).get("id") or ""
    inferred_product = product
    if not inferred_product and inferred_sub:
        owners = (sub_ids.get(inferred_sub.upper()) or {}).get("products") or []
        if owners:
            inferred_product = owners[0]
    if not inferred_product and products:
        inferred_product = ""
    return inferred_product, inferred_sub


def _load_tree_store() -> dict:
    tree_dir = Path(os.environ.get("ATE_TREE_DIR") or ((_BASE_DIR or Path(".")) / "tree"))
    path = tree_dir / "tree.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def import_bundle(
    bundle: dict,
    *,
    mode: str = "overwrite",
    operator: str = "",
    keep_values: bool = True,
) -> dict:
    """写入一个版本的全部常数模型（全量导入）。

    mode:
      * overwrite（默认）—— 同版本已存在时覆盖结构；keep_values=True 时保留仍存在字段的标定值
      * reject —— 同版本已存在时拒绝并返回冲突信息
    """
    if mode not in ("overwrite", "reject"):
        raise ValueError("mode 只能是 overwrite 或 reject")
    version = bundle["version"]
    groups = bundle["groups"]
    stats = bundle["stats"]

    with _LOCK:
        with _connect() as conn:
            exists = conn.execute(
                "SELECT version FROM constant_version WHERE version = ?", (version,)
            ).fetchone()
            if exists and mode == "reject":
                return {
                    "success": False,
                    "conflict": True,
                    "message": f"常数版本 {version} 已存在；如需覆盖请选择「覆盖导入」",
                    "version": version,
                }

            # 覆盖时先取旧值（按 pn/model/field）用于结构未变字段的值保留
            kept: dict[tuple, dict] = {}
            if exists and keep_values:
                for row in conn.execute(
                    "SELECT pn, model_key, field_key, value, unit, source, updated_at "
                    "FROM constant_value WHERE version = ?",
                    (version,),
                ):
                    kept[(row["pn"], row["model_key"], row["field_key"])] = dict(row)

            if exists:
                conn.execute("DELETE FROM constant_model WHERE version = ?", (version,))
                conn.execute("DELETE FROM constant_value WHERE version = ?", (version,))
                conn.execute("DELETE FROM constant_version WHERE version = ?", (version,))

            now = _now()
            conn.execute(
                "INSERT INTO constant_version "
                "(version, label, source, note, model_count, field_count, imported_at, "
                " imported_by, is_active, checksum) VALUES (?,?,?,?,?,?,?,?,1,?)",
                (
                    version,
                    bundle["label"] or version,
                    bundle["source"],
                    bundle["note"],
                    stats["model_count"],
                    stats["field_count"],
                    now,
                    operator,
                    _checksum(bundle["groups"]),
                ),
            )
            conn.execute("UPDATE constant_version SET is_active = 0 WHERE version <> ?", (version,))

            restored = 0
            imported_values = 0
            for group in groups:
                product, subsystem = _resolve_tree_scope(
                    group["pn"], group["product"], group["subsystem"]
                )
                for m in group["models"]:
                    field_keys = {f["key"] for f in m["fields"]}
                    conn.execute(
                        "INSERT INTO constant_model "
                        "(version, pn, product, subsystem, model_key, name, category, description, "
                        " unit, fields_json, field_count, struct_checksum, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            version,
                            group["pn"],
                            product,
                            subsystem,
                            m["model_key"],
                            m["name"],
                            m["category"],
                            m["description"],
                            m["unit"],
                            json.dumps(m["fields"], ensure_ascii=False),
                            len(m["fields"]),
                            _checksum(m["fields"]),
                            now,
                        ),
                    )
                    for fkey, fval in (m.get("values") or {}).items():
                        conn.execute(
                            "INSERT OR REPLACE INTO constant_value "
                            "(version, pn, model_key, field_key, value, unit, source, updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (
                                version,
                                group["pn"],
                                m["model_key"],
                                fkey,
                                fval,
                                next((f.get("unit", "") for f in m["fields"]
                                      if f["key"] == fkey), ""),
                                "import",
                                now,
                            ),
                        )
                        imported_values += 1

                    for key, old in kept.items():
                        if key[0] != group["pn"] or key[1] != m["model_key"]:
                            continue
                        if key[2] not in field_keys:
                            continue
                        conn.execute(
                            "INSERT OR REPLACE INTO constant_value "
                            "(version, pn, model_key, field_key, value, unit, source, updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (
                                version,
                                key[0],
                                key[1],
                                key[2],
                                old["value"],
                                old["unit"],
                                old["source"],
                                old["updated_at"],
                            ),
                        )
                        restored += 1
        return {
            "success": True,
            "version": version,
            "overwritten": bool(exists),
            "imported_values": imported_values,
            "restored_values": restored,
            "stats": stats,
            "message": (
                f"已覆盖常数版本 {version}（{stats['pn_count']} 个 PN / "
                f"{stats['model_count']} 个模型 / {stats['field_count']} 个字段"
                + (f"，包内标定值 {imported_values} 项" if imported_values else "")
                + (f"，保留原标定值 {restored} 项" if restored else "")
            )
            if exists
            else (
                f"已导入常数版本 {version}（{stats['pn_count']} 个 PN / "
                f"{stats['model_count']} 个模型 / {stats['field_count']} 个字段）"
            ),
        }


def import_from_text(
    text: str | bytes,
    *,
    mode: str = "overwrite",
    operator: str = "",
    keep_values: bool = True,
) -> dict:
    """解析 + 导入一步到位（供接口使用）。"""
    bundle = parse_bundle(text)
    result = import_bundle(bundle, mode=mode, operator=operator, keep_values=keep_values)
    if result.get("success"):
        result["label"] = bundle["label"]
    return result


# ---------------------------------------------------------------- 查询


def list_versions() -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT v.*, "
            " (SELECT COUNT(DISTINCT m.pn) FROM constant_model m WHERE m.version = v.version) AS pn_count, "
            " (SELECT COUNT(*) FROM constant_value c WHERE c.version = v.version) AS value_count "
            "FROM constant_version v ORDER BY v.imported_at DESC, v.version DESC"
        ).fetchall()
    versions = []
    for r in rows:
        versions.append(
            {
                "version": r["version"],
                "label": r["label"],
                "source": r["source"],
                "note": r["note"],
                "model_count": r["model_count"],
                "field_count": r["field_count"],
                "pn_count": r["pn_count"],
                "value_count": r["value_count"],
                "imported_at": r["imported_at"],
                "imported_by": r["imported_by"],
                "is_active": bool(r["is_active"]),
                "checksum": r["checksum"],
            }
        )
    active = next((v["version"] for v in versions if v["is_active"]), "")
    return {"versions": versions, "active": active}


def active_version() -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT version FROM constant_version WHERE is_active = 1 ORDER BY imported_at DESC LIMIT 1"
        ).fetchone()
    return (row["version"] if row else "") or ""


def set_active_version(version: str) -> dict:
    with _LOCK:
        with _connect() as conn:
            row = conn.execute(
                "SELECT version FROM constant_version WHERE version = ?", (version,)
            ).fetchone()
            if not row:
                raise ValueError(f"常数版本不存在: {version}")
            conn.execute("UPDATE constant_version SET is_active = 0")
            conn.execute("UPDATE constant_version SET is_active = 1 WHERE version = ?", (version,))
    return {"success": True, "active": version, "message": f"当前生效版本已切换为 {version}"}


def tree(version: str = "") -> dict:
    """PN 维度的装备树（按 产品 -> 子系统 -> PN 组织）。"""
    info = list_versions()
    ver = version or info["active"]
    if ver:
        names = {v["version"] for v in info["versions"]}
        if ver not in names:
            raise ValueError(f"常数版本不存在: {ver}")

    store = _load_tree_store()
    products = store.get("products") or []
    subsystems = store.get("subsystems") or []

    with _connect() as conn:
        rows = conn.execute(
            "SELECT m.pn, m.product, m.subsystem, m.model_key, m.field_count, "
            " (SELECT COUNT(*) FROM constant_value c WHERE c.version = m.version "
            "   AND c.pn = m.pn AND c.model_key = m.model_key) AS filled "
            "FROM constant_model m WHERE m.version = ? ORDER BY m.pn, m.model_key",
            (ver,),
        ).fetchall()

    # (product, subsystem) -> pn -> stats
    bucket: dict[tuple, dict[str, dict]] = {}
    for r in rows:
        product = r["product"] or ""
        subsystem = r["subsystem"] or ""
        key = (product or UNCLASSIFIED, subsystem or UNCLASSIFIED)
        node = bucket.setdefault(key, {}).setdefault(
            r["pn"], {"pn": r["pn"], "model_count": 0, "field_count": 0, "filled": 0, "models": []}
        )
        node["model_count"] += 1
        node["field_count"] += r["field_count"] or 0
        node["filled"] += r["filled"] or 0
        node["models"].append(r["model_key"])

    prod_names = {(_text(p.get("id"))): _text(p.get("name")) or _text(p.get("id")) for p in products}
    sub_names = {(_text(s.get("id"))): _text(s.get("name")) or _text(s.get("id")) for s in subsystems}

    tree_nodes: list[dict] = []
    used: set[tuple] = set()

    def _sub_node(sub_id: str, subs: dict[str, dict]) -> dict:
        pns = sorted(subs.items(), key=lambda kv: kv[0])
        return {
            "id": sub_id,
            "name": sub_names.get(sub_id, "未归类子系统") if sub_id != UNCLASSIFIED else "未归类子系统",
            "pn_count": len(pns),
            "model_count": sum(p["model_count"] for _, p in pns),
            "field_count": sum(p["field_count"] for _, p in pns),
            "filled": sum(p["filled"] for _, p in pns),
            "pns": [p for _, p in pns],
        }

    for prod in products:
        pid = _text(prod.get("id"))
        subs_of_prod: dict[str, dict[str, dict]] = {}
        for (product, subsystem), pns in bucket.items():
            if product in (pid, UNCLASSIFIED) and product == pid:
                subs_of_prod.setdefault(subsystem, {}).update(pns)
                used.add((product, subsystem))
        if not subs_of_prod:
            continue
        sub_nodes = [_sub_node(sid, subs) for sid, subs in sorted(subs_of_prod.items())]
        tree_nodes.append(
            {
                "id": pid,
                "name": prod_names.get(pid, pid),
                "note": _text(prod.get("note"), 200),
                "subsystem_count": len(sub_nodes),
                "model_count": sum(s["model_count"] for s in sub_nodes),
                "field_count": sum(s["field_count"] for s in sub_nodes),
                "filled": sum(s["filled"] for s in sub_nodes),
                "subsystems": sub_nodes,
            }
        )

    # 未归类：产品为空或不在装备树中的模型
    loose: dict[str, dict[str, dict]] = {}
    for (product, subsystem), pns in bucket.items():
        if (product, subsystem) in used:
            continue
        loose.setdefault(product, {}).setdefault(subsystem, {}).update(pns)
    if loose:
        sub_nodes = []
        for product, subs in sorted(loose.items()):
            for sid, pns in sorted(subs.items()):
                sub_nodes.append(_sub_node(sid, pns))
        tree_nodes.append(
            {
                "id": UNCLASSIFIED,
                "name": "未归类（未挂接装备树）",
                "note": "导入时未指定产品/子系统，且 PN 前缀未命中子系统编号",
                "subsystem_count": len(sub_nodes),
                "model_count": sum(s["model_count"] for s in sub_nodes),
                "field_count": sum(s["field_count"] for s in sub_nodes),
                "filled": sum(s["filled"] for s in sub_nodes),
                "subsystems": sub_nodes,
            }
        )

    totals = {
        "pn_count": len({p for subs in bucket.values() for p in subs}),
        "model_count": sum(n["model_count"] for n in tree_nodes),
        "field_count": sum(n["field_count"] for n in tree_nodes),
        "filled": sum(n["filled"] for n in tree_nodes),
    }
    return {
        "version": ver,
        "versions": info["versions"],
        "active": info["active"],
        "tree": tree_nodes,
        "totals": totals,
        "tree_dir": str(_tree_dir()),
    }


def _tree_dir() -> Path:
    return Path(os.environ.get("ATE_TREE_DIR") or ((_BASE_DIR or Path(".")) / "tree"))


def models_of(pn: str, version: str = "") -> dict:
    ver = version or active_version()
    pn = _bbox_norm(pn)
    if not pn:
        raise ValueError("缺少 PN")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT m.*, "
            " (SELECT COUNT(*) FROM constant_value c WHERE c.version = m.version "
            "   AND c.pn = m.pn AND c.model_key = m.model_key) AS filled "
            "FROM constant_model m WHERE m.version = ? AND m.pn = ? ORDER BY m.model_key",
            (ver, pn),
        ).fetchall()
    models = []
    for r in rows:
        fields = json.loads(r["fields_json"] or "[]")
        models.append(
            {
                "model_key": r["model_key"],
                "name": r["name"],
                "category": r["category"],
                "description": r["description"],
                "unit": r["unit"],
                "field_count": r["field_count"],
                "filled": r["filled"],
                "struct_checksum": r["struct_checksum"],
                "updated_at": r["updated_at"],
                "fields": fields,
            }
        )
    return {"version": ver, "pn": pn, "models": models, "count": len(models)}


def model_values(version: str, pn: str, model_key: str) -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT field_key, value, unit, source, updated_at FROM constant_value "
            "WHERE version = ? AND pn = ? AND model_key = ?",
            (version, _bbox_norm(pn), model_key),
        ).fetchall()
    return {r["field_key"]: dict(r) for r in rows}


def set_values(
    version: str,
    pn: str,
    model_key: str,
    values: dict,
    *,
    source: str = "",
) -> dict:
    """写入 / 更新标定值（标定过程回填、手工补录）。"""
    pn = _bbox_norm(pn)
    with _LOCK:
        with _connect() as conn:
            model = conn.execute(
                "SELECT fields_json FROM constant_model "
                "WHERE version = ? AND pn = ? AND model_key = ?",
                (version, pn, model_key),
            ).fetchone()
            if not model:
                raise ValueError(f"常数模型不存在: {version}/{pn}/{model_key}")
            fields = {f["key"]: f for f in json.loads(model["fields_json"] or "[]")}
            unknown = [k for k in values if k not in fields]
            if unknown:
                raise ValueError(f"模型 {model_key} 不存在字段: {', '.join(unknown[:5])}")
            now = _now()
            for key, value in values.items():
                if value is None:
                    conn.execute(
                        "DELETE FROM constant_value WHERE version = ? AND pn = ? "
                        "AND model_key = ? AND field_key = ?",
                        (version, pn, model_key, key),
                    )
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO constant_value "
                    "(version, pn, model_key, field_key, value, unit, source, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        version,
                        pn,
                        model_key,
                        key,
                        str(value),
                        fields[key].get("unit", ""),
                        source or "manual",
                        now,
                    ),
                )
    return {"success": True, "filled": len(model_values(version, pn, model_key))}


def resolve_binding(binding: dict) -> dict:
    """按 TPS 绑定解析出「模型 + 值」，缺失字段以默认值兜底并单独列出。"""
    version = _text((binding or {}).get("version"), 48) or active_version()
    pn = _bbox_norm(_text((binding or {}).get("pn"), 64))
    wanted = (binding or {}).get("models") or []
    if not version or not pn:
        return {"version": version, "pn": pn, "models": [], "missing": [], "ok": False,
                "message": "TPS 未绑定常数版本或 PN"}

    with _connect() as conn:
        if wanted:
            marks = ",".join("?" * len(wanted))
            rows = conn.execute(
                f"SELECT * FROM constant_model WHERE version = ? AND pn = ? "
                f"AND model_key IN ({marks}) ORDER BY model_key",
                (version, pn, *wanted),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM constant_model WHERE version = ? AND pn = ? ORDER BY model_key",
                (version, pn),
            ).fetchall()
    found = {r["model_key"] for r in rows}
    not_found = [m for m in wanted if m not in found]

    models = []
    missing: list[dict] = []
    for r in rows:
        fields = json.loads(r["fields_json"] or "[]")
        stored = model_values(version, pn, r["model_key"])
        items = []
        for f in fields:
            hit = stored.get(f["key"])
            if hit:
                items.append({"field": f, "value": hit["value"], "filled": True,
                              "source": hit.get("source", ""), "updated_at": hit.get("updated_at", "")})
            else:
                items.append({"field": f, "value": f.get("default"), "filled": False,
                              "source": "default", "updated_at": ""})
                missing.append({"model_key": r["model_key"], "field_key": f["key"],
                                "label": f.get("label", f["key"])})
        models.append(
            {
                "model_key": r["model_key"],
                "name": r["name"],
                "unit": r["unit"],
                "fields": items,
                "filled": sum(1 for i in items if i["filled"]),
                "field_count": len(items),
            }
        )
    return {
        "version": version,
        "pn": pn,
        "models": models,
        "missing": missing,
        "not_found_models": not_found,
        "ok": not not_found,
        "totals": {
            "model_count": len(models),
            "field_count": sum(m["field_count"] for m in models),
            "filled": sum(m["filled"] for m in models),
        },
    }


# ---------------------------------------------------------------- XML


def build_xml(binding: dict, resolved: dict, header: dict) -> str:
    """按绑定生成机器常数 XML（UTF-8，缩进 2）。"""
    root = ET.Element(
        "MachineConstants",
        {
            "schema": "ate.constants.v1",
            "version": resolved.get("version", ""),
            "pn": resolved.get("pn", ""),
            "generatedAt": _now(),
        },
    )
    h = ET.SubElement(root, "Header")
    for key in ("tpsId", "tpsName", "tpsVersion", "uut", "taskId", "runMode", "deviceMode",
                "benchId", "operator", "workOrder", "startedAt", "finishedAt", "result"):
        ET.SubElement(h, "Item", {"name": key, "value": _text(header.get(key), 200)})

    sets = ET.SubElement(root, "ConstantSets", {"count": str(len(resolved.get("models") or []))})
    for model in resolved.get("models") or []:
        node = ET.SubElement(
            sets,
            "ConstantSet",
            {
                "modelKey": model["model_key"],
                "name": model.get("name", ""),
                "unit": model.get("unit", ""),
                "filled": f"{model.get('filled', 0)}/{model.get('field_count', 0)}",
            },
        )
        for item in model.get("fields") or []:
            f = item["field"]
            el = ET.SubElement(
                node,
                "Field",
                {
                    "key": f["key"],
                    "label": f.get("label", ""),
                    "type": f.get("type", ""),
                    "unit": f.get("unit", ""),
                    "source": item.get("source", ""),
                    "calibrated": "true" if item.get("filled") else "false",
                },
            )
            el.text = "" if item.get("value") is None else str(item.get("value"))
            if item.get("updated_at"):
                el.set("updatedAt", item["updated_at"])

    totals = resolved.get("totals") or {}
    summary = ET.SubElement(
        root,
        "Summary",
        {
            "models": str(totals.get("model_count", 0)),
            "fields": str(totals.get("field_count", 0)),
            "filled": str(totals.get("filled", 0)),
            "missing": str(len(resolved.get("missing") or [])),
        },
    )
    for miss in resolved.get("missing") or []:
        ET.SubElement(
            summary,
            "Missing",
            {"modelKey": miss["model_key"], "key": miss["field_key"], "label": miss.get("label", "")},
        )

    _indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def _indent(elem: ET.Element, level: int = 0) -> None:
    pad = "\n" + "  " * level
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = pad + "  "
        for child in elem:
            _indent(child, level + 1)
        if not (child.tail or "").strip():
            child.tail = pad
    if level and not (elem.tail or "").strip():
        elem.tail = pad


def write_run_xml(task: dict, tps: dict, report_dir: Path) -> Optional[dict]:
    """测试完成后按 TPS 绑定生成常数 XML；未绑定则返回 None。"""
    binding = (tps or {}).get("constant_model") or {}
    if not binding or not binding.get("pn"):
        return None
    resolved = resolve_binding(binding)
    header = {
        "tpsId": tps.get("id", ""),
        "tpsName": tps.get("name", ""),
        "tpsVersion": tps.get("version", ""),
        "uut": task.get("uut", ""),
        "taskId": task.get("id", ""),
        "runMode": task.get("run_mode", ""),
        "deviceMode": task.get("mode", ""),
        "benchId": task.get("bench_id", ""),
        "operator": task.get("operator", "") or "",
        "workOrder": task.get("work_order", ""),
        "startedAt": task.get("start_time", ""),
        "finishedAt": task.get("end_time", ""),
        "result": "OK" if task.get("status") == "completed" else "NOK",
    }
    xml = build_xml(binding, resolved, header)
    out_dir = Path(report_dir) / "constants"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"constants_{task.get('id', '')}.xml"
    (out_dir / name).write_text(xml, encoding="utf-8")
    return {
        "file": f"reports/constants/{name}",
        "path": str(out_dir / name),
        "version": resolved.get("version", ""),
        "pn": resolved.get("pn", ""),
        "models": (resolved.get("totals") or {}).get("model_count", 0),
        "filled": (resolved.get("totals") or {}).get("filled", 0),
        "missing": len(resolved.get("missing") or []),
    }


# ---------------------------------------------------------------- TPS 绑定


def _tps_json_path(tps_id: str) -> Optional[Path]:
    if not _TPS_DIR:
        return None
    pack = Path(_TPS_DIR) / tps_id / "tps.json"
    if pack.exists():
        return pack
    single = Path(_TPS_DIR) / f"{tps_id}.json"
    if single.exists():
        return single
    return None


def read_binding(tps_id: str) -> dict:
    path = _tps_json_path(tps_id)
    if not path:
        raise ValueError(f"TPS 不存在: {tps_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    binding = raw.get("constant_model") or {}
    return {"success": True, "tps_id": tps_id, "binding": binding, "file": str(path)}


def save_binding(tps_id: str, binding: dict) -> dict:
    """把常数模型关联写回 TPS 清单（`constant_model` 字段）。"""
    path = _tps_json_path(tps_id)
    if not path:
        raise ValueError(f"TPS 不存在: {tps_id}")
    binding = binding or {}
    if not binding:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("constant_model", None)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"success": True, "binding": {}, "message": "已解除常数模型关联"}

    version = _text(binding.get("version"), 48) or active_version()
    pn = _bbox_norm(_text(binding.get("pn"), 64))
    models = [_text(m, 64) for m in (binding.get("models") or []) if _text(m, 64)]
    if not version or not pn:
        raise ValueError("关联常数模型需要指定版本与 PN")

    with _connect() as conn:
        rows = conn.execute(
            "SELECT model_key FROM constant_model WHERE version = ? AND pn = ?",
            (version, pn),
        ).fetchall()
    available = {r["model_key"] for r in rows}
    if not available:
        raise ValueError(f"常数版本 {version} 下不存在 PN={pn} 的常数模型")
    unknown = [m for m in models if m not in available]
    if unknown:
        raise ValueError(f"PN={pn} 下不存在常数模型: {', '.join(unknown[:5])}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["constant_model"] = {
        "version": version,
        "pn": pn,
        "models": models or sorted(available),
        "updated_at": _now(),
    }
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "success": True,
        "binding": raw["constant_model"],
        "message": f"已关联常数模型：{version} / {pn}（{len(raw['constant_model']['models'])} 个模型）",
    }


# ---------------------------------------------------------------- 路由


def register_routes(app, base_dir: Path, tps_dir: Path, db_path: Optional[Path] = None) -> None:
    global _BASE_DIR, _TPS_DIR, _DB_PATH
    _BASE_DIR = Path(base_dir)
    _TPS_DIR = Path(tps_dir)
    _DB_PATH = Path(db_path) if db_path else ate_db.DB_PATH
    ate_db.init_db(_DB_PATH)

    from fastapi import Body

    @app.get("/api/constants/overview")
    def constants_overview(version: str = ""):
        try:
            data = tree(version)
            return {"success": True, **data}
        except ValueError as exc:
            return {"success": False, "message": str(exc), "tree": []}
        except Exception as exc:  # pragma: no cover - 兜底
            return {"success": False, "message": f"读取常数模型失败: {exc}", "tree": []}

    @app.get("/api/constants/versions")
    def constants_versions():
        info = list_versions()
        return {"success": True, **info}

    @app.post("/api/constants/versions/active")
    def constants_set_active(payload: dict = Body(...)):
        try:
            return set_active_version(_text((payload or {}).get("version"), 48))
        except ValueError as exc:
            return {"success": False, "message": str(exc)}

    @app.get("/api/constants/models")
    def constants_models(pn: str = "", version: str = ""):
        try:
            return {"success": True, **models_of(pn, version)}
        except ValueError as exc:
            return {"success": False, "message": str(exc), "models": []}

    @app.post("/api/constants/import")
    def constants_import(payload: dict = Body(...)):
        """全量导入：body = {content|content_base64, filename, mode, operator, keep_values}"""
        payload = payload or {}
        content = payload.get("content")
        b64 = payload.get("content_base64")
        filename = _text(payload.get("filename"), 200)
        mode = _text(payload.get("mode"), 16) or "overwrite"
        operator = _text(payload.get("operator"), 64)
        keep_values = bool(payload.get("keep_values", True))
        try:
            if b64:
                import base64

                raw_bytes = base64.b64decode(b64)
                if filename.lower().endswith(".zip") or raw_bytes[:2] == b"PK":
                    return _import_zip(raw_bytes, mode=mode, operator=operator,
                                       keep_values=keep_values)
                content = raw_bytes
            if content is None:
                return {"success": False, "message": "缺少 content 或 content_base64"}
            return import_from_text(content, mode=mode, operator=operator,
                                    keep_values=keep_values)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
        except Exception as exc:  # pragma: no cover
            return {"success": False, "message": f"导入失败: {exc}"}

    @app.post("/api/constants/preview")
    def constants_preview(payload: dict = Body(...)):
        """导入前预览：只解析不落库，返回版本/PN/模型/冲突信息。"""
        payload = payload or {}
        content = payload.get("content")
        b64 = payload.get("content_base64")
        filename = _text(payload.get("filename"), 200)
        try:
            if b64:
                import base64

                raw_bytes = base64.b64decode(b64)
                if filename.lower().endswith(".zip") or raw_bytes[:2] == b"PK":
                    bundle = _parse_zip(raw_bytes)
                else:
                    bundle = parse_bundle(raw_bytes)
            elif content is not None:
                bundle = parse_bundle(content)
            else:
                return {"success": False, "message": "缺少 content 或 content_base64"}
        except ValueError as exc:
            return {"success": False, "message": str(exc)}

        info = list_versions()
        exist = next((v for v in info["versions"] if v["version"] == bundle["version"]), None)
        with _connect() as conn:
            existing_models = conn.execute(
                "SELECT COUNT(*) AS n FROM constant_model WHERE version = ?", (bundle["version"],)
            ).fetchone()["n"]
        pns = [
            {
                "pn": g["pn"],
                "product": g["product"],
                "subsystem": g["subsystem"],
                "model_count": len(g["models"]),
                "field_count": sum(len(m["fields"]) for m in g["models"]),
                "models": [
                    {
                        "model_key": m["model_key"],
                        "name": m["name"],
                        "field_count": len(m["fields"]),
                        "fields": [f["key"] for f in m["fields"]],
                    }
                    for m in g["models"]
                ],
            }
            for g in bundle["groups"]
        ]
        return {
            "success": True,
            "version": bundle["version"],
            "label": bundle["label"],
            "source": bundle["source"],
            "note": bundle["note"],
            "stats": {
            **bundle["stats"],
            "value_count": sum(len(m.get("values") or {}) for g in bundle["groups"]
                               for m in g["models"]),
        },
            "pns": pns,
            "conflict": bool(exist),
            "existing": exist,
            "existing_models": existing_models,
            "filename": filename,
        }

    @app.get("/api/constants/pn")
    def constants_pn(pn: str, version: str = ""):
        """某 PN 下全部模型 + 标定值（编辑 TPS 时选择关联用）。"""
        try:
            data = models_of(pn, version)
        except ValueError as exc:
            return {"success": False, "message": str(exc), "models": []}
        for m in data["models"]:
            m["values"] = model_values(data["version"], data["pn"], m["model_key"])
        return {"success": True, **data}

    @app.post("/api/constants/values")
    def constants_values(payload: dict = Body(...)):
        payload = payload or {}
        try:
            version = _text(payload.get("version"), 48) or active_version()
            pn = _text(payload.get("pn"), 64)
            model_key = _text(payload.get("model_key"), 64)
            values = payload.get("values") or {}
            if not isinstance(values, dict):
                return {"success": False, "message": "values 必须是 {字段: 值} 对象"}
            res = set_values(version, pn, model_key, values,
                             source=_text(payload.get("source"), 64) or "manual")
            return {**res, "version": version, "pn": _bbox_norm(pn), "model_key": model_key}
        except ValueError as exc:
            return {"success": False, "message": str(exc)}

    @app.post("/api/constants/resolve")
    def constants_resolve(payload: dict = Body(...)):
        """按绑定解析填充值（模拟测试过程中的填充结果）。"""
        try:
            return {"success": True, **resolve_binding(payload or {})}
        except Exception as exc:  # pragma: no cover
            return {"success": False, "message": str(exc)}

    @app.post("/api/constants/xml")
    def constants_xml(payload: dict = Body(...)):
        """生成 XML 预览（不落盘）。body = {binding, header}"""
        payload = payload or {}
        try:
            resolved = resolve_binding(payload.get("binding") or {})
            if not resolved.get("pn"):
                return {"success": False, "message": resolved.get("message", "未绑定常数模型")}
            xml = build_xml(payload.get("binding") or {}, resolved, payload.get("header") or {})
            return {"success": True, "xml": xml, "filename": f"constants_{resolved['pn']}.xml"}
        except Exception as exc:  # pragma: no cover
            return {"success": False, "message": str(exc)}

    @app.get("/api/tps/{tps_id}/constants")
    def tps_constants(tps_id: str):
        try:
            info = read_binding(tps_id)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
        binding = info["binding"] or {}
        resolved = resolve_binding(binding) if binding.get("pn") else None
        return {"success": True, "tps_id": tps_id, "binding": binding,
                "resolved": resolved, "file": info["file"]}

    @app.put("/api/tps/{tps_id}/constants")
    def tps_constants_save(tps_id: str, payload: dict = Body(...)):
        try:
            res = save_binding(tps_id, (payload or {}).get("binding") or payload or {})
            return res
        except ValueError as exc:
            return {"success": False, "message": str(exc)}


# ---------------------------------------------------------------- ZIP


def _parse_zip(raw: bytes) -> dict:
    """ZIP 全量包：取包内所有 .json（优先 constants.json / manifest.json）合并解析。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError("压缩包不是有效的 ZIP 文件")
    names = [n for n in zf.namelist() if n.lower().endswith(".json")
             and not n.startswith("__MACOSX")]
    if not names:
        raise ValueError("压缩包内没有 JSON 文件")
    # 优先单一入口文件
    entry = next((n for n in names if Path(n).name.lower() in
                  ("constants.json", "manifest.json", "machine_constants.json")), None)
    if entry:
        return parse_bundle(zf.read(entry))

    merged: dict = {"groups": []}
    for name in names:
        try:
            data = json.loads(zf.read(name).decode("utf-8-sig"))
        except Exception as exc:
            raise ValueError(f"压缩包内 {name} 不是有效 JSON: {exc}")
        if isinstance(data, list):
            data = {"models": data}
        if not isinstance(data, dict):
            raise ValueError(f"压缩包内 {name} 结构不支持")
        for key in ("version", "label", "source", "note"):
            if data.get(key) and not merged.get(key):
                merged[key] = data[key]
        if data.get("groups") or data.get("pn_groups"):
            merged["groups"].extend(data.get("groups") or data.get("pn_groups") or [])
        elif data.get("models"):
            merged.setdefault("models", []).extend(data["models"])
    if not merged["groups"]:
        return parse_bundle(merged)
    return parse_bundle(merged)


def _import_zip(raw: bytes, *, mode: str = "overwrite", operator: str = "",
                keep_values: bool = True) -> dict:
    bundle = _parse_zip(raw)
    return import_bundle(bundle, mode=mode, operator=operator, keep_values=keep_values)
