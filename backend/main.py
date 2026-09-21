"""ATE 自动测试装备 - 后端服务 (FastAPI + pytest)

接口:
  POST /api/login       登录: 校验用户名并写入登录日志 (logs/login.log)
  GET  /api/testcases   获取可执行测试用例列表 (pytest --collect-only 收集)
  POST /api/execute     启动指定用例执行 (后台线程 + pytest 子进程)
  GET  /api/tasks/{id}  查询执行进度 / 状态 / 实时输出
  GET  /api/tasks       最近执行记录
  GET  /api/health      健康检查

运行:
  python -m uvicorn main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import license_utils

import ate_db              # 注册库 -> SQLite 投影（设备 driver 配置）
import tps_runtime        # TPS 运行环境（workspace / 公共 conftest）

BASE_DIR = Path(__file__).resolve().parent
TEST_CASE_DIR = BASE_DIR / "test_cases"
LOG_DIR = BASE_DIR / "logs"
TEST_LOG_DIR = LOG_DIR / "test_logs"   # 每次 TPS 运行的测试日志
TPS_DIR = BASE_DIR / "testresource"
REPORT_DIR = LOG_DIR / "reports"
DB_PATH = BASE_DIR / "ate.db"
LOG_DIR.mkdir(parents=True, exist_ok=True)
TEST_LOG_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ==================== 前端静态资源 (打包部署: 与后端同端口托管) ====================
# 打包发布时前端已构建为静态文件, 放在安装目录 web/ 下, 由后端同端口托管, 现场无需 Node。
# 查找顺序: 环境变量 ATE_WEB_DIR -> <安装目录>/web -> <后端目录>/web -> 仓库内 frontend/dist
from fastapi.responses import FileResponse  # noqa: E402

WEB_DIR = Path()
_env_web = os.environ.get("ATE_WEB_DIR") or ""
if _env_web and Path(_env_web).is_dir():
    WEB_DIR = Path(_env_web).resolve()
else:
    for _cand in (BASE_DIR.parent / "web", BASE_DIR / "web", BASE_DIR.parent / "frontend" / "dist"):
        if _cand.is_dir():
            WEB_DIR = _cand.resolve()
            break
WEB_ENABLED = bool(str(WEB_DIR)) and WEB_DIR.is_dir() and (WEB_DIR / "index.html").is_file()

# 远端 TPS 源 (环境变量 ATE_REMOTE_TPS_URL 可覆盖; 在线时优先拉取, 离线回退本地)
REMOTE_TPS_URL = os.environ.get("ATE_REMOTE_TPS_URL", "http://127.0.0.1:8999").rstrip("/")
REMOTE_TIMEOUT = 2.0  # 秒

app = FastAPI(title="ATE 自动测试装备", description="FastAPI + pytest 自动测试执行服务", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==================== License 授权 ====================


class LicenseImportRequest(BaseModel):
    content: str  # License 文件文本内容


@app.get("/api/license/status")
def license_status():
    """查询当前 License 状态: valid / not_found / invalid"""
    return license_utils.license_status()


@app.post("/api/license/import")
def license_import(req: LicenseImportRequest):
    """导入 License: 校验签名/有效期/设备绑定, 通过后写入本地生效"""
    ok, msg = license_utils.import_license(req.content)
    return {"success": ok, "message": msg}


# ==================== 登录接口 ====================


class LoginRequest(BaseModel):
    username: str


@app.post("/api/login")
def login(req: LoginRequest):
    # License 前置校验: 未授权则拒绝登录
    lic_status = license_utils.license_status()
    if not lic_status["valid"]:
        return {
            "success": False,
            "license_required": True,
            "message": f"未授权: {lic_status['message']}",
            "license": lic_status,
        }
    username = req.username.strip()
    if not username:
        return {"success": False, "message": "用户名不能为空"}
    # 登录记录写入日志
    with open(LOG_DIR / "login.log", "a", encoding="utf-8") as f:
        f.write(f"[{now_str()}] 用户登录成功: {username}\n")
    return {"success": True, "message": "登录成功", "username": username, "time": now_str()}


# ==================== 用例收集 ====================


def _iter_case_files():
    """遍历测试文件: 兼容 pytest 的 test_*.py 与 *_test.py 两种命名约定"""
    seen: set[Path] = set()
    for pattern in ("test_*.py", "*_test.py"):
        for py in sorted(TEST_CASE_DIR.glob(pattern)):
            if py not in seen:
                seen.add(py)
                yield py


def _iter_test_functions(body):
    """递归遍历模块/类体, 产出所有 test_ 开头的函数"""
    for node in body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            yield node
        elif isinstance(node, ast.ClassDef):
            yield from _iter_test_functions(node.body)


def _parse_docstrings() -> dict[str, str]:
    """解析测试文件函数 docstring, 作为用例描述"""
    desc: dict[str, str] = {}
    for py in _iter_case_files():
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in _iter_test_functions(tree.body):
                doc = ast.get_docstring(node)
                if doc:
                    desc[node.name] = doc.strip().splitlines()[0]
        except Exception:
            continue
    return desc


def _collect_with_pytest() -> list[str]:
    """pytest --collect-only 收集用例节点 ID"""
    cmd = [sys.executable, "-m", "pytest", "test_cases", "--collect-only", "-q", "--no-header"]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=BASE_DIR, timeout=120)
    output = (proc.stdout or "") + (proc.stderr or "")
    nodes: list[str] = []
    for line in output.splitlines():
        line = line.strip().replace("\\", "/")
        if ".py::" not in line:
            continue
        first = line.split()[0] if line.split() else ""
        if first in ("ERROR", "FAILED", "WARNING", "INTERNALERROR", "!!!"):
            continue
        nodes.append(line)
    return nodes


def collect_testcases() -> list[dict]:
    """可执行用例列表; pytest 不可用时降级为 ast 静态扫描"""
    docstrings = _parse_docstrings()
    try:
        node_ids = _collect_with_pytest()
    except Exception:
        node_ids = []
    if not node_ids:
        for py in _iter_case_files():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
                for node in tree.body:
                    if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                        node_ids.append(f"test_cases/{py.name}::{node.name}")
            except Exception:
                continue
    cases: list[dict] = []
    seen: set[str] = set()
    for node_id in node_ids:
        m = re.match(r"^(.*\.py)::(.*)$", node_id)
        if not m:
            continue
        file_part, name_part = m.group(1), m.group(2)
        name = name_part.split("[")[0].split("::")[-1]
        if node_id in seen:
            continue
        seen.add(node_id)
        cases.append(
            {
                "id": node_id,
                "name": name,
                "file": file_part,
                "description": docstrings.get(name, name.replace("_", " ")),
            }
        )
    return cases


@app.get("/api/testcases")
def list_testcases():
    try:
        cases = collect_testcases()
        return {"success": True, "count": len(cases), "testcases": cases}
    except Exception as e:
        return {"success": False, "message": f"收集用例失败: {e}", "testcases": []}


# ==================== 用例执行 ====================

TASKS: dict[str, dict] = {}
TASKS_LOCK = threading.Lock()


class ExecuteRequest(BaseModel):
    testcase_id: str


def _count_total(node_id: str) -> int:
    """统计该用例展开后的测试条数 (参数化用例会展开为多条)"""
    cmd = [sys.executable, "-m", "pytest", node_id, "--collect-only", "-q", "--no-header"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=BASE_DIR, timeout=120)
        out = (proc.stdout or "") + (proc.stderr or "")
        n = 0
        for line in out.splitlines():
            line = line.strip().replace("\\", "/")
            if ".py::" not in line:
                continue
            first = line.split()[0] if line.split() else ""
            if first in ("ERROR", "FAILED", "WARNING", "INTERNALERROR", "!!!"):
                continue
            n += 1
        return n or 1
    except Exception:
        return 1


def _run_task(task_id: str) -> None:
    with TASKS_LOCK:
        task = TASKS[task_id]
        task["status"] = "running"
        task["start_time"] = now_str()
        node_id = task["testcase_id"]
        task["total"] = _count_total(node_id)

    cmd = [
        sys.executable, "-m", "pytest", node_id,
        "-v", "--tb=short", "--no-header", "-p", "no:cacheprovider",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=1, cwd=BASE_DIR,
    )

    assert proc.stdout is not None
    for raw in proc.stdout:
        line = _decode_output(raw).rstrip("\n")
        with TASKS_LOCK:
            task = TASKS[task_id]
            if len(task["output"]) < 500:
                task["output"].append(line)
            if ".py::" in line:
                if re.search(r"\bPASSED\b", line):
                    task["passed"] += 1
                elif re.search(r"\b(FAILED|ERROR)\b", line):
                    task["failed"] += 1
                elif re.search(r"\bSKIPPED\b", line):
                    task["skipped"] += 1
            done = task["passed"] + task["failed"] + task["skipped"]
            total = task["total"] or 1
            task["progress"] = min(100, int(done / total * 100))

    code = proc.wait()
    with TASKS_LOCK:
        task = TASKS[task_id]
        task["end_time"] = now_str()
        task["progress"] = 100
        task["status"] = "completed" if code == 0 else "failed"

    # 执行记录落盘
    with open(LOG_DIR / "execution.log", "a", encoding="utf-8") as f:
        with TASKS_LOCK:
            snap = dict(TASKS[task_id])
        f.write(
            f"[{snap['start_time']}] 执行 {snap['testcase_id']} -> {snap['status']} "
            f"(passed={snap['passed']}, failed={snap['failed']}, skipped={snap['skipped']})\n"
        )


@app.post("/api/execute")
def execute(req: ExecuteRequest):
    task_id = uuid.uuid4().hex[:12]
    with TASKS_LOCK:
        TASKS[task_id] = {
            "id": task_id,
            "testcase_id": req.testcase_id,
            "status": "pending",
            "progress": 0,
            "output": [],
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "total": 0,
            "start_time": None,
            "end_time": None,
        }
    threading.Thread(target=_run_task, args=(task_id,), daemon=True).start()
    return {"success": True, "task_id": task_id, "message": f"已启动执行: {req.testcase_id}"}


@app.get("/api/tasks/{task_id}")
def task_status(task_id: str):
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return {"success": False, "message": "任务不存在"}
        return {"success": True, **task}


@app.get("/api/tasks")
def list_tasks():
    with TASKS_LOCK:
        items = [dict(t) for t in TASKS.values()]
    items.sort(key=lambda x: x.get("start_time") or "", reverse=True)
    return {"success": True, "tasks": items[:50]}


@app.get("/api/health")
def health():
    return {"success": True, "service": "ATE-Backend", "status": "running", "time": now_str(),
            "web_enabled": WEB_ENABLED, "web_dir": str(WEB_DIR) if WEB_ENABLED else "",
            "base_dir": str(BASE_DIR), "python": sys.executable}


@app.get("/")
def root():
    """根路径: 打包部署时返回前端首页, 开发/无前端产物时返回服务信息。"""
    if WEB_ENABLED:
        return FileResponse(str(WEB_DIR / "index.html"))
    return {"service": "ATE 自动测试装备", "docs": "/docs", "health": "/api/health",
            "web_enabled": WEB_ENABLED}


# ==================== 装备树 ====================


def _build_equipment_tree() -> list[dict]:
    """从所有 TPS 聚合出 项目 -> 测试对象(DUT) -> TPS 三级装备树"""
    projects: dict[str, dict] = {}
    for path in _load_tps_files():
        tps = _read_tps(path)
        if not tps:
            continue
        project_name = tps.get("project") or "未分组项目"
        dut_name = tps.get("dut") or "未分组 DUT"
        proj = projects.setdefault(project_name, {"name": project_name, "duts": {}})
        dut = proj["duts"].setdefault(dut_name, {"name": dut_name, "tps": []})
        dut["tps"].append(_tps_summary(tps))
    result = []
    for p in projects.values():
        result.append({"name": p["name"], "duts": [d for d in p["duts"].values()]})
    return result


@app.get("/api/tree")
def equipment_tree():
    try:
        tree = _build_equipment_tree()
        return {"success": True, "tree": tree}
    except Exception as e:
        return {"success": False, "message": f"构建装备树失败: {e}", "tree": []}


# ==================== SQLite 记录库 ====================

DB_LOCK = threading.Lock()

# 规范测试报告字段: 操作员工号预置, 批次从 UUT 属性获取 (SN -> 远端, 当前本地预置)
OPERATOR_ID = "f010392"
UUT_PROFILES_PATH = TPS_DIR / "uut_profiles.json"

# 旧库兼容: 记录表新增规范字段 (批次/PN/工号/设备编号/结果 OK-NOK)
_RECORD_EXTRA_COLUMNS = {
    "batch": "TEXT DEFAULT ''",
    "part_no": "TEXT DEFAULT ''",
    "operator": "TEXT DEFAULT ''",
    "equipment_serial": "TEXT DEFAULT ''",
    "result": "TEXT DEFAULT ''",
}


def _init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS test_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                uut TEXT NOT NULL,
                project TEXT DEFAULT '',
                dut TEXT DEFAULT '',
                tps_id TEXT DEFAULT '',
                tps_name TEXT DEFAULT '',
                status TEXT DEFAULT '',
                passed INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                skipped INTEGER DEFAULT 0,
                total INTEGER DEFAULT 0,
                duration REAL DEFAULT 0,
                report_file TEXT DEFAULT '',
                start_time TEXT DEFAULT '',
                end_time TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                batch TEXT DEFAULT '',
                part_no TEXT DEFAULT '',
                operator TEXT DEFAULT '',
                equipment_serial TEXT DEFAULT '',
                result TEXT DEFAULT ''
            )"""
        )
        # 旧库迁移: 已存在的表缺少规范字段时补齐
        cols = {r[1] for r in conn.execute("PRAGMA table_info(test_records)").fetchall()}
        for col, decl in _RECORD_EXTRA_COLUMNS.items():
            if col not in cols:
                conn.execute(f"ALTER TABLE test_records ADD COLUMN {col} {decl}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_uut ON test_records(uut)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_task ON test_records(task_id)")
        conn.commit()


