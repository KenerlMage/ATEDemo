"""装备软件部署接口：供测试执行页的「部署软件」「部署状态」按钮调用。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import deploy
import tps_runtime

TPS_DIR = Path(__file__).resolve().parent / "testresource"

router = APIRouter(prefix="/api/deploy", tags=["deploy"])


class DeployRequest(BaseModel):
    tps_id: str
    uut: str = ""
    bench_id: str = ""


def _load_tps(tps_id: str) -> dict:
    """按 id 或文件/目录名定位 TPS，返回归一化后的清单。"""
    candidates = []
    try:
        candidates = tps_runtime.load_tps_files(TPS_DIR)
    except Exception:
        candidates = sorted(p for p in TPS_DIR.glob("*.json") if p.is_file())

    for path in candidates:
        stem = Path(path).parent.name if Path(path).name == "tps.json" else Path(path).stem
        if stem != tps_id and Path(path).name != f"{tps_id}.json":
            continue
        try:
            tps = tps_runtime.read_tps(Path(path))
        except Exception:
            continue
        if tps:
            return tps

    for path in candidates:
        try:
            tps = tps_runtime.read_tps(Path(path))
        except Exception:
            continue
        if tps and str(tps.get("id")) == tps_id:
            return tps

    raise HTTPException(status_code=404, detail=f"未找到 TPS：{tps_id}")


@router.get("/preview")
def preview(tps_id: str) -> dict:
    """返回该 TPS 声明的待部署项，不执行部署。"""
    try:
        tps = _load_tps(tps_id)
    except HTTPException:
        raise
    items, errors = deploy.resolve_items(tps)
    for it in items:
        target_id = str((it.get("target") or {}).get("id") or "local")
        inst = deploy.installed_state(it["object"], target_id)
        it["installed_version"] = inst["version"] if inst else ""
        it["up_to_date"] = bool(inst and it.get("version") and inst["version"] == it["version"])
    return {"ok": not errors, "items": items, "errors": errors, "running": deploy.running_batch()}


@router.post("/run")
def run(req: DeployRequest) -> dict:
    tps = _load_tps(req.tps_id)
    try:
        return deploy.start_deploy(tps, req.tps_id, req.uut, req.bench_id)
    except deploy.DeployBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except deploy.DeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/status/{batch_id}")
def status(batch_id: str) -> dict:
    try:
        return deploy.get_batch(batch_id)
    except deploy.DeployError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/installed")
def installed(object: str = "", target: str = "") -> dict:
    """查询已部署情况：给 object 返回单条，否则返回列表。"""
    if object:
        return {"ok": True, "record": deploy.installed_state(object, target or "local")}
    return {"ok": True, "records": deploy.list_installed(target)}


@router.get("/records")
def records(limit: int = 20) -> dict:
    return deploy.list_batches(limit=limit)


@router.get("/controller")
def controller() -> dict:
    return deploy.controller_status()
