"""ATE Controller —— 装备软件归档与下载服务。

职责：
1. 接收装备软件包（zip）归档，落盘到本地仓库目录；
2. 提供包清单、包详情、包下载与 manifest 查询接口，供 ATE Runner 拉取；
3. 一切以文件与 sha256 为准，不做版本推断：调用方必须显式给出 object 与 version。

仓库结构：
    <REPO_ROOT>/<object>/<version>/package.zip
    <REPO_ROOT>/<object>/<version>/manifest.json
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

REPO_ROOT = Path(os.environ.get("ATE_CONTROLLER_REPO", r"D:\ATE_CONTROLLER\repo")).resolve()
HOST = os.environ.get("ATE_CONTROLLER_HOST", "0.0.0.0")
PORT = int(os.environ.get("ATE_CONTROLLER_PORT", "8070"))

app = FastAPI(title="ATE Controller", description="装备软件归档与下载服务", version="1.0.0")


def _safe_name(value: str, field: str) -> str:
    """只允许字母、数字、下划线、点、短横线，防止路径穿越。"""
    text = (value or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail=f"{field} 不能为空")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if any(ch not in allowed for ch in text):
        raise HTTPException(status_code=400, detail=f"{field} 只允许字母、数字、下划线、点与短横线：{text}")
    return text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


WINDOWS_SCRIPT_SUFFIXES = {".ps1", ".bat", ".cmd", ".exe", ".com"}
LINUX_SCRIPT_SUFFIXES = {".sh"}


def _normalize_entry(entry: str) -> str:
    """部署脚本必须是包内相对路径。"""
    text = (entry or "").strip().replace("\\", "/")
    if not text:
        raise HTTPException(status_code=400, detail="entry 不能为空：需给出包内部署脚本的相对路径")
    if text.startswith("/") or ":" in text or ".." in text.split("/"):
        raise HTTPException(status_code=400, detail=f"entry 必须是包内相对路径：{entry}")
    return text


def _detect_platform(entry: str, platform: str) -> str:
    """由脚本后缀判定平台；后缀无法判定时要求显式给出 platform。"""
    suffix = Path(entry).suffix.lower()
    value = (platform or "").strip().lower()
    if value:
        if value not in ("windows", "linux"):
            raise HTTPException(status_code=400, detail=f"platform 只能是 windows 或 linux：{platform}")
        allowed = WINDOWS_SCRIPT_SUFFIXES if value == "windows" else LINUX_SCRIPT_SUFFIXES
        if suffix not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"脚本 {suffix} 与 platform={value} 不匹配（windows 支持 .ps1/.bat/.cmd/.exe，linux 支持 .sh）",
            )
        return value
    if suffix in WINDOWS_SCRIPT_SUFFIXES:
        return "windows"
    if suffix in LINUX_SCRIPT_SUFFIXES:
        return "linux"
    raise HTTPException(
        status_code=400,
        detail=f"无法由后缀判断平台（{suffix or '无后缀'}），请显式传 platform=windows|linux",
    )


def _assert_entry_in_zip(archive: Path, entry: str) -> None:
    """entry 必须真实存在于包内；同时拒绝包内根目录自带 manifest.json。"""
    with zipfile.ZipFile(archive) as zf:
        raw_names = [n.replace("\\", "/") for n in zf.namelist()]
    if any(n.lstrip("./") == "manifest.json" for n in raw_names):
        raise HTTPException(
            status_code=400,
            detail="包内不允许包含根目录 manifest.json：manifest 由平台在归档时生成",
        )
    names = set(raw_names)
    if entry not in names:
        preview = ", ".join(sorted(list(names))[:5])
        raise HTTPException(
            status_code=400,
            detail=f"包内不存在 entry 指向的脚本：{entry}（包内前几项：{preview}）",
        )


def _package_dir(obj: str, version: str) -> Path:
    return REPO_ROOT / obj / version


def _read_manifest(obj: str, version: str) -> dict:
    path = _package_dir(obj, version) / "manifest.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"包不存在：{obj}/{version}")
    return json.loads(path.read_text(encoding="utf-8"))


def _list_packages() -> list[dict]:
    packages: list[dict] = []
    if not REPO_ROOT.is_dir():
        return packages
    for obj_dir in sorted(p for p in REPO_ROOT.iterdir() if p.is_dir()):
        versions = []
        for ver_dir in sorted(p for p in obj_dir.iterdir() if p.is_dir()):
            manifest = ver_dir / "manifest.json"
            if not manifest.is_file():
                continue
            try:
                meta = json.loads(manifest.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            versions.append(
                {
                    "version": meta.get("version", ver_dir.name),
                    "name": meta.get("name", obj_dir.name),
                    "sha256": meta.get("sha256", ""),
                    "size": meta.get("size", 0),
                    "entry": meta.get("entry", ""),
                    "platform": meta.get("platform", ""),
                    "uploaded_at": meta.get("uploaded_at", ""),
                }
            )
        if versions:
            versions.sort(key=lambda v: v.get("uploaded_at", ""), reverse=True)
            packages.append({"object": obj_dir.name, "name": versions[0].get("name", obj_dir.name), "versions": versions})
    return packages


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "repo": str(REPO_ROOT),
        "packages": len(_list_packages()),
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/packages")
def list_packages() -> dict:
    return {"ok": True, "packages": _list_packages()}


@app.get("/api/packages/{obj}")
def package_detail(obj: str) -> dict:
    obj = _safe_name(obj, "object")
    for item in _list_packages():
        if item["object"] == obj:
            return {"ok": True, **item}
    raise HTTPException(status_code=404, detail=f"未找到包：{obj}")


@app.get("/api/packages/{obj}/{version}/manifest")
def package_manifest(obj: str, version: str) -> JSONResponse:
    obj = _safe_name(obj, "object")
    version = _safe_name(version, "version")
    return JSONResponse(_read_manifest(obj, version))


@app.get("/api/packages/{obj}/{version}/download")
def download_package(obj: str, version: str):
    obj = _safe_name(obj, "object")
    version = _safe_name(version, "version")
    meta = _read_manifest(obj, version)
    archive = _package_dir(obj, version) / "package.zip"
    if not archive.is_file():
        raise HTTPException(status_code=404, detail=f"包文件缺失：{obj}/{version}")
    return FileResponse(
        path=archive,
        media_type="application/zip",
        filename=f"{obj}-{version}.zip",
        headers={
            "X-Package-Object": obj,
            "X-Package-Version": meta.get("version", version),
            "X-Package-Sha256": meta.get("sha256", ""),
        },
    )


@app.post("/api/packages")
async def upload_package(
    object: str = Form(..., description="部署对象标识，如 spm_rh_dyn"),
    name: str = Form("", description="部署对象名称，用于界面展示"),
    version: str = Form(..., description="版本号，如 1.2.0"),
    entry: str = Form(..., description="包内部署脚本相对路径，如 deploy/install.ps1"),
    platform: str = Form("", description="运行平台 windows / linux；缺省由脚本后缀判定"),
    probe_script: str = Form("", description="探测脚本相对路径（可选），用于判断是否已安装"),
    probe_timeout_s: str = Form("10", description="探测超时秒数"),
    file: UploadFile = File(..., description="装备软件 zip 包"),
) -> dict:
    obj = _safe_name(object, "object")
    ver = _safe_name(version, "version")

    target = _package_dir(obj, ver)
    if target.exists():
        raise HTTPException(status_code=409, detail=f"版本已存在：{obj}/{ver}")

    target.mkdir(parents=True, exist_ok=True)
    archive = target / "package.zip"

    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        shutil.move(str(tmp_path), archive)
        script_entry = _normalize_entry(entry)
        script_platform = _detect_platform(script_entry, platform)
        _assert_entry_in_zip(archive, script_entry)
        digest = _sha256(archive)
        manifest = {
            "object": obj,
            "name": name or obj,
            "version": ver,
            "entry": script_entry,
            "platform": script_platform,
            "sha256": digest,
            "size": archive.stat().st_size,
            "uploaded_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }
        if probe_script.strip():
            probe_entry = _normalize_entry(probe_script)
            _assert_entry_in_zip(archive, probe_entry)
            manifest["deploy"] = {
                "probe": {
                    "script": probe_entry,
                    "timeout_s": int(probe_timeout_s or 10),
                    "expect_exit_code": 0,
                }
            }
        (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise

    return {"ok": True, "manifest": manifest}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=HOST, port=PORT, reload=False)
