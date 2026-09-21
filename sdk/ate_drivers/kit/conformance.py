# -*- coding: utf-8 -*-
"""一致性测试套件（conformance）：驱动"可上架"的判据

一个驱动包必须在**仿真模式**下通过全部检查——这是"平台敢在产线上加载它"的前提，
也是驱动开发者唯一需要记住的验收命令：

    python -m ate_drivers.kit.cli check samples/tek_mso5

检查项覆盖三类风险：
* **契约风险**：元数据、契约版本、能力声明与实现是否一致、命令表是否完整；
* **行为风险**：每个能力在仿真下能否跑通、返回结构是否可序列化、单位是否齐全、
  错误是否可分类、会话能否安全关闭、仿真是否可复现、实例之间是否互不干扰；
* **派发风险**（C17 / C18 / C19）：型号是否可派发、连接方式（网口 / 串口）与传输后端
  （原生栈 / VISA）是否声明合法、
  与 `driver.json` 的 `models` / `interfaces` 是否一致、声明的方式能否解析出真实端点；
* **依赖风险**：是否偷偷引入了第三方依赖、旧动作别名是否有效、弃用项是否给了替代。
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from typing import Any, Callable, Optional

from ..capabilities import is_known, validate
from ..contract import API_VERSION, DEPRECATIONS, RESULT_SCHEMA, check_compatible
from ..errors import DriverError, UnsupportedCapability
from ..endpoint import (SUPPORTED_BACKENDS, SUPPORTED_INTERFACES, backends_of,
                         endpoint_from, interfaces_of, normalize_backend, normalize_interface)
from ..transport import VisaTransport, transport_class
from ..models import DeviceConfig

SAMPLE_DEVICE = {
    "device_id": "chk-scope-1",
    "name": "一致性检查用示波器",
    "model": "MSO54",
    "vendor": "Tektronix",
    "category": "示波器",
    "role": "波形观测",
    "interface": "LAN",
    "host": "192.168.10.41",
    "port": 4000,
    "protocol": "VXI-11 (SCPI)",
    "programmable": True,
    "required": True,
}

REQUIRED_RESULT_KEYS = ("ok", "schema", "device_id", "driver", "action", "value",
                        "detail", "quality", "source", "simulated", "elapsed_ms")
SI_UNITS = ("V", "A", "s", "Hz", "Ω", "W", "dB", "Vpp")


def _new(driver_cls, mode: str = "simulate", device: Optional[dict] = None, **kw):
    return driver_cls(DeviceConfig.from_dict(device or SAMPLE_DEVICE), mode=mode, **kw)


# ------------------------------------------------------------------ 检查项

def c01_class_metadata(ctx) -> tuple[bool, str]:
    cls = ctx["cls"]
    problems = []
    if not re.match(r"^[a-z0-9][a-z0-9._-]*$", str(cls.driver_key or "")):
        problems.append(f"driver_key 非法: {cls.driver_key!r}（应形如 vendor-model）")
    if not str(cls.family or "").strip():
        problems.append("family 为空")
    if not str(cls.label or "").strip():
        problems.append("label 为空（报告与工具卡片要用它显示中文名）")
    if not re.match(r"^\d+\.\d+\.\d+$", str(cls.driver_version or "")):
        problems.append(f"driver_version 非法: {cls.driver_version!r}（应为 x.y.z）")
    return (not problems), "；".join(problems) or f"{cls.driver_key} v{cls.driver_version}"


def c02_contract_compatible(ctx) -> tuple[bool, str]:
    ok, reason = check_compatible(ctx["cls"].api_version, API_VERSION)
    return ok, reason


def c03_capability_declaration(ctx) -> tuple[bool, str]:
    missing, unknown, vendor = validate(ctx["cls"].CAPABILITIES, ctx["cls"].family)
    bad_unknown = [c for c in unknown if not is_known(c)]
    if missing or bad_unknown:
        return False, f"缺家族基线能力 {missing}；未登记能力 {bad_unknown}"
    note = f"声明 {len(set(ctx['cls'].CAPABILITIES))} 项；厂商扩展 {vendor}" if vendor else "声明完整"
    return True, note


def c04_capabilities_implemented(ctx) -> tuple[bool, str]:
    cls = ctx["cls"]
    bad = []
    for cap in cls.CAPABILITIES:
        method = cls.ACTIONS.get(cap)
        if not method or not callable(getattr(cls, method, None)):
            bad.append(f"{cap} -> {method or '(未映射)'}")
    return (not bad), "；".join(bad) or "每个能力都有对应方法"


def c05_command_table(ctx) -> tuple[bool, str]:
    try:
        dev = _new(ctx["cls"])
    except Exception as e:  # pragma: no cover
        return False, f"实例化失败: {e}"
    if not hasattr(dev, "missing_commands"):
        return True, "该家族无命令表要求"
    missing = dev.missing_commands()
    return (not missing), f"命令表缺键: {missing}" if missing else "命令表完整"


def c06_simulate_all_capabilities(ctx) -> tuple[bool, str]:
    dev = _new(ctx["cls"])
    failed = []
    for cap in sorted(set(ctx["cls"].CAPABILITIES)):
        try:
            result = dev.do(cap)
            if not result.get("ok"):
                failed.append(f"{cap}: ok=False")
        except Exception as e:
            failed.append(f"{cap}: {type(e).__name__} {e}")
    dev.close()
    return (not failed), "；".join(failed) or f"仿真下 {len(set(ctx['cls'].CAPABILITIES))} 项能力全部可执行"


def c07_result_schema(ctx) -> tuple[bool, str]:
    dev = _new(ctx["cls"])
    result = dev.do("identify")
    dev.close()
    if result.get("schema") != RESULT_SCHEMA:
        return False, f"结果 schema 应为 {RESULT_SCHEMA}，实际 {result.get('schema')!r}"
    missing = [k for k in REQUIRED_RESULT_KEYS if k not in result]
    if missing:
        return False, f"结果缺键: {missing}"
    try:
        json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        return False, f"结果不可 JSON 序列化（报告落盘会失败）: {e}"
    return True, f"{len(result)} 个键且可序列化"


def c08_units_on_measurements(ctx) -> tuple[bool, str]:
    dev = _new(ctx["cls"])
    if not dev.supports("scope.measure"):
        return True, "非示波器家族，跳过单位检查"
    items = dev.do("scope.measure", items=["PK2PK", "FREQUENCY"])["value"]
    dev.close()
    bad = [i for i in items if not i.get("unit") or i["unit"] not in SI_UNITS]
    want = {"PK2PK": "V", "FREQUENCY": "Hz"}
    wrong = [i["type"] for i in items if want.get(i["type"]) and i["unit"] != want[i["type"]]]
    if bad or wrong:
        return False, f"单位缺失 {bad}；单位与约定不符 {wrong}"
    return True, "；".join(f"{i['type']}={i['value']}{i['unit']}" for i in items)


def c09_typed_errors(ctx) -> tuple[bool, str]:
    dev = _new(ctx["cls"])
    notes = []
    try:
        dev.do("no.such.action")
        return False, "未知动作没有抛错（必须抛 UnsupportedCapability）"
    except UnsupportedCapability as e:
        notes.append(f"未知动作 -> {e.code}")
    except DriverError as e:
        return False, f"未知动作抛了 {type(e).__name__}（应为 UnsupportedCapability）"
    if dev.supports("scope.measure"):
        try:
            dev.do("scope.measure", items=["NOT_A_ITEM"])
            return False, "非法测量项没有抛错"
        except DriverError as e:
            if not str(e.code).startswith("E_"):
                return False, f"错误码不规范: {e.code}"
            notes.append(f"非法参数 -> {e.code}")
        except Exception as e:
            return False, f"非法参数抛了裸异常 {type(e).__name__}: {e}"
    dev.close()
    return True, "；".join(notes)


def c10_session_lifecycle(ctx) -> tuple[bool, str]:
    dev = _new(ctx["cls"])
    opened = dev.open()
    if not opened.get("ok") or not dev.opened:
        return False, "open() 未成功"
    if not dev.idn:
        return False, "open() 后 idn 为空（identify 未生效）"
    dev.close()
    dev.close()
    if dev.opened:
        return False, "close() 后仍处于打开状态"
    dev.open()
    again = dev.do("identify")
    dev.close()
    return True, f"open/close 幂等，重开可用（idn={again['value']}）"


def c11_teardown_safety(ctx) -> tuple[bool, str]:
    dev = _new(ctx["cls"])
    caps = set(ctx["cls"].CAPABILITIES)
    if not ({"scope.run", "scope.single"} & caps):
        return True, "无危险状态需保护"
    dev.do("scope.run")
    guarded = len(dev._teardown) > 0
    dev.close()
    return guarded, "启动采集后登记了安全退出命令（关闭时下发 stop）" if guarded else "采集启动后未登记安全退出命令"


def c12_determinism(ctx) -> tuple[bool, str]:
    a = _new(ctx["cls"])
    caps = set(ctx["cls"].CAPABILITIES)
    if "scope.acquire_waveform" not in caps:
        return True, "无波形能力，跳过"
    w1 = a.do("scope.acquire_waveform", points=500)["value"]["samples"]
    w2 = a.do("scope.acquire_waveform", points=500)["value"]["samples"]
    b = _new(ctx["cls"])
    w3 = b.do("scope.acquire_waveform", points=500)["value"]["samples"]
    a.close()
    b.close()
    if w1 != w2:
        return False, "两次采集结果不同（仿真必须可复现）"
    if w1 != w3:
        return False, "两个实例结果不同（应只由参数决定）"
    return True, f"{len(w1)} 点，两次调用与跨实例均一致"


def c13_instance_isolation(ctx) -> tuple[bool, str]:
    cls = ctx["cls"]
    if "scope.timebase" not in set(cls.CAPABILITIES):
        return True, "无时基能力，跳过"
    a, b = _new(cls), _new(cls)
    a.do("scope.timebase", scale_s_per_div=2e-3)
    tb_b = b.do("scope.timebase")["value"]["scale_s_per_div"]
    ta = a.do("scope.timebase")["value"]["scale_s_per_div"]
    a.close()
    b.close()
    if abs(ta - 2e-3) > 1e-12 or abs(tb_b - 5e-4) > 1e-12:
        return False, f"实例之间串了状态：A={ta} B={tb_b}（类属性被当共享变量用）"
    return True, "两个实例状态互不影响"


def c14_aliases_valid(ctx) -> tuple[bool, str]:
    cls = ctx["cls"]
    bad = [f"{k}->{v}" for k, v in getattr(cls, "ALIASES", {}).items() if v not in cls.ACTIONS]
    return (not bad), f"无效别名: {bad}" if bad else f"{len(getattr(cls, 'ALIASES', {}))} 个旧名别名均有效"


#: 可选运行时依赖：只允许**惰性导入**（函数体内），缺它时给出可读错误并降级，不算硬依赖
OPTIONAL_RUNTIME = ("serial", "pyvisa")


def _import_names(node) -> list:
    if isinstance(node, ast.Import):
        return [item.name.split(".")[0] for item in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return [node.module.split(".")[0]]
    return []


def _walk_imports(node, depth: int, out: list) -> None:
    """收集 (模块名, 嵌套深度)；depth 0 = 模块顶层（import 时就执行 → 硬依赖）"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _walk_imports(child, depth + 1, out)
        elif isinstance(child, (ast.Import, ast.ImportFrom)):
            for name in _import_names(child):
                out.append((name, depth))
        else:
            _walk_imports(child, depth, out)


