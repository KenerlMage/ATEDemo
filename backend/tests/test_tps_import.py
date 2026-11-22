"""TPS 导入用例（Runner 侧）：Controller 优先 + 离线本地兜底

前置：Runner 后端已实现 /api/tps/import/zip、/api/tps/import、/api/tps/import/capability。
本文件独立起服务，避免与其它用例互相影响。

运行：
    python -m pytest tests/test_tps_import.py -q --basetemp=tests/.pytest_tmp
"""

from __future__ import annotations

import base64
import io
import json
import os
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
RUNNER_ROOT = BACKEND.parent
CONTROLLER_ROOT = RUNNER_ROOT.parent / "MockController"
PY = sys.executable


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait(url: str, timeout: float = 45) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout=1.5).status_code < 500:
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def _tps_zip(tps_id: str, with_manifest: bool = True) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if with_manifest:
            manifest = {
                "schema": "tps.v2",
                "id": tps_id,
                "name": f"导入测试 {tps_id}",
                "version": "1.0.0",
                "project": "导入测试项目",
                "dut": "导入测试 DUT",
                "testconfig": {"TC_A": {"label": "A", "min": 0, "max": 10, "unit": "V"}},
                "device_config": {"psu": {"role": "psu"}},
                "setup": [{"id": "S1", "name": "初始化", "case": "testcase.impl::setup"}],
                "cmd_suit": [{"id": "C1", "name": "用例一", "case": "testcase.impl::main", "checks": {"a": "TC_A"}}],
                "teardown": [{"id": "T1", "name": "终止", "case": "testcase.impl::teardown"}],
            }
            zf.writestr("tps.json", json.dumps(manifest, ensure_ascii=False))
            zf.writestr("testcase/impl.py", "def main(ctx, **params):\n    return {}\n")
        else:
            zf.writestr("readme.txt", "no manifest here")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    repo = tmp_path_factory.mktemp("ctrl_repo")
    cache = tmp_path_factory.mktemp("cache")
    tests_dir = tmp_path_factory.mktemp("tps")

    ctrl_port = _free_port()
    ctrl_env = dict(os.environ)
    ctrl_env["MOCK_CONTROLLER_REPO"] = str(repo)
    ctrl = subprocess.Popen(
        [PY, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(ctrl_port)],
        cwd=str(CONTROLLER_ROOT),
        env=ctrl_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ctrl_base = f"http://127.0.0.1:{ctrl_port}"
    if not _wait(f"{ctrl_base}/api/v1/health"):
        ctrl.terminate()
        pytest.skip("Controller 未启动")

    run_port = _free_port()
    run_env = dict(os.environ)
    run_env.update(
        {
            "MOCK_CONTROLLER_URL": ctrl_base,
            "ATE_CONTROLLER_URL": ctrl_base,
            "ATE_TPS_CACHE_DIR": str(cache),
            "ATE_DB_PATH": str(tmp_path_factory.mktemp("db") / "ate.db"),
            "ATE_CONSTANTS_SYNC": "off",
        }
    )
    runner = subprocess.Popen(
        [PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(run_port)],
        cwd=str(BACKEND),
        env=run_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    run_base = f"http://127.0.0.1:{run_port}"
    if not _wait(f"{run_base}/api/health"):
        ctrl.terminate()
        runner.terminate()
        pytest.skip("Runner 未启动")

    yield {"ctrl": ctrl, "ctrl_base": ctrl_base, "runner": runner, "run_base": run_base, "tests_dir": tests_dir}

    for proc in (runner, ctrl):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_capability_online(stack):
    with httpx.Client(base_url=stack["run_base"], timeout=15) as cli:
        data = cli.get("/api/tps/import/capability").json()
        assert data["success"] is True
        assert data["online"] is True
        assert data["mode"] == "controller"


def test_import_zip_online(stack):
    tps_id = "imp-online-01"
    with httpx.Client(base_url=stack["run_base"], timeout=60) as cli:
        resp = cli.post("/api/tps/import/zip", json={"name": "t.zip", "content_base64": _tps_zip(tps_id)})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["source"] == "controller"
        assert body["tps"]["id"] == tps_id

        listed = cli.get("/api/tps").json()
        assert tps_id in [t["id"] for t in listed["tps"]]


def test_import_bad_zip_rejected(stack):
    with httpx.Client(base_url=stack["run_base"], timeout=60) as cli:
        resp = cli.post(
            "/api/tps/import/zip",
            json={"name": "bad.zip", "content_base64": _tps_zip("imp-bad-01", with_manifest=False)},
        )
        body = resp.json()
        assert body["success"] is False
        assert body.get("message"), "失败时必须给出可读原因"


def test_import_zip_offline_fallback(stack):
    """停掉 Controller 后再导入，应落到本机。"""
    if stack["ctrl"].poll() is None:
        stack["ctrl"].terminate()
        try:
            stack["ctrl"].wait(timeout=10)
        except subprocess.TimeoutExpired:
            stack["ctrl"].kill()

    tps_id = "imp-offline-01"
    with httpx.Client(base_url=stack["run_base"], timeout=60) as cli:
        cap = cli.get("/api/tps/import/capability").json()
        assert cap["online"] is False
        assert cap["mode"] == "local"

        resp = cli.post("/api/tps/import/zip", json={"name": "t.zip", "content_base64": _tps_zip(tps_id)})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["source"] == "local"

        listed = cli.get("/api/tps").json()
        assert tps_id in [t["id"] for t in listed["tps"]], "离线导入后列表应能看到该 TPS"
