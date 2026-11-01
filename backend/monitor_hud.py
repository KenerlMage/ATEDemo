# -*- coding: utf-8 -*-
"""常驻置顶监视窗口（产线方案 B：Edge 独立窗口 + Win32 置顶）

背景
    实时监视页本身是网页，浏览器不提供「让我这个窗口置顶」的接口。产线上需要一块始终
    浮在最上层的监视窗，因此用独立窗口壳承载监视页，再由 Win32 置顶：

      · 窗口壳：msedge --app=<url> --user-data-dir=<独立 profile>
        独立 profile 是关键：否则 Edge 会复用已在运行的实例，我们既拿不到新窗口，
        也无法控制它的位置与生死。
      · 置顶：SetWindowPos(HWND_TOPMOST)，切换时用 HWND_NOTOPMOST；
        一律带 SWP_NOACTIVATE，产线上操作时不允许抢焦点。
      · 句柄定位：不能按 Popen 的 pid 找窗口（Edge 启动器进程立刻退出，真正的浏览器
        进程是它的子进程）。这里用「启动前后可见窗口差集 + 标题令牌」定位；
        前端按令牌改写 document.title，前端未改也可靠差集兜住。

约束
    · 只用 HWND_TOPMOST，不用最顶层（screen-saver 级），避免压住系统弹窗与急停确认框；
    · 位置与尺寸记在 <base>/.ate_rt/monitor_hud.json，下次按上次位置打开；
    · 单实例：命名互斥 Global\\ATE_MONITOR_HUD，重复拉起时接管已有窗口；
    · 热键 Ctrl+Alt+T 切换置顶、Ctrl+Alt+H 隐藏/显示，现场随时能让开；
    · 非 Windows 或未装 Edge 时明确返回不可用，不静默失败。
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request

IS_WIN = sys.platform == "win32"

TITLE_PREFIX = "ATE-HUD-"
DEFAULT_WIDTH, DEFAULT_HEIGHT = 560, 380
DEFAULT_MARGIN, DEFAULT_TOP = 24, 24
DEFAULT_AUTO_CLOSE_S = 8.0
MUTEX_NAME = r"Global\ATE_MONITOR_HUD"
STATE_NAME = "monitor_hud.json"
EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge Beta\Application\msedge.exe",
)

# ---- Win32 常量 ----
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
GWL_EXSTYLE, GWL_STYLE = -20, -16
WS_EX_TOPMOST, WS_CAPTION = 0x00000008, 0x00C00000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
WM_CLOSE, WM_HOTKEY, WM_APP_QUIT = 0x0010, 0x0312, 0x8000 + 1
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
ERROR_ALREADY_EXISTS = 183
PROCESS_TERMINATE = 0x0001
SPI_GETWORKAREA = 0x0030
HOTKEY_TOPMOST, HOTKEY_VISIBLE = 0xA1, 0xA2


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                ("wParam", ctypes.c_void_p), ("lParam", ctypes.c_void_p),
                ("time", ctypes.c_uint), ("pt", _POINT), ("lPrivate", ctypes.c_uint)]


def _w32() -> Any:
    return ctypes.WinDLL("user32", use_last_error=True)


def _k32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


# ---------------------------------------------------------------- 窗口工具


def _title_of(u32: Any, hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    u32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 512)
    return buf.value


def _visible_windows() -> dict:
    """当前所有可见且带标题栏的顶层窗口：{hwnd: title}"""
    if not IS_WIN:
        return {}
    u32 = _w32()
    out: dict = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _lparam):
        ptr = ctypes.c_void_p(hwnd)
        if u32.IsWindowVisible(ptr) and (u32.GetWindowLongW(ptr, GWL_STYLE) & WS_CAPTION):
            title = _title_of(u32, hwnd)
            if title:
                out[int(hwnd)] = title
        return True

    u32.EnumWindows(cb, None)
    return out


def _window_rect(u32: Any, hwnd: int) -> dict:
    rc = _RECT()
    u32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rc))
    return {"x": rc.left, "y": rc.top, "w": rc.right - rc.left, "h": rc.bottom - rc.top}


def _screen_work_area(u32: Any) -> tuple:
    rc = _RECT()
    if not u32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rc), 0):
        return (0, 0, 1920, 1080)
    return (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)


def _owner_pid(u32: Any, hwnd: int) -> int:
    pid = ctypes.c_ulong(0)
    u32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    return int(pid.value)


def _edge_path() -> str:
    for cand in EDGE_CANDIDATES:
        if Path(cand).exists():
            return cand
    for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", "")):
        if not base:
            continue
        cand = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if cand.exists():
            return str(cand)
    return ""


# ---------------------------------------------------------------- 控制器


class HudController:
    """置顶监视窗的进程与窗口句柄管理（进程内单实例）"""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        self.state_path = self.base_dir / ".ate_rt" / STATE_NAME
        self.profile_dir = self.base_dir / ".ate_rt" / "hud_profile"
        self._lock = threading.RLock()
        self._proc: Any = None
        self._hwnd = 0
        self._token = ""
        self._url = ""
        self._started_at = 0.0
        self._mutex: Any = None
        self._hk_stop = threading.Event()
        self._hk_thread: Optional[threading.Thread] = None
        self._hk_tid = 0
        self._timer: Optional[threading.Timer] = None
        self._last_error = ""
        self._last_task = ""

    # ---------------- 状态文件 ----------------

    def load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_state(self, **patch) -> dict:
        state = self.load_state()
        state.update({k: v for k, v in patch.items() if v is not None})
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        except Exception:
            pass
        return state

    # ---------------- 环境自检 ----------------

    def available(self) -> dict:
        if not IS_WIN:
            return {"ok": False, "reason": "置顶窗仅支持 Windows", "edge": "", "win32": False}
        edge = _edge_path()
        if not edge:
            return {"ok": False, "reason": "未找到 Microsoft Edge，可改用 pywebview 壳",
                    "edge": "", "win32": True}
        return {"ok": True, "reason": "", "edge": edge, "win32": True}

    # ---------------- 互斥 ----------------

    def _acquire_mutex(self) -> bool:
        """True：本进程是首个实例；False：已有实例持有互斥"""
        if not IS_WIN:
            return True
        if self._mutex:
            return True
        k32 = _k32()
        handle = k32.CreateMutexW(None, False, MUTEX_NAME)
        if k32.GetLastError() == ERROR_ALREADY_EXISTS:
            if handle:
                k32.CloseHandle(ctypes.c_void_p(handle))
            return False
        self._mutex = handle
        return True

    def _release_mutex(self) -> None:
        if self._mutex:
            try:
                _k32().CloseHandle(ctypes.c_void_p(self._mutex))
            except Exception:
                pass
            self._mutex = None

    # ---------------- 窗口基础操作 ----------------

    def alive(self) -> bool:
        if not (IS_WIN and self._hwnd):
            return False
        return bool(_w32().IsWindow(ctypes.c_void_p(self._hwnd)))

    def topmost(self) -> bool:
        if not self.alive():
            return False
        ex = _w32().GetWindowLongW(ctypes.c_void_p(self._hwnd), GWL_EXSTYLE)
        return bool(ex & WS_EX_TOPMOST)

    def visible(self) -> bool:
        if not self.alive():
            return False
        return bool(_w32().IsWindowVisible(ctypes.c_void_p(self._hwnd)))

    def set_topmost(self, on: bool = True) -> dict:
        with self._lock:
            if not self.alive():
                return {"success": False, "message": "置顶窗未在运行", "topmost": False}
            u32 = _w32()
            target = HWND_TOPMOST if on else HWND_NOTOPMOST
            ok = u32.SetWindowPos(ctypes.c_void_p(self._hwnd), ctypes.c_void_p(target), 0, 0, 0, 0,
                                  SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            self.save_state(topmost=bool(on))
            return {"success": bool(ok), "message": "已置顶" if on else "已取消置顶",
                    "topmost": self.topmost()}

    def set_visible(self, on: bool = True) -> dict:
        with self._lock:
            if not self.alive():
                return {"success": False, "message": "置顶窗未在运行", "visible": False}
            u32 = _w32()
            u32.ShowWindow(ctypes.c_void_p(self._hwnd), SW_SHOWNOACTIVATE if on else SW_HIDE)
            return {"success": True, "message": "已显示" if on else "已隐藏",
                    "visible": self.visible()}

    def apply_rect(self, x: int, y: int, w: int, h: int) -> dict:
        with self._lock:
            if not self.alive():
                return {"success": False, "message": "置顶窗未在运行"}
            u32 = _w32()
            u32.SetWindowPos(ctypes.c_void_p(self._hwnd), ctypes.c_void_p(0), int(x), int(y),
                             int(w), int(h), SWP_NOZORDER | SWP_NOACTIVATE)
            rect = _window_rect(u32, self._hwnd)
            self.save_state(x=rect["x"], y=rect["y"], w=rect["w"], h=rect["h"])   # 记住位置
            return {"success": True, "rect": rect}

    def _default_rect(self) -> dict:
        u32 = _w32()
        left, top, sw, sh = _screen_work_area(u32)
        w = min(DEFAULT_WIDTH, max(360, sw // 3))
        h = min(DEFAULT_HEIGHT, max(260, sh // 3))
        return {"x": left + sw - w - DEFAULT_MARGIN, "y": top + DEFAULT_TOP, "w": w, "h": h}

    # ---------------- 启动 / 关闭 ----------------

    def launch(self, task_id: str = "", base_url: str = "", force: bool = False,
               topmost: bool = True) -> dict:
        if not IS_WIN:
            return {"success": False, "message": "置顶窗仅支持 Windows", "mode": ""}
        edge = _edge_path()
        if not edge:
            return {"success": False, "message": "未找到 Microsoft Edge，无法拉起置顶窗", "mode": ""}

        with self._lock:
            if self.alive() and not force:
                self.set_visible(True)
                if topmost:
                    self.set_topmost(True)
                self._last_task = task_id or self._last_task
                out = self.status()
                return {"success": True, "reused": True, "message": "置顶窗已在运行，已切回前台",
                        **out}

            if self.alive():
                self.stop()

            first = self._acquire_mutex()
            if not first:
                adopted = self._adopt_existing()
                if adopted:
                    self._last_task = task_id or self._last_task
                    return {"success": True, "reused": True,
                            "message": "已接管运行中的置顶窗", **self.status()}

            state = self.load_state() or {}
            try:
                rect = {"x": int(state["x"]), "y": int(state["y"]),
                        "w": int(state["w"]), "h": int(state["h"])}
            except Exception:
                rect = self._default_rect()
            if rect["w"] <= 0 or rect["h"] <= 0:
                rect = self._default_rect()
            want_top = bool(state.get("topmost", topmost))

            token = TITLE_PREFIX + uuid.uuid4().hex[:8]
            base = (base_url or os.environ.get("ATE_HUD_URL") or "http://127.0.0.1:8000").rstrip("/")
            query = f"hud={token}" + (f"&task={task_id}" if task_id else "")
            url = f"{base}/monitor?{query}"

            before = _visible_windows()
            args = [
                edge,
                f"--app={url}",
                f"--user-data-dir={self.profile_dir}",
                f"--window-size={rect['w']},{rect['h']}",
                f"--window-position={rect['x']},{rect['y']}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=Translate,msEdgeIdentityFeature",
                "--app-auto-launched",
            ]
            try:
                proc = subprocess.Popen(args)
            except Exception as exc:
                self._last_error = f"拉起窗口失败：{exc}"
                self._release_mutex()
                return {"success": False, "message": self._last_error, "mode": ""}

            hwnd, _title = self._wait_window(before, token, timeout=20.0)
            if not hwnd:
                self._last_error = "窗口已启动但未捕获到句柄，请检查 Edge 是否被组策略拦截"
                try:
                    proc.kill()
                except Exception:
                    pass
                self._release_mutex()
                return {"success": False, "message": self._last_error, "mode": ""}

            self._proc, self._hwnd, self._token = proc, int(hwnd), token
            self._url, self._started_at = url, time.time()
            self._last_error = ""
            self._last_task = task_id
            self.apply_rect(rect["x"], rect["y"], rect["w"], rect["h"])
            self.set_topmost(want_top)
            self._start_hotkeys()
            self.save_state(x=rect["x"], y=rect["y"], w=rect["w"], h=rect["h"], topmost=want_top)
            return {"success": True, "reused": False, "message": "置顶监视窗已启动",
                    "mode": "edge-app", **self.status()}

    def _adopt_existing(self) -> bool:
        """已有实例持锁时，按标题令牌接管它的窗口，避免重复开窗"""
        for hwnd, title in _visible_windows().items():
            if title.startswith(TITLE_PREFIX):
                self._hwnd = int(hwnd)
                self._token = title.split()[0]
                self._started_at = time.time()
                self._start_hotkeys()
                return True
        return False

    def _wait_window(self, before: dict, token: str, timeout: float = 20.0) -> tuple:
        """启动前后窗口差集 + 标题令牌定位句柄"""
        start = time.time()
        end = start + timeout
        fallback = 0
        while time.time() < end:
            fresh = {h: t for h, t in _visible_windows().items() if h not in before}
            for hwnd, title in fresh.items():
                if token in title:
                    return hwnd, title
            if not fallback:
                for hwnd, title in fresh.items():
                    # 独立 profile 下新增窗口只可能来自本次拉起，排除系统壳窗口即可
                    if "Edge" not in title and "Microsoft" not in title:
                        fallback = hwnd
                        break
            if fallback and time.time() - start > 6.0:
                # 兜底窗口已出现 6 s 仍无令牌标题：前端未改写标题时走这条路
                return fallback, ""
            time.sleep(0.35)
        return 0, ""

    def stop(self, keep_state: bool = True) -> dict:
        with self._lock:
            self._cancel_timer()
            if not IS_WIN or not self._hwnd:
                return {"success": True, "message": "置顶窗未在运行", **self.status()}
            if self.alive():
                u32 = _w32()
                rect = _window_rect(u32, self._hwnd)
                pid = _owner_pid(u32, self._hwnd)
                if keep_state:
                    self.save_state(x=rect["x"], y=rect["y"], w=rect["w"], h=rect["h"],
                                    topmost=self.topmost())
                u32.PostMessageW(ctypes.c_void_p(self._hwnd), WM_CLOSE, 0, 0)
                for _ in range(24):
                    if not self.alive():
                        break
                    time.sleep(0.25)
                if self.alive() and pid:
                    # 关窗未响应（页面拦截关闭等）时按进程结束兜底
                    try:
                        k32 = _k32()
                        handle = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
                        if handle:
                            k32.TerminateProcess(ctypes.c_void_p(handle), 0)
                            k32.CloseHandle(ctypes.c_void_p(handle))
                    except Exception:
                        pass
            self._stop_hotkeys()
            self._release_mutex()
            self._hwnd = 0
            self._proc = None
            self._token = ""
            self._url = ""
            self._started_at = 0.0
            return {"success": True, "message": "置顶窗已关闭", **self.status()}

    # ---------------- 热键 ----------------

    def _start_hotkeys(self) -> None:
        if not IS_WIN or self._hk_thread:
            return
        self._hk_stop.clear()

        def loop():
            u32, k32 = _w32(), _k32()
            self._hk_tid = int(k32.GetCurrentThreadId())
            reg = []
            for hk_id, vk in ((HOTKEY_TOPMOST, ord("T")), (HOTKEY_VISIBLE, ord("H"))):
                if u32.RegisterHotKey(None, hk_id, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, vk):
                    reg.append(hk_id)
            msg = _MSG()
            try:
                while not self._hk_stop.is_set():
                    got = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if got in (0, -1):
                        break
                    if msg.message == WM_HOTKEY:
                        if int(msg.wParam or 0) == HOTKEY_TOPMOST:
                            self.set_topmost(not self.topmost())
                        elif int(msg.wParam or 0) == HOTKEY_VISIBLE:
                            self.set_visible(not self.visible())
                    elif msg.message == WM_APP_QUIT:
                        break
            finally:
                for hk_id in reg:
                    u32.UnregisterHotKey(None, hk_id)
                self._hk_tid = 0

        self._hk_thread = threading.Thread(target=loop, name="ate-hud-hotkey", daemon=True)
        self._hk_thread.start()

    def _stop_hotkeys(self) -> None:
        if not self._hk_thread:
            return
        self._hk_stop.set()
        try:
            if self._hk_tid:
                _w32().PostThreadMessageW(self._hk_tid, WM_APP_QUIT, 0, 0)
        except Exception:
            pass
        self._hk_thread.join(timeout=1.5)
        self._hk_thread = None

    # ---------------- 自动收尾 ----------------

    def _cancel_timer(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def schedule_stop(self, delay: Optional[float] = None) -> None:
        """执行结束后延时关闭；delay<=0 表示不关闭"""
        if delay is None:
            try:
                delay = float(os.environ.get("ATE_HUD_AUTOCLOSE", DEFAULT_AUTO_CLOSE_S))
            except Exception:
                delay = DEFAULT_AUTO_CLOSE_S
        if delay <= 0:
            return
        with self._lock:
            self._cancel_timer()
            if not self.alive():
                return
            self._timer = threading.Timer(float(delay), lambda: self.stop())
            self._timer.daemon = True
            self._timer.start()

    # ---------------- 汇总 ----------------

    def status(self) -> dict:
        info: dict = {
            "running": self.alive(),
            "mode": "edge-app" if self.alive() else "",
            "url": self._url,
            "token": self._token,
            "task_id": self._last_task,
            "available": self.available(),
            "state": self.load_state(),
            "hotkeys": {"topmost": "Ctrl+Alt+T", "visible": "Ctrl+Alt+H"},
        }
        if info["running"]:
            u32 = _w32()
            info.update({
                "hwnd": self._hwnd,
                "pid": _owner_pid(u32, self._hwnd),
                "title": _title_of(u32, self._hwnd),
                "topmost": self.topmost(),
                "visible": self.visible(),
                "rect": _window_rect(u32, self._hwnd),
                "uptime_s": round(time.time() - self._started_at, 1) if self._started_at else 0.0,
            })
        else:
            info.update({"hwnd": 0, "pid": 0, "title": "", "topmost": False,
                         "visible": False, "rect": {}, "uptime_s": 0.0})
        if self._last_error:
            info["error"] = self._last_error
        return info


# ---------------------------------------------------------------- 单例与执行钩子

_HUD: Optional[HudController] = None


def controller(base_dir: Path | None = None) -> HudController:
    global _HUD
    if _HUD is None or (base_dir is not None and Path(base_dir) != _HUD.base_dir):
        _HUD = HudController(base_dir)
    return _HUD


def auto_start(task_id: str = "", base_url: str = "") -> Optional[dict]:
    """执行开始时自动拉起（ATE_HUD_AUTO=0 关闭；任何异常都不影响执行）"""
    if os.environ.get("ATE_HUD_AUTO", "1") in ("0", "false", "False", "off"):
        return None
    if not IS_WIN or not _edge_path():
        return None
    try:
        return controller().launch(task_id=task_id, base_url=base_url)
    except Exception:
        return None


def auto_stop() -> None:
    """执行结束后按 ATE_HUD_AUTOCLOSE（默认 8 s）延时关闭"""
    try:
        if _HUD is not None:
            _HUD.schedule_stop()
    except Exception:
        pass


# ---------------------------------------------------------------- 路由


def register_routes(app: FastAPI, base_dir: Path | None = None) -> None:
    ctl = controller(base_dir)

    async def _json(request: Request) -> dict:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _base_url(request: Request) -> str:
        try:
            return str(request.base_url).rstrip("/")
        except Exception:
            return ""

    def _guard(fn, *args, **kwargs):
        """路由统一兜底：置顶窗的任何异常都不该变成 500，也不该影响执行链路"""
        try:
            out = fn(*args, **kwargs)
            return out if isinstance(out, dict) else {"success": True}
        except Exception as exc:                     # noqa: BLE001
            return {"success": False, "message": f"置顶窗操作失败：{type(exc).__name__}: {exc}"}

    @app.get("/api/monitor/hud/status")
    def hud_status():
        try:
            return {"success": True, **ctl.status()}
        except Exception as exc:                     # noqa: BLE001
            return {"success": False, "message": f"读取状态失败：{exc}", "running": False}

    @app.post("/api/monitor/hud/start")
    async def hud_start(request: Request):
        payload = await _json(request)
        res = _guard(ctl.launch, task_id=str(payload.get("task_id") or ""),
                     base_url=str(payload.get("base_url") or _base_url(request)),
                     force=bool(payload.get("force")),
                     topmost=bool(payload.get("topmost", True)))
        res.setdefault("success", False)
        return res

    @app.post("/api/monitor/hud/stop")
    def hud_stop():
        return _guard(ctl.stop)

    @app.post("/api/monitor/hud/topmost")
    async def hud_topmost(request: Request):
        payload = await _json(request)
        return _guard(ctl.set_topmost, bool(payload.get("on", True)))

    @app.post("/api/monitor/hud/visible")
    async def hud_visible(request: Request):
        payload = await _json(request)
        return _guard(ctl.set_visible, bool(payload.get("on", True)))

    @app.post("/api/monitor/hud/rect")
    async def hud_rect(request: Request):
        payload = await _json(request)
        try:
            rect = {k: int(payload[k]) for k in ("x", "y", "w", "h")}
            if rect["w"] < 320 or rect["h"] < 200:
                raise ValueError
        except Exception:
            return {"success": False, "message": "rect 需要整数 x / y / w / h，且宽高不小于 320×200"}
        return _guard(ctl.apply_rect, rect["x"], rect["y"], rect["w"], rect["h"])
