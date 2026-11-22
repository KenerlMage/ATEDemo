# -*- coding: utf-8 -*-
"""Runner 单用例人工确认与标定类标识（SR02-AR04、SR02-AR05）。

语义
----
* **manual**（`step["manual"]` 为真）= 操作员现场判定结果（通过/失败/中止），
  判定完即入账，与 MES 回传无关。
* **manual_check**（`step["manual_check"]` 为真或 dict）= 该步骤结果先落本地，
  等复核人确认后**才**回传 MES；驳回必须带理由且不回传。

两者互不替代：`manual` 不会触发确认门禁，`manual_check` 也不会代替现场判定。

状态文件
--------
`<base_dir>/manual_check.json`：

    {
      "<task_id>": {
        "1": {"decision": "confirm", "status": "confirmed", "note": "",
              "operator": "zhang", "at": "2026-11-22T20:10:11+08:00",
              "pushed": true, "push_mode": "dry-run", "push_at": "..."}
      }
    }

键为步骤序号（从 1 开始，与执行端 `Step i/total` 一致）。写入用「临时文件 +
`os.replace`」原子落盘，并配一把锁文件做跨进程串行；`pushed` 是回传幂等位。

对外
----
* `needs_check(step) -> bool`
* `submit_decision(task_id, step_index, decision, note="", operator="") -> dict`
* `pending_checks(task) -> list[dict]`
* `calib_badge(step) -> dict`
* `set_base_dir(base_dir)` / `state_path()` —— 测试与多实例用
* `register_routes(app, base_dir, db_path=None)` —— 挂 `/api/runner` 前缀路由
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

STATE_FILENAME = "manual_check.json"
PUSH_LOG = Path("logs") / "mes_push.log"

# 运行期基准目录：默认 backend/，register_routes / set_base_dir 覆盖
_BASE_DIR = Path(__file__).resolve().parent
_LOCK = threading.RLock()

_CALIB_TYPES = ("calib", "calibration", "calibrate")
_TRUE_WORDS = ("1", "true", "yes", "on", "y")


# ==================== 基础设施 ====================


def now_str() -> str:
    """统一时间格式：本地时区、秒级 ISO8601。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def set_base_dir(base_dir) -> Path:
    """切换状态文件与推送日志的基准目录（测试用 tmp_path 隔离）。"""
    global _BASE_DIR
    _BASE_DIR = Path(base_dir)
    return _BASE_DIR


def _base_dir() -> Path:
    return Path(_BASE_DIR)


def state_path(base_dir=None) -> Path:
    """门禁状态文件路径。"""
    return Path(base_dir) / STATE_FILENAME if base_dir else _base_dir() / STATE_FILENAME


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_WORDS
    if isinstance(value, (dict, list, tuple)):
        return len(value) > 0
    return False


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _fail(message: str, **extra) -> dict:
    body = {"success": False, "message": message}
    body.update(extra)
    return body


# ---- 状态文件读写：文件锁 + 原子写 ----


@contextlib.contextmanager
def _file_lock(path: Path, timeout: float = 3.0):
    """跨进程锁：O_EXCL 建锁文件，超时/异常时降级为「只靠原子写」。

    不因锁竞争把正常请求打回失败——最坏情况是两个进程各写一份完整 JSON，
    原子替换保证文件不会出现半截内容。
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    handle = None
    deadline = time.monotonic() + timeout
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001 - 目录不可建时由后续写盘报错
        pass
    while True:
        try:
            handle = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                try:  # 超过 30s 的锁视为残留
                    if time.time() - lock_path.stat().st_mtime > 30:
                        lock_path.unlink(missing_ok=True)
                        continue
                except Exception:  # noqa: BLE001
                    pass
                break
            time.sleep(0.02)
        except Exception:  # noqa: BLE001 - 无权限等场景直接放行
            break
    try:
        yield
    finally:
        if handle is not None:
            try:
                os.close(handle)
            except Exception:  # noqa: BLE001
                pass
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _write_json_atomic(path: Path, data: dict) -> None:
    """写 tmp -> fsync -> os.replace，杜绝半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:  # noqa: BLE001
                pass


