# -*- coding: utf-8 -*-
"""ATE Runner - 测试台注册信息 / 设备 driver 配置的 SQLite 侧车

设计要点
--------
* **JSON 是注册页的唯一事实源**：`testresource/testbenches.json` 由 `testbench_registry.py`
  以原子写维护，前端注册/改设备属性都落在这里。
* **SQLite 是运行时读取源**：注册库每次变更（注册 / 改设备属性 / 恢复默认 / 自检写回）
  都会调用 `sync_from_registry()` 把注册信息**全量投影**到 `bench_registry` /
  `device_registry` 两张关系表；`ate.db` 与注册库不一致时，以注册库为准重建投影。
* **谁读它**：TPS 运行时由公共 `conftest.py` 读取 `device_registry` 拿设备连接参数
  （IP/端口/串口/波特率/资源地址）与 driver 型号（`driver` 列），再交给 `drivers` 包实例化。
* 表结构变更走 `SCHEMA_SQL` + `init_db()`（幂等 `CREATE TABLE IF NOT EXISTS`）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parent          # D:\ATE\backend
DB_PATH = Path(os.environ.get("ATE_DB_PATH") or (BASE_DIR / "ate.db"))
REGISTRY_FILE = BASE_DIR / "testresource" / "testbenches.json"

_LOCK = threading.RLock()

# ---------------------------------------------------------------- 表结构

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bench_registry (
    id                TEXT PRIMARY KEY,
    preset_id         TEXT,
    preset_name       TEXT,
    title             TEXT,
    serial            TEXT,
    line              TEXT,
    station           TEXT,
    location          TEXT,
    status            TEXT,
    step              INTEGER,
    registered_at     TEXT,
    updated_at        TEXT,
    verify_overall    TEXT,
    verify_checked_at TEXT,
    device_count      INTEGER DEFAULT 0,
    payload_json      TEXT
);

CREATE TABLE IF NOT EXISTS device_registry (
    bench_id      TEXT NOT NULL,
    device_id     TEXT NOT NULL,
    name          TEXT,
    model         TEXT,
    vendor        TEXT,
    category      TEXT,
    role          TEXT,
    programmable  INTEGER DEFAULT 0,
    interface     TEXT,
    protocol      TEXT,
    host          TEXT,
    port          INTEGER,
    serial_port   TEXT,
    baudrate      INTEGER,
    address       TEXT,
    channel       TEXT,
    required      INTEGER DEFAULT 1,
    configured    INTEGER DEFAULT 0,
    note          TEXT,
    driver        TEXT,
    timeout       REAL,
    config_json   TEXT,
    PRIMARY KEY (bench_id, device_id)
);

CREATE INDEX IF NOT EXISTS idx_device_bench ON device_registry (bench_id);

CREATE TABLE IF NOT EXISTS bench_sync_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    synced_at    TEXT,
    bench_count  INTEGER,
    device_count INTEGER,
    source       TEXT
);
"""


