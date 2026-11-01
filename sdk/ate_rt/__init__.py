"""ate_rt —— ATE Runner 实时数据发布库（测试用例侧）

用途：测试用例在运行过程中把测量值、状态量、判据量写入实时数据通道，
      Runner「测试执行」页的监视窗口即可按 1 Hz（最高 2 Hz）刷新查看，并对数值做折线图。

最小用法（三行）:

    import ate_rt
    ate_rt.declare("电源电压", unit="V", group="电源")
    ate_rt.write("电源电压", 12.03)

带层级与生命周期:

    with ate_rt.Monitor(name="光功率扫描", tps="spm_rh_dyn") as m:
        m.declare("光功率", unit="dBm", group="光学", lo=-30, hi=10)
        for i in range(60):
            m.write("光功率", -3.2 + i * 0.01)
            time.sleep(1)

约定:
  * 任务号默认取环境变量 ATE_RT_TASK_ID（Runner 启动用例时注入），也可显式传 task=...；
  * 存储位置由 ATE_RT_URL 决定，形如 redis://[[user:]pass@]host:port/db；
    **仅支持 Redis**，连不上时本库降级为空操作，不抛错、不中断测试；
  * 本库任何存储异常都不会向上抛给用例，只在 stderr 打一行提示，保证测试稳定性优先。
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .store import (  # noqa: F401  （对外暴露）
    DEFAULT_KEEP,
    KEY_PREFIX,
    RedisStore,
    RtUnavailable,
    describe,
    open_store,
    probe,
)

__version__ = "2.0.0"          # 2.0：取消 SQLite 后端，仅保留 Redis

_WARNED = {"logged": False}
_EVENT_CHANNEL = "_event"


def _task_from_env() -> str:
    tid = os.environ.get("ATE_RT_TASK_ID", "").strip()
    if tid:
        return tid
    env_file = os.environ.get("ATE_ENV_FILE", "").strip()
    if env_file and Path(env_file).is_file():
        try:
            data = json.loads(Path(env_file).read_text(encoding="utf-8"))
            return str(data.get("task_id") or data.get("run_id") or "").strip()
        except Exception:
            return ""
    return ""


def _warn(message: str) -> None:
    if _WARNED["logged"]:
        return
    _WARNED["logged"] = True
    try:
        sys.stderr.write(f"[ate_rt] {message}（后续同类提示不再重复）\n")
    except Exception:
        pass


class Monitor:
    """一次执行的数据会话：一个任务号 + 若干数据项（channel）+ 数据点"""

    def __init__(
        self,
        task: Optional[str] = None,
        *,
        name: str = "",
        tps: str = "",
        uut: str = "",
        mode: str = "",
        url: Optional[str] = None,
        keep: int = DEFAULT_KEEP,
        enabled: bool = True,
    ) -> None:
        self.enabled = bool(enabled) and os.environ.get("ATE_RT_DISABLE", "") not in ("1", "true")
        self.task_id = (task or _task_from_env() or f"rt-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}")
        self.name = name
        self.keep = int(keep)
        self.last_error = ""
        self._store = None
        self._declared: set[str] = set()
        if not self.enabled:
            return
        try:
            self._store = open_store(url)
            # 只写非空字段：Runner 已经登记过任务时，不用空值把它抹掉
            fields = {
                "name": name or self.task_id,
                "status": "running",
                "host": os.environ.get("COMPUTERNAME", ""),
                "pid": os.getpid(),
            }
            for key, val in (("tps_id", tps), ("tps_name", tps), ("uut", uut), ("mode", mode)):
                if val:
                    fields[key] = val
            if not fields.get("started_at"):
                fields["started_at"] = f"{time.time():.3f}"
            self._store.upsert_task(self.task_id, fields)
        except Exception as exc:                      # 存储不可用不影响测试
            self._store = None
            self.last_error = str(exc)
            _warn(f"实时数据存储不可用：{exc}")

    # ---------------- 生命周期 ----------------

    def __enter__(self) -> "Monitor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close(status="error" if exc_type else "done")

    def close(self, status: str = "done") -> None:
        if not self._store:
            return
        try:
            self._store.upsert_task(self.task_id, {
                "status": status,
                "ended_at": f"{time.time():.3f}",
            })
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            try:
                self._store.close()
            except Exception:
                pass
            self._store = None

    # ---------------- 数据项 ----------------

    def declare(self, name: str, **meta: Any) -> "Monitor":
        """声明数据项（可重复调用，后一次覆盖描述字段）

        可用字段：label、unit、group、kind(float/text)、lo、hi，其余写入忽略。
        """
        channel = str(name)
        if not channel or not self._store:
            self._declared.add(channel)
            return self
        payload = {
            "label": meta.get("label") or channel,
            "unit": meta.get("unit", ""),
            "group": meta.get("group", ""),
            "kind": meta.get("kind") or ("float" if meta.get("lo") is not None or meta.get("hi") is not None else ""),
            "lo": meta.get("lo"),
            "hi": meta.get("hi"),
        }
        try:
            self._store.declare_channel(self.task_id, channel, payload)
            self._declared.add(channel)
        except Exception as exc:
            self.last_error = str(exc)
            _warn(f"声明数据项失败：{exc}")
        return self

    def write(self, name: str, value: Any, ts_ms: Optional[int] = None) -> "Monitor":
        """写入一个数据点（同名字数据项自动声明）"""
        channel = str(name)
        if not self._store:
            return self
        try:
            if channel not in self._declared:
                self._store.declare_channel(self.task_id, channel, {"label": channel})
                self._declared.add(channel)
            self._store.write(self.task_id, channel, value, ts_ms=ts_ms, keep=self.keep)
        except Exception as exc:
            self.last_error = str(exc)
            _warn(f"写入数据点失败：{exc}")
        return self

    def write_many(self, values: dict, ts_ms: Optional[int] = None) -> "Monitor":
        for key, val in (values or {}).items():
            self.write(key, val, ts_ms=ts_ms)
        return self

    def event(self, text: str) -> "Monitor":
        """写一条事件标记（在监视窗口里作为文本项出现）"""
        if not self._store:
            return self
        try:
            self.declare(_EVENT_CHANNEL, label="事件", group="事件", kind="text")
            self._store.write(self.task_id, _EVENT_CHANNEL,
                              f"{time.strftime('%H:%M:%S')} {text}", keep=200)
        except Exception as exc:
            self.last_error = str(exc)
        return self

    # ---------------- 便捷 ----------------

    @property
    def store_backend(self) -> str:
        if not self._store:
            return "none"
        return getattr(self._store, "backend", "?")

    @property
    def degraded(self) -> bool:
        """保留字段：改为 Redis 专用后不再有降级后端，恒为 False"""
        return False

    def info(self) -> dict:
        return {
            "task_id": self.task_id,
            "enabled": self.enabled,
            "backend": self.store_backend,
            "degraded": self.degraded,
            "keep": self.keep,
        }


# ==================== 模块级默认会话 ====================

_DEFAULT: Optional[Monitor] = None


def session() -> Monitor:
    """取默认会话（首次调用自动创建；任务号取 ATE_RT_TASK_ID）"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Monitor()
    return _DEFAULT


