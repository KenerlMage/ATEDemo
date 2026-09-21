"""ATE 测试台注册与导航 - 独立后端模块

职责:
  1) 预设测试台类型 (含 BOM 清单)      -> GET  /api/testbench/presets
  1.1) 预设类型级联树 (产品/子系统/类型)  -> GET  /api/testbench/tree
  2) 测试台注册 (三步流程数据落库)      -> POST /api/testbenches
  3) 已注册测试台列表 / 详情 / 删除     -> GET  /api/testbenches
  3.1) 重建默认测试台 (删除后恢复)       -> POST /api/testbenches/restore-defaults
  4) 注册后连通性自检 (自检清单)        -> POST /api/testbenches/{id}/verify

默认测试台:
  后端启动时自动登记 DEFAULT_TESTBENCHES 中的测试台 (开箱即已注册),
  幂等且删除后不自动复活; 需要时调用 POST /api/testbenches/restore-defaults 重建。

数据文件:
  backend/testresource/testbench_presets.json   预设测试台类型 + BOM (只读)
  backend/testresource/testbenches.json         已注册测试台 (配置 + 最近一次自检结果)

约定:
  测试台编号 serial = "SPMTS" + 恰好 12 位数字, 与装备清单 XML 校验规则保持一致。

挂载方式 (在 main.py 末尾):
  import testbench_registry
  testbench_registry.register_routes(app, TPS_DIR)
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel

SERIAL_RE = re.compile(r"^SPMTS[0-9]{12}$")
NET_INTERFACES = {"LAN", "ETHERNET", "TCP", "IP"}
SERIAL_INTERFACES = {"SERIAL", "RS232", "RS485", "UART", "COM"}
PASSIVE_INTERFACES = {"USB", "GPIB", "NONE", "MANUAL", ""}

_LOCK = threading.RLock()
_PRESET_FILE: Path = Path("testbench_presets.json")
_REGISTRY_FILE: Path = Path("testbenches.json")
# 预设类型摘要缓存 {key: (文件路径, mtime), table: {preset_id: 摘要}}
_PRESET_META_CACHE: dict[str, Any] = {"key": None, "table": {}}

SEEDED_KEY = "seeded_defaults"  # 注册库中记录"已种过默认测试台"的预设列表, 删除后不复活

# 默认测试台: 后端启动时自动登记, 开箱即已注册 (可直接演示 / 联调)
DEFAULT_TESTBENCHES: list[dict] = [
    {
        "preset_id": "SPM-RH-DYN-01",
        "title": "SPM读头动态测试台",
        "serial": "SPMTS202609120001",
        "station": {
            "line": "SPM 总装线",
            "station": "读头动态测试工位",
            "location": "一号厂房 A 区 3 号工位",
            "remark": "系统默认测试台 (预置), 可直接用于演示与联调",
        },
        "verify_mode": "simulate",  # 启动时以离线模拟方式生成自检清单, 不探测真实网络
    }
]


# ==================== 通用工具 ====================


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    """原子写: 先写临时文件再替换, 避免注册过程中断导致数据文件损坏"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _norm_serial(text: str) -> str:
    return (text or "").strip().upper().replace(" ", "")


def _as_int(value: Any) -> Optional[int]:
    """前端空字符串 / 非法值统一归一为 None, 避免写入脏数据"""
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ==================== 请求模型 ====================


class DeviceConfig(BaseModel):
    id: str = ""
    name: str = ""
    model: str = ""
    vendor: str = ""
    category: str = ""
    role: str = ""
    programmable: bool = False
    interface: str = "NONE"
    host: str = ""
    port: Optional[int] = None
    protocol: str = ""
    serial_port: str = ""
    baudrate: Optional[int] = None
    address: str = ""
    channel: str = ""
    required: bool = True
    note: str = ""


class TestbenchSaveRequest(BaseModel):
    id: Optional[str] = None
    preset_id: str = ""
    preset_name: str = ""
    title: str = ""
    serial: str = ""
    station: dict = {}
    devices: list[dict] = []
    step: int = 1
    status: str = "draft"          # draft(草稿) / registered(已注册)


class VerifyRequest(BaseModel):
    mode: str = "real"       # real(真实连通性检查) / simulate(离线演示模拟自检)
    timeout: float = 2.0
    retries: int = 1


class DeviceConfigPatch(BaseModel):
    """可编程设备属性修改请求。

    只允许改连接参数 (IP / 端口 / 串口 / 波特率 / 资源地址 / 通道 / 协议 / 备注),
    BOM 元信息 (型号 / 厂商 / 类别 / 用途) 不允许通过此接口改动。
    reset_defaults=true 时恢复为该预设类型 BOM 中的默认连接值。
    """
    host: Optional[str] = None
    port: Optional[int] = None
    protocol: Optional[str] = None
    serial_port: Optional[str] = None
    baudrate: Optional[int] = None
    address: Optional[str] = None
    channel: Optional[str] = None
    note: Optional[str] = None
    reset_defaults: bool = False


# ==================== 预设测试台类型 ====================


def _load_presets() -> list[dict]:
    data = _read_json(_PRESET_FILE, {"presets": []})
    presets = data.get("presets") if isinstance(data, dict) else data
    return [p for p in (presets or []) if isinstance(p, dict)]


def _preset_summary(p: dict) -> dict:
    bom = p.get("bom") or []
    prog = [d for d in bom if d.get("programmable")]
    return {
        "id": p.get("id", ""),
        "name": p.get("name", ""),
        "category": p.get("category", ""),
        "product": p.get("product", ""),
        "product_name": p.get("product_name", "") or p.get("product", ""),
        "subsystem": p.get("subsystem", ""),
        "subsystem_name": p.get("subsystem_name", "") or p.get("subsystem", ""),
        "description": p.get("description", ""),
        "typical_dut": p.get("typical_dut", ""),
        "recommended_cycle": p.get("recommended_cycle", ""),
        "bom_count": len(bom),
        "programmable_count": len(prog),
        "net_device_count": len([d for d in bom if str(d.get("interface", "")).upper() in NET_INTERFACES]),
        "serial_device_count": len([d for d in bom if str(d.get("interface", "")).upper() in SERIAL_INTERFACES]),
        "device_categories": sorted({d.get("category", "") for d in bom if d.get("category")}),
    }


