# -*- coding: utf-8 -*-
"""const_runtime 单元测试（SR02-AR01 / AR02 / AR03）。

运行：
    cd D:\\ATE\\MockRunner\\backend
    .\\.venv\\Scripts\\python.exe -m pytest tests\\test_const_runtime.py -q --basetemp=.pytest_tmp
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import const_runtime  # noqa: E402

PN = "SPM-RH-01"
MK = "SPM_RH"


# ---------------------------------------------------------------- 测试夹具


def _fields(gain_default: float = 0.0) -> list[dict]:
    return [
        {
            "key": "gain_offset",
            "label": "增益偏移",
            "type": "float",
            "unit": "mV",
            "default": gain_default,
            "min": -50.0,
            "max": 50.0,
        },
        {
            "key": "bias_voltage",
            "label": "偏置电压",
            "type": "float",
            "unit": "V",
            "default": 1.85,
            "min": 1.5,
            "max": 2.2,
        },
        {
            "key": "head_grade",
            "label": "读头等级",
            "type": "enum",
            "unit": "",
            "default": "B",
            "values": ["A", "B", "C"],
        },
        {"key": "enabled", "label": "启用", "type": "bool", "unit": "", "default": True},
    ]


def _catalog() -> dict:
    return {
        "version": "2026.11.01",
        "models": [
            {
                "pn": PN,
                "model_key": MK,
                "name": "SPM 读头机常数",
                "version": "2026.11.01",
                "product": "1000-A",
                "subsystem": "SPM",
                "fields": _fields(0.0),
            },
            {
                "pn": PN,
                "model_key": MK,
                "name": "SPM 读头机常数（旧版）",
                "version": "2025.12.01",
                "product": "1000-A",
                "subsystem": "SPM",
                "fields": _fields(0.1),
            },
        ],
    }


def _bind(**extra) -> dict:
    binding = {"pn": PN, "model_key": MK}
    binding.update(extra)
    return binding


def _sn_models(values: dict, **extra) -> dict:
    binding = _bind(source="sn", sn="SC0012345678", values=values, **extra)
    return const_runtime.resolve_models([binding], _catalog())


def _header(**extra) -> dict:
    header = {
        "pn": PN,
        "module": MK,
        "version": "2026.11.01",
        "name": "SPM 读头机常数",
        "product": "1000-A",
        "subsystem": "SPM",
    }
    header.update(extra)
    return header


def _root(path: Path) -> ET.Element:
    return ET.fromstring(path.read_bytes())


def _write_template(path: Path) -> dict:
    return const_runtime.write_constants(
        path,
        const_runtime.resolve_models([_bind()], _catalog()),
        _header(),
    )


def _write_sn(path: Path) -> dict:
    models = _sn_models(
        {
            "gain_offset": 42.5,
            "bias_voltage": 1.9,
            "head_grade": "A",
            "enabled": False,
        }
    )
    return const_runtime.write_constants(path, models, _header(sn="SC0012345678"))


# ---------------------------------------------------------------- SR02-AR01 解析


def test_parse_machine_constants_list():
    tps = {
        "id": "tps-1",
        "machine_constants": [
            {
                "pn": "spm-rh-01",
                "model_key": MK,
                "alias": "head",
                "source": "version",
                "version": "2026.11.01",
                "parser": {"type": "kdb", "params": {"table": "cal"}},
            },
            {"pn": PN, "model_key": MK},
        ],
    }
    out = const_runtime.parse_bindings(tps)
    assert len(out) == 2
    assert out[0]["pn"] == PN  # PN 归一化为大写
    assert out[0]["alias"] == "head"
    assert out[0]["source"] == "version"
    assert out[0]["parser"] == {"type": "kdb", "params": {"table": "cal"}}
    assert out[1]["alias"] == MK
    assert out[1]["source"] == "default"
    assert out[1]["parser"] == {"type": "xml", "params": {}}


def test_parse_machine_constants_object_form():
    tps = {
        "machine_constants": {
            "bindings": [
                {"pn": PN, "model_key": MK, "source": "sn", "sn": "SC1", "parser": "python"}
            ]
        }
    }
    out = const_runtime.parse_bindings(tps)
    assert len(out) == 1
    assert out[0]["source"] == "sn"
    assert out[0]["sn"] == "SC1"
    assert out[0]["parser"] == {"type": "python", "params": {}}


def test_parse_missing_section_returns_empty():
    assert const_runtime.parse_bindings({}) == []
    assert const_runtime.parse_bindings({"id": "old-tps"}) == []
    assert const_runtime.parse_bindings({"machine_constants": None}) == []
    assert const_runtime.parse_bindings({"machine_constants": {"bindings": []}}) == []
    # 直接传绑定段对象（路由内部形态）也可解析
    assert const_runtime.parse_bindings({"bindings": []}) == []


def test_parse_missing_pn_reports_entry_number():
    tps = {"machine_constants": [_bind(), {"model_key": "MOT_DRV"}]}
    with pytest.raises(ValueError) as excinfo:
        const_runtime.parse_bindings(tps)
    message = str(excinfo.value)
    assert "第 2 条" in message
    assert "pn" in message


def test_parse_missing_model_key_reports_entry_number():
    tps = {"machine_constants": [_bind(), {"pn": "MOT-DRV-01"}]}
    with pytest.raises(ValueError) as excinfo:
        const_runtime.parse_bindings(tps)
    message = str(excinfo.value)
    assert "第 2 条" in message
    assert "model_key" in message


def test_parse_invalid_source_and_parser_rejected():
    with pytest.raises(ValueError, match="第 1 条"):
        const_runtime.parse_bindings({"machine_constants": [_bind(source="mes")]})
    with pytest.raises(ValueError, match="parser.type"):
        const_runtime.parse_bindings({"machine_constants": [_bind(parser="excel")]})
    with pytest.raises(ValueError, match="缺少 version"):
        const_runtime.parse_bindings({"machine_constants": [_bind(source="version")]})


# ---------------------------------------------------------------- 模型解析


def test_resolve_hit_and_missing():
    bindings = [_bind(), {"pn": "MOT-DRV-01", "model_key": "MOT_DRV"}]
    out = const_runtime.resolve_models(bindings, _catalog())
    assert len(out["models"]) == 1
    assert out["models"][0]["pn"] == PN
    assert out["models"][0]["model_key"] == MK
    assert len(out["missing"]) == 1
    assert out["missing"][0]["pn"] == "MOT-DRV-01"
    assert "未找到模型" in out["missing"][0]["reason"]
    assert out["counts"]["model_count"] == 1
    assert out["counts"]["missing_count"] == 1


def test_resolve_default_source_uses_defaults():
    out = const_runtime.resolve_models([_bind()], _catalog())
    fields = {f["key"]: f for f in out["models"][0]["fields"]}
    assert fields["gain_offset"]["value"] == "0.0"
    assert fields["bias_voltage"]["value"] == "1.85"
    assert fields["head_grade"]["value"] == "B"
    assert fields["enabled"]["value"] == "true"
    assert all(f["filled"] for f in fields.values())
    assert out["models"][0]["source"] == "default"


def test_resolve_version_source_picks_version_structure():
    out = const_runtime.resolve_models(
        [_bind(source="version", version="2025.12.01")], _catalog()
    )
    model = out["models"][0]
    assert model["version"] == "2025.12.01"
    assert model["fields"][0]["value"] == "0.1"
    assert out["missing"] == []

    miss = const_runtime.resolve_models(
        [_bind(source="version", version="2099.01.01")], _catalog()
    )
    assert miss["models"] == []
    assert "不存在版本" in miss["missing"][0]["reason"]


def test_resolve_sn_source_requires_sn_and_accepts_values():
    no_sn = const_runtime.resolve_models([_bind(source="sn")], _catalog())
    assert no_sn["models"] == []
    assert "SN" in no_sn["missing"][0]["reason"]

    out = _sn_models({"gain_offset": 12.5, "head_grade": "C"})
    fields = {f["key"]: f for f in out["models"][0]["fields"]}
    assert fields["gain_offset"]["value"] == "12.5"
    assert fields["gain_offset"]["source"] == "sn"
    assert fields["head_grade"]["value"] == "C"
    assert fields["bias_voltage"]["value"] == "1.85"  # 未覆盖字段回默认值
    assert fields["bias_voltage"]["source"] == "default"


# ---------------------------------------------------------------- SR02-AR03 校验


def test_validate_strict_lists_all_violations():
    models = _sn_models(
        {"gain_offset": 60, "bias_voltage": 1.0, "head_grade": "D", "enabled": "yes"}
    )
    result = const_runtime.validate_values(models, strict=True)
    assert result["ok"] is False
    assert result["strict"] is True
    assert result["invalid"] == 4
    by_key = {err["key"]: err for err in result["errors"]}
    assert set(by_key) == {"gain_offset", "bias_voltage", "head_grade", "enabled"}
    assert by_key["gain_offset"]["rule"] == "range"
    assert by_key["gain_offset"]["allowed"] == {"min": -50.0, "max": 50.0}
    assert by_key["bias_voltage"]["allowed"] == {"min": 1.5, "max": 2.2}
    assert by_key["head_grade"]["rule"] == "enum"
    assert by_key["head_grade"]["allowed"] == ["A", "B", "C"]
    assert by_key["enabled"]["rule"] == "bool"
    assert by_key["enabled"]["allowed"] == ["true", "false"]


def test_validate_strict_false_warns_and_passes():
    models = _sn_models({"gain_offset": 60, "bias_voltage": 1.0})
    result = const_runtime.validate_values(models, strict=False)
    assert result["ok"] is True
    assert result["errors"] == []
    assert len(result["warnings"]) == 2
    assert result["passed"] is False
    assert result["invalid"] == 2
    assert {w["key"] for w in result["warnings"]} == {"gain_offset", "bias_voltage"}


def test_validate_enum_illegal_value_rejected():
    result = const_runtime.validate_values(_sn_models({"head_grade": "Z"}))
    assert result["ok"] is False
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert error["key"] == "head_grade"
    assert error["rule"] == "enum"
    assert "Z" in error["message"]


def test_validate_enum_options_alias_from_local_db(tmp_path):
    """本地 ate.db 的字段用 options 存枚举，校验与写 XML 都要兼容。"""
    catalog = {
        "version": "2026.11.01",
        "models": [
            {
                "pn": PN,
                "model_key": MK,
                "version": "2026.11.01",
                "fields": [
                    {"key": "grade", "label": "等级", "type": "enum", "default": "A", "options": ["A", "B"]}
                ],
            }
        ],
    }
    models = const_runtime.resolve_models(
        [_bind(source="sn", sn="SC1", values={"grade": "C"})], catalog
    )
    result = const_runtime.validate_values(models)
    assert result["ok"] is False
    assert result["errors"][0]["allowed"] == ["A", "B"]

    target = tmp_path / "options.xml"
    written = const_runtime.write_constants(
        target, models, {"pn": PN, "module": MK, "version": "2026.11.01", "sn": "SC1"}
    )
    assert written["success"] is True, written
    field = _root(target).find("Fields/Field")
    assert field.get("values") == "A,B"
    assert field.get("value") == "C"


def test_validate_bool_illegal_value_rejected():
    result = const_runtime.validate_values(_sn_models({"enabled": "1"}))
    assert result["ok"] is False
    assert result["errors"][0]["key"] == "enabled"
    assert result["errors"][0]["rule"] == "bool"
    assert result["errors"][0]["allowed"] == ["true", "false"]


def test_validate_type_illegal_value_rejected():
    result = const_runtime.validate_values(_sn_models({"gain_offset": "abc"}))
    assert result["ok"] is False
    assert result["errors"][0]["key"] == "gain_offset"
    assert result["errors"][0]["rule"] == "type"


def test_validate_required_empty_value_rejected():
    catalog = {
        "version": "2026.11.01",
        "models": [
            {
                "pn": PN,
                "model_key": MK,
                "version": "2026.11.01",
                "fields": [
                    {"key": "batch_no", "label": "批次", "type": "str", "default": ""}
                ],
            }
        ],
    }
    models = const_runtime.resolve_models([_bind()], catalog)
    assert models["models"][0]["fields"][0]["filled"] is False
    result = const_runtime.validate_values(models)
    assert result["ok"] is False
    assert result["errors"][0]["key"] == "batch_no"
    assert result["errors"][0]["rule"] == "required"


# ---------------------------------------------------------------- SR02-AR02 写入


def test_write_creates_template(tmp_path):
    target = tmp_path / "SPM_RH.xml"
    result = _write_template(target)
    assert result["success"] is True, result
    assert target.is_file()
    assert result["created"] is True
    assert result["calibrated"] is False
    assert result["sn"] == ""
    assert result["written"] == []  # 结构模板不写值
    assert result["skipped"] == ["gain_offset", "bias_voltage", "head_grade", "enabled"]
    assert result["checksum"] == hashlib.sha256(target.read_bytes()).hexdigest()

    text = target.read_text(encoding="utf-8")
    assert text.startswith('<?xml version="1.0" encoding="utf-8"?>\n<MachineConstants')
    assert "\n  <Meta " in text
    assert '\n    <Field key="gain_offset"' in text  # 2 空格逐层缩进

    root = _root(target)
    assert root.tag == "MachineConstants"
    assert root.get("schema") == "ate.constants.v2"
    assert root.get("module") == MK
    assert root.get("pn") == PN
    assert root.get("version") == "2026.11.01"
    assert root.get("calibrated") == "false"
    assert root.get("sn") == ""
    assert root.find("Meta").get("name") == "SPM 读头机常数"
    fields = {el.get("key"): el for el in root.findall("Fields/Field")}
    assert len(fields) == 4
    assert fields["gain_offset"].get("min") == "-50.0"
    assert fields["gain_offset"].get("max") == "50.0"
    assert fields["gain_offset"].get("value") == ""  # 模板值留空，默认值在 default 属性
    assert fields["gain_offset"].get("default") == "0.0"
    assert fields["head_grade"].get("values") == "A,B,C"
    assert fields["enabled"].get("default") == "true"
    assert root.find("Calibration") is None


def test_write_creates_calibrated_instance(tmp_path):
    target = tmp_path / "SC0012345678_SPM_RH.xml"
    result = _write_sn(target)
    assert result["success"] is True, result
    assert result["created"] is True
    assert result["calibrated"] is True
    assert result["sn"] == "SC0012345678"
    assert len(result["written"]) == 4

    root = _root(target)
    assert root.get("calibrated") == "true"
    assert root.get("sn") == "SC0012345678"
    calib = root.find("Calibration")
    assert calib is not None
    assert calib.get("sn") == "SC0012345678"
    fields = {el.get("key"): el for el in root.findall("Fields/Field")}
    assert fields["gain_offset"].get("value") == "42.5"
    assert fields["enabled"].get("value") == "false"


def test_write_updates_values_and_preserves_other_attributes(tmp_path):
    target = tmp_path / "SPM_RH.xml"
    target.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<MachineConstants schema="ate.constants.v2" module="SPM_RH" pn="SPM-RH-01" '
        'version="2026.11.01" calibrated="false" sn="" owner="line-A" '
        'generatedAt="2026-01-01T00:00:00+08:00">\n'
        '  <Meta product="1000-A" subsystem="SPM" name="旧名" />\n'
        "  <Fields>\n"
        '    <Field key="gain_offset" label="增益偏移" type="float" unit="mV" '
        'default="0.0" min="-50.0" max="50.0" value="" desc="keep-me" />\n'
        "  </Fields>\n"
        "</MachineConstants>\n",
        encoding="utf-8",
    )
    models = _sn_models(
        {
            "gain_offset": 42.5,
            "bias_voltage": 1.9,
            "head_grade": "A",
            "enabled": False,
        }
    )
    result = const_runtime.write_constants(
        target,
        models,
        _header(
            sn="SC0012345678",
            name="新名",
            operator="op-1",
            tps_id="tps-1",
            task_id="task-9",
            at="2026-11-22T10:00:00+08:00",
        ),
    )
    assert result["success"] is True, result
    assert result["written"] == ["gain_offset"]
    assert result["calibrated"] is True
    assert set(result["skipped"]) == {"bias_voltage", "head_grade", "enabled"}

    root = _root(target)
    assert root.get("owner") == "line-A"  # 其它根属性保留
    assert root.get("calibrated") == "true"
    assert root.get("sn") == "SC0012345678"
    assert root.find("Meta").get("name") == "新名"
    fields = root.findall("Fields/Field")
    assert len(fields) == 1  # 不新增结构
    assert fields[0].get("value") == "42.5"
    assert fields[0].get("desc") == "keep-me"  # 其它字段属性保留
    calib = root.find("Calibration")
    assert calib is not None
    assert calib.get("sn") == "SC0012345678"
    assert calib.get("operator") == "op-1"
    assert calib.get("tpsId") == "tps-1"
    assert calib.get("taskId") == "task-9"
    assert calib.get("at") == "2026-11-22T10:00:00+08:00"


def test_write_is_atomic_on_replace_failure(tmp_path, monkeypatch):
    target = tmp_path / "SPM_RH.xml"
    assert _write_template(target)["success"] is True
    before = target.read_bytes()

    def boom(src: str, dst: str) -> None:
        raise OSError("模拟磁盘故障")

    monkeypatch.setattr(const_runtime, "_replace_file", boom)
    result = _write_sn(target)
    assert result["success"] is False
    assert "模拟磁盘故障" in result["message"]
    assert target.read_bytes() == before  # 原文件未被破坏
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_write_bad_path_keeps_existing_file(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("keep", encoding="utf-8")
    models = const_runtime.resolve_models([_bind()], _catalog())
    result = const_runtime.write_constants(blocker / "child.xml", models, _header())
    assert result["success"] is False
    assert blocker.read_text(encoding="utf-8") == "keep"


def test_write_rejects_calibrated_without_values(tmp_path):
    target = tmp_path / "SPM_RH.xml"
    models = _sn_models({"gain_offset": 1.0})  # 其余字段回默认值，均有值
    # 人为清空一个字段，模拟“标定实例缺值”
    models["models"][0]["fields"][1]["value"] = ""
    result = const_runtime.write_constants(target, models, _header(sn="SC001"))
    assert result["success"] is False
    assert "无值字段" in result["message"]
    assert not target.exists()


# ---------------------------------------------------------------- SR02-AR02 清除


def test_clear_all_restores_default_semantics(tmp_path):
    target = tmp_path / "SC0012345678_SPM_RH.xml"
    assert _write_sn(target)["success"] is True
    assert _root(target).find("Calibration") is not None

    result = const_runtime.clear_constants(target)
    assert result["success"] is True
    assert result["cleared"] == ["gain_offset", "bias_voltage", "head_grade", "enabled"]
    assert result["skipped"] == []
    assert result["checksum"] == hashlib.sha256(target.read_bytes()).hexdigest()

    root = _root(target)
    assert root.get("calibrated") == "false"
    assert root.get("sn") == ""
    assert root.find("Calibration") is None
    fields = {el.get("key"): el for el in root.findall("Fields/Field")}
    assert all(el.get("value") == "" for el in fields.values())
    assert fields["gain_offset"].get("default") == "0.0"  # 默认值属性保留
    assert fields["head_grade"].get("values") == "A,B,C"


def test_clear_selected_keys_only(tmp_path):
    target = tmp_path / "SC0012345678_SPM_RH.xml"
    assert _write_sn(target)["success"] is True

    result = const_runtime.clear_constants(target, ["gain_offset", "not_exist"])
    assert result["success"] is True
    assert result["cleared"] == ["gain_offset"]
    assert result["keys"] == ["gain_offset"]
    assert result["skipped"] == ["not_exist"]

    root = _root(target)
    fields = {el.get("key"): el for el in root.findall("Fields/Field")}
    assert fields["gain_offset"].get("value") == ""
    assert fields["bias_voltage"].get("value") == "1.9"  # 其它字段保留
    assert root.get("calibrated") == "false"
    assert root.get("sn") == "SC0012345678"  # 部分清除保留实例标识
    assert root.find("Calibration") is None


def test_clear_missing_file_and_unknown_keys(tmp_path):
    missing = const_runtime.clear_constants(tmp_path / "none.xml")
    assert missing["success"] is False
    assert "不存在" in missing["message"]

    target = tmp_path / "SPM_RH.xml"
    assert _write_template(target)["success"] is True
    unknown = const_runtime.clear_constants(target, ["nope"])
    assert unknown["success"] is False
    assert "未找到字段" in unknown["message"]


# ---------------------------------------------------------------- HTTP 路由


def test_routes_validate_write_resolve_clear(tmp_path, monkeypatch):
    app = FastAPI()
    const_runtime.register_routes(app, tmp_path)
    client = TestClient(app)

    body = {"bindings": [_bind()], "catalog": _catalog()}
    response = client.post("/api/runner/constants/validate", json=body)
    data = response.json()
    assert data["success"] is True
    assert data["ok"] is True
    assert data["counts"]["model_count"] == 1
    assert data["missing"] == []

    target = tmp_path / "api_SPM_RH.xml"
    response = client.post(
        "/api/runner/constants/write",
        json={
            "target_path": str(target),
            "bindings": [_bind()],
            "catalog": _catalog(),
            "header": _header(name="API 写入"),
            "strict": True,
        },
    )
    data = response.json()
    assert data["success"] is True, data
    assert target.is_file()

    blocked_path = tmp_path / "blocked.xml"
    response = client.post(
        "/api/runner/constants/write",
        json={
            "target_path": str(blocked_path),
            "bindings": [_bind(source="sn", sn="SC1", values={"gain_offset": 99})],
            "catalog": _catalog(),
            "header": _header(sn="SC1"),
        },
    )
    data = response.json()
    assert data["success"] is False
    assert data["blocked"] is True
    assert data["errors"][0]["key"] == "gain_offset"
    assert not blocked_path.exists()

    monkeypatch.setattr(
        const_runtime,
        "_load_tps",
        lambda tps_id: {
            "tps": {"id": tps_id, "machine_constants": [_bind()]},
            "source": "test",
            "error": "",
        },
    )
    response = client.get(
        "/api/runner/constants/resolve",
        params={"tps_id": "tps-1", "catalog": json.dumps(_catalog(), ensure_ascii=False)},
    )
    data = response.json()
    assert data["success"] is True
    assert data["models"][0]["fields"][0]["value"] == "0.0"
    assert data["catalog_source"] == "request"

    response = client.post(
        "/api/runner/constants/clear",
        json={"target_path": str(target), "keys": ["gain_offset"]},
    )
    data = response.json()
    assert data["success"] is True
    assert data["cleared"] == ["gain_offset"]

    response = client.post(
        "/api/runner/constants/validate",
        json={"bindings": [{"pn": "MOT-DRV-01", "model_key": "MOT_DRV"}], "catalog": _catalog()},
    )
    data = response.json()
    assert data["success"] is False
    assert "未找到任何模型" in data["message"]

    missing_target = tmp_path / "missing_model.xml"
    response = client.post(
        "/api/runner/constants/write",
        json={
            "target_path": str(missing_target),
            "bindings": [{"pn": "MOT-DRV-01", "model_key": "MOT_DRV"}],
            "catalog": _catalog(),
            "header": {"pn": "MOT-DRV-01", "module": "MOT_DRV"},
        },
    )
    data = response.json()
    assert data["success"] is False
    assert data["blocked"] is True
    assert "拒绝写入" in data["message"]
    assert not missing_target.exists()

    response = client.post(
        "/api/runner/constants/validate",
        json={"bindings": [{"model_key": "X"}], "catalog": _catalog()},
    )
    data = response.json()
    assert data["success"] is False
    assert "第 1 条" in data["message"]


def test_route_resolve_uses_local_db_catalog(tmp_path, monkeypatch):
    """GET resolve 未传 catalog 时从本地 ate.db 的模型库取数。"""
    import sqlite3

    db = tmp_path / "ate.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE constant_version (version TEXT PRIMARY KEY, is_active INTEGER DEFAULT 0);
        CREATE TABLE constant_model (
            version TEXT, pn TEXT, product TEXT, subsystem TEXT, model_key TEXT,
            name TEXT, fields_json TEXT, UNIQUE(version, pn, model_key)
        );
        CREATE TABLE constant_value (
            version TEXT, pn TEXT, model_key TEXT, field_key TEXT, value TEXT,
            PRIMARY KEY(version, pn, model_key, field_key)
        );
        """
    )
    fields = [
        {
            "key": "head_grade",
            "label": "读头等级",
            "type": "enum",
            "options": ["A", "B"],
            "default": "A",
        }
    ]
    conn.execute("INSERT INTO constant_version VALUES ('2026.11.01', 1)")
    conn.execute(
        "INSERT INTO constant_model VALUES (?,?,?,?,?,?,?)",
        ("2026.11.01", PN, "1000-A", "SPM", MK, "SPM 读头机常数",
         json.dumps(fields, ensure_ascii=False)),
    )
    conn.execute(
        "INSERT INTO constant_value VALUES (?,?,?,?,?)",
        ("2026.11.01", PN, MK, "head_grade", "B"),
    )
    conn.commit()
    conn.close()

    app = FastAPI()
    const_runtime.register_routes(app, tmp_path, db)
    monkeypatch.setattr(
        const_runtime,
        "_load_tps",
        lambda tps_id: {
            "tps": {"id": tps_id, "machine_constants": [_bind(source="sn", sn="SC1")]},
            "source": "test",
            "error": "",
        },
    )
    data = TestClient(app).get(
        "/api/runner/constants/resolve", params={"tps_id": "tps-db"}
    ).json()
    assert data["success"] is True, data
    assert data["catalog_source"] == "db"
    model = data["models"][0]
    assert model["version"] == "2026.11.01"
    assert model["fields"][0]["value"] == "B"  # source=sn 取库里该 SN 的标定值
