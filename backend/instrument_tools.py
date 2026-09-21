"""ATE 装备助手 - 仪器控制工具后端

职责 (供前端「装备助手」页调用):
  1) 工具卡片清单        -> GET  /api/tools
  2) 可绑定设备列表       -> GET  /api/tools/oscilloscope/bindings
  3) 连接 / 断开 / 状态    -> POST /api/tools/oscilloscope/connect
                            POST /api/tools/oscilloscope/action  (action=close)
                            GET  /api/tools/oscilloscope/state
  4) 示波器操作 (SCPI)    -> POST /api/tools/oscilloscope/action

第一版工具: 数字示波器 (泰克 MSO54 / 5 系列 MSO 方案)
  传输: SCPI over raw socket (泰克默认 socket server, 端口 4000; VXI-11 亦可)
  命令: *IDN? / AUTOSet EXECute / ACQuire:STATE RUN|STOP / ACQuire:STOPAfter SEQuence
        HORizontal:SCAle / CH<x>:SCAle|COUPling|OFFSet|DISplay|POSition
        DATa:* + CURVe? (取波形) / WFMOutpre:* (波形标定) / MEASUrement:ADDMEAS + MEAS<x>:VALUE?
  无硬件时: mode="simulate" 生成与真实命令返回同构的波形与测量值, 离线也能完整演示;
            真实模式下仪器不可达且 allow_fallback=true 时自动降级为模拟并在响应里标注。

数据来源: 装备绑定列表直接读「测试台注册库」(testbenches.json) 中 BOM 清单的设备,
         不额外维护一份设备清单。

说明: 本模块不修改注册库, 只读; 设备属性的修改走 testbench_registry 的 PATCH 接口。
"""

from __future__ import annotations

import math
import random
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel

import testbench_registry as tbr

_LOCK = threading.RLock()
_TPS_DIR: Path = Path(".")
_SESSIONS: dict[str, "ScopeSession"] = {}

# 示波器识别关键词 (泰克机型前缀 + 通用叫法)
SCOPE_HINTS = ("示波器", "oscilloscope", "scope", "mso", "dpo", "mdo", "tds", "dso", "wave")
MEAS_TYPES = ("PK2PK", "AMPLITUDE", "FREQUENCY", "PERIOD", "MEAN", "RMS", "RISE", "FALL")
COUPLINGS = ("DC", "AC", "GND")

# 仿真信号源: 波形类型 / 常用频率档位 / 1-2-5 序列的 V/div
SIGNAL_TYPES = ("sine", "square", "triangle", "sawtooth", "dc", "noise")
SIGNAL_LABELS = {
    "sine": "正弦波",
    "square": "方波",
    "triangle": "三角波",
    "sawtooth": "锯齿波",
    "dc": "直流",
    "noise": "噪声",
}
SIM_FREQS = (10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0, 5000.0,
             10000.0, 20000.0, 50000.0, 100000.0, 200000.0, 500000.0, 1000000.0)
V_DIV_STEPS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)

# 无需硬件的内置仿真信号源 (绑定列表末尾, 不依赖注册库)
VIRTUAL_SIM_DEVICE = {
    "id": "sim-scope",
    "name": "内置仿真信号源",
    "model": "SIM-SCOPE",
    "vendor": "ATE Runner",
    "category": "示波器",
    "role": "离线仿真",
    "interface": "NONE",
    "protocol": "SIMULATED",
    "host": "",
    "port": 0,
    "programmable": True,
    "configured": True,
}
VIRTUAL_BENCH = {
    "id": "__sim__",
    "title": "内置仿真（无需硬件）",
    "serial": "SIM000000000000",
    "status": "registered",
    "preset_name": "内置仿真",
}


def _sim_wave(kind: str, ph: float) -> float:
    """归一化 (-1..1) 的仿真波形, ph 为弧度相位"""
    if kind == "square":
        return 1.0 if math.sin(ph) >= 0 else -1.0
    if kind == "triangle":
        return (2.0 / math.pi) * math.asin(math.sin(ph))
    if kind == "sawtooth":
        return 2.0 * ((ph / (2.0 * math.pi)) % 1.0) - 1.0
    if kind in ("dc", "noise"):
        return 0.0
    return math.sin(ph)
BAUDRATES = (9600, 19200, 38400, 57600, 115200)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _key(bench_id: str, device_id: str) -> str:
    return f"{bench_id}:{device_id}"


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value == 0:
            return "0"
        if abs(value) >= 1e5 or abs(value) < 1e-3:
            return f"{value:.6g}"
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


# ==================== 会话 (真实 socket / 离线模拟) ====================


