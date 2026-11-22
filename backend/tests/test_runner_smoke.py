"""MockRunner 端到端冒烟测试（起真实服务 + 真实 Controller）

验证目标（两系统各自可用）：
  1. Runner 自身可用：健康检查、前端托管、助手货架清单；
  2. Runner → Controller：TPS 清单与详情来自 Controller（source=controller）；
  3. Controller 不可达时：TPS 仍可从本地缓存读取（离线兜底）；
  4. 元数据只读转发可用，写操作被拒绝（moved_to_controller）。

运行：
    python -m pytest tests -q --basetemp=.pytest_tmp
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
RUNNER_ROOT = BACKEND.parent
CONTROLLER_ROOT = RUNNER_ROOT.parent / "MockController"
PY = sys.executable


def pytest_configure(config):
    root = HERE / ".pytest_tmp"
    root.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(root)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_http(url: str, timeout: float = 45) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1.5).status_code < 500:
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


@pytest.fixture(scope="session")
def configurator(tmp_path_factory):
    """先起一个 Controller（独立数据目录），供 Runner 拉取。"""
    if not (CONTROLLER_ROOT / "app.py").is_file():
        pytest.skip("未找到 MockController 工程")
    repo = tmp_path_factory.mktemp("ctrl_repo")
    port = _free_port()
    env = dict(os.environ)
    env["MOCK_CONTROLLER_REPO"] = str(repo)
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(CONTROLLER_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    if not _wait_http(f"{base}/api/v1/health"):
        proc.terminate()
        pytest.skip("Controller 未能启动，跳过联动用例")
    yield {"base": base, "proc": proc}
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def runner(configurator, tmp_path_factory):
    """起 Runner，指向上面的 Controller；缓存与数据库落到临时目录。"""
    cache = tmp_path_factory.mktemp("runner_cache")
    db = tmp_path_factory.mktemp("runner_db") / "ate_test.db"
    port = _free_port()
    env = dict(os.environ)
    env.update(
        {
            "MOCK_CONTROLLER_URL": configurator["base"],
            "ATE_CONTROLLER_URL": configurator["base"],
            "ATE_TPS_CACHE_DIR": str(cache),
            "ATE_METADATA_CACHE_DIR": str(cache / "metadata"),
            "ATE_DB_PATH": str(db),
            "ATE_CONSTANTS_SYNC": "off",
        }
    )
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BACKEND),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    if not _wait_http(f"{base}/api/health"):
        proc.terminate()
        pytest.skip("Runner 未能启动")
    yield {"base": base, "proc": proc}
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


# ------------------------------------------------------------------ 1. Runner 自身

def test_runner_health(runner):
    with httpx.Client(base_url=runner["base"], timeout=10) as cli:
        assert cli.get("/api/health").status_code == 200


def test_runner_serves_frontend(runner):
    with httpx.Client(base_url=runner["base"], timeout=10) as cli:
        resp = cli.get("/")
        assert resp.status_code == 200
        assert "<html" in resp.text.lower()


def test_assistant_catalog_available(runner):
    with httpx.Client(base_url=runner["base"], timeout=30) as cli:
        data = cli.get("/api/assistants/catalog").json()
        assert data["success"] is True
        assert data["source"] in ("controller", "cache", "local")
        assert isinstance(data.get("items"), list)


# ------------------------------------------------------------------ 2. Runner → Controller

def _ensure_tps_on_controller(configurator_base: str) -> str:
    with httpx.Client(base_url=configurator_base, timeout=20) as ctrl:
        tree = ctrl.get("/api/metadata/tree").json()["tree"]
        preset_id = ""
        for product in tree:
            for subsystem in product["subsystems"]:
                if subsystem.get("preset_types"):
                    preset_id = subsystem["preset_types"][0]["id"]
                    break
            if preset_id:
                break
        assert preset_id, "Controller 装备树为空"
        created = ctrl.post("/api/v1/tps/scaffold", json={"preset_id": preset_id, "save": True})
        assert created.status_code == 200, created.text
        return created.json()["tps"]["id"]


def test_tps_list_from_controller(runner, configurator):
    tps_id = _ensure_tps_on_controller(configurator["base"])
    with httpx.Client(base_url=runner["base"], timeout=20) as cli:
        data = cli.get("/api/tps").json()
        assert data["success"] is True
        assert data["source"] == "controller"
        assert tps_id in [t["id"] for t in data["tps"]]

        detail = cli.get(f"/api/tps/{tps_id}").json()
        assert detail["success"] is True
        assert detail["tps"]["id"] == tps_id
        assert detail["source"] in ("controller", "cache")


# ------------------------------------------------------------------ 3. 元数据转发（Controller 在线时）

def test_metadata_forwarded_from_controller(runner):
    with httpx.Client(base_url=runner["base"], timeout=30) as cli:
        data = cli.get("/api/metadata/tree").json()
        assert data["success"] is True
        assert data["source"] == "controller"
        assert data.get("tree"), "元数据树为空"


def test_metadata_write_rejected(runner):
    with httpx.Client(base_url=runner["base"], timeout=10) as cli:
        resp = cli.post("/api/metadata/products", json={"id": "X", "name": "X"})
        body = resp.json()
        assert body.get("success") is False
        assert body.get("code") == "moved_to_controller"


# ------------------------------------------------------------------ 4. 离线兜底（会停掉 Controller，必须最后跑）

def test_tps_cache_fallback_when_controller_down(runner, configurator):
    with httpx.Client(base_url=runner["base"], timeout=20) as cli:
        online = cli.get("/api/tps").json()
        assert online["source"] == "controller"

    if configurator["proc"].poll() is None:
        configurator["proc"].terminate()
        try:
            configurator["proc"].wait(timeout=10)
        except subprocess.TimeoutExpired:
            configurator["proc"].kill()

    with httpx.Client(base_url=runner["base"], timeout=30) as cli:
        offline = cli.get("/api/tps").json()
        assert offline["success"] is True
        assert offline["source"] == "cache", offline
        assert offline["count"] >= 1
        assert offline.get("remote_ok") is False