_init_db()


UUT_RE = re.compile(r"^[A-Za-z0-9]{12}$")


def _save_record(record: dict):
    with DB_LOCK:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """INSERT INTO test_records
                (task_id, uut, project, dut, tps_id, tps_name, status, passed, failed,
                 skipped, total, duration, report_file, start_time, end_time, created_at,
                 batch, part_no, operator, equipment_serial, result)
                VALUES (:task_id, :uut, :project, :dut, :tps_id, :tps_name, :status, :passed,
                        :failed, :skipped, :total, :duration, :report_file, :start_time, :end_time, :created_at,
                        :batch, :part_no, :operator, :equipment_serial, :result)""",
                record,
            )
            conn.commit()


def _fetch_records(uut: str | None = None, limit: int = 100, date: str | None = None, project: str | None = None, batch: str | None = None) -> list[dict]:
    """查询测试记录; 支持按 UUT / 日期(YYYY-MM-DD) / 项目(含 TPS 名, 模糊) / 批次(模糊) 过滤"""
    where, params = [], []
    if uut:
        where.append("uut = ?")
        params.append(uut)
    if date:
        # 日期按 start_time 前缀匹配 (YYYY-MM-DD)
        where.append("start_time LIKE ?")
        params.append(f"{date}%")
    if project:
        # 项目同时匹配 project 列与 TPS 名称 (模糊)
        where.append("(project LIKE ? OR tps_name LIKE ?)")
        params.extend([f"%{project}%", f"%{project}%"])
    if batch:
        where.append("batch LIKE ?")
        params.append(f"%{batch}%")
    sql = "SELECT * FROM test_records"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with DB_LOCK:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]


