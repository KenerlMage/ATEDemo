# -*- coding: utf-8 -*-
"""ATE Runner 公共 conftest（由后端自动部署到 workspace，请勿手工改名）

职责
----
1. **定位运行环境**：读取同目录下的 `ate_env.json`（由 ATE Runner 在 prepare 阶段写入），
   从中拿到 ATE 后端目录、SQLite 库路径、测试台编号、运行模式等。
2. **导入装备信息（driver 配置）**：从 SQLite `device_registry` / `bench_registry`
   读取该测试台登记的设备连接参数（IP/端口/串口/波特率/资源地址）与 driver 规格名。
3. **导入测试阈值**：读取 TPS 清单 `testconfig` 子字典，提供上下限判定。
4. **导入设备 driver**：`from drivers import get_driver`，按别名实例化（仿真 / 真机同一驱动）。
5. **提供夹具**：`ate_ctx` / `ate_runner` / `ate_bench` / `ate_devices` / `ate_thresholds` / `driver`。
6. **收集结果**：把每个用例的测量值、阈值判定、耗时写到 `run_result.json`，供 ATE Runner
   汇总成测试报告；会话结束统一释放仪器会话（含 OUTP OFF 等安全收尾）。

版本标记：ATECONFTEST_VERSION = 2（后端用它判断是否需要覆盖更新）
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

import pytest

ATECONFTEST_VERSION = 2

ROOT = Path(__file__).resolve().parent


# ------------------------------------------------------------------ 运行环境

def _default_backend() -> str:
    """兜底推断 ATE 后端目录：workspace 的同级 backend"""
    for cand in (ROOT.parent / "backend", ROOT.parent.parent / "backend"):
        if (cand / "drivers").is_dir():
            return str(cand)
    return ""


def _load_env() -> dict:
    env_file = os.environ.get("ATE_ENV_FILE") or str(ROOT / "ate_env.json")
    env: dict = {}
    try:
        env = json.loads(Path(env_file).read_text(encoding="utf-8"))
    except Exception as e:
        pytest.exit(f"[ATE] 无法读取运行环境文件 {env_file}: {e}", returncode=3)
    backend = env.get("ate_backend") or os.environ.get("ATE_BACKEND") or _default_backend()
    if backend:
        os.environ["ATE_BACKEND"] = backend
        if backend not in sys.path:
            sys.path.insert(0, backend)
    # 运行环境文件是权威来源：让 ate_db 去读这一轮用的 SQLite 库
    if env.get("db_path"):
        os.environ["ATE_DB_PATH"] = str(env["db_path"])
    env["ate_backend"] = backend
    env["env_file"] = env_file
    env["run_dir"] = str(ROOT)
    return env


ENV = _load_env()

import ate_db  # noqa: E402  注册库 -> SQLite 投影（含设备 driver 配置）
import drivers  # noqa: E402  仪器驱动层（仿真 / 真机同一驱动）

RUN_DIR = ROOT
RESULT_FILE = RUN_DIR / "run_result.json"
RUN_LOG = RUN_DIR / "run_log.txt"


# ------------------------------------------------------------------ 工具

def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _read_manifest() -> dict:
    for name in ("tps.json",):
        path = ROOT / name
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                pytest.exit(f"[ATE] TPS 清单 {name} 解析失败: {e}", returncode=3)
    pytest.exit(f"[ATE] 运行目录缺少 TPS 清单 tps.json: {ROOT}", returncode=3)


class LimitError(AssertionError):
    """阈值判定不通过（继承 AssertionError，pytest 直接判 FAIL）

    带 `checks` 属性：把已算出的阈值明细一起带到上层，便于结果落盘（含实测/允许区间）
    """

    def __init__(self, message: str, checks=None):
        super().__init__(message)
        self.checks = checks or []


class AteContext:
    """一次 TPS 运行的全部上下文：装备 / 驱动 / 阈值 / 记录"""

    def __init__(self, env: dict, manifest: dict):
        self.env = env
        self.manifest = manifest
        self.task_id = env.get("task_id", "")
        self.uut = env.get("uut", "")
        self.mode = (env.get("mode") or "simulate").lower()
        self.run_dir = Path(env.get("run_dir") or ROOT)
        self.device_config: dict = manifest.get("device_config") or {}
        self.testconfig: dict = manifest.get("testconfig") or {}
        self.cmd_suit: list = manifest.get("cmd_suit") or []
        self.bench: dict = {}
        self.devices: dict = {}          # alias -> SQLite 设备行
        self._drivers: dict = {}         # alias -> Driver 实例（会话内复用）
        self._driver_errors: dict = {}
        self.results: list = []
        self.started_at = _now()
        self.log_lines: list = []
        self._resume_results()
        self._load_bench()
        self._load_devices()
        self._write_result(force=True)

    # ---------------- 准备：从 SQLite 导入装备 / driver 配置 ----------------

    def _load_bench(self) -> None:
        bench_id = self.env.get("bench_id", "")
        try:
            self.bench = ate_db.find_bench(
                bench_id=bench_id,
                preset_id=self.env.get("bench_preset_id", ""),
                serial=self.env.get("bench_serial", ""),
            ) or {}
        except Exception as e:
            self.bench = {}
            self.log(f"[warn] 读取测试台注册信息失败: {e}")
        if not self.bench:
            self.log("[warn] SQLite 中没有匹配的测试台记录，设备连接参数可能不完整")

    def _load_devices(self) -> None:
        bench_id = self.bench.get("id", "") or self.env.get("bench_id", "")
        rows = []
        if bench_id:
            try:
                rows = ate_db.get_devices(bench_id)
            except Exception as e:
                self.log(f"[warn] 读取设备注册信息失败: {e}")
        by_id = {r.get("device_id"): r for r in rows}
        for alias, spec in (self.device_config or {}).items():
            spec = spec if isinstance(spec, dict) else {"device_id": str(spec)}
            row = None
            dev_id = str(spec.get("device_id") or "").strip()
            if dev_id and dev_id in by_id:
                row = by_id[dev_id]
            elif bench_id:
                row = ate_db.match_device(bench_id, spec)
            if row:
                self.devices[alias] = row
            else:
                self._driver_errors[alias] = f"测试台 {bench_id or '(未指定)'} 中找不到设备: {spec}"

    # ---------------- 日志 / 结果 ----------------

    def _resume_results(self) -> None:
        """续写上一轮的 run_result.json

        每条用例是一次独立的 pytest 进程，若不续写，后跑的用例会把先跑的覆盖掉。
        同 id 的用例以最新一次为准（便于重跑单条用例后刷新结果）。
        """
        try:
            data = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        if str(data.get("task_id") or "") != str(self.task_id):
            return
        for case in data.get("cases") or []:
            if isinstance(case, dict) and case.get("id"):
                self.results.append(case)

    def _upsert(self, case: dict) -> None:
        case_id = case.get("id", "")
        for idx, old in enumerate(self.results):
            if old.get("id") == case_id:
                self.results[idx] = case
                return
        self.results.append(case)

    def log(self, message: str) -> None:
        line = f"[{_now()}] {message}"
        self.log_lines.append(line)
        print(f"[ATE] {message}")
        try:
            with open(RUN_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def _write_result(self, force: bool = False) -> None:
        passed = sum(1 for r in self.results if r.get("status") == "passed")
        failed = sum(1 for r in self.results if r.get("status") == "failed")
        skipped = sum(1 for r in self.results if r.get("status") == "skipped")
        payload = {
            "task_id": self.task_id,
            "tps_id": self.manifest.get("id", ""),
            "tps_name": self.manifest.get("name", ""),
            "uut": self.uut,
            "mode": self.mode,
            "bench": {
                "id": self.bench.get("id", ""),
                "title": self.bench.get("title", ""),
                "serial": self.bench.get("serial", ""),
                "preset_id": self.bench.get("preset_id", ""),
            },
            "run_dir": str(self.run_dir),
            "started_at": self.started_at,
            "updated_at": _now(),
            "workspace": self.env.get("workspace", ""),
            "conftest_version": ATECONFTEST_VERSION,
            "summary": {
                "total": len(self.results),
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "expected": len(self.cmd_suit),
            },
            "cases": self.results,
            "devices": {a: {"device_id": r.get("device_id", ""), "model": r.get("model", ""),
                            "driver": r.get("driver", ""), "resource": _resource_of(r)}
                        for a, r in self.devices.items()},
            "driver_errors": self._driver_errors,
        }
        try:
            RESULT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def record(self, case: dict, status: str, *, measurements=None, checks=None,
               message: str = "", duration: float = 0.0) -> None:
        self._upsert({
            "id": case.get("id", ""),
            "no": case.get("no"),
            "name": case.get("name", ""),
            "case": case.get("case", ""),
            "kind": case.get("kind", "test"),
            "status": status,
            "message": message,
            "duration": round(duration, 3),
            "measurements": measurements or {},
            "checks": checks or [],
            "at": _now(),
        })
        self._write_result(force=True)

    # ---------------- 阈值（testconfig） ----------------

    def limit(self, key: str) -> dict:
        cfg = dict(self.testconfig.get(key) or {})
        if not cfg:
            raise KeyError(f"testconfig 中没有阈值定义: {key}")
        cfg["key"] = key
        cfg.setdefault("label", key)
        cfg.setdefault("unit", "")
        return cfg

    def check(self, key: str, value, *, label: str = "") -> tuple:
        """按 testconfig 的上下限判定，返回 (是否通过, 明细字典)"""
        cfg = self.limit(key)
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False, {"key": key, "value": value, "ok": False,
                           "message": f"{cfg['label']} 测量值不是数值: {value!r}"}
        lo, hi = cfg.get("min"), cfg.get("max")
        ok = True
        reasons = []
        if lo is not None and v < float(lo):
            ok, reasons = False, reasons + [f"低于下限 {lo}{cfg['unit']}"]
        if hi is not None and v > float(hi):
            ok, reasons = False, reasons + [f"高于上限 {hi}{cfg['unit']}"]
        detail = {
            "key": key,
            "label": label or cfg["label"],
            "value": round(v, 6),
            "min": lo,
            "max": hi,
            "unit": cfg["unit"],
            "ok": ok,
            "message": "" if ok else "；".join(reasons),
        }
        if ok:
            self.log(f"{detail['label']} = {detail['value']}{cfg['unit']} 在 [{lo}, {hi}] 内 ✓")
        else:
            self.log(f"{detail['label']} = {detail['value']}{cfg['unit']} 超限 " + detail["message"])
        return ok, detail

    def expect(self, key: str, value, label: str = "") -> float:
        """判定不通过直接抛 AssertionError（供用例内部使用）"""
        ok, detail = self.check(key, value, label=label)
        if not ok:
            raise LimitError(
                f"{detail['label']} 超限: 实测 {detail['value']}{detail['unit']} "
                f"(允许 {detail['min']} ~ {detail['max']}{detail['unit']}, {detail['message']})",
                checks=[detail],
            )
        return detail["value"]

    # ---------------- 设备驱动 ----------------

    def driver(self, alias: str):
        """按别名拿到已连接的驱动实例（同一别名在会话内只连一次）"""
        if alias in self._drivers:
            return self._drivers[alias]
        row = self.devices.get(alias)
        if not row:
            why = self._driver_errors.get(alias) or f"未在 device_config 中声明别名: {alias}"
            raise drivers.DriverError(f"无法为 {alias} 建立驱动: {why}", device_id=alias)
        spec = self.device_config.get(alias) or {}
        mode = str(spec.get("mode") or self.mode).lower()
        driver = drivers.get_driver(row, mode=mode, alias=alias)
        info = driver.open()
        self.log(f"驱动就绪 {alias}: {info['driver']} @ {info['resource']} "
                 f"[{'仿真' if info['simulated'] else '真机'}] idn={info['idn']}")
        self._drivers[alias] = driver
        return driver

    def device(self, alias: str) -> dict:
        """拿设备注册信息（IP/端口/型号/driver 等）"""
        return dict(self.devices.get(alias) or {})

    def command(self, alias: str, action: str, **params):
        """便捷动作：ctx.command('dmm', 'measure_dc_voltage') -> 值"""
        driver = self.driver(alias)
        try:
            out = driver.do(action, **params)
        except drivers.DriverError as e:
            self._handle_driver_error(alias, e)
            raise
        value = out.get("value")
        if isinstance(value, dict):
            self.log(f"{alias}.{action} -> {json.dumps(value, ensure_ascii=False)[:160]}")
        else:
            self.log(f"{alias}.{action} -> {value}")
        return value

    def _handle_driver_error(self, alias: str, exc: Exception) -> None:
        if str(self.env.get("on_driver_error", "")).lower() == "skip":
            pytest.skip(f"{alias} 驱动不可用（现场跳过）: {exc}")

    def close(self) -> None:
        for alias, driver in list(self._drivers.items()):
            try:
                driver.close()
                self.log(f"驱动已释放: {alias}")
            except Exception as e:  # 收尾失败不影响结论
                self.log(f"[warn] 驱动释放失败 {alias}: {e}")
        self._drivers.clear()
        drivers.release_all()


def _resource_of(row: dict) -> str:
    if row.get("host") and row.get("port"):
        return f"TCPIP0::{row['host']}::{row['port']}::SOCKET"
    if row.get("serial_port"):
        return f"ASRL::{row['serial_port']}::{row.get('baudrate') or 9600}::INSTR"
    return row.get("address") or ""


# ------------------------------------------------------------------ 夹具

_STATE: dict = {}


@pytest.fixture(scope="session")
def ate_ctx(request) -> AteContext:
    """本次 TPS 运行的上下文（装备 / 驱动 / 阈值 / 记录）"""
    if "ctx" not in _STATE:
        manifest = _read_manifest()
        ctx = AteContext(ENV, manifest)
        bench = ctx.bench
        print("=" * 78)
        print(f"[ATE] 运行环境: {ROOT}")
        print(f"[ATE] TPS: {manifest.get('name', '')} ({manifest.get('id', '')})  UUT: {ctx.uut}  任务: {ctx.task_id}")
        print(f"[ATE] 测试台: {bench.get('title', '(未匹配)')} / {bench.get('serial', '-')}  "
              f"设备: {len(ctx.devices)} 台  阈值: {len(ctx.testconfig)} 项  模式: {ctx.mode}")
        for alias, row in ctx.devices.items():
            print(f"[ATE]   {alias:<8} <- {row.get('device_id', ''):<14} {row.get('category', '')}"
                  f" {row.get('model', '')}  driver={row.get('driver', '')}")
        if ctx._driver_errors:
            for alias, why in ctx._driver_errors.items():
                print(f"[ATE]   [warn] {alias}: {why}")
        print("=" * 78)
        _STATE["ctx"] = ctx
    yield _STATE["ctx"]


@pytest.fixture(scope="session")
def ate_bench(ate_ctx: AteContext) -> dict:
    """测试台注册信息（来自 SQLite）"""
    return ate_ctx.bench


@pytest.fixture(scope="session")
def ate_devices(ate_ctx: AteContext) -> dict:
    """设备注册信息（来自 SQLite，含 IP/端口/串口/driver 规格）"""
    return ate_ctx.devices


@pytest.fixture(scope="session")
def ate_thresholds(ate_ctx: AteContext) -> dict:
    """测试阈值（TPS 清单 testconfig 子字典）"""
    return ate_ctx.testconfig


@pytest.fixture(scope="session")
def driver(ate_ctx: AteContext):
    """驱动工厂：driver('dmm') -> 已连接的驱动实例"""
    return ate_ctx.driver


@pytest.fixture(scope="session")
def ate_runner(ate_ctx: AteContext):
    """用例执行器：把 cmd_suit 的一个条目跑起来（自动做阈值判定与记录）"""
    return lambda case: run_case(ate_ctx, case)


# ------------------------------------------------------------------ 用例执行

def _load_callable(ref: str):
    """`testcase.power::power_on_voltage` / `testcase.power::PowerTests.check` -> callable"""
    if "::" not in ref:
        raise ImportError(f"用例实现引用格式应为 module::function，实际: {ref}")
    module_name, qual = ref.split("::", 1)
    if str(RUN_DIR) not in sys.path:
        sys.path.insert(0, str(RUN_DIR))
    module = importlib.import_module(module_name)
    target = module
    for part in qual.split("."):
        target = getattr(target, part)
    return target


def run_case(ctx: AteContext, case: dict) -> None:
    """执行一个 cmd_suit 条目

    * 用例实现签名：`def case(ctx, **params) -> None | float | dict`
    * 返回值若为数值/字典，则按条目 `checks`（或 `config`）自动做阈值判定
    """
    name = case.get("name") or case.get("id") or case.get("case", "?")
    kind = case.get("kind", "test")
    t0 = time.time()
    ctx.log(f"▶ [{kind}] {case.get('id', '')} {name} -> {case.get('case', '')}")
    try:
        fn = _load_callable(str(case.get("case", "")))
        returned = fn(ctx, **(case.get("params") or {}))
        measurements, checks = _apply_checks(ctx, case, returned)
        duration = time.time() - t0
        ctx.record(case, "passed", measurements=measurements, checks=checks, duration=duration)
        ctx.log(f"✔ [{case.get('id', '')}] {name} 通过 ({duration:.2f}s)")
    except (LimitError, AssertionError) as e:
        duration = time.time() - t0
        ctx.record(case, "failed", message=str(e), checks=getattr(e, "checks", None), duration=duration)
        ctx.log(f"✘ [{case.get('id', '')}] {name} 失败: {e}")
        raise
    except Exception as e:  # 驱动异常 / 用例内部异常
        if isinstance(e, drivers.DriverError):
            ctx._handle_driver_error(case.get("id", ""), e)
        duration = time.time() - t0
        detail = f"{type(e).__name__}: {e}"
        ctx.record(case, "failed", message=detail, duration=duration)
        ctx.log(f"✘ [{case.get('id', '')}] {name} 异常: {detail}")
        ctx.log(traceback.format_exc(limit=3))
        raise


def _apply_checks(ctx: AteContext, case: dict, returned) -> tuple:
    """把用例返回值映射到 testconfig 阈值上做判定"""
    measurements: dict = {}
    checks: list = []
    if isinstance(returned, dict) and "measurements" in returned:
        measurements.update(returned.get("measurements") or {})
        returned = returned.get("measurements") or {}
    if isinstance(returned, dict):
        measurements.update({k: v for k, v in returned.items() if not isinstance(v, (dict, list))})
    elif isinstance(returned, (int, float)):
        measurements["value"] = float(returned)

    mapping = case.get("checks")
    if not mapping and case.get("config"):
        mapping = {"value": case["config"]}
    if isinstance(mapping, list):
        mapping = {m: m for m in mapping}
    if mapping:
        if not measurements:
            raise AssertionError(f"用例 {case.get('id', '')} 配置了阈值判定但没有返回测量值")
        for field, key in dict(mapping).items():
            if field not in measurements:
                raise AssertionError(
                    f"用例 {case.get('id', '')} 返回值缺少字段 {field}（现有: {', '.join(measurements) or '无'}）"
                )
            ok, detail = ctx.check(str(key), measurements[field])
            checks.append(detail)
            if not ok:
                raise LimitError(
                    f"{detail['label']} 超限: 实测 {detail['value']}{detail['unit']} "
                    f"(允许 {detail['min']} ~ {detail['max']}{detail['unit']}, {detail['message']})",
                    checks=list(checks),
                )
    return measurements, checks


# ------------------------------------------------------------------ pytest 钩子

def pytest_sessionfinish(session, exitstatus) -> None:
    ctx = _STATE.get("ctx")
    if not ctx:
        return
    ctx._write_result(force=True)
    summary = {"total": len(ctx.results),
               "passed": sum(1 for r in ctx.results if r["status"] == "passed"),
               "failed": sum(1 for r in ctx.results if r["status"] == "failed"),
               "skipped": sum(1 for r in ctx.results if r["status"] == "skipped")}
    print("-" * 78)
    print(f"[ATE] 运行结果: {summary}  结果文件: {RESULT_FILE}")
    try:
        ctx.close()
    except Exception:
        pass


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """兜底记录：用例在 run_case 之外失败（例如夹具异常）也能留痕"""
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed:
        return
    ctx = _STATE.get("ctx")
    if not ctx:
        return
    longrepr = str(getattr(report, "longrepr", ""))[:400]
    if any(r.get("status") == "failed" and r.get("at") for r in ctx.results[-1:]):
        return
    ctx.record({"id": item.name, "name": item.name, "case": str(item.nodeid), "kind": "test"},
               "failed", message=longrepr)
