"""ATE 元数据管理 - 装备树 + 测试台 BOM (独立后端模块)

职责:
  1) 装备树管理: 产品 / 子系统 / 测试台类型 三级节点的查询与增删改
  2) 测试台 BOM 管理: 每个测试台类型一张可编辑 BOM (测试台属性 + 设备清单)
  3) 作为「预设测试台类型」的唯一数据源, 供 新建测试台注册流程 / 测试台导航 读取

数据目录 (后端的 tree 文件夹; 可用环境变量 ATE_TREE_DIR 覆盖):
  backend/tree/tree.json              装备树 + 测试台类型属性 (不含 BOM)
  backend/tree/bom/<preset_id>.json   测试台类型的 BOM 清单 (一台设备一条记录)
  backend/tree/README.md              目录职责说明

首次启动种子 (把既有预设纳入管理):
  若 tree.json 不存在, 自动从 backend/testresource/testbench_presets.json 导入
  产品 / 子系统 / 测试台类型属性 / BOM; 导入只发生一次, 之后以 tree 文件夹为准。
  需要重新导入时调用 POST /api/metadata/reseed (mode=merge 补齐缺失 / mode=reset 全量重建)。

与 testbench_registry 的关系:
  testbench_registry 的预设测试台类型优先从本模块读取 (load_presets / load_meta),
  读取失败或 tree 文件夹不可用时回退到 testresource/testbench_presets.json (只读)。
  已注册测试台仍把设备配置保存在自身注册记录中, 管理 BOM 不会静默改写已注册测试台。

接口 (全部挂在 /api/metadata 下):
  GET    /api/metadata/overview                                  指标块数据 + 存储路径
  GET    /api/metadata/tree                                      装备树 (产品/子系统/类型 + BOM 摘要)
  GET    /api/metadata/store                                     原始 tree.json (排障 / 导出)
  POST   /api/metadata/reseed                                    从预设文件重新导入
  POST   /api/metadata/products           DELETE /products/{id}          产品节点
  POST   /api/metadata/subsystems         DELETE /subsystems/{id}        子系统节点
  POST   /api/metadata/presets            DELETE /presets/{id}           测试台类型节点 (含属性)
  GET    /api/metadata/presets/{id}                              测试台类型详情 (属性 + BOM + 引用统计)
  GET    /api/metadata/presets/{id}/bom                          BOM 清单
  PUT    /api/metadata/presets/{id}/bom                          整张 BOM 保存 (可选同时保存属性)
  POST   /api/metadata/presets/{id}/bom/items                    追加一台设备
  DELETE /api/metadata/presets/{id}/bom/items/{device_id}        删除一台设备
  GET    /api/metadata/device-catalog                            可复用设备模板库 (按型号去重)

挂载方式 (在 main.py 末尾):
  import metadata_registry
  metadata_registry.register_routes(app, BASE_DIR, TPS_DIR)
"""

from __future__ import annotations

import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel

# ---------------- 常量 ----------------

# 节点 ID: 字母/数字开头, 允许 . _ - (与既有预设 ID 风格一致, 如 PB-PWR-01)
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")
PRODUCT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
SUBSYSTEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")

NET_INTERFACES = {"LAN", "ETHERNET", "TCP", "IP"}
SERIAL_INTERFACES = {"SERIAL", "RS232", "RS485", "UART", "COM"}
PASSIVE_INTERFACES = {"USB", "GPIB", "NONE", "MANUAL", ""}
ALL_INTERFACES = NET_INTERFACES | SERIAL_INTERFACES | PASSIVE_INTERFACES

HOST_RE = re.compile(r"^(?:(?:\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9][A-Za-z0-9._-]{0,62})$")
SERIAL_PORT_RE = re.compile(r"^(?:COM[0-9]{1,3}|/dev/tty[A-Za-z0-9._-]{1,40})$")

_LOCK = threading.RLock()

# 运行期由 register_routes 注入
_BASE_DIR: Path = Path(".")
_TPS_DIR: Path = Path("testresource")
_TREE_DIR: Path = Path("tree")
_TREE_FILE: Path = Path("tree") / "tree.json"
_BOM_DIR: Path = Path("tree") / "bom"
_SEED_FILE: Path = Path("testresource") / "testbench_presets.json"
_REGISTRY_FILE: Path = Path("testresource") / "testbenches.json"


# ---------------- 通用工具 ----------------


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Path, default: Any) -> Any:
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    """原子写: 先写 .tmp 再替换, 避免写入中断损坏元数据"""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _text(value: Any, limit: int = 400) -> str:
    return str(value or "").strip()[:limit]


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _safe_bom_filename(preset_id: str) -> str:
    """BOM 文件名只允许安全字符, 防止路径穿越"""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", _text(preset_id, 64))
    return f"{safe or 'unnamed'}.json"


def _preset_type_file_path(preset_id: str) -> Path:
    """测试台类型节点文件 (tree 文件夹下 bom/ 之外的元数据文件)"""
    return _BOM_DIR.parent / "preset_types" / _safe_bom_filename(preset_id)


# ---------------- 存储读写 ----------------


def _default_store() -> dict:
    return {
        "version": 1,
        "managed_by": "元数据管理 · 装备树管理 / 测试台BOM管理",
        "created_at": _now(),
        "updated_at": _now(),
        "seed_source": "",
        "products": [],
        "subsystems": [],
        "preset_types": [],
    }


def _load_store() -> dict:
    data = _read_json(_TREE_FILE, None)
    if not isinstance(data, dict):
        data = _default_store()
    for key in ("products", "subsystems", "preset_types"):
        if not isinstance(data.get(key), list):
            data[key] = []
    data.setdefault("version", 1)
    return data


