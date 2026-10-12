# -*- coding: utf-8 -*-
"""ATE Runner - 云端数据库（演示）上报通道

定位
----
生产模式（`run_mode=production`）的测试结果在收尾后通过本模块上报「云端数据库」。
现场部署时把 `_do_upload()` 换成真实 MES / 云端 HTTP 接口即可，其余逻辑（幂等键、
失败不阻断、待重传标记）保持不变。

演示实现
--------
* 「云端」落在 `backend/cloud_demo.db`（SQLite），表 `cloud_test_records`；
* 上报**幂等**：以 `task_id` 为业务主键，重复上报走 UPDATE，不产生重复行；
* 断网 / 云端不可用时返回 `ok=False`，**不抛异常**，不影响本轮测试结论；
  记录库把该条标记为 `pending`，可事后手动重传（`POST /api/cloud/upload/{task_id}`）。

环境变量
--------
* `ATE_CLOUD_ENDPOINT`  云端接口地址（仅展示；演示版不发真实网络请求）
* `ATE_CLOUD_OFFLINE`   置 `1` 时强制模拟「云端不可达」，用于演练断网路径
* `ATE_CLOUD_DB_PATH`   演示库落盘位置，默认 `backend/cloud_demo.db`
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DEFAULT_ENDPOINT = "https://cloud.ate-demo.local/api/v1/test-records"

# 演示上报的模拟网络耗时（秒），让界面上的「上传中」状态可见
_SIMULATED_LATENCY_S = 0.35

_LOCK = threading.RLock()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cloud_test_records (
    cloud_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id          TEXT UNIQUE,           -- 幂等键：同一轮测试重复上报只更新不新增
    work_order       TEXT,                  -- 生产模式工单号（12 位）
    uut              TEXT,
    project          TEXT,
    dut              TEXT,
    tps_id           TEXT,
    tps_name         TEXT,
    result           TEXT,                  -- OK / NOK
    status           TEXT,                  -- completed / failed
    passed           INTEGER DEFAULT 0,
    failed           INTEGER DEFAULT 0,
    skipped          INTEGER DEFAULT 0,
    total            INTEGER DEFAULT 0,
    duration         REAL DEFAULT 0,
    operator         TEXT,                  -- 操作员工号
    equipment_serial TEXT,                  -- 测试台唯一编号
    batch            TEXT,
    part_no          TEXT,
    run_mode         TEXT,                  -- production / debug
    device_mode      TEXT,                  -- real / simulate
    report_file      TEXT,
    started_at       TEXT,
    ended_at         TEXT,
    uploaded_at      TEXT,
    source           TEXT                   -- auto（收尾自动上报）/ manual（手动重传）
);

CREATE INDEX IF NOT EXISTS idx_cloud_wo  ON cloud_test_records (work_order);
CREATE INDEX IF NOT EXISTS idx_cloud_uut ON cloud_test_records (uut);
"""

# 上报字段 = 表列（除自增主键与上传时间）
_PAYLOAD_FIELDS = (
    "task_id",
    "work_order",
    "uut",
    "project",
    "dut",
    "tps_id",
    "tps_name",
    "result",
    "status",
    "passed",
    "failed",
    "skipped",
    "total",
    "duration",
    "operator",
    "equipment_serial",
    "batch",
    "part_no",
    "run_mode",
    "device_mode",
    "report_file",
    "started_at",
    "ended_at",
    "source",
)


# ---------------------------------------------------------------- 配置


def endpoint() -> str:
    """云端接口地址（演示版仅用于展示）"""
    return (os.environ.get("ATE_CLOUD_ENDPOINT") or DEFAULT_ENDPOINT).strip()


def cloud_db_path() -> Path:
    """演示「云端库」落盘位置"""
    env = os.environ.get("ATE_CLOUD_DB_PATH") or ""
    return Path(env) if env else (BASE_DIR / "cloud_demo.db")


def is_offline() -> bool:
    """是否强制模拟云端不可达（演练断网路径）"""
    return str(os.environ.get("ATE_CLOUD_OFFLINE") or "").strip().lower() in ("1", "true", "yes")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 建表


