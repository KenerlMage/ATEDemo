"""Runner 升级功能的 HTTP 接口组（零删除版本）。

在 main.py 中通过一行挂载:

    import updater_api
    updater_api.install_routes(app)

必须在该文件末尾的 SPA 兜底路由 ``@app.get("/{full_path:path}")`` **之前**调用,
否则 ``/api/*`` 会被 SPA 兜底吞掉。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

import updater


def _ok(data=None, **extra) -> JSONResponse:
    payload = {"success": True}
    if isinstance(data, dict):
        payload.update(data)
    payload.update(extra)
    return JSONResponse(payload)


def _fail(exc: updater.UpdateError) -> JSONResponse:
    return JSONResponse(
        {
            "success": False,
            "code": exc.code,
            "message": str(exc),
            "active_tasks": getattr(exc, "active", []),
        }
    )


def install_routes(app: FastAPI) -> None:
    """注册升级相关路由。"""

    @app.get("/api/runner/version")
    def runner_version():
        return _ok(updater.manager().version_info())

    @app.get("/api/updates/status")
    def updates_status():
        return _ok(updater.manager().snapshot())

    @app.get("/api/updates/check")
    def updates_check(force: int = 0):
        try:
            return _ok(updater.manager().check(force=bool(force)))
        except updater.UpdateError as exc:
            return _fail(exc)

    @app.get("/api/updates/release-note/{version}")
    def updates_release_note(version: str):
        try:
            return _ok(updater.manager().release_note(version))
        except updater.UpdateError as exc:
            return _fail(exc)

    @app.post("/api/updates/download")
    def updates_download():
        try:
            return _ok(updater.manager().download())
        except updater.UpdateError as exc:
            return _fail(exc)

    @app.post("/api/updates/apply")
    def updates_apply():
        try:
            return _ok(updater.manager().apply())
        except updater.UpdateError as exc:
            return _fail(exc)

    @app.post("/api/updates/rollback")
    def updates_rollback():
        try:
            return _ok(updater.manager().rollback())
        except updater.UpdateError as exc:
            return _fail(exc)

    @app.post("/api/updates/ack-note")
    def updates_ack_note(version: str = ""):
        return _ok(updater.manager().ack_note(version or None))