def c15_no_third_party_imports(ctx) -> tuple[bool, str]:
    """驱动包只允许标准库 + `ate_drivers`；`OPTIONAL_RUNTIME` 里的库只允许惰性导入"""
    allowed = set(getattr(sys, "stdlib_module_names", set()))
    root_pkg = ctx.get("root_package") or ""
    hard: list[str] = []
    lazy: list[str] = []
    for modname, module in list(sys.modules.items()):
        if not root_pkg or not modname.startswith(root_pkg):
            continue
        path = getattr(module, "__file__", "") or ""
        if not path.endswith(".py") or not os.path.exists(path):
            continue
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except (OSError, SyntaxError):
            continue
        found: list = []
        _walk_imports(tree, 0, found)
        for name, depth in found:
            if name in allowed or name in ("ate_drivers", root_pkg.split(".")[0]):
                continue
            if depth > 0 and name in OPTIONAL_RUNTIME:
                lazy.append(name)
            else:
                hard.append("%s: %s（深度 %d）" % (os.path.basename(path), name, depth))
    uniq = sorted(set(hard))
    lazy_names = sorted(set(lazy))
    tail = ("仅标准库与 ate_drivers" if not lazy_names
            else "仅标准库与 ate_drivers（可选运行时依赖：%s，惰性导入、缺它时降级）"
                 % ", ".join(lazy_names))
    return (not uniq), (f"引入第三方依赖: {uniq}" if uniq else tail)