# ---------------------------------------------------------------- 连接 / 初始化


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """建立连接（行工厂 Row，便于按列名取用）"""
    conn = sqlite3.connect(str(path or DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Optional[Path] = None) -> Path:
    """创建 / 升级侧车表（幂等，可重复调用）"""
    with _LOCK:
        with connect(path) as conn:
            conn.executescript(SCHEMA_SQL)
    return path or DB_PATH


def _now() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 写入（注册库 -> SQLite）


def _driver_key_of(dev: dict) -> str:
    """解析该设备应使用的 driver 规格名（失败不抛异常，回退 generic-scpi）"""
    try:
        from drivers.factory import resolve_driver_key

        return resolve_driver_key(dev)
    except Exception:
        return "generic-scpi"


def _device_rows(bench: dict) -> list[dict]:
    rows: list[dict] = []
    for dev in bench.get("devices") or []:
        if not isinstance(dev, dict):
            continue
        dev_id = str(dev.get("id") or "").strip()
        if not dev_id:
            continue
        cfg = {
            "bench_id": bench.get("id", ""),
            "device_id": dev_id,
            "interface": (dev.get("interface") or "NONE").upper(),
            "protocol": dev.get("protocol") or "",
            "host": dev.get("host") or "",
            "port": dev.get("port"),
            "serial_port": dev.get("serial_port") or "",
            "baudrate": dev.get("baudrate"),
            "address": dev.get("address") or "",
            "channel": dev.get("channel") or "",
            "programmable": bool(dev.get("programmable")),
            "category": dev.get("category") or "",
            "model": dev.get("model") or "",
        }
        rows.append(
            {
                "bench_id": bench.get("id", ""),
                "device_id": dev_id,
                "name": dev.get("name") or "",
                "model": dev.get("model") or "",
                "vendor": dev.get("vendor") or "",
                "category": dev.get("category") or "",
                "role": dev.get("role") or "",
                "programmable": 1 if dev.get("programmable") else 0,
                "interface": (dev.get("interface") or "NONE").upper(),
                "protocol": dev.get("protocol") or "",
                "host": dev.get("host") or "",
                "port": dev.get("port"),
                "serial_port": dev.get("serial_port") or "",
                "baudrate": dev.get("baudrate"),
                "address": dev.get("address") or "",
                "channel": dev.get("channel") or "",
                "required": 1 if dev.get("required", True) else 0,
                "configured": 1 if dev.get("configured") else 0,
                "note": dev.get("note") or "",
                "driver": _driver_key_of(cfg),
                "timeout": 2.0,
                "config_json": json.dumps(cfg, ensure_ascii=False),
            }
        )
    return rows


def sync_from_bench_rows(benches: list[dict], source: str = "registry", path: Optional[Path] = None) -> dict:
    """把测试台列表**全量投影**到 SQLite（删旧插新，单事务，避免半更新状态）"""
    with _LOCK:
        init_db(path)
        bench_rows = []
        device_rows: list[dict] = []
        for bench in benches or []:
            if not isinstance(bench, dict) or not bench.get("id"):
                continue
            st = bench.get("station") or {}
            ver = bench.get("verification") or {}
            devs = _device_rows(bench)
            device_rows.extend(devs)
            bench_rows.append(
                {
                    "id": bench.get("id", ""),
                    "preset_id": bench.get("preset_id", ""),
                    "preset_name": bench.get("preset_name", ""),
                    "title": bench.get("title", ""),
                    "serial": bench.get("serial", ""),
                    "line": st.get("line", ""),
                    "station": st.get("station", ""),
                    "location": st.get("location", ""),
                    "status": bench.get("status", ""),
                    "step": bench.get("step"),
                    "registered_at": bench.get("registered_at", ""),
                    "updated_at": bench.get("updated_at", ""),
                    "verify_overall": ver.get("overall", ""),
                    "verify_checked_at": ver.get("checked_at", ""),
                    "device_count": len(devs),
                    "payload_json": json.dumps(bench, ensure_ascii=False),
                }
            )
        with connect(path) as conn:
            conn.execute("DELETE FROM bench_registry")
            conn.execute("DELETE FROM device_registry")
            conn.executemany(
                """INSERT INTO bench_registry
                   (id, preset_id, preset_name, title, serial, line, station, location, status, step,
                    registered_at, updated_at, verify_overall, verify_checked_at, device_count, payload_json)
                   VALUES (:id, :preset_id, :preset_name, :title, :serial, :line, :station, :location,
                           :status, :step, :registered_at, :updated_at, :verify_overall,
                           :verify_checked_at, :device_count, :payload_json)""",
                bench_rows,
            )
            conn.executemany(
                """INSERT INTO device_registry
                   (bench_id, device_id, name, model, vendor, category, role, programmable, interface,
                    protocol, host, port, serial_port, baudrate, address, channel, required, configured,
                    note, driver, timeout, config_json)
                   VALUES (:bench_id, :device_id, :name, :model, :vendor, :category, :role, :programmable,
                           :interface, :protocol, :host, :port, :serial_port, :baudrate, :address,
                           :channel, :required, :configured, :note, :driver, :timeout, :config_json)""",
                device_rows,
            )
            conn.execute(
                "INSERT INTO bench_sync_log (synced_at, bench_count, device_count, source) VALUES (?, ?, ?, ?)",
                (_now(), len(bench_rows), len(device_rows), source),
            )
            conn.commit()
    return {
        "success": True,
        "synced_at": _now(),
        "bench_count": len(bench_rows),
        "device_count": len(device_rows),
        "source": source,
        "db_path": str(path or DB_PATH),
    }


def sync_from_registry(path: Optional[Path] = None, registry_file: Optional[Path] = None) -> dict:
    """读取注册库 JSON 并同步到 SQLite（注册库是事实源）"""
    reg_file = Path(registry_file or REGISTRY_FILE)
    try:
        data = json.loads(reg_file.read_text(encoding="utf-8"))
    except Exception as e:  # 注册库还不存在 / 损坏 -> 清空投影，保证运行时不会读到幽灵设备
        benches: list[dict] = []
        sync_from_bench_rows(benches, source=f"registry-missing: {e}", path=path)
        return {"success": False, "message": f"注册库不可读: {e}", "bench_count": 0, "device_count": 0}
    if isinstance(data, list):
        benches = data
    else:
        benches = data.get("testbenches") or []
    return sync_from_bench_rows(benches, source="registry-json", path=path)


# ---------------------------------------------------------------- 读取（运行时）


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def list_benches(path: Optional[Path] = None) -> list[dict]:
    with connect(path) as conn:
        return _rows(conn.execute("SELECT * FROM bench_registry ORDER BY registered_at, id"))


def get_bench(bench_id: str, path: Optional[Path] = None) -> Optional[dict]:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM bench_registry WHERE id = ?", (bench_id,)).fetchone()
    return dict(row) if row else None


def find_bench(
    bench_id: str = "",
    preset_id: str = "",
    serial: str = "",
    path: Optional[Path] = None,
) -> Optional[dict]:
    """按 编号 / 预设类型 / 序列号 依次匹配测试台（用于 TPS 未显式指定测试台时的自动选取）"""
    with connect(path) as conn:
        if bench_id:
            row = conn.execute("SELECT * FROM bench_registry WHERE id = ?", (bench_id,)).fetchone()
            if row:
                return dict(row)
        if serial:
            row = conn.execute("SELECT * FROM bench_registry WHERE serial = ?", (serial,)).fetchone()
            if row:
                return dict(row)
        if preset_id:
            row = conn.execute(
                "SELECT * FROM bench_registry WHERE preset_id = ? ORDER BY registered_at, id LIMIT 1",
                (preset_id,),
            ).fetchone()
            if row:
                return dict(row)
    return None


def get_devices(bench_id: str, path: Optional[Path] = None) -> list[dict]:
    with connect(path) as conn:
        return _rows(
            conn.execute(
                "SELECT * FROM device_registry WHERE bench_id = ? ORDER BY rowid", (bench_id,)
            )
        )


def get_device(bench_id: str, device_id: str, path: Optional[Path] = None) -> Optional[dict]:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM device_registry WHERE bench_id = ? AND device_id = ?", (bench_id, device_id)
        ).fetchone()
    return dict(row) if row else None