def _preset_tree() -> list[dict]:
    """按「产品 -> 子系统 -> 预设测试台类型」三级聚合, 供前端级联菜单使用。

    以预设类型自身的 product / subsystem 字段为唯一数据源, 避免手工维护两处结构。
    """
    data = _read_json(_PRESET_FILE, {})
    meta_products = data.get("products") or [] if isinstance(data, dict) else []
    meta_subsystems = data.get("subsystems") or [] if isinstance(data, dict) else []
    pname = {str(m.get("id")): str(m.get("name") or m.get("id")) for m in meta_products if isinstance(m, dict)}
    sname = {str(m.get("id")): str(m.get("name") or m.get("id")) for m in meta_subsystems if isinstance(m, dict)}

    def _md(name: str) -> str:
        return str(name or "").split(" ", 1)[-1] if " " in str(name or "") else str(name or "")

    products: dict[str, dict] = {}
    for p in _load_presets():
        summary = _preset_summary(p)
        pid = summary["product"] or "未分类"
        sid = summary["subsystem"] or "未分类"
        node = products.setdefault(
            pid,
            {
                "product": pid,
                "product_name": pname.get(pid) or summary["product_name"] or pid,
                "subsystems": {},
            },
        )
        sub = node["subsystems"].setdefault(
            sid,
            {
                "subsystem": sid,
                "subsystem_name": sname.get(sid) or _md(summary["subsystem_name"]) or sid,
                "presets": [],
            },
        )
        sub["presets"].append(
            {
                "id": summary["id"],
                "name": summary["name"],
                "category": summary["category"],
                "description": summary["description"],
                "typical_dut": summary["typical_dut"],
                "recommended_cycle": summary["recommended_cycle"],
                "bom_count": summary["bom_count"],
                "programmable_count": summary["programmable_count"],
                "net_device_count": summary["net_device_count"],
                "serial_device_count": summary["serial_device_count"],
            }
        )

    # 未出现在预设中的产品 / 子系统 (元数据里声明但暂无机型) 也一并返回, 便于后续扩展
    for m in meta_products:
        if isinstance(m, dict) and str(m.get("id")) not in products:
            products[str(m.get("id"))] = {
                "product": str(m.get("id")),
                "product_name": str(m.get("name") or m.get("id")),
                "subsystems": {},
            }

    out: list[dict] = []
    for pid, node in products.items():
        subs = [node["subsystems"][k] for k in sorted(node["subsystems"].keys())]
        if not subs:
            continue
        out.append(
            {
                "product": node["product"],
                "product_name": node["product_name"],
                "subsystem_count": len(subs),
                "preset_count": sum(len(s["presets"]) for s in subs),
                "subsystems": subs,
            }
        )
    out.sort(key=lambda x: str(x["product"]))
    return out


def _find_preset(preset_id: str) -> Optional[dict]:
    pid = (preset_id or "").strip()
    for p in _load_presets():
        if p.get("id") == pid:
            return p
    return None


def _bom_of_presets() -> dict[str, dict]:
    """返回 {preset_id: {bom_id: bom_entry}} 便于按 BOM 补齐设备元信息"""
    table: dict[str, dict] = {}
    for p in _load_presets():
        table[p.get("id", "")] = {d.get("id", ""): d for d in (p.get("bom") or [])}
    return table


def _devices_from_preset(preset: dict) -> list[dict]:
    devices = []
    for d in preset.get("bom") or []:
        devices.append(
            {
                "id": d.get("id", ""),
                "name": d.get("name", ""),
                "model": d.get("model", ""),
                "vendor": d.get("vendor", ""),
                "category": d.get("category", ""),
                "role": d.get("role", ""),
                "programmable": bool(d.get("programmable")),
                "interface": (d.get("interface") or "NONE").upper(),
                "host": d.get("default_host", ""),
                "port": d.get("default_port"),
                "protocol": d.get("protocol", ""),
                "serial_port": d.get("default_serial_port", ""),
                "baudrate": d.get("default_baudrate"),
                "address": d.get("default_address", ""),
                "channel": d.get("default_channel", ""),
                "required": bool(d.get("required", True)),
                "note": d.get("note", ""),
                "configured": False,
            }
        )
    return devices


def _merge_devices(devices: list[dict], preset_id: str) -> list[dict]:
    """以预设 BOM 为基准合并前端提交的设备配置, 保留 BOM 元信息防止被前端覆盖"""
    bom = _bom_of_presets().get(preset_id, {})
    merged: list[dict] = []
    for raw in devices:
        if not isinstance(raw, dict):
            continue
        base = dict(bom.get(raw.get("id", ""), {}))
        item = {
            "id": raw.get("id") or base.get("id", ""),
            "name": raw.get("name") or base.get("name", ""),
            "model": raw.get("model") or base.get("model", ""),
            "vendor": raw.get("vendor") or base.get("vendor", ""),
            "category": raw.get("category") or base.get("category", ""),
            "role": raw.get("role") or base.get("role", ""),
            "programmable": bool(raw.get("programmable", base.get("programmable", False))),
            "interface": (raw.get("interface") or base.get("interface") or "NONE").upper(),
            "host": (raw.get("host") or "").strip(),
            "port": _as_int(raw.get("port")),
            "protocol": raw.get("protocol") or base.get("protocol", ""),
            "serial_port": (raw.get("serial_port") or base.get("default_serial_port", "") or "").strip(),
            "baudrate": _as_int(raw.get("baudrate")) or _as_int(base.get("default_baudrate")),
            "address": (raw.get("address") or "").strip(),
            "channel": (raw.get("channel") or "").strip(),
            "required": bool(raw.get("required", base.get("required", True))),
            "note": raw.get("note") or base.get("note", ""),
        }
        item["configured"] = _device_configured(item)
        merged.append(item)
    return merged