def _save_store(data: dict) -> None:
    data["updated_at"] = _now()
    _write_json(_TREE_FILE, data)


def _bom_path(preset_id: str) -> Path:
    return _BOM_DIR / _safe_bom_filename(preset_id)


def _load_bom(preset_id: str) -> list[dict]:
    data = _read_json(_bom_path(preset_id), None)
    if isinstance(data, dict):
        items = data.get("items") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    return [x for x in items if isinstance(x, dict)]


def _save_bom(preset_id: str, items: list[dict]) -> None:
    _write_json(
        _bom_path(preset_id),
        {
            "version": 1,
            "preset_id": preset_id,
            "updated_at": _now(),
            "count": len(items),
            "items": items,
        },
    )


def _registered_usage() -> dict[str, int]:
    """{preset_id: 已注册/草稿测试台引用的数量} — 用于删除/改动前的引用提示"""
    reg = _read_json(_REGISTRY_FILE, {})
    rows = reg.get("testbenches") if isinstance(reg, dict) else []
    usage: dict[str, int] = {}
    for b in rows or []:
        if not isinstance(b, dict):
            continue
        pid = _text(b.get("preset_id"), 64)
        if pid:
            usage[pid] = usage.get(pid, 0) + 1
    return usage


# ---------------- 种子导入 (把既有预设纳入管理) ----------------


def _seed_from_presets() -> tuple[list[dict], list[dict], list[dict], dict[str, list[dict]]]:
    data = _read_json(_SEED_FILE, {})
    if not isinstance(data, dict):
        data = {}
    raw_products = data.get("products") or []
    raw_subsystems = data.get("subsystems") or []
    raw_presets = data.get("presets") or []

    products: list[dict] = []
    pname: dict[str, str] = {}
    for m in raw_products:
        if not isinstance(m, dict):
            continue
        pid = _text(m.get("id"), 48)
        if not pid:
            continue
        name = _text(m.get("name"), 120) or pid
        products.append({"id": pid, "name": name, "note": _text(m.get("note"), 200)})
        pname[pid] = name

    subsystems: list[dict] = []
    sname: dict[str, str] = {}
    for m in raw_subsystems:
        if not isinstance(m, dict):
            continue
        sid = _text(m.get("id"), 48)
        if not sid:
            continue
        name = _text(m.get("name"), 120) or sid
        prods = [str(x) for x in (m.get("products") or []) if str(x)]
        subsystems.append({"id": sid, "name": name, "products": prods, "note": _text(m.get("note"), 200)})
        sname[sid] = name

    preset_types: list[dict] = []
    boms: dict[str, list[dict]] = {}
    for p in raw_presets:
        if not isinstance(p, dict):
            continue
        pid = _text(p.get("id"), 64)
        if not pid:
            continue
        product = _text(p.get("product"), 48)
        subsystem = _text(p.get("subsystem"), 48)
        preset_types.append(
            {
                "id": pid,
                "name": _text(p.get("name"), 120) or pid,
                "category": _text(p.get("category"), 60),
                "description": _text(p.get("description"), 600),
                "typical_dut": _text(p.get("typical_dut"), 200),
                "recommended_cycle": _text(p.get("recommended_cycle"), 60),
                "product": product,
                "subsystem": subsystem,
                "updated_at": _now(),
            }
        )
        # 产品 / 子系统 兜底补全 (预设里出现但元数据未声明的也建节点)
        if product and product not in pname:
            products.append({"id": product, "name": _text(p.get("product_name"), 120) or product, "note": ""})
            pname[product] = _text(p.get("product_name"), 120) or product
        if subsystem and subsystem not in sname:
            subsystems.append(
                {
                    "id": subsystem,
                    "name": _text(p.get("subsystem_name"), 120) or subsystem,
                    "products": [product] if product else [],
                    "note": "",
                }
            )
            sname[subsystem] = _text(p.get("subsystem_name"), 120) or subsystem
        boms[pid] = [_norm_bom_item(d) for d in (p.get("bom") or []) if isinstance(d, dict)]
    return products, subsystems, preset_types, boms


def ensure_store(force: bool = False, mode: str = "merge") -> dict:
    """确保 tree 文件夹可用; tree.json 缺失时按种子导入。

    force=True 时重新导入 (mode=merge 补齐缺失节点/文件, mode=reset 全量重建)。
    返回 {created: bool, seeded: {...}, store: dict}
    """
    with _LOCK:
        existed = _TREE_FILE.is_file()
        store = _load_store()
        created = False
        seeded_products = seeded_subsystems = seeded_presets = seeded_boms = 0

        need_seed = force or not existed or not store.get("preset_types")
        if need_seed:
            sp, ss, st, boms = _seed_from_presets()
            if mode == "reset" or not existed or not store.get("preset_types"):
                store = _default_store()
                store["seed_source"] = str(_SEED_FILE)
                store["products"] = sp
                store["subsystems"] = ss
                store["preset_types"] = [dict(t, bom_count=len(boms.get(t["id"], []))) for t in st]
                for pid, items in boms.items():
                    _save_bom(pid, items)
                    seeded_boms += 1
                seeded_products, seeded_subsystems, seeded_presets = len(sp), len(ss), len(st)
            else:  # merge
                known_p = {p.get("id") for p in store["products"]}
                known_s = {s.get("id") for s in store["subsystems"]}
                known_t = {t.get("id") for t in store["preset_types"]}
                for p in sp:
                    if p["id"] not in known_p:
                        store["products"].append(p)
                        seeded_products += 1
                for s in ss:
                    if s["id"] not in known_s:
                        store["subsystems"].append(s)
                        seeded_subsystems += 1
                    else:
                        node = next(x for x in store["subsystems"] if x.get("id") == s["id"])
                        merged = list(dict.fromkeys(list(node.get("products") or []) + list(s.get("products") or [])))
                        node["products"] = merged
                for t in st:
                    if t["id"] not in known_t:
                        store["preset_types"].append(dict(t, bom_count=len(boms.get(t["id"], []))))
                        _save_bom(t["id"], boms.get(t["id"], []))
                        seeded_presets += 1
                        seeded_boms += 1
            created = True
            _save_store(store)
        else:
            # 已有 tree.json: 补齐缺失的 BOM 文件 (手删文件 / 首次迁移场景)
            for t in store.get("preset_types", []):
                pid = t.get("id", "")
                if pid and not _bom_path(pid).is_file():
                    _save_bom(pid, [])
                    seeded_boms += 1

        return {
            "created": created,
            "seeded": {
                "products": seeded_products,
                "subsystems": seeded_subsystems,
                "preset_types": seeded_presets,
                "bom_files": seeded_boms,
                "mode": "reset" if mode == "reset" else "merge",
            },
            "store": store,
        }