# ==================== UUT 属性 ====================

# UUT 属性本应通过 SN 号从远端获取; 当前先预置在本地 JSON (uut_profiles.json),
# 每条含 batch(批次号)/type/model/remark 等字段, 供测试报告与记录库使用。


def load_uut_profile(sn: str) -> dict | None:
    """按 SN 号获取 UUT 属性; 未预置返回 None"""
    if not sn or not UUT_PROFILES_PATH.exists():
        return None
    try:
        profiles = json.loads(UUT_PROFILES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return profiles.get(sn.strip())


@app.get("/api/uut/{sn}")
def get_uut_profile(sn: str):
    """按 SN 号查询 UUT 属性 (批次号等); 未预置返回空属性"""
    if not UUT_RE.match(sn):
        return {"success": False, "message": "UUT 名称必须为 12 位英文与数字组合"}
    profile = load_uut_profile(sn)
    if profile is None:
        return {"success": True, "uut": {"sn": sn, "batch": "", "part_no": sn[:10]}, "predefined": False}
    return {
        "success": True,
        "uut": {"sn": sn, "batch": profile.get("batch", ""), "part_no": sn[:10], **profile},
        "predefined": True,
    }


@app.get("/api/records")
def list_records(uut: str | None = None, date: str | None = None, project: str | None = None, batch: str | None = None, limit: int = 100):
    """查询测试记录; 支持按 UUT(12位英数) / 日期(YYYY-MM-DD) / 项目 / 批次 组合过滤"""
    limit = max(1, min(int(limit), 500))
    if uut is not None:
        uut = uut.strip()
        if not UUT_RE.match(uut):
            return {"success": False, "message": "UUT 名称必须为 12 位英文与数字组合"}
    date = (date or "").strip()
    project = (project or "").strip()
    batch = (batch or "").strip()
    try:
        records = _fetch_records(uut or None, limit, date or None, project or None, batch or None)
        return {"success": True, "count": len(records), "records": records}
    except Exception as e:
        return {"success": False, "message": f"查询记录失败: {e}", "records": []}


# ==================== TPS 测试程序集 ====================

# ---- 远端 TPS 源 (在线优先, 离线回退本地) ----


def _http_get_json(url: str) -> dict | None:
    """GET 远端 JSON, 超时/异常返回 None"""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=REMOTE_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _fetch_remote_tps_list() -> list[dict] | None:
    """从远端服务器拉取 TPS 摘要列表; 失败返回 None"""
    data = _http_get_json(f"{REMOTE_TPS_URL}/api/tps")
    if not data or not isinstance(data.get("tps"), list):
        return None
    return [t for t in data["tps"] if isinstance(t, dict) and t.get("id")]


def _fetch_remote_tps_detail(tps_id: str) -> dict | None:
    """从远端服务器拉取 TPS 详情; 失败返回 None"""
    data = _http_get_json(f"{REMOTE_TPS_URL}/api/tps/{tps_id}")
    if not data or not data.get("success") or not isinstance(data.get("tps"), dict):
        return None
    return data["tps"]


def _load_tps_files() -> list[Path]:
    """扫描 TPS 定义: testresource/*.json(v1 单文件) + testresource/<目录>/tps.json(v2 包)"""
    if not TPS_DIR.exists():
        return []
    try:
        return tps_runtime.load_tps_files(TPS_DIR)
    except Exception:
        return sorted(p for p in TPS_DIR.glob("*.json") if p.is_file())


def _read_tps(path: Path) -> dict | None:
    """读取 TPS 清单: v2 目录式(含 cmd_suit)自动归一化, v1 单文件原样返回"""
    try:
        tps = tps_runtime.read_tps(Path(path))
        if tps:
            return tps
    except Exception:
        pass
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("id") and data.get("steps"):
            return data
    except Exception:
        return None
    return None


def _tps_summary(tps: dict) -> dict:
    """TPS 摘要 (列表用, 不含完整步骤)"""
    steps = tps.get("steps", [])
    return {
        "id": tps["id"],
        "name": tps.get("name", tps["id"]),
        "description": tps.get("description", ""),
        "version": tps.get("version", ""),
        "schema": tps.get("schema", "tps.v1"),
        "step_count": len(steps),
        "environment": tps.get("environment", {}),
        "cmd_suit_count": len(tps.get("cmd_suit") or []),
        "testconfig_count": len(tps.get("testconfig") or {}),
        "device_alias_count": len(tps.get("device_config") or {}),
        "bench": tps.get("bench", {}),
        "workspace_dir": tps.get("workspace_dir", ""),
    }


@app.get("/api/tps")
def list_tps():
    """获取 TPS 列表: 在线时从远端服务器拉取; 离线时回退本地默认 TPS 并标记 source"""
    remote = _fetch_remote_tps_list()
    if remote is not None:
        return {"success": True, "count": len(remote), "tps": remote, "source": "remote", "remote_ok": True}
    tps_list = []
    for path in _load_tps_files():
        tps = _read_tps(path)
        if tps:
            tps_list.append(_tps_summary(tps))
    return {
        "success": True,
        "count": len(tps_list),
        "tps": tps_list,
        "source": "local",
        "remote_ok": False,
        "remote_error": f"远端 TPS 服务器不可达: {REMOTE_TPS_URL}（已回退本地默认 TPS）",
    }


@app.get("/api/tps/by-uut")
def get_tps_by_uut(uut: str):
    """按 UUT 名称查询最近一次使用的 TPS (从 SQLite 记录库匹配)"""
    uut = (uut or "").strip()
    if not UUT_RE.match(uut):
        return {"success": False, "message": "UUT 名称必须为 12 位英文与数字组合"}
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT tps_id FROM test_records WHERE uut = ? ORDER BY id DESC LIMIT 1", (uut,)
        ).fetchone()
    if not row:
        return {"success": False, "message": f"未找到 UUT {uut} 的测试记录", "tps": None}
    tps_id = row["tps_id"]
    # 优先远端, 回退本地
    remote = _fetch_remote_tps_detail(tps_id)
    if remote is not None:
        return {"success": True, "tps": remote, "source": "remote"}
    for path in _load_tps_files():
        tps = _read_tps(path)
        if tps and tps["id"] == tps_id:
            return {"success": True, "tps": tps, "source": "local"}
    return {"success": False, "message": f"UUT {uut} 对应的 TPS 不存在: {tps_id}"}