class ScopeSession:
    """一台示波器的控制会话。真实模式走 SCPI socket, 模拟模式维护内存态。"""

    def __init__(self, bench_id: str, device_id: str, dev: dict, timeout: float = 2.0):
        self.bench_id = bench_id
        self.device_id = device_id
        self.device = dict(dev)
        self.host = str(dev.get("host") or "").strip()
        self.port = int(dev.get("port") or 4000)
        self.timeout = max(0.5, min(float(timeout or 2.0), 10.0))
        self.mode = "real"
        self.sock: Optional[socket.socket] = None
        self.lock = threading.RLock()
        self.log: list[dict] = []
        self.idn = ""
        self.opened_at = ""
        self.running = True
        self.single = False
        self.timebase = 1e-6          # 秒/格
        self.record = 10000           # 记录长度
        self.channels: dict[str, dict] = {
            "CH1": {"display": True, "scale": 1.0, "coupling": "DC", "offset": 0.0, "position": 0.0},
            "CH2": {"display": False, "scale": 1.0, "coupling": "DC", "offset": 0.0, "position": 0.0},
            "CH3": {"display": False, "scale": 1.0, "coupling": "DC", "offset": 0.0, "position": 0.0},
            "CH4": {"display": False, "scale": 1.0, "coupling": "DC", "offset": 0.0, "position": 0.0},
        }
        self.signal = {
            "type": "sine",      # sine / square / triangle / sawtooth / dc / noise
            "freq": 1.0e3,       # Hz
            "amp": 1.2,          # 半幅 (V)
            "vpp": 2.4,          # 峰峰值 (V)
            "offset": 0.0,       # 直流偏置 (V)
            "noise": 0.02,       # 相对噪底 (× 幅度)
            "phase": 0.0,        # 相位 (度)
        }
        # 仿真画面自适配: 屏内周期数 + 是否自动配时基/档位
        self.sim = {"cycles": 5.0, "auto_timebase": True, "auto_scale": True}
        self.virtual = False       # 内置仿真信号源 (未经注册库设备)
        self.last_error = ""

    # ---------- 日志 ----------
    def _log(self, direction: str, text: str) -> None:
        self.log.append({"at": _now(), "dir": direction, "text": text})
        self.log = self.log[-200:]

    # ---------- 连接 ----------
    def connect(self) -> dict:
        if self.mode == "simulate":
            self.idn = self.idn or "TEKTRONIX,MSO54,SIMULATED,CF:91.1CT FV:1.0.0 (仿真信号源)"
            self.opened_at = _now()
            self._apply_sim_frame()
            self._log("info", "已建立仿真会话 (%s %s · 屏内 %.0f 周期)" % (
                SIGNAL_LABELS.get(self.signal.get("type"), "正弦波"), _fmt(self.signal.get("freq")), self.sim.get("cycles", 5)))
            return {"connected": True, "mode": "simulate", "idn": self.idn,
                    "signal": self.signal, "sim": dict(self.sim)}

        if self.sock is not None:
            return {"connected": True, "mode": "real", "idn": self.idn}

        if not self.host or not self.port:
            raise RuntimeError("设备未配置 IP / 端口, 无法连接")

        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self._log("open", f"TCP {self.host}:{self.port}")
        self.idn = self.query("*IDN?").strip()
        self.opened_at = _now()
        # 读回基础状态 (失败不影响连接)
        with self._quiet():
            self.timebase = _as_float(self.query("HORizontal:SCAle?"), self.timebase)
            for ch in self.channels:
                self.channels[ch]["scale"] = _as_float(self.query(f"{ch}:SCAle?"), 1.0)
                self.channels[ch]["coupling"] = (self.query(f"{ch}:COUPling?").strip() or "DC").upper()
                self.channels[ch]["display"] = _as_float(self.query(f"{ch}:DISplay?"), 0.0) > 0.5
                self.channels[ch]["offset"] = _as_float(self.query(f"{ch}:OFFSet?"), 0.0)
        return {"connected": True, "mode": "real", "idn": self.idn}

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
            self._log("close", f"TCP {self.host}:{self.port} 已断开")
        else:
            self._log("info", "会话已结束")

    def _quiet(self):
        return _Quiet(self)

    # ---------- SCPI 读写 ----------
    def write(self, cmd: str) -> None:
        if self.sock is None:
            raise RuntimeError("尚未连接设备")
        self._log("tx", cmd)
        self.sock.sendall((cmd + "\n").encode("ascii", "ignore"))

    def query(self, cmd: str, timeout: Optional[float] = None) -> str:
        if self.sock is None:
            raise RuntimeError("尚未连接设备")
        with self.lock:
            self._log("tx", cmd)
            self.sock.sendall((cmd + "\n").encode("ascii", "ignore"))
            self.sock.settimeout(timeout or self.timeout)
            chunks: list[bytes] = []
            deadline = time.time() + (timeout or self.timeout)
            while time.time() < deadline:
                try:
                    chunk = self.sock.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                if chunk.endswith(b"\n"):
                    break
            text = b"".join(chunks).decode("ascii", "replace").strip()
            self._log("rx", text[:400])
            return text

    def query_float(self, cmd: str, default: Optional[float] = None) -> Optional[float]:
        text = self.query(cmd).strip()
        if text == "":
            return default
        try:
            return float(text.split()[0])
        except (ValueError, IndexError):
            return default

    # ---------- 命令动作 ----------

    # ---------- 仿真信号源 ----------
    def _auto_v_div(self, amp: float) -> float:
        """按幅度挑一档 1-2-5 序列 V/div, 让波形占屏约 60%"""
        target = max(abs(float(amp or 1.0)) * 0.33, 1e-3)
        return min(V_DIV_STEPS, key=lambda s: abs(s - target))

    def _apply_sim_frame(self) -> None:
        """按仿真信号源参数自适配时基与通道档位"""
        sig = self.signal
        if self.sim.get("auto_timebase", True):
            freq = max(float(sig.get("freq") or 1.0), 1e-6)
            cycles = max(1.0, min(float(self.sim.get("cycles") or 5.0), 50.0))
            self.timebase = max(min(cycles / (10.0 * freq), 10.0), 1e-9)
        if self.sim.get("auto_scale", True):
            self.channels["CH1"]["scale"] = self._auto_v_div(sig.get("amp") or 1.0)

    def do_set_signal(self, params: dict) -> dict:
        """设置仿真信号源: 波形类型 / 频率 / Vpp / 偏移 / 噪声 / 相位 / 屏内周期"""
        p = params or {}
        sig = self.signal
        note = ""
        if p.get("type") is not None:
            kind = str(p["type"]).lower()
            if kind in SIGNAL_TYPES:
                sig["type"] = kind
            else:
                note = "不支持的波形类型 %s, 已忽略" % p["type"]
        if p.get("freq") is not None:
            freq = _as_float(p.get("freq"), sig["freq"])
            if freq > 0:
                sig["freq"] = min(max(freq, 1e-3), 1e8)
            else:
                note = note or "频率必须大于 0, 已忽略"
        if p.get("vpp") is not None:
            vpp = _as_float(p.get("vpp"), sig.get("vpp", sig["amp"] * 2.0))
            if vpp > 0:
                sig["vpp"] = min(max(vpp, 0.01), 100.0)
                sig["amp"] = sig["vpp"] / 2.0
        elif p.get("amp") is not None:
            amp = _as_float(p.get("amp"), sig["amp"])
            if amp > 0:
                sig["amp"] = min(max(amp, 0.005), 50.0)
                sig["vpp"] = sig["amp"] * 2.0
        if p.get("offset") is not None:
            sig["offset"] = min(max(_as_float(p.get("offset"), sig["offset"]), -100.0), 100.0)
        if p.get("noise") is not None:
            sig["noise"] = min(max(_as_float(p.get("noise"), sig["noise"]), 0.0), 2.0)
        if p.get("phase") is not None:
            sig["phase"] = _as_float(p.get("phase"), sig["phase"]) % 360.0
        if p.get("cycles") is not None:
            self.sim["cycles"] = min(max(_as_float(p.get("cycles"), 5.0), 1.0), 50.0)
        for k in ("auto_timebase", "auto_scale"):
            if p.get(k) is not None:
                self.sim[k] = bool(p[k])
        if self.mode == "simulate":
            self._apply_sim_frame()
            self._log("info", "仿真信号源: %s %s Hz · Vpp %.4g V · 偏移 %.4g V · 噪声 %.3g · 屏内 %.0f 周期 · 时基 %s s/div" % (
                SIGNAL_LABELS.get(sig["type"], sig["type"]), _fmt(sig["freq"]), sig.get("vpp", sig["amp"] * 2.0),
                sig["offset"], sig["noise"], self.sim["cycles"], _fmt(self.timebase)))
        else:
            self._log("warn", "当前为真实仪器模式, 仿真信号源参数已记录但不影响实测波形")
        return {
            "signal": sig,
            "sim": dict(self.sim),
            "signal_label": SIGNAL_LABELS.get(sig["type"], sig["type"]),
            "timebase": self.timebase,
            "channel_scale": self.channels["CH1"]["scale"],
            "note": note,
        }

    def do_run(self) -> dict:
        if self.mode == "real":
            self.write("ACQuire:STATE RUN")
        self.running = True
        self.single = False
        self._log("info", "采集已开始 (RUN)")
        return {"running": True, "single": False}

    def do_stop(self) -> dict:
        if self.mode == "real":
            self.write("ACQuire:STATE STOP")
        self.running = False
        self._log("info", "采集已停止 (STOP)")
        return {"running": False, "single": False}

    def do_single(self) -> dict:
        if self.mode == "real":
            self.write("ACQuire:STOPAfter SEQuence")
            self.write("ACQuire:STATE RUN")
        self.single = True
        self.running = False
        self._log("info", "单次采集 (SINGLE) 已触发")
        return {"running": False, "single": True}

    def do_autoset(self) -> dict:
        if self.mode == "real":
            self.write("AUTOSet EXECute")
            time.sleep(0.4)
            with self._quiet():
                self.timebase = _as_float(self.query("HORizontal:SCAle?"), self.timebase)
                for ch in self.channels:
                    self.channels[ch]["scale"] = _as_float(self.query(f"{ch}:SCAle?"), 1.0)
        else:
            # 模拟自动设置: 把波形调到 4 格 / 4 周期
            self.channels["CH1"]["scale"] = round(max(self.signal["amp"], 0.05) * 4 / 4, 4)
            self.timebase = round(4.0 / (self.signal["freq"] * 10), 12)
            self.signal["noise"] = 0.02
        self._log("info", "自动设置完成 (AUTOSET)")
        return {"timebase": self.timebase, "channels": self.channels}

    def do_set_timebase(self, scale: Optional[float], position: Optional[float]) -> dict:
        if scale:
            scale = max(1e-12, min(float(scale), 1000.0))
            if self.mode == "real":
                self.write(f"HORizontal:SCAle {_fmt(scale)}")
            self.timebase = scale
        if position is not None and self.mode == "real":
            self.write(f"HORizontal:POSition {_fmt(float(position))}")
        self._log("info", f"时基已设置: {_fmt(self.timebase)} s/div")
        return {"timebase": self.timebase}

    def do_set_channel(self, channel: str, params: dict) -> dict:
        ch = (channel or "CH1").upper()
        if ch not in self.channels:
            raise RuntimeError(f"不支持的通道: {channel}")
        st = self.channels[ch]
        if params.get("display") is not None:
            want = bool(params["display"])
            if self.mode == "real":
                self.write(f"{ch}:DISplay {'1' if want else '0'}")
            st["display"] = want
        if params.get("scale") is not None:
            scale = max(1e-6, min(_as_float(params.get("scale"), st["scale"]), 100.0))
            if self.mode == "real":
                self.write(f"{ch}:SCAle {_fmt(scale)}")
            st["scale"] = scale
            self.sim["auto_scale"] = False      # 手动调过档位后不再自动适配
        if params.get("coupling") is not None:
            cp = str(params["coupling"]).upper()
            if cp not in COUPLINGS:
                raise RuntimeError(f"耦合方式非法: {cp}")
            if self.mode == "real":
                self.write(f"{ch}:COUPling {cp}")
            st["coupling"] = cp
        if params.get("offset") is not None:
            off = _as_float(params.get("offset"), st["offset"])
            if self.mode == "real":
                self.write(f"{ch}:OFFSet {_fmt(off)}")
            st["offset"] = off
        self._log("info", f"{ch} 已更新: {st['scale']} V/div / {st['coupling']} / offset {st['offset']} V")
        return {"channel": ch, "state": st}

    # ---------- 取波形 ----------
    def waveform(self, channel: str = "CH1", points: int = 1000, simulated: bool = False) -> dict:
        ch = (channel or "CH1").upper()
        if ch not in self.channels:
            raise RuntimeError(f"不支持的通道: {channel}")
        points = max(100, min(int(points or 1000), 5000))

        if self.mode == "real" and not simulated:
            return self._waveform_real(ch, points)
        return self._waveform_sim(ch, points)

    def _waveform_real(self, ch: str, points: int) -> dict:
        if self.sock is None:
            raise RuntimeError("尚未连接设备")
        with self.lock:
            self.query(f"DATa:SOUrce {ch}")
            self.query("DATa:ENCdg ASCI")
            total = self.query_float("WFMOutpre:NR_Pt?", None) or self.query_float("DATa:STOP?") or 10000.0
            total = int(max(10.0, min(total, 1_000_000.0)))
            step = max(1, total // points)
            self.write(f"DATa:START 1; DATa:STOP {total}")
            self.write(f"DATa:RESOlution {step}")
            raw = self.query("CURVe?", timeout=max(self.timeout, 5.0))
            values = [v for v in (_as_float(x, None) if x else None for x in raw.replace(";", ",").split(",")) if v is not None]  # type: ignore[arg-type]
            if not values:
                raise RuntimeError("示波器未返回波形数据 (CURVe? 为空)")
            x_incr = self.query_float("WFMOutpre:XINcr?", 1e-6) or 1e-6
            x_zero = self.query_float("WFMOutpre:XZEro?", 0.0) or 0.0
            encdg = (self.query("WFMOutpre:ENCdg?").strip() or "ASCII").upper()
            if encdg.startswith("ASC"):
                volts = values                      # ASCII 编码返回已标定的电平值
            else:                                   # 二进制编码需按标定系数换算
                y_mult = self.query_float("WFMOutpre:YMUlt?", 1.0) or 1.0
                y_off = self.query_float("WFMOutpre:YOFf?", 0.0) or 0.0
                y_zero = self.query_float("WFMOutpre:YZEro?", 0.0) or 0.0
                volts = [(v - y_off) * y_mult + y_zero for v in values]
            step = max(1, len(volts) // points)
            samples = volts[::step][:points]
            return {
                "channel": ch,
                "points": len(samples),
                "samples": [round(v, 6) for v in samples],
                "x_incr": x_incr * step,
                "x_zero": x_zero,
                "encoding": encdg,
                "source": "instrument",
                "timebase": self.timebase,
                "sampled_at": _now(),
            }

    def _waveform_sim(self, ch: str, points: int) -> dict:
        st = self.channels[ch]
        sig = self.signal
        kind = str(sig.get("type") or "sine").lower()
        if kind not in SIGNAL_TYPES:
            kind = "sine"
        # 屏幕窗口 = 10 格 × 时基, 采样间隔按整屏均匀分布 (与真实示波器一致)
        window = max(10.0 * self.timebase, 1e-12)
        x_incr = window / max(1, points)
        rnd = random.Random(int(time.time() * 1000) % 100000)
        amp = sig["amp"] * (1.0 if st["display"] else 0.6)
        offset = sig["offset"] + st["offset"]
        freq = sig["freq"]
        phase = math.radians(_as_float(sig.get("phase"), 0.0))
        noise = max(0.0, _as_float(sig.get("noise"), 0.0)) * abs(sig["amp"])
        if kind == "noise":
            noise = max(noise, abs(sig["amp"]) or 1.0)
        # 防止采样率不足造成非物理的混叠读数 (工程上一般保证 ≥20 点/周期)
        clipped = False
        if kind not in ("dc", "noise") and freq * x_incr > 1.0 / 20.0:
            freq = 1.0 / (20.0 * x_incr)
            clipped = True
        samples = []
        for i in range(points):
            t = i * x_incr
            ph = 2.0 * math.pi * freq * t + phase
            v = offset + amp * _sim_wave(kind, ph)
            if noise:
                v += (rnd.random() - 0.5) * 2.0 * noise
            samples.append(round(v, 6))
        return {
            "channel": ch,
            "points": len(samples),
            "samples": samples,
            "x_incr": x_incr,
            "x_zero": 0.0,
            "encoding": "SIMULATED",
            "source": "simulate",
            "signal_type": kind,
            "signal_label": SIGNAL_LABELS.get(kind, kind),
            "signal_freq": round(freq, 6),
            "signal_vpp": round(amp * 2.0, 6),
            "signal_offset": round(offset, 6),
            "noise_rms": round(noise, 6),
            "clipped": clipped,
            "timebase": self.timebase,
            "sampled_at": _now(),
        }

    # ---------- 测量 ----------
    def measure(self, channel: str = "CH1", types: Optional[list[str]] = None, wave: Optional[dict] = None) -> list[dict]:
        ch = (channel or "CH1").upper()
        want = [str(t).upper() for t in (types or list(MEAS_TYPES[:6])) if str(t).upper() in MEAS_TYPES]
        if self.mode == "real" and wave is None:
            return self._measure_real(ch, want)
        wave = wave or self.waveform(ch, 1000)
        return self._measure_sim(ch, want, wave)

    def _measure_real(self, ch: str, types: list[str]) -> list[dict]:
        out: list[dict] = []
        for idx, mtype in enumerate(types, start=1):
            item: dict = {"type": mtype, "value": None, "unit": _unit_of(mtype), "source": "instrument"}
            try:
                with self.lock:
                    self.write(f"MEASUrement:ADDMEAS {mtype}")
                    self.write(f"MEASUrement:MEAS{idx}:SOUrce1 {ch}")
                    val = self.query_float(f"MEASUrement:MEAS{idx}:VALUE?")
                item["value"] = val
                if val is None:
                    item["source"] = "error"
                    item["error"] = "仪器未返回测量值 (可能信号未触发或无有效边沿)"
            except Exception as e:  # 单个测量失败不影响其他项
                item["source"] = "error"
                item["error"] = str(e)
            out.append(item)
        return out

    def _measure_sim(self, ch: str, types: list[str], wave: dict) -> list[dict]:
        s = wave.get("samples") or []
        x_incr = _as_float(wave.get("x_incr"), 1e-7)
        if not s:
            return [{"type": t, "value": None, "unit": _unit_of(t), "source": "error", "error": "无波形数据"} for t in types]
        vmax, vmin = max(s), min(s)
        mean = sum(s) / len(s)
        rms = math.sqrt(sum(v * v for v in s) / len(s))
        vpp = vmax - vmin
        mid = (vmax + vmin) / 2.0
        # 幅度按示波器习惯取 Top/Base 直方图均值差 (对正弦≈峰峰值, 与偏置无关)
        top_n = max(1, len(s) // 20)
        srt = sorted(s)
        base = sum(srt[:top_n]) / top_n
        top = sum(srt[-top_n:]) / top_n
        # 上升沿: 带迟滞的过中点检测, 避免噪声在中点附近产生伪穿越
        hyst = max(0.02 * vpp, 1e-9)
        hi_th, lo_th = mid + hyst, mid - hyst
        armed = False
        crossings: list[int] = []
        for i, v in enumerate(s):
            if v < lo_th:
                armed = True
            elif armed and v >= hi_th:
                crossings.append(i)
                armed = False
        period = None
        if len(crossings) >= 2:
            gaps = [(crossings[i] - crossings[i - 1]) * x_incr for i in range(1, len(crossings))]
            gaps = [g for g in gaps if g > 0]
            if gaps:
                med = sorted(gaps)[len(gaps) // 2]
                keep = [g for g in gaps if 0.5 * med <= g <= 2.0 * med] or gaps
                period = sum(keep) / len(keep)
        freq = 1.0 / period if period else self.signal["freq"]
        # 10%/90% 上升时间
        lo, hi = vmin + 0.1 * vpp, vmin + 0.9 * vpp
        rise = None
        for i in range(1, len(s)):
            if s[i - 1] < lo <= s[i]:
                j = i
                while j < len(s) and s[j] < hi:
                    j += 1
                if j < len(s):
                    rise = (j - i) * x_incr
                break
        table = {
            "PK2PK": vpp,
            "AMPLITUDE": top - base,
            "FREQUENCY": freq,
            "PERIOD": period or (1.0 / freq if freq else None),
            "MEAN": mean,
            "RMS": rms,
            "RISE": rise,
            "FALL": rise,
        }
        return [
            {
                "type": t,
                "value": round(table.get(t), 9) if isinstance(table.get(t), float) else table.get(t),
                "unit": _unit_of(t),
                "source": "simulate",
            }
            for t in types
        ]

    # ---------- 状态 ----------
    def state(self) -> dict:
        return {
            "bench_id": self.bench_id,
            "device_id": self.device_id,
            "name": self.device.get("name", ""),
            "model": self.device.get("model", ""),
            "vendor": self.device.get("vendor", ""),
            "host": self.host,
            "port": self.port,
            "interface": self.device.get("interface", ""),
            "mode": self.mode,
            "connected": bool(self.sock) or self.mode == "simulate",
            "idn": self.idn,
            "opened_at": self.opened_at,
            "running": self.running,
            "single": self.single,
            "timebase": self.timebase,
            "record": self.record,
            "channels": self.channels,
            "signal": self.signal,
            "sim": dict(self.sim),
            "virtual": self.virtual,
            "signal_label": SIGNAL_LABELS.get(str(self.signal.get("type") or "sine"), ""),
            "log": self.log[-40:],
            "last_error": self.last_error,
        }


class _Quiet:
    """with session._quiet(): 块内 SCPI 失败静默忽略"""

    def __init__(self, session: ScopeSession):
        self.session = session

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return True


def _unit_of(mtype: str) -> str:
    return {
        "PK2PK": "V",
        "AMPLITUDE": "V",
        "FREQUENCY": "Hz",
        "PERIOD": "s",
        "MEAN": "V",
        "RMS": "V",
        "RISE": "s",
        "FALL": "s",
    }.get(mtype.upper(), "")


# ==================== 装备绑定 (读注册库 BOM 设备) ====================


def _is_scope(dev: dict) -> bool:
    blob = " ".join(str(dev.get(k) or "") for k in ("name", "model", "vendor", "category", "role")).lower()
    return any(h in blob for h in SCOPE_HINTS)


def _bindings() -> list[dict]:
    reg = tbr.load_registry()
    out: list[dict] = []
    for bench in reg.get("testbenches", []):
        for dev in bench.get("devices") or []:
            out.append(
                {
                    "bench_id": bench.get("id", ""),
                    "bench_title": bench.get("title", ""),
                    "bench_serial": bench.get("serial", ""),
                    "bench_status": bench.get("status", "draft"),
                    "preset_name": bench.get("preset_name", ""),
                    "device_id": dev.get("id", ""),
                    "name": dev.get("name", ""),
                    "model": dev.get("model", ""),
                    "vendor": dev.get("vendor", ""),
                    "category": dev.get("category", ""),
                    "interface": dev.get("interface", ""),
                    "host": dev.get("host", ""),
                    "port": dev.get("port"),
                    "protocol": dev.get("protocol", ""),
                    "configured": bool(dev.get("configured")),
                    "programmable": bool(dev.get("programmable")),
                    "is_scope": _is_scope(dev),
                    "bindable": str(dev.get("interface", "")).upper() in ("LAN", "ETHERNET", "TCP", "IP"),
                }
            )
    out.sort(key=lambda d: (not d["bindable"], not d["is_scope"], d["bench_title"]))
    # 末尾附一个内置仿真信号源 (无需硬件, 不依赖注册库)
    out.append({
        "bench_id": VIRTUAL_BENCH["id"],
        "bench_title": VIRTUAL_BENCH["title"],
        "bench_serial": VIRTUAL_BENCH["serial"],
        "bench_status": "registered",
        "preset_name": VIRTUAL_BENCH["preset_name"],
        "device_id": VIRTUAL_SIM_DEVICE["id"],
        "name": VIRTUAL_SIM_DEVICE["name"],
        "model": VIRTUAL_SIM_DEVICE["model"],
        "vendor": VIRTUAL_SIM_DEVICE["vendor"],
        "category": VIRTUAL_SIM_DEVICE["category"],
        "interface": VIRTUAL_SIM_DEVICE["interface"],
        "host": "",
        "port": None,
        "protocol": VIRTUAL_SIM_DEVICE["protocol"],
        "configured": True,
        "programmable": True,
        "is_scope": True,
        "bindable": True,
        "virtual": True,
        "signal": None,
    })
    return out


TOOL_CARDS: list[dict] = [
    {
        "id": "oscilloscope",
        "name": "数字示波器",
        "title": "数字示波器",
        "subtitle": "泰克 MSO54 方案 · SCPI over socket",
        "desc": "绑定已注册测试台中的示波器，远程完成自动设置、时基与通道调整、采集控制、波形读取与参数测量；无硬件时可切仿真信号源（正弦/方波/三角/锯齿/直流/噪声，频率与幅度可调）。",
        "icon": "wave",
        "vendor": "Tektronix",
        "model": "MSO54",
        "protocol": "SCPI / raw socket (4000)",
        "available": True,
        "capabilities": ["连接与识别", "自动设置", "运行/停止/单次", "时基与通道", "波形读取", "参数测量", "仿真信号源", "离线仿真"],
        "module": "instrument_tools.py",
    },
    {
        "id": "dmm",
        "name": "数字万用表",
        "title": "数字万用表",
        "subtitle": "规划中 · 预留工位",
        "desc": "直流/交流电压电流、电阻、通断测量与连续采集趋势。",
        "icon": "meter",
        "vendor": "—",
        "model": "—",
        "protocol": "SCPI / USBTMC",
        "available": False,
        "capabilities": ["电压/电流", "电阻/通断", "趋势记录"],
        "module": "",
    },
    {
        "id": "psu",
        "name": "可编程电源",
        "title": "可编程电源",
        "subtitle": "规划中 · 预留工位",
        "desc": "设定输出电压/电流限值、上下电时序、电压电流回读。",
        "icon": "power",
        "vendor": "—",
        "model": "—",
        "protocol": "SCPI / LAN",
        "available": False,
        "capabilities": ["电压/电流设定", "上电时序", "回读"],
        "module": "",
    },
    {
        "id": "eload",
        "name": "电子负载",
        "title": "电子负载",
        "subtitle": "规划中 · 预留工位",
        "desc": "恒流 / 恒阻 / 恒功率带载与功率回读。",
        "icon": "load",
        "vendor": "—",
        "model": "—",
        "protocol": "SCPI / LAN",
        "available": False,
        "capabilities": ["CC/CV/CR 模式", "带载曲线", "功率回读"],
        "module": "",
    },
]


# ==================== 请求模型 ====================


class ConnectRequest(BaseModel):
    bench_id: str = ""
    device_id: str = ""
    mode: str = "real"            # real(真实仪器) / simulate(离线演示)
    timeout: float = 2.0
    allow_fallback: bool = True   # 真实模式连不上时自动降级为模拟


class ActionRequest(BaseModel):
    bench_id: str = ""
    device_id: str = ""
    action: str = ""
    channel: str = "CH1"
    params: dict = {}


# ==================== 路由 ====================


def register_routes(app: FastAPI, tps_dir: Path) -> None:
    global _TPS_DIR
    _TPS_DIR = Path(tps_dir)

    def _get_session(bench_id: str, device_id: str) -> Optional[ScopeSession]:
        return _SESSIONS.get(_key(bench_id, device_id))

    # ---------- 工具卡片 ----------

    @app.get("/api/tools")
    def list_tools():
        return {
            "success": True,
            "count": len(TOOL_CARDS),
            "available_count": len([c for c in TOOL_CARDS if c.get("available")]),
            "tools": TOOL_CARDS,
        }

    @app.get("/api/tools/oscilloscope/bindings")
    def oscilloscope_bindings(scope_only: bool = False):
        """可绑定的示波器设备 (来自测试台注册 BOM 清单)"""
        try:
            rows = _bindings()
        except Exception as e:
            return {"success": False, "message": f"读取装备绑定失败: {e}", "devices": []}
        bindable = [d for d in rows if d["bindable"]]
        if scope_only:
            bindable = [d for d in bindable if d["is_scope"]]
        scope_like = [d for d in bindable if d["is_scope"]]
        return {
            "success": True,
            "count": len(bindable),
            "scope_count": len(scope_like),
            "devices": bindable,
            "hint": "未找到示波器设备时, 可先在「装备属性配置」页确认设备 IP / 端口" if not scope_like else "",
        }

    @app.get("/api/tools/oscilloscope/state")
    def oscilloscope_state(bench_id: str = "", device_id: str = ""):
        sess = _get_session(bench_id, device_id)
        if not sess:
            return {"success": True, "connected": False, "state": None}
        return {"success": True, "connected": True, "state": sess.state()}

    # ---------- 连接 ----------

    @app.post("/api/tools/oscilloscope/connect")
    def oscilloscope_connect(req: ConnectRequest):
        bench_id, device_id = req.bench_id, req.device_id
        want_sim = (req.mode or "real").lower() == "simulate"

        # 仿真模式: 未选设备/设备不存在时, 自动改用内置仿真信号源 (无硬件演示)
        virtual = False
        if want_sim and (not bench_id or not device_id or str(bench_id) == VIRTUAL_BENCH["id"]):
            bench_id, device_id = VIRTUAL_BENCH["id"], VIRTUAL_SIM_DEVICE["id"]
            bench, dev = dict(VIRTUAL_BENCH), dict(VIRTUAL_SIM_DEVICE)
            virtual = True
        else:
            bench, dev = tbr.find_device(bench_id, device_id)
            if want_sim and not dev:
                bench_id, device_id = VIRTUAL_BENCH["id"], VIRTUAL_SIM_DEVICE["id"]
                bench, dev = dict(VIRTUAL_BENCH), dict(VIRTUAL_SIM_DEVICE)
                virtual = True
            elif not bench:
                return {"success": False, "message": f"测试台不存在: {bench_id}"}
            elif not dev:
                return {"success": False, "message": f"该测试台下没有设备: {device_id}"}

        key = _key(bench_id, device_id)

        with _LOCK:
            old = _SESSIONS.pop(key, None)
            if old:
                old.close()

            sess = ScopeSession(bench_id, device_id, dev, timeout=req.timeout)
            sess.mode = "simulate" if want_sim else "real"
            sess.virtual = virtual
            try:
                info = sess.connect()
                _SESSIONS[key] = sess
                return {
                    "success": True,
                    "message": ("已连接示波器" if not want_sim
                                else ("已进入离线仿真模式（内置仿真信号源，无需硬件）" if virtual else "已进入离线仿真模式")),
                    "state": sess.state(),
                    "idn": info.get("idn", ""),
                    "fallback": False,
                    "virtual": virtual,
                }
            except Exception as e:
                err = str(e) or e.__class__.__name__
                if want_sim or not req.allow_fallback:
                    return {
                        "success": False,
                        "message": f"连接失败: {err}",
                        "state": sess.log and {"log": sess.log} or None,
                        "error": err,
                    }
                # 真实模式连不上 → 自动降级为离线模拟, 明确标注
                sess.close()
                sess = ScopeSession(bench_id, device_id, dev, timeout=req.timeout)
                sess.mode = "simulate"
                sess.last_error = err
                sess.connect()
                sess._log("warn", f"真实连接失败({err}), 已自动降级为离线模拟")
                _SESSIONS[key] = sess
                return {
                    "success": True,
                    "message": f"无法连接 {sess.host}:{sess.port} ({err})，已自动切换为离线仿真（波形来自仿真信号源，非真机实测）",
                    "state": sess.state(),
                    "idn": sess.idn,
                    "fallback": True,
                    "error": err,
                }

    # ---------- 操作 ----------

    @app.post("/api/tools/oscilloscope/action")
    def oscilloscope_action(req: ActionRequest):
        key = _key(req.bench_id, req.device_id)
        sess = _get_session(req.bench_id, req.device_id)
        if not sess:
            return {"success": False, "message": "尚未连接示波器, 请先点击「运行」建立连接"}

        action = (req.action or "").strip().lower()
        p = req.params or {}
        before = len(sess.log)
        result: Any = None

        try:
            with sess.lock:
                if action == "identify":
                    result = {"idn": sess.idn or (sess.query("*IDN?").strip() if sess.mode == "real" else sess.idn)}
                elif action == "run":
                    result = sess.do_run()
                elif action == "stop":
                    result = sess.do_stop()
                elif action == "single":
                    result = sess.do_single()
                elif action == "autoset":
                    result = sess.do_autoset()
                elif action in ("timebase", "set_timebase"):
                    result = sess.do_set_timebase(p.get("scale"), p.get("position"))
                elif action in ("channel", "set_channel"):
                    result = sess.do_set_channel(req.channel or p.get("channel") or "CH1", p)
                elif action in ("sim_signal", "signal", "set_signal"):
                    result = sess.do_set_signal(p)
                elif action == "acquire":
                    result = sess.waveform(req.channel or p.get("channel") or "CH1", int(p.get("points") or 1000))
                elif action == "measure":
                    result = sess.measure(req.channel or p.get("channel") or "CH1", p.get("types"))
                elif action == "frame":
                    wave = sess.waveform(req.channel or p.get("channel") or "CH1", int(p.get("points") or 1000))
                    meas = sess.measure(req.channel or p.get("channel") or "CH1", p.get("types"), wave=wave)
                    result = {"waveform": wave, "measurements": meas}
                elif action == "close":
                    sess.close()
                    with _LOCK:
                        _SESSIONS.pop(key, None)
                    result = {"connected": False}
                else:
                    return {"success": False, "message": f"不支持的操作: {req.action}"}
        except Exception as e:
            sess.last_error = str(e) or e.__class__.__name__
            sess._log("err", f"{action} 失败: {sess.last_error}")
            return {
                "success": False,
                "message": f"{req.action} 失败: {sess.last_error}",
                "log": sess.log[before:],
                "state": sess.state(),
            }

        return {
            "success": True,
            "action": action,
            "mode": sess.mode,
            "result": result,
            "log": sess.log[before:],
            "state": sess.state(),
        }