# ---------------- BOM 设备条目归一化 ----------------


def _norm_bom_item(raw: dict) -> dict:
    iface = _text(raw.get("interface"), 24).upper() or "NONE"
    if iface not in ALL_INTERFACES:
        iface = iface or "NONE"
    dev_id = _text(raw.get("id"), 64)
    if not dev_id or not ID_RE.match(dev_id):
        dev_id = "dev-" + uuid.uuid4().hex[:8]
    return {
        "id": dev_id,
        "name": _text(raw.get("name"), 80),
        "model": _text(raw.get("model"), 80),
        "vendor": _text(raw.get("vendor"), 60),
        "category": _text(raw.get("category"), 40),
        "role": _text(raw.get("role"), 160),
        "programmable": bool(raw.get("programmable")),
        "interface": iface,
        "protocol": _text(raw.get("protocol"), 60),
        "default_host": _text(raw.get("default_host") or raw.get("host"), 80),
        "default_port": _as_int(raw.get("default_port") if raw.get("default_port") is not None else raw.get("port")),
        "default_serial_port": _text(raw.get("default_serial_port") or raw.get("serial_port"), 40),
        "default_baudrate": _as_int(
            raw.get("default_baudrate") if raw.get("default_baudrate") is not None else raw.get("baudrate")
        ),
        "default_address": _text(raw.get("default_address") or raw.get("address"), 120),
        "default_channel": _text(raw.get("default_channel") or raw.get("channel"), 24),
        "required": raw.get("required") is not False,
        "note": _text(raw.get("note"), 240),
    }


def _validate_bom_item(item: dict, index: int) -> list[str]:
    errs: list[str] = []
    label = item.get("name") or item.get("model") or f"第 {index + 1} 行"
    if not item.get("name"):
        errs.append(f"{label}：设备名称不能为空")
    if not item.get("model"):
        errs.append(f"{label}：型号不能为空")
    iface = item.get("interface", "NONE")
    if iface in NET_INTERFACES:
        host = item.get("default_host") or ""
        if host and not HOST_RE.match(host):
            errs.append(f"{label}：默认 IP/主机名格式非法 ({host})")
        port = item.get("default_port")
        if port is not None and not (1 <= int(port) <= 65535):
            errs.append(f"{label}：默认端口需在 1-65535 之间")
        if item.get("programmable") and not host:
            errs.append(f"{label}：可编程网络设备需填写默认 IP")
        if item.get("programmable") and not port:
            errs.append(f"{label}：可编程网络设备需填写默认端口")
    elif iface in SERIAL_INTERFACES:
        sp = item.get("default_serial_port") or ""
        if sp and not SERIAL_PORT_RE.match(sp):
            errs.append(f"{label}：串口号格式非法 ({sp})，应为 COM1 或 /dev/ttyUSB0")
        br = item.get("default_baudrate")
        if br is not None and not (300 <= int(br) <= 921600):
            errs.append(f"{label}：波特率需在 300-921600 之间")
        if item.get("programmable") and not sp:
            errs.append(f"{label}：可编程串口设备需填写默认串口号")
    return errs


def _dedupe_ids(items: list[dict]) -> list[str]:
    seen: dict[str, int] = {}
    errs: list[str] = []
    for it in items:
        did = it.get("id", "")
        seen[did] = seen.get(did, 0) + 1
    for did, n in seen.items():
        if n > 1:
            errs.append(f"设备编号重复: {did}（{n} 次），每台设备编号需唯一")
    return errs


# ---------------- 对外读取 (供 testbench_registry / 前端) ----------------


def load_meta() -> tuple[list[dict], list[dict]]:
    """(产品列表, 子系统列表) — 供预设类型树补全名称"""
    with _LOCK:
        ensure_store()
        store = _load_store()
    return list(store.get("products") or []), list(store.get("subsystems") or [])


def load_presets() -> list[dict]:
    """合并后的预设测试台类型列表 (含 BOM), 字段与 testbench_presets.json 保持一致"""
    with _LOCK:
        ensure_store()
        store = _load_store()
        products = {p.get("id", ""): p for p in store.get("products", [])}
        subsystems = {s.get("id", ""): s for s in store.get("subsystems", [])}
        out: list[dict] = []
        for t in store.get("preset_types", []):
            pid = t.get("id", "")
            product = t.get("product", "")
            subsystem = t.get("subsystem", "")
            out.append(
                {
                    "id": pid,
                    "name": t.get("name", ""),
                    "category": t.get("category", ""),
                    "description": t.get("description", ""),
                    "typical_dut": t.get("typical_dut", ""),
                    "recommended_cycle": t.get("recommended_cycle", ""),
                    "product": product,
                    "product_name": (products.get(product) or {}).get("name") or product,
                    "subsystem": subsystem,
                    "subsystem_name": (subsystems.get(subsystem) or {}).get("name") or subsystem,
                    "bom": _load_bom(pid),
                    "managed": True,
                    "updated_at": t.get("updated_at", ""),
                }
            )
        return out


