# -*- coding: utf-8 -*-
"""ATE Runner 启动器（打包部署用，纯标准库，可被 PyInstaller 打成 exe）

职责：
  1. 定位安装根目录（<root>/app、<root>/web、<root>/runtime）
  2. 检查可写性、创建数据目录（logs/workspace/testresource 补种）
  3. 端口探测（默认 8000，被占用则 8001..8010；若已被 ATE 自己占用则直接开浏览器）
  4. 用内置 runtime\\python.exe 启动 uvicorn（cwd=app，ATE_WEB_DIR=web）
  5. 等 /api/health 就绪 → 打开默认浏览器
  6. 前台运行并转发日志到 logs\\launcher.log；退出时清理子进程

用法：
  runtime\\python.exe app\\ate_launcher.py [--port 8000] [--no-browser] [--host 0.0.0.0]
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------- 路径定位
def find_root() -> Path:
    """安装根目录：PyInstaller 冻结时取 exe 所在目录，否则取脚本上两级。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    here = Path(__file__).resolve()
    for cand in (here.parent.parent, here.parent):
        if (cand / "app").is_dir():
            return cand
    return here.parent


ROOT = find_root()
APP_DIR = ROOT / "app"
WEB_DIR = ROOT / "web"
RUNTIME_DIR = ROOT / "runtime"
LOG_DIR = ROOT / "logs"
LAUNCHER_LOG = LOG_DIR / "launcher.log"


def log(msg: str) -> None:
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LAUNCHER_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 自检
def python_exe() -> str:
    """优先用内置 runtime\\python.exe；找不到则退回当前解释器。"""
    cand = RUNTIME_DIR / "python.exe"
    return str(cand) if cand.is_file() else sys.executable


def preflight() -> list[str]:
    """部署自检，返回错误列表（空 = 通过）。"""
    errs: list[str] = []
    if not (APP_DIR / "main.py").is_file():
        errs.append("缺少后端程序: %s" % (APP_DIR / "main.py"))
    if not (WEB_DIR / "index.html").is_file():
        errs.append("缺少前端页面: %s（web/ 目录未随安装包部署）" % (WEB_DIR / "index.html"))
    if not (RUNTIME_DIR / "python.exe").is_file():
        log("未找到内置 Python 运行时，将使用当前解释器: %s" % sys.executable)
    try:
        probe = ROOT / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as e:
        errs.append("安装目录不可写（%s）: %s；请把 ATE 安装到非系统保护目录，或改用管理员身份运行" % (ROOT, e))
    return errs


def port_busy(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def ate_alive(port: int) -> bool:
    """端口上是不是已经在跑的 ATE 后端。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port, timeout=1.5) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        return str(data.get("service", "")).startswith("ATE")
    except Exception:
        return False


def pick_port(preferred: int) -> tuple[int, bool]:
    """返回 (端口, 是否已有实例在跑)。"""
    if ate_alive(preferred):
        return preferred, True
    if not port_busy(preferred):
        return preferred, False
    for p in range(preferred + 1, preferred + 11):
        if ate_alive(p):
            return p, True
        if not port_busy(p):
            log("端口 %d 被占用，改用 %d" % (preferred, p))
            return p, False
    raise SystemExit("端口 %d..%d 全部被占用，请先关闭占用进程（--port 可指定其它端口）"
                     % (preferred, preferred + 10))


def wait_ready(port: int, timeout: float = 90.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.7)
    return False


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="ATE Runner 启动器")
    ap.add_argument("--port", type=int, default=int(os.environ.get("ATE_PORT", "8000")))
    ap.add_argument("--host", default=os.environ.get("ATE_HOST", "0.0.0.0"))
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log("=" * 62)
    log("ATE Runner 启动   root=%s" % ROOT)

    errs = preflight()
    if errs:
        for e in errs:
            log("[错误] " + e)
        print("\n启动中止，请修复上面的问题后重试。按回车退出...")
        try:
            input()
        except EOFError:
            pass
        return 2

    port, running = pick_port(args.port)
    url = "http://127.0.0.1:%d" % port
    if running:
        log("检测到 ATE 已在 %s 运行，直接打开界面" % url)
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    py = python_exe()
    env = dict(os.environ)
    env["ATE_WEB_DIR"] = str(WEB_DIR)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("PYTHONHOME", None)
    cmd = [py, "-m", "uvicorn", "main:app", "--host", args.host, "--port", str(port), "--log-level", "info"]
    log("启动后端: %s" % " ".join(cmd))
    log("工作目录: %s" % APP_DIR)

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if args.no_browser else 0
    proc = subprocess.Popen(cmd, cwd=str(APP_DIR), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1,
                            creationflags=creationflags)

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            log("  " + line.rstrip())

    threading.Thread(target=pump, daemon=True).start()

    if wait_ready(port):
        log("后端就绪: %s" % url)
        if not args.no_browser:
            webbrowser.open(url)
        print("\n  ATE Runner 已启动 →  %s\n  关闭本窗口即停止服务（或按 Ctrl+C）\n" % url, flush=True)
    else:
        log("[警告] 90 秒内未探到 /api/health，请查看上面的日志")

    try:
        proc.wait()
    except KeyboardInterrupt:
        log("收到中断，正在停止后端…")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    log("已退出，返回码 %s" % proc.returncode)
    return proc.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