def c16_deprecations_documented(ctx) -> tuple[bool, str]:
    if not DEPRECATIONS:
        return True, "无弃用项"
    bad = [k for k, (_, remove_in, repl) in DEPRECATIONS.items() if not repl or not remove_in]
    return (not bad), f"弃用项缺替代/移除版本: {bad}" if bad else f"{len(DEPRECATIONS)} 项弃用均登记了替代动作"


def c17_model_dispatch(ctx) -> tuple[bool, str]:
    """型号派发：必须声明 `MODELS`（与 `driver.json` 的 `models` 一致）"""
    cls = ctx["cls"]
    models = tuple(getattr(cls, "MODELS", ()) or ())
    problems = []
    if not models:
        problems.append("未声明 MODELS（型号派发要求显式列出支持的型号）")
    if any(not str(m).strip() for m in models):
        problems.append(f"MODELS 含空项: {models!r}")
    padded = [re.sub(r"[^A-Z0-9]", "", str(m).upper()) for m in models]
    if len(set(padded)) != len(padded):
        problems.append(f"MODELS 内有重复: {models!r}")
    module = sys.modules.get(cls.__module__)
    path = getattr(module, "__file__", "") or ""
    if path:
        manifest_path = os.path.join(os.path.dirname(path), "driver.json")
        if os.path.isfile(manifest_path):
            try:
                listed = [str(m) for m in (json.load(open(manifest_path, encoding="utf-8")).get("models") or [])]
            except Exception as e:  # pragma: no cover
                listed, problems = None, problems + [f"driver.json 无法解析: {e}"]
            if listed is not None and [re.sub(r"[^A-Z0-9]", "", m.upper()) for m in listed] != padded:
                problems.append(f"driver.json models={listed} 与类 MODELS={list(models)} 不一致")
    tail = f"（{len(models)} 个型号：{', '.join(str(m) for m in models)}）"
    return (not problems), "；".join(problems) or f"型号派发就绪{tail}"