@app.get("/api/tps/{tps_id}")
def get_tps(tps_id: str):
    # 优先远端, 离线回退本地
    remote = _fetch_remote_tps_detail(tps_id)
    if remote is not None:
        return {"success": True, "tps": remote, "source": "remote"}
    for path in _load_tps_files():
        tps = _read_tps(path)
        if tps and tps["id"] == tps_id:
            return {"success": True, "tps": tps, "source": "local"}
    return {"success": False, "message": f"TPS 不存在: {tps_id}"}


class TpsImportRequest(BaseModel):
    name: str = ""        # 原始文件名 (仅用于提示)
    content: str           # TPS JSON 文本内容


@app.post("/api/tps/import")
def import_tps(req: TpsImportRequest):
    """导入 TPS: 校验 JSON 结构与必填字段, 保存到 testresource 目录立即生效"""
    try:
        data = json.loads(req.content)
    except Exception:
        return {"success": False, "message": "TPS 文件不是有效的 JSON 格式"}
    if not isinstance(data, dict):
        return {"success": False, "message": "TPS 必须是 JSON 对象"}
    tps_id = str(data.get("id", "")).strip()
    if not tps_id:
        return {"success": False, "message": "TPS 缺少必填字段: id"}
    if not re.match(r"^[A-Za-z0-9_\-]{1,64}$", tps_id):
        return {"success": False, "message": "TPS id 只能包含英文、数字、下划线、短横线 (1-64 位)"}
    if tps_runtime.is_v2(data):
        # TPS v2 包(含 cmd_suit): 落成 testresource/<id>/tps.json, 用例实现放 <id>/testcase/
        return tps_runtime.import_v2(json.dumps(data, ensure_ascii=False), TPS_DIR)
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        return {"success": False, "message": "TPS 缺少有效字段: steps (非空数组)"}
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not step.get("name"):
            return {"success": False, "message": f"steps[{i}] 缺少 name 字段"}
        stype = step.get("type", "test")
        if stype not in ("init", "test", "teardown"):
            return {"success": False, "message": f"steps[{i}] type 必须是 init/test/teardown"}
        if stype == "test" and not step.get("testcase_id"):
            return {"success": False, "message": f"steps[{i}] (test) 缺少 testcase_id"}
        if stype != "test" and not step.get("script"):
            return {"success": False, "message": f"steps[{i}] ({stype}) 缺少 script"}
    # 去重: 已存在同 id 则覆盖
    data.setdefault("name", tps_id)
    data.setdefault("version", "1.0.0")
    data.setdefault("project", "导入项目")
    data.setdefault("dut", "导入 DUT")
    target = TPS_DIR / f"{tps_id}.json"
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "message": f"TPS 导入成功: {tps_id}（已加入装备树）", "tps": _tps_summary(data)}


TPS_TASKS: dict[str, dict] = {}
TPS_TASKS_LOCK = threading.Lock()


class TpsRunRequest(BaseModel):
    tps_id: str | None = None
    uut: str = ""
    bench_id: str = ""            # 指定测试台（不填则按 TPS 清单 bench 自动匹配）
    mode: str = "simulate"        # simulate(默认, 无硬件) | real(真机)


def _decode_output(data: bytes) -> str:
    """子进程输出智能解码: 逐行优先 UTF-8 严格解码, 失败回退 GBK (Windows 中文环境常见)

    之前用 encoding='utf-8', errors='replace' 会把 GBK 字节流硬按 UTF-8 解码,
    无效字节被替换成 U+FFFD (乱码) 且信息丢失无法恢复; 改为逐行尝试解码,
    兼容脚本以 UTF-8 / GBK 混合输出的情况。
    """
    if not data:
        return ""
    out_lines = []
    for raw_line in data.splitlines(keepends=True):
        line = raw_line
        for enc in ("utf-8", "gbk"):
            try:
                line = raw_line.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            line = raw_line.decode("utf-8", errors="replace")
        out_lines.append(line)
    return "".join(out_lines)


def _run_tps_step(step: dict, tps_dir: Path, stage: dict | None = None) -> tuple[bool, str]:
    """执行单个 TPS 步骤, 返回 (是否成功, 输出摘要)

    * v2 TPS(cmd_suit 制): 在 workspace 临时副本里执行由 cmd_suit 生成的 pytest 用例
    * v1 TPS(旧 steps 制): 保持原行为(test 步骤跑 testcase_id, init/teardown 跑脚本)
    """
    if step.get("v2"):
        if not stage or not stage.get("run_dir"):
            return False, "缺少运行环境(workspace 临时副本), 无法执行 v2 步骤"
        return tps_runtime.run_step(step, stage)
    step_type = step.get("type", "test")
    if step_type == "test":
        node = step.get("testcase_id", "")
        cmd = [sys.executable, "-m", "pytest", node, "-v", "--tb=short", "--no-header", "-p", "no:cacheprovider"]
        cwd = BASE_DIR
    else:
        script = step.get("script", "")
        script_path = tps_dir / script
        if not script_path.exists():
            return False, f"脚本不存在: {script}"
        cmd = [sys.executable, str(script_path)]
        cwd = BASE_DIR

    try:
        # 字节级读取 + 智能解码, 兼容 UTF-8 / GBK (Windows 中文) 输出
        proc = subprocess.run(cmd, capture_output=True, cwd=cwd, timeout=300)
        out = _decode_output(proc.stdout or b"").strip() + "\n" + _decode_output(proc.stderr or b"").strip()
        lines = [ln for ln in out.splitlines() if ln.strip()]
        detail = lines[-8:] if lines else []
        ok = proc.returncode == 0
        return ok, "\n".join(detail)
    except subprocess.TimeoutExpired:
        return False, "步骤执行超时 (300s)"
    except Exception as e:
        return False, f"执行异常: {e}"