CATEGORY_ALIASES: dict[str, list[str]] = {
    "dmm": ["万用表", "dmm", "multimeter"],
    "psu": ["电源", "psu", "power supply"],
    "eload": ["电子负载", "eload", "load"],
    "awg": ["信号源", "波形", "awg", "generator"],
    "scope": ["示波器", "scope", "oscilloscope"],
    "motion": ["运动控制", "转速", "spin", "motion"],
    "fixture": ["夹具", "探针", "fixture", "probe"],
}


def _expand_wants(values: list[str]) -> list[str]:
    """把别名（如 role=dmm）扩展成中英文关键词集合，便于跨命名匹配设备"""
    out: list[str] = []
    for raw in values:
        want = str(raw or "").strip().lower()
        if not want:
            continue
        out.append(want)
        for key, group in CATEGORY_ALIASES.items():
            if want == key or any(want == str(g).lower() for g in group):
                out.extend(str(g).lower() for g in group)
                out.append(key)
    return sorted(set(out))


def match_device(bench_id: str, spec: dict, path: Optional[Path] = None) -> Optional[dict]:
    """按 device_id / role / category / model 关键词在测试台设备表里找一台设备

    `spec` 可含: `device_id` / `device` / `role` / `category` / `model`
    role 支持英文别名（`dmm` / `psu` / `eload` / `awg` / `scope` / `motion` / `fixture`），
    会自动扩展成「万用表 / 电源 / 电子负载 / 信号源 / 示波器 / 运动控制 / 夹具」再匹配。
    """
    devices = get_devices(bench_id, path)
    if not devices:
        return None
    dev_id = str(spec.get("device_id") or spec.get("device") or "").strip()
    if dev_id:
        for d in devices:
            if d["device_id"] == dev_id:
                return d
    wants = _expand_wants([spec.get(k) for k in ("role", "category", "model", "name", "keyword")])
    for want in wants:
        for d in devices:
            hay = " ".join(
                str(d.get(k) or "") for k in ("role", "category", "model", "name", "vendor", "device_id")
            ).lower()
            if want in hay:
                return d
    return None


def stats(path: Optional[Path] = None) -> dict:
    init_db(path)
    with connect(path) as conn:
        benches = conn.execute("SELECT COUNT(*) AS c FROM bench_registry").fetchone()["c"]
        devices = conn.execute("SELECT COUNT(*) AS c FROM device_registry").fetchone()["c"]
        log = conn.execute(
            "SELECT synced_at, bench_count, device_count, source FROM bench_sync_log ORDER BY id DESC LIMIT 5"
        ).fetchall()
    return {
        "success": True,
        "db_path": str(path or DB_PATH),
        "bench_count": benches,
        "device_count": devices,
        "recent_sync": [dict(r) for r in log],
    }