def c18_connection_declared(ctx) -> tuple[bool, str]:
    """连接方式：必须声明 `INTERFACES`（`LAN` / `SERIAL` 组合），每种方式都能解析出真实端点，
    且与 `driver.json` 的 `interfaces` 一致（兼容旧的单值 `interface`）"""
    cls = ctx["cls"]
    raw = getattr(cls, "INTERFACES", None)
    if raw is None:
        raw = (getattr(cls, "INTERFACE", "") or "",)
    elif isinstance(raw, str):
        raw = (raw,)
    declared = [str(item).strip() for item in tuple(raw or ()) if str(item).strip()]
    kinds = interfaces_of(cls)
    problems = []
    if not declared:
        problems.append("未声明连接方式（INTERFACES 应为 %s 组合）" % (SUPPORTED_INTERFACES,))
    unknown = [item for item in declared if not normalize_interface(item)]
    if unknown:
        problems.append(f"连接方式不在支持范围: {unknown}（应为 {SUPPORTED_INTERFACES}）")
    samples = {"LAN": {"host": "192.168.10.41", "port": 4000, "protocol": "TCP"},
               "SERIAL": {"serial_port": "COM6", "baudrate": 115200}}
    for kind in kinds:
        try:
            ep = endpoint_from(dict(samples.get(kind) or {}), interface=kind)
        except Exception as e:      # 端点解析必须给出可读错误，不能崩
            problems.append(f"{kind} 端点解析异常: {type(e).__name__}: {e}")
            continue
        if ep is None or ep.kind != kind:
            problems.append(f"{kind} 端点解析结果异常: {ep!r}")
            continue
        try:
            cfg = ep.to_device_config(model="CHK-MODEL", vendor="CHK")
        except Exception as e:
            problems.append(f"{kind} 端点转档案失败: {type(e).__name__}: {e}")
            continue
        if cfg.transport_kind() != ("socket" if kind == "LAN" else "serial"):
            problems.append(f"{kind} 端点转档案后链路类型为 {cfg.transport_kind()!r}")
    module = sys.modules.get(cls.__module__)
    path = getattr(module, "__file__", "") or ""
    if path:
        manifest_path = os.path.join(os.path.dirname(path), "driver.json")
        if os.path.isfile(manifest_path):
            try:
                raw = json.load(open(manifest_path, encoding="utf-8"))
            except Exception as e:  # pragma: no cover
                raw = None
                problems.append(f"driver.json 无法解析: {e}")
            if raw is not None:
                listed = raw.get("interfaces")
                if listed is None:
                    listed = [raw["interface"]] if raw.get("interface") else []
                norm = [normalize_interface(m) for m in listed]
                if sorted(norm) != sorted(kinds):
                    problems.append(f"driver.json interfaces={listed} 与类 INTERFACES={list(kinds)} 不一致")
    return (not problems), "；".join(problems) or f"连接方式就绪（{' / '.join(kinds)}）"