def _device_configured(dev: dict) -> bool:
    iface = str(dev.get("interface", "")).upper()
    if iface in NET_INTERFACES:
        return bool(str(dev.get("host") or "").strip()) and bool(dev.get("port"))
    if iface in SERIAL_INTERFACES:
        return bool(str(dev.get("serial_port") or "").strip())
    # USB / GPIB 等不支持自动探测的接口: 以是否登记资源地址判定配置完整度
    # (连通性仍需人工确认, 自检时记为 skip)
    return bool(str(dev.get("address") or "").strip())


# ==================== 注册库读写 ====================


def _load_registry() -> dict:
    data = _read_json(_REGISTRY_FILE, {"version": 1, "testbenches": []})
    if isinstance(data, list):
        data = {"version": 1, "testbenches": data}
    if not isinstance(data, dict):
        data = {"version": 1, "testbenches": []}
    data.setdefault("testbenches", [])
    return data


def _save_registry(data: dict) -> None:
    _write_json(_REGISTRY_FILE, data)
    # 注册信息同步投影到 SQLite: TPS 运行时由公共 conftest 从 device_registry 读取
    # 设备连接参数与 driver 规格, 因此每次注册库变更都要重建投影(失败不影响注册流程)
    try:
        import ate_db

        rows = data.get("testbenches") if isinstance(data, dict) else data
        ate_db.sync_from_bench_rows(rows or [], source="registry-save")
    except Exception:
        pass


def _preset_meta_table() -> dict[str, dict]:
    """{preset_id: 预设摘要}（按预设文件 mtime 缓存），用于给测试台补产品/子系统归属"""
    try:
        key = (str(_PRESET_FILE), _PRESET_FILE.stat().st_mtime)
    except OSError:
        key = (str(_PRESET_FILE), None)
    if _PRESET_META_CACHE.get("key") != key:
        table: dict[str, dict] = {}
        for p in _load_presets():
            s = _preset_summary(p)
            table[s["id"]] = s
        _PRESET_META_CACHE["key"] = key
        _PRESET_META_CACHE["table"] = table
    return _PRESET_META_CACHE["table"]


def _public(item: dict) -> dict:
    """对外返回的测试台对象 (补一个自检摘要, 便于列表页直接渲染标签)"""
    out = dict(item)
    ver = item.get("verification") or {}
    out["verify_summary"] = {
        "overall": ver.get("overall", ""),
        "checked_at": ver.get("checked_at", ""),
        "pass": (ver.get("summary") or {}).get("pass", 0),
        "fail": (ver.get("summary") or {}).get("fail", 0),
        "skip": (ver.get("summary") or {}).get("skip", 0),
        "mode": ver.get("mode", ""),
    }
    # 产品 / 子系统归属（来自预设类型，代表该测试台服务的装备树分支）
    meta = _preset_meta_table().get(item.get("preset_id", "")) or {}
    out["product"] = meta.get("product", "")
    out["product_name"] = meta.get("product_name", "")
    out["subsystem"] = meta.get("subsystem", "")
    out["subsystem_name"] = meta.get("subsystem_name", "")
    return out


def _tag(item: dict) -> dict:
    """列表标签用精简对象"""
    st = item.get("station") or {}
    pub = _public(item)
    return {
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "serial": item.get("serial", ""),
        "preset_id": item.get("preset_id", ""),
        "preset_name": item.get("preset_name", ""),
        "product": pub.get("product", ""),
        "product_name": pub.get("product_name", ""),
        "subsystem": pub.get("subsystem", ""),
        "subsystem_name": pub.get("subsystem_name", ""),
        "status": item.get("status", "draft"),
        "line": st.get("line", ""),
        "station": st.get("station", ""),
        "device_count": len(item.get("devices") or []),
        "programmable_count": len([d for d in (item.get("devices") or []) if d.get("programmable")]),
        "registered_at": item.get("registered_at", ""),
        "updated_at": item.get("updated_at", ""),
        "verify_summary": pub["verify_summary"],
    }


def _find_bench(reg: dict, tb_id: str) -> Optional[dict]:
    for item in reg.get("testbenches", []):
        if item.get("id") == tb_id:
            return item
    return None


# ==================== 对外只读访问 (供装备助手 / 其他模块复用) ====================


def registry_path() -> Path:
    return _REGISTRY_FILE


def load_registry() -> dict:
    with _LOCK:
        return _load_registry()


def find_device(bench_id: str, device_id: str) -> tuple[Optional[dict], Optional[dict]]:
    """按 (测试台编号, 设备编号) 取注册库中的对象: 返回 (测试台, 设备)"""
    with _LOCK:
        reg = _load_registry()
        item = _find_bench(reg, bench_id)
        if not item:
            return None, None
        dev = next((d for d in (item.get("devices") or []) if d.get("id") == device_id), None)
        return item, dev


# ==================== 装备属性配置 (设备属性读写) ====================

HOST_RE = re.compile(r"^(?:(?:\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9][A-Za-z0-9._-]{0,62})$")
SERIAL_PORT_RE = re.compile(r"^(?:COM[0-9]{1,3}|/dev/tty[A-Za-z0-9._-]{1,40})$")
ADDRESS_RE = re.compile(r"^[A-Za-z0-9:;._/\- ]{1,80}$")
CHANNEL_RE = re.compile(r"^[A-Za-z0-9._\-]{0,16}$")

