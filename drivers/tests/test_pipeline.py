"""端到端流程测试（stdlib unittest，无需 pytest）。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for _pkg in ("rohde-schwarz-mxo", "basler-pypylon", "pi-hexapod"):
    _p = str(ROOT / "packages" / _pkg / "pkg")
    if _p not in sys.path:
        sys.path.append(_p)

from kit.conformance import run_camera_suite, run_motion_suite, run_suite  # noqa: E402
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

    def test_tier_declared_and_valid(self) -> None:
        # 示波器与工业相机是柜内固定配置（baseline）；六足平台多台型复用但非标配（shared）
        baseline = sorted(m.name for m in self.reg.by_tier("baseline"))
        self.assertEqual(baseline, ["basler-pypylon", "rohde-schwarz-mxo"])
        self.assertEqual([m.name for m in self.reg.by_tier("shared")], ["pi-hexapod"])
        self.assertEqual(self.reg.by_tier("station"), [])

    def test_channel_and_distribution(self) -> None:
        for m in self.reg.all():
            self.assertIn(m.channel, ("stable", "canary"))
            self.assertEqual(m.distribution, "platform-repo")

    def test_three_capabilities_registered(self) -> None:
        caps = sorted(m.capability for m in self.reg.all())
        self.assertEqual(caps, ["camera", "hexapod", "oscilloscope"])
        for m in self.reg.all():
            self.assertTrue(m.vendor_deps, f"{m.name} 未声明 vendor_deps")

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

    def test_simulation_is_capability_layer(self) -> None:
        # 仿真不建会话、不接管链路：_io 为空，仿真层为 capability
        cls = self.rs.load("oscilloscope")
        drv = cls("SIM::T", mode="simulate")
        drv.connect()
        try:
            self.assertIsNone(drv._io)
            snap = drv.describe()
            self.assertEqual(snap["simulation"]["layer"], "capability")
            self.assertTrue(snap["simulation"]["active"])
        finally:
            drv.close()

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


class RepoTest(unittest.TestCase):
    """制品仓：发布 → 拉取 → 校验 → 按 lock 出计划。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ate-repo-"))
        self.repo_root = self.tmp / "repo"
        self.repo_root.mkdir(parents=True)
        self.pkg = ROOT / "packages" / "rohde-schwarz-mxo"
        self.zip_path = self._make_zip("rohde-schwarz-mxo", "1.3.0")

    def _make_zip(self, name: str, version: str) -> Path:
        zip_path = self.repo_root / f"{name}-{version}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(self.pkg.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    z.write(f, (Path(name) / f.relative_to(self.pkg)).as_posix())
        return zip_path

    def test_publish_fetch_roundtrip(self) -> None:
        from kit.repo import Repo

        repo = Repo(self.repo_root)
        entry = repo.publish(self.zip_path, channel="stable")
        self.assertEqual(entry.tier, "baseline")
        self.assertEqual(len(entry.tree_sha256), 64)

        target = repo.fetch("rohde-schwarz-mxo", "1.3.0", dest=self.tmp / "site")
        self.assertTrue((target / "manifest.json").is_file())
        self.assertTrue((target / "pkg" / "rs_mxo" / "driver.py").is_file())

    def test_fetch_rejects_tampered_zip(self) -> None:
        from kit.repo import Repo, RepoError

        repo = Repo(self.repo_root)
        repo.publish(self.zip_path)
        self.zip_path.write_bytes(self.zip_path.read_bytes() + b"tampered")
        with self.assertRaises(RepoError):
            repo.fetch("rohde-schwarz-mxo", "1.3.0", dest=self.tmp / "site2")

    def test_plan_states(self) -> None:
        from kit.repo import Repo

        repo = Repo(self.repo_root)
        repo.publish(self.zip_path)
        lock = {
            "station_type": "scope-bench",
            "resolved": [{"capability": "oscilloscope", "package": "rohde-schwarz-mxo", "version": "1.3.0"}],
        }
        lock_path = self.tmp / "bench.lock.json"
        lock_path.write_text(json.dumps(lock), encoding="utf-8")

        site = self.tmp / "site"
        plan = repo.plan(lock_path, site)
        self.assertEqual(plan[0]["action"], "install")
        repo.apply(plan, dest=site)
        self.assertEqual(repo.plan(lock_path, site)[0]["action"], "ok")

        lock["resolved"][0]["version"] = "9.9.9"
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
        self.assertFalse(repo.plan(lock_path, site)[0]["in_repo"])


