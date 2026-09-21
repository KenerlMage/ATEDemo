# -*- coding: utf-8 -*-
"""TPS 运行环境（workspace）管理

目录约定（`workspace` 默认位于 ATE 安装运行目录下，不存在时由程序自动创建）
------------------------------------------------------------------------
```
<ATE>/workspace/
├── conftest.py                  # 公共 conftest（程序部署的正本，所有 TPS 共用）
├── SPM读头动态测试/               # 以 TPS 名称命名的运行环境目录
│   ├── tps.json                 # 清单快照（便于现场核对）
│   └── run_<task_id>/           # 每轮运行的**临时副本**（实际执行目录）
│       ├── tps.json             # TPS 清单副本
│       ├── testcase/            # TPS 自带的用例实现副本
│       ├── ops/                 # TPS 自带脚本副本（若有）
│       ├── conftest.py          # 公共 conftest 的副本（保证 pytest 一定能加载）
│       ├── test_tps_generated.py# 由 cmd_suit 生成的执行入口
│       ├── ate_env.json         # 运行环境描述（conftest 读取）
│       ├── run_result.json      # 逐用例结果（conftest 写，供报告汇总）
│       └── run_log.txt          # 运行日志
```
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from . import manifest as mf
from .templates import ENV_FILE, GENERATED_FILE, RESULT_FILE, render_generated

BASE_DIR = Path(__file__).resolve().parent.parent        # D:\ATE\backend
DEFAULT_WORKSPACE = BASE_DIR.parent / "workspace"        # D:\ATE\workspace
CONFTEST_SRC = Path(__file__).resolve().parent / "conftest_template.py"
CONFTEST_VERSION_RE = re.compile(r"ATECONFTEST_VERSION\s*=\s*(\d+)")
DEFAULT_KEEP_RUNS = 5
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "*.log", ".git", "node_modules")


# ---------------------------------------------------------------- 基础


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def workspace_root(create: bool = True, path: Optional[Path] = None) -> Path:
    """workspace 根目录：默认 <ATE>/workspace，可用环境变量 ATE_WORKSPACE 覆盖"""
    root = Path(path or os.environ.get("ATE_WORKSPACE") or DEFAULT_WORKSPACE)
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def conftest_version(path: Path) -> Optional[int]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    hit = CONFTEST_VERSION_RE.search(text)
    return int(hit.group(1)) if hit else None


def deploy_conftest(force: bool = False, root: Optional[Path] = None) -> dict:
    """把公共 conftest 部署到 workspace（版本一致则保留，避免覆盖现场修改）"""
    root = workspace_root(True, root)
    target = root / "conftest.py"
    src_version = conftest_version(CONFTEST_SRC) or 0
    cur_version = conftest_version(target) if target.exists() else None
    action, backup = "kept", ""
    if force or cur_version is None or cur_version != src_version:
        if target.exists():
            backup = str(target.with_name(f"conftest.py.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
            shutil.copy2(target, backup)
        shutil.copy2(CONFTEST_SRC, target)
        action = "updated" if cur_version is not None else "created"
    return {
        "workspace": str(root),
        "path": str(target),
        "action": action,
        "version": src_version,
        "previous_version": cur_version,
        "backup": backup,
    }


def ensure_workspace(root: Optional[Path] = None) -> dict:
    """确保 workspace 与公共 conftest 就绪（幂等，可反复调用）"""
    existed = workspace_root(False, root).exists()
    root = workspace_root(True, root)
    conf = deploy_conftest(root=root)
    return {"success": True, "workspace": str(root), "created": not existed, "conftest": conf}


def tps_dir_name(tps: dict) -> str:
    """运行环境目录名：清单 workspace_dir -> TPS 名称 -> id（去掉非法字符）"""
    raw = str(tps.get("workspace_dir") or tps.get("name") or tps.get("id") or "").strip()
    if not raw:
        raw = "tps"
    safe = re.sub(r'[\\/:*?"<>|]+', "_", raw).strip(" .")
    return safe or str(tps.get("id") or "tps")


def tps_root(tps: dict, root: Optional[Path] = None, create: bool = True) -> Path:
    path = workspace_root(create, root) / tps_dir_name(tps)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


# ---------------------------------------------------------------- 读取 TPS 清单


def load_tps_files(tps_dir: Path) -> list[Path]:
    """TPS 清单文件：目录下的 `*.json`（v1 单文件）+ 子目录里的 `tps.json`（v2 包）"""
    tps_dir = Path(tps_dir)
    files = sorted(p for p in tps_dir.glob("*.json") if p.is_file())
    packs = sorted(p / "tps.json" for p in tps_dir.iterdir() if p.is_dir() and (p / "tps.json").is_file())
    return files + packs


def read_tps(path: Path) -> Optional[dict]:
    """读取 TPS 清单：v2 自动归一化（补 steps），v1 原样返回"""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    if mf.is_v2(raw):
        tps = mf.normalize_tps(raw, Path(path).parent)
        tps["_manifest_file"] = str(path)
        tps["_generated"] = GENERATED_FILE
        return tps
    if raw.get("id") and raw.get("steps"):
        raw["schema"] = mf.TPS_SCHEMA_V1
        raw["_manifest_file"] = str(path)
        return raw
    return None


def find_tps(tps_dir: Path, tps_id: str) -> tuple[Optional[dict], Optional[Path]]:
    for path in load_tps_files(Path(tps_dir)):
        tps = read_tps(path)
        if tps and tps.get("id") == tps_id:
            return tps, path
    return None, None


# ---------------------------------------------------------------- 环境准备（stage）


def _resolve_bench(tps: dict, bench_id: str = "") -> tuple[dict, str]:
    """定位测试台：显式指定 -> 清单 bench.bench_id -> 清单 bench.preset_id -> 清单 bench.serial"""
    import ate_db

    declared = tps.get("bench") or {}
    want_id = str(bench_id or declared.get("bench_id") or "").strip()
    row = ate_db.find_bench(
        bench_id=want_id,
        preset_id=str(declared.get("preset_id") or "").strip(),
        serial=str(declared.get("serial") or "").strip(),
    )
    if row:
        return row, ""
    if want_id:
        return {}, f"指定的测试台不存在: {want_id}"
    if declared:
        return {}, f"未注册匹配的测试台（要求: {json.dumps(declared, ensure_ascii=False)}）"
    return {}, "TPS 未声明测试台，且注册库中没有可用测试台"


def stage(
    tps: dict,
    source_dir: Path,
    *,
    task_id: str,
    uut: str = "",
    bench_id: str = "",
    mode: str = "simulate",
    keep_runs: int = DEFAULT_KEEP_RUNS,
    root: Optional[Path] = None,
    on_driver_error: str = "",
) -> dict:
    """准备一次运行：校验清单 -> 建 workspace -> 复制临时副本 -> 生成执行入口 -> 写运行环境"""
    import ate_db

    source_dir = Path(source_dir).resolve()
    raw = tps.get("_raw") if tps.get("_raw") else {k: v for k, v in tps.items() if not k.startswith("_")}
    errors = mf.validate_v2(dict(raw))
    if errors:
        raise ValueError("TPS 清单校验未通过: " + "；".join(errors))

    conf = deploy_conftest(root=root)
    ws = Path(conf["workspace"])
    root_dir = tps_root(tps, root)
    run_dir = root_dir / f"run_{task_id}"

    # 每次运行都重新复制一份临时副本（源 TPS 改动后立即生效，且运行互不污染）
    if run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)
    shutil.copytree(source_dir, run_dir, dirs_exist_ok=True, ignore=IGNORE)

    entries = mf.case_entries(tps)
    bench, bench_warning = _resolve_bench(tps, bench_id)
    generated = render_generated(
        tps, entries, task_id=task_id, uut=uut, mode=mode,
        bench=f"{bench.get('title', '')}/{bench.get('serial', '')}" if bench else "",
        created_at=_now(),
    )
    (run_dir / GENERATED_FILE).write_text(generated, encoding="utf-8")
    (root_dir / "tps.json").write_text(
        json.dumps(tps.get("_raw") or {k: v for k, v in tps.items() if not k.startswith("_")},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # 公共 conftest 副本：pytest 从运行目录向上找 conftest，这里是双保险
    shutil.copy2(Path(conf["path"]), run_dir / "conftest.py")

    env = {
        "schema": "ate.run.env.v1",
        "created_at": _now(),
        "task_id": task_id,
        "uut": uut,
        "tps_id": tps.get("id", ""),
        "tps_name": tps.get("name", ""),
        "tps_source": str(source_dir),
        "workspace": str(ws),
        "tps_dir": str(root_dir),
        "run_dir": str(run_dir),
        "ate_backend": str(BASE_DIR),
        "db_path": str(ate_db.DB_PATH),
        "bench_id": bench.get("id", ""),
        "bench_title": bench.get("title", ""),
        "bench_serial": bench.get("serial", ""),
        "bench_preset_id": bench.get("preset_id", "") or str((tps.get("bench") or {}).get("preset_id", "")),
        "mode": (mode or "simulate").lower(),
        "on_driver_error": on_driver_error or os.environ.get("ATE_ON_DRIVER_ERROR", ""),
        "conftest_version": mf_version(),
        "keep_runs": keep_runs,
        "generated_file": GENERATED_FILE,
        "case_count": {k: len(v) for k, v in entries.items()},
    }
    (run_dir / ENV_FILE).write_text(json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")

    pruned = cleanup_runs(tps, keep=keep_runs, root=root, protect=run_dir.name)
    files = sorted(str(p.relative_to(run_dir)) for p in run_dir.rglob("*") if p.is_file())
    return {
        "success": True,
        "task_id": task_id,
        "uut": uut,
        "workspace": str(ws),
        "conftest": conf,
        "tps_dir": str(root_dir),
        "run_dir": str(run_dir),
        "tps": mf.summary_tps(tps),
        "bench": {k: bench.get(k, "") for k in ("id", "title", "serial", "preset_id", "line", "station")},
        "bench_warning": bench_warning,
        "mode": env["mode"],
        "case_count": env["case_count"],
        "files": files,
        "pruned": pruned,
    }


def mf_version() -> int:
    return conftest_version(CONFTEST_SRC) or 0


# ---------------------------------------------------------------- 运行 / 结果


def _decode(data: bytes) -> str:
    """子进程输出逐行智能解码（UTF-8 优先，回退 GBK）"""
    if not data:
        return ""
    parts = []
    for raw in data.splitlines(keepends=True):
        for enc in ("utf-8", "gbk"):
            try:
                parts.append(raw.decode(enc))
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            parts.append(raw.decode("utf-8", errors="replace"))
    return "".join(parts)


def read_run_result(run_dir: Path) -> Optional[dict]:
    path = Path(run_dir) / RESULT_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def case_summary(run_dir: Path, case_id: str) -> str:
    """从 run_result.json 里取出某个用例的测量值 / 阈值判定摘要"""
    data = read_run_result(run_dir) or {}
    for case in data.get("cases") or []:
        if case.get("id") != case_id:
            continue
        bits = []
        for key, val in (case.get("measurements") or {}).items():
            bits.append(f"{key}={val}")
        if bits:
            return "测量: " + ", ".join(bits)
        return case.get("message", "")
    return ""


def run_step(step: dict, stage_info: dict, timeout: int = 600) -> tuple[bool, str]:
    """执行一个 v2 步骤（在运行目录里针对生成文件跑单条 pytest 用例）"""
    v2 = step.get("v2") or {}
    node = v2.get("node") or ""
    run_dir = Path(stage_info.get("run_dir") or "")
    if not node or not run_dir.exists():
        return False, f"运行环境缺失（run_dir={run_dir}, node={node}）"
    cmd = [sys.executable, "-m", "pytest", node, "-v", "--tb=short", "--no-header", "-p", "no:cacheprovider"]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["ATE_ENV_FILE"] = str(run_dir / ENV_FILE)
    try:
        proc = subprocess.run(cmd, capture_output=True, cwd=str(run_dir), timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, f"步骤执行超时 ({timeout}s)"
    except Exception as e:
        return False, f"执行异常: {e}"
    out = _decode(proc.stdout or b"") + "\n" + _decode(proc.stderr or b"")
    lines = [ln.rstrip() for ln in out.splitlines() if ln.strip()]
    keep = [ln for ln in lines if ln.startswith("[ATE]") or "PASSED" in ln or "FAILED" in ln
            or "ERROR" in ln or "assert" in ln or "Error" in ln]
    detail = "\n".join((keep or lines)[-8:])
    extra = case_summary(run_dir, v2.get("id", ""))
    if extra:
        detail = (detail + "\n" + extra).strip()
    return proc.returncode == 0, detail


# ---------------------------------------------------------------- 运行记录 / 清理


def _run_sort_key(path: Path) -> tuple:
    """运行目录排序键：按修改时间（新 -> 旧），同名同时戳再按名称"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (mtime, path.name)


