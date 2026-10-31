"""ATE 装备助手 - 货架与本地安装管理

职责（供前端「装备助手」页与其货架页调用）:
  1) 货架清单        -> GET  /api/assistants/catalog
  2) 已安装列表      -> GET  /api/assistants/installed
  3) 下载（安装）    -> POST /api/assistants/install   {id}
  4) 启动            -> POST /api/assistants/launch    {id}
  5) 移除            -> POST /api/assistants/uninstall {id}
  6) 图标            -> GET  /api/assistants/icon/{id}

货架清单在设计上由远端服务下发；本次为演示，清单与安装包均随程序本地持有：
  assistant_shelf/catalog.json      货架清单（等价于远端返回的 JSON）
  assistant_shelf/packages/<id>/    安装包（等价于远端下载得到的文件）
  assistant_shelf/installed/<id>/   下载后的本地安装目录（运行时生成）

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

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

SHELF_ROOT: Path = Path("assistant_shelf")
PACKAGES_DIR: Path = Path("assistant_shelf") / "packages"
INSTALLED_DIR: Path = Path("assistant_shelf") / "installed"
CATALOG_PATH: Path = Path("assistant_shelf") / "catalog.json"

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


def catalog() -> dict:
    """货架清单（本地文件模拟远端下发）"""
    raw = _read_json(CATALOG_PATH, default={}) or {}
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
        files, size = _dir_size(pkg)
        rec = installed.get(aid)
        entry = dict(item)
        entry["id"] = aid
        entry["package_ready"] = pkg.is_dir()
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
        "remote_url": raw.get("remote_url", ""),
        "updated_at": raw.get("updated_at", ""),
        "note": raw.get("note", ""),
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
    aid = _safe_id(assistant_id)
    pkg = PACKAGES_DIR / aid
    if not pkg.is_dir():
        return {"success": False, "message": f"货架中没有该助手的安装包：{aid}"}
    manifest = _read_json(pkg / "assistant.json", default={}) or {}
    if not manifest:
        return {"success": False, "message": "安装包缺少 assistant.json 清单"}
    if manifest.get("id") and str(manifest["id"]) != aid:
        return {"success": False, "message": "安装包清单与货架标识不一致"}

    dest = INSTALLED_DIR / aid
    prev = _install_meta(dest)
    existed = dest.is_dir()
    try:
        files, size = _copy_tree(pkg, dest)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    except OSError as exc:
        return {"success": False, "message": f"复制安装包失败：{exc}"}

    catalog_item = next((x for x in (catalog().get("items") or []) if x["id"] == aid), {})
    meta = {
        "id": aid,
        "name": manifest.get("name", aid),
        "version": manifest.get("version", ""),
        "entry": manifest.get("entry", "start.bat"),
        "source": "shelf",
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
    if os.name == "nt":
        # 用独立控制台窗口启动：与 DETACHED_PROCESS 互斥，二者不可同时置位
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


def icon_file(assistant_id: str) -> Optional[Path]:
    """优先取已安装目录的图标，其次取安装包图标"""
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
    return None


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

    @app.get("/api/assistants/catalog")
    def assistants_catalog():
        data = catalog()
        return {"success": True, **data}

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
