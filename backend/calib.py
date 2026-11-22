# -*- coding: utf-8 -*-
"""MockRunner 标定模块（SR02-AR06 / SR02-AR07 / SR02-AR08）。

职责
----
1. 本机标定：读取常数模型结构（`<Module>.xml` 或传入 catalog），把实测值按字段写入，
   生成 `<SN>_<Module>.xml` 标定实例，校验范围，算 sha256 并归档到 `const_calibration`。
2. 下位机标定：通过 paramiko SSH 登录目标机，按其数据库方言（sqlite / pgsql）执行只读查询，
   按字段映射（source_field -> target_field，支持 scale / offset）换算，生成一个或多个常数文件。
3. 连接配置：`<base_dir>/remote_targets.json`，口令与私钥路径做「机器绑定 + base64」简单混淆，
   读取接口不明文回显；数据库类型只允许 sqlite / pgsql。
4. 连通性测试：sqlite 校验本地文件并跑一次 count；pgsql 走 SSH 执行 select 1；返回往返毫秒。

对外契约（见 docs/需求设计/机器常数实现契约.md 第五节）
------------------------------------------------------
    build_local(sn, pn, model_key, values, header, out_dir) -> dict
    fetch_remote(target, query, mapping, out_dir) -> dict
    register_routes(app, base_dir, db_path=None) -> None

约定
----
* 所有对外函数不抛异常，统一返回 ``{"success": bool, "message": str, ...}``；
  失败时额外带 ``error_code``（远程错误码：remote_connect_failed / remote_auth_failed /
  remote_query_timeout / remote_empty_rows / mapping_missing 等）。
* paramiko 为可选依赖：真实环境未安装时给出明确错误，测试通过 ``_import_paramiko`` 打桩。
* 写库仅写 ``const_calibration``（表不存在时按契约列幂等创建），不改其它表。
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import platform
import re
import shlex
import socket
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from xml.etree import ElementTree as ET

SCHEMA = "ate.constants.v2"

SN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
FIELD_TYPES = ("float", "int", "str", "bool", "enum")
DB_KINDS = ("sqlite", "pgsql")
AUTH_KINDS = ("password", "key")
LOCAL_HOSTS = ("", "local", "localhost", "127.0.0.1", "::1")

# 这些 target_field 视为元数据而不是模型字段
META_TARGETS = {"sn", "pn", "module", "model_key", "modelkey"}

# 运行期由 configure / register_routes 注入；默认取 backend 目录，便于 Demo 直接可用
_BASE_DIR: Path = Path(__file__).resolve().parent
_DB_PATH: Optional[Path] = None

_CALIBRATION_DDL = """
CREATE TABLE IF NOT EXISTS const_calibration (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sn          TEXT,
    pn          TEXT,
    model_key   TEXT,
    version     TEXT,
    values_json TEXT,
    source_tps  TEXT,
    task_id     TEXT,
    operator    TEXT,
    checksum    TEXT,
    created_at  TEXT
)
"""


# ------------------------------------------------------------------ 基础工具


class CalibError(Exception):
    """带错误码的业务异常；由对外函数转成 success=false 的 JSON。"""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def to_dict(self) -> dict:
        data = {
            "success": False,
            "error_code": self.code,
            "code": self.code,
            "message": self.message,
        }
        data.update(self.extra)
        return data


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _text(value: Any, limit: int = 400) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _as_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _fail(exc: Exception, prefix: str) -> dict:
    if isinstance(exc, CalibError):
        return exc.to_dict()
    return {"success": False, "message": f"{prefix}：{exc}"}


def configure(base_dir: Any = None, db_path: Any = None) -> None:
    """设置模型目录与归档库路径（register_routes 会自动调用）。"""
    global _BASE_DIR, _DB_PATH
    if base_dir:
        _BASE_DIR = Path(base_dir).expanduser().resolve()
    if db_path:
        _DB_PATH = Path(db_path).expanduser().resolve()


# ------------------------------------------------------------------ 模型结构


def _fmt_num(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return f"{value:.10g}"
    return str(value)


def _to_number(value: Any, kind: str) -> Any:
    """把模板里的字符串数值转成 float/int；失败保留原值，由校验阶段报错。"""
    if value is None or value == "":
        return None
    try:
        if kind == "int":
            return int(float(value))
        if kind == "float":
            return float(value)
    except (TypeError, ValueError):
        return value
    return value


def _norm_field(raw: Any, index: int) -> dict:
    if not isinstance(raw, dict):
        raise CalibError("model_invalid", f"模型第 {index + 1} 个字段定义不是对象")
    key = _text(raw.get("key") or raw.get("name") or raw.get("id"), 64)
    if not key:
        raise CalibError("model_invalid", f"模型第 {index + 1} 个字段缺少 key")
    if not MODULE_RE.match(key):
        raise CalibError("model_invalid", f"字段 key 不合法：{key}")

    options: list[str] = []
    raw_options = raw.get("values", raw.get("options"))
    if isinstance(raw_options, str):
        options = [p.strip() for p in raw_options.split(",") if p.strip()]
    elif isinstance(raw_options, (list, tuple)):
        options = [_text(p, 64) for p in raw_options if _text(p, 64)]

    ftype = _text(raw.get("type"), 16).lower()
    if ftype not in FIELD_TYPES:
        if options:
            ftype = "enum"
        elif raw.get("min") is not None or raw.get("max") is not None:
            ftype = "float"
        else:
            ftype = "str"

    default = raw.get("default")
    if ftype in ("float", "int"):
        default = _to_number(default, ftype)
    elif ftype == "bool" and isinstance(default, str):
        default = default.strip().lower() in ("true", "1", "yes", "是")

    return {
        "key": key,
        "label": _text(raw.get("label") or raw.get("title") or key, 120),
        "type": ftype,
        "unit": _text(raw.get("unit"), 32),
        "default": default,
        "min": _to_number(raw.get("min"), "float") if ftype in ("float", "int") else None,
        "max": _to_number(raw.get("max"), "float") if ftype in ("float", "int") else None,
        "values": options,
    }


def _parse_model_xml(text: str, fallback_key: str = "") -> dict:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise CalibError("model_invalid", f"模型 XML 解析失败：{exc}") from exc
    if root.tag != "MachineConstants":
        raise CalibError("model_invalid", f"模型 XML 根节点必须是 MachineConstants，实际为 {root.tag}")

    module = _text(root.get("module") or fallback_key, 64)
    if not module:
        raise CalibError("model_invalid", "模型 XML 缺少 module 属性")

    meta_node = root.find("Meta")
    meta = dict(meta_node.attrib) if meta_node is not None else {}

    field_nodes = root.findall("./Fields/Field")
    if not field_nodes:
        field_nodes = root.findall(".//Fields/Field")
    if not field_nodes:
        raise CalibError("model_invalid", f"模型 {module} 未定义任何 Field")

    fields = [_norm_field(node.attrib, idx) for idx, node in enumerate(field_nodes)]
    return {
        "module": module,
        "pn": _text(root.get("pn"), 64),
        "version": _text(root.get("version"), 48),
        "name": _text(meta.get("name"), 120) or module,
        "product": _text(meta.get("product"), 64),
        "subsystem": _text(meta.get("subsystem"), 64),
        "fields": fields,
        "source": "xml",
    }


def _norm_model(raw: Any, fallback_key: str = "") -> dict:
    if not isinstance(raw, dict):
        raise CalibError("model_invalid", "模型定义必须是对象")
    module = _text(
        raw.get("module") or raw.get("model_key") or raw.get("key") or fallback_key, 64
    )
    if not module:
        raise CalibError("model_invalid", "模型定义缺少 module")
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    raw_fields = raw.get("fields")
    if not isinstance(raw_fields, list):
        raise CalibError("model_invalid", f"模型 {module} 缺少 fields 列表")
    fields = [_norm_field(item, idx) for idx, item in enumerate(raw_fields)]
    return {
        "module": module,
        "pn": _text(raw.get("pn"), 64),
        "version": _text(raw.get("version"), 48),
        "name": _text(raw.get("name") or meta.get("name"), 120) or module,
        "product": _text(raw.get("product") or meta.get("product"), 64),
        "subsystem": _text(raw.get("subsystem") or meta.get("subsystem"), 64),
        "fields": fields,
        "source": "catalog",
    }


def _collect_catalog(catalog: Any) -> dict[str, Any]:
    """把多种 catalog 形态归一为 {model_key: raw_model}。"""
    out: dict[str, Any] = {}
    if not catalog:
        return out
    if isinstance(catalog, list):
        for item in catalog:
            if isinstance(item, dict):
                key = _text(item.get("module") or item.get("model_key") or item.get("key"), 64)
                if key:
                    out[key] = item
        return out
    if isinstance(catalog, dict):
        if "fields" in catalog:
            key = _text(catalog.get("module") or catalog.get("model_key") or catalog.get("key"), 64)
            if key:
                out[key] = catalog
            return out
        if "models" in catalog:
            out.update(_collect_catalog(catalog.get("models")))
            return out
        for key, value in catalog.items():
            if isinstance(value, dict):
                out[_text(key, 64)] = value
    return out


def _model_search_paths(model_key: str) -> list[Path]:
    base = _BASE_DIR
    names = [f"{model_key}.xml", f"{model_key}.XML"]
    candidates: list[Path] = []
    for folder in (
        base / "testresource" / "calib_demo",
        base / "testresource" / "models",
        base / "testresource",
        base / "calib_demo",
    ):
        for name in names:
            candidates.append(folder / name)
    # 兜底：testresource 下深层子目录（限次，避免遍历大目录）
    try:
        for path in list((base / "testresource").rglob("*")):
            if path.is_file() and path.name.lower() == f"{model_key}.xml".lower():
                candidates.append(path)
    except OSError:
        pass
    return candidates


def _load_model(pn: str, model_key: str, catalog: Any = None) -> dict:
    key = _text(model_key, 64)
    models = _collect_catalog(catalog)
    if key and key in models:
        model = _norm_model(models[key], key)
    elif not key and len(models) == 1:
        only_key = next(iter(models))
        model = _norm_model(models[only_key], only_key)
        key = only_key
    else:
        model = None

    if model is None and key:
        for path in _model_search_paths(key):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise CalibError("model_invalid", f"模型文件读取失败：{path}（{exc}）") from exc
            model = _parse_model_xml(text, key)
            model["path"] = str(path)
            break

    if model is None:
        raise CalibError(
            "model_not_found",
            f"未找到常数模型 {key or '(空)'}（PN={pn or '-'}）：请确认 "
            f"testresource/calib_demo/{key or 'Module'}.xml 存在，或通过 catalog 传入模型结构",
        )

    if not MODULE_RE.match(model["module"]):
        raise CalibError("model_invalid", f"模型 module 名不合法，不能用于文件名：{model['module']}")

    if pn and model.get("pn") and _text(pn) != model["pn"]:
        raise CalibError(
            "pn_mismatch",
            f"PN 不匹配：请求 PN={pn}，模型 {model['module']} 的 PN={model['pn']}",
        )
    return model


# ------------------------------------------------------------------ 值校验


def _values_to_dict(values: Any) -> dict:
    if values is None:
        return {}
    if isinstance(values, dict):
        return dict(values)
    if isinstance(values, list):
        out: dict[str, Any] = {}
        for item in values:
            if isinstance(item, dict):
                key = _text(item.get("key") or item.get("field"), 64)
                if key:
                    out[key] = item.get("value")
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                out[_text(item[0], 64)] = item[1]
        return out
    raise CalibError("invalid_values", "values 必须是对象（字段键 -> 值）或 [{key,value}] 列表")


def _coerce_value(field: dict, raw: Any) -> tuple[bool, Any, str]:
    ftype = field["type"]
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return False, None, "缺少值"

    if ftype in ("float", "int"):
        try:
            number = float(str(raw).strip())
        except (TypeError, ValueError):
            return False, None, f"不是合法{'整数' if ftype == 'int' else '数值'}：{raw}"
        if ftype == "int" and not number.is_integer():
            return False, None, f"不是整数：{raw}"
        value: Any = int(number) if ftype == "int" else number
        low, high = field.get("min"), field.get("max")
        if low is not None and value < float(low):
            return False, None, f"{_fmt_num(value)} 小于下限 {_fmt_num(low)}"
        if high is not None and value > float(high):
            return False, None, f"{_fmt_num(value)} 大于上限 {_fmt_num(high)}"
        return True, value, ""

    if ftype == "bool":
        text = str(raw).strip().lower()
        if text in ("true", "1", "yes", "y", "on", "是", "开"):
            return True, True, ""
        if text in ("false", "0", "no", "n", "off", "否", "关"):
            return True, False, ""
        return False, None, f"不是布尔值：{raw}"

    if ftype == "enum":
        text = str(raw).strip()
        options = field.get("values") or []
        if options and text not in options:
            return False, None, f"不在枚举范围 [{', '.join(options)}] 内：{text}"
        return True, text, ""

    return True, str(raw), ""


# ------------------------------------------------------------------ XML 生成


def _render_xml(model: dict, sn: str, values: dict, header: dict) -> str:
    header = header if isinstance(header, dict) else {}
    at = _now()
    root = ET.Element(
        "MachineConstants",
        {
            "schema": SCHEMA,
            "module": model["module"],
            "pn": model.get("pn", ""),
            "version": model.get("version", ""),
            "calibrated": "true",
            "sn": sn,
            "generatedAt": at,
        },
    )
    ET.SubElement(
        root,
        "Meta",
        {
            "product": model.get("product", ""),
            "subsystem": model.get("subsystem", ""),
            "name": model.get("name", model["module"]),
        },
    )
    fields_node = ET.SubElement(root, "Fields")
    for field in model["fields"]:
        attrs = {
            "key": field["key"],
            "label": field["label"],
            "type": field["type"],
            "unit": field["unit"],
        }
        if field.get("default") is not None:
            attrs["default"] = _fmt_num(field["default"])
        if field["type"] in ("float", "int"):
            if field.get("min") is not None:
                attrs["min"] = _fmt_num(field["min"])
            if field.get("max") is not None:
                attrs["max"] = _fmt_num(field["max"])
        if field["type"] == "enum" and field.get("values"):
            attrs["values"] = ",".join(field["values"])
        attrs["value"] = _fmt_num(values[field["key"]])
        ET.SubElement(fields_node, "Field", attrs)

    ET.SubElement(
        root,
        "Calibration",
        {
            "sn": sn,
            "operator": _text(header.get("operator") or header.get("user"), 64),
            "tpsId": _text(header.get("tpsId") or header.get("tps_id") or header.get("source_tps"), 128),
            "taskId": _text(header.get("taskId") or header.get("task_id"), 128),
            "at": at,
        },
    )
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


# ------------------------------------------------------------------ 归档


def _archive_calibration(
    db_path: Optional[Path],
    *,
    sn: str,
    pn: str,
    model_key: str,
    version: str,
    values: dict,
    header: dict,
    checksum: str,
) -> tuple[Optional[int], str]:
    if not db_path:
        return None, ""
    path = Path(db_path)
    try:
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=5)
        try:
            conn.execute(_CALIBRATION_DDL)
            cur = conn.execute(
                "INSERT INTO const_calibration "
                "(sn, pn, model_key, version, values_json, source_tps, task_id, operator, checksum, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sn,
                    pn,
                    model_key,
                    version,
                    json.dumps(values, ensure_ascii=False, sort_keys=True),
                    _text(header.get("source_tps") or header.get("tpsId"), 128),
                    _text(header.get("taskId"), 128),
                    _text(header.get("operator"), 64),
                    checksum,
                    _now(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid), ""
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - 归档失败不阻断文件生成，但要回传原因
        return None, str(exc)


# ------------------------------------------------------------------ 本机标定


def _emit(
    *,
    sn: str,
    pn: str,
    model: dict,
    values: dict,
    header: dict,
    out_dir: Any,
    db_path: Optional[Path],
) -> dict:
    xml_text = _render_xml(model, sn, values, header)
    payload = xml_text.encode("utf-8")
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{sn}_{model['module']}.xml"
    tmp = folder / f".{target.name}.tmp"
    tmp.write_bytes(payload)
    os.replace(tmp, target)

    checksum = hashlib.sha256(payload).hexdigest()
    record_header = header if isinstance(header, dict) else {}
    record_id, archive_error = _archive_calibration(
        db_path,
        sn=sn,
        pn=model.get("pn") or pn,
        model_key=model["module"],
        version=model.get("version", ""),
        values=values,
        header=record_header,
        checksum=checksum,
    )
    message = f"已生成标定文件 {target.name}"
    if archive_error:
        message += f"；但写 const_calibration 失败：{archive_error}"
    return {
        "success": True,
        "message": message,
        "path": str(target),
        "file": target.name,
        "out_dir": str(folder),
        "sn": sn,
        "pn": model.get("pn") or pn,
        "model_key": model["module"],
        "version": model.get("version", ""),
        "values": values,
        "checksum": checksum,
        "calibration_id": record_id,
        "archived": record_id is not None,
        "archive_error": archive_error or None,
    }


def _build_local(
    sn: str,
    pn: str,
    model_key: str,
    values: Any,
    header: Any,
    out_dir: Any,
    catalog: Any,
    db_path: Optional[Path],
) -> dict:
    sn = _text(sn, 64)
    if not sn:
        raise CalibError("invalid_sn", "缺少被测对象 SN")
    if not SN_RE.match(sn):
        raise CalibError("invalid_sn", f"SN 不合法（字母/数字/._-，最长 64）：{sn}")

    model = _load_model(_text(pn, 64), model_key, catalog)
    supplied = _values_to_dict(values)
    header = header if isinstance(header, dict) else {}

    known = {field["key"] for field in model["fields"]}
    unknown = [key for key in supplied if key not in known]
    if unknown:
        raise CalibError(
            "unknown_fields",
            f"标定值包含模型未定义的字段：{', '.join(unknown)}",
            unknown_fields=unknown,
            accepted=sorted(known),
        )

    violations: list[dict] = []
    missing: list[str] = []
    effective: dict[str, Any] = {}
    written: list[dict] = []
    for field in model["fields"]:
        if field["key"] in supplied:
            raw = supplied[field["key"]]
            source = "input"
        else:
            raw = field.get("default")
            source = "default"
        ok, value, reason = _coerce_value(field, raw)
        if not ok:
            if source == "default" and reason == "缺少值":
                missing.append(field["key"])
                continue
            violations.append(
                {
                    "key": field["key"],
                    "label": field["label"],
                    "value": raw,
                    "type": field["type"],
                    "min": field.get("min"),
                    "max": field.get("max"),
                    "reason": reason,
                }
            )
            continue
        effective[field["key"]] = value
        written.append(
            {
                "key": field["key"],
                "label": field["label"],
                "value": value,
                "unit": field["unit"],
                "source": source,
            }
        )

    if violations:
        detail = "；".join(f"{v['key']}={v['value']}（{v['reason']}）" for v in violations)
        raise CalibError(
            "value_out_of_range",
            f"标定值校验未通过，{len(violations)} 个字段不合规：{detail}",
            violations=violations,
        )
    if missing:
        raise CalibError(
            "missing_values",
            f"以下字段既未给值也没有默认值：{', '.join(missing)}",
            missing_fields=missing,
        )

    result = _emit(
        sn=sn,
        pn=_text(pn, 64),
        model=model,
        values=effective,
        header=header,
        out_dir=out_dir,
        db_path=db_path,
    )
    result["written"] = written
    result["applied"] = {key: effective[key] for key in supplied if key in effective}
    result["defaulted"] = [item["key"] for item in written if item["source"] == "default"]
    return result


def build_local(
    sn: str,
    pn: str,
    model_key: str,
    values: dict,
    header: dict,
    out_dir: Path,
    *,
    catalog: Any = None,
    db_path: Any = None,
) -> dict:
    """本机标定：生成 ``<SN>_<Module>.xml``。

    参数
    ----
    catalog : 可选。模型结构（dict / list / {model_key: model}）；缺省从 base_dir
        下 ``testresource`` 找 ``<model_key>.xml``。
    db_path : 可选。归档库路径；缺省用 register_routes 注入的路径，再无则跳过归档。
    """
    try:
        target_db = Path(db_path).expanduser().resolve() if db_path else _DB_PATH
        return _build_local(sn, pn, model_key, values, header, out_dir, catalog, target_db)
    except Exception as exc:  # noqa: BLE001 - 对外统一 JSON
        return _fail(exc, "本机标定失败")


# ------------------------------------------------------------------ 目标机配置


def _machine_key() -> bytes:
    seed = "|".join(
        [
            "ate.calib.v1",
            platform.node() or "",
            str(uuid.getnode()),
            os.environ.get("COMPUTERNAME", ""),
            os.environ.get("USERNAME", ""),
        ]
    )
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _xor(raw: bytes) -> bytes:
    key = _machine_key()
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(raw))


def _obfuscate(secret: str) -> str:
    """机器绑定 + 校验尾的简单混淆（非强加密，仅避免明文落盘/回显）。"""
    payload = str(secret).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16].encode("ascii")
    blob = _xor(payload + b"|" + digest)
    return "enc:v1:" + base64.urlsafe_b64encode(blob).decode("ascii")


def _deobfuscate(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    if not text.startswith("enc:v1:"):
        return text  # 兼容历史明文配置
    try:
        blob = base64.urlsafe_b64decode(text[len("enc:v1:") :].encode("ascii"))
        plain = _xor(blob)
        payload, _, digest = plain.rpartition(b"|")
        if hashlib.sha256(payload).hexdigest()[:16].encode("ascii") != digest:
            raise ValueError("digest mismatch")
        return payload.decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise CalibError(
            "credential_unreadable",
            "凭据无法在当前机器解密（配置可能来自另一台机器或已损坏），请重新填写",
        ) from exc


def _targets_path() -> Path:
    return Path(_BASE_DIR) / "remote_targets.json"


def _load_targets() -> list[dict]:
    path = _targets_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CalibError("config_invalid", f"下位机配置文件读取失败：{path}（{exc}）") from exc
    if isinstance(data, dict):
        data = data.get("targets", [])
    if not isinstance(data, list):
        raise CalibError("config_invalid", f"下位机配置文件格式错误：{path}")
    return [item for item in data if isinstance(item, dict)]


def _save_targets(targets: list[dict]) -> None:
    path = _targets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"targets": targets}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _public_view(record: dict) -> dict:
    """对外视图：绝不回显口令、密钥路径原文。"""
    try:
        key_path = _deobfuscate(record.get("key_path_enc"))
    except CalibError:
        key_path = ""
    dsn = _text(record.get("dsn"), 400)
    if not dsn and record.get("dsn_enc"):
        try:
            dsn = _deobfuscate(record.get("dsn_enc"))
        except CalibError:
            dsn = ""
    dsn = re.sub(r"(?i)(password\s*=)\s*\S+", r"\1***", dsn)
    return {
        "id": record.get("id", ""),
        "name": record.get("name", ""),
        "host": record.get("host", ""),
        "port": _as_int(record.get("port"), 22),
        "username": record.get("username", ""),
        "auth": record.get("auth", "password"),
        "db": record.get("db", "sqlite"),
        "db_path": record.get("db_path", ""),
        "dsn": dsn,
        "database": record.get("database", ""),
        "query_template": record.get("query_template", ""),
        "local": bool(record.get("local")),
        "sn": record.get("sn", ""),
        "pn": record.get("pn", ""),
        "model_key": record.get("model_key", ""),
        "mapping": record.get("mapping") or [],
        "password_set": bool(record.get("password_enc")),
        "key_path_set": bool(record.get("key_path_enc")),
        "key_path_hint": Path(key_path).name if key_path else "",
        "updated_at": record.get("updated_at", ""),
    }


def _find_target(targets: list[dict], target_id: str) -> Optional[dict]:
    for item in targets:
        if _text(item.get("id")) == _text(target_id):
            return item
    return None


def _get_target(target_id: str) -> dict:
    tid = _text(target_id, 64)
    if not tid:
        raise CalibError("target_not_found", "缺少下位机连接配置 id")
    record = _find_target(_load_targets(), tid)
    if record is None:
        raise CalibError("target_not_found", f"未找到下位机连接配置：{tid}")
    return record


def _validate_record(record: dict, *, creating: bool) -> None:
    name = _text(record.get("name"), 120)
    if not name:
        raise CalibError("invalid_target", "连接配置缺少名称")
    db = _text(record.get("db") or "sqlite").lower()
    if db not in DB_KINDS:
        raise CalibError("invalid_db", f"数据库类型只允许 sqlite|pgsql，收到：{db}")
    record["db"] = db
    auth = _text(record.get("auth") or ("key" if record.get("key_path_enc") else "password")).lower()
    if auth not in AUTH_KINDS:
        raise CalibError("invalid_target", f"认证方式只允许 password|key，收到：{auth}")
    record["auth"] = auth
    port = _as_int(record.get("port"), 22)
    if port < 1 or port > 65535:
        raise CalibError("invalid_target", f"端口不合法：{port}")
    record["port"] = port
    host = _text(record.get("host"), 200)
    record["host"] = host
    if not host and db == "pgsql":
        raise CalibError("invalid_target", "pgsql 连接必须填写主机")
    if not host and db == "sqlite" and not record.get("local"):
        raise CalibError("invalid_target", "sqlite 远程连接必须填写主机；本机库请设置 local=true")
    if auth == "key" and not record.get("key_path_enc"):
        raise CalibError("invalid_target", "密钥认证必须提供私钥路径")
    if db == "sqlite" and not _text(record.get("db_path"), 400):
        raise CalibError("invalid_target", "sqlite 连接必须提供 db_path")


def upsert_target(payload: dict) -> dict:
    """新增或更新下位机连接配置（PUT /remote-targets）。"""
    try:
        if not isinstance(payload, dict):
            raise CalibError("invalid_target", "请求体必须是对象")
        targets = _load_targets()
        target_id = _text(payload.get("id"), 64)
        existing = _find_target(targets, target_id) if target_id else None

        record = dict(existing) if existing else {"id": target_id or f"tgt_{uuid.uuid4().hex[:8]}", "created_at": _now()}
        for field in (
            "name",
            "host",
            "username",
            "db",
            "db_path",
            "database",
            "query_template",
            "sn",
            "pn",
            "model_key",
            "local",
        ):
            if field in payload:
                record[field] = payload[field]
        if "port" in payload:
            record["port"] = _as_int(payload.get("port"), 22)
        if "auth" in payload:
            record["auth"] = _text(payload.get("auth"), 16)
        if "mapping" in payload:
            mapping = payload.get("mapping")
            if mapping is not None and not isinstance(mapping, list):
                raise CalibError("invalid_mapping", "mapping 必须是列表")
            record["mapping"] = mapping or []

        if "dsn" in payload:
            dsn_text = _text(payload.get("dsn"), 400)
            if dsn_text and re.search(r"(?i)password\s*=", dsn_text):
                # dsn 内嵌口令：整体混淆，避免明文落盘
                record["dsn_enc"] = _obfuscate(dsn_text)
                record.pop("dsn", None)
            elif dsn_text:
                record["dsn"] = dsn_text
                record.pop("dsn_enc", None)
            else:
                record.pop("dsn", None)
                record.pop("dsn_enc", None)

        if "password" in payload:
            password = _text(payload.get("password"), 400)
            if password:
                record["password_enc"] = _obfuscate(password)
            else:
                record.pop("password_enc", None)
        if "key_path" in payload:
            key_path = _text(payload.get("key_path"), 400)
            if key_path:
                record["key_path_enc"] = _obfuscate(key_path)
            else:
                record.pop("key_path_enc", None)

        record.pop("password", None)  # 明文绝不落盘
        _validate_record(record, creating=existing is None)
        record["updated_at"] = _now()

        if existing is None:
            targets.append(record)
        else:
            targets[targets.index(existing)] = record
        _save_targets(targets)
        return {
            "success": True,
            "message": "已保存下位机连接配置" if existing else "已新增下位机连接配置",
            "target": _public_view(record),
            "created": existing is None,
        }
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, "保存下位机连接配置失败")


def list_targets() -> dict:
    """列出连接配置（口令/私钥路径只回显是否已设置）。"""
    try:
        targets = _load_targets()
        views = [_public_view(item) for item in targets]
        return {"success": True, "targets": views, "count": len(views)}
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, "读取下位机连接配置失败")


# ------------------------------------------------------------------ 目标机执行


def _normalize_target(target: dict) -> dict:
    if not isinstance(target, dict):
        raise CalibError("invalid_target", "下位机连接配置必须是对象")
    db = _text(target.get("db") or "sqlite").lower()
    if db not in DB_KINDS:
        raise CalibError("invalid_db", f"数据库类型只允许 sqlite|pgsql，收到：{db}")
    host = _text(target.get("host"), 200)
    db_path_raw = _text(target.get("db_path"), 400)
    local_db_path = str(Path(db_path_raw).expanduser()) if db_path_raw else ""
    local_flag = bool(target.get("local"))
    local = db == "sqlite" and (
        local_flag
        or (host.lower() in LOCAL_HOSTS and bool(local_db_path) and Path(local_db_path).is_file())
    )
    password = target.get("password")
    if not password:
        password = _deobfuscate(target.get("password_enc"))
    key_path = target.get("key_path")
    if not key_path:
        key_path = _deobfuscate(target.get("key_path_enc"))
    auth = _text(target.get("auth") or ("key" if key_path else "password")).lower()
    if auth not in AUTH_KINDS:
        raise CalibError("invalid_target", f"认证方式只允许 password|key，收到：{auth}")
    return {
        "id": _text(target.get("id"), 64),
        "name": _text(target.get("name"), 120),
        "host": host,
        "port": _as_int(target.get("port"), 22),
        "username": _text(target.get("username"), 120),
        "auth": auth,
        "password": _text(password, 400),
        "key_path": str(Path(key_path).expanduser()) if key_path else "",
        "db": db,
        "db_path": local_db_path if local else db_path_raw,
        "dsn": _text(target.get("dsn"), 400) or _deobfuscate(target.get("dsn_enc")),
        "database": _text(target.get("database"), 120) or "postgres",
        "query_template": _text(target.get("query_template"), 4000),
        "mapping": target.get("mapping") or [],
        "local": local,
        "sn": _text(target.get("sn"), 64),
        "pn": _text(target.get("pn"), 64),
        "model_key": _text(target.get("model_key"), 64),
        "header": target.get("header") if isinstance(target.get("header"), dict) else {},
        "timeout": max(1, _as_int(target.get("timeout"), 15)),
        "header_row": bool(target.get("header_row")),
    }


def _quote(value: Any) -> str:
    return shlex.quote(str(value))


def _command_for(target: dict, query: str) -> str:
    """按方言拼装只读查询命令（远程 Linux 视角，POSIX 引号）。"""
    sql = _text(query, 8000)
    if not sql:
        raise CalibError("invalid_query", "缺少查询语句")
    if target["db"] == "sqlite":
        if not target["db_path"]:
            raise CalibError("invalid_target", "sqlite 查询缺少 db_path")
        parts = ["sqlite3", "-separator", _quote("|")]
        if target["header_row"]:
            parts.append("-header")
        parts.extend([_quote(target["db_path"]), _quote(sql)])
        return " ".join(parts)

    parts = ["psql", "-A"]
    if not target["header_row"]:
        parts.append("-t")
    parts.extend(["-v", "ON_ERROR_STOP=1", "-w"])
    if target["dsn"]:
        parts.extend(["-d", _quote(target["dsn"])])
    else:
        parts.extend(
            [
                "-h",
                _quote(target["host"]),
                "-p",
                str(target["port"]),
                "-U",
                _quote(target["username"]),
                "-d",
                _quote(target["database"]),
            ]
        )
    parts.extend(["-c", _quote(sql)])
    return " ".join(parts)


def build_command(target: dict, query: str) -> str:
    """对外暴露的命令拼装（便于诊断与测试）。"""
    return _command_for(_normalize_target(target), query)


def _import_paramiko():
    try:
        import paramiko  # type: ignore
    except Exception as exc:  # noqa: BLE001 - ImportError 或环境损坏
        raise CalibError(
            "remote_connect_failed",
            f"paramiko 不可用，无法建立 SSH 连接（{exc}）；请在后端环境安装 paramiko 后重试",
        ) from exc
    return paramiko


def _ssh_exec(target: dict, command: str, timeout: int) -> tuple[str, int, str]:
    """执行远程命令，返回 (stdout, exit_code, stderr)。异常映射为明确错误码。"""
    paramiko = _import_paramiko()
    auth_exc = getattr(paramiko, "AuthenticationException", None)
    client = paramiko.SSHClient()
    try:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    except Exception:  # noqa: BLE001 - 打桩客户端可能没有该策略方法
        pass

    kwargs: dict[str, Any] = {
        "hostname": target["host"],
        "port": target["port"],
        "username": target["username"],
        "timeout": min(timeout, 10),
        "allow_agent": False,
        "look_for_keys": False,
    }
    if target["auth"] == "key":
        kwargs["key_filename"] = target["key_path"]
        if target["password"]:
            kwargs["passphrase"] = target["password"]
    else:
        kwargs["password"] = target["password"]

    try:
        client.connect(**kwargs)
    except Exception as exc:  # noqa: BLE001
        if auth_exc is not None and isinstance(exc, auth_exc):
            raise CalibError(
                "remote_auth_failed",
                f"SSH 认证失败（{target['username']}@{target['host']}:{target['port']}）：{exc}",
            ) from exc
        raise CalibError(
            "remote_connect_failed",
            f"无法连接下位机 {target['host']}:{target['port']}：{exc}",
        ) from exc

    env = {}
    if target["db"] == "pgsql" and target["auth"] == "password" and target["password"]:
        env["PGPASSWORD"] = target["password"]

    try:
        try:
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout, environment=env)
        except TypeError:  # 打桩客户端不接受 environment
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        except socket.timeout as exc:
            raise CalibError(
                "remote_query_timeout", f"查询超时（>{timeout}s），下位机未在限定时间内返回"
            ) from exc
        try:
            out = stdout.read()
            err = stderr.read()
        except socket.timeout as exc:
            raise CalibError(
                "remote_query_timeout", f"查询超时（>{timeout}s），读取结果时连接未返回"
            ) from exc
        try:
            code = int(stdout.channel.recv_exit_status())
        except Exception:  # noqa: BLE001
            code = 0
    except CalibError:
        raise
    except socket.timeout as exc:
        raise CalibError("remote_query_timeout", f"查询超时（>{timeout}s）") from exc
    except Exception as exc:  # noqa: BLE001
        raise CalibError("remote_query_failed", f"远程命令执行失败：{exc}") from exc
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    text = out.decode("utf-8", "replace") if isinstance(out, (bytes, bytearray)) else str(out or "")
    error_text = err.decode("utf-8", "replace") if isinstance(err, (bytes, bytearray)) else str(err or "")
    return text, code, error_text


def _ensure_readonly(query: str) -> None:
    head = query.lstrip().lstrip("(").lstrip().lower()
    if not re.match(r"^(select|with|show|describe|desc|explain)\b", head):
        raise CalibError(
            "query_not_readonly",
            f"仅允许只读查询（select/with），拒绝执行：{query.strip()[:80]}",
        )


def _parse_rows(text: str, header: bool = False) -> list:
    lines = [line for line in text.replace("\r\n", "\n").split("\n") if line.strip()]
    if lines and lines[0].strip().lower() in ("(0 rows)", "(0 行)"):
        lines = lines[1:]
    if not lines:
        return []
    delimiter = "\t" if "\t" in lines[0] else "|"
    parsed = [next(csv.reader([line], delimiter=delimiter)) for line in lines]
    if not header:
        return parsed
    if len(parsed) < 2:
        return []
    names = [str(name).strip() for name in parsed[0]]
    rows: list[dict] = []
    for raw in parsed[1:]:
        padded = list(raw) + [""] * (len(names) - len(raw))
        rows.append(dict(zip(names, padded)))
    return rows


def _query_sqlite_local(target: dict, query: str, timeout: int) -> list[dict]:
    path = Path(target["db_path"])
    if not path.is_file():
        raise CalibError(
            "remote_connect_failed", f"下位机 SQLite 数据库文件不存在：{path}"
        )
    _ensure_readonly(query)
    deadline = time.monotonic() + timeout
    try:
        conn = sqlite3.connect(str(path), timeout=min(timeout, 10))
    except sqlite3.Error as exc:
        raise CalibError("remote_connect_failed", f"打开 SQLite 数据库失败：{path}（{exc}）") from exc
    try:
        conn.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0, 10000
        )
        cur = conn.execute(query)
        names = [item[0] for item in (cur.description or [])]
        rows = [dict(zip(names, tuple(row))) for row in cur.fetchall()]
        return rows
    except sqlite3.OperationalError as exc:
        if "interrupt" in str(exc).lower() or time.monotonic() > deadline:
            raise CalibError(
                "remote_query_timeout", f"查询超时（>{timeout}s），SQLite 已中断"
            ) from exc
        raise CalibError("remote_query_failed", f"SQLite 查询失败：{exc}") from exc
    except sqlite3.Error as exc:
        raise CalibError("remote_query_failed", f"SQLite 查询失败：{exc}") from exc
    finally:
        conn.close()


def _execute(target: dict, command: str, query: str, timeout: int) -> tuple[list, dict]:
    if target["local"]:
        rows = _query_sqlite_local(target, query, timeout)
        return rows, {"transport": "local", "command": command, "stdout": "", "stderr": ""}
    text, code, err = _ssh_exec(target, command, timeout)
    if code not in (0, None):
        detail = err.strip() or text.strip()
        raise CalibError(
            "remote_query_failed",
            f"下位机查询失败（退出码 {code}）：{detail[:300]}",
            command=command,
            stderr=err.strip()[:1000],
        )
    rows = _parse_rows(text, header=bool(target.get("header_row")))
    return rows, {"transport": "ssh", "command": command, "stdout": text, "stderr": err}


# ------------------------------------------------------------------ 字段映射


def _norm_mapping(mapping: Any) -> list[dict]:
    if mapping in (None, ""):
        return []
    if not isinstance(mapping, list):
        raise CalibError("invalid_mapping", "mapping 必须是列表")
    normalized: list[dict] = []
    for index, item in enumerate(mapping):
        if not isinstance(item, dict):
            raise CalibError("invalid_mapping", f"第 {index + 1} 条映射不是对象")
        source = _text(
            item.get("source_field") or item.get("source") or item.get("sourceField"), 120
        )
        target = _text(
            item.get("target_field")
            or item.get("target")
            or item.get("targetField")
            or item.get("model_field"),
            120,
        )
        if not source or not target:
            raise CalibError(
                "invalid_mapping", f"第 {index + 1} 条映射缺少 source_field / target_field"
            )
        try:
            scale = float(item.get("scale", 1) if item.get("scale") not in (None, "") else 1)
            offset = float(item.get("offset", 0) if item.get("offset") not in (None, "") else 0)
        except (TypeError, ValueError) as exc:
            raise CalibError(
                "invalid_mapping", f"映射 {source}->{target} 的 scale/offset 不是数值"
            ) from exc
        normalized.append(
            {
                "source_field": source,
                "target_field": target,
                "scale": scale,
                "offset": offset,
                "required": bool(item.get("required")),
            }
        )
    return normalized


def _apply_mapping(row: Any, mapping: list[dict], row_is_dict: bool) -> tuple[dict, dict]:
    """按 target = source*scale + offset 换算；required 缺失列出 source_field。"""
    if not mapping:
        if row_is_dict:
            values = {
                key: value
                for key, value in row.items()
                if key not in META_TARGETS and value not in (None, "")
            }
            return values, {}
        raise CalibError(
            "mapping_missing",
            "未提供字段映射，且查询结果无表头，无法确定字段对应关系",
            missing=[],
        )

    values: dict[str, Any] = {}
    meta: dict[str, str] = {}
    missing: list[str] = []
    for index, item in enumerate(mapping):
        source = item["source_field"]
        target = item["target_field"]
        if row_is_dict:
            raw = row.get(source)
        else:
            raw = row[index] if index < len(row) else None
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            if item["required"]:
                missing.append(source)
            continue
        target_key = target.lower()
        if target_key in META_TARGETS:
            meta[target_key] = _text(raw, 64)
            continue
        if item["scale"] == 1.0 and item["offset"] == 0.0:
            values[target] = raw
            continue
        try:
            number = float(str(raw).strip()) * item["scale"] + item["offset"]
        except (TypeError, ValueError) as exc:
            raise CalibError(
                "mapping_invalid",
                f"字段 {source}={raw} 不是数值，无法按 scale={item['scale']} / offset={item['offset']} 换算",
            ) from exc
        values[target] = _fmt_num(round(number, 10))

    if missing:
        raise CalibError(
            "mapping_missing",
            f"缺少必填字段：{', '.join(missing)}",
            missing=missing,
        )
    if not values:
        raise CalibError(
            "mapping_missing", "映射后没有任何模型字段值，请检查 mapping 与查询列名"
        )
    return values, meta


def _group_rows(rows: list, model_key: str) -> list[tuple[str, list]]:
    """按 module 列分组；无该列则单模型。"""
    if not rows or not isinstance(rows[0], dict):
        return [(model_key, rows)]
    keys = ("module", "module_key", "model", "model_key", "modelKey")
    column = next(
        (key for key in keys if any(key in row for row in rows if isinstance(row, dict))), ""
    )
    if not column:
        return [(model_key, rows)]
    groups: dict[str, list] = {}
    for row in rows:
        name = _text(row.get(column), 64) or model_key
        groups.setdefault(name, []).append(row)
    return list(groups.items())


def _pick(row: Any, *names: str) -> str:
    if not isinstance(row, dict):
        return ""
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return _text(value, 64)
    return ""


def _fetch_remote(
    target: dict,
    query: str,
    mapping: Any,
    out_dir: Any,
    *,
    pn: str,
    model_key: str,
    sn: str,
    catalog: Any,
    db_path: Optional[Path],
    timeout: Any,
) -> dict:
    normalized = _normalize_target(target)
    query_text = _text(query, 8000) or normalized["query_template"]
    if not query_text:
        raise CalibError("invalid_query", "缺少查询语句，且目标机未配置 query_template")

    mapping_list = _norm_mapping(mapping)
    if not mapping_list and normalized.get("mapping"):
        mapping_list = _norm_mapping(normalized["mapping"])
    timeout_value = max(1, _as_int(timeout, normalized["timeout"]))
    command = _command_for(normalized, query_text)
    rows, meta = _execute(normalized, command, query_text, timeout_value)
    if not rows:
        raise CalibError(
            "remote_empty_rows",
            f"下位机查询返回空数据（0 行），未生成常数文件；命令：{command}",
            command=command,
        )

    wanted_module = _text(model_key or normalized.get("model_key"), 64)
    pn_value = _text(pn or normalized.get("pn"), 64)
    sn_value = _text(sn or normalized.get("sn"), 64)
    header = normalized.get("header") or {}

    files: list[dict] = []
    for module, group in _group_rows(rows, wanted_module):
        row_is_dict = isinstance(group[0], dict)
        if row_is_dict:
            merged: dict = {}
            for item in group:
                merged.update(item)
        else:
            merged = group[0]

        row_sn = _pick(merged, "sn", "serial", "SN")
        row_pn = _pick(merged, "pn", "PN")
        group_sn = row_sn or sn_value
        group_pn = row_pn or pn_value

        if not group_sn:
            raise CalibError(
                "missing_sn",
                "查询结果缺少 sn 列，且请求未指定 sn，无法命名标定文件",
                module=module,
            )
        if not module:
            module = wanted_module
        model = _load_model(group_pn, module, catalog)

        values, row_meta = _apply_mapping(merged, mapping_list, row_is_dict)
        for key, value in row_meta.items():
            if key == "sn":
                group_sn = value or group_sn
            elif key == "pn":
                group_pn = value or group_pn
        if row_meta.get("module"):
            model = _load_model(group_pn, row_meta["module"], catalog)

        emitted = _emit(
            sn=group_sn,
            pn=group_pn,
            model=model,
            values=values,
            header=header,
            out_dir=out_dir,
            db_path=db_path,
        )
        files.append(
            {
                "path": emitted["path"],
                "file": emitted["file"],
                "module": model["module"],
                "sn": group_sn,
                "pn": emitted["pn"],
                "checksum": emitted["checksum"],
                "values": emitted["values"],
                "calibration_id": emitted["calibration_id"],
            }
        )

    names = "、".join(item["file"] for item in files)
    return {
        "success": True,
        "message": f"已从下位机读取 {len(rows)} 行数据，生成 {len(files)} 个常数文件：{names}",
        "files": files,
        "count": len(files),
        "rows": len(rows),
        "command": command,
        "transport": meta.get("transport"),
    }


def fetch_remote(
    target: dict,
    query: str,
    mapping: list[dict],
    out_dir: Path,
    *,
    pn: str = "",
    model_key: str = "",
    sn: str = "",
    catalog: Any = None,
    db_path: Any = None,
    timeout: int = 15,
) -> dict:
    """下位机取数并生成常数文件。

    目标机为 sqlite 且数据库在本机可访问时走本地分支（Windows 无 sqlite3 命令行时的兜底）。
    """
    try:
        target_db = Path(db_path).expanduser().resolve() if db_path else _DB_PATH
        return _fetch_remote(
            target,
            query,
            mapping,
            out_dir,
            pn=pn,
            model_key=model_key,
            sn=sn,
            catalog=catalog,
            db_path=target_db,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - 对外统一 JSON
        return _fail(exc, "下位机标定失败")


# ------------------------------------------------------------------ 连通性测试


def test_target(target_id: str) -> dict:
    """连通性测试：返回 {ok, elapsed_ms, message}；连接不上时 ok=false 而不是抛错。"""
    started = time.perf_counter()
    try:
        record = _get_target(target_id)
        target = _normalize_target(record)
        if target["db"] == "sqlite":
            path = Path(target["db_path"])
            if not target["db_path"] or not path.is_file():
                return {
                    "success": True,
                    "ok": False,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "message": f"SQLite 数据库文件不存在：{target['db_path'] or '(未配置)'}",
                    "error_code": "remote_connect_failed",
                }
            count: Optional[int] = None
            if not target["local"]:
                # 远程 sqlite：仍走 SSH + sqlite3
                command = _command_for(target, "select count(*) from sqlite_master")
                _ssh_exec(target, command, target["timeout"])
            else:
                conn = sqlite3.connect(str(path), timeout=5)
                try:
                    count = conn.execute(
                        "select count(*) from sqlite_master where type='table'"
                    ).fetchone()[0]
                finally:
                    conn.close()
            elapsed = int((time.perf_counter() - started) * 1000)
            suffix = f"{count} 张表，" if count is not None else ""
            return {
                "success": True,
                "ok": True,
                "elapsed_ms": elapsed,
                "message": f"SQLite 连通正常：{path.name}（{suffix}往返 {elapsed} ms）",
            }

        command = _command_for(target, "select 1")
        text, code, err = _ssh_exec(target, command, target["timeout"])
        elapsed = int((time.perf_counter() - started) * 1000)
        ok = code in (0, None) and "1" in text
        message = (
            f"PostgreSQL 连通正常（SSH 往返 {elapsed} ms）"
            if ok
            else f"PostgreSQL 未返回预期结果（退出码 {code}）：{(err or text).strip()[:200]}"
        )
        return {"success": True, "ok": ok, "elapsed_ms": elapsed, "message": message}
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.perf_counter() - started) * 1000)
        if isinstance(exc, CalibError):
            request_error = exc.code in (
                "target_not_found",
                "invalid_target",
                "invalid_db",
                "credential_unreadable",
            )
            return {
                "success": not request_error,
                "ok": False,
                "elapsed_ms": elapsed,
                "message": exc.message,
                "error_code": exc.code,
            }
        return {
            "success": True,
            "ok": False,
            "elapsed_ms": elapsed,
            "message": f"连通性测试失败：{exc}",
        }


# ------------------------------------------------------------------ HTTP 路由


def _default_out_dir() -> Path:
    return Path(_BASE_DIR) / "calib_output"


def register_routes(app, base_dir: Path, db_path=None) -> None:
    """挂载 /api/runner 下的标定路由（异常一律包成 success=false）。"""
    configure(base_dir, db_path)
    from fastapi import Body

    @app.post("/api/runner/calib/local/build")
    def calib_local_build(payload: Any = Body(default=None)) -> dict:
        data = payload if isinstance(payload, dict) else {}
        try:
            out_dir = Path(data.get("out_dir") or _default_out_dir())
            return build_local(
                _text(data.get("sn"), 64),
                _text(data.get("pn"), 64),
                _text(data.get("model_key"), 64),
                data.get("values") or {},
                data.get("header") or {},
                out_dir,
                catalog=data.get("catalog"),
                db_path=data.get("db_path"),
            )
        except Exception as exc:  # noqa: BLE001
            return _fail(exc, "本机标定请求处理失败")

    @app.post("/api/runner/calib/remote/fetch")
    def calib_remote_fetch(payload: Any = Body(default=None)) -> dict:
        data = payload if isinstance(payload, dict) else {}
        try:
            target_id = _text(data.get("target_id"), 64)
            if target_id:
                target = _get_target(target_id)
            else:
                target = data.get("target") or {}
            out_dir = Path(data.get("out_dir") or _default_out_dir())
            mapping = data.get("mapping")
            if mapping is None and isinstance(target, dict):
                mapping = target.get("mapping") or []
            return fetch_remote(
                target,
                _text(data.get("query"), 8000),
                mapping or [],
                out_dir,
                pn=_text(data.get("pn"), 64),
                model_key=_text(data.get("model_key"), 64),
                sn=_text(data.get("sn"), 64),
                catalog=data.get("catalog"),
                db_path=data.get("db_path"),
                timeout=_as_int(data.get("timeout"), 15),
            )
        except Exception as exc:  # noqa: BLE001
            return _fail(exc, "下位机标定请求处理失败")

    @app.get("/api/runner/remote-targets")
    def remote_targets_list() -> dict:
        try:
            return list_targets()
        except Exception as exc:  # noqa: BLE001
            return _fail(exc, "读取下位机连接配置失败")

    @app.put("/api/runner/remote-targets")
    @app.post("/api/runner/remote-targets")
    def remote_targets_upsert(payload: Any = Body(default=None)) -> dict:
        try:
            return upsert_target(payload if isinstance(payload, dict) else {})
        except Exception as exc:  # noqa: BLE001
            return _fail(exc, "保存下位机连接配置失败")

    @app.post("/api/runner/remote-targets/{target_id}/test")
    def remote_targets_test(target_id: str) -> dict:
        try:
            return test_target(target_id)
        except Exception as exc:  # noqa: BLE001
            return _fail(exc, "连通性测试失败")


__all__ = [
    "CalibError",
    "SCHEMA",
    "build_command",
    "build_local",
    "configure",
    "fetch_remote",
    "list_targets",
    "register_routes",
    "test_target",
    "upsert_target",
]
