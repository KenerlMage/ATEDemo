"""ATE 装备助手 - 货架与本地安装管理

职责（供前端「装备助手」页与其货架页调用）:
  1) 货架清单        -> GET  /api/assistants/catalog
  2) 已安装列表      -> GET  /api/assistants/installed
  3) 下载（安装）    -> POST /api/assistants/install   {id}
  4) 启动            -> POST /api/assistants/launch    {id}
  5) 移除            -> POST /api/assistants/uninstall {id}
  6) 图标            -> GET  /api/assistants/icon/{id}

货架清单由 MockController 下发（GET /api/v1/assistants），Runner 不再持有权威清单：
  assistant_shelf/cache/catalog.json   最近一次 Controller 清单（断网兜底）
  assistant_shelf/catalog.json         本地兜底清单（Controller 与缓存都不可用时）
  assistant_shelf/installed/<id>/      下载后的本地安装目录（运行时生成，文件来自 Controller）
  assistant_shelf/packages/<id>/       历史本地安装包（仅兜底，不再作为主来源）

安装语义:
  「下载」= 把安装包完整复制到本地安装目录，并在该目录写入 install.json 记录来源与时间。
  已安装时再次下载视为覆盖升级，保留原 install.json 中的首次安装时间。
  安装包本身不被修改，便于重复下载与回滚。

移除语义:
  不做物理删除：把目录移动到 installed/_removed/<id>-<时间戳>/，保留可追溯痕迹。

启动语义:
  读取 install.json / assistant.json 中的 entry（默认 start.bat），
  以独立控制台窗口启动，不阻塞后端；启动记录写回 install.json 的 last_launch。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import hashlib
import urllib.error
import urllib.request

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

SHELF_ROOT: Path = Path("assistant_shelf")
PACKAGES_DIR: Path = Path("assistant_shelf") / "packages"
INSTALLED_DIR: Path = Path("assistant_shelf") / "installed"
CATALOG_PATH: Path = Path("assistant_shelf") / "catalog.json"

# 货架清单来源：controller（MockController 下发）| local（随程序本地清单）
CONTROLLER_URL = (
    os.environ.get("MOCK_CONTROLLER_URL")
    or os.environ.get("ATE_CONTROLLER_URL")
    or "http://127.0.0.1:8070"
).rstrip("/")
SOURCE_MODE = (os.environ.get("ASSISTANT_SOURCE") or "controller").strip().lower()
FETCH_TIMEOUT = float(os.environ.get("ASSISTANT_TIMEOUT") or 8)
CACHE_DIR: Path = Path("assistant_shelf") / "cache"
CACHE_CATALOG: Path = CACHE_DIR / "catalog.json"
CACHE_ICONS: Path = CACHE_DIR / "icons"

ALLOWED_ENTRY_SUFFIX = (".bat", ".cmd", ".exe", ".py")
MAX_PACKAGE_FILES = 500
MAX_PACKAGE_BYTES = 200 * 1024 * 1024


# ==================== 基础工具 ====================


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _safe_id(raw: str) -> str:
    """货架 id 只允许字母数字与 . _ -，防止路径穿越"""
    text = str(raw or "").strip()
    if not text or len(text) > 64:
        raise ValueError("助手标识为空或过长")
    for ch in text:
        if not (ch.isalnum() or ch in "._-"):
            raise ValueError(f"助手标识含非法字符: {ch}")
    if text.startswith(".") or ".." in text:
        raise ValueError("助手标识不合法")
    return text


def _dir_size(path: Path) -> tuple[int, int]:
    """返回 (文件数, 总字节数)"""
    files = 0
    size = 0
    if not path.is_dir():
        return 0, 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            fp = Path(root) / name
            try:
                size += fp.stat().st_size
                files += 1
            except OSError:
                continue
    return files, size


def _copy_tree(src: Path, dst: Path) -> tuple[int, int]:
    """逐文件复制（不使用 shutil，便于统计与失败定位）"""
    files = 0
    size = 0
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, names in os.walk(src):
        rel = Path(root).relative_to(src)
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            source = Path(root) / name
            target = target_dir / name
            with open(source, "rb") as fh:
                payload = fh.read()
            with open(target, "wb") as fh:
                fh.write(payload)
            files += 1
            size += len(payload)
            if files > MAX_PACKAGE_FILES or size > MAX_PACKAGE_BYTES:
                raise ValueError("安装包超出限制，已中止复制")
    return files, size


def _fmt_mb(size_bytes: int) -> str:
    mb = size_bytes / (1024 * 1024)
    if mb < 1:
        return f"{max(size_bytes, 1) / 1024:.0f} KB"
    return f"{mb:.1f} MB"


# ==================== 货架清单 ====================


# ==================== 远端来源（MockController 下发）====================


def _http_json(url: str, timeout: float = FETCH_TIMEOUT) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        return None


def _http_bytes(url: str, timeout: float = FETCH_TIMEOUT * 6) -> Optional[bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def _controller_catalog() -> Optional[dict]:
    """从 MockController 取助手文件清单，成功返回 Controller 结构的目录"""
    data = _http_json(f"{CONTROLLER_URL}/api/v1/assistants")
    if not data or not isinstance(data.get("assistants"), list):
        return None
    items: list[dict] = []
    for raw in data["assistants"]:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        items.append(
            {
                "id": raw.get("id"),
                "name": raw.get("name") or raw.get("id"),
                "version": raw.get("version", ""),
                "vendor": raw.get("vendor", ""),
                "summary": raw.get("summary", ""),
                "description": raw.get("description", ""),
                "tags": raw.get("tags") or [],
                "requires": raw.get("requires", ""),
                "release_note": raw.get("release_note", ""),
                "published_at": raw.get("published_at", ""),
                "entry": raw.get("entry") or "start.bat",
                "size_hint": raw.get("size_hint", ""),
                "size_bytes": int(raw.get("size_bytes") or 0),
                "file_count": int(raw.get("file_count") or 0),
                "self_contained": bool(raw.get("self_contained")),
                "icon": bool(raw.get("icon", True)),
            }
        )
    return {
        "source": "controller",
        "channel": data.get("channel") or "stable",
        "controller_url": CONTROLLER_URL,
        "updated_at": data.get("updated_at", ""),
        "note": data.get("note", ""),
        "items": items,
    }


def _read_cached_catalog() -> Optional[dict]:
    payload = _read_json(CACHE_CATALOG, default=None)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return None
    cached = dict(payload)
    cached["source"] = "cache"
    cached["controller_url"] = CONTROLLER_URL
    cached["note"] = payload.get("note") or "MockController 不可达，使用本地缓存清单"
    return cached


def _write_cached_catalog(payload: dict) -> None:
    try:
        data = dict(payload)
        data["source"] = "controller"
        data["fetched_at"] = _now()
        _write_json(CACHE_CATALOG, data)
    except OSError:
        pass


def load_catalog_raw() -> tuple[dict, str]:
    """返回 (原始清单, 错误信息)；来源标记写在清单的 source 字段"""
    if SOURCE_MODE != "local":
        remote = _controller_catalog()
        if remote is not None:
            _write_cached_catalog(remote)
            return remote, ""

        cached = _read_cached_catalog()
        if cached is not None:
            return cached, f"MockController 不可达：{CONTROLLER_URL}（已使用本地缓存清单）"

        local = _read_json(CATALOG_PATH, default={}) or {}
        local["source"] = "local"
        return local, f"MockController 不可达：{CONTROLLER_URL}，且无缓存（已回退本地清单）"

    local = _read_json(CATALOG_PATH, default={}) or {}
    local["source"] = "local"
    return local, ""


def _controller_files(assistant_id: str) -> Optional[list[dict]]:
    """取 Controller 上该助手的文件清单（path/size/sha256）"""
    data = _http_json(f"{CONTROLLER_URL}/api/v1/assistants/{assistant_id}")
    if not data or not isinstance(data.get("assistant"), dict):
        return None
    files = data["assistant"].get("files")
    return files if isinstance(files, list) else None


def _download_package(assistant_id: str, dest: Path) -> tuple[int, int, str]:
    """按 Controller 文件清单逐文件下载到 dest；返回 (文件数, 字节数, 错误)"""
    files = _controller_files(assistant_id)
    if files is None:
        return 0, 0, "Controller 未返回文件清单"
    count = 0
    total = 0
    for item in files:
        rel = str((item or {}).get("path") or "").replace("\\", "/").lstrip("/")
        if not rel or ".." in rel.split("/"):
            return count, total, f"文件路径不合法：{rel}"
        blob = _http_bytes(f"{CONTROLLER_URL}/api/v1/assistants/{assistant_id}/files/{rel}")
        if blob is None:
            return count, total, f"下载失败：{rel}"
        expect = str((item or {}).get("sha256") or "").lower()
        if expect and hashlib.sha256(blob).hexdigest().lower() != expect:
            return count, total, f"校验不通过：{rel}"
        target = (dest / rel).resolve()
        if not str(target).startswith(str(dest.resolve())):
            return count, total, f"路径越界：{rel}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        count += 1
        total += len(blob)
        if count > MAX_PACKAGE_FILES or total > MAX_PACKAGE_BYTES:
            return count, total, "安装包超出限制，已中止下载"
    return count, total, ""


def source_status() -> dict:
    payload = _read_json(CACHE_CATALOG, default={}) or {}
    return {
        "controller_url": CONTROLLER_URL,
        "mode": SOURCE_MODE,
        "cached_count": len(payload.get("items") or []),
        "cached_at": payload.get("fetched_at", ""),
        "cache_dir": str(CACHE_DIR),
    }


def catalog() -> dict:
    """货架清单：MockController 下发优先，其次缓存，最后本地兜底"""
    raw, fetch_error = load_catalog_raw()
    items = raw.get("items") or []
    if not isinstance(items, list):
        items = []
    installed = {rec["id"]: rec for rec in list_installed()}
    out = []
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        try:
            aid = _safe_id(item["id"])
        except ValueError:
            continue
        pkg = PACKAGES_DIR / aid
        from_remote = raw.get("source") in ("controller", "cache")
        if from_remote:
            files = int(item.get("file_count") or 0)
            size = int(item.get("size_bytes") or 0)
            ready = files > 0
        else:
            files, size = _dir_size(pkg)
            ready = pkg.is_dir()
        rec = installed.get(aid)
        entry = dict(item)
        entry["id"] = aid
        entry["package_ready"] = ready
        entry["package_files"] = files
        entry["package_size"] = size
        entry["package_size_text"] = _fmt_mb(size) if size else "—"
        entry["installed"] = bool(rec)
        entry["installed_at"] = (rec or {}).get("installed_at", "")
        entry["installed_version"] = (rec or {}).get("version", "")
        entry["updated"] = bool(rec) and rec.get("version") != item.get("version")
        entry["icon_url"] = f"/api/assistants/icon/{aid}"
        out.append(entry)
    return {
        "source": raw.get("source", "local"),
        "channel": raw.get("channel", ""),
        "remote_url": raw.get("controller_url") or CONTROLLER_URL,
        "controller_url": CONTROLLER_URL,
        "updated_at": raw.get("updated_at", ""),
        "fetched_at": raw.get("fetched_at", ""),
        "note": raw.get("note", ""),
        "error": fetch_error,
        "count": len(out),
        "installed_count": len([x for x in out if x["installed"]]),
        "items": out,
    }


# ==================== 本地安装区 ====================


def _install_meta(install_dir: Path) -> dict:
    return _read_json(install_dir / "install.json", default={}) or {}


def list_installed() -> list[dict]:
    """已安装助手列表（读 installed/<id>/install.json + assistant.json）"""
    out: list[dict] = []
    if not INSTALLED_DIR.is_dir():
        return out
    for entry in sorted(INSTALLED_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        meta = _install_meta(entry)
        manifest = _read_json(entry / "assistant.json", default={}) or {}
        if not meta and not manifest:
            continue
        files, size = _dir_size(entry)
        out.append(
            {
                "id": entry.name,
                "name": manifest.get("name") or meta.get("name") or entry.name,
                "version": manifest.get("version") or meta.get("version") or "",
                "vendor": manifest.get("vendor", ""),
                "summary": manifest.get("summary", ""),
                "description": manifest.get("description", ""),
                "tags": manifest.get("tags") or [],
                "requires": manifest.get("requires", ""),
                "entry": meta.get("entry") or manifest.get("entry") or "start.bat",
                "icon_url": f"/api/assistants/icon/{entry.name}",
                "installed_at": meta.get("installed_at", ""),
                "source_updated_at": meta.get("source_updated_at", ""),
                "last_launch": meta.get("last_launch", ""),
                "launch_count": int(meta.get("launch_count") or 0),
                "dir": str(entry),
                "files": files,
                "size": size,
                "size_text": _fmt_mb(size) if size else "—",
                "available": True,
                "source": "shelf",
            }
        )
    out.sort(key=lambda x: x.get("installed_at", ""), reverse=True)
    return out


def install(assistant_id: str) -> dict:
    """下载安装：优先从 MockController 拉文件，Controller 不可用时回退本地安装包"""
    aid = _safe_id(assistant_id)
    dest = INSTALLED_DIR / aid
    prev = _install_meta(dest)
    existed = dest.is_dir()

    cat = catalog()
    catalog_item = next((x for x in (cat.get("items") or []) if x["id"] == aid), {})
    # 仅当清单直接来自 Controller 时才走下载；缓存态说明 Controller 不可达，回退本地安装包
    from_remote = cat.get("source") == "controller"
    fetch_error = ""

    if from_remote:
        dest.mkdir(parents=True, exist_ok=True)
        files, size, fetch_error = _download_package(aid, dest)
        if fetch_error:
            if not existed:
                try:
                    for item in dest.rglob("*"):
                        if item.is_file():
                            item.unlink()
                except OSError:
                    pass
            return {"success": False, "message": f"从 MockController 下载失败：{fetch_error}"}
        manifest = _read_json(dest / "assistant.json", default={}) or {}
        if not manifest:
            manifest = {
                "id": aid,
                "name": catalog_item.get("name") or aid,
                "version": catalog_item.get("version", ""),
                "entry": catalog_item.get("entry") or "start.bat",
                "vendor": catalog_item.get("vendor", ""),
                "summary": catalog_item.get("summary", ""),
            }
            _write_json(dest / "assistant.json", manifest)
    else:
        pkg = PACKAGES_DIR / aid
        if not pkg.is_dir():
            return {"success": False, "message": f"MockController 不可达，且本地没有该助手的安装包：{aid}"}
        manifest = _read_json(pkg / "assistant.json", default={}) or {}
        if not manifest:
            return {"success": False, "message": "安装包缺少 assistant.json 清单"}
        try:
            files, size = _copy_tree(pkg, dest)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
        except OSError as exc:
            return {"success": False, "message": f"复制安装包失败：{exc}"}

    if manifest.get("id") and str(manifest["id"]) != aid:
        return {"success": False, "message": "安装包清单与货架标识不一致"}
    meta = {
        "id": aid,
        "name": manifest.get("name", aid),
        "version": manifest.get("version", ""),
        "entry": manifest.get("entry", "start.bat"),
        "source": "controller" if from_remote else "shelf",
        "source_url": CONTROLLER_URL if from_remote else "",
        "source_updated_at": catalog_item.get("published_at", ""),
        "installed_at": prev.get("installed_at") or _now(),
        "updated_at": _now(),
        "files": files,
        "size": size,
        "launch_count": int(prev.get("launch_count") or 0),
        "last_launch": prev.get("last_launch", ""),
    }
    _write_json(dest / "install.json", meta)
    return {
        "success": True,
        "id": aid,
        "name": meta["name"],
        "version": meta["version"],
        "overwritten": existed and bool(prev),
        "files": files,
        "size": size,
        "size_text": _fmt_mb(size),
        "dir": str(dest),
        "message": (
            f"已更新 {meta['name']} 至 {meta['version']}" if existed and prev
            else f"已下载 {meta['name']} {meta['version']}（{files} 个文件 / {_fmt_mb(size)}）"
        ),
    }


def uninstall(assistant_id: str) -> dict:
    aid = _safe_id(assistant_id)
    dest = INSTALLED_DIR / aid
    if not dest.is_dir():
        return {"success": False, "message": f"未安装该助手：{aid}"}
    trash = INSTALLED_DIR / "_removed" / f"{aid}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    trash.parent.mkdir(parents=True, exist_ok=True)
    # 助手进程可能仍占用目录（Windows 目录锁），短暂重试后再给出提示
    last: Optional[OSError] = None
    for _ in range(6):
        try:
            os.rename(dest, trash)
            last = None
            break
        except OSError as exc:
            last = exc
            time.sleep(0.12)
    if last is not None:
        if getattr(last, "winerror", None) in (32, 5):
            return {
                "success": False,
                "busy": True,
                "message": "助手进程正在使用该目录，请先关闭助手窗口再移除",
            }
        return {"success": False, "message": f"移除失败：{last}"}
    return {"success": True, "id": aid, "moved_to": str(trash), "message": f"已移除 {aid}（目录已归档，可恢复）"}


def launch(assistant_id: str) -> dict:
    aid = _safe_id(assistant_id)
    dest = INSTALLED_DIR / aid
    if not dest.is_dir():
        return {"success": False, "message": f"尚未下载该助手：{aid}"}
    meta = _install_meta(dest)
    manifest = _read_json(dest / "assistant.json", default={}) or {}
    entry = str(meta.get("entry") or manifest.get("entry") or "start.bat")
    # 自带运行时的发布包直接起 exe：不经过 cmd 中转，也不会多出一个控制台窗口
    runtime_exe = str(manifest.get("runtime_exe") or "").strip()
    if runtime_exe and (dest / runtime_exe).is_file():
        entry = runtime_exe
    script = (dest / entry).resolve()
    try:
        script.relative_to(dest.resolve())
    except ValueError:
        return {"success": False, "message": "启动脚本路径越界，已拒绝"}
    if script.suffix.lower() not in ALLOWED_ENTRY_SUFFIX:
        return {"success": False, "message": f"不支持的启动脚本类型：{script.suffix}"}
    if not script.is_file():
        return {"success": False, "message": f"启动脚本不存在：{entry}"}

    if script.suffix.lower() in (".bat", ".cmd"):
        cmd = ["cmd", "/c", "start", "", str(script)]
        shell = False
    elif script.suffix.lower() == ".py":
        cmd = [sys.executable or "python", str(script)]
        shell = False
    else:
        cmd = [str(script)]
        shell = False
    flags = 0
    if os.name == "nt" and script.suffix.lower() != ".exe":
        # 脚本类入口用独立控制台窗口：与 DETACHED_PROCESS 互斥，二者不可同时置位；
        # 自带运行时的 exe 本身就是窗口程序，再给控制台会多一个黑框。
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(dest),
            shell=shell,
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        return {"success": False, "message": f"启动失败：{exc}"}

    meta["last_launch"] = _now()
    meta["launch_count"] = int(meta.get("launch_count") or 0) + 1
    _write_json(dest / "install.json", meta)
    return {
        "success": True,
        "id": aid,
        "pid": proc.pid,
        "entry": entry,
        "launched_at": meta["last_launch"],
        "launch_count": meta["launch_count"],
        "message": f"已启动 {meta.get('name') or aid}（{entry}，进程 {proc.pid}）",
    }


def _cached_icon(assistant_id: str) -> Optional[Path]:
    """未安装时从 Controller 拉图标并缓存到 assistant_shelf/cache/icons/<id>.png"""
    cached = CACHE_ICONS / f"{assistant_id}.png"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached
    if SOURCE_MODE == "local":
        return None
    blob = _http_bytes(f"{CONTROLLER_URL}/api/v1/assistants/{assistant_id}/files/icon.png")
    if not blob:
        return None
    try:
        CACHE_ICONS.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(blob)
    except OSError:
        return None
    return cached


def icon_file(assistant_id: str) -> Optional[Path]:
    """优先取已安装目录的图标，其次安装包，最后向 Controller 取（带缓存）"""
    try:
        aid = _safe_id(assistant_id)
    except ValueError:
        return None
    for base in (INSTALLED_DIR / aid, PACKAGES_DIR / aid):
        manifest = _read_json(base / "assistant.json", default={}) or {}
        name = str(manifest.get("icon") or "icon.png")
        candidate = (base / name).resolve()
        try:
            candidate.relative_to(base.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
        fallback = base / "icon.png"
        if fallback.is_file():
            return fallback
    return _cached_icon(aid)


# ==================== 路由 ====================


class IdRequest(BaseModel):
    id: str = ""


def register_routes(app: FastAPI, base_dir: Path) -> None:
    global SHELF_ROOT, PACKAGES_DIR, INSTALLED_DIR, CATALOG_PATH
    SHELF_ROOT = Path(base_dir) / "assistant_shelf"
    PACKAGES_DIR = SHELF_ROOT / "packages"
    INSTALLED_DIR = SHELF_ROOT / "installed"
    CATALOG_PATH = SHELF_ROOT / "catalog.json"
    INSTALLED_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @app.get("/api/assistants/catalog")
    def assistants_catalog():
        data = catalog()
        return {"success": True, **data}

    @app.get("/api/assistants/source")
    def assistants_source():
        """货架清单来源状态：Controller 地址、模式、缓存条目"""
        return {"success": True, **source_status()}

    @app.get("/api/assistants/installed")
    def assistants_installed():
        items = list_installed()
        return {
            "success": True,
            "count": len(items),
            "total_size": sum(x.get("size") or 0 for x in items),
            "items": items,
        }

    @app.post("/api/assistants/install")
    def assistants_install(body: IdRequest):
        try:
            result = install(body.id)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "message": str(exc)})
        return result

    @app.post("/api/assistants/uninstall")
    def assistants_uninstall(body: IdRequest):
        try:
            result = uninstall(body.id)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "message": str(exc)})
        return result

    @app.post("/api/assistants/launch")
    def assistants_launch(body: IdRequest):
        try:
            result = launch(body.id)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "message": str(exc)})
        if not result.get("success"):
            return JSONResponse(status_code=404, content=result)
        return result

    @app.get("/api/assistants/icon/{assistant_id}")
    def assistants_icon(assistant_id: str):
        path = icon_file(assistant_id)
        if not path:
            return JSONResponse(status_code=404, content={"success": False, "message": "无图标"})
        return FileResponse(str(path), media_type="image/png")