class CameraPackageTest(unittest.TestCase):
    """相机包：能力层仿真 + 契约套件 + 实模式缺库时的错误语义。"""

    def setUp(self) -> None:
        from basler_pypylon import BaslerCamera  # noqa: WPS433

        self.cls = BaslerCamera

    def test_conformance(self) -> None:
        checks = run_camera_suite(lambda: self.cls("serial:SIM1", mode="simulate"), expect_mode="simulate")
        failed = [c for c in checks if not c.ok]
        self.assertFalse(failed, "; ".join(f"{c.name}: {c.detail}" for c in failed))

    def test_simulation_is_capability_layer(self) -> None:
        drv = self.cls("serial:SIM1", mode="simulate")
        drv.connect()
        try:
            self.assertIsNone(drv._cam)
            snap = drv.describe()
            self.assertEqual(snap["simulation"]["layer"], "capability")
            self.assertTrue(snap["simulation"]["active"])
        finally:
            drv.close()

    def test_hardware_without_sdk_raises(self) -> None:
        from contracts.camera import CameraError

        drv = self.cls("serial:0000", mode="hardware")
        with self.assertRaises(CameraError):
            drv.connect()

    def test_statistics_shared_definition(self) -> None:
        from contracts.camera import ExposureSpec, compute_statistics

        drv = self.cls("serial:SIM1", mode="simulate")
        drv.connect()
        try:
            drv.configure_exposure(ExposureSpec(exposure_us=8000))
            frame = drv.grab(1)[0]
            expect = compute_statistics(frame.samples(), width=frame.width)
            for item, value in expect.items():
                self.assertAlmostEqual(drv.statistics(frame, item), value, places=6)
            # 曝光翻倍，亮度必须上升
            drv.configure_exposure(ExposureSpec(exposure_us=16000))
            brighter = drv.statistics(drv.grab(1)[0], "mean_gray")
            self.assertGreater(brighter, expect["mean_gray"])
        finally:
            drv.close()


class HexapodPackageTest(unittest.TestCase):
    """六足包：能力层仿真 + 契约套件 + 未回零拒绝运动。"""

    def setUp(self) -> None:
        from pi_hexapod import PiHexapod  # noqa: WPS433

        self.cls = PiHexapod

    def test_conformance(self) -> None:
        checks = run_motion_suite(lambda: self.cls("tcpip://127.0.0.1:50000", mode="simulate"))
        failed = [c for c in checks if not c.ok]
        self.assertFalse(failed, "; ".join(f"{c.name}: {c.detail}" for c in failed))

    def test_reference_required(self) -> None:
        from contracts.motion import MotionUnsupported

        drv = self.cls("tcpip://127.0.0.1:50000", mode="simulate")
        drv.connect()
        try:
            with self.assertRaises(MotionUnsupported):
                drv.move_to({"X": 100.0})
            drv.reference()
            pos = drv.move_to({"X": 1500.0, "Z": -800.0})
            self.assertAlmostEqual(pos.get("X"), 1500.0, places=3)
            self.assertAlmostEqual(pos.get("Z"), -800.0, places=3)
        finally:
            drv.close()

    def test_limit_and_axis_errors(self) -> None:
        from contracts.motion import MotionLimitError, MotionUnsupported

        drv = self.cls("tcpip://127.0.0.1:50000", mode="simulate")
        drv.connect()
        try:
            drv.reference()
            with self.assertRaises(MotionLimitError):
                drv.move_to({"X": 1e6})
            with self.assertRaises(MotionUnsupported):
                drv.move_to({"Q": 1.0})
        finally:
            drv.close()

    def test_hardware_without_sdk_raises(self) -> None:
        from contracts.motion import MotionError

        drv = self.cls("tcpip://127.0.0.1:50000", mode="hardware")
        with self.assertRaises(MotionError):
            drv.connect()


class MultiCapabilityStationTest(unittest.TestCase):
    """一台柜内三台设备：解析 → 会话 → 各自仿真跑通。"""

    def setUp(self) -> None:
        self.reg = Registry(ROOT)
        self.reg.load()
        self.station = load_station_type(ROOT / "station-types" / "standard-bench.json")

    def test_resolve_three_packages(self) -> None:
        rs = resolve(self.station, self.reg, contract_version="1.0")
        labels = {r.capability: r.label for r in rs}
        self.assertEqual(sorted(labels), ["camera", "hexapod", "oscilloscope"])
        self.assertEqual(labels["camera"], "basler-pypylon@1.0.0")
        self.assertEqual(labels["hexapod"], "pi-hexapod@1.0.0")

    def test_session_runs_all_capabilities(self) -> None:
        from contracts.camera import ExposureSpec, RoiSpec

        rs = resolve(self.station, self.reg, contract_version="1.0")
        devices = {
            "oscilloscope": DeviceRecord("oscilloscope", "SIM::MXO", mode="simulate"),
            "camera": DeviceRecord("camera", "serial:SIM1", mode="simulate"),
            "hexapod": DeviceRecord("hexapod", "tcpip://127.0.0.1:50000", mode="simulate"),
        }
        with DriverSession(rs, devices, bench="BENCH-CAB") as s:
            with s.operation("oscilloscope") as scope:
                scope.configure_timebase(time_span_s=1e-3)
                scope.configure_channel(1, scale_v=0.5)
                self.assertEqual(scope.acquire([1], points=500)[1].n, 500)
            with s.operation("camera") as cam:
                cam.configure_exposure(ExposureSpec(exposure_us=9000))
                cam.configure_roi(RoiSpec(width=160, height=120))
                frame = cam.grab(1)[0]
                self.assertGreater(cam.statistics(frame, "mean_gray"), 0)
            with s.operation("hexapod") as stage:
                stage.reference()
                self.assertAlmostEqual(stage.move_to({"X": 200.0}).get("X"), 200.0, places=3)
            snap = s.describe()
            self.assertEqual(len(snap["devices"]), 3)
            self.assertTrue(all(d["connected"] for d in snap["devices"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
