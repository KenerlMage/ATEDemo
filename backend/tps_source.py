# -*- coding: utf-8 -*-
"""TPS 源：从 ATE Controller 读取 TPS，本地保留缓存与离线兜底。

设计
----
* **Controller 是唯一事实源**：TPS 编辑只在 Controller Web 进行，Runner 只读。
* **本地缓存**：每次成功拉取都落盘，断网时用缓存继续执行，响应里标记 `source=cache`。
* **物化工作副本**：执行前把 TPS 文件同步到本地 TPS 目录（`testresource/<id>/`），
  运行期仍按既有路径读取，不改变执行链路。
* **开关**：`ATE_TPS_SOURCE=controller|local`，默认 controller；local 用于验收回退。

环境变量
--------
* `ATE_CONTROLLER_URL`  默认 http://127.0.0.1:8070
* `ATE_TPS_SOURCE`      controller | local，默认 controller
* `ATE_TPS_CACHE_DIR`   默认 <backend>/tps_cache
"""

from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parent
CONTROLLER_URL = os.environ.get("ATE_CONTROLLER_URL", "http://127.0.0.1:8070").rstrip("/")
SOURCE = (os.environ.get("ATE_TPS_SOURCE") or "controller").strip().lower()
CACHE_DIR = Path(os.environ.get("ATE_TPS_CACHE_DIR") or (BASE_DIR / "tps_cache"))
TIMEOUT = float(os.environ.get("ATE_TPS_TIMEOUT") or 8)


def _http_json(url: str, timeout: float = TIMEOUT) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        return None


def _http_bytes(url: str, timeout: float = TIMEOUT * 4) -> Optional[bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def _cache_list_path() -> Path:
    return cache_dir() / "list.json"


def _cache_tps_dir(tps_id: str) -> Path:
    return cache_dir() / "tps" / tps_id


def _read_cache_list() -> list[dict]:
    path = _cache_list_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [t for t in data.get("tps", []) if isinstance(t, dict)]
    except (OSError, json.JSONDecodeError):
        return []


def _write_cache_list(items: list[dict]) -> None:
    path = _cache_list_path()
    payload = {"fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"), "tps": items}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_cache_tps(tps: dict) -> None:
    if not tps.get("id"):
        return
    target = _cache_tps_dir(str(tps["id"]))
    target.mkdir(parents=True, exist_ok=True)
    (target / "tps.json").write_text(json.dumps(tps, ensure_ascii=False, indent=2), encoding="utf-8")


def list_tps() -> dict:
    """返回 {items, source, remote_ok, error}。source ∈ controller|cache|none。"""
    if SOURCE == "local":
        return {"items": [], "source": "local", "remote_ok": False, "error": ""}

    data = _http_json(f"{CONTROLLER_URL}/api/v1/tps")
    if data and isinstance(data.get("tps"), list):
        items = [t for t in data["tps"] if isinstance(t, dict) and t.get("id")]
        _write_cache_list(items)
        return {"items": items, "source": "controller", "remote_ok": True, "error": ""}

    cached = _read_cache_list()
    if cached:
        return {
            "items": cached,
            "source": "cache",
            "remote_ok": False,
            "error": f"Controller 不可达：{CONTROLLER_URL}（已使用本地缓存）",
        }
    return {
        "items": [],
        "source": "none",
        "remote_ok": False,
        "error": f"Controller 不可达：{CONTROLLER_URL}，且无本地缓存",
    }


def get_tps(tps_id: str) -> dict:
    """返回 {tps, source, error}。"""
    if SOURCE != "local":
        data = _http_json(f"{CONTROLLER_URL}/api/v1/tps/{tps_id}")
        if data and isinstance(data.get("tps"), dict):
            tps = data["tps"]
            _write_cache_tps(tps)
            return {"tps": tps, "source": "controller", "error": ""}

        cached = _cache_tps_dir(tps_id) / "tps.json"
        if cached.is_file():
            try:
                tps = json.loads(cached.read_text(encoding="utf-8"))
                return {
                    "tps": tps,
                    "source": "cache",
                    "error": f"Controller 不可达：{CONTROLLER_URL}（已使用本地缓存）",
                }
            except (OSError, json.JSONDecodeError):
                pass

    return {"tps": None, "source": "none", "error": f"TPS 不存在或不可达：{tps_id}"}


def sync_files(tps_id: str, dest: Path) -> dict:
    """把 Controller 上该 TPS 的附带文件同步到 dest（物化为运行副本）。

    dest 为本地 TPS 目录 `testresource/<id>/`；先写临时目录再整体替换，避免半成品。
    """
    listing = _http_json(f"{CONTROLLER_URL}/api/v1/tps/{tps_id}/files")
    if not listing or not isinstance(listing.get("files"), list):
        return {"ok": False, "synced": 0, "error": f"无法读取文件清单：{CONTROLLER_URL}"}

    names: list[str] = listing["files"]
    payload: dict[str, bytes] = {}
    for name in names:
        blob = _http_bytes(f"{CONTROLLER_URL}/api/v1/tps/{tps_id}/files/{name}")
        if blob is None:
            return {"ok": False, "synced": len(payload), "error": f"下载失败：{name}"}
        payload[name] = blob

    tmp = dest.with_name(dest.name + ".sync")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for name, blob in payload.items():
            target = (tmp / name).resolve()
            if not str(target).startswith(str(tmp.resolve())):
                return {"ok": False, "synced": 0, "error": f"路径越界：{name}"}
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "synced": 0, "error": f"写入失败：{exc}"}

    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    tmp.replace(dest)
    return {"ok": True, "synced": len(payload), "error": ""}


def status() -> dict:
    cached = _read_cache_list()
    return {
        "controller_url": CONTROLLER_URL,
        "source": SOURCE,
        "cache_dir": str(CACHE_DIR),
        "cached_count": len(cached),
        "cached_at": (
            json.loads(_cache_list_path().read_text(encoding="utf-8")).get("fetched_at", "")
            if _cache_list_path().is_file()
            else ""
        ),
    }