# 允许通过属性配置接口修改的字段
PATCHABLE_FIELDS = ("host", "port", "protocol", "serial_port", "baudrate", "address", "channel", "note")


def _bench_lines(reg: dict) -> tuple[list[dict], list[dict]]:
    """装备属性配置页数据源: (测试台摘要列表, 拍平后的设备行)

    设备行 = 测试台注册时 BOM 清单中的设备 + 当前已保存的连接配置 + 所属测试台上下文。
    """
    benches: list[dict] = []
    rows: list[dict] = []
    for item in reg.get("testbenches", []):
        pub = _public(item)
        st = item.get("station") or {}
        devs = item.get("devices") or []
        benches.append(
            {
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "serial": item.get("serial", ""),
                "status": item.get("status", "draft"),
                "preset_id": item.get("preset_id", ""),
                "preset_name": item.get("preset_name", ""),
                "product_name": pub.get("product_name", ""),
                "subsystem_name": pub.get("subsystem_name", ""),
                "line": st.get("line", ""),
                "station": st.get("station", ""),
                "device_count": len(devs),
                "programmable_count": len([d for d in devs if d.get("programmable")]),
                "configured_count": len([d for d in devs if d.get("configured")]),
                "verify_overall": (item.get("verification") or {}).get("overall", ""),
                "updated_at": item.get("updated_at", ""),
            }
        )
        for idx, dev in enumerate(devs):
            row = dict(dev)
            row.update(
                {
                    "bench_id": item.get("id", ""),
                    "bench_title": item.get("title", ""),
                    "bench_serial": item.get("serial", ""),
                    "bench_status": item.get("status", "draft"),
                    "preset_id": item.get("preset_id", ""),
                    "preset_name": item.get("preset_name", ""),
                    "product_name": pub.get("product_name", ""),
                    "subsystem_name": pub.get("subsystem_name", ""),
                    "line": st.get("line", ""),
                    "station": st.get("station", ""),
                    "index": idx,
                    "config_history_count": len(dev.get("config_history") or []),
                }
            )
            rows.append(row)
    benches.sort(key=lambda b: b.get("updated_at") or "", reverse=True)
    return benches, rows


def _resource_of(dev: dict) -> str:
    """设备当前连接资源串 (用于展示 / 冲突排查)"""
    iface = str(dev.get("interface", "")).upper()
    if iface in NET_INTERFACES:
        host = str(dev.get("host") or "").strip()
        port = _as_int(dev.get("port"))
        if host and port:
            return f"TCPIP::{host}::{port}::SOCKET"
        return host or ""
    if iface in SERIAL_INTERFACES:
        sp = str(dev.get("serial_port") or "").strip()
        br = _as_int(dev.get("baudrate"))
        if sp:
            return f"ASRL::{sp}::{br or ''}".rstrip(":")
        return ""
    return str(dev.get("address") or "").strip()


def _validate_device_patch(dev: dict, patch: dict) -> list[str]:
    """校验待写入的设备属性 (与前端表单校验保持同一套规则)"""
    iface = str(dev.get("interface", "")).upper()
    errs: list[str] = []

    if "host" in patch:
        host = str(patch.get("host") or "").strip()
        if iface in NET_INTERFACES and not host:
            errs.append("网口设备的 IP / 主机名不能为空")
        elif host and not HOST_RE.match(host):
            errs.append(f"IP / 主机名格式非法: {host}")

    if "port" in patch:
        port = _as_int(patch.get("port"))
        if iface in NET_INTERFACES and port is None:
            errs.append("网口设备的端口不能为空")
        elif port is not None and not 1 <= port <= 65535:
            errs.append(f"端口超出范围 (1-65535): {port}")

    if "serial_port" in patch:
        sp = str(patch.get("serial_port") or "").strip()
        if iface in SERIAL_INTERFACES and not sp:
            errs.append("串口设备的串口号不能为空")
        elif sp and not SERIAL_PORT_RE.match(sp):
            errs.append(f"串口号格式非法 (形如 COM3): {sp}")

    if "baudrate" in patch:
        br = _as_int(patch.get("baudrate"))
        if br is not None and not 300 <= br <= 1000000:
            errs.append(f"波特率超出范围 (300-1000000): {br}")

    if "address" in patch:
        addr = str(patch.get("address") or "").strip()
        if iface in ("USB", "GPIB") and not addr and dev.get("programmable"):
            errs.append(f"{iface} 接口设备需填写资源地址 (形如 GPIB0::22::INSTR)")
        elif addr and not ADDRESS_RE.match(addr):
            errs.append("资源地址含非法字符")

    if "channel" in patch:
        ch = str(patch.get("channel") or "").strip()
        if ch and not CHANNEL_RE.match(ch):
            errs.append("通道号含非法字符")

    if "protocol" in patch and len(str(patch.get("protocol") or "").strip()) > 40:
        errs.append("协议名称过长 (≤40 字符)")

    return errs


def _conflict_with_peers(devices: list[dict], dev: dict, patch: dict) -> list[str]:
    """同一测试台内网口地址冲突检查 (IP:端口 不可被两台设备占用)"""
    iface = str(dev.get("interface", "")).upper()
    if iface not in NET_INTERFACES:
        return []
    host = str(patch.get("host", dev.get("host")) or "").strip().lower()
    port = _as_int(patch.get("port", dev.get("port")))
    if not host or not port:
        return []
    for other in devices:
        if other is dev:
            continue
        if str(other.get("interface", "")).upper() not in NET_INTERFACES:
            continue
        if str(other.get("host") or "").strip().lower() == host and _as_int(other.get("port")) == port:
            return [f"地址冲突: {host}:{port} 已被同一测试台的「{other.get('name')}」占用"]
    return []


