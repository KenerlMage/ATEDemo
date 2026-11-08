"""端到端流程测试（stdlib unittest，无需 pytest）。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.conformance import run_suite  # noqa: E402
from kit.registry import Registry  # noqa: E402
from kit.resolve import ResolveError, load_station_type, resolve  # noqa: E402
from runtime.session import DeviceRecord, DriverSession  # noqa: E402


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = Registry(ROOT)
        self.reg.load()

    def test_manifest_fields(self) -> None:
        m = [x for x in self.reg.all() if x.name == "rohde-schwarz-mxo"][0]
        self.assertEqual(m.capability, "oscilloscope")
        self.assertEqual(m.contract, "1.0")
        self.assertTrue(m.simulate)
        self.assertEqual(len(m.tree_sha256), 64)

    def test_find_by_spec(self) -> None:
        self.assertTrue(self.reg.find("oscilloscope", "rohde-schwarz-mxo", "^1.2"))
        self.assertFalse(self.reg.find("oscilloscope", "rohde-schwarz-mxo", "^2.0"))


class ResolveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = Registry(ROOT)
        self.reg.load()
        self.station = load_station_type(ROOT / "station-types" / "scope-bench.json")

    def test_optional_skipped(self) -> None:
        rs = resolve(self.station, self.reg, contract_version="1.0")
        caps = [r.capability for r in rs]
        self.assertIn("oscilloscope", caps)
        self.assertNotIn("optical-power-meter", caps)

    def test_lock_roundtrip(self) -> None:
        rs = resolve(self.station, self.reg, contract_version="1.0")
        lock = rs.to_lock(bench="BENCH-TEST", runner="1.7.3")
        locked = resolve(self.station, self.reg, lock, contract_version="1.0")
        self.assertEqual(locked.get("oscilloscope").label, rs.get("oscilloscope").label)

    def test_lock_integrity(self) -> None:
        rs = resolve(self.station, self.reg, contract_version="1.0")
        lock = rs.to_lock(bench="BENCH-TEST", runner="1.7.3")
        lock["resolved"][0]["tree_sha256"] = "0" * 64
        with self.assertRaises(ResolveError):
            resolve(self.station, self.reg, lock, contract_version="1.0")

    def test_wrong_station_in_lock(self) -> None:
        rs = resolve(self.station, self.reg, contract_version="1.0")
        lock = rs.to_lock(bench="BENCH-TEST", runner="1.7.3")
        lock["station_type"] = "other-bench"
        with self.assertRaises(ResolveError):
            resolve(self.station, self.reg, lock, contract_version="1.0")


class SessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = Registry(ROOT)
        self.reg.load()
        self.station = load_station_type(ROOT / "station-types" / "scope-bench.json")
        self.rs = resolve(self.station, self.reg, contract_version="1.0")

    def test_acquire_and_measure(self) -> None:
        devices = {"oscilloscope": DeviceRecord("oscilloscope", "SIM::T", mode="simulate")}
        with DriverSession(self.rs, devices, bench="BENCH-TEST") as s:
            with s.operation("oscilloscope") as scope:
                scope.configure_timebase(time_span_s=1e-3)
                scope.configure_channel(1, scale_v=0.5)
                wf = scope.acquire([1], points=1000)[1]
                self.assertEqual(wf.n, 1000)
                self.assertAlmostEqual(wf.vpp, 0.8, delta=0.08)
                self.assertGreater(scope.measure(1, "frequency"), 1500)
            snap = s.describe()
            self.assertEqual(snap["devices"][0]["mode"], "simulate")

    def test_conformance_suite(self) -> None:
        cls = self.rs.load("oscilloscope")
        checks = run_suite(lambda: cls("SIM::T", mode="simulate"))
        failed = [c for c in checks if not c.ok]
        self.assertFalse(failed, "; ".join(f"{c.name}: {c.detail}" for c in failed))

    def test_unknown_capability(self) -> None:
        devices = {"oscilloscope": DeviceRecord("oscilloscope", "SIM::T", mode="simulate")}
        with DriverSession(self.rs, devices) as s:
            with self.assertRaises(Exception):
                s.driver("optical-power-meter")


if __name__ == "__main__":
    unittest.main(verbosity=2)