def _tps_step_html(step: dict, idx: int) -> str:
    """报告中的单个步骤 HTML 行"""
    name = step.get("name", f"步骤 {idx}")
    stype = step.get("type", "test")
    desc = step.get("description", "")
    type_label = {"init": "环境初始化", "test": "测试用例", "teardown": "环境终止"}.get(stype, stype)
    return f"""<tr>
      <td>{idx}</td><td>{name}</td><td>{type_label}</td><td>{desc}</td>
    </tr>"""


def _generate_report(task: dict, tps: dict) -> str:
    """生成测试报告 HTML (自包含, 可独立打开)

    规范字段: 日期 / 批次(UUT属性) / 测试项目(TPS名称) / 项目(单条测试用例) /
    设备编号(测试台) / UUT类型(SN前10位=PN) / 操作员工号 / 测试结果(OK|NOK) /
    测试耗时(秒) / 测试详情(原始输出·阈值·尝试次数) / 报告文件链接
    """
    env = tps.get("environment", {})
    env_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in env.items())
    step_rows = ""
    step_status = {}
    for s in task.get("steps", []):
        step_status[s["index"]] = s
    for i, step in enumerate(tps.get("steps", []), 1):
        st = step_status.get(i, {})
        status = st.get("status", "pending")
        status_label = {"pending": "未执行", "running": "执行中", "passed": "通过", "failed": "失败", "skipped": "跳过"}.get(status, status)
        badge_cls = {"passed": "ok", "failed": "err", "running": "run", "skipped": "warn"}.get(status, "idle")
        detail = (st.get("detail") or "").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        duration = st.get("duration")
        dur_text = f"{duration:.2f}s" if duration is not None else "-"
        step_rows += f"""<tr>
          <td>{i}</td><td>{step.get('name', '')}</td>
          <td>{'环境初始化' if step.get('type')=='init' else ('环境终止' if step.get('type')=='teardown' else '测试用例')}</td>
          <td><span class="badge {badge_cls}">{status_label}</span></td>
          <td>{dur_text}</td><td class="detail">{detail}</td>
        </tr>"""
    total = len(tps.get("steps", []))
    passed = sum(1 for s in task.get("steps", []) if s["status"] == "passed")
    failed = sum(1 for s in task.get("steps", []) if s["status"] == "failed")
    skipped = sum(1 for s in task.get("steps", []) if s["status"] == "skipped")
    conclusion = "PASS" if task["status"] == "completed" else "FAIL"
    uut = task.get("uut", "-")
    bench_info = task.get("bench") or {}
    bench_text = f"{bench_info.get('title', '')}/{bench_info.get('serial', '')}".strip("/") or "-"
    project = tps.get("project", "-")
    dut = tps.get("dut", "-")
    # ---- 规范字段 ----
    equip = load_equipment()
    equip_serial = equip.get("serial", "") if equip.get("success") else ""
    equip_title = equip.get("title", "") if equip.get("success") else ""
    profile = load_uut_profile(uut) or {}
    batch = profile.get("batch", "") or "-"
    part_no = uut[:10] if len(uut) >= 10 else uut
    result = "OK" if task["status"] == "completed" else "NOK"
    report_link = f"reports/tps_{task.get('id', '')}.html"
    # 测试详情: 各步骤名称 + 输出摘要 (原始输出/阈值/尝试次数等见步骤表)
    step_names = "、".join(s.get("name", "") for s in tps.get("steps", []))
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>测试报告 - {tps.get('name', '')}</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; padding: 24px; }}
h1 {{ font-size: 20px; margin: 0 0 4px; }}
.sub {{ color: #94a3b8; font-size: 13px; margin-bottom: 20px; }}
.card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 16px 20px; margin-bottom: 16px; }}
.card h2 {{ font-size: 14px; margin: 0 0 10px; color: #38bdf8; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ text-align: left; padding: 7px 10px; border-bottom: 1px solid #334155; vertical-align: top; }}
th {{ color: #94a3b8; font-weight: 600; }}
.badge {{ display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
.badge.ok {{ background: rgba(52,211,153,.15); color: #34d399; }}
.badge.err {{ background: rgba(248,113,113,.15); color: #f87171; }}
.badge.run {{ background: rgba(56,189,248,.15); color: #38bdf8; }}
.badge.warn {{ background: rgba(251,191,36,.15); color: #fbbf24; }}
.badge.idle {{ background: rgba(148,163,184,.15); color: #94a3b8; }}
.conclusion {{ font-size: 16px; font-weight: 700; }}
.conclusion.pass {{ color: #34d399; }} .conclusion.fail {{ color: #f87171; }}
.detail {{ font-family: Consolas, monospace; font-size: 12px; color: #a8c0dd; white-space: pre-wrap; }}
.summary {{ display: flex; gap: 24px; font-size: 13px; color: #cbd5e1; flex-wrap: wrap; }}
.kv {{ display: inline-block; }}
.kv b {{ color: #94a3b8; font-weight: 600; margin-right: 4px; }}
.info td:first-child {{ color: #94a3b8; font-weight: 600; width: 140px; }}
.link {{ color: #38bdf8; text-decoration: none; }}
</style></head><body>
<h1>测试报告 - {tps.get('name', '')}</h1>
<div class="sub">TPS: {tps.get('id', '')} · v{tps.get('version', '')} · 开始 {task.get('start_time', '-')} · 结束 {task.get('end_time', '-')}</div>
<div class="card"><h2>报告信息（规范字段）</h2>
<table class="info">
<tr><td>日期</td><td>{task.get('start_time', '-')}</td></tr>
<tr><td>批次</td><td>{batch}</td></tr>
<tr><td>测试项目（TPS 名称）</td><td>{tps.get('name', '')}</td></tr>
<tr><td>项目（单条测试用例）</td><td>{step_names or '-'}</td></tr>
<tr><td>设备编号（测试台）</td><td>{equip_serial or '-'}（{equip_title}）</td></tr>
<tr><td>UUT 类型（PN 号）</td><td>{part_no}</td></tr>
<tr><td>操作员工号</td><td>{OPERATOR_ID}</td></tr>
<tr><td>测试结果</td><td><span class="badge {'ok' if result=='OK' else 'err'}">{result}</span></td></tr>
<tr><td>测试耗时（秒）</td><td>{task.get('duration', 0):.2f}</td></tr>
<tr><td>测试详情</td><td>详见下方执行步骤表（执行结果 / 原始输出 / 阈值 / 尝试次数）</td></tr>
<tr><td>报告文件链接</td><td><a class="link" href="{report_link}">{report_link}</a></td></tr>
</table></div>
<div class="card"><h2>结论</h2>
<div class="summary"><span class="conclusion {'pass' if conclusion=='PASS' else 'fail'}">{conclusion}</span><span>通过 {passed} / {total}</span><span>失败 {failed}</span><span>跳过 {skipped}</span><span>总耗时 {task.get('duration', 0):.2f}s</span></div></div>
<div class="card"><h2>装备信息</h2>
<div class="summary"><span class="kv"><b>项目</b>{project}</span><span class="kv"><b>测试对象 DUT</b>{dut}</span><span class="kv"><b>UUT 编号</b>{uut}</span><span class="kv"><b>设备编号</b>{equip_serial or '-'}</span><span class="kv"><b>测试台</b>{bench_text}</span><span class="kv"><b>运行模式</b>{task.get('mode', 'simulate')}</span></div></div>
<div class="card"><h2>运行环境 (workspace)</h2><table><tr><td>临时副本目录</td><td class="detail">{task.get('workspace') or '-'}</td></tr><tr><td>公共 conftest</td><td class="detail">{tps_runtime.workspace_root(False) / 'conftest.py'}</td></tr><tr><td>TPS 清单</td><td class="detail">{tps.get('schema', 'tps.v1')} · cmd_suit {len(tps.get('cmd_suit') or [])} 条 · 阈值 {len(tps.get('testconfig') or {})} 项 · 设备别名 {len(tps.get('device_config') or {})} 个</td></tr></table></div>
<div class="card"><h2>测试环境</h2><table>{env_rows}</table></div>
<div class="card"><h2>执行步骤</h2><table><thead><tr><th>#</th><th>步骤</th><th>类型</th><th>状态</th><th>耗时</th><th>输出</th></tr></thead><tbody>{step_rows}</tbody></table></div>
</body></html>"""
    return html


def _run_tps_task(task_id: str) -> None:
    with TPS_TASKS_LOCK:
        task = TPS_TASKS[task_id]
        tps_id = task["tps_id"]
        tps_path = next((p for p in _load_tps_files() if _read_tps(p) and _read_tps(p)["id"] == tps_id), None)
        tps = _read_tps(tps_path) if tps_path else None
        if not tps:
            task["status"] = "failed"
            task["end_time"] = now_str()
            return
        task["tps"] = tps
        task["status"] = "running"
        task["start_time"] = now_str()
        task["steps"] = [{"index": i, "status": "pending", "detail": "", "duration": None} for i in range(1, len(tps["steps"]) + 1)]
        import time as _time
        task["_t0"] = _time.time()
        tps_dir = tps_path.parent
        stage = None
        if tps.get("schema") == tps_runtime.TPS_V2_SCHEMA:
            # v2: 先把 TPS 复制一份临时副本到 workspace/<TPS名称>/run_<task_id>/ 再执行
            try:
                stage = tps_runtime.stage(
                    tps, tps_dir,
                    task_id=task_id,
                    uut=task.get("uut", ""),
                    bench_id=task.get("bench_id", ""),
                    mode=task.get("mode", "simulate"),
                )
                task["workspace"] = stage["run_dir"]
                task["bench"] = stage.get("bench", {})
                if stage.get("bench_warning"):
                    task["bench_warning"] = stage["bench_warning"]
            except Exception as e:
                task["status"] = "failed"
                task["end_time"] = now_str()
                task["error"] = f"运行环境准备失败: {e}"
                with open(TEST_LOG_DIR / f"tps_{task_id}.log", "w", encoding="utf-8") as lf:
                    lf.write(f"运行环境准备失败: {e}\n")
                return

    total = len(tps["steps"])
    all_ok = True
    log_path = TEST_LOG_DIR / f"tps_{task_id}.log"
    with open(log_path, "w", encoding="utf-8") as lf:
        lf.write(f"===== ATE Runner 测试日志 =====\n")
        lf.write(f"TPS      : {tps_id} ({tps.get('name', tps_id)})\n")
        lf.write(f"UUT      : {task.get('uut', '')}\n")
        lf.write(f"Project  : {tps.get('project', '')}\n")
        lf.write(f"DUT      : {tps.get('dut', '')}\n")
        lf.write(f"Start    : {task['start_time']}\n")
        lf.write(f"Steps    : {total}\n")
        lf.write(f"Workspace: {task.get('workspace') or '-'}\n")
        lf.write(f"Bench    : {(task.get('bench') or {}).get('title', '-')} / {(task.get('bench') or {}).get('serial', '-')}\n")
        lf.write(f"Mode     : {task.get('mode', 'simulate')}\n")
        if task.get("bench_warning"):
            lf.write(f"Warning  : {task['bench_warning']}\n")
        lf.write("-" * 60 + "\n")
    with TPS_TASKS_LOCK:
        task["current_step"] = 0
    for i, step in enumerate(tps["steps"], 1):
        with TPS_TASKS_LOCK:
            task["current_step"] = i
            task["steps"][i - 1]["status"] = "running"
        t0 = _time.time()
        ok, detail = _run_tps_step(step, tps_dir, stage)
        dt = _time.time() - t0
        with TPS_TASKS_LOCK:
            task["steps"][i - 1]["status"] = "passed" if ok else "failed"
            task["steps"][i - 1]["detail"] = detail
            task["steps"][i - 1]["duration"] = dt
            task["progress"] = int(i / total * 100)
        # 逐步追加测试日志
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(
                f"[{now_str()}] Step {i}/{total} [{step.get('name', step.get('type', '?'))}] "
                f"-> {'PASS' if ok else 'FAIL'} ({dt:.2f}s)\n"
            )
            if detail:
                lf.write(f"    output: {detail[:400]}\n")
        if not ok:
            all_ok = False
            with TPS_TASKS_LOCK:
                for j in range(i, total):
                    task["steps"][j]["status"] = "skipped"
            break

    with TPS_TASKS_LOCK:
        task["status"] = "completed" if all_ok else "failed"
        task["progress"] = 100
        task["end_time"] = now_str()
        task["duration"] = _time.time() - task.pop("_t0", _time.time())
        try:
            report_html = _generate_report(task, tps)
            report_name = f"tps_{task_id}.html"
            (REPORT_DIR / report_name).write_text(report_html, encoding="utf-8")
            task["report"] = f"reports/{report_name}"
        except Exception:
            task["report"] = None
    # 日志收尾
    with open(log_path, "a", encoding="utf-8") as lf:
        lf.write("-" * 60 + "\n")
        lf.write(f"End      : {task['end_time']}\n")
        lf.write(f"Result   : {'ALL PASS' if all_ok else 'FAILED (中止执行)'}\n")
        lf.write(f"Duration : {task.get('duration', 0):.2f}s\n")

    with open(LOG_DIR / "execution.log", "a", encoding="utf-8") as f:
        with TPS_TASKS_LOCK:
            snap = dict(TPS_TASKS[task_id])
        f.write(f"[{snap['start_time']}] TPS {tps_id} -> {snap['status']} (steps={total}, duration={snap.get('duration', 0):.1f}s)\n")

    # 写入 SQLite 记录库 (规范测试报告字段)
    try:
        _passed = sum(1 for s in task.get("steps", []) if s["status"] == "passed")
        _failed = sum(1 for s in task.get("steps", []) if s["status"] == "failed")
        _skipped = sum(1 for s in task.get("steps", []) if s["status"] == "skipped")
        _uut = task.get("uut", "")
        _profile = load_uut_profile(_uut) or {}
        _equip = load_equipment()
        _save_record(
            {
                "task_id": task_id,
                "uut": _uut,
                "project": tps.get("project", ""),
                "dut": tps.get("dut", ""),
                "tps_id": tps_id,
                "tps_name": tps.get("name", tps_id),
                "status": task["status"],
                "passed": _passed,
                "failed": _failed,
                "skipped": _skipped,
                "total": total,
                "duration": round(task.get("duration", 0), 2),
                "report_file": task.get("report") or "",
                "start_time": task.get("start_time", ""),
                "end_time": task.get("end_time", ""),
                "created_at": now_str(),
                # ---- 规范字段: 批次(UUT属性) / PN(SN前10位) / 工号(预置) / 设备编号 / 结果 OK-NOK ----
                "batch": _profile.get("batch", ""),
                "part_no": _uut[:10] if len(_uut) >= 10 else _uut,
                "operator": OPERATOR_ID,
                "equipment_serial": _equip.get("serial", "") if _equip.get("success") else "",
                "result": "OK" if task["status"] == "completed" else "NOK",
            }
        )
    except Exception:
        pass


@app.post("/api/tps/{tps_id}/run")
def run_tps(tps_id: str, req: TpsRunRequest | None = None):
    tps_path = next((p for p in _load_tps_files() if _read_tps(p) and _read_tps(p)["id"] == tps_id), None)
    if not tps_path:
        return {"success": False, "message": f"TPS 不存在: {tps_id}"}
    uut = (req.uut if req else "").strip()
    if not UUT_RE.match(uut):
        return {"success": False, "message": "UUT 名称必须为 12 位英文与数字组合（如 ABC123DEF456）"}
    task_id = uuid.uuid4().hex[:12]
    with TPS_TASKS_LOCK:
        TPS_TASKS[task_id] = {
            "id": task_id,
            "kind": "tps",
            "tps_id": tps_id,
            "tps_name": _read_tps(tps_path).get("name", tps_id),
            "uut": uut,
            "status": "pending",
            "progress": 0,
            "steps": [],
            "current_step": 0,
            "report": None,
            "start_time": None,
            "end_time": None,
            "duration": None,
        }
    threading.Thread(target=_run_tps_task, args=(task_id,), daemon=True).start()
    return {"success": True, "task_id": task_id, "message": f"已启动 TPS 执行: {tps_id} (UUT={uut})"}


@app.get("/api/tps-tasks/{task_id}")
def tps_task_status(task_id: str):
    with TPS_TASKS_LOCK:
        task = TPS_TASKS.get(task_id)
        if not task:
            return {"success": False, "message": "任务不存在"}
        return {"success": True, **task}


@app.get("/api/reports/{filename}")
def get_report(filename: str):
    """返回生成的测试报告 HTML"""
    if ".." in filename or not filename.endswith(".html"):
        return {"success": False, "message": "非法文件名"}
    path = REPORT_DIR / filename
    if not path.exists():
        return {"success": False, "message": "报告不存在"}
    from fastapi.responses import HTMLResponse

    return HTMLResponse(path.read_text(encoding="utf-8"))


# ==================== AI 测试分析 ====================


class AiAnalyzeRequest(BaseModel):
    api_url: str = ""    # AI API 地址 (OpenAI 兼容 chat/completions)
    api_key: str = ""    # API Key
    task_id: str = ""    # TPS 任务 ID


def _strip_html(html: str, max_len: int = 12000) -> str:
    """从 HTML 报告中提取纯文本 (供 AI 分析)"""
    import re as _re

    text = _re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=_re.S | _re.I)
    text = _re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=_re.S | _re.I)
    text = _re.sub(r"<[^>]+>", " ", text)
    text = _re.sub(r"&nbsp;", " ", text)
    text = _re.sub(r"&lt;", "<", text)
    text = _re.sub(r"&gt;", ">", text)
    text = _re.sub(r"&amp;", "&", text)
    text = _re.sub(r"\s+\n", "\n", text)
    text = _re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:max_len]


@app.post("/api/ai/analyze")
def ai_analyze(req: AiAnalyzeRequest):
    """读取任务测试日志 + 测试报告, 调用配置的 AI API 给出失败分析 (OpenAI 兼容)"""
    task_id = (req.task_id or "").strip()
    api_url = (req.api_url or "").strip()
    api_key = (req.api_key or "").strip()
    if not task_id:
        return {"success": False, "message": "缺少任务 ID"}
    if not api_url or not api_key:
        return {"success": False, "message": "请先在报告区配置 AI API 地址与 Key"}

    # 1. 收集测试日志
    log_path = TEST_LOG_DIR / f"tps_{task_id}.log"
    log_text = ""
    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")[-6000:]

    # 2. 收集报告纯文本
    report_text = ""
    report_path = REPORT_DIR / f"tps_{task_id}.html"
    if report_path.exists():
        report_text = _strip_html(report_path.read_text(encoding="utf-8", errors="replace"))

    if not log_text and not report_text:
        return {"success": False, "message": f"任务 {task_id} 没有可分析的日志或报告"}

    # 3. 组装分析 prompt
    prompt = (
        "你是 ATE(自动测试设备) 的资深测试分析工程师。请根据下面的测试日志与测试报告，"
        "分析测试失败的可能原因，并给出排查建议。要求：\n"
        "1) 先给出结论(失败步骤/失败类型)；\n"
        "2) 列出可能原因(按可能性排序)；\n"
        "3) 给出下一步排查/修复建议；\n"
        "4) 用中文回答，简洁有条理。\n\n"
        f"===== 测试日志 (task {task_id}) =====\n{log_text}\n\n"
        f"===== 测试报告摘要 =====\n{report_text}\n"
    )

    # 4. 调用 AI API (OpenAI 兼容 /chat/completions)
    endpoint = api_url if api_url.endswith("/chat/completions") else api_url.rstrip("/") + "/chat/completions"
    payload = json.dumps(
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1200,
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    try:
        req_obj = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req_obj, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        try:
            analysis = body["choices"][0]["message"]["content"]
        except Exception:
            return {"success": False, "message": f"AI 返回格式异常: {str(body)[:200]}"}
        return {"success": True, "analysis": analysis, "task_id": task_id}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return {"success": False, "message": f"AI 请求失败 (HTTP {e.code}): {detail or e.reason}"}
    except urllib.error.URLError as e:
        return {"success": False, "message": f"无法连接 AI 服务: {e.reason}（请检查 API 地址与网络）"}
    except Exception as e:
        return {"success": False, "message": f"AI 分析异常: {e}"}


# ==================== 测试装备清单 ====================

EQUIPMENT_XML = TPS_DIR / "equipment_demo.xml"   # 测试装备清单 XML 路径
SERIAL_RE = re.compile(r"^SPMTS[0-9]{12}$")          # serial = "SPMTS" + 恰好 12 位 ASCII 数字
DATE_FMT = "%Y-%m-%d"


def _parse_date(text: str) -> datetime | None:
    """解析 YYYY-MM-DD 日期, 非法返回 None"""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), DATE_FMT)
    except (ValueError, TypeError):
        return None


def load_equipment() -> dict:
    """读取并解析测试装备清单 XML, 统一返回 {success, ...} 语义的 dict"""
    # 1) 文件不存在
    if not EQUIPMENT_XML.exists():
        return {"success": False, "message": f"测试装备清单文件不存在: {EQUIPMENT_XML}"}

    # 2) XML 解析 (非法/编码错误)
    try:
        tree = ET.parse(EQUIPMENT_XML)
    except ET.ParseError as e:
        return {"success": False, "message": f"测试装备清单 XML 解析失败: {e}"}
    except Exception as e:
        return {"success": False, "message": f"读取测试装备清单失败: {e}"}

    root = tree.getroot()
    title = (root.get("title") or "").strip()
    serial = (root.get("serial") or "").strip()

    # 3) serial 格式校验: SPMTS + 恰好 12 位数字
    if not SERIAL_RE.match(serial):
        return {"success": False, "message": f"测试台 serial 格式非法: '{serial}' (应为 SPMTS + 12 位数字, 如 SPMTS000000000123)"}

    today = datetime.now().date()
    items: list[dict] = []
    for node in root.findall("equipment"):
        name = (node.get("name") or "").strip()
        model = (node.get("model") or "").strip()
        vendor = (node.get("vendor") or "").strip()
        status = (node.get("status") or "unknown").strip()
        consumable = (node.get("consumable") or "false").strip().lower() == "true"
        item: dict = {
            "name": name,
            "model": model,
            "vendor": vendor,
            "status": status,
            "consumable": consumable,
            "remaining_days": None,
        }
        if consumable:
            try:
                lifespan_days = int((node.get("lifespan_days") or "").strip())
            except (ValueError, TypeError):
                lifespan_days = 0
            installed = _parse_date(node.get("installed_at") or "")
            if lifespan_days > 0 and installed:
                expire = installed.date() + timedelta(days=lifespan_days)
                item["remaining_days"] = (expire - today).days
            else:
                item["remaining_days"] = None
        items.append(item)

    return {"success": True, "title": title, "serial": serial, "items": items}


@app.get("/api/equipment")
def get_equipment():
    """测试装备清单: 测试台信息 (title/serial) + 硬件列表 (易损件含剩余天数)"""
    data = load_equipment()
    if not data.get("success"):
        return {"success": False, "message": data.get("message", "装备清单加载失败")}
    return {
        "success": True,
        "title": data["title"],
        "serial": data["serial"],
        "items": data["items"],
    }



# ==================== 测试台注册与导航 (独立模块) ====================
# 预设测试台类型 / 测试台注册 / 设备连通性自检
# 接口: GET  /api/testbench/presets, GET /api/testbench/overview
#       GET/POST/DELETE /api/testbenches, POST /api/testbenches/{id}/verify
import testbench_registry  # noqa: E402  (末尾导入, 避免与上方定义顺序耦合)

testbench_registry.register_routes(app, TPS_DIR)


# ==================== 元数据管理 (装备树 + 测试台 BOM, 独立模块) ====================
# 装备树节点 (产品 / 子系统 / 测试台类型) 与测试台 BOM 的集中管理,
# 数据落在后端 tree 文件夹 (backend/tree/tree.json + backend/tree/bom/*.json);
# 首次启动自动把既有预设 (testresource/testbench_presets.json) 纳入管理,
# 并由 testbench_registry 作为预设测试台类型的数据源, 供「新建测试台」注册流程读取。
# 接口: GET    /api/metadata/overview | /api/metadata/tree | /api/metadata/store
#       GET    /api/metadata/presets/{id} | /api/metadata/presets/{id}/bom | /api/metadata/device-catalog
#       POST   /api/metadata/products | /api/metadata/subsystems | /api/metadata/presets | /api/metadata/reseed
#       PUT    /api/metadata/presets/{id}/bom
#       POST   /api/metadata/presets/{id}/bom/items
#       DELETE /api/metadata/products/{id} | /subsystems/{id} | /presets/{id} | /presets/{id}/bom/items/{dev}
import metadata_registry  # noqa: E402

metadata_registry.register_routes(app, BASE_DIR, TPS_DIR)


# ==================== 装备助手 (仪器控制工具, 独立模块) ====================
# 工具卡片 / 示波器连接与 SCPI 操作 (泰克 MSO54 方案, 支持离线模拟)
import instrument_tools  # noqa: E402

instrument_tools.register_routes(app, TPS_DIR)


# ==================== 测试台装备运维 (独立模块) ====================
# 待注册导出 / 装备初始化 / 装备终止 / 硬件自检报告 / 报告目录（本地资源管理器）
# 接口: GET  /api/testbench/pending,      POST /api/testbenches/export
#       POST /api/testbenches/{id}/init|teardown|selfcheck
#       GET  /api/testbench/reports, /reports/dir, /reports/{file}
#       POST /api/testbench/reports/open
import testbench_lifecycle  # noqa: E402

testbench_lifecycle.register_routes(app, TPS_DIR)


# ==================== TPS 运行环境 (workspace + 公共 conftest, 独立模块) ====================
# workspace 位于 ATE 安装运行目录下(不存在自动创建); 运行时把 TPS 复制成临时副本到
# workspace/<TPS名称>/run_<task_id>/ 执行; 公共 conftest.py 负责导入装备信息(SQLite
# device_registry 里的 driver 配置)、测试阈值(testconfig)与设备 driver。
# 接口: GET  /api/runtime/workspace, /api/runtime/drivers, /api/runtime/db, /api/runtime/schema
#       POST /api/runtime/workspace/init, /api/runtime/db/sync
#       GET  /api/tps/{id}/manifest, /api/tps/{id}/runs
#       POST /api/tps/{id}/stage, /api/tps/{id}/collect, /api/tps/{id}/runs/cleanup, /api/tps/validate
tps_runtime.register_routes(app, TPS_DIR)

# 启动即把注册库投影到 SQLite(运行时 driver 配置的读取源), 失败不影响主流程
try:
    ate_db.sync_from_registry()
except Exception:
    pass


# ==================== 前端静态托管 (assets + SPA 兜底) ====================
# 必须在所有 /api 路由注册之后挂载: 先匹配到具体接口, 未命中才落到 SPA 兜底。
if WEB_ENABLED:
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles

    _assets_dir = WEB_DIR / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    @app.get("/favicon.ico", include_in_schema=False)
    def _favicon():
        _f = WEB_DIR / "favicon.ico"
        if _f.is_file():
            return FileResponse(str(_f))
        return JSONResponse({"detail": "not found"}, status_code=404)

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa_fallback(full_path: str):
        """SPA 兜底: 命中真实文件返回文件, 否则回 index.html; /api 与 /assets 一律 404。"""
        if full_path.startswith("api/") or full_path.startswith("assets/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        if full_path:
            target = (WEB_DIR / full_path).resolve()
            try:
                target.relative_to(WEB_DIR)
            except ValueError:
                return JSONResponse({"detail": "invalid path"}, status_code=400)
            if target.is_file():
                return FileResponse(str(target))
        index = WEB_DIR / "index.html"
        if index.is_file():
            return FileResponse(str(index))
        return JSONResponse({"detail": "not found"}, status_code=404)
