"""Runner 版本与升级路径定义（单一事实源）。

本模块只定义常量与轻量读写函数，不依赖 FastAPI，便于 launcher / 升级脚本复用。

目录约定（安装形态 <RUNNER_HOME> 即 Inno Setup 的 {app}，例如 C:\\ATERunner）：
    <RUNNER_HOME>/app       后端代码（可替换）
    <RUNNER_HOME>/web       前端构建产物（可替换）
    <RUNNER_HOME>/runtime   嵌入式 Python（可替换）
    <RUNNER_HOME>/logs      测试/运行日志（升级保留）
    <RUNNER_HOME>/workspace TPS 运行目录（升级保留）
    <RUNNER_HOME>/ate.db    历史记录库（升级保留）
    <RUNNER_HOME>/updates   升级工作区（下载 / 暂存 / 备份 / 状态）
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

# ==================== 路径 ====================

BASE_DIR = Path(__file__).resolve().parent
# 开发环境中 BASE_DIR = <repo>/backend，打包后为 <RUNNER_HOME>/app
RUNNER_HOME = Path(os.environ.get("ATE_RUNNER_HOME") or BASE_DIR.parent).resolve()

UPDATES_DIR = RUNNER_HOME / "updates"
DOWNLOAD_DIR = UPDATES_DIR / "download"      # 安装包固定下载路径
STAGING_DIR = UPDATES_DIR / "staging"        # 安装包解压暂存
BACKUP_DIR = UPDATES_DIR / "backup"          # 升级前备份登记
STATE_FILE = UPDATES_DIR / "state.json"      # 升级状态机
PENDING_SWAP_FILE = UPDATES_DIR / "pending_swap.json"
VERSION_FILE = UPDATES_DIR / "installed_version.json"
LOG_FILE = UPDATES_DIR / "update.log"

# ==================== 远端频道 ====================

# 模拟实现: 远端为本地目录; 也可指向 http(s) 地址切换为真实服务器。
_DEFAULT_SERVER = str(BASE_DIR.parent / "tools" / "update-server")
UPDATE_SERVER = (os.environ.get("ATE_UPDATE_SERVER") or _DEFAULT_SERVER).strip()
UPDATE_CHANNEL = (os.environ.get("ATE_UPDATE_CHANNEL") or "stable").strip() or "stable"

# ==================== 版本 ====================

DEFAULT_VERSION = "1.0.250913"


def _read_version_file() -> str:
    try:
        data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        v = str(data.get("version") or "").strip()
        if v:
            return v
    except Exception:
        pass
    return ""


def installed_version() -> str:
    """当前已安装版本: installed_version.json > 环境变量 > 编译期缺省值。"""
    return (
        _read_version_file()
        or (os.environ.get("ATE_RUNNER_VERSION") or "").strip()
        or DEFAULT_VERSION
    )


def write_installed_version(version: str, extra: dict | None = None) -> None:
    ensure_dirs()
    payload = {
        "version": version,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "runner_home": str(RUNNER_HOME),
    }
    if extra:
        payload.update(extra)
    VERSION_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ==================== 升级保留/替换范围 ====================

# 升级时绝不触碰的本地数据（"继承已有数据"的白名单）
PRESERVED_PATHS = [
    "logs",
    "workspace",
    "testresource",
    "ate.db",
    "cloud_demo.db",
    "license.dat",
]

# 允许被新版本覆盖的代码目录（打包形态）
PAYLOAD_DIRS = ["app", "web", "runtime"]


def ensure_dirs() -> None:
    for d in (UPDATES_DIR, DOWNLOAD_DIR, STAGING_DIR, BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)