def _preset_type(store: dict, preset_id: str) -> Optional[dict]:
    for t in store.get("preset_types", []):
        if t.get("id") == preset_id:
            return t
    return None


def _tree_payload(store: dict, usage: dict[str, int]) -> list[dict]:
    products = {p.get("id", ""): p for p in store.get("products", [])}
    subsystems = {s.get("id", ""): s for s in store.get("subsystems", [])}
    buckets: dict[str, dict] = {}
    for t in store.get("preset_types", []):
        pid = t.get("product", "") or "未分类"
        sid = t.get("subsystem", "") or "未分类"
        node = buckets.setdefault(
            pid,
            {
                "product": pid,
                "product_name": (products.get(pid) or {}).get("name") or pid,
                "note": (products.get(pid) or {}).get("note", ""),
                "subsystems": {},
                "preset_count": 0,
            },
        )
        sub = node["subsystems"].setdefault(
            sid,
            {
                "subsystem": sid,
                "subsystem_name": (subsystems.get(sid) or {}).get("name") or sid,
                "note": (subsystems.get(sid) or {}).get("note", ""),
                "preset_types": [],
            },
        )
        bom = _load_bom(t.get("id", ""))
        prog = [d for d in bom if d.get("programmable")]
        sub["preset_types"].append(
            {
                "id": t.get("id", ""),
                "name": t.get("name", ""),
                "category": t.get("category", ""),
                "description": t.get("description", ""),
                "typical_dut": t.get("typical_dut", ""),
                "recommended_cycle": t.get("recommended_cycle", ""),
                "bom_count": len(bom),
                "programmable_count": len(prog),
                "used_by": usage.get(t.get("id", ""), 0),
                "updated_at": t.get("updated_at", ""),
            }
        )
        node["preset_count"] += 1

    # 声明了但暂无机型的产品 / 子系统也返回, 便于后续挂节点
    for pid, p in products.items():
        buckets.setdefault(
            pid,
            {"product": pid, "product_name": p.get("name") or pid, "note": p.get("note", ""), "subsystems": {}, "preset_count": 0},
        )
    out: list[dict] = []
    for pid in sorted(buckets.keys()):
        node = buckets[pid]
        subs = []
        for sid in sorted(node["subsystems"].keys()):
            sub = node["subsystems"][sid]
            sub["preset_types"].sort(key=lambda x: str(x.get("id")))
            subs.append(sub)
        # 子系统声明里列出但无类型的分支也带上
        for sid, s in subsystems.items():
            if any(x["subsystem"] == sid for x in subs):
                continue
            if pid in (s.get("products") or []):
                subs.append(
                    {
                        "subsystem": sid,
                        "subsystem_name": s.get("name") or sid,
                        "note": s.get("note", ""),
                        "preset_types": [],
                    }
                )
        subs.sort(key=lambda x: str(x.get("subsystem")))
        node["subsystems"] = subs
        node["subsystem_count"] = len(subs)
        out.append(node)
    return out


# ---------------- 请求模型 ----------------


class ProductRequest(BaseModel):
    id: str = ""
    name: str = ""
    note: str = ""
    original_id: Optional[str] = None


class SubsystemRequest(BaseModel):
    id: str = ""
    name: str = ""
    products: list[str] = []
    note: str = ""
    original_id: Optional[str] = None


class PresetTypeRequest(BaseModel):
    id: str = ""
    name: str = ""
    category: str = ""
    description: str = ""
    typical_dut: str = ""
    recommended_cycle: str = ""
    product: str = ""
    subsystem: str = ""
    copy_bom_from: str = ""     # 新建类型时从已有类型复制 BOM (可选)
    copy_bom: bool = False      # 新建类型时从同子系统内最新类型复制 BOM (可选)
    original_id: Optional[str] = None


class BomSaveRequest(BaseModel):
    attributes: Optional[PresetTypeRequest] = None
    items: list[dict] = []
    create_copy_of: str = ""    # 以此 ID 为模板新建 (用于 BOM 另存为新的测试台类型)


class BomItemRequest(BaseModel):
    item: dict = {}
    before_id: str = ""


# ---------------- 路由注册 ----------------