def list_runs(tps: dict, root: Optional[Path] = None) -> list[dict]:
    root_dir = tps_root(tps, root, create=False)
    if not root_dir.exists():
        return []
    rows = []
    dirs = sorted((p for p in root_dir.iterdir() if p.is_dir() and p.name.startswith("run_")),
                  key=_run_sort_key, reverse=True)
    for path in dirs:
        env = {}
        try:
            env = json.loads((path / ENV_FILE).read_text(encoding="utf-8"))
        except Exception:
            pass
        result = read_run_result(path) or {}
        summary = result.get("summary") or {}
        rows.append({
            "name": path.name,
            "task_id": env.get("task_id", path.name[4:]),
            "path": str(path),
            "created_at": env.get("created_at", ""),
            "uut": env.get("uut", ""),
            "mode": env.get("mode", ""),
            "bench_serial": env.get("bench_serial", ""),
            "has_result": bool(result),
            "summary": summary,
            "finished": bool(summary) and summary.get("total", 0) > 0,
            "size_bytes": size_of(path),
        })
    return rows


def cleanup_runs(tps: dict, keep: int = DEFAULT_KEEP_RUNS, root: Optional[Path] = None,
                 protect: str = "") -> list[str]:
    """只保留最近 keep 个运行目录（当前运行目录永远保留）"""
    root_dir = tps_root(tps, root, create=False)
    if not root_dir.exists():
        return []
    runs = sorted((p for p in root_dir.iterdir() if p.is_dir() and p.name.startswith("run_")),
                  key=_run_sort_key, reverse=True)
    removed = []
    for idx, path in enumerate(runs):
        if idx < int(keep) or path.name == protect:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path.name)
    return removed