def declare(name: str, **meta: Any) -> Monitor:
    return session().declare(name, **meta)


def write(name: str, value: Any, ts_ms: Optional[int] = None) -> Monitor:
    return session().write(name, value, ts_ms=ts_ms)


def write_many(values: dict, ts_ms: Optional[int] = None) -> Monitor:
    return session().write_many(values, ts_ms=ts_ms)


def event(text: str) -> Monitor:
    return session().event(text)


def close(status: str = "done") -> None:
    global _DEFAULT
    if _DEFAULT is not None:
        _DEFAULT.close(status=status)
        _DEFAULT = None


def reset() -> None:
    """丢弃默认会话（测试或换任务时使用）"""
    global _DEFAULT
    _DEFAULT = None


def store_info() -> dict:
    """当前存储后端信息（Redis 专用；不可用时返回 ok=False）"""
    try:
        store = open_store()
        info = describe(store)
        try:
            store.close()
        except Exception:
            pass
        return info
    except Exception as exc:
        return {"backend": "none", "ok": False, "error": str(exc)}


__all__ = [
    "Monitor",
    "session",
    "declare",
    "write",
    "write_many",
    "event",
    "close",
    "reset",
    "store_info",
    "RedisStore",
    "open_store",
    "describe",
    "probe",
    "RtUnavailable",
    "KEY_PREFIX",
    "DEFAULT_KEEP",
    "__version__",
]
