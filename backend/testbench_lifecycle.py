# -*- coding: utf-8 -*-
"""测试台装备运维模块（装备属性配置页的运维动作）

能力：
  1) 待注册测试台导出   GET  /api/testbench/pending   ·  POST /api/testbenches/export
  2) 装备初始化 / 终止  POST /api/testbenches/{tb_id}/init | /teardown
  3) 硬件自检 + 报告    POST /api/testbenches/{tb_id}/selfcheck
  4) 报告区（界面空间） GET  /api/testbench/reports | /reports/dir | /reports/{file}
                        POST /api/testbench/reports/open   （调系统资源管理器打开报告目录）

设计要点：
  - 测试台配置（含设备连接参数）始终以注册库 `testresource/testbenches.json` 为唯一事实源，本模块不另存一份；
  - 每次初始化 / 终止 / 自检 / 导出都落盘一份文件到 `backend/logs/testbench/`，并登记到 `index.json` 清单，
    前端「报告区」据此列出历史硬件自检报告，可一键在资源管理器中打开目录；
  - `mode=simulate` 只生成与真实流程同构的步骤记录、绝不冒充实测；`mode=real` 才真正建链下发指令；
  - 自检与注册第三步共用 `testbench_registry._run_verification`，并同步写回注册库的 verification / verify_history。
"""

from __future__ import annotations

import html
import json
import os
import platform
import re
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import testbench_registry as tbr

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
REPORT_ROOT = LOG_DIR / "testbench"
INDEX_FILE = REPORT_ROOT / "index.json"

_LOCK = threading.RLock()

KIND_LABEL = {
    "selfcheck": "硬件自检",
    "init": "装备初始化",
    "teardown": "装备终止",
    "export": "测试台导出",
}
MODE_LABEL = {"real": "真实探测", "simulate": "离线模拟"}
OVERALL_LABEL = {"pass": "通过", "warn": "待人工确认", "fail": "未通过"}
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,120}\.(?:html|json)$")

RESULT_CONCLUSION = {
    "pass": "全部动作执行成功，测试台处于预期状态",
    "warn": "存在未执行的动作（缺动作模板 / 未安装串口库），请按清单人工确认",
    "fail": "存在失败项，请检查设备供电、网络与接口连接后重试",
}

# ---------------------------------------------------------------- 设备动作模板
# 每个动作 = (动作说明, SCPI/串口指令)。真实模式下按序下发；模拟模式只记录不发送。

INIT_SEQUENCE: dict[str, list[tuple[str, str]]] = {
    "scope": [("设备复位", "*RST"), ("清空状态", "*CLS"), ("恢复默认时基", "TIM:SCAL 1E-3"), ("读取设备标识", "*IDN?")],
    "dmm": [("设备复位", "*RST"), ("置直流电压功能", 'FUNC "VOLT:DC"'), ("开启自动量程", "VOLT:DC:RANG:AUTO ON"), ("读取设备标识", "*IDN?")],
    "psu": [("关闭输出", "OUTP OFF"), ("电压归零（安全态）", "VOLT 0"), ("设定限流保护", "CURR 0.1"), ("读取设备标识", "*IDN?")],
    "eload": [("关闭输入", "INP OFF"), ("置恒流模式", "FUNC CURR"), ("读取设备标识", "*IDN?")],
    "awg": [("关闭输出", "OUTP OFF"), ("置正弦波默认", "FUNC SIN"), ("读取设备标识", "*IDN?")],
    "motion": [("串口握手", "AT"), ("转速指令归零", "SPD 0"), ("读取控制盒标识", "ID?")],
}

TEARDOWN_SEQUENCE: dict[str, list[tuple[str, str]]] = {
    "scope": [("停止采集", "STOP"), ("退回本地控制", "SYST:LOC")],
    "dmm": [("停止测量", "ABOR"), ("退回本地控制", "SYST:LOC")],
    "psu": [("关闭输出", "OUTP OFF"), ("电压归零", "VOLT 0"), ("退回本地控制", "SYST:LOC")],
    "eload": [("关闭输入", "INP OFF"), ("退回本地控制", "SYST:LOC")],
    "awg": [("关闭输出", "OUTP OFF"), ("退回本地控制", "SYST:LOC")],
    "motion": [("转速归零", "SPD 0"), ("撤销使能", "DIS"), ("关闭串口会话", "BYE")],
}