def _apply_device_patch(dev: dict, patch: dict, preset_id: str) -> dict:
    """写入设备属性, 返回变更明细 (前后值对比)"""
    before = {k: dev.get(k) for k in PATCHABLE_FIELDS}

    if patch.get("reset_defaults"):
        bom = ((_bom_of_presets().get(preset_id) or {}).get(dev.get("id", ""))) or {}
        dev["host"] = bom.get("default_host", "")
        dev["port"] = _as_int(bom.get("default_port"))
        dev["serial_port"] = bom.get("default_serial_port", "")
        dev["baudrate"] = _as_int(bom.get("default_baudrate"))
        dev["address"] = bom.get("default_address", "")
        dev["channel"] = bom.get("default_channel", "")
        dev["protocol"] = bom.get("protocol", "") or dev.get("protocol", "")
    else:
        if "host" in patch:
            dev["host"] = str(patch.get("host") or "").strip()
        if "port" in patch:
            dev["port"] = _as_int(patch.get("port"))
        if "serial_port" in patch:
            dev["serial_port"] = str(patch.get("serial_port") or "").strip()
        if "baudrate" in patch:
            dev["baudrate"] = _as_int(patch.get("baudrate")) or _as_int(dev.get("baudrate"))
        if "address" in patch:
            dev["address"] = str(patch.get("address") or "").strip()
        if "channel" in patch:
            dev["channel"] = str(patch.get("channel") or "").strip()
        if "protocol" in patch:
            dev["protocol"] = str(patch.get("protocol") or "").strip()
        if "note" in patch:
            dev["note"] = str(patch.get("note") or "")[:200]

    dev["configured"] = _device_configured(dev)
    dev["updated_at"] = _now()

    changes = {k: {"from": before.get(k), "to": dev.get(k)} for k in PATCHABLE_FIELDS if before.get(k) != dev.get(k)}
    if changes:
        hist = (dev.get("config_history") or [])[-9:]
        hist.append({"at": dev["updated_at"], "reset_defaults": bool(patch.get("reset_defaults")), "changes": changes})
        dev["config_history"] = hist
    return changes


# ==================== 连通性自检 ====================


def _tcp_probe(host: str, port: int, timeout: float) -> tuple[bool, Optional[float], str]:
    """TCP 连通性探测: 只做三次握手 + 读一次超时探测, 不发送具体 SCPI 指令"""
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            # 连通后再快速试探一次应答, 不阻塞整个自检流程
            sock.settimeout(min(timeout, 0.3))
            try:
                sock.recv(1)
            except Exception:
                pass  # 设备不应答也视为端口可达
        return True, round((time.perf_counter() - t0) * 1000, 1), ""
    except socket.timeout:
        return False, None, f"连接超时 (>{timeout}s)"
    except OSError as e:
        return False, None, f"{e}"


def _serial_probe(port_name: str, baudrate: Optional[int], timeout: float) -> tuple[Optional[bool], str]:
    """串口探测: 依赖 pyserial, 未安装时返回 None 表示跳过"""
    try:
        import serial  # type: ignore
    except Exception:
        return None, "未安装 pyserial, 跳过串口连通检查 (可 pip install pyserial 后重试)"
    try:
        with serial.Serial(port_name, baudrate or 9600, timeout=timeout) as ser:
            return bool(ser.is_open), ""
    except Exception as e:
        return False, f"串口打开失败: {e}"


def _probe_device(dev: dict, timeout: float, mode: str) -> dict:
    iface = str(dev.get("interface") or "NONE").upper()
    item = {
        "key": f"device::{dev.get('id', dev.get('name', ''))}",
        "kind": "device",
        "label": dev.get("name") or dev.get("id") or "未命名设备",
        "model": dev.get("model", ""),
        "category": dev.get("category", ""),
        "interface": iface,
        "programmable": bool(dev.get("programmable")),
        "required": bool(dev.get("required", True)),
        "target": "",
        "status": "skip",
        "detail": "",
        "latency_ms": None,
        "simulated": False,
    }
    if mode == "simulate":
        item.update(status="pass", simulated=True, latency_ms=0.0, detail="模拟自检：未发起真实连接（离线演示模式）")
        return item

    if iface in NET_INTERFACES:
        host = str(dev.get("host") or "").strip()
        port = dev.get("port")
        if not host or not port:
            item.update(status="fail", detail="未配置 IP / 端口")
            return item
        item["target"] = f"{host}:{port}"
        ok, ms, err = _tcp_probe(host, int(port), timeout)
        if ok:
            item.update(status="pass", latency_ms=ms, detail=f"TCP 连通 ({ms} ms)")
        else:
            item.update(status="fail", latency_ms=ms, detail=err)
        return item

    if iface in SERIAL_INTERFACES:
        port_name = str(dev.get("serial_port") or "").strip()
        if not port_name:
            item.update(status="fail", detail="未配置串口号")
            return item
        item["target"] = f"{port_name}@{dev.get('baudrate') or 9600}"
        ok, msg = _serial_probe(port_name, dev.get("baudrate"), timeout)
        if ok is None:
            item.update(status="skip", detail=msg)
        elif ok:
            item.update(status="pass", latency_ms=0.0, detail="串口已打开")
        else:
            item.update(status="fail", detail=msg)
        return item

    item.update(status="skip", detail=f"{iface or '未知'} 接口不支持自动连通检查, 请按自检清单人工确认")
    return item


