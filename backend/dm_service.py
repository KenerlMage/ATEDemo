# -*- coding: utf-8 -*-
"""装备管理模块（DM）在 Runner 侧的服务层

职责
    · 进程管理：按需拉起 / 停止 `dm_broker` 子进程，解析端点文件，做健康检查；
    · 接口面：`/api/dm/*` 供「装备属性配置 → 连接管理」使用；
    · 设备目录：把 SQLite 设备注册表投影成「可连接设备」，与 DM 里「已建立的连接」对照；
    · 容错：DM 未启动、进程崩溃、协议不匹配，一律变成可读状态，不抛给界面。

为什么单独一层
    DM 是独立进程（与 NI 的 TestStand↔MAX 关系一致）：界面刷新、TPS 执行、脚本调试
    都通过它访问仪器，谁都不直接碰驱动实例；进程崩了也只影响自己，Runner 不受牵连。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request

BACKEND = Path(__file__).resolve().parent
SDK = BACKEND.parent / "sdk"
for _p in (str(BACKEND), str(SDK)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ate_dm import DMClient, DmError  # noqa: E402

START_TIMEOUT = 25.0
CLIENT_NAME = "ate-runner"


class DmService:
    def __init__(self, base_dir: Path | None = None, bench: str = "") -> None:
        self.base_dir = Path(base_dir) if base_dir else BACKEND
        self.bench = bench or os.environ.get("ATE_DM_BENCH", "")
        self.runtime_dir = self.base_dir / ".ate_rt"
        self.endpoint_path = self.runtime_dir / "dm_endpoint.json"
        self.log_path = self.runtime_dir / "dm_requests.jsonl"
        self._proc: Optional[subprocess.Popen] = None
        self._client: Optional[DMClient] = None
        self._lock = threading.RLock()
        self._last_error = ""
        self._started_by_us = False
        self._boot_thread: Optional[threading.Thread] = None

    # ---------------- 端点与客户端 ----------------

    def endpoint(self) -> dict:
        try:
            return json.loads(self.endpoint_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def client(self, reconnect: bool = False) -> DMClient:
        with self._lock:
            if self._client is not None and not reconnect:
                return self._client
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
            dm = DMClient(str(self.endpoint_path), timeout=30.0, name=CLIENT_NAME, autoconnect=False)
            dm.connect_transport()
            self._client = dm
            return dm

    def _drop_client(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._client = None

    # ---------------- 进程管理 ----------------

    def alive(self) -> bool:
        ep = self.endpoint()
        if not ep:
            return False
        pid = int(ep.get("pid") or 0)
        if not pid:
            return False
        try:
            if sys.platform == "win32":
                out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                     capture_output=True, text=True, timeout=8)
                return str(pid) in (out.stdout or "")
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def start(self, force: bool = False, bench: str = "") -> dict:
        with self._lock:
            if force:
                self.stop()
            if self.alive():
                try:
                    info = self.client(reconnect=True).ping()
                    return {"success": True, "reused": True, "message": "装备管理模块已在运行",
                            "endpoint": self.endpoint(), "server": info}
                except Exception as exc:
                    self._last_error = f"旧实例不可用：{exc}"
                    self.stop()

            bench = bench or self.bench
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            args = [sys.executable, "-m", "dm_broker",
                    "--bench", bench,
                    "--endpoint", str(self.endpoint_path),
                    "--log", str(self.log_path)]
            flags = 0
            if sys.platform == "win32":
                flags = 0x08000000                     # CREATE_NO_WINDOW：不弹黑框
            try:
                self._proc = subprocess.Popen(
                    args, cwd=str(BACKEND), creationflags=flags,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception as exc:
                self._last_error = f"拉起装备管理模块失败：{exc}"
                return {"success": False, "message": self._last_error}
            self._started_by_us = True

            end = time.time() + START_TIMEOUT
            last = ""
            while time.time() < end:
                if self.endpoint() and self.alive():
                    try:
                        info = self.client(reconnect=True).ping()
                        self._last_error = ""
                        return {"success": True, "reused": False, "message": "装备管理模块已启动",
                                "endpoint": self.endpoint(), "server": info}
                    except Exception as exc:
                        last = str(exc)
                else:
                    last = "等待端点文件"
                time.sleep(0.4)
            self._last_error = f"启动超时：{last}"
            return {"success": False, "message": self._last_error, "endpoint": self.endpoint()}

    def stop(self) -> dict:
        with self._lock:
            stopped = False
            try:
                if self.endpoint():
                    self.client(reconnect=True).shutdown()
                    stopped = True
            except Exception:
                pass
            self._drop_client()
            if self._proc is not None:
                try:
                    self._proc.wait(timeout=5)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None
            pid = int(self.endpoint().get("pid") or 0)
            if pid and sys.platform == "win32":
                for _ in range(10):
                    if not self.alive():
                        break
                    time.sleep(0.3)
                if self.alive():
                    try:
                        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                                       capture_output=True, timeout=8)
                    except Exception:
                        pass
            try:
                if self.endpoint_path.exists():
                    self.endpoint_path.unlink()
            except Exception:
                pass
            self._started_by_us = False
            return {"success": True, "message": "装备管理模块已停止" if stopped else "装备管理模块未在运行"}

    def ensure_started(self) -> dict:
        if self.alive():
            return {"success": True, "reused": True, "message": "已在运行"}
        return self.start()

    def bootstrap_async(self) -> None:
        """应用启动时后台拉起，避免首次执行时等待握手"""
        if os.environ.get("ATE_DM_AUTOSTART", "1") in ("0", "false", "False", "off"):
            return
        if self._boot_thread is not None:
            return

        def run():
            try:
                self.ensure_started()
            except Exception:
                pass

        self._boot_thread = threading.Thread(target=run, name="dm-bootstrap", daemon=True)
        self._boot_thread.start()

    # ---------------- 状态汇总 ----------------

    def status(self) -> dict:
        ep = self.endpoint()
        out: dict = {
            "running": False, "endpoint": ep, "bench": self.bench,
            "endpoint_file": str(self.endpoint_path), "started_by_runner": self._started_by_us,
            "error": self._last_error,
        }
        if not ep:
            out["error"] = out["error"] or "尚未启动装备管理模块"
            return out
        if not self.alive():
            out["error"] = "端点文件存在但进程不在（可能崩溃），请重启"
            return out
        try:
            dm = self.client(reconnect=True)
            out["running"] = True
            out["server"] = dm.ping()
            out["stats"] = dm.stats()
            out["devices"] = dm.devices()
            out["sessions"] = dm.sessions()
            out["caps"] = dm.caps()
            out["error"] = ""
        except Exception as exc:
            out["error"] = f"通信失败：{exc}"
            self._drop_client()
        return out

    def log(self, limit: int = 50) -> list:
        try:
            return self.client().log(limit=limit)
        except Exception:
            # 通道不可用时退化为读日志文件，界面至少还能看到历史
            rows: list = []
            try:
                with self.log_path.open("r", encoding="utf-8") as fh:
                    for line in fh.readlines()[-max(1, limit):]:
                        try:
                            rows.append(json.loads(line))
                        except Exception:
                            continue
            except Exception:
                return []
            return rows


# ---------------------------------------------------------------- 设备目录


def _device_catalog(service: DmService, bench_id: str = "") -> dict:
    """可连接设备（来自注册表投影）与已建立连接（来自 DM）对照"""
    import ate_db
    bench_id = bench_id or os.environ.get("ATE_DM_BENCH", "")
    benches = []
    try:
        benches = ate_db.list_benches()
    except Exception:
        benches = []
    if not bench_id and benches:
        bench_id = str(benches[0].get("id") or benches[0].get("bench_id") or "")
    rows = []
    if bench_id:
        try:
            rows = ate_db.get_devices(bench_id)
        except Exception:
            rows = []
    connected = {}
    try:
        for slot in service.client().devices():
            connected[slot.get("device_id") or slot.get("alias")] = slot
    except Exception:
        pass
    available = []
    for row in rows:
        key = row.get("device_id") or ""
        hit = connected.get(key) or connected.get(row.get("alias") or "")
        available.append({
            "device_id": key,
            "alias": row.get("alias") or key,
            "name": row.get("name") or "",
            "model": row.get("model") or "",
            "device_type": row.get("device_type") or row.get("driver") or "",
            "category": row.get("category") or "",
            "role": row.get("role") or "",
            "interface": row.get("interface") or "",
            "address": row.get("address") or "",
            "host": row.get("host") or "",
            "port": row.get("port") or "",
            "serial_port": row.get("serial_port") or "",
            "connected": bool(hit),
            "handles": list((hit or {}).get("handles") or []),
            "owner": (hit or {}).get("refs", 0) if hit else 0,
        })
    bench_list = [{"bench_id": b.get("id") or b.get("bench_id"),
                   "name": b.get("title") or b.get("preset_name") or b.get("line") or ""}
                  for b in benches]
    return {"bench_id": bench_id, "benches": bench_list, "available": available,
            "connected": list(connected.values())}


# ---------------------------------------------------------------- 路由


def register_routes(app: FastAPI, base_dir: Path | None = None) -> DmService:
    service = DmService(base_dir)

    async def _json(request: Request) -> dict:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _guard(fn, *args, **kwargs) -> dict:
        try:
            out = fn(*args, **kwargs)
            return out if isinstance(out, dict) else {"success": True, "data": out}
        except DmError as exc:
            return {"success": False, "message": str(exc), "code": exc.code}
        except Exception as exc:                      # noqa: BLE001
            return {"success": False, "message": f"{type(exc).__name__}: {exc}"}

    @app.get("/api/dm/status")
    def dm_status():
        return _guard(service.status)

    @app.post("/api/dm/start")
    async def dm_start(request: Request):
        payload = await _json(request)
        return _guard(service.start, force=bool(payload.get("force")),
                      bench=str(payload.get("bench") or ""))

    @app.post("/api/dm/stop")
    def dm_stop():
        return _guard(service.stop)

    @app.get("/api/dm/devices")
    def dm_devices(bench_id: str = ""):
        return _guard(_device_catalog, service, bench_id)

    @app.get("/api/dm/sessions")
    def dm_sessions():
        return _guard(lambda: {"success": True, "sessions": service.client().sessions()})

    @app.get("/api/dm/log")
    def dm_log(limit: int = 50):
        return {"success": True, "lines": service.log(limit=max(1, min(int(limit), 300)))}

    @app.post("/api/dm/connect")
    async def dm_connect(request: Request):
        payload = await _json(request)
        bench_id = str(payload.get("bench_id") or "")
        device_id = str(payload.get("device_id") or "")
        alias = str(payload.get("alias") or "")
        mode = str(payload.get("mode") or "simulate")

        def run() -> dict:
            import ate_db
            if not device_id:
                return {"success": False, "message": "需要 device_id"}
            row = ate_db.get_device(bench_id, device_id) if bench_id else None
            if not row:
                return {"success": False, "message": f"注册表里没有该设备：{bench_id}/{device_id}"}
            res = service.client().connect(row, alias=alias or str(row.get("alias") or device_id),
                                           mode=mode)
            return {"success": True, "message": f"已连接 {res.alias}（句柄 {res.handle}）",
                    "handle": int(res.handle), "alias": res.alias, "driver": res.driver,
                    "resource": res.resource, "idn": res.idn, "simulated": res.simulated}

        return _guard(run)

    @app.post("/api/dm/disconnect")
    async def dm_disconnect(request: Request):
        payload = await _json(request)

        def run() -> dict:
            handle = int(payload.get("handle") or 0)
            out = service.client().disconnect(handle)
            return {"success": True, "message": f"已断开句柄 {handle}", **out}

        return _guard(run)

    @app.post("/api/dm/call")
    async def dm_call(request: Request):
        payload = await _json(request)

        def run() -> dict:
            handle = int(payload.get("handle") or 0)
            action = str(payload.get("action") or "")
            params = payload.get("params") or {}
            if not action:
                return {"success": False, "message": "需要 action"}
            value = service.client().call(handle, action, **params)
            return {"success": True, "handle": handle, "action": action, "result": value}

        return _guard(run)

    @app.post("/api/dm/batch")
    async def dm_batch(request: Request):
        payload = await _json(request)

        def run() -> dict:
            calls = payload.get("calls") or []
            return {"success": True, "results": service.client().batch(calls)}

        return _guard(run)

    @app.get("/api/dm/caps")
    def dm_caps():
        return _guard(lambda: {"success": True, **service.client().caps()})

    return service