def _load_state() -> dict:
    """读状态文件；缺失或损坏都按空状态处理（不抛异常）。"""
    path = state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 损坏文件当空，下一次写入即重建
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _save_state(state: dict) -> None:
    _write_json_atomic(state_path(), state)


# ==================== 人工确认判定 ====================


def _check_flag(step: dict):
    if "manual_check" in step:
        return step.get("manual_check")
    return step.get("manualCheck")


def needs_check(step) -> bool:
    """该步骤是否需要复核后回传。

    `manual_check` 为 True 或 dict（可带 role / fields）即需要；
    False、其他假值、缺失均不需要。`manual` 与 `manual_check` 是两件事，
    这里只认 `manual_check`。
    """
    if not isinstance(step, dict):
        return False
    flag = _check_flag(step)
    if isinstance(flag, dict):
        return True
    return _truthy(flag)


def _normalize_fields(value) -> list[str]:
    """把 fields 归一化成字段键列表，兼容 str / list[str] / list[dict] / dict。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        return [str(k) for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    out.append(item.strip())
            elif isinstance(item, dict):
                key = item.get("key") or item.get("name") or item.get("field")
                if isinstance(key, str) and key.strip():
                    out.append(key.strip())
        return out
    return []


def _check_spec(step: dict) -> dict:
    """确认项的附加信息：复核角色与需确认字段。"""
    flag = _check_flag(step)
    if isinstance(flag, dict):
        role = flag.get("role") or flag.get("approver") or flag.get("reviewer") or ""
        fields = flag.get("fields")
        if fields is None:
            fields = flag.get("required_fields")
    else:
        role = step.get("manual_check_role") or step.get("review_role") or ""
        fields = step.get("manual_check_fields")
        if fields is None:
            fields = step.get("check_fields")
    return {"role": str(role or "").strip(), "fields": _normalize_fields(fields)}


def _step_name(step: dict, index: int) -> str:
    if isinstance(step, dict):
        for key in ("name", "label", "title", "id"):
            value = step.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return f"步骤 {index}"


def _task_steps(task) -> list[dict]:
    """取任务的定义态步骤列表（task 可以是 dict，也可以直接给 steps 列表）。"""
    if isinstance(task, list):
        return [s for s in task if isinstance(s, dict)]
    if not isinstance(task, dict):
        return []
    steps = task.get("steps")
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


def pending_checks(task) -> list[dict]:
    """筛出「需要人工确认且尚未决」的步骤。

    返回 `[{step_index, name, fields, role}]`；step_index 从 1 开始。
    任务带 id 时按状态文件过滤已确认/已驳回项，未带 id 时全部视为待确认。
    """
    steps = _task_steps(task)
    if not steps:
        return []
    task_id = ""
    if isinstance(task, dict):
        task_id = str(task.get("id") or task.get("task_id") or "").strip()
    decided = _load_state().get(task_id, {}) if task_id else {}

    items: list[dict] = []
    for position, step in enumerate(steps, 1):
        if not needs_check(step):
            continue
        if str(position) in decided:
            continue
        spec = _check_spec(step)
        items.append(
            {
                "step_index": position,
                "name": _step_name(step, position),
                "fields": spec["fields"],
                "role": spec["role"],
            }
        )
    return items


# ==================== 任务 / TPS 取数 ====================


def _load_task(task_id: str):
    """按任务号找执行中的任务：sys.modules 里的 main 内存表优先，再退任务快照文件。

    只查已加载的 `main` / `__main__`，不主动 import（避免副作用与循环导入）；
    测试或多实例场景可用 `_TASK_LOOKUP` 注入。
    """
    tid = str(task_id or "").strip()
    if not tid:
        return None

    lookup = globals().get("_TASK_LOOKUP")
    if callable(lookup):
        try:
            found = lookup(tid)
            if isinstance(found, dict):
                return found
        except Exception:  # noqa: BLE001 - 注入钩子失败继续走默认路径
            pass

    for mod_name in ("main", "__main__"):
        module = sys.modules.get(mod_name)
        if module is None:
            continue
        for store_name in ("TPS_TASKS", "TASKS"):
            store = getattr(module, store_name, None)
            if isinstance(store, dict) and tid in store:
                task = store.get(tid)
                return dict(task) if isinstance(task, dict) else None

    snapshot = _base_dir() / "tasks" / f"{tid}.json"
    if snapshot.exists():
        try:
            data = json.loads(snapshot.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        if isinstance(data, dict):
            data.setdefault("id", tid)
            return data
    return None


def _compose_steps(tps: dict) -> list[dict]:
    """v2 清单兜底拼步骤：setup -> cmd_suit -> teardown（tps_runtime 缺席时用）。"""
    steps: list[dict] = []
    for key, kind in (("setup", "setup"), ("cmd_suit", "test"), ("teardown", "teardown")):
        for entry in tps.get(key) or []:
            if isinstance(entry, dict):
                item = dict(entry)
                item.setdefault("type", kind)
                steps.append(item)
            elif isinstance(entry, str) and entry.strip():
                steps.append({"type": kind, "name": entry.strip(), "case": entry.strip()})
    return steps


def _normalize_tps(tps: dict) -> dict:
    if not isinstance(tps, dict):
        return {}
    if isinstance(tps.get("steps"), list) and tps["steps"]:
        return tps
    try:
        runtime = importlib.import_module("tps_runtime")
        normalized = runtime.normalize_tps(tps)
        if isinstance(normalized, dict) and normalized.get("steps"):
            return normalized
    except Exception:  # noqa: BLE001 - 归一化不可用时自行兜底
        pass
    merged = dict(tps)
    merged["steps"] = _compose_steps(tps)
    return merged


def _load_tps(tps_id: str):
    """从本地 testresource 读 TPS 清单（`<id>/tps.json` 或 `<id>.json`）。"""
    tid = str(tps_id or "").strip()
    if not tid or "/" in tid or "\\" in tid or tid in (".", ".."):
        return None
    root = _base_dir() / "testresource"
    for path in (root / tid / "tps.json", root / f"{tid}.json"):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            data.setdefault("id", tid)
            return _normalize_tps(data)
    return None


def _is_definition_step(step) -> bool:
    """判断步骤是否带定义信息（name/type/manual_check 等），而非纯运行态行。

    注意不能把 `machine_constants` / `result` 算作定义标记：执行后运行态行也会带这两项。
    """
    if not isinstance(step, dict):
        return False
    return any(
        key in step
        for key in (
            "name",
            "type",
            "manual",
            "manual_check",
            "manualCheck",
            "calibration",
            "model_key",
        )
    )


def _resolve_steps(task) -> list[dict]:
    """任务步骤定义：任务内已有定义就用，否则按 tps_id 回查 TPS 清单。

    执行中的任务 `steps` 只有 index/status/detail 等运行态字段，
    这里补回 TPS 定义，保证步骤序号与执行日志一致。
    """
    steps = _task_steps(task)
    if steps and any(_is_definition_step(s) for s in steps):
        return steps
    if isinstance(task, dict):
        tps = _load_tps(task.get("tps_id"))
        if tps:
            defined = _task_steps(tps)
            if defined:
                return defined
    return steps


# ==================== 提交确认 / 驳回 ====================


def _pick(step: dict, keys: tuple[str, ...]) -> dict:
    """取第一个非空的值（结果字段在不同写法间兼容）。"""
    for key in keys:
        value = step.get(key)
        if value:
            return value if isinstance(value, dict) else {"value": value}
    return {}


def _build_payload(task_id: str, step_index: int, note: str, operator: str, at: str) -> dict:
    """回传体：机器常数 + 测角结果（能从任务里取到就带上，取不到留空对象）。"""
    payload = {
        "kind": "manual_check",
        "task_id": task_id,
        "step_index": step_index,
        "decision": "confirm",
        "note": note,
        "operator": operator,
        "at": at,
        "target": "MES",
        "machine_constants": {},
        "angle_results": {},
        "step_name": "",
    }
    try:
        task = _load_task(task_id)
        if not task:
            return payload
        defined = _resolve_steps(task)
        running = _task_steps(task)
        definition = defined[step_index - 1] if 0 < step_index <= len(defined) else {}
        runtime = running[step_index - 1] if 0 < step_index <= len(running) else {}
        payload["step_name"] = _step_name(definition, step_index) if definition else ""
        payload["machine_constants"] = _pick(runtime, ("machine_constants", "constants")) or _pick(
            definition, ("machine_constants", "constants")
        )
        payload["angle_results"] = _pick(runtime, ("angle_results", "angles", "results", "result")) or _pick(
            definition, ("angle_results", "angles", "results", "result")
        )
    except Exception:  # noqa: BLE001 - 取数失败不影响回传门禁本身
        pass
    return payload


def _push_to_mes(payload: dict) -> dict:
    """MES 回传桩：只写日志不外发（dry-run），返回 ok/mode/log。"""
    log_path = _base_dir() / PUSH_LOG
    result = {"ok": True, "mode": "dry-run", "log": str(log_path)}
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        line = (
            f"[{now_str()}] dry-run task={payload.get('task_id', '')} "
            f"step={payload.get('step_index', '')} decision=confirm "
            f"operator={payload.get('operator') or '-'} "
            f"payload={json.dumps(payload, ensure_ascii=False, sort_keys=True)}\n"
        )
        with open(log_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
    except Exception as exc:  # noqa: BLE001 - 日志是回传的唯一痕迹，写不下去就当回传失败
        return {
            "ok": False,
            "mode": "dry-run",
            "log": str(log_path),
            "message": f"MES 回传日志写入失败：{exc}",
        }
    return result


def _public_record(record: dict) -> dict:
    return {
        "decision": record.get("decision", ""),
        "status": record.get("status", ""),
        "note": record.get("note", ""),
        "operator": record.get("operator", ""),
        "at": record.get("at", ""),
        "pushed": bool(record.get("pushed")),
        "push_mode": record.get("push_mode", ""),
        "push_at": record.get("push_at", ""),
    }


def submit_decision(task_id: str, step_index, decision: str, note: str = "", operator: str = "") -> dict:
    """确认或驳回某步骤。

    * confirm：先落盘（pushed=false）再回传；回传成功置 pushed=true。
      已 pushed 的步骤再确认返回 `already=true`，不重复推送。
    * reject：`note` 必填，标记 rejected 且不回传。

    返回统一 `{"success": bool, "message": str, ...}`，不抛异常。
    """
    try:
        tid = str(task_id or "").strip()
        if not tid:
            return _fail("缺少任务号（task_id）")
        index = _as_int(step_index)
        if index is None or index <= 0:
            return _fail("步骤序号必须为正整数（从 1 开始）")

        action = str(decision or "").strip().lower()
        if action in ("confirm", "confirmed", "ok", "pass"):
            action = "confirm"
        elif action in ("reject", "rejected", "fail", "refuse"):
            action = "reject"
        else:
            return _fail("decision 只能是 confirm 或 reject")

        note = str(note or "").strip()
        operator = str(operator or "").strip()
        if action == "reject" and not note:
            return _fail("驳回必须填写理由（note）", code="NOTE_REQUIRED")

        at = now_str()
        key = str(index)
        with _LOCK, _file_lock(state_path()):
            state = _load_state()
            bucket = state.get(tid) if isinstance(state.get(tid), dict) else {}
            record = bucket.get(key) if isinstance(bucket.get(key), dict) else {}

            if action == "confirm":
                if record.get("pushed"):
                    return {
                        "success": True,
                        "already": True,
                        "message": "该步骤此前已确认并回传，本次未重复推送",
                        "task_id": tid,
                        "step_index": index,
                        "pushed": True,
                        "record": _public_record(record),
                    }
                if record.get("status") == "rejected":
                    return _fail("该步骤已被驳回，需重新执行后再确认", code="ALREADY_REJECTED")

                record = {
                    "decision": "confirm",
                    "status": "confirmed",
                    "note": note,
                    "operator": operator,
                    "at": at,
                    "pushed": False,
                    "push_mode": "",
                    "push_at": "",
                }
                bucket[key] = record
                state[tid] = bucket
                _save_state(state)  # 先固化确认事实，回传失败也不丢

                push = _push_to_mes(_build_payload(tid, index, note, operator, at))
                record["push_mode"] = str(push.get("mode") or "")
                if push.get("ok"):
                    record["pushed"] = True
                    record["push_at"] = now_str()
                else:
                    record["push_error"] = str(push.get("message") or push.get("error") or "未知错误")
                bucket[key] = record
                state[tid] = bucket
                _save_state(state)

                if not push.get("ok"):
                    return {
                        "success": False,
                        "already": False,
                        "message": f"已确认但 MES 回传失败：{record['push_error']}（可重试）",
                        "task_id": tid,
                        "step_index": index,
                        "pushed": False,
                        "record": _public_record(record),
                    }
                return {
                    "success": True,
                    "already": False,
                    "message": f"已确认，机器常数与测角结果已回传 MES（{record['push_mode']}）",
                    "task_id": tid,
                    "step_index": index,
                    "pushed": True,
                    "record": _public_record(record),
                }

            # ---- 驳回：带理由、落本地、不回传 ----
            if record.get("status") == "rejected":
                return {
                    "success": True,
                    "already": True,
                    "message": "该步骤此前已驳回，本次未重复记录",
                    "task_id": tid,
                    "step_index": index,
                    "pushed": bool(record.get("pushed")),
                    "record": _public_record(record),
                }
            if record.get("pushed"):
                return _fail("该步骤已确认并回传，不能再驳回", code="ALREADY_PUSHED")

            record = {
                "decision": "reject",
                "status": "rejected",
                "note": note,
                "operator": operator,
                "at": at,
                "pushed": False,
                "push_mode": "",
                "push_at": "",
            }
            bucket[key] = record
            state[tid] = bucket
            _save_state(state)
            return {
                "success": True,
                "already": False,
                "message": "已驳回，结果留存本地，不回传 MES",
                "task_id": tid,
                "step_index": index,
                "pushed": False,
                "record": _public_record(record),
            }
    except Exception as exc:  # noqa: BLE001 - 契约要求异常不外抛
        return _fail(f"提交确认失败：{exc}")


# ==================== 标定类标识 ====================


def _is_calib_model(model_key: str) -> bool:
    """模型是否标定型：优先问 const_runtime / const_catalog，导入或调用失败即降级。"""
    key = str(model_key or "").strip()
    if not key:
        return False
    for module_name in ("const_runtime", "const_catalog"):
        try:
            module = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 - 未部署该模块时降级
            continue
        checker = getattr(module, "is_calibration_model", None)
        if not callable(checker):
            continue
        try:
            return bool(checker(key))
        except Exception:  # noqa: BLE001 - 判定失败按非标定处理
            continue
    return False


def _model_refs(step: dict) -> list[str]:
    """收集步骤引用的模型键（含 machine_constants 绑定里的模型）。"""
    refs: list[str] = []

    def add(value) -> None:
        if isinstance(value, str):
            text = value.strip()
            if text:
                refs.append(text)
        elif isinstance(value, dict):
            for key in ("model_key", "modelKey", "model", "key"):
                text = value.get(key)
                if isinstance(text, str) and text.strip():
                    refs.append(text.strip())
            for key in ("models", "machine_constants", "items", "bindings"):
                add(value.get(key))
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)

    for key in ("model_key", "modelKey", "model", "models", "calibration_model", "constant_model"):
        add(step.get(key))
    add(step.get("machine_constants"))
    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            unique.append(ref)
    return unique


def calib_badge(step) -> dict:
    """标定标识：`{'calib': bool, 'label': '标定', 'position': 'before'}`。

    判定来源三选一：步骤 type 为 calib / `calibration` 为真 / 引用的模型是标定模型。
    """
    badge = {"calib": False, "label": "标定", "position": "before"}
    if not isinstance(step, dict):
        return badge
    kind = str(step.get("type") or step.get("step_type") or "").strip().lower()
    if kind in _CALIB_TYPES:
        badge["calib"] = True
        return badge
    if _truthy(step.get("calibration")) or _truthy(step.get("is_calib")) or _truthy(step.get("calib")):
        badge["calib"] = True
        return badge
    for ref in _model_refs(step):
        if _is_calib_model(ref):
            badge["calib"] = True
            return badge
    return badge


def _calib_items(task) -> tuple[list[dict], list[dict], list[dict]]:
    steps = _resolve_steps(task)
    items: list[dict] = []
    normal: list[dict] = []
    calib: list[dict] = []
    for position, step in enumerate(steps, 1):
        item = {
            "step_index": position,
            "name": _step_name(step, position),
            "badge": calib_badge(step),
        }
        items.append(item)
        (calib if item["badge"]["calib"] else normal).append(item)
    return items, normal, calib


# ==================== HTTP 路由 ====================


def register_routes(app, base_dir, db_path=None) -> None:
    """挂载 `/api/runner` 下的人工确认与标定标识路由。

    `base_dir` 决定状态文件（`manual_check.json`）、推送日志与 testresource 位置；
    `db_path` 目前不参与本模块逻辑，仅为与其它 Runner 模块签名一致。

    POST 先校验任务存在（任务快照或 main 内存表），避免对错误任务号误回传。
    """
    from fastapi import APIRouter, Body

    set_base_dir(base_dir)
    router = APIRouter(prefix="/api/runner", tags=["manual-check"])

    @router.get("/manual-check/{task_id}")
    def list_manual_checks(task_id: str) -> dict:
        """列出该任务待确认项。"""
        try:
            task = _load_task(task_id)
            if not task:
                return _fail(f"任务不存在：{task_id}", task_id=str(task_id))
            steps = _resolve_steps(task)
            if not steps:
                return _fail(f"任务无可识别步骤：{task_id}", task_id=str(task_id))
            items = pending_checks({**task, "id": task.get("id") or task_id, "steps": steps})
            return {
                "success": True,
                "message": None,
                "task_id": str(task_id),
                "tps_id": str(task.get("tps_id") or ""),
                "total_steps": len(steps),
                "items": items,
                "pending": len(items),
            }
        except Exception as exc:  # noqa: BLE001
            return _fail(f"读取待确认项失败：{exc}", task_id=str(task_id))

    @router.post("/manual-check/{task_id}/{step_index}")
    def decide_manual_check(task_id: str, step_index: int, body: dict | None = Body(default=None)) -> dict:
        """确认或驳回：body `{decision: confirm|reject, note, operator}`。"""
        try:
            if not _load_task(task_id):
                return _fail(f"任务不存在：{task_id}", task_id=str(task_id))
            if body is None:
                body = {}
            if not isinstance(body, dict):
                return _fail("请求体必须是 JSON 对象", task_id=str(task_id))
            result = submit_decision(
                task_id,
                step_index,
                body.get("decision", ""),
                note=body.get("note", "") or "",
                operator=body.get("operator", "") or "",
            )
            result.setdefault("task_id", str(task_id))
            return result
        except Exception as exc:  # noqa: BLE001
            return _fail(f"人工确认失败：{exc}", task_id=str(task_id))

    @router.get("/tasks/{task_id}/calib")
    def task_calib(task_id: str) -> dict:
        """标定标识与标定结果分区。"""
        try:
            task = _load_task(task_id)
            if not task:
                return _fail(f"任务不存在：{task_id}", task_id=str(task_id))
            items, normal, calib = _calib_items(task)
            return {
                "success": True,
                "message": None,
                "task_id": str(task_id),
                "tps_id": str(task.get("tps_id") or ""),
                "has_calib": bool(calib),
                "items": items,
                "partitions": {"normal": normal, "calib": calib},
                "summary": {"total": len(items), "calib": len(calib), "normal": len(normal)},
            }
        except Exception as exc:  # noqa: BLE001
            return _fail(f"读取标定标识失败：{exc}", task_id=str(task_id))

    app.include_router(router)