def _static_checks(item: dict) -> list[dict]:
    devices = item.get("devices") or []
    station = item.get("station") or {}
    serial = item.get("serial", "")
    checks: list[dict] = []

    checks.append(
        {
            "key": "serial_format",
            "kind": "config",
            "label": "测试台编号格式 (SPMTS + 12 位数字)",
            "status": "pass" if SERIAL_RE.match(serial) else "fail",
            "detail": serial if SERIAL_RE.match(serial) else f"编号非法: '{serial}'",
        }
    )
    checks.append(
        {
            "key": "title",
            "kind": "config",
            "label": "测试台名称已填写",
            "status": "pass" if (item.get("title") or "").strip() else "fail",
            "detail": item.get("title") or "未填写",
        }
    )
    line_ok = bool(str(station.get("line") or "").strip()) and bool(str(station.get("station") or "").strip())
    checks.append(
        {
            "key": "station_info",
            "kind": "config",
            "label": "产线 / 工位信息完整",
            "status": "pass" if line_ok else "fail",
            "detail": f"产线 {station.get('line') or '—'} / 工位 {station.get('station') or '—'}",
        }
    )

    prog = [d for d in devices if d.get("programmable")]
    prog_ok = [d for d in prog if _device_configured(d)]
    checks.append(
        {
            "key": "programmable_configured",
            "kind": "config",
            "label": "可编程设备配置完整 (IP/端口 或 串口)",
            "status": "pass" if prog and len(prog_ok) == len(prog) else ("fail" if not prog else "fail"),
            "detail": f"已配置 {len(prog_ok)}/{len(prog)} 台可编程设备",
        }
    )

    # IP 冲突: 同一 IP:端口 被多台设备占用
    seen: dict[str, list[str]] = {}
    for d in devices:
        if str(d.get("interface", "")).upper() in NET_INTERFACES and d.get("host"):
            key = f"{str(d['host']).strip()}:{d.get('port')}"
            seen.setdefault(key, []).append(d.get("name") or d.get("id") or "?")
    conflicts = {k: v for k, v in seen.items() if len(v) > 1}
    checks.append(
        {
            "key": "address_conflict",
            "kind": "config",
            "label": "设备地址无冲突",
            "status": "fail" if conflicts else "pass",
            "detail": "、".join(f"{k} 被 {'/'.join(v)} 同时占用" for k, v in conflicts.items()) if conflicts else f"{len(seen)} 个网络地址唯一",
        }
    )

    missing = [d for d in devices if d.get("required") and d.get("programmable") and not _device_configured(d)]
    checks.append(
        {
            "key": "required_devices",
            "kind": "config",
            "label": "必备设备均已配置",
            "status": "pass" if not missing else "fail",
            "detail": "、".join(d.get("name", "?") for d in missing) if missing else "全部必备设备已配置",
        }
    )
    return checks


def _run_verification(item: dict, mode: str, timeout: float) -> dict:
    devices = item.get("devices") or []
    checklist = _static_checks(item)
    for dev in devices:
        checklist.append(_probe_device(dev, timeout, mode))

    total = len(checklist)
    passed = len([c for c in checklist if c["status"] == "pass"])
    failed = len([c for c in checklist if c["status"] == "fail"])
    skipped = len([c for c in checklist if c["status"] == "skip"])
    overall = "fail" if failed else ("warn" if skipped else "pass")

    result = {
        "checked_at": _now(),
        "mode": mode,
        "timeout": timeout,
        "overall": overall,
        "summary": {"total": total, "pass": passed, "fail": failed, "skip": skipped},
        "checklist": checklist,
        "conclusion": {
            "pass": "全部检查项通过，测试台可投入使用",
            "warn": "存在跳过项（多为不支持自动探测的接口），请按清单人工确认后投用",
            "fail": "存在失败项，请修正设备配置或网络/接口连接后重新自检",
        }[overall],
    }
    return result


def _default_devices(preset: dict) -> list[dict]:
    """默认测试台的设备列表: 直接取预设 BOM 默认值, 并计算配置完整度"""
    devices = _devices_from_preset(preset)
    for dev in devices:
        dev["configured"] = _device_configured(dev)
    return devices


def _seed_default_testbenches(force: bool = False) -> list[dict]:
    """写入默认测试台 (开箱即已注册)。

    - 幂等: 已存在同编号测试台则不重复创建
    - 删除后不复活: 注册库用 seeded_defaults 记录已种过的 preset_id; force=True 时忽略该记录
    - 自检先走一次离线模拟, 保证导航页一进去就有自检清单
    """
    created: list[dict] = []
    with _LOCK:
        reg = _load_registry()
        benches = reg.get("testbenches", [])
        seeded = list(reg.get(SEEDED_KEY) or [])
        for spec in DEFAULT_TESTBENCHES:
            preset_id = spec.get("preset_id", "")
            preset = _find_preset(preset_id)
            if not preset:
                continue
            serial = _norm_serial(spec.get("serial", ""))
            if any(_norm_serial(b.get("serial", "")) == serial for b in benches):
                continue
            if not force and preset_id in seeded:
                continue

            item = {
                "id": "tb_" + uuid.uuid4().hex[:10],
                "preset_id": preset_id,
                "preset_name": preset.get("name", ""),
                "title": spec.get("title", ""),
                "serial": serial,
                "station": dict(spec.get("station") or {}),
                "devices": _default_devices(preset),
                "step": 3,
                "status": "registered",
                "created_at": _now(),
                "updated_at": _now(),
                "registered_at": _now(),
                "verification": None,
                "verify_history": [],
                "seeded": True,
            }
            mode = spec.get("verify_mode", "simulate")
            try:
                result = _run_verification(item, mode, 1.0)
            except Exception:
                result = None
            if result:
                item["verification"] = result
                item["verify_history"] = [
                    {
                        "at": result["checked_at"],
                        "mode": mode,
                        "overall": result["overall"],
                        "pass": result["summary"]["pass"],
                        "fail": result["summary"]["fail"],
                        "skip": result["summary"]["skip"],
                    }
                ]

            benches.append(item)
            if preset_id not in seeded:
                seeded.append(preset_id)
            created.append(item)

        if created:
            reg["testbenches"] = benches
            reg[SEEDED_KEY] = seeded
            _save_registry(reg)
    return created


# ==================== 路由注册 ====================


