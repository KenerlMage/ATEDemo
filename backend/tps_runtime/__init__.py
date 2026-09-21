# -*- coding: utf-8 -*-
"""TPS 运行环境模块：workspace 管理 + 公共 conftest + 运行环境接口

对外接口
--------
* `register_routes(app, tps_dir)` —— 挂载 HTTP 路由（`main.py` 末尾调用）
* `load_tps_files` / `read_tps` / `find_tps` —— 供 `main.py` 读取 v2 目录式 TPS
* `stage` / `run_step` —— 运行环境准备与单步执行（`main.py` 的 TPS 任务线程调用）
* `ensure_workspace` / `workspace_overview` —— workspace 与公共 conftest 部署
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import manifest as mf
from . import workspace as ws
from .manifest import (  # noqa: F401
    TPS_V2_SCHEMA,
    case_entries,
    is_v2,
    normalize_tps,
    steps_of,
    summary_tps,
    validate_v2,
)
from .workspace import (  # noqa: F401
    cleanup_runs,
    deploy_conftest,
    ensure_workspace,
    find_tps,
    import_v2,
    list_runs,
    load_tps_files,
    read_run_result,
    read_tps,
    run_step,
    stage,
    tps_dir_name,
    tps_root,
    workspace_overview,
    workspace_root,
)

__all__ = [
    "register_routes",
    "load_tps_files",
    "read_tps",
    "find_tps",
    "stage",
    "run_step",
    "ensure_workspace",
    "workspace_overview",
    "deploy_conftest",
    "list_runs",
    "cleanup_runs",
    "import_v2",
    "read_run_result",
    "validate_v2",
    "normalize_tps",
    "summary_tps",
    "is_v2",
    "TPS_V2_SCHEMA",
]


def collect_cases(tps: dict, source_dir: Path, *, mode: str = "simulate", bench_id: str = "") -> dict:
    """只做收集（pytest --collect-only），校验执行入口能否被 pytest 正确发现"""
    import shutil as _shutil
    import time as _time

    task_id = f"collect{_time.strftime('%H%M%S')}"
    try:
        info = stage(tps, source_dir, task_id=task_id, uut="COLLECT00001", bench_id=bench_id,
                     mode=mode, keep_runs=99)
    except Exception as e:
        return {"success": False, "message": str(e)}
    run_dir = Path(info["run_dir"])
    cmd = [sys.executable, "-m", "pytest", "test_tps_generated.py",
           "--collect-only", "-q", "--no-header", "-p", "no:cacheprovider"]
    try:
        proc = subprocess.run(cmd, capture_output=True, cwd=str(run_dir), timeout=120)
        out = ws._decode(proc.stdout or b"") + ws._decode(proc.stderr or b"")
        nodes = [ln.strip() for ln in out.splitlines() if "::" in ln]
        # 收集用的临时目录不留存（不影响已有运行记录）
        _shutil.rmtree(run_dir, ignore_errors=True)
        return {
            "success": proc.returncode == 0,
            "run_dir": str(run_dir),
            "count": len(nodes),
            "nodes": nodes,
            "output": out.strip()[-1200:],
        }
    except Exception as e:
        return {"success": False, "message": f"收集失败: {e}"}


def register_routes(app, tps_dir) -> None:
    """挂载 TPS 运行环境相关路由（FastAPI）"""
    from fastapi import Body

    tps_dir = Path(tps_dir)

    # 启动即确保 workspace 与公共 conftest 就绪（不存在则由程序自动创建）
    try:
        ensure_workspace()
    except Exception:
        pass

    def _tps(tps_id: str):
        return find_tps(tps_dir, tps_id)

    # ---------------- workspace / 运行时 ----------------

    @app.get("/api/runtime/workspace")
    def runtime_workspace():
        """workspace 总览：路径、公共 conftest、各 TPS 运行目录"""
        return workspace_overview()

    @app.post("/api/runtime/workspace/init")
    def runtime_workspace_init(payload: dict | None = Body(default=None)):
        """创建 workspace（若不存在）并部署公共 conftest"""
        force = bool((payload or {}).get("force"))
        ws.ensure_workspace()
        conf = ws.deploy_conftest(force=force)
        return {"success": True, "message": f"workspace 就绪（conftest {conf['action']}）",
                "deploy": conf, "workspace": conf["workspace"], "conftest": conf["path"],
                "conftest_exists": Path(conf["path"]).is_file(), "conftest_version": conf["version"],
                "template_version": conf["version"]}

    @app.get("/api/runtime/drivers")
    def runtime_drivers():
        """驱动规格清单（型号 -> 驱动实现 -> 支持动作）"""
        import drivers

        specs = drivers.list_specs()
        return {"success": True, "count": len(specs), "specs": specs,
                "families": sorted({s["family"] for s in specs})}

    @app.get("/api/runtime/db")
    def runtime_db():
        """SQLite 侧车状态：测试台表 / 设备 driver 配置表 / 最近同步记录"""
        import ate_db

        return ate_db.stats()

    @app.post("/api/runtime/db/sync")
    def runtime_db_sync():
        """把注册库（testbenches.json）重新投影到 SQLite"""
        import ate_db

        out = ate_db.sync_from_registry()
        return {**out, "stats": ate_db.stats()}

    @app.get("/api/runtime/schema")
    def runtime_schema():
        """TPS v2 清单结构说明（供前端 / 文档展示）"""
        return {
            "success": True,
            "schema": TPS_V2_SCHEMA,
            "fields": {
                "testconfig": "测试阈值子字典：{阈值键: {label, min, max, unit}}",
                "device_config": "设备信息子字典：{别名: {device_id 或 role, mode}}",
                "cmd_suit": "测试套排列：有序用例列表，case 指向 testcase/ 里的实现",
                "setup": "可选：环境初始化用例列表",
                "teardown": "可选：环境终止用例列表",
            },
            "workspace": str(ws.workspace_root(False)),
            "conftest": str(ws.workspace_root(False) / "conftest.py"),
        }

    # ---------------- TPS v2 ----------------

    @app.get("/api/tps/{tps_id}/manifest")
    def tps_manifest(tps_id: str):
        """TPS 清单（v2 归一化后）"""
        tps, path = _tps(tps_id)
        if not tps:
            return {"success": False, "message": f"TPS 不存在: {tps_id}"}
        return {"success": True, "tps": tps, "path": str(path), "schema": tps.get("schema", "")}

    @app.post("/api/tps/{tps_id}/stage")
    def tps_stage(tps_id: str, payload: dict | None = Body(default=None)):
        """准备运行环境（不执行）：复制临时副本、生成执行入口、写运行环境描述"""
        payload = payload or {}
        tps, path = _tps(tps_id)
        if not tps:
            return {"success": False, "message": f"TPS 不存在: {tps_id}"}
        if not mf.is_v2(tps):
            return {"success": False, "message": f"该 TPS 是 v1 单文件格式，无需 stage: {tps_id}"}
        import uuid

        task_id = str(payload.get("task_id") or uuid.uuid4().hex[:12])
        try:
            info = stage(
                tps, Path(path).parent,
                task_id=task_id,
                uut=str(payload.get("uut") or ""),
                bench_id=str(payload.get("bench_id") or ""),
                mode=str(payload.get("mode") or "simulate"),
                keep_runs=int(payload.get("keep_runs") or ws.DEFAULT_KEEP_RUNS),
            )
        except Exception as e:
            return {"success": False, "message": str(e)}
        return {"success": True, "message": f"运行环境已就绪: {info['run_dir']}", **info}

    @app.post("/api/tps/{tps_id}/collect")
    def tps_collect(tps_id: str, payload: dict | None = Body(default=None)):
        """收集用例（pytest --collect-only），校验执行入口可用"""
        payload = payload or {}
        tps, path = _tps(tps_id)
        if not tps:
            return {"success": False, "message": f"TPS 不存在: {tps_id}"}
        return collect_cases(tps, Path(path).parent, mode=str(payload.get("mode") or "simulate"),
                             bench_id=str(payload.get("bench_id") or ""))

    @app.get("/api/tps/{tps_id}/runs")
    def tps_runs(tps_id: str):
        """该 TPS 的历史运行目录（含最近一次用例结果摘要）"""
        tps, _ = _tps(tps_id)
        if not tps:
            return {"success": False, "message": f"TPS 不存在: {tps_id}"}
        runs = list_runs(tps)
        result = ws.read_run_result(Path(runs[0]["path"])) if runs else None
        return {
            "success": True,
            "tps_id": tps_id,
            "tps_dir": str(tps_root(tps, create=False)),
            "count": len(runs),
            "runs": runs,
            "last_result": result,
        }

    @app.post("/api/tps/{tps_id}/runs/cleanup")
    def tps_runs_cleanup(tps_id: str, payload: dict | None = Body(default=None)):
        """清理旧运行目录（保留最近 keep 个）"""
        tps, _ = _tps(tps_id)
        if not tps:
            return {"success": False, "message": f"TPS 不存在: {tps_id}"}
        keep = int((payload or {}).get("keep") or ws.DEFAULT_KEEP_RUNS)
        removed = ws.cleanup_runs(tps, keep=keep)
        return {"success": True, "removed": removed, "count": len(removed), "keep": keep,
                "message": f"已清理 {len(removed)} 个旧运行目录" if removed else "没有需要清理的运行目录"}

    @app.post("/api/tps/validate")
    def tps_validate(payload: dict | None = Body(default=None)):
        """校验 TPS v2 清单（导入前预检），返回错误列表或归一化摘要"""
        content = (payload or {}).get("content") or ""
        if not content:
            raw = (payload or {}).get("manifest")
            if not isinstance(raw, dict):
                return {"success": False, "message": "请提供 content(JSON 文本) 或 manifest(对象)"}
        else:
            try:
                raw = json.loads(content)
            except Exception as e:
                return {"success": False, "message": f"JSON 解析失败: {e}"}
        errors = validate_v2(raw)
        if errors:
            return {"success": False, "message": f"校验未通过（{len(errors)} 项）", "errors": errors}
        tps = normalize_tps(raw)
        return {"success": True, "message": "校验通过", "tps": summary_tps(tps),
                "steps": [s["name"] for s in tps["steps"]]}