def register_routes(app: FastAPI, base_dir: Path, tps_dir: Path) -> None:
    global _BASE_DIR, _TPS_DIR, _TREE_DIR, _TREE_FILE, _BOM_DIR, _SEED_FILE, _REGISTRY_FILE

    _BASE_DIR = Path(base_dir)
    _TPS_DIR = Path(tps_dir)
    _TREE_DIR = Path(os.environ.get("ATE_TREE_DIR") or (_BASE_DIR / "tree"))
    _TREE_FILE = _TREE_DIR / "tree.json"
    _BOM_DIR = _TREE_DIR / "bom"
    _SEED_FILE = _TPS_DIR / "testbench_presets.json"
    _REGISTRY_FILE = _TPS_DIR / "testbenches.json"

    # 启动即确保数据目录存在 (首次运行自动把既有预设纳入管理)
    try:
        ensure_store()
    except Exception:
        pass

    # ---------- 只读 ----------

    @app.get("/api/metadata/overview")
    def metadata_overview():
        try:
            with _LOCK:
                info = ensure_store()
                store = info["store"]
                presets = load_presets()
                usage = _registered_usage()
            bom_total = sum(len(p.get("bom") or []) for p in presets)
            programmable = sum(len([d for d in (p.get("bom") or []) if d.get("programmable")]) for p in presets)
            categories = sorted({d.get("category", "") for p in presets for d in (p.get("bom") or []) if d.get("category")})
            # 子系统可能同时挂在多个产品下: 声明数 = 节点数量, 分支数 = 产品/子系统组合数
            tree_payload = _tree_payload(store, usage)
            return {
                "success": True,
                "product_count": len(store.get("products") or []),
                "subsystem_count": len(store.get("subsystems") or []),
                "subsystem_branch_count": sum(n["subsystem_count"] for n in tree_payload),
                "preset_type_count": len(store.get("preset_types") or []),
                "bom_device_total": bom_total,
                "programmable_total": programmable,
                "device_categories": categories,
                "used_type_count": len([k for k in usage if k]),
                "registered_testbench_total": sum(usage.values()),
                "tree_dir": str(_TREE_DIR),
                "tree_file": str(_TREE_FILE),
                "bom_dir": str(_BOM_DIR),
                "seed_source": store.get("seed_source", ""),
                "created_at": store.get("created_at", ""),
                "updated_at": store.get("updated_at", ""),
                "seeded_now": info["created"],
            }
        except Exception as e:
            return {"success": False, "message": f"读取元数据概览失败: {e}"}

    @app.get("/api/metadata/tree")
    def metadata_tree():
        try:
            with _LOCK:
                ensure_store()
                store = _load_store()
                usage = _registered_usage()
                tree = _tree_payload(store, usage)
            distinct_subs = {s.get("id") for s in store.get("subsystems") or [] if s.get("id")}
            for node in tree:
                for sub in node.get("subsystems") or []:
                    distinct_subs.add(sub.get("subsystem"))
            return {
                "success": True,
                "product_count": len(tree),
                "subsystem_count": len(distinct_subs),
                "subsystem_branch_count": sum(n["subsystem_count"] for n in tree),
                "preset_type_count": len(store.get("preset_types") or []),
                "tree": tree,
                "tree_dir": str(_TREE_DIR),
                "updated_at": store.get("updated_at", ""),
            }
        except Exception as e:
            return {"success": False, "message": f"读取装备树元数据失败: {e}", "tree": []}

    @app.get("/api/metadata/store")
    def metadata_store():
        with _LOCK:
            ensure_store()
            store = _load_store()
        return {"success": True, "store": store, "tree_dir": str(_TREE_DIR)}

    @app.get("/api/metadata/presets/{preset_id}")
    def metadata_preset_detail(preset_id: str):
        pid = _text(preset_id, 64)
        with _LOCK:
            ensure_store()
            store = _load_store()
            t = _preset_type(store, pid)
            if not t:
                return {"success": False, "message": f"测试台类型不存在: {pid}"}
            products = {p.get("id", ""): p for p in store.get("products", [])}
            subsystems = {s.get("id", ""): s for s in store.get("subsystems", [])}
            bom = _load_bom(pid)
        product = t.get("product", "")
        subsystem = t.get("subsystem", "")
        return {
            "success": True,
            "preset": {
                "id": pid,
                "name": t.get("name", ""),
                "category": t.get("category", ""),
                "description": t.get("description", ""),
                "typical_dut": t.get("typical_dut", ""),
                "recommended_cycle": t.get("recommended_cycle", ""),
                "product": product,
                "product_name": (products.get(product) or {}).get("name") or product,
                "subsystem": subsystem,
                "subsystem_name": (subsystems.get(subsystem) or {}).get("name") or subsystem,
                "updated_at": t.get("updated_at", ""),
            },
            "bom": bom,
            "bom_file": str(_bom_path(pid)),
            "used_by": _registered_usage().get(pid, 0),
        }

    @app.get("/api/metadata/device-catalog")
    def metadata_device_catalog():
        """从所有已纳管 BOM 去重汇总的可复用设备模板 (供 BOM 编辑时快速引用)"""
        try:
            with _LOCK:
                ensure_store()
                store = _load_store()
                table: dict[str, dict] = {}
                for t in store.get("preset_types", []):
                    for d in _load_bom(t.get("id", "")):
                        key = "|".join([d.get("category", ""), d.get("model", ""), d.get("vendor", "")]).lower()
                        entry = table.setdefault(
                            key,
                            {
                                "key": key,
                                "name": d.get("name", ""),
                                "model": d.get("model", ""),
                                "vendor": d.get("vendor", ""),
                                "category": d.get("category", ""),
                                "role": d.get("role", ""),
                                "programmable": d.get("programmable", False),
                                "interface": d.get("interface", "NONE"),
                                "protocol": d.get("protocol", ""),
                                "default_host": d.get("default_host", ""),
                                "default_port": d.get("default_port"),
                                "default_serial_port": d.get("default_serial_port", ""),
                                "default_baudrate": d.get("default_baudrate"),
                                "default_address": d.get("default_address", ""),
                                "default_channel": d.get("default_channel", ""),
                                "required": d.get("required", True),
                                "note": d.get("note", ""),
                                "used_in": [],
                            },
                        )
                        if t.get("id") not in entry["used_in"]:
                            entry["used_in"].append(t.get("id"))
            items = sorted(table.values(), key=lambda x: (x.get("category", ""), x.get("model", "")))
            return {"success": True, "count": len(items), "devices": items}
        except Exception as e:
            return {"success": False, "message": f"读取设备模板库失败: {e}", "devices": []}

    # ---------- 种子 / 导入 ----------

    @app.post("/api/metadata/reseed")
    def metadata_reseed(mode: str = "merge"):
        """从 testresource/testbench_presets.json 重新导入 (merge=补齐缺失 / reset=全量重建)"""
        m = "reset" if str(mode).lower() == "reset" else "merge"
        try:
            info = ensure_store(force=True, mode=m)
        except Exception as e:
            return {"success": False, "message": f"导入预设失败: {e}"}
        s = info["seeded"]
        return {
            "success": True,
            "message": (
                f"已从预设文件{'重建' if m == 'reset' else '补齐'}："
                f"产品 {s['products']} · 子系统 {s['subsystems']} · 测试台类型 {s['preset_types']} · BOM 文件 {s['bom_files']}"
            ),
            "seeded": s,
        }

    # ---------- 产品节点 ----------

    @app.post("/api/metadata/products")
    def save_product(req: ProductRequest):
        pid = _text(req.id, 48)
        name = _text(req.name, 120)
        errs: list[str] = []
        if not pid:
            errs.append("产品编号不能为空")
        elif not PRODUCT_ID_RE.match(pid):
            errs.append("产品编号仅允许字母/数字/._-，且以字母或数字开头（如 3000-C）")
        if not name:
            errs.append("产品名称不能为空")
        if errs:
            return {"success": False, "message": "；".join(errs), "errors": errs}

        with _LOCK:
            ensure_store()
            store = _load_store()
            old_id = _text(req.original_id, 48)
            target = next((p for p in store["products"] if p.get("id") == (old_id or pid)), None)
            if target:
                # 更新 (允许改编号: 同步子系统引用与测试台类型归属)
                if old_id and old_id != pid:
                    if any(p.get("id") == pid for p in store["products"]):
                        return {"success": False, "message": f"产品编号已存在: {pid}"}
                    for s in store["subsystems"]:
                        s["products"] = [pid if x == old_id else x for x in (s.get("products") or [])]
                    for t in store["preset_types"]:
                        if t.get("product") == old_id:
                            t["product"] = pid
                target.update({"id": pid, "name": name, "note": _text(req.note, 200)})
                msg = f"产品「{name}」已更新"
            else:
                if any(p.get("id") == pid for p in store["products"]):
                    return {"success": False, "message": f"产品编号已存在: {pid}"}
                store["products"].append({"id": pid, "name": name, "note": _text(req.note, 200)})
                msg = f"产品「{name}」已新增"
            _save_store(store)
        return {"success": True, "message": f"{msg}（已写入 tree 文件夹）", "product": {"id": pid, "name": name}}

    @app.delete("/api/metadata/products/{product_id}")
    def delete_product(product_id: str, force: bool = False):
        pid = _text(product_id, 48)
        with _LOCK:
            ensure_store()
            store = _load_store()
            target = next((p for p in store["products"] if p.get("id") == pid), None)
            if not target:
                return {"success": False, "message": f"产品不存在: {pid}"}
            subs = [s.get("id") for s in store["subsystems"] if pid in (s.get("products") or [])]
            types = [t.get("id") for t in store["preset_types"] if t.get("product") == pid]
            if (subs or types) and not force:
                return {
                    "success": False,
                    "message": (
                        f"产品「{pid}」仍被引用，不能删除："
                        f"子系统 {len(subs)} 个（{'、'.join(subs[:5])}）、测试台类型 {len(types)} 个"
                        f"（{'、'.join(types[:5])}）。请先迁移或删除下级节点。"
                    ),
                }
            store["products"] = [p for p in store["products"] if p.get("id") != pid]
            for s in store["subsystems"]:
                s["products"] = [x for x in (s.get("products") or []) if x != pid]
            _save_store(store)
        return {"success": True, "message": f"产品「{pid}」已删除"}

    # ---------- 子系统节点 ----------

    @app.post("/api/metadata/subsystems")
    def save_subsystem(req: SubsystemRequest):
        sid = _text(req.id, 48)
        name = _text(req.name, 120)
        products = [str(x) for x in (req.products or []) if str(x)]
        errs: list[str] = []
        if not sid:
            errs.append("子系统编号不能为空")
        elif not SUBSYSTEM_ID_RE.match(sid):
            errs.append("子系统编号仅允许字母/数字/._-，且以字母或数字开头（如 DSP）")
        if not name:
            errs.append("子系统名称不能为空")
        if errs:
            return {"success": False, "message": "；".join(errs), "errors": errs}

        with _LOCK:
            ensure_store()
            store = _load_store()
            known = {p.get("id") for p in store["products"]}
            unknown = [p for p in products if p not in known]
            if unknown:
                return {
                    "success": False,
                    "message": f"所属产品不存在: {'、'.join(unknown)}。请先在「装备树管理」新增产品节点。",
                }
            old_id = _text(req.original_id, 48)
            target = next((s for s in store["subsystems"] if s.get("id") == (old_id or sid)), None)
            if target:
                if old_id and old_id != sid:
                    if any(s.get("id") == sid for s in store["subsystems"]):
                        return {"success": False, "message": f"子系统编号已存在: {sid}"}
                    for t in store["preset_types"]:
                        if t.get("subsystem") == old_id:
                            t["subsystem"] = sid
                target.update({"id": sid, "name": name, "products": products, "note": _text(req.note, 200)})
                msg = f"子系统「{name}」已更新"
            else:
                if any(s.get("id") == sid for s in store["subsystems"]):
                    return {"success": False, "message": f"子系统编号已存在: {sid}"}
                store["subsystems"].append({"id": sid, "name": name, "products": products, "note": _text(req.note, 200)})
                msg = f"子系统「{name}」已新增"
            _save_store(store)
        return {"success": True, "message": f"{msg}（已写入 tree 文件夹）", "subsystem": {"id": sid, "name": name}}

    @app.delete("/api/metadata/subsystems/{subsystem_id}")
    def delete_subsystem(subsystem_id: str, force: bool = False):
        sid = _text(subsystem_id, 48)
        with _LOCK:
            ensure_store()
            store = _load_store()
            target = next((s for s in store["subsystems"] if s.get("id") == sid), None)
            if not target:
                return {"success": False, "message": f"子系统不存在: {sid}"}
            types = [t.get("id") for t in store["preset_types"] if t.get("subsystem") == sid]
            if types and not force:
                return {
                    "success": False,
                    "message": (
                        f"子系统「{sid}」下仍有 {len(types)} 个测试台类型"
                        f"（{'、'.join(types[:6])}），不能删除。请先迁移或删除这些类型节点。"
                    ),
                }
            store["subsystems"] = [s for s in store["subsystems"] if s.get("id") != sid]
            _save_store(store)
        return {"success": True, "message": f"子系统「{sid}」已删除"}

    # ---------- 测试台类型节点 (含属性) ----------

    def _apply_preset_request(store: dict, req: PresetTypeRequest) -> dict:
        pid = _text(req.id, 64)
        name = _text(req.name, 120)
        product = _text(req.product, 48)
        subsystem = _text(req.subsystem, 48)
        errs: list[str] = []
        if not pid:
            errs.append("测试台类型编号不能为空")
        elif not ID_RE.match(pid):
            errs.append("类型编号仅允许字母/数字/._-，且以字母或数字开头（如 PB-PWR-02）")
        if not name:
            errs.append("测试台类型名称不能为空")
        if not product:
            errs.append("请选择所属产品")
        if not subsystem:
            errs.append("请选择所属子系统")
        if errs:
            return {"success": False, "message": "；".join(errs), "errors": errs}

        old_id = _text(req.original_id, 64)
        target = _preset_type(store, old_id or pid)
        if product:
            if not any(p.get("id") == product for p in store["products"]):
                return {"success": False, "message": f"所属产品不存在: {product}"}
            sub = next((s for s in store["subsystems"] if s.get("id") == subsystem), None)
            if not sub:
                return {"success": False, "message": f"所属子系统不存在: {subsystem}"}
            if (sub.get("products") or []) and product not in sub["products"]:
                return {
                    "success": False,
                    "message": (
                        f"子系统「{subsystem}」未挂在该产品下（当前归属：{'、'.join(sub['products'])}）。"
                        f"请先在装备树管理中把产品加入该子系统。"
                    ),
                }

        changes: list[str] = []
        if target:  # 更新
            if old_id and old_id != pid:
                if _preset_type(store, pid):
                    return {"success": False, "message": f"测试台类型编号已存在: {pid}"}
                _save_bom(pid, _load_bom(old_id))
                try:  # 迁移类型文件 (若已单独落盘)
                    src = _preset_type_file_path(old_id)
                    if src.is_file():
                        src.replace(_preset_type_file_path(pid))
                except Exception:
                    pass
                changes.append(f"编号 {old_id} → {pid}")
            attr = {
                "id": pid,
                "name": name,
                "category": _text(req.category, 60),
                "description": _text(req.description, 600),
                "typical_dut": _text(req.typical_dut, 200),
                "recommended_cycle": _text(req.recommended_cycle, 60),
                "product": product,
                "subsystem": subsystem,
                "updated_at": _now(),
            }
            target.update(attr)
            msg = f"测试台类型「{name}」属性已保存"
        else:  # 新建
            if _preset_type(store, pid):
                return {"success": False, "message": f"测试台类型编号已存在: {pid}"}
            store["preset_types"].append(
                {
                    "id": pid,
                    "name": name,
                    "category": _text(req.category, 60),
                    "description": _text(req.description, 600),
                    "typical_dut": _text(req.typical_dut, 200),
                    "recommended_cycle": _text(req.recommended_cycle, 60),
                    "product": product,
                    "subsystem": subsystem,
                    "bom_count": 0,
                    "updated_at": _now(),
                }
            )
            src_id = _text(req.copy_bom_from, 64)
            if not src_id and req.copy_bom:
                peers = [t for t in store["preset_types"] if t.get("subsystem") == subsystem and t.get("id") != pid]
                peers.sort(key=lambda x: (x.get("bom_count") or 0), reverse=True)
                src_id = peers[0]["id"] if peers else ""
            items = [_norm_bom_item(d) for d in _load_bom(src_id)] if src_id else []
            _save_bom(pid, items)
            msg = f"测试台类型「{name}」已新增，BOM {len(items)} 台" + (f"（复制自 {src_id}）" if src_id else "")

        node = _preset_type(store, pid)
        if node is not None:
            node["bom_count"] = len(_load_bom(pid))
        _save_store(store)
        return {
            "success": True,
            "message": f"{msg}（已写入 tree 文件夹）",
            "changes": changes,
            "preset": {"id": pid, "name": name, "product": product, "subsystem": subsystem},
        }

    @app.post("/api/metadata/presets")
    def save_preset_type(req: PresetTypeRequest):
        with _LOCK:
            ensure_store()
            store = _load_store()
            try:
                return _apply_preset_request(store, req)
            except Exception as e:
                return {"success": False, "message": f"保存测试台类型失败: {e}"}

    @app.delete("/api/metadata/presets/{preset_id}")
    def delete_preset_type(preset_id: str, force: bool = False):
        pid = _text(preset_id, 64)
        with _LOCK:
            ensure_store()
            store = _load_store()
            target = _preset_type(store, pid)
            if not target:
                return {"success": False, "message": f"测试台类型不存在: {pid}"}
            used = _registered_usage().get(pid, 0)
            if used and not force:
                return {
                    "success": False,
                    "message": (
                        f"测试台类型「{pid}」已被 {used} 台已注册/草稿测试台引用，不能删除。"
                        f"如确认删除，请先在「测试台导航」中删除或改配这些测试台，或勾选强制删除。"
                    ),
                }
            store["preset_types"] = [t for t in store["preset_types"] if t.get("id") != pid]
            _save_store(store)
            removed_file = ""
            try:
                f = _bom_path(pid)
                if f.is_file():
                    f.unlink()
                    removed_file = str(f)
            except Exception:
                pass
        return {"success": True, "message": f"测试台类型「{pid}」已删除", "removed_bom": removed_file}

    # ---------- BOM 管理 ----------

    @app.get("/api/metadata/presets/{preset_id}/bom")
    def get_bom(preset_id: str):
        pid = _text(preset_id, 64)
        with _LOCK:
            ensure_store()
            store = _load_store()
            if not _preset_type(store, pid):
                return {"success": False, "message": f"测试台类型不存在: {pid}", "items": []}
            items = _load_bom(pid)
            t = _preset_type(store, pid) or {}
        return {
            "success": True,
            "preset_id": pid,
            "count": len(items),
            "programmable_count": len([d for d in items if d.get("programmable")]),
            "items": items,
            "bom_file": str(_bom_path(pid)),
            "attributes": {
                "id": pid,
                "name": t.get("name", ""),
                "category": t.get("category", ""),
                "description": t.get("description", ""),
                "typical_dut": t.get("typical_dut", ""),
                "recommended_cycle": t.get("recommended_cycle", ""),
                "product": t.get("product", ""),
                "subsystem": t.get("subsystem", ""),
            },
            "updated_at": t.get("updated_at", ""),
        }

    def _save_bom_payload(pid: str, items_raw: list[dict], attributes: Optional[PresetTypeRequest]) -> dict:
        items = [_norm_bom_item(d) for d in items_raw if isinstance(d, dict)]
        errs = _dedupe_ids(items)
        for i, it in enumerate(items):
            errs.extend(_validate_bom_item(it, i))
        if errs:
            return {"success": False, "message": "；".join(errs[:8]), "errors": errs}

        with _LOCK:
            ensure_store()
            store = _load_store()
            node = _preset_type(store, pid)
            if not node:
                return {"success": False, "message": f"测试台类型不存在: {pid}"}
            attr_msg = ""
            if attributes is not None:
                payload = attributes.model_copy(deep=True) if hasattr(attributes, "model_copy") else attributes.copy(deep=True)
                payload.id = pid
                payload.original_id = pid
                res = _apply_preset_request(store, payload)
                if not res.get("success"):
                    return res
                attr_msg = "；测试台属性已同步保存"
                store = _load_store()
                node = _preset_type(store, pid) or node
            _save_bom(pid, items)
            node["bom_count"] = len(items)
            node["updated_at"] = _now()
            _save_store(store)
        prog = len([d for d in items if d.get("programmable")])
        return {
            "success": True,
            "message": f"BOM 已保存：{len(items)} 台设备（可编程 {prog} 台）{attr_msg}",
            "count": len(items),
            "programmable_count": prog,
            "items": items,
            "bom_file": str(_bom_path(pid)),
        }

    @app.put("/api/metadata/presets/{preset_id}/bom")
    def put_bom(preset_id: str, req: BomSaveRequest):
        pid = _text(preset_id, 64)
        try:
            return _save_bom_payload(pid, req.items, req.attributes)
        except Exception as e:
            return {"success": False, "message": f"保存 BOM 失败: {e}"}

    @app.post("/api/metadata/presets/{preset_id}/bom")
    def post_bom_alias(preset_id: str, req: BomSaveRequest):
        """与 PUT 等价, 便于前端在部分代理环境下使用 POST"""
        pid = _text(preset_id, 64)
        try:
            return _save_bom_payload(pid, req.items, req.attributes)
        except Exception as e:
            return {"success": False, "message": f"保存 BOM 失败: {e}"}

    @app.post("/api/metadata/presets/{preset_id}/bom/items")
    def add_bom_item(preset_id: str, req: BomItemRequest):
        pid = _text(preset_id, 64)
        with _LOCK:
            ensure_store()
            store = _load_store()
            node = _preset_type(store, pid)
            if not node:
                return {"success": False, "message": f"测试台类型不存在: {pid}"}
            items = _load_bom(pid)
            item = _norm_bom_item(req.item if isinstance(req.item, dict) else {})
            if any(x.get("id") == item["id"] for x in items):
                item["id"] = "dev-" + uuid.uuid4().hex[:8]
            before = _text(req.before_id, 64)
            if before and any(x.get("id") == before for x in items):
                idx = next(i for i, x in enumerate(items) if x.get("id") == before)
                items.insert(idx, item)
            else:
                items.append(item)
            errs = _validate_bom_item(item, len(items) - 1)
            if errs:
                return {"success": False, "message": "；".join(errs), "errors": errs}
            _save_bom(pid, items)
            node["bom_count"] = len(items)
            node["updated_at"] = _now()
            _save_store(store)
        return {"success": True, "message": f"已添加设备「{item['name'] or item['model']}」", "item": item, "count": len(items)}

    @app.delete("/api/metadata/presets/{preset_id}/bom/items/{device_id}")
    def delete_bom_item(preset_id: str, device_id: str):
        pid = _text(preset_id, 64)
        did = _text(device_id, 64)
        with _LOCK:
            ensure_store()
            store = _load_store()
            node = _preset_type(store, pid)
            if not node:
                return {"success": False, "message": f"测试台类型不存在: {pid}"}
            items = _load_bom(pid)
            remain = [x for x in items if x.get("id") != did]
            if len(remain) == len(items):
                return {"success": False, "message": f"设备不存在: {did}"}
            _save_bom(pid, remain)
            node["bom_count"] = len(remain)
            node["updated_at"] = _now()
            _save_store(store)
        return {"success": True, "message": f"已删除设备 {did}", "count": len(remain)}
