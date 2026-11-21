# -*- coding: utf-8 -*-
"""元数据源：装备树 / 测试台类型 / BOM 从 MockController 只读拉取，本地缓存 + 离线兜底。

设计
----
* **MockController 是唯一事实源**：元数据编辑只在 Controller 控制台（/ui）进行，Runner 只读。
* **本地缓存**：每次成功拉取都落盘，Controller 不可达时用缓存继续注册测试台，
  响应里标记 `source=cache`；缓存也没有时标 `source=none` 并带回错误原因。
* **不改结构**：透传 Controller 的 JSON，不改动内部字段；形状适配由调用方（main.py 路由）负责。

环境变量
--------
* `MOCK_CONTROLLER_URL`（兼容旧名 `ATE_CONTROLLER_URL`）默认 http://127.0.0.1:8070
* `ATE_METADATA_CACHE_DIR` 默认 <backend>/metadata_cache
* `ATE_METADATA_TIMEOUT`   单次 HTTP 超时秒数，默认 5

缓存文件
--------
`tree.json` / `overview.json` / `store.json` / `device_catalog.json` /
`device_templates.json` / `presets/<id>.json` / `presets/<id>.bom.json`
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parent
CONTROLLER_URL = (
    os.environ.get("MOCK_CONTROLLER_URL")
    or os.environ.get("ATE_CONTROLLER_URL")
    or "http://127.0.0.1:8070"
).rstrip("/")
CACHE_DIR = Path(os.environ.get("ATE_METADATA_CACHE_DIR") or (BASE_DIR / "metadata_cache"))
TIMEOUT = float(os.environ.get("ATE_METADATA_TIMEOUT") or 5)


def cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def _cache_path(cache_name: str) -> Path:
    """缓存文件路径（限 cache_dir 之内，避免缓存名越界）"""
    base = cache_dir().resolve()
    target = (base / cache_name).resolve()
    if base != target and base not in target.parents:
        raise ValueError(f"缓存路径越界: {cache_name}")
    return target


def _safe_id(value: Any) -> str:
    """测试台类型编号 -> 缓存文件名（保留字母数字与 -_，其余剔除）"""
    keep = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in "-_")
    return keep or "unknown"


def _fetch(url: str) -> tuple[Optional[dict], str]:
    """GET 一个 JSON；返回 (数据, 错误原因)"""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if isinstance(payload, dict):
            return payload, ""
        return None, "响应不是 JSON 对象"
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return None, str(exc) or exc.__class__.__name__


def _read_cache(cache_name: str) -> Optional[dict]:
    path = _cache_path(cache_name)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(cache_name: str, data: dict) -> None:
    try:
        path = _cache_path(cache_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass


def get_json(path_suffix: str, cache_name: str) -> dict:
    """读 Controller 的 JSON：成功落缓存返回 source=controller；失败读缓存 source=cache；否则 none。

    返回 {data, source, error}；data 为 Controller/缓存里的原始 JSON（dict 或 None）。
    HTTP 200 但业务 success=false 的响应（如类型不存在）按 Controller 结果透传，不写缓存。
    """
    data, err = _fetch(f"{CONTROLLER_URL}{path_suffix}")
    if data is not None:
        if data.get("success") is not False:
            _write_cache(cache_name, data)
        return {"data": data, "source": "controller", "error": ""}

    cached = _read_cache(cache_name)
    if cached is not None:
        return {
            "data": cached,
            "source": "cache",
            "error": f"Controller 不可达：{CONTROLLER_URL}（已使用本地缓存）",
        }
    reason = f"（{err}）" if err else ""
    return {
        "data": None,
        "source": "none",
        "error": f"Controller 不可达：{CONTROLLER_URL}{reason}，且无本地缓存",
    }


# ---------------- 具体接口封装 ----------------


def tree() -> dict:
    return get_json("/api/metadata/tree", "tree.json")


def overview() -> dict:
    return get_json("/api/metadata/overview", "overview.json")


def store() -> dict:
    return get_json("/api/metadata/store", "store.json")


def preset(preset_id: str) -> dict:
    safe = _safe_id(preset_id)
    return get_json(f"/api/metadata/presets/{safe}", f"presets/{safe}.json")


def bom(preset_id: str) -> dict:
    safe = _safe_id(preset_id)
    return get_json(f"/api/metadata/presets/{safe}/bom", f"presets/{safe}.bom.json")


def device_catalog() -> dict:
    return get_json("/api/metadata/device-catalog", "device_catalog.json")


def device_templates() -> dict:
    """同批新开发接口，Controller 上可能尚未提供（404 由 get_json 兜底，不抛异常）"""
    return get_json("/api/metadata/device-templates", "device_templates.json")


def status() -> dict:
    """源状态：Controller 地址 / 缓存目录 / 最近一次装备树缓存时间与产品数"""
    cached_at = ""
    tree_products = 0
    path = _cache_path("tree.json")
    if path.is_file():
        try:
            cached_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
            payload = json.loads(path.read_text(encoding="utf-8"))
            nodes = payload.get("tree")
            tree_products = len(nodes) if isinstance(nodes, list) else 0
        except (OSError, json.JSONDecodeError, ValueError):
            cached_at, tree_products = "", 0
    return {
        "controller_url": CONTROLLER_URL,
        "cache_dir": str(CACHE_DIR),
        "cached_at": cached_at,
        "tree_products": tree_products,
    }
