# -*- coding: utf-8 -*-
"""装备管理模块（DM）服务进程

职责
    1. 持有所有外设驱动连接：驱动实例只在这里存在，测试程序、界面、脚本都通过它操作设备；
    2. 控制面：命名管道（Windows）/ 本地 TCP（其他平台）上的请求-响应，长度前缀 + JSON；
    3. 数据面：大块采样数据写入共享内存环，控制面只回描述符，避免大数组挤占指令通道；
    4. 可观测：请求日志、每设备调用计数、共享内存用量，供「装备属性配置 → 连接管理」展示。

启动
    python -m dm_broker --bench line1 --endpoint .ate_rt/dm_endpoint.json
    （通常由 Runner 的 /api/dm/start 拉起，无需人工执行）

生命周期
    客户端全部断开后不自动退出（界面可能还要看状态）；带 --idle-exit N 时，
    连续 N 秒没有任何连接与请求则自行退出。Runner 侧调用 shutdown 动作可优雅退出。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import zlib
from collections import deque
from pathlib import Path
from typing import Any, Optional

BACKEND = Path(__file__).resolve().parent
SDK = BACKEND.parent / "sdk"
for _p in (str(BACKEND), str(SDK)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import drivers  # noqa: E402  驱动层（进程内唯一持有者）
from ate_dm import protocol as proto  # noqa: E402  与客户端共用同一份协议常量
from ate_dm.shm import (  # noqa: E402
    DEFAULT_BLOCK, DEFAULT_DTYPE, DEFAULT_SLOTS, DTYPE_MAP, HEADER, HEADER_SIZE, MAGIC,
)

PIPE_BASE = r"\\.\pipe\ate_dm_v1"
# 整型下发时的取值域：超界夹取，而不是抛 OverflowError
_INT_RANGES = {"i": (-2 ** 31, 2 ** 31 - 1), "l": (-2 ** 63, 2 ** 63 - 1),
               "B": (0, 255), "h": (-32768, 32767)}
ARRAY_KEYS = ("samples", "points_array", "waveform", "data", "values", "y", "trace", "curve")
LOG_LIMIT = 300


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------- 共享内存环


class ShmRing:
    """固定槽位的共享内存环：写入即返回描述符，客户端读完回 release"""

    def __init__(self, bench: str, slots: int = DEFAULT_SLOTS, block: int = DEFAULT_BLOCK) -> None:
        from multiprocessing import shared_memory
        self.bench = bench
        self.slots = max(2, int(slots))
        self.block = max(HEADER_SIZE + 1024, int(block))
        self.name = f"ate_dm_{bench or 'default'}_{os.getpid()}"
        self.mem = shared_memory.SharedMemory(create=True, size=self.slots * self.block, name=self.name)
        self._seq = 0
        self._next = 0
        self._live: dict[int, int] = {}
        self._lock = threading.Lock()
        self.bytes_written = 0

    def write(self, payload: bytes, dtype: str, shape: list) -> dict:
        if len(payload) + HEADER_SIZE > self.block:
            raise proto.DmError(
                f"数据块 {len(payload)} 字节超过单槽上限 {self.block - HEADER_SIZE}，"
                f"请减少采样点或调大 --block",
                proto.ERR_TOO_LARGE,
            )
        with self._lock:
            slot = self._next
            self._next = (self._next + 1) % self.slots
            self._seq += 1
            seq = self._seq
            off = slot * self.block
            crc = zlib.crc32(payload) & 0xFFFFFFFF
            try:
                self.mem.buf[off:off + HEADER_SIZE] = HEADER.pack(MAGIC, seq, len(payload), crc)
                self.mem.buf[off + HEADER_SIZE:off + HEADER_SIZE + len(payload)] = payload
            except Exception as exc:      # 段被外部 close 或越界
                raise proto.DmError(f"写共享内存失败：{exc}", proto.ERR_STREAM) from exc
            self._live[seq] = slot
            self.bytes_written += len(payload)
            # 只保留最近 slots 条，更早的块视为已被覆盖
            for old in [s for s in self._live if s <= seq - self.slots]:
                self._live.pop(old, None)
            return {"shm": self.name, "offset": off, "nbytes": len(payload), "seq": seq,
                    "dtype": dtype, "shape": shape, "crc32": crc, "slot": slot}

    def release(self, seq: int) -> bool:
        with self._lock:
            return self._live.pop(int(seq), None) is not None

    def info(self) -> dict:
        with self._lock:
            return {"shm": self.name, "slots": self.slots, "block": self.block,
                    "total_bytes": self.slots * self.block, "live_blocks": len(self._live),
                    "seq": self._seq, "bytes_written": self.bytes_written}

    def close(self) -> None:
        try:
            self.mem.close()
            self.mem.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------- 驱动槽位


class DriverSlot:
    """一台设备的唯一驱动实例 + 引用计数 + 串行化锁"""

    def __init__(self, key: str, driver: Any, meta: dict) -> None:
        self.key = key
        self.driver = driver
        self.meta = meta
        self.lock = threading.RLock()
        self.refs = 0
        self.calls = 0
        self.errors = 0
        self.opened_at = _now()
        self.last_used = _now()

    def view(self) -> dict:
        return {
            "key": self.key,
            "alias": self.meta.get("alias", ""),
            "device_id": self.meta.get("device_id", ""),
            "name": self.meta.get("name", ""),
            "driver": self.meta.get("driver", ""),
            "resource": self.meta.get("resource", ""),
            "idn": self.meta.get("idn", ""),
            "simulated": self.meta.get("simulated", False),
            "refs": self.refs,
            "calls": self.calls,
            "errors": self.errors,
            "opened_at": round(self.opened_at, 3),
            "last_used": round(self.last_used, 3),
            "idle_s": round(_now() - self.last_used, 1),
        }


# ---------------------------------------------------------------- 服务端


class DmServer:
    def __init__(self, bench: str = "", endpoint_path: str = "", log_path: str = "",
                 slots: int = DEFAULT_SLOTS, block: int = DEFAULT_BLOCK,
                 idle_exit: float = 0.0, force_tcp: bool = False, hard_exit: bool = False) -> None:
        self.bench = bench or ""
        self.endpoint_path = Path(endpoint_path) if endpoint_path else (BACKEND / ".ate_rt" / "dm_endpoint.json")
        self.log_path = Path(log_path) if log_path else None
        self.idle_exit = float(idle_exit or 0.0)
        self.hard_exit = bool(hard_exit)
        self.force_tcp = bool(force_tcp)
        self.started_at = _now()
        self.ring = ShmRing(self.bench or "default", slots, block)
        self.slots: dict[str, DriverSlot] = {}
        self.slots_lock = threading.RLock()
        self.sessions: dict[int, dict] = {}
        self.sessions_lock = threading.RLock()
        self.handles: dict[int, str] = {}
        self._handle_seq = 0
        self._client_seq = 0
        self.requests = 0
        self.errors = 0
        self.streamed = 0
        self.log_lines: deque = deque(maxlen=LOG_LIMIT)
        self.authkey = os.urandom(16)
        self._address: Any = None
        self._family = "AF_PIPE"
        self._stop = threading.Event()
        self._listener = None
        self.hard_exit = False
        self._last_activity = _now()

    # ---------------- 端点 ----------------

    def _listen_address(self) -> tuple:
        if sys.platform == "win32" and not self.force_tcp:
            name = PIPE_BASE + (f"_{self.bench}" if self.bench else "")
            return (name, "AF_PIPE")
        # 其他平台或强制 TCP：随机端口，地址写进端点文件供客户端发现
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return (("127.0.0.1", port), "AF_INET")

    def _write_endpoint(self, address: Any, family: str) -> None:
        payload = {
            "kind": "pipe" if family == "AF_PIPE" else "tcp",
            "address": address if isinstance(address, str) else f"{address[0]}:{address[1]}",
            "bench": self.bench,
            "pid": os.getpid(),
            "protocol": proto.PROTOCOL_VERSION,
            "shm": self.ring.name,
            "slots": self.ring.slots,
            "block": self.ring.block,
            "started_at": round(self.started_at, 3),
            "authkey": self.authkey.hex(),
        }
        try:
            self.endpoint_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.endpoint_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.endpoint_path)
        except Exception as exc:            # 端点文件写不出去，服务等于不可用
            print(f"[dm] 写端点文件失败: {exc}", file=sys.stderr)

    # ---------------- 主循环 ----------------

    def serve_forever(self) -> None:
        """accept 放在后台线程，主线程只等停止信号

        Windows 下 Listener.close() 不保证让阻塞中的 accept() 立即返回。早先把 accept
        放在主线程会导致 stop() 之后进程停不下来（端点文件也清不掉）。现在主线程等
        停止事件 → 清理 → 退出；accept 线程是 daemon，随进程结束。
        """
        from multiprocessing.connection import Listener
        address, family = self._listen_address()
        try:
            self._listener = Listener(address, family=family, authkey=self.authkey)
        except (PermissionError, OSError) as exc:
            # 同名管道仍被上一个实例占用（重启/强杀残留）：退回带 pid 的唯一名字
            if family != "AF_PIPE":
                raise
            alt = f"{address}_{os.getpid()}"
            print(f"[dm] 管道 {address} 被占用（{exc}），改用 {alt}", file=sys.stderr, flush=True)
            address = alt
            self._listener = Listener(address, family=family, authkey=self.authkey)
        self._address, self._family = address, family
        self._write_endpoint(address, family)
        print(f"[dm] 装备管理模块已启动 bench={self.bench or '-'} "
              f"addr={address} shm={self.ring.name} slots={self.ring.slots} block={self.ring.block}",
              flush=True)
        threading.Thread(target=self._accept_loop, name="dm-accept", daemon=True).start()
        if self.idle_exit > 0:
            threading.Thread(target=self._idle_watch, name="dm-idle", daemon=True).start()
        self._stop.wait()
        self._cleanup()
        if self.hard_exit:
            os._exit(0)

    def _accept_loop(self) -> None:
        """在后台线程里 accept，避免主线程卡在不可中断的 accept 上"""
        while not self._stop.is_set():
            try:
                conn = self._listener.accept()
            except Exception:
                if self._stop.is_set():
                    break
                time.sleep(0.2)
                continue
            if self._stop.is_set():
                try:
                    conn.close()
                except Exception:
                    pass
                break
            threading.Thread(target=self._serve_conn, args=(conn,), name="dm-conn",
                             daemon=True).start()

    def _idle_watch(self) -> None:
        while not self._stop.is_set():
            time.sleep(1.0)
            with self.sessions_lock:
                busy = bool(self.sessions)
            if not busy and (_now() - self._last_activity) > self.idle_exit:
                print("[dm] 空闲超时，自动退出", flush=True)
                self.stop()
                break

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._listener is not None:
                self._listener.close()
        except Exception:
            pass
        # Windows 上 Listener.close() 不保证唤醒阻塞中的 accept()：
        # 主动连一次把主线程踢出 accept，否则进程停不下来（端点文件也不会清理）
        try:
            from multiprocessing.connection import Client
            if self._address is not None:
                Client(self._address, family=self._family, authkey=self.authkey).close()
        except Exception:
            pass

    def _cleanup(self) -> None:
        with self.slots_lock:
            for slot in list(self.slots.values()):
                try:
                    slot.driver.close()
                except Exception:
                    pass
            self.slots.clear()
        self.ring.close()
        try:
            if self.endpoint_path.exists():
                doc = json.loads(self.endpoint_path.read_text(encoding="utf-8"))
                if int(doc.get("pid", 0)) == os.getpid():
                    self.endpoint_path.unlink()
        except Exception:
            pass
        print("[dm] 装备管理模块已退出", flush=True)

    # ---------------- 连接处理 ----------------

    def _serve_conn(self, conn: Any) -> None:
        with self.sessions_lock:
            self._client_seq += 1
            cid = self._client_seq
            self.sessions[cid] = {"id": cid, "name": "", "connected_at": _now(),
                                  "calls": 0, "handles": []}
        name = f"client-{cid}"
        try:
            hello = conn.recv_bytes()
            info = proto.loads(hello) or {}
            name = str(info.get("name") or name)
            with self.sessions_lock:
                self.sessions[cid]["name"] = name
            conn.send_bytes(proto.make_ok(int(info.get("id", 0)),
                                          {"session": cid, "protocol": proto.PROTOCOL_VERSION}))
            while not self._stop.is_set():
                raw = conn.recv_bytes()
                self._last_activity = _now()
                resp = self._handle(raw, cid)
                conn.send_bytes(resp)
        except (EOFError, OSError):
            pass
        except Exception as exc:                      # 单连接异常不能带走整个服务
            print(f"[dm] 连接处理异常 client={cid}: {exc}", file=sys.stderr)
            try:
                conn.send_bytes(proto.make_err(0, proto.ERR_INTERNAL, f"服务端异常：{exc}"))
            except Exception:
                pass
        finally:
            self._drop_session(cid)
            try:
                conn.close()
            except Exception:
                pass

    def _drop_session(self, cid: int) -> None:
        with self.sessions_lock:
            sess = self.sessions.pop(cid, None)
        if not sess:
            return
        for handle in list(sess.get("handles") or []):
            try:
                self._release_handle(int(handle))
            except Exception:
                pass

    # ---------------- 请求分发 ----------------

    def _handle(self, raw: bytes, cid: int) -> bytes:
        t0 = time.perf_counter()
        rid = 0
        op = "?"
        detail = ""
        try:
            msg = proto.loads(raw)
            rid = int(msg.get("id", 0))
            op = str(msg.get("op") or "")
            args = msg.get("args") or {}
            handler = getattr(self, f"_op_{op}", None)
            if handler is None:
                raise proto.DmError(f"未知操作: {op}", proto.ERR_UNKNOWN_OP)
            result = handler(cid, args)
            detail = self._detail_of(op, args, result)
            return proto.make_ok(rid, result)
        except proto.DmError as exc:
            self.errors += 1
            return proto.make_err(rid, exc.code, str(exc), exc.detail)
        except Exception as exc:                      # 驱动内部异常统一转成可读错误
            self.errors += 1
            return proto.make_err(rid, proto.ERR_DRIVER, f"{type(exc).__name__}: {exc}")
        finally:
            ms = (time.perf_counter() - t0) * 1000.0
            self.requests += 1
            self._log(cid, op, ms, detail)

    @staticmethod
    def _detail_of(op: str, args: dict, result: Any) -> str:
        if op in ("call", "stream"):
            h = args.get("handle")
            a = args.get("action") or args.get("dtype") or ""
            return f"h{h} {a}"
        if op == "connect":
            return f"{args.get('alias') or (args.get('device') or {}).get('device_id', '')}"
        if op == "disconnect":
            return f"h{args.get('handle')}"
        return ""

    def _log(self, cid: int, op: str, ms: float, detail: str) -> None:
        line = {"ts": round(_now(), 3), "client": cid, "op": op, "detail": detail,
                "ms": round(ms, 2)}
        self.log_lines.append(line)
        if self.log_path:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            except Exception:
                pass

    # ---------------- 操作实现 ----------------

    def _op_ping(self, cid: int, args: dict) -> dict:
        return {"pong": True, "bench": self.bench, "pid": os.getpid(),
                "protocol": proto.PROTOCOL_VERSION, "uptime_s": round(_now() - self.started_at, 1)}

    def _op_caps(self, cid: int, args: dict) -> dict:
        return {"protocol": proto.PROTOCOL_VERSION, "ops": list(proto.OPS),
                "dtypes": list(DTYPE_MAP), "max_block": self.ring.block - HEADER_SIZE,
                "slots": self.ring.slots, "bench": self.bench, "shm": self.ring.name}

    def _slot_of(self, handle: int) -> DriverSlot:
        key = self.handles.get(int(handle))
        if not key:
            raise proto.DmError(f"句柄无效或已释放: {handle}", proto.ERR_NO_HANDLE)
        slot = self.slots.get(key)
        if slot is None:
            raise proto.DmError(f"设备会话已失效: {key}", proto.ERR_NO_DEVICE)
        return slot

    def _op_connect(self, cid: int, args: dict) -> dict:
        device = args.get("device") or {}
        alias = str(args.get("alias") or "")
        mode = str(args.get("mode") or "simulate")
        if not device.get("device_id"):
            raise proto.DmError("connect 需要包含 device_id 的设备登记信息", proto.ERR_BAD_REQUEST)
        key = alias or str(device.get("device_id"))
        with self.slots_lock:
            slot = self.slots.get(key)
            if slot is not None:
                slot.refs += 1
                slot.last_used = _now()
            else:
                try:
                    driver = drivers.get_driver(device, mode=mode, alias=alias)
                    info = driver.open()
                except Exception as exc:
                    raise proto.DmError(f"打开设备失败（{key}）：{exc}", proto.ERR_DRIVER) from exc
                slot = DriverSlot(key, driver, {
                    "alias": alias,
                    "device_id": str(device.get("device_id")),
                    "name": device.get("name", ""),
                    "driver": info.get("driver", ""),
                    "resource": info.get("resource", ""),
                    "idn": info.get("idn", ""),
                    "simulated": bool(info.get("simulated")),
                })
                slot.refs = 1
                self.slots[key] = slot
            self._handle_seq += 1
            handle = self._handle_seq
            self.handles[handle] = key
        with self.sessions_lock:
            self.sessions[cid]["handles"].append(handle)
        return {"handle": handle, "alias": slot.meta["alias"], "device_id": slot.meta["device_id"],
                "driver": slot.meta["driver"], "resource": slot.meta["resource"],
                "idn": slot.meta["idn"], "simulated": slot.meta["simulated"],
                "refs": slot.refs}

    def _release_handle(self, handle: int) -> None:
        key = self.handles.pop(int(handle), None)
        if not key:
            return
        with self.slots_lock:
            slot = self.slots.get(key)
            if not slot:
                return
            slot.refs -= 1
            slot.last_used = _now()
            if slot.refs <= 0:
                self.slots.pop(key, None)
                try:
                    slot.driver.close()
                except Exception:
                    pass

    def _op_disconnect(self, cid: int, args: dict) -> dict:
        handle = int(args.get("handle") or 0)
        if handle not in self.handles:
            return {"closed": False, "handle": handle, "message": "句柄已释放"}
        self._release_handle(handle)
        with self.sessions_lock:
            sess = self.sessions.get(cid) or {}
            if handle in (sess.get("handles") or []):
                sess["handles"].remove(handle)
        return {"closed": True, "handle": handle}

    def _op_call(self, cid: int, args: dict) -> dict:
        slot = self._slot_of(int(args.get("handle") or 0))
        action = str(args.get("action") or "")
        params = args.get("params") or {}
        with slot.lock:
            slot.calls += 1
            self._bump_session(cid)
            try:
                out = slot.driver.do(action, **params)
                slot.last_used = _now()
                return out if isinstance(out, dict) else {"value": out}
            except Exception:
                slot.errors += 1
                raise

    def _op_batch(self, cid: int, args: dict) -> list:
        out = []
        for item in (args.get("calls") or []):
            try:
                res = self._op_call(cid, item)
                out.append({"ok": True, "result": res})
            except proto.DmError as exc:
                out.append({"ok": False, "error": {"code": exc.code, "message": str(exc)}})
        return out

    @staticmethod
    def _extract_array(result: Any) -> tuple:
        """从驱动返回值里取出可下发的数值数组：(list, 形状)"""
        value = result.get("value") if isinstance(result, dict) else result
        if isinstance(value, list):
            return value, [len(value)]
        if isinstance(value, dict):
            for key in ARRAY_KEYS:
                arr = value.get(key)
                if isinstance(arr, list) and arr and isinstance(arr[0], (int, float)):
                    shape = [len(arr)]
                    if value.get("points"):
                        shape = [int(value["points"])]
                    return arr, shape
        return [], []

    def _op_stream(self, cid: int, args: dict) -> dict:
        slot = self._slot_of(int(args.get("handle") or 0))
        action = str(args.get("action") or "")
        dtype = str(args.get("dtype") or DEFAULT_DTYPE)
        if dtype not in DTYPE_MAP:
            raise proto.DmError(f"不支持的数据类型: {dtype}", proto.ERR_BAD_REQUEST)
        with slot.lock:
            slot.calls += 1
            self._bump_session(cid)
            try:
                out = slot.driver.do(action, **(args.get("params") or {}))
            except Exception:
                slot.errors += 1
                raise
            arr, shape = self._extract_array(out)
            if not arr:
                raise proto.DmError(f"动作 {action} 未返回可下发的数值数组", proto.ERR_STREAM)
            import array as _array
            code = DTYPE_MAP[dtype][0]
            if code in ("f", "d"):
                buf = _array.array(code, (float(v) for v in arr))
            else:
                lo, hi = _INT_RANGES[code]
                buf = _array.array(code, (max(lo, min(hi, int(round(float(v))))) for v in arr))
            desc = self.ring.write(buf.tobytes(), dtype, shape)
            slot.last_used = _now()
            self.streamed += desc["nbytes"]
            meta = out.get("value") if isinstance(out.get("value"), dict) else {}
            desc["meta"] = {k: v for k, v in (meta or {}).items()
                            if isinstance(v, (int, float, str, bool)) or v is None}
            return desc

    def _op_release(self, cid: int, args: dict) -> dict:
        return {"released": self.ring.release(int(args.get("seq") or 0))}

    def _op_devices(self, cid: int, args: dict) -> list:
        with self.slots_lock:
            out = []
            for slot in self.slots.values():
                view = slot.view()
                view["handles"] = [h for h, k in self.handles.items() if k == slot.key]
                out.append(view)
            return out

    def _op_sessions(self, cid: int, args: dict) -> list:
        with self.sessions_lock:
            out = []
            for sess in self.sessions.values():
                alias = []
                for h in sess.get("handles") or []:
                    key = self.handles.get(int(h))
                    if key:
                        alias.append({"handle": int(h), "key": key})
                out.append({"id": sess["id"], "name": sess["name"],
                            "connected_s": round(_now() - sess["connected_at"], 1),
                            "calls": sess["calls"], "handles": alias})
            return out

    def _bump_session(self, cid: int) -> None:
        with self.sessions_lock:
            sess = self.sessions.get(cid)
            if sess:
                sess["calls"] += 1

    def _op_stats(self, cid: int, args: dict) -> dict:
        with self.slots_lock:
            slot_count = len(self.slots)
            refs = sum(s.refs for s in self.slots.values())
            calls = sum(s.calls for s in self.slots.values())
            errors = sum(s.errors for s in self.slots.values())
        with self.sessions_lock:
            sess = len(self.sessions)
        return {
            "bench": self.bench, "pid": os.getpid(), "protocol": proto.PROTOCOL_VERSION,
            "uptime_s": round(_now() - self.started_at, 1),
            "requests": self.requests, "errors": self.errors,
            "sessions": sess, "devices": slot_count, "refs": refs,
            "device_calls": calls, "device_errors": errors,
            "streamed_bytes": self.streamed, "ring": self.ring.info(),
        }

    def _op_log(self, cid: int, args: dict) -> list:
        limit = max(1, min(int(args.get("limit") or 50), LOG_LIMIT))
        return list(self.log_lines)[-limit:]

    def _op_shutdown(self, cid: int, args: dict) -> dict:
        threading.Thread(target=self.stop, name="dm-shutdown", daemon=True).start()
        return {"stopping": True}


# ---------------------------------------------------------------- 入口


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="ATE 装备管理模块（DM）服务进程")
    ap.add_argument("--bench", default="", help="测试台标识，用于区分同名设备的管道")
    ap.add_argument("--endpoint", default="", help="端点文件路径（默认 backend/.ate_rt/dm_endpoint.json）")
    ap.add_argument("--log", default="", help="请求日志文件（JSONL，可选）")
    ap.add_argument("--slots", type=int, default=DEFAULT_SLOTS, help="共享内存槽位数")
    ap.add_argument("--block", type=int, default=DEFAULT_BLOCK, help="单槽字节数")
    ap.add_argument("--idle-exit", type=float, default=0.0, help="空闲多少秒后自动退出（0 表示不退出）")
    ap.add_argument("--tcp", action="store_true", help="强制使用本地 TCP（调试用）")
    ns = ap.parse_args(argv)

    server = DmServer(bench=ns.bench, endpoint_path=ns.endpoint, log_path=ns.log,
                      slots=ns.slots, block=ns.block, idle_exit=ns.idle_exit, force_tcp=ns.tcp,
                      hard_exit=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.stop()
        server._cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
