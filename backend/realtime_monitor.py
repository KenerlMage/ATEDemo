"""实时数据监视 - 后端接口

读取测试用例通过 `ate_rt` 库写入的实时数据（Redis KVS，缺 Redis 时回落本地 SQLite），
供 Runner「测试执行」页的独立监视窗口按 1 Hz（最高 2 Hz）轮询。

接口:
  GET /api/monitor/info                     存储后端（redis / sqlite）与地址
  GET /api/monitor/tasks                    最近任务（测试用例）列表
  GET /api/monitor/snapshot?task=<id>       该任务全部数据项的最新值
  GET /api/monitor/series?task=&channel=    单个数据项的最近 N 个点（折线图）
  GET /api/monitor/store                    存储键空间说明（便于运维排查）

任务元数据由执行链路在开始时写入（begin_task），结束时置状态（end_task）；
若用例自己用 `ate_rt.Monitor(task=...)` 另建任务号，也能在这里被列出。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI

_SDK_DIR = Path(__file__).resolve().parent.parent / "sdk"
if str(_SDK_DIR) not in sys.path:
    sys.path.insert(0, str(_SDK_DIR))

import ate_rt  # noqa: E402  测试用例侧同一个库，保证读写同构
from ate_rt import store as rt_store  # noqa: E402

_STORE: Any = None
_STORE_KEY = ""


def _store() -> Any:
    """惰性打开存储；URL 变化时重开（便于运行中切换 Redis / SQLite）"""
    global _STORE, _STORE_KEY
    import os

    key = os.environ.get("ATE_RT_URL", "") + "|" + os.environ.get("ATE_RT_DB", "")
    if _STORE is not None and key == _STORE_KEY:
        return _STORE
    try:
        if _STORE is not None:
            _STORE.close()
    except Exception:
        pass
    _STORE = rt_store.open_store()
    _STORE_KEY = key
    return _STORE


def begin_task(task_id: str, tps: Optional[dict] = None, task: Optional[dict] = None) -> None:
    """执行开始时登记任务（失败不影响测试执行）"""
    try:
        tps = tps or {}
        task = task or {}
        _store().upsert_task(str(task_id), {
            "name": tps.get("name") or str(task_id),
            "tps_id": tps.get("id", ""),
            "tps_name": tps.get("name", ""),
            "uut": task.get("uut", ""),
            "mode": task.get("mode", ""),
            "status": "running",
            "started_at": f"{time.time():.3f}",
        })
    except Exception:
        pass


def end_task(task_id: str, status: str = "done") -> None:
    try:
        _store().upsert_task(str(task_id), {"status": status, "ended_at": f"{time.time():.3f}"})
    except Exception:
        pass


def _channel_view(rows: list[dict], now: Optional[float] = None) -> list[dict]:
    now = now if now is not None else time.time()
    out = []
    for row in rows:
        ts_ms = int(row.get("ts_ms") or 0)
        age = round(now - ts_ms / 1000.0, 1) if ts_ms else None
        value = row.get("value")
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        out.append({
            "name": row.get("name", ""),
            "label": row.get("label") or row.get("name", ""),
            "unit": row.get("unit", "") or "",
            "group": row.get("group", "") or "",
            "kind": row.get("kind", "") or ("float" if numeric else "text"),
            "lo": row.get("lo"),
            "hi": row.get("hi"),
            "value": value,
            "ts_ms": ts_ms,
            "age_s": age,
            "stale": bool(age is None or age > 5),
            "numeric": numeric,
        })
    return out


def register_routes(app: FastAPI) -> None:

    @app.get("/api/monitor/info")
    def monitor_info():
        info = rt_store.describe(_store())
        info["sdk_version"] = getattr(ate_rt, "__version__", "")
        return {"success": True, **info}

    @app.get("/api/monitor/tasks")
    def monitor_tasks(limit: int = 30, active_only: bool = False):
        try:
            tasks = _store().list_tasks(limit=limit, active_only=active_only)
        except Exception as exc:
            return {"success": False, "message": f"读取任务失败：{exc}", "tasks": []}
        items = []
        for task in tasks:
            tid = task.get("task_id") or task.get("task_id", "")
            started = task.get("started_at")
            items.append({
                "task_id": tid,
                "name": task.get("name") or tid,
                "tps_id": task.get("tps_id", ""),
                "tps_name": task.get("tps_name", ""),
                "uut": task.get("uut", ""),
                "mode": task.get("mode", ""),
                "status": task.get("status", ""),
                "started_at": float(started) if started not in (None, "") else None,
                "ended_at": float(task["ended_at"]) if task.get("ended_at") not in (None, "") else None,
                "updated_at": float(task["updated_at"]) if task.get("updated_at") not in (None, "") else None,
            })
        return {"success": True, "count": len(items), "tasks": items}

    @app.get("/api/monitor/snapshot")
    def monitor_snapshot(task: str = ""):
        if not task:
            return {"success": False, "message": "缺少 task 参数"}
        try:
            store = _store()
            meta = store.get_task(task) or {}
            rows = store.snapshot(task)
        except Exception as exc:
            return {"success": False, "message": f"读取实时数据失败：{exc}", "channels": []}
        channels = _channel_view(rows)
        return {
            "success": True,
            "task": {
                "task_id": task,
                "name": meta.get("name") or task,
                "tps_name": meta.get("tps_name", ""),
                "uut": meta.get("uut", ""),
                "mode": meta.get("mode", ""),
                "status": meta.get("status", ""),
            },
            "backend": rt_store.describe(store).get("backend", ""),
            "count": len(channels),
            "numeric_count": len([c for c in channels if c["numeric"]]),
            "ts_ms": int(time.time() * 1000),
            "channels": channels,
        }

    @app.get("/api/monitor/series")
    def monitor_series(task: str = "", channel: str = "", limit: int = 600):
        if not task or not channel:
            return {"success": False, "message": "缺少 task 或 channel 参数", "points": []}
        limit = max(10, min(int(limit) if limit else 600, 3600))
        try:
            points = _store().series(task, channel, limit=limit)
        except Exception as exc:
            return {"success": False, "message": f"读取曲线失败：{exc}", "points": []}
        return {
            "success": True,
            "task": task,
            "channel": channel,
            "count": len(points),
            "points": points,
        }

    @app.get("/api/monitor/store")
    def monitor_store():
        """键空间说明（运维视图）"""
        return {
            "success": True,
            "prefix": rt_store.KEY_PREFIX,
            "keys": {
                "task": f"{rt_store.KEY_PREFIX}:task:<task_id>            哈希：name/tps_id/uut/status/started_at/updated_at",
                "tasks": f"{rt_store.KEY_PREFIX}:tasks                   有序集合：task_id -> 开始时间戳",
                "channels": f"{rt_store.KEY_PREFIX}:task:<id>:channels    有序集合：数据项名 -> 声明顺序",
                "channel": f"{rt_store.KEY_PREFIX}:task:<id>:ch:<name>    哈希：label/unit/group/kind/value/ts",
                "series": f"{rt_store.KEY_PREFIX}:task:<id>:ch:<name>:z  有序集合：时间戳 -> 数据点（保留最近 N 点）",
            },
            "retention_points": rt_store.DEFAULT_KEEP,
            "fallback": "Redis 不可用时自动使用 SQLite（backend/.ate_rt/realtime.db）",
        }
