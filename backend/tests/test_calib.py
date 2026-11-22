# -*- coding: utf-8 -*-
"""calib.py 单元测试（SR02-AR06 / AR07 / AR08）。

全部用 tmp_path 隔离目录与 SQLite，不碰 repo/ 与后端真实 ate.db；不依赖真实 SSH：
paramiko 通过 monkeypatch 注入假模块。
"""

from __future__ import annotations

import hashlib
import json
import socket
import sqlite3
import sys
import types
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import calib  # noqa: E402


# ------------------------------------------------------------------ 夹具与模型

SPM_MODEL = {
    "module": "SPM_RH",
    "pn": "SPM-RH-01",
    "version": "2026.11.01",
    "meta": {"product": "1000-A", "subsystem": "SPM", "name": "SPM 读头机常数"},
    "fields": [
        {"key": "gain_offset", "label": "增益偏移", "type": "float", "unit": "mV",
         "default": 0.0, "min": -50.0, "max": 50.0},
        {"key": "bias_voltage", "label": "偏置电压", "type": "float", "unit": "V",
         "default": 1.85, "min": 1.5, "max": 2.2},
        {"key": "head_grade", "label": "读头等级", "type": "enum", "unit": "",
         "default": "B", "values": ["A", "B", "C"]},
    ],
}

MOT_MODEL = {
    "module": "MOT_DRV",
    "pn": "MOT-DRV-02",
    "version": "2026.11.02",
    "meta": {"product": "1000-A", "subsystem": "MOT", "name": "马达驱动板机常数"},
    "fields": [
        {"key": "drive_gain", "label": "驱动增益", "type": "float", "default": 1.0,
         "min": 0.8, "max": 1.2},
        {"key": "pole_pairs", "label": "极对数", "type": "int", "default": 8, "min": 1, "max": 16},
        {"key": "direction", "label": "旋转方向", "type": "enum", "default": "CW",
         "values": ["CW", "CCW"]},
    ],
}

CATALOG = {"SPM_RH": SPM_MODEL, "MOT_DRV": MOT_MODEL}

SPM_MAPPING = [
    {"source_field": "gain_raw", "target_field": "gain_offset", "scale": 1.0, "offset": 0.0,
     "required": True},
    {"source_field": "bias_raw", "target_field": "bias_voltage", "scale": 0.1, "offset": 0.01,
     "required": True},
    {"source_field": "grade", "target_field": "head_grade", "required": True},
]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """每个用例把模块级 base_dir/db 指向临时目录，避免写真实 backend。"""
    base = tmp_path / "base"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(calib, "_BASE_DIR", base, raising=False)
    monkeypatch.setattr(calib, "_DB_PATH", None, raising=False)


def _build(tmp_path, values, *, sn="SN0001", header=None, db=True):
    return calib.build_local(
        sn,
        "SPM-RH-01",
        "SPM_RH",
        values,
        header or {},
        tmp_path / "out",
        catalog=CATALOG,
        db_path=(tmp_path / "calib.db") if db else None,
    )


def _read_fields(path: Path) -> dict:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    return {node.get("key"): node.get("value") for node in root.findall("./Fields/Field")}


def _make_sqlite(tmp_path, rows=None) -> Path:
    db = tmp_path / "remote.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("create table calib(sn text, module text, gain_raw real, bias_raw real, grade text)")
        for row in rows if rows is not None else [("SN777", "SPM_RH", 4.5, 19.0, "A")]:
            conn.execute("insert into calib values (?, ?, ?, ?, ?)", row)
        conn.commit()
    finally:
        conn.close()
    return db


class _FakeAuth(Exception):
    pass


class _FakeChannel:
    def __init__(self, code: int = 0) -> None:
        self._code = code

    def recv_exit_status(self) -> int:
        return self._code


class _FakeStream:
    def __init__(self, data: str = "", code: int = 0) -> None:
        self._data = data.encode("utf-8")
        self.channel = _FakeChannel(code)

    def read(self) -> bytes:
        return self._data