def c19_backends_declared(ctx) -> tuple[bool, str]:
    """传输后端：必须声明 `BACKENDS`（`native` / `visa` 组合），每种后端在网口与串口链路上
    都能解析到链路类，且与 `driver.json` 的 `backends` 一致（兼容旧的单值 `backend`）。

    装了 pyvisa 才算"能用 visa"，但**门禁不要求当前环境装 pyvisa**——它校验的是
    "声明合法 + 链路类可解析 + 缺依赖时可读报错"，与 C15 对可选依赖的口径一致。
    """
    cls = ctx["cls"]
    raw = getattr(cls, "BACKENDS", None)
    if raw is None:
        raw = (getattr(cls, "BACKEND", "") or "",)
    elif isinstance(raw, str):
        raw = (raw,)
    declared = [str(item).strip() for item in tuple(raw or ()) if str(item).strip()]
    backends = backends_of(cls)
    problems = []
    if not declared:
        problems.append("未声明传输后端（BACKENDS 应为 %s 组合）" % (SUPPORTED_BACKENDS,))
    unknown = [item for item in declared if normalize_backend(item) not in SUPPORTED_BACKENDS]
    if unknown:
        problems.append(f"传输后端不在支持范围: {unknown}（应为 {SUPPORTED_BACKENDS}）")
    if any(normalize_backend(item) == "auto" for item in declared):
        problems.append("BACKENDS 不能写 auto（auto 是调用时的选择，不是声明的能力）")
    for backend in backends:
        for link in ("socket", "serial"):
            if transport_class(backend, link) is None:
                problems.append(f"{backend} 后端在 {link} 链路上没有链路类")
    if "visa" in backends and transport_class("visa", "serial") is not VisaTransport:
        problems.append("visa 后端（串口）没有走同一个 VISA 链路类")
    module = sys.modules.get(cls.__module__)
    path = getattr(module, "__file__", "") or ""
    if path:
        manifest_path = os.path.join(os.path.dirname(path), "driver.json")
        if os.path.isfile(manifest_path):
            try:
                listed = json.load(open(manifest_path, encoding="utf-8")).get("backends")
                if listed is None:
                    legacy = json.load(open(manifest_path, encoding="utf-8")).get("backend")
                    listed = [legacy] if legacy else []
            except Exception as e:  # pragma: no cover
                listed = None
                problems.append(f"driver.json 无法解析: {e}")
            if listed is not None:
                norm = [normalize_backend(m) for m in listed]
                if sorted(x for x in norm if x) != sorted(backends):
                    problems.append(f"driver.json backends={listed} 与类 BACKENDS={list(backends)} 不一致")
    return (not problems), "；".join(problems) or f"传输后端就绪（{' / '.join(backends)}）"