def workspace_overview(root: Optional[Path] = None) -> dict:
    """workspace 总览（前端 / 排查用）"""
    ws = workspace_root(True, root)
    conf_path = ws / "conftest.py"
    tps_dirs = []
    for path in sorted(ws.iterdir()):
        if not path.is_dir():
            continue
        runs = [{"name": p.name, "size_bytes": size_of(p), "created_at": _mtime(p)} for p in
                sorted((q for q in path.iterdir() if q.is_dir() and q.name.startswith("run_")),
                       key=_run_sort_key, reverse=True)]
        tps_dirs.append({
            "name": path.name,
            "path": str(path),
            "runs": runs,
            "run_count": len(runs),
            "size_bytes": size_of(path),
            "has_manifest": (path / "tps.json").is_file(),
        })
    return {
        "success": True,
        "workspace": str(ws),
        "exists": ws.exists(),
        "conftest": str(conf_path),
        "conftest_exists": conf_path.is_file(),
        "conftest_version": conftest_version(conf_path),
        "template_version": mf_version(),
        "tps_dirs": tps_dirs,
        "tps_count": len(tps_dirs),
        "size_bytes": size_of(ws),
        "keep_runs": DEFAULT_KEEP_RUNS,
    }


def _mtime(path: Path) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
    except OSError:
        return ""