# 类别关键字 → 动作模板键（按顺序匹配 BOM 里的 category 字段）
CATEGORY_KEYS: list[tuple[str, str]] = [
    ("示波器", "scope"),
    ("万用表", "dmm"),
    ("电源模块", "psu"),
    ("电源", "psu"),
    ("电子负载", "eload"),
    ("信号源", "awg"),
    ("波形", "awg"),
    ("运动", "motion"),
    ("转速", "motion"),
    ("夹具", "fixture"),
    ("探针", "fixture"),
]
SEQ_KEY_LABEL = {
    "scope": "示波器默认态",
    "dmm": "万用表默认态",
    "psu": "电源安全态",
    "eload": "电子负载安全态",
    "awg": "信号源安全态",
    "motion": "运动控制默认态",
}


class OpsRequest(BaseModel):
    mode: str = "simulate"      # real 真实探测 / simulate 离线模拟
    timeout: float = 2.0
    note: str = ""


class ExportRequest(BaseModel):
    bench_ids: Optional[list[str]] = None   # 空 = 按 status 过滤（默认待注册）
    include_registered: bool = False        # 旧参数：连带已注册一起导出（等价 status=all）
    status: str = ""                       # registered 只导已注册 / draft 只导草稿 / all 全导 / 空=兼容旧行为


class OpenRequest(BaseModel):
    filename: str = ""                      # 指定文件则定位到该文件；空则打开报告目录


# ---------------------------------------------------------------- 基础工具


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def ensure_report_root() -> Path:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    return REPORT_ROOT


def _safe_name(name: str) -> bool:
    """只允许报告目录内的普通文件名，杜绝路径穿越"""
    return bool(SAFE_NAME_RE.match(name or ""))


def _report_path(name: str) -> Optional[Path]:
    if not _safe_name(name):
        return None
    path = (REPORT_ROOT / name).resolve()
    try:
        path.relative_to(REPORT_ROOT.resolve())
    except ValueError:
        return None
    return path


# ---------------------------------------------------------------- 报告清单（index.json）


def _load_index() -> dict:
    data = _read_json(INDEX_FILE, {"version": 1, "entries": []})
    if not isinstance(data, dict):
        data = {"version": 1, "entries": []}
    data.setdefault("entries", [])
    return data


def _prune_index(data: dict, prune_missing: bool = True) -> dict:
    if prune_missing:
        data["entries"] = [e for e in data.get("entries", []) if (REPORT_ROOT / str(e.get("file", ""))).is_file()]
    return data


def _register_entry(kind: str, bench: dict, path: Path, overall: str, summary: dict, mode: str, extra: dict) -> dict:
    entry = {
        "file": path.name,
        "kind": kind,
        "kind_label": KIND_LABEL.get(kind, kind),
        "bench_id": bench.get("id", ""),
        "bench_title": bench.get("title", ""),
        "bench_serial": bench.get("serial", ""),
        "bench_preset": bench.get("preset_name") or bench.get("preset_id", ""),
        "mode": mode,
        "mode_label": MODE_LABEL.get(mode, mode),
        "overall": overall,
        "overall_label": OVERALL_LABEL.get(overall, overall),
        "summary": summary,
        "created_at": _now(),
        "size": path.stat().st_size if path.exists() else 0,
        "path": str(path),
        "url": f"/api/testbench/reports/{path.name}",
    }
    entry.update(extra or {})
    with _LOCK:
        data = _load_index()
        data["entries"] = [e for e in data["entries"] if e.get("file") != path.name]
        data["entries"].append(entry)
        data["entries"] = data["entries"][-400:]
        _write_json(INDEX_FILE, data)
    return entry


# ---------------------------------------------------------------- 报告渲染


def _esc(text: Any) -> str:
    return html.escape("" if text is None else str(text))