def register_routes(app: FastAPI, tps_dir: Path) -> None:
    global _TPS_DIR, _PRESET_FILE, _REGISTRY_FILE
    _TPS_DIR = Path(tps_dir)
    _PRESET_FILE = _TPS_DIR / "testbench_presets.json"
    _REGISTRY_FILE = _TPS_DIR / "testbenches.json"

    # 默认测试台: 开箱即已注册 (幂等; 失败不影响服务启动)
    try:
        _seed_default_testbenches()
    except Exception:
        pass

    # ---------- 预设测试台类型 ----------

    @app.get("/api/testbench/presets")
    def list_presets():
        try:
            presets = [_preset_summary(p) for p in _load_presets()]
            return {"success": True, "count": len(presets), "presets": presets}
        except Exception as e:
            return {"success": False, "message": f"读取预设测试台类型失败: {e}", "presets": []}

    @app.get("/api/testbench/tree")
    def testbench_tree():
        """预设测试台类型级联树: 产品 -> 子系统 -> 预设类型"""
        try:
            tree = _preset_tree()
            return {
                "success": True,
                "product_count": len(tree),
                "preset_count": sum(n["preset_count"] for n in tree),
                "tree": tree,
            }
        except Exception as e:
            return {"success": False, "message": f"读取预设测试台类型树失败: {e}", "tree": []}

    @app.get("/api/testbench/presets/{preset_id}")
    def get_preset(preset_id: str):
        preset = _find_preset(preset_id)
        if not preset:
            return {"success": False, "message": f"预设测试台类型不存在: {preset_id}"}
        return {"success": True, "preset": preset, "summary": _preset_summary(preset)}

    # ---------- 已注册测试台 ----------

    @app.get("/api/testbenches")
    def list_testbenches(status: Optional[str] = None):
        try:
            with _LOCK:
                reg = _load_registry()
            items = [_tag(t) for t in reg.get("testbenches", [])]
            if status:
                items = [t for t in items if t.get("status") == status]
            items.sort(key=lambda t: t.get("updated_at") or "", reverse=True)
            registered = [t for t in items if t.get("status") == "registered"]
            return {
                "success": True,
                "count": len(items),
                "registered_count": len(registered),
                "draft_count": len([t for t in items if t.get("status") != "registered"]),
                "device_total": sum(t.get("device_count", 0) for t in items),
                "programmable_total": sum(t.get("programmable_count", 0) for t in items),
                "testbenches": items,
            }
        except Exception as e:
            return {"success": False, "message": f"读取测试台注册表失败: {e}", "testbenches": []}

    @app.get("/api/testbenches/{tb_id}")
    def get_testbench(tb_id: str):
        with _LOCK:
            reg = _load_registry()
        item = _find_bench(reg, tb_id)
        if not item:
            return {"success": False, "message": f"测试台不存在: {tb_id}"}
        return {"success": True, "testbench": _public(item)}

    @app.post("/api/testbenches")
    def save_testbench(req: TestbenchSaveRequest):
        """创建或更新测试台 (三步注册流程的每步都可调用; 传 id 为更新)"""
        serial = _norm_serial(req.serial)
        title = (req.title or "").strip()
        step = max(1, min(3, int(req.step or 1)))
        status = "registered" if req.status == "registered" else "draft"

        with _LOCK:
            reg = _load_registry()
            benches = reg.get("testbenches", [])

            # 更新分支
            item = _find_bench(reg, req.id) if req.id else None
            if not item:
                item = {
                    "id": "tb_" + uuid.uuid4().hex[:10],
                    "created_at": _now(),
                    "registered_at": None,
                    "verification": None,
                    "verify_history": [],
                }
                benches.append(item)

            # 字段校验
            errors: list[str] = []
            if not req.preset_id:
                errors.append("未选择预设测试台类型")
            preset = _find_preset(req.preset_id) if req.preset_id else None
            if req.preset_id and not preset:
                errors.append(f"预设测试台类型不存在: {req.preset_id}")
            if step >= 1 and not title:
                errors.append("测试台名称不能为空")
            if step >= 1 and not SERIAL_RE.match(serial):
                errors.append("测试台编号格式非法 (应为 SPMTS + 12 位数字)")
            if SERIAL_RE.match(serial):
                dup = [b for b in benches if b is not item and _norm_serial(b.get("serial", "")) == serial]
                if dup:
                    errors.append(f"测试台编号已被占用: {serial}")
            if status == "registered":
                if step < 3:
                    errors.append("注册需完成第三步 (注册并验证) 后才能提交")
                st = req.station or {}
                if not str(st.get("line") or "").strip() or not str(st.get("station") or "").strip():
                    errors.append("产线 / 工位信息不完整")
            if errors:
                return {"success": False, "message": "；".join(errors), "errors": errors}

            preset_id = req.preset_id
            devices = _merge_devices(req.devices, preset_id)
            if not devices and preset:
                devices = _devices_from_preset(preset)

            item.update(
                {
                    "preset_id": preset_id,
                    "preset_name": req.preset_name or (preset or {}).get("name", ""),
                    "title": title,
                    "serial": serial,
                    "station": req.station or {},
                    "devices": devices,
                    "step": step,
                    "status": status,
                    "updated_at": _now(),
                }
            )
            if status == "registered" and not item.get("registered_at"):
                item["registered_at"] = _now()

            _save_registry(reg)
            return {
                "success": True,
                "message": "测试台已注册" if status == "registered" else "草稿已保存",
                "testbench": _public(item),
            }

    @app.delete("/api/testbenches/{tb_id}")
    def delete_testbench(tb_id: str):
        with _LOCK:
            reg = _load_registry()
            benches = reg.get("testbenches", [])
            item = _find_bench(reg, tb_id)
            if not item:
                return {"success": False, "message": f"测试台不存在: {tb_id}"}
            reg["testbenches"] = [b for b in benches if b is not item]
            _save_registry(reg)
        return {"success": True, "message": f"已删除测试台 {item.get('title') or tb_id}"}

    @app.post("/api/testbenches/restore-defaults")
    def restore_default_testbenches():
        """重建默认测试台 (用于删除后恢复出厂演示数据)"""
        try:
            created = _seed_default_testbenches(force=True)
        except Exception as exc:
            return {"success": False, "message": f"重建默认测试台失败: {exc}"}
        if not created:
            return {"success": True, "message": "默认测试台已存在, 无需重建", "created": []}
        return {
            "success": True,
            "message": f"已重建 {len(created)} 台默认测试台",
            "created": [
                {"id": b.get("id"), "title": b.get("title"), "serial": b.get("serial"), "preset_id": b.get("preset_id")}
                for b in created
            ],
        }

    # ---------- 装备属性配置 (设备属性读写) ----------

    @app.get("/api/testbench/devices")
    def list_bench_devices(bench_id: Optional[str] = None, programmable_only: bool = False):
        """装备属性配置页数据源: 来自测试台注册 BOM 清单的设备 (拍平 + 带测试台上下文)"""
        try:
            with _LOCK:
                reg = _load_registry()
            benches, rows = _bench_lines(reg)
            if bench_id:
                rows = [r for r in rows if r.get("bench_id") == bench_id]
                benches = [b for b in benches if b.get("id") == bench_id]
            if programmable_only:
                rows = [r for r in rows if r.get("programmable")]
            for row in rows:
                row["resource"] = _resource_of(row)
            return {
                "success": True,
                "count": len(rows),
                "programmable_count": len([r for r in rows if r.get("programmable")]),
                "configured_count": len([r for r in rows if r.get("configured")]),
                "bench_count": len(benches),
                "benches": benches,
                "devices": rows,
            }
        except Exception as e:
            return {"success": False, "message": f"读取装备属性失败: {e}", "benches": [], "devices": []}

    @app.patch("/api/testbenches/{tb_id}/devices/{device_id}")
    def patch_device(tb_id: str, device_id: str, req: DeviceConfigPatch):
        """修改可编程设备的连接属性 (右键菜单 → 修改设备属性)"""
        with _LOCK:
            reg = _load_registry()
            item = _find_bench(reg, tb_id)
            if not item:
                return {"success": False, "message": f"测试台不存在: {tb_id}"}
            devices = item.get("devices") or []
            dev = next((d for d in devices if d.get("id") == device_id), None)
            if not dev:
                return {"success": False, "message": f"设备不存在: {device_id}"}
            if not dev.get("programmable") and not req.reset_defaults:
                return {"success": False, "message": f"「{dev.get('name')}」不是可编程设备, 连接属性不可修改"}

            try:
                patch = req.model_dump(exclude_unset=True)  # pydantic v2
            except AttributeError:  # pragma: no cover - pydantic v1 兼容
                patch = req.dict(exclude_unset=True)

            errs = _validate_device_patch(dev, patch) + _conflict_with_peers(devices, dev, patch)
            if errs:
                return {"success": False, "message": "；".join(errs), "errors": errs}

            changes = _apply_device_patch(dev, patch, item.get("preset_id", ""))
            item["updated_at"] = _now()
            _save_registry(reg)
            out = dict(dev)
            out["resource"] = _resource_of(dev)
            return {
                "success": True,
                "message": f"已更新「{dev.get('name')}」的连接属性",
                "changes": changes,
                "device": out,
                "testbench": _public(item),
            }

    @app.post("/api/testbenches/{tb_id}/verify")
    def verify_testbench(tb_id: str, req: Optional[VerifyRequest] = None):
        """注册第三步: 登记设备连通性自检, 返回逐项自检清单"""
        req = req or VerifyRequest()
        mode = "simulate" if (req.mode or "real").lower() == "simulate" else "real"
        timeout = float(req.timeout or 2.0)
        timeout = min(max(timeout, 0.2), 10.0)

        with _LOCK:
            reg = _load_registry()
            item = _find_bench(reg, tb_id)
            if not item:
                return {"success": False, "message": f"测试台不存在: {tb_id}"}

            result = _run_verification(item, mode, timeout)
            item["verification"] = result
            item["verify_history"] = (item.get("verify_history") or [])[
                -9:
            ] + [
                {
                    "at": result["checked_at"],
                    "mode": mode,
                    "overall": result["overall"],
                    "pass": result["summary"]["pass"],
                    "fail": result["summary"]["fail"],
                    "skip": result["summary"]["skip"],
                }
            ]
            item["updated_at"] = _now()
            _save_registry(reg)

        return {"success": True, "result": result, "testbench": _public(item)}

    # ---------- 汇总看板 (导航页指标块) ----------

    @app.get("/api/testbench/overview")
    def testbench_overview():
        with _LOCK:
            reg = _load_registry()
        benches = reg.get("testbenches", [])
        registered = [b for b in benches if b.get("status") == "registered"]
        tree_now = _preset_tree()
        verified = [b for b in registered if (b.get("verification") or {}).get("overall") == "pass"]
        warned = [b for b in registered if (b.get("verification") or {}).get("overall") == "warn"]
        failed = [b for b in registered if (b.get("verification") or {}).get("overall") == "fail"]
        return {
            "success": True,
            "preset_count": len(_load_presets()),
            "product_count": len(tree_now),
            "subsystem_count": sum(len(n.get("subsystems") or []) for n in tree_now),
            "registered_count": len(registered),
            "draft_count": len(benches) - len(registered),
            "device_total": sum(len(b.get("devices") or []) for b in registered),
            "programmable_total": sum(len([d for d in (b.get("devices") or []) if d.get("programmable")]) for b in registered),
            "verify": {"pass": len(verified), "warn": len(warned), "fail": len(failed), "unchecked": len(registered) - len(verified) - len(warned) - len(failed)},
        }
