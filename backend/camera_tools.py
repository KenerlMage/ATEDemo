# -*- coding: utf-8 -*-
"""工业相机装备助手（Basler pylon 8.0.0）：参数设置 / 曝光控制 / 数据保存

定位
    与 instrument_tools（数字示波器）同级的装备助手：会话放在后端进程内，
    前端在「装备助手」页以卡片进入工作区，路由统一挂在 /api/tools/camera 下。

驱动
    复用 `backend/drivers/camera.py`（driver_key = basler-pylon）：
    真实模式走 pypylon，仿真模式按图样生成确定性灰度帧，无硬件也能演示全流程。

能力
    * 参数设置：采集几何（宽 / 高 / 偏移）、像质（像素格式 / 增益 / 帧率）、
      触发（触发模式 / 触发源 / 超时）、仿真场景（图样 / 亮度 / 噪声）；
    * 曝光控制：绝对值设定、按倍数微调、按目标亮度自动曝光（多轮迭代并回放过程）；
    * 数据保存：单帧抓取即落盘、连拍序列落盘、按帧导出为带业务命名的归档文件，
      帧图像可预览、下载，落盘目录按日期分层。
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from drivers import get_driver
from drivers.errors import DriverError

# ==================== 常量 ====================

FRAME_ROOT: Optional[Path] = None        # register_routes 时绑定：<backend>/logs/camera_frames

_SESSIONS: dict[str, "CameraSession"] = {}
_LOCK = threading.RLock()

EXPOSURE_MIN_US = 10.0
EXPOSURE_MAX_US = 60_000_000.0
EXPOSURE_PRESETS = [
    {"label": "100 µs", "us": 100.0},
    {"label": "500 µs", "us": 500.0},
    {"label": "1 ms", "us": 1000.0},
    {"label": "5 ms", "us": 5000.0},
    {"label": "10 ms", "us": 10000.0},
    {"label": "20 ms", "us": 20000.0},
    {"label": "50 ms", "us": 50000.0},
    {"label": "100 ms", "us": 100000.0},
]
PIXEL_FORMATS = ["Mono8", "Mono12", "BayerRG8", "RGB8"]
PATTERNS = [
    {"value": "flat", "label": "均匀场"},
    {"value": "gradient", "label": "水平渐变"},
    {"value": "checker", "label": "棋盘格"},
    {"value": "edge", "label": "阶跃边缘"},
    {"value": "spot", "label": "中心光斑"},
    {"value": "noise", "label": "纯噪声"},
]

# 参数表单描述：前端按此渲染，避免页面里硬编码驱动字段
PARAM_GROUPS = [
    {
        "key": "geometry",
        "label": "采集几何",
        "items": [
            {"key": "width", "label": "采集宽度", "type": "int", "min": 16, "max": 20000, "unit": "px"},
            {"key": "height", "label": "采集高度", "type": "int", "min": 16, "max": 20000, "unit": "px"},
            {"key": "offset_x", "label": "水平偏移", "type": "int", "min": 0, "max": 20000, "unit": "px"},
            {"key": "offset_y", "label": "垂直偏移", "type": "int", "min": 0, "max": 20000, "unit": "px"},
        ],
    },
    {
        "key": "quality",
        "label": "像质与曝光",
        "items": [
            {"key": "pixel_format", "label": "像素格式", "type": "select", "options": PIXEL_FORMATS},
            {"key": "exposure_us", "label": "曝光时间", "type": "float", "min": 10, "max": 60000000,
             "step": 100, "unit": "µs"},
            {"key": "exposure_auto", "label": "自动曝光", "type": "select", "options": ["Off", "Once", "Continuous"]},
            {"key": "gain_db", "label": "增益", "type": "float", "min": -24, "max": 48, "step": 0.1, "unit": "dB"},
            {"key": "gain_auto", "label": "自动增益", "type": "select", "options": ["Off", "Once", "Continuous"]},
        ],
    },
    {
        "key": "timing",
        "label": "采集节奏与触发",
        "items": [
            {"key": "frame_rate", "label": "目标帧率", "type": "float", "min": 0.1, "max": 1000, "step": 1,
             "unit": "fps"},
            {"key": "trigger_mode", "label": "触发模式", "type": "select", "options": ["Off", "On"]},
            {"key": "trigger_source", "label": "触发源", "type": "select", "options": ["Software", "Line1"]},
            {"key": "timeout_ms", "label": "取帧超时", "type": "int", "min": 100, "max": 60000, "unit": "ms"},
        ],
    },
    {
        "key": "simulate",
        "label": "仿真场景（仅仿真模式生效）",
        "sim_only": True,
        "items": [
            {"key": "pattern", "label": "图样", "type": "select", "options": [p["value"] for p in PATTERNS]},
            {"key": "level", "label": "基准亮度", "type": "int", "min": 0, "max": 255},
            {"key": "noise", "label": "噪声幅度", "type": "float", "min": 0, "max": 64, "step": 0.5},
        ],
    },
]

_NAME_RE = re.compile(r"^[0-9A-Za-z_\-.]{1,48}$")


# ==================== 工具函数 ====================


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _session_key(mode: str, serial: str) -> str:
    return f"{'real' if str(mode).lower() == 'real' else 'simulate'}|{str(serial or '').strip()}"


def _frame_root() -> Path:
    if FRAME_ROOT is None:
        raise RuntimeError("相机助手尚未初始化落盘目录")
    return FRAME_ROOT


def _safe_name(name: str, default: str) -> str:
    """导出文件名清洗：只留字母数字与 _ - .，避免路径穿越"""
    text = str(name or "").strip()
    if not text:
        return default
    text = re.sub(r"[^0-9A-Za-z_\-.]", "_", text)
    if not text.lower().endswith(".png"):
        text += ".png"
    return text[:60]


def _frame_path(rel: str) -> Path:
    root = _frame_root().resolve()
    rel = str(rel or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError("非法的帧路径")
    target = (root / rel).resolve()
    if root != target and root not in target.parents:
        raise ValueError("帧路径越界")
    if target.suffix.lower() != ".png":
        raise ValueError("只允许访问 PNG 帧")
    if not target.is_file():
        raise ValueError("帧文件不存在")
    return target


def _rel(path: Path) -> str:
    return path.resolve().relative_to(_frame_root().resolve()).as_posix()


def _frame_url(rel: str) -> str:
    return f"/api/tools/camera/frame?path={quote(rel)}"


def _clamp_exposure(value: float) -> float:
    return max(EXPOSURE_MIN_US, min(EXPOSURE_MAX_US, float(value)))


# ==================== 会话 ====================


class CameraSession:
    """一台相机的控制会话：包一层驱动，负责落盘命名与会话状态。"""

    def __init__(self, mode: str = "simulate", serial: str = "", alias: str = "camera"):
        self.mode = "real" if str(mode).lower() == "real" else "simulate"
        self.serial = str(serial or "").strip()
        self.alias = alias or "camera"
        self.key = _session_key(self.mode, self.serial)
        row = {
            "device_id": f"camera-{self.serial or 'default'}",
            "name": "Basler 工业相机",
            "vendor": "Basler",
            "model": "acA1920-40gm",
            "category": "camera",
            "role": "camera",
            "device_type": "basler-pylon",
            "interface": "GigE",
            "protocol": "pylon",
            "programmable": True,
            "configured": True,
            "note": "装备助手直连（未经测试台注册）",
            "extra": {"serial_number": self.serial, "camera": {}},
        }
        self.driver = get_driver(row, mode=self.mode, alias=self.alias)
        self.frame_seq = 0
        self.opened_at = ""
        self.last_frame: Optional[dict] = None
        self.last_error = ""

    # ---------------- 生命周期 ----------------

    def open(self) -> dict:
        try:
            info = self.driver.open()
            self.opened_at = _now()
            self.last_error = ""
            return info
        except Exception as e:  # 真实模式常见：未装 pypylon / 未识别到相机
            self.last_error = str(e) or e.__class__.__name__
            raise

    def close(self) -> dict:
        try:
            return self.driver.close()
        finally:
            self.opened_at = ""

    # ---------------- 参数与曝光 ----------------

    def configure(self, params: dict) -> dict:
        clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        res = self.driver.configure(**clean)
        return res

    def exposure(self, exposure_us: Optional[float] = None, scale: Optional[float] = None,
                 auto: Optional[str] = None) -> dict:
        setup = dict(self.driver.setup)
        if exposure_us is not None:
            setup["exposure_us"] = _clamp_exposure(exposure_us)
        if scale is not None:
            setup["exposure_us"] = _clamp_exposure(float(setup["exposure_us"]) * float(scale))
        if auto:
            setup["exposure_auto"] = str(auto)
        return self.driver.configure(**setup)

    def auto_expose(self, target: float = 118.0, max_rounds: int = 5) -> dict:
        """按目标平均灰度迭代曝光：曝光按亮度比例逼近，返回每轮均值与曝光

        两个提前收束条件：
          * 收敛带 ±6% 内视为达标；
          * 连续两轮均值变化 < 0.5 灰度视为「亮度不随曝光变化」（仿真模式为确定性帧、
            或相机自身处于自动曝光/增益闭环），此时保留原曝光，不做无效拉高。
        """
        target = max(1.0, min(254.0, float(target)))
        original = float(self.driver.setup["exposure_us"])
        trace = []
        converged = responsive = False
        prev_mean = None
        for i in range(max(1, min(10, int(max_rounds)))):
            stats = self.driver.stats()["value"]
            mean = float(stats.get("mean") or 0.0)
            cur = float(self.driver.setup["exposure_us"])
            trace.append({"round": i + 1, "exposure_us": round(cur, 1), "mean": round(mean, 2)})
            if prev_mean is not None and abs(mean - prev_mean) < 0.5:
                responsive = False
                break
            prev_mean = mean
            responsive = True
            if mean <= 0.5 or mean >= 254.5:
                break
            ratio = target / mean
            if abs(ratio - 1.0) <= 0.06:
                converged = True
                break
            # 单轮限幅，避免真机高倍率来回震荡
            ratio = max(0.35, min(2.8, ratio))
            self.exposure(exposure_us=_clamp_exposure(cur * ratio))
        if not responsive:
            self.exposure(exposure_us=original)     # 无效调整，回到原曝光
        elif not self.driver.simulate:
            self.exposure(auto="Continuous")         # 真机交给相机内部闭环
        final = self.driver.stats()["value"]
        trace.append({"round": len(trace) + 1, "exposure_us": round(float(self.driver.setup["exposure_us"]), 1),
                      "mean": round(float(final.get("mean") or 0.0), 2)})
        return {"target": target, "trace": trace, "converged": converged, "responsive": responsive,
                "final_exposure_us": round(float(self.driver.setup["exposure_us"]), 1),
                "final_mean": round(float(final.get("mean") or 0.0), 2)}

    # ---------------- 采集与落盘 ----------------

    def grab(self, note: str = "") -> dict:
        root = _frame_root()
        folder = root / time.strftime("%Y%m%d")
        folder.mkdir(parents=True, exist_ok=True)
        self.frame_seq += 1
        name = f"cam_{time.strftime('%H%M%S')}_{self.frame_seq:04d}.png"
        target = folder / name
        value = self.driver.snap(path=str(target), format="png")["value"]
        rel = _rel(target)
        frame = {
            "name": name,
            "path": rel,
            "url": _frame_url(rel),
            "bytes": int(value.get("bytes") or 0),
            "width": int(value.get("width") or 0),
            "height": int(value.get("height") or 0),
            "pixel_format": str(value.get("pixel_format") or ""),
            "exposure_us": round(float(self.driver.setup["exposure_us"]), 1),
            "gain_db": self.driver.setup.get("gain_db"),
            "sha256": value.get("sha256", ""),
            "stats": value.get("stats") or {},
            "at": _now(),
            "note": str(note or "")[:60],
            "source": "simulate" if self.driver.simulate else "instrument",
        }
        self.last_frame = frame
        return frame

    def sequence(self, count: int = 10, interval_ms: float = 200.0, prefix: str = "seq") -> dict:
        root = _frame_root()
        folder = root / time.strftime("%Y%m%d") / _safe_name(prefix, "seq")
        folder.mkdir(parents=True, exist_ok=True)
        n = max(1, min(200, int(count or 1)))
        res = self.driver.sequence(count=n, interval_ms=max(0.0, float(interval_ms or 0.0)),
                                   save_dir=str(folder), prefix=_safe_name(prefix, "seq").rsplit(".png", 1)[0])
        value = res["value"]
        frames = []
        for item in value.get("saved", [])[:n]:
            p = Path(item)
            rel = _rel(p)
            frames.append({"name": p.name, "path": rel, "url": _frame_url(rel),
                           "bytes": p.stat().st_size if p.is_file() else 0})
        out = {
            "count": value.get("count"), "saved": len(frames), "elapsed_ms": value.get("elapsed_ms"),
            "fps": value.get("fps"), "mean_of_means": value.get("mean_of_means"),
            "mean_spread": value.get("mean_spread"), "frames": frames,
            "interval_ms": round(float(interval_ms or 0.0), 1),
        }
        if frames:
            self.last_frame = {"name": frames[-1]["name"], "path": frames[-1]["path"],
                               "url": frames[-1]["url"], "at": _now(),
                               "width": 0, "height": 0, "bytes": frames[-1]["bytes"],
                               "stats": {"mean": value.get("mean_of_means")},
                               "source": "simulate" if self.driver.simulate else "instrument"}
        return out

    # ---------------- 状态 ----------------

    def state(self) -> dict:
        base = self.driver.state()
        return {
            "key": self.key,
            "mode": self.mode,
            "alias": self.alias,
            "serial": self.serial,
            "idn": base.get("idn", ""),
            "opened": bool(base.get("connected")),
            "opened_at": self.opened_at,
            "setup": dict(base.get("setup") or {}),
            "device": dict(base.get("device") or {}),
            "fps_frames": base.get("frames", 0),
            "last_stats": dict(base.get("last_stats") or {}),
            "warnings": list(base.get("warnings") or []),
            "pylon": dict(base.get("pylon") or {}),
            "last_frame": self.last_frame,
            "last_error": self.last_error,
            "root": str(_frame_root()),
        }


# ==================== 请求模型 ====================


class ConnectBody(BaseModel):
    mode: str = "simulate"
    serial: str = ""


class KeyBody(BaseModel):
    key: str = ""


class ParamBody(BaseModel):
    key: str = ""
    params: dict = {}
    reset: bool = False


class ExposureBody(BaseModel):
    key: str = ""
    exposure_us: Optional[float] = None
    scale: Optional[float] = None
    auto: str = ""


class AutoExposureBody(BaseModel):
    key: str = ""
    target_mean: float = 118.0
    max_rounds: int = 5


class GrabBody(BaseModel):
    key: str = ""
    note: str = ""


class SequenceBody(BaseModel):
    key: str = ""
    count: int = 10
    interval_ms: float = 200.0
    prefix: str = "seq"


class SaveBody(BaseModel):
    key: str = ""
    path: str = ""
    name: str = ""
    subdir: str = "exports"


# ==================== 路由 ====================


def _pick_session(key: str) -> Optional[CameraSession]:
    if key:
        return _SESSIONS.get(key)
    if len(_SESSIONS) == 1:
        return next(iter(_SESSIONS.values()))
    return None


def _err(message: str, **extra) -> dict:
    return {"success": False, "message": message, **extra}


def register_routes(app: FastAPI, base_dir: Path) -> None:
    global FRAME_ROOT
    FRAME_ROOT = Path(base_dir) / "logs" / "camera_frames"
    FRAME_ROOT.mkdir(parents=True, exist_ok=True)

    # ---------- 能力清单 ----------

    @app.get("/api/tools/camera/info")
    def camera_info():
        return {
            "success": True,
            "driver": "basler-pylon",
            "label": "工业相机 · Basler pylon 8.0.0",
            "vendor": "Basler",
            "model": "acA1920-40gm",
            "pylon_baseline": "8.0.0",
            "modes": [{"value": "simulate", "label": "仿真（无需硬件）"},
                      {"value": "real", "label": "真实相机（需 pypylon）"}],
            "param_groups": PARAM_GROUPS,
            "exposure_presets": EXPOSURE_PRESETS,
            "patterns": PATTERNS,
            "pixel_formats": PIXEL_FORMATS,
            "frame_root": str(FRAME_ROOT),
            "exposure_range": [EXPOSURE_MIN_US, EXPOSURE_MAX_US],
        }

    @app.get("/api/tools/camera/sessions")
    def camera_sessions():
        return {"success": True, "count": len(_SESSIONS),
                "items": [{"key": s.key, "mode": s.mode, "idn": s.state()["idn"],
                           "opened": s.state()["opened"]} for s in _SESSIONS.values()]}

    # ---------- 连接 ----------

    @app.post("/api/tools/camera/connect")
    def camera_connect(req: ConnectBody):
        mode = "real" if str(req.mode).lower() == "real" else "simulate"
        key = _session_key(mode, req.serial)
        with _LOCK:
            old = _SESSIONS.pop(key, None)
            if old:
                try:
                    old.close()
                except Exception:
                    pass
            sess = CameraSession(mode=mode, serial=req.serial)
            try:
                info = sess.open()
            except Exception as e:
                err = str(e) or e.__class__.__name__
                return _err(f"{'真实相机' if mode == 'real' else '仿真相机'}连接失败: {err}",
                            error=err, hint="未装 pypylon 或未识别到相机时可切「仿真」模式演示")
            _SESSIONS[key] = sess
            return {
                "success": True,
                "key": key,
                "message": ("已连接真实相机" if mode == "real" else "已进入仿真相机模式（确定性合成帧，无需硬件）"),
                "idn": info.get("idn", ""),
                "state": sess.state(),
            }

    @app.post("/api/tools/camera/disconnect")
    def camera_disconnect(req: KeyBody):
        with _LOCK:
            sess = _pick_session(req.key)
            if not sess:
                return _err("当前没有已连接的相机会话")
            _SESSIONS.pop(sess.key, None)
            try:
                info = sess.close()
            except Exception as e:
                return _err(f"断开时出错: {e}")
            return {"success": True, "message": "已断开相机", "closed": info}

    @app.get("/api/tools/camera/state")
    def camera_state(key: str = ""):
        sess = _pick_session(key)
        if not sess:
            return {"success": True, "connected": False, "state": None}
        return {"success": True, "connected": True, "state": sess.state()}

    # ---------- 参数设置 ----------

    @app.post("/api/tools/camera/params")
    def camera_params(req: ParamBody):
        sess = _pick_session(req.key)
        if not sess:
            return _err("尚未连接相机，请先连接")
        try:
            if req.reset:
                sess.driver.reset()
                return {"success": True, "message": "已恢复出厂默认参数", "state": sess.state()}
            if not req.params:
                return _err("没有需要应用的参数")
            res = sess.configure(req.params)
            changed = res.get("changed") or {}
            warnings = res.get("warnings") or []
            msg = f"已应用 {len(changed)} 项参数" if changed else "参数未变化"
            if warnings:
                msg += f"；{len(warnings)} 项相机不支持并已跳过"
            return {"success": True, "message": msg, "changed": changed,
                    "warnings": warnings, "state": sess.state()}
        except DriverError as e:
            return _err(str(e), detail=getattr(e, "detail", ""), state=sess.state())
        except Exception as e:
            return _err(f"参数设置失败: {e}")

    # ---------- 曝光控制 ----------

    @app.post("/api/tools/camera/exposure")
    def camera_exposure(req: ExposureBody):
        sess = _pick_session(req.key)
        if not sess:
            return _err("尚未连接相机，请先连接")
        try:
            if req.exposure_us is None and req.scale is None and not req.auto:
                return _err("请给出曝光时间、曝光倍数或自动曝光档位")
            sess.exposure(exposure_us=req.exposure_us, scale=req.scale, auto=req.auto or None)
            cur = float(sess.driver.setup["exposure_us"])
            msg = f"曝光时间已设为 {cur:.0f} µs"
            if req.auto:
                msg += f"，自动曝光 {req.auto}"
            return {"success": True, "message": msg, "exposure_us": cur, "state": sess.state()}
        except DriverError as e:
            return _err(str(e), state=sess.state())
        except Exception as e:
            return _err(f"曝光设置失败: {e}")

    @app.post("/api/tools/camera/auto-exposure")
    def camera_auto_exposure(req: AutoExposureBody):
        sess = _pick_session(req.key)
        if not sess:
            return _err("尚未连接相机，请先连接")
        try:
            out = sess.auto_expose(target=req.target_mean, max_rounds=req.max_rounds)
            if out["responsive"] is False:
                msg = (f"帧亮度不随曝光变化（当前均值 {out['final_mean']:.0f}，目标 {out['target']:.0f}）："
                       f"仿真模式为确定性帧，或相机处于自动曝光/自动增益闭环；已保留原曝光 "
                       f"{out['final_exposure_us']:.0f} µs")
            else:
                msg = (f"自动曝光完成：目标灰度 {out['target']:.0f}，实际 {out['final_mean']:.0f}，"
                       f"曝光 {out['final_exposure_us']:.0f} µs（{len(out['trace'])} 轮）")
                if not out["converged"]:
                    msg += "；未达到收敛带，可调整目标灰度或采集几何后重试"
            return {"success": True, "message": msg, "result": out, "state": sess.state()}
        except DriverError as e:
            return _err(str(e), state=sess.state())
        except Exception as e:
            return _err(f"自动曝光失败: {e}")

    # ---------- 采集与保存 ----------

    @app.post("/api/tools/camera/grab")
    def camera_grab(req: GrabBody):
        sess = _pick_session(req.key)
        if not sess:
            return _err("尚未连接相机，请先连接")
        try:
            frame = sess.grab(note=req.note)
            return {"success": True, "message": f"已抓取 1 帧（{frame['width']}×{frame['height']}）",
                    "frame": frame, "state": sess.state()}
        except DriverError as e:
            return _err(f"抓取失败: {e}", detail=getattr(e, "detail", ""))
        except Exception as e:
            return _err(f"抓取失败: {e}")

    @app.post("/api/tools/camera/sequence")
    def camera_sequence(req: SequenceBody):
        sess = _pick_session(req.key)
        if not sess:
            return _err("尚未连接相机，请先连接")
        try:
            out = sess.sequence(count=req.count, interval_ms=req.interval_ms, prefix=req.prefix)
            msg = (f"连拍完成：{out['saved']}/{out['count']} 帧落盘，"
                   f"耗时 {out['elapsed_ms']} ms，平均 {out['fps']} fps")
            if out.get("mean_spread") is not None:
                msg += f"，帧间亮度极差 {out['mean_spread']}"
            return {"success": True, "message": msg, "result": out, "state": sess.state()}
        except DriverError as e:
            return _err(f"连拍失败: {e}", detail=getattr(e, "detail", ""))
        except Exception as e:
            return _err(f"连拍失败: {e}")

    @app.get("/api/tools/camera/frames")
    def camera_frames(limit: int = 24):
        root = _frame_root()
        items = []
        try:
            files = sorted(root.rglob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:
            files = []
        for p in files[: max(1, min(200, int(limit or 24)))]:
            try:
                rel = _rel(p)
                st = p.stat()
                items.append({"name": p.name, "path": rel, "url": _frame_url(rel),
                              "bytes": st.st_size, "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                                                          time.localtime(st.st_mtime))})
            except Exception:
                continue
        total = len(files)
        return {"success": True, "count": len(items), "total": total,
                "root": str(root), "items": items}

    @app.get("/api/tools/camera/frame")
    def camera_frame(path: str = "", download: int = 0):
        try:
            target = _frame_path(path)
        except ValueError as e:
            return JSONResponse({"success": False, "message": str(e)}, status_code=400)
        headers = {"Cache-Control": "no-store"}
        if int(download or 0):
            return FileResponse(str(target), media_type="image/png", filename=target.name, headers=headers)
        return FileResponse(str(target), media_type="image/png", headers=headers)

    @app.post("/api/tools/camera/save")
    def camera_save(req: SaveBody):
        sess = _pick_session(req.key)
        source_rel = req.path or (sess.last_frame or {}).get("path") if sess else req.path
        if not source_rel:
            return _err("没有可导出的帧，请先抓取一帧")
        try:
            src = _frame_path(str(source_rel))
        except ValueError as e:
            return _err(str(e))
        subdir = re.sub(r"[^0-9A-Za-z_\-]", "_", str(req.subdir or "exports"))[:32] or "exports"
        default_name = f"{src.stem}_{time.strftime('%Y%m%d_%H%M%S')}.png"
        name = _safe_name(req.name, default_name)
        folder = _frame_root() / subdir
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        try:
            target.write_bytes(src.read_bytes())   # 逐字节复制，不经 shutil
        except Exception as e:
            return _err(f"导出失败: {e}")
        rel = _rel(target)
        return {"success": True, "message": f"已导出到 {subdir}/{name}",
                "frame": {"name": name, "path": rel, "url": _frame_url(rel),
                          "bytes": target.stat().st_size,
                          "download": _frame_url(rel) + "&download=1"}}