def _bench_head(bench: dict) -> str:
    station = bench.get("station") or {}
    rows = [
        ("测试台名称", bench.get("title") or "—"),
        ("测试台编号", bench.get("serial") or "—"),
        ("预设类型", bench.get("preset_name") or bench.get("preset_id") or "—"),
        ("状态", "已注册" if bench.get("status") == "registered" else "草稿（待注册）"),
        ("产线 / 工位", f"{station.get('line') or '—'} · {station.get('station') or '—'}"),
        ("物理位置", station.get("location") or "—"),
        ("设备规模", f"{len(bench.get('devices') or [])} 台（可编程 {len([d for d in (bench.get('devices') or []) if d.get('programmable')])} 台）"),
    ]
    body = "".join(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>" for k, v in rows)
    return f'<table class="kv">{body}</table>'


def render_report_html(kind: str, bench: dict, payload: dict) -> str:
    """生成自包含的 HTML 报告（紫白工业风，可在界面内 iframe 直接查看）"""
    title = f"{KIND_LABEL.get(kind, kind)}报告"
    overall = payload.get("overall", "pass")
    summary = payload.get("summary") or {}
    mode = payload.get("mode", "simulate")
    checked_at = payload.get("checked_at") or _now()

    chips = "".join(
        f'<span class="chip"><b>{_esc(summary.get(k, 0))}</b>{_esc(lbl)}</span>'
        for k, lbl in (("total", "检查项"), ("pass", "通过"), ("fail", "失败"), ("skip", "跳过"))
    )

    if kind == "selfcheck":
        rows = []
        for c in payload.get("checklist") or []:
            rows.append(
                "<tr>"
                f'<td>{_esc("配置类" if c.get("kind") != "device" else "设备类")}</td>'
                f'<td>{_esc(c.get("label"))}</td>'
                f'<td>{_esc(c.get("target") or "—")}</td>'
                f'<td class="s-{_esc(c.get("status"))}">{_esc({"pass": "通过", "fail": "失败", "skip": "跳过"}.get(c.get("status"), c.get("status")))}</td>'
                f'<td>{_esc(c.get("detail"))}</td>'
                "</tr>"
            )
        detail = (
            "<h2>逐项检查清单</h2><table class=\"grid\">"
            "<tr><th>类型</th><th>检查项</th><th>目标</th><th>结果</th><th>说明</th></tr>"
            + "".join(rows)
            + "</table>"
        )
    else:
        blocks = []
        for d in payload.get("devices") or []:
            steps = "".join(
                "<tr>"
                f'<td>{_esc(i + 1)}</td>'
                f'<td>{_esc(s.get("label"))}</td>'
                f'<td class="mono">{_esc(s.get("command") or "—")}</td>'
                f'<td class="s-{_esc(s.get("status"))}">{_esc({"pass": "成功", "fail": "失败", "skip": "跳过"}.get(s.get("status"), s.get("status")))}</td>'
                f'<td>{_esc(s.get("detail"))}</td>'
                "</tr>"
                for i, s in enumerate(d.get("steps") or [])
            )
            blocks.append(
                f'<h3>{_esc(d.get("name"))} <span class="mono">{_esc(d.get("model"))}</span>'
                f' <span class="s-{_esc(d.get("status"))}">· {_esc({"pass": "成功", "fail": "失败", "skip": "跳过"}.get(d.get("status"), d.get("status")))}</span></h3>'
                f'<p class="mono">{_esc(d.get("resource") or "—")} · {_esc(d.get("interface"))}</p>'
                '<table class="grid"><tr><th>#</th><th>动作</th><th>指令</th><th>结果</th><th>说明</th></tr>'
                + steps
                + "</table>"
            )
        detail = "<h2>设备动作明细</h2>" + ("".join(blocks) or '<p class="muted">无设备</p>')

    if kind in ("init", "teardown") and payload.get("duration_ms") is not None:
        detail = f'<p class="muted">总耗时 {_esc(payload.get("duration_ms"))} ms</p>' + detail

    if kind == "export":
        detail = "<h2>导出内容</h2><p>" + _esc(payload.get("note") or "") + "</p>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>{_esc(bench.get('title', ''))} · {_esc(title)}</title>
<style>
  :root {{ --accent:#6d28d9; --accent2:#8b5cf6; --bg:#fff; --soft:#faf9ff; --panel:#f5f2fd;
           --border:#e4def6; --ok:#15803d; --warn:#b45309; --err:#dc2626; --ink:#241b3f; --muted:#6b6485; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:28px 32px; background:var(--soft); color:var(--ink);
          font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif; font-size:13px; line-height:1.6; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  h2 {{ font-size:15px; margin:24px 0 10px; padding-left:9px; border-left:3px solid var(--accent); }}
  h3 {{ font-size:13.5px; margin:18px 0 4px; }}
  .sub {{ color:var(--muted); margin-bottom:16px; }}
  .card {{ background:var(--bg); border:1px solid var(--border); border-radius:12px; padding:16px 18px; }}
  .verdict {{ display:flex; align-items:center; gap:14px; margin-bottom:14px; }}
  .verdict .big {{ font-size:16px; font-weight:700; padding:7px 16px; border-radius:999px; color:#fff; }}
  .v-pass {{ background:var(--ok); }} .v-warn {{ background:var(--warn); }} .v-fail {{ background:var(--err); }}
  .chip {{ display:inline-block; background:var(--panel); border:1px solid var(--border); border-radius:999px;
           padding:3px 12px; margin:0 8px 8px 0; color:var(--muted); }}
  .chip b {{ color:var(--accent); font-size:15px; margin-right:4px; }}
  table {{ width:100%; border-collapse:collapse; background:var(--bg); }}
  table.kv th {{ width:130px; text-align:left; color:var(--muted); font-weight:500; background:var(--panel); }}
  table.kv th, table.kv td {{ border:1px solid var(--border); padding:6px 10px; }}
  table.grid th {{ background:var(--panel); text-align:left; font-weight:600; }}
  table.grid th, table.grid td {{ border:1px solid var(--border); padding:6px 10px; vertical-align:top; }}
  .mono {{ font-family:Consolas,"Courier New",monospace; color:var(--muted); }}
  .s-pass {{ color:var(--ok); font-weight:600; }} .s-fail {{ color:var(--err); font-weight:600; }}
  .s-skip {{ color:var(--warn); font-weight:600; }}
  .muted {{ color:var(--muted); }}
  footer {{ margin-top:26px; color:var(--muted); font-size:12px; border-top:1px dashed var(--border); padding-top:10px; }}
</style>
</head>
<body>
  <h1>{_esc(bench.get('title', '测试台'))} · {_esc(title)}</h1>
  <div class="sub">生成时间 {_esc(checked_at)} · 运行模式 {_esc(MODE_LABEL.get(mode, mode))}{' · 离线模拟结果，不代表真机实测' if mode == 'simulate' else ''}</div>
  <div class="card">
    <div class="verdict">
      <span class="big v-{_esc(overall)}">{_esc(OVERALL_LABEL.get(overall, overall))}</span>
      <span>{_esc(payload.get('conclusion') or RESULT_CONCLUSION.get(overall, ''))}</span>
    </div>
    <div>{chips}</div>
  </div>
  <h2>测试台信息</h2>
  {_bench_head(bench)}
  {detail}
  <footer>由 ATE Runner 测试台装备运维模块生成 · 报告目录 backend/logs/testbench · 文件 {_esc(payload.get('filename', ''))}</footer>
</body>
</html>
"""


# ---------------------------------------------------------------- 链路与会话（真实模式）


class _Link:
    """LAN / SERIAL 极简链路：只做连接 + 逐行读写，失败不抛异常，交由调用方记录"""

    def __init__(self, dev: dict, timeout: float):
        self.dev = dev
        self.timeout = max(0.2, min(float(timeout), 10.0))
        self.kind = ""
        self.sock: Optional[socket.socket] = None
        self.ser: Any = None
        self.error = ""

    def open(self) -> bool:
        iface = str(self.dev.get("interface") or "").upper()
        if iface in tbr.NET_INTERFACES:
            host, port = str(self.dev.get("host") or "").strip(), self.dev.get("port")
            if not host or not port:
                self.error = "未配置 IP / 端口"
                return False
            try:
                self.sock = socket.create_connection((host, int(port)), timeout=self.timeout)
                self.sock.settimeout(min(self.timeout, 0.6))
                self.kind = "lan"
                return True
            except Exception as e:  # noqa: BLE001
                self.error = f"连接失败: {e}"
                return False
        if iface in tbr.SERIAL_INTERFACES:
            port_name = str(self.dev.get("serial_port") or "").strip()
            if not port_name:
                self.error = "未配置串口号"
                return False
            try:
                import serial  # type: ignore
            except Exception:
                self.error = "未安装 pyserial，无法建立串口会话（可 pip install pyserial 后重试）"
                return False
            try:
                self.ser = serial.Serial(port_name, int(self.dev.get("baudrate") or 9600), timeout=self.timeout)
                self.kind = "serial"
                return True
            except Exception as e:  # noqa: BLE001
                self.error = f"串口打开失败: {e}"
                return False
        self.error = f"{iface or '未知'} 接口不支持程控"
        return False

    def command(self, cmd: str) -> tuple[bool, str]:
        payload = (cmd.strip() + "\n").encode("ascii", "ignore")
        try:
            if self.kind == "lan" and self.sock:
                self.sock.sendall(payload)
                if cmd.strip().endswith("?"):
                    data = self.sock.recv(512)
                    return True, f"应答 {data.decode('ascii', 'ignore').strip()[:80] or '(空)'}"
                return True, "指令已下发"
            if self.kind == "serial" and self.ser:
                self.ser.write(payload)
                if cmd.strip().endswith("?"):
                    data = self.ser.readline()
                    return True, f"应答 {data.decode('ascii', 'ignore').strip()[:80] or '(空)'}"
                return True, "指令已下发"
        except Exception as e:  # noqa: BLE001
            return False, f"下发失败: {e}"
        return False, "会话未建立"

    def close(self) -> None:
        try:
            if self.sock:
                self.sock.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.ser:
                self.ser.close()
        except Exception:  # noqa: BLE001
            pass


def _category_key(dev: dict) -> str:
    text = f"{dev.get('category', '')}{dev.get('name', '')}{dev.get('role', '')}"
    for kw, key in CATEGORY_KEYS:
        if kw in text:
            return key
    return "generic"


def _resource(dev: dict) -> str:
    try:
        return tbr._resource_of(dev)
    except Exception:  # noqa: BLE001
        return ""


def _device_phase(dev: dict, phase: str, mode: str, timeout: float) -> dict:
    """单台设备在某个阶段（init / teardown）的动作执行记录"""
    seq = INIT_SEQUENCE if phase == "init" else TEARDOWN_SEQUENCE
    key = _category_key(dev)
    steps_tpl = seq.get(key)

    row = {
        "id": dev.get("id", ""),
        "name": dev.get("name") or dev.get("id") or "未命名设备",
        "model": dev.get("model", ""),
        "category": dev.get("category", ""),
        "interface": str(dev.get("interface") or "NONE").upper(),
        "resource": _resource(dev),
        "programmable": bool(dev.get("programmable")),
        "required": bool(dev.get("required", True)),
        "status": "skip",
        "detail": "",
        "steps": [],
    }

    if not dev.get("programmable"):
        row["status"] = "info"
        row["detail"] = "非可编程设备，无程控动作（不参与结论判定），请人工确认实物状态"
        return row
    if not steps_tpl:
        row["detail"] = f"类别「{row['category'] or '未知'}」暂无标准动作模板，需人工执行"
        return row

    if mode == "simulate":
        row["steps"] = [
            {"label": label, "command": cmd, "status": "pass", "detail": "模拟：未向仪器下发指令"}
            for label, cmd in steps_tpl
        ]
        row["status"] = "pass"
        row["detail"] = f"模拟执行 {len(steps_tpl)} 个动作（{SEQ_KEY_LABEL.get(key, key)}）"
        return row

    link = _Link(dev, timeout)
    if not link.open():
        row["status"] = "fail"
        row["detail"] = link.error
        row["steps"] = [
            {"label": label, "command": cmd, "status": "fail", "detail": link.error} for label, cmd in steps_tpl
        ]
        return row

    try:
        for label, cmd in steps_tpl:
            t0 = time.perf_counter()
            ok, detail = link.command(cmd)
            cost = round((time.perf_counter() - t0) * 1000, 1)
            row["steps"].append(
                {
                    "label": label,
                    "command": cmd,
                    "status": "pass" if ok else "fail",
                    "detail": f"{detail} ({cost} ms)",
                }
            )
    finally:
        link.close()

    failed = [s for s in row["steps"] if s["status"] == "fail"]
    row["status"] = "fail" if failed else "pass"
    row["detail"] = f"{len(row['steps']) - len(failed)}/{len(row['steps'])} 个动作成功"
    return row


def _run_phase(item: dict, phase: str, mode: str, timeout: float) -> dict:
    started = time.perf_counter()
    rows = [_device_phase(dev, phase, mode, timeout) for dev in (item.get("devices") or [])]
    # 结论只看可编程设备：非程控设备（夹具 / 探针等）不拉低结论，只标为信息项
    prog = [r for r in rows if r["programmable"]]
    failed = [r for r in prog if r["status"] == "fail"]
    skipped = [r for r in prog if r["status"] == "skip"]
    info = [r for r in rows if r["status"] == "info"]
    overall = "fail" if failed else ("warn" if skipped else "pass")
    return {
        "phase": phase,
        "phase_label": KIND_LABEL.get(phase, phase),
        "mode": mode,
        "checked_at": _now(),
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "overall": overall,
        "summary": {
            "total": len(rows),
            "pass": len([r for r in prog if r["status"] == "pass"]),
            "fail": len(failed),
            "skip": len(skipped),
            "info": len(info),
            "action_total": sum(len(r["steps"]) for r in rows),
            "action_pass": sum(len([s for s in r["steps"] if s["status"] == "pass"]) for r in rows),
        },
        "devices": rows,
        "conclusion": RESULT_CONCLUSION[overall],
    }


def _selfcheck(item: dict, mode: str, timeout: float) -> dict:
    result = tbr._run_verification(item, mode, timeout)
    result["phase"] = "selfcheck"
    result["phase_label"] = KIND_LABEL["selfcheck"]
    return result


# ---------------------------------------------------------------- 测试台导出（待注册 / 已注册）

SCOPE_LABEL = {"pending": "待注册", "registered": "已注册", "all": "全部", "selected": "选中"}
SCOPE_PURPOSE = {
    "registered": "已注册测试台导出：备份现场注册配置与设备连接参数，可跨工控机迁移或作为注册基线比对",
    "all": "全部测试台导出（含草稿）：完整备份当前注册库内容",
    "selected": "指定测试台导出：按选中测试台导出设备清单与连接参数",
    "pending": "待注册测试台导出：可线下核对设备清单与连接参数，或迁移到其他工控机后再导入注册",
}


def _export_payload(reg: dict, bench_ids: Optional[list[str]], include_registered: bool,
                    status: str = "") -> dict:
    """组装导出内容

    * bench_ids: 指定测试台 id（任意状态）
    * status: registered 只导已注册 / draft 只导草稿 / all 全导 / 空 = 兼容旧行为（待注册）
    * include_registered: 旧参数，等价于 status="all"
    """
    benches = reg.get("testbenches", [])
    status = (status or "").strip().lower()
    if bench_ids:
        want = set(bench_ids)
        picked = [b for b in benches if b.get("id") in want]
        scope = "selected"
    elif status == "registered":
        picked = [b for b in benches if b.get("status") == "registered"]
        scope = "registered"
    elif status == "all" or include_registered:
        picked = list(benches)
        scope = "all"
    else:
        picked = [b for b in benches if b.get("status") != "registered"]
        scope = "pending"

    items = []
    for b in picked:
        devices = []
        for d in b.get("devices") or []:
            devices.append(
                {
                    "id": d.get("id"),
                    "name": d.get("name"),
                    "model": d.get("model"),
                    "vendor": d.get("vendor"),
                    "category": d.get("category"),
                    "role": d.get("role"),
                    "programmable": bool(d.get("programmable")),
                    "interface": d.get("interface"),
                    "protocol": d.get("protocol"),
                    "host": d.get("host"),
                    "port": d.get("port"),
                    "serial_port": d.get("serial_port"),
                    "baudrate": d.get("baudrate"),
                    "address": d.get("address"),
                    "channel": d.get("channel"),
                    "required": bool(d.get("required", True)),
                    "note": d.get("note", ""),
                    "configured": bool(d.get("configured")),
                }
            )
        items.append(
            {
                "id": b.get("id"),
                "title": b.get("title"),
                "serial": b.get("serial"),
                "status": b.get("status"),
                "step": b.get("step"),
                "preset_id": b.get("preset_id"),
                "preset_name": b.get("preset_name"),
                "product": b.get("product"),
                "subsystem": b.get("subsystem"),
                "station": b.get("station") or {},
                "device_count": len(devices),
                "programmable_count": len([d for d in devices if d["programmable"]]),
                "configured_count": len([d for d in devices if d["configured"]]),
                "devices": devices,
                "created_at": b.get("created_at"),
                "updated_at": b.get("updated_at"),
            }
        )
    return {
        "schema": "ate.testbench.export.v1",
        "exported_at": _now(),
        "scope": scope,
        "scope_label": SCOPE_LABEL.get(scope, scope),
        "status_filter": status or ("all" if include_registered else "pending"),
        "count": len(items),
        "by_status": {
            "registered": len([i for i in items if i.get("status") == "registered"]),
            "draft": len([i for i in items if i.get("status") != "registered"]),
        },
        "purpose": SCOPE_PURPOSE.get(scope, SCOPE_PURPOSE["pending"]),
        "testbenches": items,
    }


# ---------------------------------------------------------------- 路由注册


def register_routes(app: FastAPI, tps_dir: Path) -> None:
    ensure_report_root()

    # ---------- 待注册测试台 ----------

    @app.get("/api/testbench/pending")
    def pending_testbenches():
        """装备属性配置页「待注册」工具条数据源：草稿状态的测试台"""
        try:
            with _LOCK:
                reg = tbr.load_registry()
            benches = reg.get("testbenches", [])
            drafts = [b for b in benches if b.get("status") != "registered"]
            rows = []
            for b in drafts:
                devices = b.get("devices") or []
                rows.append(
                    {
                        "id": b.get("id"),
                        "title": b.get("title"),
                        "serial": b.get("serial"),
                        "status": b.get("status", "draft"),
                        "step": b.get("step"),
                        "preset_id": b.get("preset_id"),
                        "preset_name": b.get("preset_name") or b.get("preset_id"),
                        "device_count": len(devices),
                        "programmable_count": len([d for d in devices if d.get("programmable")]),
                        "configured_count": len([d for d in devices if _device_ok(d)]),
                        "updated_at": b.get("updated_at"),
                    }
                )
            return {
                "success": True,
                "count": len(rows),
                "registered_count": len(benches) - len(rows),
                "total_count": len(benches),
                "testbenches": rows,
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "message": f"读取待注册测试台失败: {e}", "testbenches": []}

    @app.post("/api/testbenches/export")
    def export_testbenches(req: Optional[ExportRequest] = None):
        """导出测试台（默认待注册；`status=registered` 导出已注册；`bench_ids` 指定台），落盘 + 回传内容供浏览器下载"""
        req = req or ExportRequest()
        try:
            with _LOCK:
                reg = tbr.load_registry()
            payload = _export_payload(reg, req.bench_ids, bool(req.include_registered), req.status)
            scope = payload["scope"]
            scope_label = SCOPE_LABEL.get(scope, scope)
            name = f"export_{scope}_{_stamp()}.json"
            path = ensure_report_root() / name
            _write_json(path, payload)
            summary = {
                "total": payload["count"],
                "pass": payload["count"],
                "fail": 0,
                "skip": 0,
            }
            bench_stub = {
                "id": (req.bench_ids or ["<all>"])[0] if req.bench_ids else "",
                "title": f"{scope_label}测试台导出（{payload['count']} 台）",
                "serial": "",
                "preset_name": "",
                "status": "registered" if scope in ("registered", "all") else "draft",
                "station": {},
                "devices": [],
            }
            entry = _register_entry(
                "export",
                bench_stub,
                path,
                "pass",
                summary,
                "simulate",
                {"note": payload["purpose"], "download_name": name, "scope": scope,
                 "scope_label": scope_label, "status_filter": payload["status_filter"],
                 "by_status": payload["by_status"]},
            )
            if payload["count"]:
                message = f"已导出 {payload['count']} 台{scope_label}测试台（已注册 {payload['by_status']['registered']} 台 / 草稿 {payload['by_status']['draft']} 台）"
            elif scope == "registered":
                message = "当前没有已注册测试台，已导出空清单"
            elif scope == "all":
                message = "注册库为空，已导出空清单"
            else:
                message = "当前没有待注册测试台，已导出空的待注册清单（可用于线下填写）"
            return {
                "success": True,
                "message": message,
                "count": payload["count"],
                "filename": name,
                "path": str(path),
                "content": payload,
                "report": entry,
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "message": f"导出失败: {e}"}

    # ---------- 装备初始化 / 终止 / 自检 ----------

    def _run_ops(tb_id: str, phase: str, req: Optional[OpsRequest]):
        req = req or OpsRequest()
        mode = "real" if (req.mode or "simulate").lower() == "real" else "simulate"
        timeout = min(max(float(req.timeout or 2.0), 0.2), 10.0)
        with _LOCK:
            reg = tbr.load_registry()
            item = tbr._find_bench(reg, tb_id)
            if not item:
                return {"success": False, "message": f"测试台不存在: {tb_id}"}

            if phase == "selfcheck":
                result = _selfcheck(item, mode, timeout)
                item["verification"] = result
                item["verify_history"] = (item.get("verify_history") or [])[-9:] + [
                    {
                        "at": result["checked_at"],
                        "mode": mode,
                        "overall": result["overall"],
                        "pass": result["summary"]["pass"],
                        "fail": result["summary"]["fail"],
                        "skip": result["summary"]["skip"],
                    }
                ]
            else:
                result = _run_phase(item, phase, mode, timeout)

            name = f"{phase}_{item.get('serial') or item.get('id')}_{_stamp()}.html"
            result["filename"] = name
            bench_view = dict(item)
            bench_view["device_count"] = len(item.get("devices") or [])
            html_text = render_report_html(phase, bench_view, result)
            path = ensure_report_root() / name
            path.write_text(html_text, encoding="utf-8")
            entry = _register_entry(phase, item, path, result["overall"], result["summary"], mode, {"note": req.note})

            if phase != "selfcheck":
                item.setdefault("ops_history", [])
                item["ops_history"] = (item.get("ops_history") or [])[-19:] + [
                    {
                        "phase": phase,
                        "at": result["checked_at"],
                        "mode": mode,
                        "overall": result["overall"],
                        "summary": result["summary"],
                        "report": name,
                    }
                ]
            item["updated_at"] = tbr._now()
            tbr._save_registry(reg)
            public = tbr._public(item)

        return {
            "success": True,
            "phase": phase,
            "message": f"{KIND_LABEL.get(phase, phase)}完成（{MODE_LABEL.get(mode, mode)}）：{result['conclusion']}",
            "result": result,
            "report": entry,
            "testbench": public,
        }

    @app.post("/api/testbenches/{tb_id}/init")
    def init_testbench(tb_id: str, req: Optional[OpsRequest] = None):
        """装备初始化：按设备类别下发复位 / 远程 / 安全态动作，并生成报告"""
        return _run_ops(tb_id, "init", req)

    @app.post("/api/testbenches/{tb_id}/teardown")
    def teardown_testbench(tb_id: str, req: Optional[OpsRequest] = None):
        """装备终止：输出关闭 / 输入关闭 / 转速归零 / 退回本地控制，并生成报告"""
        return _run_ops(tb_id, "teardown", req)

    @app.post("/api/testbenches/{tb_id}/selfcheck")
    def selfcheck_testbench(tb_id: str, req: Optional[OpsRequest] = None):
        """硬件自检：复用注册自检逻辑，结果写回注册库并落盘 HTML 报告"""
        return _run_ops(tb_id, "selfcheck", req)

    # ---------- 报告区（历史报告 + 本地资源管理器） ----------

    @app.get("/api/testbench/reports")
    def list_reports(bench_id: Optional[str] = None, kind: Optional[str] = None, limit: int = 40):
        try:
            with _LOCK:
                data = _prune_index(_load_index())
                _write_json(INDEX_FILE, data)
                entries = list(data.get("entries") or [])
            if bench_id:
                entries = [e for e in entries if e.get("bench_id") == bench_id]
            if kind and kind != "all":
                entries = [e for e in entries if e.get("kind") == kind]
            entries.sort(key=lambda e: str(e.get("created_at", "")), reverse=True)
            # 类型标签以当前定义为准（历史记录里的旧标签名也能统一展示，无需迁移数据）
            for e in entries:
                e["kind_label"] = KIND_LABEL.get(e.get("kind"), e.get("kind_label") or e.get("kind"))
                e["mode_label"] = MODE_LABEL.get(e.get("mode"), e.get("mode_label") or e.get("mode"))
                e["overall_label"] = OVERALL_LABEL.get(e.get("overall"), e.get("overall_label") or e.get("overall"))
            limit = max(1, min(int(limit or 40), 200))
            kinds = {}
            for e in data.get("entries") or []:
                kinds[e.get("kind")] = kinds.get(e.get("kind"), 0) + 1
            scope_counts = {}
            for e in entries:
                if e.get("kind") == "export":
                    scope_counts[e.get("scope") or "pending"] = scope_counts.get(e.get("scope") or "pending", 0) + 1
            return {
                "success": True,
                "count": len(entries),
                "total": len(data.get("entries") or []),
                "dir": str(REPORT_ROOT),
                "kinds": kinds,
                "scopes": scope_counts,
                "reports": entries[:limit],
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "message": f"读取报告清单失败: {e}", "reports": []}

    @app.get("/api/testbench/reports/dir")
    def report_dir_info():
        """报告目录信息：路径 / 文件数 / 占用空间（界面「报告区」展示用）"""
        try:
            root = ensure_report_root()
            files = [p for p in root.iterdir() if p.is_file()]
            return {
                "success": True,
                "path": str(root),
                "exists": True,
                "file_count": len(files),
                "size_bytes": sum(p.stat().st_size for p in files),
                "platform": platform.system(),
                "openable": platform.system() == "Windows" or platform.system() == "Darwin" or os.name == "posix",
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "message": f"读取报告目录失败: {e}", "path": str(REPORT_ROOT)}

    @app.post("/api/testbench/reports/open")
    def open_reports(req: Optional[OpenRequest] = None):
        """用系统资源管理器打开报告目录（或定位到指定报告文件）；只允许打开固定的报告目录"""
        req = req or OpenRequest()
        root = ensure_report_root()
        target = root
        if req.filename:
            path = _report_path(req.filename)
            if not path or not path.is_file():
                return {"success": False, "message": f"报告不存在或文件名非法: {req.filename}", "path": str(root)}
            target = path
        try:
            system = platform.system()
            if system == "Windows":
                if target.is_file():
                    subprocess.Popen(["explorer", "/select,", str(target)])
                else:
                    os.startfile(str(target))  # type: ignore[attr-defined]
            elif system == "Darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "message": f"无法调起资源管理器: {e}",
                "path": str(target),
                "hint": "在工控机上可手动打开该路径",
            }
        return {
            "success": True,
            "message": f"已在资源管理器中打开：{target.name if target.is_file() else target}",
            "path": str(target),
            "is_file": target.is_file(),
        }

    @app.get("/api/testbench/reports/{filename}")
    def get_report(filename: str):
        """返回单份报告的 HTML（界面内 iframe / 新窗口查看）"""
        path = _report_path(filename)
        if not path or not path.is_file():
            return HTMLResponse("<h3>报告不存在或文件名非法</h3>", status_code=404)
        if path.suffix.lower() == ".json":
            return HTMLResponse(
                f"<pre style='white-space:pre-wrap'>{html.escape(path.read_text(encoding='utf-8'))}</pre>"
            )
        return HTMLResponse(path.read_text(encoding="utf-8"))


def _device_ok(dev: dict) -> bool:
    """包一层：注册库侧配置完整度判定"""
    try:
        return bool(tbr._device_configured(dev))
    except Exception:  # noqa: BLE001
        return bool(dev.get("configured"))