class _FakeClient:
    def __init__(self, *, stdout="", stderr="", code=0, connect_exc=None, exec_exc=None):
        self.stdout = stdout
        self.stderr = stderr
        self.code = code
        self.connect_exc = connect_exc
        self.exec_exc = exec_exc
        self.commands: list[str] = []
        self.connect_kwargs: dict = {}
        self.closed = False
        self.policy = None

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, **kwargs):
        self.connect_kwargs = kwargs
        if self.connect_exc:
            raise self.connect_exc

    def exec_command(self, command, timeout=None, environment=None):
        self.commands.append(command)
        if self.exec_exc:
            raise self.exec_exc
        return _FakeStream(), _FakeStream(self.stdout, self.code), _FakeStream(self.stderr)

    def close(self):
        self.closed = True


def _install_fake_ssh(monkeypatch, **kwargs) -> _FakeClient:
    client = _FakeClient(**kwargs)
    module = types.SimpleNamespace(
        SSHClient=lambda: client,
        AutoAddPolicy=lambda: object(),
        AuthenticationException=_FakeAuth,
    )
    monkeypatch.setattr(calib, "_import_paramiko", lambda: module)
    return client


PG_TARGET = {
    "id": "pg1",
    "name": "下位机 PostgreSQL",
    "host": "10.1.2.3",
    "port": 5432,
    "username": "cal",
    "auth": "password",
    "password": "s3cret",
    "db": "pgsql",
    "database": "caldb",
}


# ------------------------------------------------------------------ 本机标定


def test_build_local_generates_sn_module_file_with_values(tmp_path):
    result = _build(
        tmp_path,
        {"gain_offset": 12.5, "bias_voltage": 1.92, "head_grade": "A"},
        header={"operator": "张工", "tpsId": "TPS-CAL-01", "taskId": "T-9"},
    )

    assert result["success"], result
    path = Path(result["path"])
    assert path.name == "SN0001_SPM_RH.xml"
    assert path.is_file()

    root = ET.fromstring(path.read_text(encoding="utf-8"))
    assert root.tag == "MachineConstants"
    assert root.get("schema") == calib.SCHEMA
    assert root.get("module") == "SPM_RH"
    assert root.get("pn") == "SPM-RH-01"
    assert root.get("calibrated") == "true"
    assert root.get("sn") == "SN0001"

    values = _read_fields(path)
    assert float(values["gain_offset"]) == 12.5
    assert float(values["bias_voltage"]) == 1.92
    assert values["head_grade"] == "A"

    calibration = root.find("Calibration")
    assert calibration is not None
    assert calibration.get("sn") == "SN0001"
    assert calibration.get("operator") == "张工"
    assert calibration.get("tpsId") == "TPS-CAL-01"
    assert calibration.get("taskId") == "T-9"
    assert calibration.get("at")

    assert result["applied"] == {"gain_offset": 12.5, "bias_voltage": 1.92, "head_grade": "A"}
    assert result["calibration_id"] == 1


def test_build_local_rejects_out_of_range_and_lists_all_fields(tmp_path):
    result = _build(tmp_path, {"gain_offset": 99, "bias_voltage": 1.0, "head_grade": "D"})

    assert result["success"] is False
    assert result["error_code"] == "value_out_of_range"
    assert "校验未通过" in result["message"]
    violations = {item["key"]: item for item in result["violations"]}
    assert list(violations) == ["gain_offset", "bias_voltage", "head_grade"]
    assert "上限" in violations["gain_offset"]["reason"]
    assert "下限" in violations["bias_voltage"]["reason"]
    assert "枚举" in violations["head_grade"]["reason"]
    assert not (tmp_path / "out" / "SN0001_SPM_RH.xml").exists()