def init_db(path: Path | None = None) -> Path:
    """创建演示云端表（幂等）"""
    target = Path(path or cloud_db_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with sqlite3.connect(str(target), timeout=10) as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
    return target


# ---------------------------------------------------------------- 上报


def _do_upload(payload: dict) -> tuple[bool, str]:
    """真正把数据写进「云端」——现场换成 HTTP 调用即可

    返回 `(是否成功, 说明)`。演示版：`ATE_CLOUD_OFFLINE=1` 时模拟不可达。
    """
    time.sleep(_SIMULATED_LATENCY_S)
    if is_offline():
        return False, f"云端不可达（演示断网开关已开启，endpoint={endpoint()}）"
    return True, "ok"


def upload_record(payload: dict, *, source: str = "auto") -> dict:
    """把一轮测试结果上报「云端数据库」（幂等）

    返回:
        `{ok, cloud_id, uploaded_at, endpoint, message, duplicate}`

    * 同 `task_id` 重复上报 -> 走 UPDATE，`duplicate=True`，`cloud_id` 不变；
    * 云端不可达 -> `ok=False`，**不抛异常**，调用方把该条标记为待重传。
    """
    row = {k: payload.get(k) for k in _PAYLOAD_FIELDS}
    row["task_id"] = str(row.get("task_id") or "").strip()
    if not row["task_id"]:
        return {"ok": False, "cloud_id": None, "uploaded_at": None,
                "endpoint": endpoint(), "message": "缺少 task_id，无法上报", "duplicate": False}
    row["source"] = source
    row["uploaded_at"] = _now()

    target = init_db()
    ok, message = _do_upload(row)
    if not ok:
        return {"ok": False, "cloud_id": None, "uploaded_at": None,
                "endpoint": endpoint(), "message": message, "duplicate": False}

    cols = list(row.keys())
    with _LOCK:
        with sqlite3.connect(str(target), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            existed = conn.execute(
                "SELECT cloud_id FROM cloud_test_records WHERE task_id = ?", (row["task_id"],)
            ).fetchone()
            if existed:
                # 幂等：同 task_id 重复上报只更新，不新增行
                assignments = ", ".join(f"{c} = :{c}" for c in cols if c != "task_id")
                conn.execute(
                    f"UPDATE cloud_test_records SET {assignments} WHERE task_id = :task_id", row
                )
                cloud_id = existed["cloud_id"]
                duplicate = True
            else:
                placeholders = ", ".join(f":{c}" for c in cols)
                cur = conn.execute(
                    f"INSERT INTO cloud_test_records ({', '.join(cols)}) VALUES ({placeholders})", row
                )
                cloud_id = cur.lastrowid
                duplicate = False
            conn.commit()
    return {
        "ok": True,
        "cloud_id": cloud_id,
        "uploaded_at": row["uploaded_at"],
        "endpoint": endpoint(),
        "message": ("云端已有该轮记录，已更新" if duplicate else "上报成功"),
        "duplicate": duplicate,
    }


# ---------------------------------------------------------------- 查询


def list_records(limit: int = 100, work_order: str = "", uut: str = "", result: str = "") -> list[dict]:
    """查询「云端」已入库的测试记录（演示看板用）"""
    where, params = [], []
    if work_order:
        where.append("work_order LIKE ?")
        params.append(f"%{work_order}%")
    if uut:
        where.append("uut = ?")
        params.append(uut)
    if result:
        where.append("result = ?")
        params.append(result.upper())
    sql = "SELECT * FROM cloud_test_records"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY cloud_id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    init_db()
    with _LOCK:
        with sqlite3.connect(str(cloud_db_path()), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_record(task_id: str) -> dict | None:
    """按 task_id 查云端记录"""
    init_db()
    with _LOCK:
        with sqlite3.connect(str(cloud_db_path()), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM cloud_test_records WHERE task_id = ?", (task_id,)
            ).fetchone()
    return dict(row) if row else None


def stats() -> dict:
    """云端连接状态 + 入库统计（界面顶部的云端指示灯用）"""
    init_db()
    with _LOCK:
        with sqlite3.connect(str(cloud_db_path()), timeout=10) as conn:
            conn.row_factory = sqlite3.Row   # dict(row) 需要 Row 工厂
            total = conn.execute("SELECT COUNT(*) AS c FROM cloud_test_records").fetchone()[0]
            by_wo = [
                dict(r)
                for r in conn.execute(
                    """SELECT work_order, COUNT(*) AS n, SUM(result = 'OK') AS ok, SUM(result = 'NOK') AS nok
                       FROM cloud_test_records
                       WHERE work_order IS NOT NULL AND work_order <> ''
                       GROUP BY work_order ORDER BY work_order DESC LIMIT 10"""
                ).fetchall()
            ]
    return {
        "success": True,
        "endpoint": endpoint(),
        "db_path": str(cloud_db_path()),
        "offline_switch": is_offline(),
        "online": not is_offline(),
        "record_count": total,
        "by_work_order": by_wo,
    }
