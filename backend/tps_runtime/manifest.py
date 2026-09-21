# -*- coding: utf-8 -*-
"""TPS 清单（schema = tps.v2）的校验与归一化

三个主要字段
------------
* `testconfig`   —— 测试阈值子字典：`{阈值键: {label, min, max, unit, ...}}`
* `device_config`—— 设备信息子字典：`{别名: {device_id|role, mode, ...}}`（连接参数在 SQLite 里）
* `cmd_suit`     —— 测试套排列：有序列表，每项 `case` 指向 TPS 自带 `testcase/` 中的实现

归一化后额外产出 `steps`（兼容既有前端的步骤列表 / 报告 / 任务模型）：
`setup[]` -> init 步骤，`cmd_suit[]` -> test 步骤，`teardown[]` -> teardown 步骤。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

TPS_V2_SCHEMA = "tps.v2"
TPS_SCHEMA_V1 = "tps.v1"

ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
CASE_REF_RE = re.compile(r"^(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*::[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")

CASE_KINDS = {"setup": ("init", "test_setup"), "test": ("test", "test_case"), "teardown": ("teardown", "test_teardown")}


def is_v2(raw: Any) -> bool:
    """判断清单是否为 v2（含 cmd_suit 或显式声明 schema）"""
    if not isinstance(raw, dict):
        return False
    return str(raw.get("schema", "")) == TPS_V2_SCHEMA or ("cmd_suit" in raw and "steps" not in raw)


# ------------------------------------------------------------------ 校验


def validate_v2(raw: dict) -> list[str]:
    """返回错误信息列表（空列表 = 通过）"""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return ["TPS 清单必须是 JSON 对象"]

    schema = raw.get("schema")
    if schema not in (None, TPS_V2_SCHEMA):
        errors.append(f"schema 只支持 {TPS_V2_SCHEMA}（实际: {schema}）")

    tps_id = str(raw.get("id", "")).strip()
    if not tps_id:
        errors.append("缺少必填字段: id")
    elif not ID_RE.match(tps_id):
        errors.append("id 只能包含英文/数字/下划线/点/短横线 (1-64 位)")

    # ---- testconfig ----
    testconfig = raw.get("testconfig")
    if testconfig is None:
        errors.append("缺少必填字段: testconfig（测试阈值子字典）")
        testconfig = {}
    elif not isinstance(testconfig, dict) or not testconfig:
        errors.append("testconfig 必须是非空对象: {阈值键: {min, max, unit, label}}")
        testconfig = {} if not isinstance(testconfig, dict) else testconfig
    for key, spec in (testconfig or {}).items():
        if not isinstance(spec, dict):
            errors.append(f"testconfig[{key}] 必须是对象（含 min/max/unit/label）")
            continue
        has_limit = any(spec.get(k) is not None for k in ("min", "max"))
        if not has_limit:
            errors.append(f"testconfig[{key}] 至少要给 min 或 max 之一")
        for edge in ("min", "max"):
            val = spec.get(edge)
            if val is None:
                continue
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                errors.append(f"testconfig[{key}].{edge} 必须是数字（实际: {val!r}）")
        lo, hi = spec.get("min"), spec.get("max")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo > hi:
            errors.append(f"testconfig[{key}] 下限 {lo} 大于上限 {hi}")

    # ---- device_config ----
    device_config = raw.get("device_config")
    if device_config is None:
        errors.append("缺少必填字段: device_config（设备信息子字典）")
        device_config = {}
    elif not isinstance(device_config, dict) or not device_config:
        errors.append("device_config 必须是非空对象: {别名: {device_id 或 role}}")
        device_config = {} if not isinstance(device_config, dict) else device_config
    for alias, spec in (device_config or {}).items():
        if isinstance(spec, str):
            continue  # 允许简写: {别名: device_id}
        if not isinstance(spec, dict):
            errors.append(f"device_config[{alias}] 必须是对象或设备编号字符串")
            continue
        if not any(spec.get(k) for k in ("device_id", "device", "role", "category", "model")):
            errors.append(f"device_config[{alias}] 需要 device_id / role / category / model 之一")

    # ---- cmd_suit ----
    cmd_suit = raw.get("cmd_suit")
    if not isinstance(cmd_suit, list) or not cmd_suit:
        errors.append("缺少必填字段: cmd_suit（非空测试套排列）")
        cmd_suit = []
    seen_ids: set[str] = set()
    for i, case in enumerate(cmd_suit, 1):
        errors.extend(_validate_case(case, i, testconfig or {}, seen_ids, prefix="cmd_suit"))

    for section in ("setup", "teardown"):
        entries = raw.get(section)
        if entries is None:
            continue
        if not isinstance(entries, list):
            errors.append(f"{section} 必须是数组")
            continue
        local_ids: set[str] = set()
        for i, case in enumerate(entries, 1):
            errors.extend(_validate_case(case, i, testconfig or {}, local_ids, prefix=section))

    # `steps`（v1 遗留）与 cmd_suit 同时出现时给出明确提示，避免误解
    if raw.get("steps") and cmd_suit:
        errors.append("TPS 同时包含 steps（v1）与 cmd_suit（v2），请只保留一种")
    return errors


def _validate_case(case: Any, index: int, testconfig: dict, seen_ids: set, prefix: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(case, dict):
        return [f"{prefix}[{index}] 必须是对象"]
    case_id = str(case.get("id") or "").strip()
    if case_id:
        if not ID_RE.match(case_id):
            errors.append(f"{prefix}[{index}].id 含非法字符（只允许英文/数字/下划线/点/短横线）: {case_id}")
        elif case_id in seen_ids:
            errors.append(f"{prefix}[{index}].id 重复: {case_id}")
        else:
            seen_ids.add(case_id)
    ref = str(case.get("case") or "").strip()
    if not ref:
        errors.append(f"{prefix}[{index}] 缺少 case（用例实现引用，如 testcase.power::power_on_voltage）")
    elif not CASE_REF_RE.match(ref):
        errors.append(f"{prefix}[{index}].case 格式应为 module::function（实际: {ref}）")
    if not case.get("name"):
        errors.append(f"{prefix}[{index}] 缺少 name（用例名称）")
    checks = case.get("checks")
    keys: list[str] = []
    if isinstance(checks, dict):
        keys = [str(v) for v in checks.values()]
    elif isinstance(checks, list):
        keys = [str(v) for v in checks]
    elif isinstance(checks, str):
        keys = [checks]
    if case.get("config"):
        keys.append(str(case["config"]))
    for key in keys:
        if key not in (testconfig or {}):
            errors.append(f"{prefix}[{index}] 引用了未定义的阈值: {key}")
    params = case.get("params")
    if params is not None and not isinstance(params, dict):
        errors.append(f"{prefix}[{index}].params 必须是对象")
    if case.get("optional") is not None and not isinstance(case.get("optional"), bool):
        errors.append(f"{prefix}[{index}].optional 必须是布尔值")
    return errors


# ------------------------------------------------------------------ 归一化


def case_entries(tps: dict) -> dict:
    """把三个字段拆成三段可执行用例：setup / test / teardown

    每个用例: `{id, no, name, kind, func, case, params, checks, config, optional, description}`
    其中 `func` 是生成文件里的测试函数名（用于拼 pytest 节点 id）
    """
    out: dict[str, list] = {"setup": [], "test": [], "teardown": []}
    for section, entries in (("setup", tps.get("setup") or []),
                            ("test", tps.get("cmd_suit") or []),
                            ("teardown", tps.get("teardown") or [])):
        kind, func = CASE_KINDS[section]
        for i, raw_case in enumerate(entries, 1):
            case = dict(raw_case or {})
            case_id = str(case.get("id") or "").strip()
            if not case_id:
                case_id = {"setup": f"SETUP{i:02d}", "test": f"TC{i:03d}", "teardown": f"TD{i:02d}"}[section]
            checks = case.get("checks")
            if isinstance(checks, list):
                checks = {str(k): str(k) for k in checks}
            elif isinstance(checks, str):
                checks = {"value": checks}
            out[section].append({
                "id": case_id,
                "no": case.get("no", i),
                "name": case.get("name") or case_id,
                "kind": kind,
                "func": func,
                "case": str(case.get("case") or ""),
                "params": dict(case.get("params") or {}),
                "checks": dict(checks or {}),
                "config": str(case.get("config") or ""),
                "optional": bool(case.get("optional")),
                "description": case.get("description", ""),
            })
    return out


def steps_of(tps: dict) -> list[dict]:
    """归一化成既有任务模型 / 报告使用的 steps 列表"""
    entries = case_entries(tps)
    steps: list[dict] = []
    for section in ("setup", "test", "teardown"):
        for case in entries[section]:
            node = f"{tps.get('_generated', 'test_tps_generated.py')}::{case['func']}[{case['id']}]"
            steps.append({
                "type": case["kind"],
                "name": case["name"],
                "description": case.get("description", ""),
                "v2": {
                    "id": case["id"],
                    "no": case["no"],
                    "kind": case["kind"],
                    "case": case["case"],
                    "checks": case["checks"],
                    "config": case["config"],
                    "optional": case["optional"],
                    "node": node,
                },
            })
    return steps


def normalize_tps(raw: dict, base_dir: Optional[Path] = None) -> dict:
    """v2 清单 -> 既有代码可用的 TPS 字典（带 steps / testconfig / device_config / cmd_suit）"""
    tps = dict(raw or {})
    tps["_raw"] = dict(raw or {})
    tps["schema"] = TPS_V2_SCHEMA
    tps.setdefault("id", "")
    tps.setdefault("name", tps["id"])
    tps.setdefault("version", "2.0.0")
    tps.setdefault("description", "")
    tps.setdefault("project", "")
    tps.setdefault("dut", "")
    tps.setdefault("environment", {})
    tps["testconfig"] = dict(tps.get("testconfig") or {})
    tps["device_config"] = dict(tps.get("device_config") or {})
    tps["cmd_suit"] = list(tps.get("cmd_suit") or [])
    tps["setup"] = list(tps.get("setup") or [])
    tps["teardown"] = list(tps.get("teardown") or [])
    tps["steps"] = steps_of(tps)
    tps["step_count"] = len(tps["steps"])
    if base_dir is not None:
        tps["_source"] = str(base_dir)
    thumb = tps.get("testconfig") or {}
    tps["testconfig_count"] = len(thumb)
    tps["device_alias_count"] = len(tps["device_config"])
    return tps


def summary_tps(tps: dict) -> dict:
    """列表页摘要"""
    return {
        "id": tps.get("id", ""),
        "name": tps.get("name", tps.get("id", "")),
        "description": tps.get("description", ""),
        "version": tps.get("version", ""),
        "schema": tps.get("schema", TPS_SCHEMA_V1),
        "step_count": len(tps.get("steps") or []),
        "environment": tps.get("environment", {}),
        "project": tps.get("project", ""),
        "dut": tps.get("dut", ""),
        "cmd_suit_count": len(tps.get("cmd_suit") or []),
        "testconfig_count": len(tps.get("testconfig") or {}),
        "device_alias_count": len(tps.get("device_config") or {}),
        "bench": tps.get("bench", {}),
        "workspace_dir": tps.get("workspace_dir", ""),
    }