def import_v2(content: str, tps_dir: Path) -> dict:
    """导入 TPS v2 包：单文件 JSON -> `<tps_dir>/<id>/tps.json`"""
    try:
        raw = json.loads(content)
    except Exception as e:
        return {"success": False, "message": f"TPS 文件不是有效的 JSON: {e}"}
    if not isinstance(raw, dict):
        return {"success": False, "message": "TPS 必须是 JSON 对象"}
    if not mf.is_v2(raw):
        return {"success": False, "message": "该文件不是 TPS v2 清单（缺少 cmd_suit 字段）"}
    errors = mf.validate_v2(raw)
    if errors:
        return {"success": False, "message": "校验未通过: " + "；".join(errors), "errors": errors}
    tps_id = str(raw["id"])
    pack = Path(tps_dir) / tps_id
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "tps.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    if not (pack / "testcase").exists():
        (pack / "testcase").mkdir(parents=True, exist_ok=True)
        (pack / "testcase" / "__init__.py").write_text('"""TPS 用例实现包"""\n', encoding="utf-8")
    tps = mf.normalize_tps(raw, pack)
    return {"success": True, "message": f"TPS 导入成功: {tps_id}（用例实现放 {pack / 'testcase'}）",
            "tps": mf.summary_tps(tps), "path": str(pack / "tps.json")}
