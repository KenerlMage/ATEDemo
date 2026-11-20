# -*- coding: utf-8 -*-
"""常数库同步：Controller → Runner 本地运行库。

背景
----
机器常数模型库的唯一事实源在 ATE Controller；Runner 执行 TPS 时需要读常数
（绑定解析、写 run xml、驱动填充），因此保留本地 `ate.db` 作为**运行期副本**。

同步语义
--------
* 拉取 Controller `/api/v1/constants/export`（缺省导出生效版本）；
* 用现有 `machine_constants.import_bundle` 落本地库，`mode=overwrite` + 保留标定值；
* 落盘缓存一份 bundle，Controller 不可达时可用缓存恢复；
* 只在版本变化或强制时导入，避免每次启动重写库。

环境变量
--------
* `ATE_CONSTANTS_SYNC=auto|off`（默认 auto：启动时尝试同步一次）
* `ATE_CONTROLLER_URL` 复用 tps_source 的配置
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import machine_constants

BASE_DIR = Path(__file__).resolve().parent
CONTROLLER_URL = (
    os.environ.get("MOCK_CONTROLLER_URL")
    or os.environ.get("ATE_CONTROLLER_URL")
    or "http://127.0.0.1:8070"
).rstrip("/")
CACHE_DIR = Path(os.environ.get("ATE_TPS_CACHE_DIR") or (BASE_DIR / "tps_cache"))
MODE = (os.environ.get("ATE_CONSTANTS_SYNC") or "auto").strip().lower()
TIMEOUT = float(os.environ.get("ATE_CONSTANTS_TIMEOUT") or 15)


def _cache_file() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / "constants_bundle.json"


def _state_file() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / "constants_state.json"


def _read_state() -> dict:
    path = _state_file()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(payload: dict) -> None:
    _state_file().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fetch_bundle(timeout: float = TIMEOUT) -> Optional[dict]:
    url = f"{CONTROLLER_URL}/api/v1/constants/export"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        return None
    bundle = data.get("bundle") if isinstance(data, dict) else None
    return bundle if isinstance(bundle, dict) else None


def sync(force: bool = False, version: str = "") -> dict:
    """把 Controller 的常数库同步到本地运行库。

    返回 {ok, version, source, imported, message}；source ∈ controller|cache|none。
    """
    if MODE == "off":
        return {"ok": False, "version": "", "source": "off", "imported": False, "message": "常数同步已关闭"}

    state = _read_state()
    bundle = _fetch_bundle()
    source = "controller"

    if bundle is None:
        cached = _cache_file()
        if cached.is_file():
            try:
                bundle = json.loads(cached.read_text(encoding="utf-8"))
                source = "cache"
            except (OSError, json.JSONDecodeError):
                bundle = None
        if bundle is None:
            return {
                "ok": False,
                "version": "",
                "source": "none",
                "imported": False,
                "message": f"Controller 不可达且无缓存：{CONTROLLER_URL}",
            }

    target_version = (version or bundle.get("version") or "").strip()
    if not target_version:
        return {"ok": False, "version": "", "source": source, "imported": False, "message": "导出包缺少版本号"}

    same = state.get("version") == target_version and state.get("source") != "none"
    if same and not force and not version:
        return {
            "ok": True,
            "version": target_version,
            "source": source,
            "imported": False,
            "message": f"本地已是 {target_version}，跳过导入",
        }

    payload = json.loads(json.dumps(bundle, ensure_ascii=False))
    payload["version"] = target_version
    try:
        result = machine_constants.import_bundle(payload, mode="overwrite", operator="controller-sync", keep_values=True)
    except ValueError as exc:
        return {"ok": False, "version": target_version, "source": source, "imported": False, "message": f"导入失败：{exc}"}

    _cache_file().write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_state(
        {
            "version": target_version,
            "source": source,
            "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    stats = (result or {}).get("stats") or {}
    return {
        "ok": True,
        "version": target_version,
        "source": source,
        "imported": True,
        "message": f"已同步 {target_version}（模型 {stats.get('model_count', '-')} / 字段 {stats.get('field_count', '-')}）",
    }


def status() -> dict:
    state = _read_state()
    return {
        "controller_url": CONTROLLER_URL,
        "mode": MODE,
        "local_version": state.get("version", ""),
        "source": state.get("source", "none"),
        "synced_at": state.get("synced_at", ""),
        "cache_dir": str(CACHE_DIR),
    }
