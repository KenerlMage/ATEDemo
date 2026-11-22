# -*- coding: utf-8 -*-
"""SR02-AR04（manual check 与回传门禁）、SR02-AR05（标定类标识）测试。

全部用例用 `tmp_path` 作为独立 base_dir：状态文件、推送日志、任务快照与
testresource 都落在临时目录，不碰 repo 的 `manual_check.json` / `logs/`。

运行：
    python -m pytest tests/test_manual_check.py -q --basetemp=.pytest_tmp
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import manual_check  # noqa: E402


# ==================== 夹具与小工具 ====================


@pytest.fixture()
def base(tmp_path, monkeypatch):
    """独立 backend 目录：状态文件与日志都在这里。"""
    root = tmp_path / "backend"
    (root / "logs").mkdir(parents=True)
    monkeypatch.setattr(manual_check, "_BASE_DIR", root, raising=False)
    return root


@pytest.fixture()
def calib_module(monkeypatch):
    """假的 const_runtime：只有 SPM_RH 是标定模型。"""
    fake = types.ModuleType("const_runtime")
    fake.is_calibration_model = lambda key: str(key).strip() == "SPM_RH"
    monkeypatch.setitem(sys.modules, "const_runtime", fake)
    return fake


def _task(steps=None, task_id="T1") -> dict:
    return {"id": task_id, "tps_id": "tps_demo", "steps": list(steps or [])}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_task(base: Path, data: dict) -> None:
    _write_json(base / "tasks" / f"{data['id']}.json", data)


def _write_tps(base: Path, tps_id: str, steps: list[dict]) -> None:
    _write_json(base / "testresource" / tps_id / "tps.json", {"id": tps_id, "name": "演示 TPS", "steps": steps})


def _state(base: Path) -> dict:
    path = base / "manual_check.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _push_lines(base: Path) -> list[str]:
    path = base / "logs" / "mes_push.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _client(base: Path) -> TestClient:
    app = FastAPI()
    manual_check.register_routes(app, base)
    return TestClient(app)


# ==================== needs_check / manual 区分 ====================


def test_needs_check_three_forms():
    """三种形态：True 要确认、dict 要确认、False/缺失不要确认。"""
    assert manual_check.needs_check({"manual_check": True}) is True
    assert manual_check.needs_check({"manual_check": {"role": "qe", "fields": ["gain_offset"]}}) is True
    assert manual_check.needs_check({"manual_check": False}) is False
    assert manual_check.needs_check({"name": "普通步骤", "type": "test"}) is False
    assert manual_check.needs_check({"manual_check": None}) is False
    assert manual_check.needs_check({}) is False
    assert manual_check.needs_check(None) is False


def test_manual_and_manual_check_are_distinct(base):
    """manual 等操作员判定结果，manual_check 等复核回传，两者不互相替代。"""
    manual_step = {"type": "test", "name": "操作员判定", "manual": True}
    check_step = {"type": "test", "name": "复核回传", "manual_check": True}
    both_step = {"type": "test", "name": "既判定又复核", "manual": True, "manual_check": True}

    assert manual_check.needs_check(manual_step) is False
    assert manual_check.needs_check(check_step) is True
    assert manual_check.needs_check(both_step) is True

    items = manual_check.pending_checks(_task([manual_step, check_step], task_id="T_MANUAL"))
    assert [item["step_index"] for item in items] == [2]
    assert items[0]["name"] == "复核回传"


# ==================== 驳回 ====================


def test_reject_without_note_is_refused(base):
    """驳回必须带理由：无理由拒绝，且不落状态、不推送。"""
    result = manual_check.submit_decision("T1", 1, "reject", note="   ", operator="li")

    assert result["success"] is False
    assert "理由" in result["message"]
    assert result.get("code") == "NOTE_REQUIRED"
    assert _state(base) == {}
    assert _push_lines(base) == []


def test_reject_keeps_local_without_push(base):
    """带理由驳回：标记 rejected、留存本地、不回传；重复驳回幂等。"""
    result = manual_check.submit_decision("T1", 2, "reject", note="波形异常，需复测", operator="li")

    assert result["success"] is True
    assert result["pushed"] is False
    assert result["already"] is False
    assert result["record"]["decision"] == "reject"
    assert result["record"]["status"] == "rejected"

    record = _state(base)["T1"]["2"]
    assert record["note"] == "波形异常，需复测"
    assert record["operator"] == "li"
    assert record["pushed"] is False
    assert _push_lines(base) == []          # 驳回绝不回传

    again = manual_check.submit_decision("T1", 2, "reject", note="再写一次", operator="li")
    assert again["success"] is True
    assert again["already"] is True
    assert _state(base)["T1"]["2"]["note"] == "波形异常，需复测"   # 首次理由不被覆盖


# ==================== 确认与幂等回传 ====================


def test_confirm_pushes_once_and_is_idempotent(base):
    """确认后回传一次；重复确认 already=true 且不重复推送。"""
    first = manual_check.submit_decision("T9", 1, "confirm", note="复核通过", operator="zhang")

    assert first["success"] is True
    assert first["already"] is False
    assert first["pushed"] is True
    assert first["record"]["push_mode"] == "dry-run"
    assert len(_push_lines(base)) == 1

    second = manual_check.submit_decision("T9", 1, "confirm", note="复核通过", operator="zhang")

    assert second["success"] is True
    assert second["already"] is True
    assert second["pushed"] is True
    assert len(_push_lines(base)) == 1                 # 幂等：没有第二条日志
    assert _state(base)["T9"]["1"]["pushed"] is True


def test_payload_carries_constants_and_angles(base):
    """确认回传体带上机器常数与测角结果（从任务步骤取）。"""
    _write_task(
        base,
        {
            "id": "T7",
            "tps_id": "tps_mc",
            "steps": [
                {
                    "type": "test",
                    "name": "测角复核",
                    "manual_check": {"role": "qe", "fields": ["gain_offset", "theta"]},
                    "machine_constants": {"SPM_RH": {"gain_offset": 1.25}},
                    "angle_results": {"theta": 12.5},
                }
            ],
        },
    )

    result = manual_check.submit_decision("T7", 1, "confirm", operator="zhang")
    assert result["success"] is True

    line = _push_lines(base)[0]
    payload = json.loads(line.split("payload=", 1)[1])
    assert payload["task_id"] == "T7"
    assert payload["step_name"] == "测角复核"
    assert payload["machine_constants"] == {"SPM_RH": {"gain_offset": 1.25}}
    assert payload["angle_results"] == {"theta": 12.5}
    assert payload["operator"] == "zhang"


def test_payload_merges_runtime_results_with_definition_name(base):
    """真实执行态：步骤名取 TPS 定义，机器常数/测角结果取运行态行。"""
    _write_tps(
        base,
        "tps_run",
        [
            {"type": "test", "name": "增益复核", "manual_check": True},
            {"type": "test", "name": "普通步骤"},
        ],
    )
    _write_task(
        base,
        {
            "id": "T12",
            "tps_id": "tps_run",
            "steps": [
                {
                    "index": 1,
                    "status": "passed",
                    "detail": "ok",
                    "machine_constants": {"SPM_RH": {"gain_offset": 1.4}},
                    "result": {"theta": 11.2},
                },
                {"index": 2, "status": "passed"},
            ],
        },
    )

    result = manual_check.submit_decision("T12", 1, "confirm", operator="zhang")
    assert result["success"] is True

    payload = json.loads(_push_lines(base)[0].split("payload=", 1)[1])
    assert payload["step_name"] == "增益复核"
    assert payload["machine_constants"] == {"SPM_RH": {"gain_offset": 1.4}}
    assert payload["angle_results"] == {"theta": 11.2}


def test_push_log_written_under_base_dir(base):
    """推送日志落盘到 <base_dir>/logs/mes_push.log，且无临时/锁文件残留。"""
    manual_check.submit_decision("T5", 3, "confirm", note="复核通过", operator="wang")

    log_path = base / "logs" / "mes_push.log"
    assert log_path.exists()
    text = log_path.read_text(encoding="utf-8")
    assert "dry-run" in text
    assert "task=T5" in text
    assert "step=3" in text
    assert "operator=wang" in text

    line = text.splitlines()[0]
    assert line.startswith("[") and line.endswith("}")
    payload = json.loads(line.split("payload=", 1)[1])
    assert payload["step_index"] == 3
    assert payload["decision"] == "confirm"

    leftovers = sorted(p.name for p in base.iterdir())
    assert leftovers == ["logs", "manual_check.json"]
    assert not list(base.glob("*.tmp"))


def test_state_file_shape(base):
    """状态文件结构：{task_id: {step_index: {decision, note, operator, at, pushed}}}。"""
    manual_check.submit_decision("T6", 1, "confirm", operator="zhang")

    state = _state(base)
    assert set(state) == {"T6"}
    record = state["T6"]["1"]
    for key in ("decision", "note", "operator", "at", "pushed"):
        assert key in record
    assert record["operator"] == "zhang"
    assert record["pushed"] is True
    assert "T" in record["at"] and len(record["at"]) >= 19


def test_confirm_retries_after_push_failure(base, monkeypatch):
    """回传失败：确认事实落盘、pushed 保持 false；重试成功后只推一次。"""
    real_push = manual_check._push_to_mes
    calls = {"n": 0}

    def flaky(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": False, "mode": "dry-run", "message": "MES 不可达"}
        return real_push(payload)

    monkeypatch.setattr(manual_check, "_push_to_mes", flaky)

    first = manual_check.submit_decision("T10", 1, "confirm", operator="zhang")
    assert first["success"] is False
    assert first["pushed"] is False
    assert "MES 不可达" in first["message"]
    assert _state(base)["T10"]["1"]["pushed"] is False     # 确认事实不丢
    assert _push_lines(base) == []

    second = manual_check.submit_decision("T10", 1, "confirm", operator="zhang")
    assert second["success"] is True
    assert second["pushed"] is True
    assert calls["n"] == 2
    assert len(_push_lines(base)) == 1

    third = manual_check.submit_decision("T10", 1, "confirm", operator="zhang")
    assert third["already"] is True
    assert len(_push_lines(base)) == 1


def test_compose_steps_fallback_for_v2_manifest():
    """v2 清单无 steps 时的兜底顺序：setup -> cmd_suit -> teardown。"""
    tps = {
        "setup": [{"name": "初始化", "case": "a"}],
        "cmd_suit": [{"name": "用例一", "case": "b"}, "用例二"],
        "teardown": [{"name": "终止", "case": "c"}],
    }

    steps = manual_check._compose_steps(tps)

    assert [step["name"] for step in steps] == ["初始化", "用例一", "用例二", "终止"]
    assert [step["type"] for step in steps] == ["setup", "test", "test", "teardown"]


# ==================== 待确认列表 ====================


def test_pending_checks_filters_decided(base):
    """pending_checks 只返回未决项，确认/驳回后立刻从列表消失。"""
    steps = [
        {"type": "test", "name": "增益复核", "manual_check": {"role": "qe", "fields": ["gain_offset"]}},
        {"type": "test", "name": "偏置复核", "manual_check": True},
        {"type": "test", "name": "测角复核", "manual_check": True},
        {"type": "test", "name": "普通步骤"},
    ]
    task = _task(steps, task_id="T2")

    assert [item["step_index"] for item in manual_check.pending_checks(task)] == [1, 2, 3]

    manual_check.submit_decision("T2", 2, "confirm", operator="zhang")
    left = manual_check.pending_checks(task)
    assert [item["step_index"] for item in left] == [1, 3]
    assert left[0]["role"] == "qe"
    assert left[0]["fields"] == ["gain_offset"]
    assert left[1]["fields"] == []

    manual_check.submit_decision("T2", 1, "reject", note="值超差")
    assert [item["step_index"] for item in manual_check.pending_checks(task)] == [3]


# ==================== 标定标识 ====================


def test_calib_badge_three_sources(calib_module):
    """标定标识三种判定来源：type=calib / calibration 为真 / 引用标定模型。"""
    by_type = manual_check.calib_badge({"type": "calib", "name": "标定步骤"})
    by_flag = manual_check.calib_badge({"type": "test", "name": "偏置标定", "calibration": True})
    by_model = manual_check.calib_badge(
        {"type": "test", "name": "读头标定", "machine_constants": {"models": [{"model_key": "SPM_RH"}]}}
    )

    for badge in (by_type, by_flag, by_model):
        assert badge["calib"] is True
        assert badge["label"] == "标定"
        assert badge["position"] == "before"

    plain = manual_check.calib_badge({"type": "test", "name": "普通步骤", "model_key": "MOT_DRV"})
    assert plain["calib"] is False
    assert plain["label"] == "标定" and plain["position"] == "before"
    assert manual_check.calib_badge(None)["calib"] is False


def test_calib_badge_degrades_when_model_checker_fails(monkeypatch):
    """模型判定器异常/缺失时降级为「非标定」，不外抛。"""
    fake = types.ModuleType("const_runtime")

    def _boom(_key):
        raise RuntimeError("标定模型库不可用")

    fake.is_calibration_model = _boom
    monkeypatch.setitem(sys.modules, "const_runtime", fake)

    badge = manual_check.calib_badge({"type": "test", "model_key": "SPM_RH"})
    assert badge["calib"] is False


def test_calib_route_partitions(base, calib_module):
    """GET /tasks/{id}/calib：标识、分区与统计一致（任务只有运行态步骤时回查 TPS）。"""
    _write_tps(
        base,
        "tps_calib",
        [
            {"type": "setup", "name": "初始化"},
            {"type": "test", "name": "增益测试"},
            {"type": "calib", "name": "标定步骤"},
            {"type": "test", "name": "偏置标定", "calibration": True},
            {"type": "test", "name": "读头标定", "model_key": "SPM_RH"},
        ],
    )
    _write_task(
        base,
        {
            "id": "T3",
            "tps_id": "tps_calib",
            "status": "completed",
            "steps": [{"index": i, "status": "passed"} for i in range(1, 6)],
        },
    )

    body = _client(base).get("/api/runner/tasks/T3/calib").json()

    assert body["success"] is True
    assert body["has_calib"] is True
    assert body["summary"] == {"total": 5, "calib": 3, "normal": 2}
    assert [item["name"] for item in body["partitions"]["calib"]] == ["标定步骤", "偏置标定", "读头标定"]
    assert [item["name"] for item in body["partitions"]["normal"]] == ["初始化", "增益测试"]
    assert [item["step_index"] for item in body["partitions"]["calib"]] == [3, 4, 5]
    assert all(item["badge"]["label"] == "标定" for item in body["partitions"]["calib"])


def test_calib_route_without_calib_steps(base):
    """无标定步骤时 has_calib=false，全部进 normal 分区。"""
    _write_task(
        base,
        {
            "id": "T4",
            "tps_id": "tps_plain",
            "steps": [{"type": "setup", "name": "初始化"}, {"type": "test", "name": "电压测试"}],
        },
    )

    body = _client(base).get("/api/runner/tasks/T4/calib").json()

    assert body["success"] is True
    assert body["has_calib"] is False
    assert body["partitions"]["calib"] == []
    assert len(body["partitions"]["normal"]) == 2


# ==================== 路由 ====================


def test_manual_check_routes_flow(base):
    """路由全流程：列待确认 -> 驳回缺理由被拒 -> 确认回传 -> 列表清空。"""
    _write_tps(
        base,
        "tps_mc",
        [
            {"type": "test", "name": "增益复核", "manual_check": {"role": "qe", "fields": ["gain_offset"]}},
            {"type": "test", "name": "普通步骤"},
        ],
    )
    _write_task(base, {"id": "T8", "tps_id": "tps_mc"})
    client = _client(base)

    listed = client.get("/api/runner/manual-check/T8").json()
    assert listed["success"] is True
    assert listed["pending"] == 1
    assert listed["total_steps"] == 2
    item = listed["items"][0]
    assert item["step_index"] == 1
    assert item["name"] == "增益复核"
    assert item["role"] == "qe"
    assert item["fields"] == ["gain_offset"]

    refused = client.post(
        "/api/runner/manual-check/T8/1", json={"decision": "reject", "note": "", "operator": "li"}
    ).json()
    assert refused["success"] is False
    assert "理由" in refused["message"]
    assert _state(base) == {}

    bad_decision = client.post("/api/runner/manual-check/T8/1", json={"decision": "maybe"}).json()
    assert bad_decision["success"] is False
    assert "confirm" in bad_decision["message"]

    confirmed = client.post(
        "/api/runner/manual-check/T8/1", json={"decision": "confirm", "note": "复核通过", "operator": "zhang"}
    ).json()
    assert confirmed["success"] is True
    assert confirmed["pushed"] is True
    assert len(_push_lines(base)) == 1

    again = client.post(
        "/api/runner/manual-check/T8/1", json={"decision": "confirm", "note": "复核通过", "operator": "zhang"}
    ).json()
    assert again["already"] is True
    assert len(_push_lines(base)) == 1

    assert client.get("/api/runner/manual-check/T8").json()["pending"] == 0


def test_load_task_from_main_memory_store(base, monkeypatch):
    """生产路径：任务在执行端 main 的内存表 TPS_TASKS 里，也能列出待确认项。"""
    fake_main = types.ModuleType("main")
    fake_main.TPS_TASKS = {
        "T11": {
            "id": "T11",
            "tps_id": "tps_mem",
            "steps": [{"type": "test", "name": "内存任务复核", "manual_check": True}],
        }
    }
    fake_main.TASKS = {}
    monkeypatch.setitem(sys.modules, "main", fake_main)

    body = _client(base).get("/api/runner/manual-check/T11").json()

    assert body["success"] is True
    assert body["pending"] == 1
    assert body["items"][0]["name"] == "内存任务复核"


def test_routes_report_unknown_task_in_chinese(base):
    """未知任务号：三个路由都返回 success=false + 中文原因，不抛裸异常。"""
    client = _client(base)

    listed = client.get("/api/runner/manual-check/NO_SUCH_TASK").json()
    assert listed["success"] is False
    assert "任务不存在" in listed["message"]

    decided = client.post(
        "/api/runner/manual-check/NO_SUCH_TASK/1", json={"decision": "confirm"}
    ).json()
    assert decided["success"] is False
    assert "任务不存在" in decided["message"]

    calib = client.get("/api/runner/tasks/NO_SUCH_TASK/calib").json()
    assert calib["success"] is False
    assert "任务不存在" in calib["message"]

    assert _push_lines(base) == []
