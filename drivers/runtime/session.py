"""运行时层（L0）：会话、资源仲裁、超时与诊断。

存在的理由（标准柜场景特有）
--------------------------
一个标准柜可以挂多个测试站，共用同一台仪器。量程、触发、采集状态都是仪器全局状态，
两个站同时下发命令会互相打断，表现为"偶发读数异常"。因此连接与仲裁必须是平台层，
不能下放给每个驱动包自己管。

本模块不解析包、不读文件，只消费 ResolutionSet 与设备档案投影。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Mapping

from kit.resolve import ResolutionSet


class SessionError(Exception):
    pass


@dataclass(frozen=True)
class DeviceRecord:
    """设备档案中与驱动相关的投影（由平台从 device_registry 取出后传入）。"""

    capability: str
    resource: str
    mode: str = "hardware"
    timeout_ms: int = 15000
    extra: Mapping[str, object] = field(default_factory=dict)


class DriverSession:
    def __init__(
        self,
        resolutions: ResolutionSet,
        devices: Mapping[str, DeviceRecord],
        *,
        bench: str = "",
        timeout_s: float = 30.0,
        retries: int = 1,
    ) -> None:
        self.resolutions = resolutions
        self.devices = dict(devices)
        self.bench = bench
        self.timeout_s = timeout_s
        self.retries = max(0, retries)
        self._drivers: dict[str, object] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()
        self.events: list[dict] = []

    # -- 记录 -----------------------------------------------------------
    def _log(self, kind: str, **fields) -> None:
        self.events.append({"t": time.time(), "kind": kind, **fields})

    # -- 会话 -----------------------------------------------------------
    def driver(self, capability: str):
        with self._meta_lock:
            if capability in self._drivers:
                return self._drivers[capability]
            rec = self.devices.get(capability)
            if rec is None:
                raise SessionError(f"设备档案中没有 {capability} 的记录")
            cls = self.resolutions.load(capability)
            driver = cls(
                rec.resource,
                mode=rec.mode,
                timeout_ms=rec.timeout_ms,
                **dict(rec.extra),
            )
            attempts = self.retries + 1
            last: Exception | None = None
            for i in range(attempts):
                try:
                    driver.connect()
                    self._log("connect", capability=capability, resource=rec.resource, mode=rec.mode, attempt=i + 1)
                    break
                except Exception as exc:  # 连接失败按次数重试后上抛
                    last = exc
                    self._log("connect_failed", capability=capability, attempt=i + 1, error=str(exc))
                    time.sleep(min(0.2 * (i + 1), 1.0))
            else:
                raise SessionError(f"{capability} 连接失败（{attempts} 次）: {last}") from last
            self._drivers[capability] = driver
            return driver

    def _lock_for(self, capability: str) -> threading.Lock:
        resource = self.devices[capability].resource
        with self._meta_lock:
            return self._locks.setdefault(resource, threading.Lock())

    @contextmanager
    def operation(self, capability: str) -> Iterator[object]:
        """独占该能力背后的仪器资源，超时保护 + 事件记录。"""
        drv = self.driver(capability)
        lock = self._lock_for(capability)
        acquired = lock.acquire(timeout=self.timeout_s)
        if not acquired:
            self._log("lock_timeout", capability=capability)
            raise SessionError(f"{capability} 资源被占用超过 {self.timeout_s}s（柜内多站争用）")
        started = time.time()
        try:
            yield drv
        finally:
            lock.release()
            self._log(
                "operation",
                capability=capability,
                resource=self.devices[capability].resource,
                ms=int((time.time() - started) * 1000),
            )

    # -- 诊断 -----------------------------------------------------------
    def describe(self) -> dict:
        topology = []
        for cap, drv in self._drivers.items():
            snap = {}
            try:
                snap = drv.describe()
            except Exception as exc:
                snap = {"error": str(exc)}
            topology.append(
                {
                    "capability": cap,
                    "package": snap.get("package"),
                    "version": snap.get("package_version"),
                    "resource": self.devices[cap].resource,
                    "mode": self.devices[cap].mode,
                    "connected": snap.get("connected"),
                }
            )
        return {
            "bench": self.bench,
            "station_type": self.resolutions.station.name,
            "devices": topology,
            "events": len(self.events),
        }

    def close(self) -> None:
        for cap, drv in list(self._drivers.items()):
            try:
                drv.close()
            finally:
                self._log("close", capability=cap)
        self._drivers.clear()

    def __enter__(self) -> "DriverSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