def test_build_local_checksum_matches_file_and_db(tmp_path):
    result = _build(tmp_path, {"gain_offset": 7.5}, header={"operator": "李工"})
    assert result["success"], result

    raw = Path(result["path"]).read_bytes()
    assert result["checksum"] == hashlib.sha256(raw).hexdigest()
    assert len(result["checksum"]) == 64
    assert result["checksum"] == result["checksum"].lower()

    conn = sqlite3.connect(tmp_path / "calib.db")
    try:
        row = conn.execute(
            "select sn, pn, model_key, checksum, values_json, operator from const_calibration where id=?",
            (result["calibration_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "SN0001"
    assert row[1] == "SPM-RH-01"
    assert row[2] == "SPM_RH"
    assert row[3] == result["checksum"]
    assert json.loads(row[4])["gain_offset"] == 7.5
    assert row[5] == "李工"


def test_build_local_fills_defaults_for_unspecified_fields(tmp_path):
    result = _build(tmp_path, {"gain_offset": 1.0})
    assert result["success"], result
    assert result["defaulted"] == ["bias_voltage", "head_grade"]
    values = _read_fields(Path(result["path"]))
    assert float(values["bias_voltage"]) == 1.85
    assert values["head_grade"] == "B"


def test_build_local_rejects_unknown_field(tmp_path):
    result = _build(tmp_path, {"gain_offset": 1.0, "no_such_field": 3})
    assert result["success"] is False
    assert result["error_code"] == "unknown_fields"
    assert result["unknown_fields"] == ["no_such_field"]


def test_build_local_rejects_field_without_value_or_default(tmp_path):
    model = {
        "module": "SPM_RH",
        "pn": "SPM-RH-01",
        "fields": [{"key": "trim", "type": "float", "min": 0, "max": 1}],
    }
    result = calib.build_local(
        "SN0002", "SPM-RH-01", "SPM_RH", {}, {}, tmp_path / "out", catalog={"SPM_RH": model}
    )
    assert result["success"] is False
    assert result["error_code"] == "missing_values"
    assert result["missing_fields"] == ["trim"]


def test_build_local_rejects_bad_sn(tmp_path):
    result = _build(tmp_path, {"gain_offset": 1.0}, sn="../evil")
    assert result["success"] is False
    assert result["error_code"] == "invalid_sn"


def test_build_local_loads_demo_model_from_testresource(tmp_path):
    calib.configure(BACKEND, None)
    result = calib.build_local(
        "SN-DEMO-1", "SPM-RH-01", "SPM_RH", {"gain_offset": 3.0}, {}, tmp_path / "out"
    )
    assert result["success"], result
    assert result["model_key"] == "SPM_RH"
    assert Path(result["path"]).name == "SN-DEMO-1_SPM_RH.xml"


# ------------------------------------------------------------------ 远程取数


def test_sqlite_local_query_generates_file(tmp_path):
    db = _make_sqlite(tmp_path)
    target = {"id": "t1", "name": "本机模拟下位机", "db": "sqlite",
              "db_path": str(db), "local": True, "host": ""}

    result = calib.fetch_remote(
        target,
        "select sn, module, gain_raw, bias_raw, grade from calib",
        SPM_MAPPING,
        tmp_path / "out",
        pn="SPM-RH-01",
        model_key="SPM_RH",
        catalog=CATALOG,
        db_path=tmp_path / "archive.db",
    )

    assert result["success"], result
    assert result["count"] == 1
    assert result["rows"] == 1
    assert "sqlite3" in result["command"]
    assert "select sn" in result["command"]

    item = result["files"][0]
    assert item["file"] == "SN777_SPM_RH.xml"
    assert item["module"] == "SPM_RH"
    values = _read_fields(Path(item["path"]))
    assert float(values["gain_offset"]) == 4.5
    assert abs(float(values["bias_voltage"]) - 1.91) < 1e-9
    assert values["head_grade"] == "A"


def test_pgsql_command_assembly_and_ssh_fetch(tmp_path, monkeypatch):
    client = _install_fake_ssh(
        monkeypatch,
        stdout="sn|module|gain_raw|bias_raw|grade\nSN900|SPM_RH|2.0|19.0|B\n",
    )
    target = dict(PG_TARGET, header_row=True)

    result = calib.fetch_remote(
        target,
        "select sn, module, gain_raw, bias_raw, grade from calib",
        SPM_MAPPING,
        tmp_path / "out",
        pn="SPM-RH-01",
        model_key="SPM_RH",
        catalog=CATALOG,
    )

    assert result["success"], result
    assert client.commands, "应通过 paramiko 执行命令"
    command = client.commands[0]
    assert command.startswith("psql ")
    assert "-A" in command
    assert "-c" in command
    assert "select sn" in command
    assert "s3cret" not in command, "口令不得拼进命令行"
    assert client.connect_kwargs["password"] == "s3cret"
    assert client.connect_kwargs["hostname"] == "10.1.2.3"
    assert client.closed is True

    values = _read_fields(Path(result["files"][0]["path"]))
    assert float(values["gain_offset"]) == 2.0
    assert abs(float(values["bias_voltage"]) - 1.91) < 1e-9
    assert values["head_grade"] == "B"


def test_pgsql_header_pipe_parsing_without_header_flag(tmp_path, monkeypatch):
    """默认 psql -A -t 无表头：映射按顺序对应列。"""
    _install_fake_ssh(monkeypatch, stdout="2.5|19.0|C\n")
    mapping = [
        {"source_field": "gain_raw", "target_field": "gain_offset", "scale": 2, "offset": -1,
         "required": True},
        {"source_field": "bias_raw", "target_field": "bias_voltage", "scale": 0.1, "offset": 0.01,
         "required": True},
        {"source_field": "grade", "target_field": "head_grade", "required": True},
    ]
    result = calib.fetch_remote(
        PG_TARGET, "select gain_raw, bias_raw, grade from calib", mapping, tmp_path / "out",
        pn="SPM-RH-01", model_key="SPM_RH", sn="SN5", catalog=CATALOG,
    )
    assert result["success"], result
    assert "-t" in result["command"]
    values = _read_fields(Path(result["files"][0]["path"]))
    assert float(values["gain_offset"]) == 4.0
    assert abs(float(values["bias_voltage"]) - 1.91) < 1e-9
    assert values["head_grade"] == "C"


def test_multiple_modules_generate_multiple_files(tmp_path, monkeypatch):
    header = "sn|module|gain_raw|bias_raw|grade|drive_raw|poles|direction"
    rows = [
        "SN1|SPM_RH|1.0|19.0|A||||",
        "SN1|MOT_DRV||||1.05|8|CCW",
    ]
    _install_fake_ssh(monkeypatch, stdout="\n".join([header, *rows]) + "\n")
    mapping = [
        {"source_field": "gain_raw", "target_field": "gain_offset", "required": False},
        {"source_field": "bias_raw", "target_field": "bias_voltage", "scale": 0.1, "offset": 0.01,
         "required": False},
        {"source_field": "grade", "target_field": "head_grade", "required": False},
        {"source_field": "drive_raw", "target_field": "drive_gain", "required": False},
        {"source_field": "poles", "target_field": "pole_pairs", "required": False},
        {"source_field": "direction", "target_field": "direction", "required": False},
    ]
    result = calib.fetch_remote(
        dict(PG_TARGET, header_row=True),
        "select * from calib",
        mapping,
        tmp_path / "out",
        catalog=CATALOG,
    )

    assert result["success"], result
    assert result["count"] == 2
    names = sorted(item["file"] for item in result["files"])
    assert names == ["SN1_MOT_DRV.xml", "SN1_SPM_RH.xml"]
    mot = next(item for item in result["files"] if item["module"] == "MOT_DRV")
    values = _read_fields(Path(mot["path"]))
    assert float(values["drive_gain"]) == 1.05
    assert int(values["pole_pairs"]) == 8
    assert values["direction"] == "CCW"


def test_remote_connect_failed(tmp_path, monkeypatch):
    _install_fake_ssh(monkeypatch, connect_exc=OSError("Connection refused"))
    result = calib.fetch_remote(
        PG_TARGET, "select 1", SPM_MAPPING, tmp_path / "out",
        pn="SPM-RH-01", model_key="SPM_RH", catalog=CATALOG,
    )
    assert result["success"] is False
    assert result["error_code"] == "remote_connect_failed"
    assert "无法连接下位机" in result["message"]


def test_remote_auth_failed(tmp_path, monkeypatch):
    _install_fake_ssh(monkeypatch, connect_exc=_FakeAuth("Authentication failed."))
    result = calib.fetch_remote(
        PG_TARGET, "select 1", SPM_MAPPING, tmp_path / "out",
        pn="SPM-RH-01", model_key="SPM_RH", catalog=CATALOG,
    )
    assert result["success"] is False
    assert result["error_code"] == "remote_auth_failed"
    assert "认证失败" in result["message"]


def test_remote_query_timeout(tmp_path, monkeypatch):
    _install_fake_ssh(monkeypatch, exec_exc=socket.timeout("timed out"))
    result = calib.fetch_remote(
        dict(PG_TARGET, timeout=3), "select 1", SPM_MAPPING, tmp_path / "out",
        pn="SPM-RH-01", model_key="SPM_RH", catalog=CATALOG,
    )
    assert result["success"] is False
    assert result["error_code"] == "remote_query_timeout"
    assert "超时" in result["message"]


def test_remote_empty_rows(tmp_path, monkeypatch):
    _install_fake_ssh(monkeypatch, stdout="\n")
    result = calib.fetch_remote(
        PG_TARGET, "select 1", SPM_MAPPING, tmp_path / "out",
        pn="SPM-RH-01", model_key="SPM_RH", catalog=CATALOG,
    )
    assert result["success"] is False
    assert result["error_code"] == "remote_empty_rows"
    assert "空数据" in result["message"]
    assert not (tmp_path / "out" / "SN_SPM_RH.xml").exists()


def test_required_mapping_field_missing(tmp_path):
    db = _make_sqlite(tmp_path, rows=[("SN778", "SPM_RH", 1.0, None, "A")])
    target = {"id": "t1", "db": "sqlite", "db_path": str(db), "local": True, "host": ""}
    result = calib.fetch_remote(
        target,
        "select sn, module, gain_raw, bias_raw, grade from calib",
        SPM_MAPPING,
        tmp_path / "out",
        pn="SPM-RH-01",
        model_key="SPM_RH",
        catalog=CATALOG,
    )
    assert result["success"] is False
    assert result["error_code"] == "mapping_missing"
    assert result["missing"] == ["bias_raw"]
    assert "bias_raw" in result["message"]


def test_pgsql_requires_no_empty_dsn_but_builds_command(tmp_path):
    command = calib.build_command(PG_TARGET, "select 1")
    assert command.startswith("psql ")
    assert "-c 'select 1'" in command
    assert "-h 10.1.2.3" in command
    assert "-p 5432" in command
    assert "-U cal" in command


def test_sqlite_remote_command_keeps_posix_path(tmp_path):
    command = calib.build_command(
        {"host": "10.0.0.8", "username": "cal", "db": "sqlite",
         "db_path": "/opt/cal/calib.db"},
        "select 1",
    )
    assert command.startswith("sqlite3 ")
    assert "/opt/cal/calib.db" in command
    assert "\\opt" not in command


# ------------------------------------------------------------------ 连接配置


def test_get_targets_never_echoes_password_or_key_path(tmp_path):
    saved = calib.upsert_target(
        {
            "name": "下位机 A",
            "host": "10.0.0.5",
            "port": 22,
            "username": "cal",
            "auth": "password",
            "password": "P@ssw0rd!",
            "db": "pgsql",
            "database": "caldb",
            "dsn": "host=10.0.0.5 password=P@ssw0rd!",
            "query_template": "select * from calibration",
        }
    )
    assert saved["success"], saved
    assert saved["target"]["password_set"] is True
    assert "P@ssw0rd!" not in json.dumps(saved, ensure_ascii=False)
    assert "password_enc" not in saved["target"]

    listing = calib.list_targets()
    assert listing["success"] and listing["count"] == 1
    payload = json.dumps(listing, ensure_ascii=False)
    assert "P@ssw0rd!" not in payload
    assert "password_enc" not in payload
    assert listing["targets"][0]["password_set"] is True
    assert listing["targets"][0]["key_path_set"] is False

    disk = (tmp_path / "base" / "remote_targets.json").read_text(encoding="utf-8")
    assert "P@ssw0rd!" not in disk
    assert "password_enc" in disk  # 只允许混淆串落盘

    key_saved = calib.upsert_target(
        {
            "name": "下位机 B",
            "host": "10.0.0.6",
            "username": "cal",
            "auth": "key",
            "key_path": r"C:\keys\id_rsa",
            "db": "pgsql",
            "database": "caldb",
            "password": "passphrase",
        }
    )
    assert key_saved["success"], key_saved
    assert key_saved["target"]["key_path_set"] is True
    assert key_saved["target"]["key_path_hint"] == "id_rsa"
    assert r"C:\keys\id_rsa" not in json.dumps(key_saved, ensure_ascii=False)
    assert r"C:\keys\id_rsa" not in (tmp_path / "base" / "remote_targets.json").read_text(
        encoding="utf-8"
    )


def test_target_rejects_unsupported_db(tmp_path):
    result = calib.upsert_target(
        {"name": "mysql 下位机", "host": "10.0.0.7", "db": "mysql", "db_path": "/tmp/a.db"}
    )
    assert result["success"] is False
    assert "sqlite|pgsql" in result["message"]
    assert not (tmp_path / "base" / "remote_targets.json").exists()


def test_connectivity_test_sqlite_returns_elapsed(tmp_path):
    db = _make_sqlite(tmp_path)
    saved = calib.upsert_target(
        {"name": "本机 SQLite", "host": "", "db": "sqlite", "db_path": str(db), "local": True}
    )
    assert saved["success"], saved

    result = calib.test_target(saved["target"]["id"])
    assert result["success"] is True
    assert result["ok"] is True
    assert isinstance(result["elapsed_ms"], int) and result["elapsed_ms"] >= 0
    assert "连通正常" in result["message"]


def test_connectivity_test_pgsql_uses_psql_and_missing_db_file(tmp_path, monkeypatch):
    client = _install_fake_ssh(monkeypatch, stdout="1\n")
    saved = calib.upsert_target(dict(PG_TARGET, name="PG 下位机"))
    assert saved["success"], saved

    result = calib.test_target(saved["target"]["id"])
    assert result["ok"] is True, result
    assert isinstance(result["elapsed_ms"], int)
    assert client.commands and "psql" in client.commands[0]
    assert "select 1" in client.commands[0]

    missing = calib.upsert_target(
        {"name": "不存在的库", "host": "", "db": "sqlite", "db_path": str(tmp_path / "nope.db"),
         "local": True}
    )
    assert missing["success"], missing
    bad = calib.test_target(missing["target"]["id"])
    assert bad["ok"] is False
    assert "不存在" in bad["message"]


# ------------------------------------------------------------------ 路由


def test_routes_local_build_and_targets_no_password(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    base = tmp_path / "base"
    model_dir = base / "testresource" / "calib_demo"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "SPM_RH.xml").write_text(
        (BACKEND / "testresource" / "calib_demo" / "SPM_RH.xml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    app = FastAPI()
    calib.register_routes(app, base, db_path=tmp_path / "api.db")
    client = TestClient(app)

    built = client.post(
        "/api/runner/calib/local/build",
        json={
            "sn": "SN-API-1",
            "pn": "SPM-RH-01",
            "model_key": "SPM_RH",
            "values": {"gain_offset": 5.5},
            "header": {"operator": "api"},
        },
    )
    body = built.json()
    assert body["success"] is True, body
    assert Path(body["path"]).name == "SN-API-1_SPM_RH.xml"

    rejected = client.post(
        "/api/runner/calib/local/build",
        json={"sn": "SN-API-2", "pn": "SPM-RH-01", "model_key": "SPM_RH",
              "values": {"gain_offset": 500}},
    )
    rejected_body = rejected.json()
    assert rejected_body["success"] is False
    assert rejected_body["error_code"] == "value_out_of_range"
    assert "gain_offset" in rejected_body["message"]

    put = client.put(
        "/api/runner/remote-targets",
        json={"name": "API 下位机", "host": "10.9.9.9", "username": "cal",
              "auth": "password", "password": "TopSecret", "db": "pgsql",
              "database": "caldb"},
    )
    assert put.json()["success"] is True, put.json()
    listing = client.get("/api/runner/remote-targets").json()
    assert listing["success"] is True
    assert listing["count"] == 1
    assert "TopSecret" not in json.dumps(listing, ensure_ascii=False)
    assert listing["targets"][0]["password_set"] is True

    bad_db = client.put(
        "/api/runner/remote-targets",
        json={"name": "MySQL", "host": "10.9.9.9", "db": "mysql", "db_path": "/x.db"},
    )
    assert bad_db.json()["success"] is False
    assert "sqlite|pgsql" in bad_db.json()["message"]

    unknown = client.post("/api/runner/remote-targets/not-exist/test")
    assert unknown.json()["success"] is False
    assert "未找到" in unknown.json()["message"]