CHECKS: list[tuple[str, str, Callable]] = [
    ("C01", "类元数据完整", c01_class_metadata),
    ("C02", "契约版本兼容", c02_contract_compatible),
    ("C03", "能力声明合法", c03_capability_declaration),
    ("C04", "能力均有实现", c04_capabilities_implemented),
    ("C05", "命令表完整", c05_command_table),
    ("C06", "仿真可执行全部能力", c06_simulate_all_capabilities),
    ("C07", "返回结构可序列化", c07_result_schema),
    ("C08", "测量项带单位", c08_units_on_measurements),
    ("C09", "错误可分类", c09_typed_errors),
    ("C10", "会话可重入", c10_session_lifecycle),
    ("C11", "安全退出", c11_teardown_safety),
    ("C12", "仿真可复现", c12_determinism),
    ("C13", "实例互不干扰", c13_instance_isolation),
    ("C14", "旧动作别名有效", c14_aliases_valid),
    ("C15", "零第三方依赖", c15_no_third_party_imports),
    ("C16", "弃用项有替代", c16_deprecations_documented),
    ("C17", "型号可派发", c17_model_dispatch),
    ("C18", "连接方式可声明", c18_connection_declared),
    ("C19", "传输后端可声明", c19_backends_declared),
]


def run_conformance(driver_cls, device: Optional[dict] = None,
                    root_package: str = "") -> dict:
    """跑全套检查，返回报告（`ok` 为 True 才允许上架）"""
    ctx: dict[str, Any] = {
        "cls": driver_cls,
        "device": device or SAMPLE_DEVICE,
        "root_package": root_package or (driver_cls.__module__.split(".")[0] if "." in driver_cls.__module__ else ""),
    }
    rows = []
    for cid, title, fn in CHECKS:
        try:
            ok, detail = fn(ctx)
        except Exception as e:  # 检查本身出错也算不通过（不能静默放过）
            ok, detail = False, f"检查执行异常: {type(e).__name__}: {e}"
        rows.append({"id": cid, "title": title, "ok": bool(ok), "detail": detail})
    passed = sum(1 for r in rows if r["ok"])
    return {
        "driver": getattr(driver_cls, "driver_key", driver_cls.__name__),
        "version": getattr(driver_cls, "driver_version", ""),
        "api": getattr(driver_cls, "api_version", ""),
        "family": getattr(driver_cls, "family", ""),
        "total": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "ok": passed == len(rows),
        "checks": rows,
    }


def format_report(report: dict) -> str:
    lines = [
        f"驱动 {report['driver']} v{report['version']}（契约 {report['api']} · 家族 {report['family']}）",
        f"结果 {report['passed']}/{report['total']} 通过" + ("" if report["ok"] else "  ← 不通过，禁止上架"),
        "-" * 72,
    ]
    for r in report["checks"]:
        lines.append(f"  [{'PASS' if r['ok'] else 'FAIL'}] {r['id']} {r['title']}：{r['detail']}")
    return "\n".join(lines)
