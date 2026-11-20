"""装备软件部署：从 ATE Controller 拉取软件包、校验、解压并执行包内部署脚本。

严格约定：
- 只部署 TPS 中显式声明的内容，不做型号或版本推测；
- 脚本路径相对「部署包根目录」，不相对 TPS 目录，也不允许绝对路径；
- 脚本按原样执行，不注入参数、不改写脚本内容。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ATE_DB_PATH", str(BASE_DIR / "ate.db")))
DEPLOY_ROOT = Path(os.environ.get("ATE_DEPLOY_ROOT", str(BASE_DIR / "deploy_runtime")))
CONTROLLER_URL = os.environ.get("ATE_CONTROLLER_URL", "http://127.0.0.1:8070").rstrip("/")
DEFAULT_TIMEOUT = int(os.environ.get("ATE_DEPLOY_TIMEOUT", "1800"))

_lock = threading.Lock()


class DeployError(Exception):
    """部署过程中的可读错误。"""


class DeployBusy(DeployError):
    """已有部署批次在执行：同一时间只允许一个批次。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables() -> None:
    with _lock, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS deploy_batch (
                batch_id   TEXT PRIMARY KEY,
                tps_id     TEXT NOT NULL,
                tps_name   TEXT DEFAULT '',
                uut        TEXT DEFAULT '',
                bench_id   TEXT DEFAULT '',
                status     TEXT NOT NULL,
                message    TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                finished_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS deploy_item (
                batch_id   TEXT NOT NULL,
                seq        INTEGER NOT NULL,
                object     TEXT NOT NULL,
                name       TEXT DEFAULT '',
                version    TEXT DEFAULT '',
                script     TEXT DEFAULT '',
                status     TEXT NOT NULL,
                message    TEXT DEFAULT '',
                log_path   TEXT DEFAULT '',
                started_at TEXT DEFAULT '',
                finished_at TEXT DEFAULT '',
                PRIMARY KEY (batch_id, seq)
            );
            CREATE TABLE IF NOT EXISTS deploy_installed (
                target_id  TEXT NOT NULL,
                object     TEXT NOT NULL,
                version    TEXT NOT NULL,
                batch_id   TEXT DEFAULT '',
                path       TEXT DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (target_id, object)
            );
            """
        )
        # 兼容旧库：deploy_item 增补 target 列
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(deploy_item)")}
        if "target" not in cols:
            conn.execute("ALTER TABLE deploy_item ADD COLUMN target TEXT DEFAULT ''")
        if "key" not in cols:
            conn.execute("ALTER TABLE deploy_item ADD COLUMN key TEXT DEFAULT ''")
        inst_cols = {r["name"] for r in conn.execute("PRAGMA table_info(deploy_installed)")}
        if "verified" not in inst_cols:
            conn.execute("ALTER TABLE deploy_installed ADD COLUMN verified INTEGER DEFAULT 0")
        if "probe_version" not in inst_cols:
            conn.execute("ALTER TABLE deploy_installed ADD COLUMN probe_version TEXT DEFAULT ''")


def running_batch() -> str | None:
    """返回当前执行中的批次号；没有则返回 None。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT batch_id FROM deploy_batch WHERE status = 'running' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return row["batch_id"] if row else None


def recover_stale() -> int:
    """进程启动时，把上一次进程遗留的「执行中」批次标记为中断。

    仅在模块导入时调用一次：此时本进程尚未创建任何批次，
    因此所有 running 记录都属于已退出的旧进程。
    """
    ensure_tables()
    stamp = _now()
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT batch_id FROM deploy_batch WHERE status = 'running'").fetchall()
        for row in rows:
            batch_id = row["batch_id"]
            conn.execute(
                "UPDATE deploy_batch SET status = 'failed', message = ?, finished_at = ? WHERE batch_id = ?",
                ("服务重启，批次中断", stamp, batch_id),
            )
            conn.execute(
                "UPDATE deploy_item SET status = 'failed', message = ?, finished_at = ? "
                "WHERE batch_id = ? AND status IN ('pending', 'running')",
                ("服务重启，批次中断", stamp, batch_id),
            )
    return len(rows)


# ---------------------------------------------------------------- TPS 解析

PKG_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$")
KEY_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
DEPLOY_MODES = {"ensure", "force"}
FAILURE_POLICIES = {"abort", "continue"}


def resolve_items(tps: dict) -> tuple[list[dict], list[str]]:
    """解析 TPS 的 deploy 声明。

    格式：{key, package: "object@version", target: {id}, mode, on_failure, depends_on}
    兼容旧格式 {object, version, script}：script 仅在包 manifest 缺 entry 时使用。
    """
    items: list[dict] = []
    errors: list[str] = []

    deploy = tps.get("deploy")
    if deploy is None:
        errors.append("TPS 未声明 deploy（device_info 兜底已停用）")
        return items, errors
    if not isinstance(deploy, list) or not deploy:
        errors.append("deploy 必须是非空数组")
        return items, errors

    keys: list[str] = []
    for idx, raw in enumerate(deploy, start=1):
        if not isinstance(raw, dict):
            errors.append(f"deploy[{idx}] 必须是对象")
            continue

        package = str(raw.get("package") or "").strip()
        if not package:
            legacy_obj = str(raw.get("object") or "").strip()
            legacy_ver = str(raw.get("version") or "").strip()
            if legacy_obj and legacy_ver:
                package = f"{legacy_obj}@{legacy_ver}"
            else:
                errors.append(f"deploy[{idx}] 缺少 package（格式 object@version）")
                continue
        if not PKG_RE.match(package):
            errors.append(f"deploy[{idx}].package 格式应为 object@version：{package}")
            continue
        obj, version = package.split("@", 1)

        key = str(raw.get("key") or obj).strip()
        if not KEY_RE.match(key):
            errors.append(f"deploy[{idx}].key 只允许小写字母、数字、下划线、短横线且不超过 32 位：{key}")
            continue
        if key in keys:
            errors.append(f"deploy[{idx}].key 重复：{key}")
            continue
        keys.append(key)

        target = raw.get("target")
        if target is None:
            target = {"id": "local"}
        elif not isinstance(target, dict) or not str(target.get("id") or "").strip():
            errors.append(f'deploy[{idx}].target 必须是 {{"id": "..."}}')
            continue
        else:
            target = {"id": str(target["id"]).strip()}

        mode = str(raw.get("mode") or "ensure")
        if mode not in DEPLOY_MODES:
            errors.append(f"deploy[{idx}].mode 只能是 ensure 或 force：{mode}")
            continue

        on_failure = str(raw.get("on_failure") or "abort")
        if on_failure not in FAILURE_POLICIES:
            errors.append(f"deploy[{idx}].on_failure 只能是 abort 或 continue：{on_failure}")
            continue

        depends = raw.get("depends_on") or []
        if not isinstance(depends, list) or any(not isinstance(d, str) for d in depends):
            errors.append(f"deploy[{idx}].depends_on 必须是字符串数组")
            continue

        script = str(raw.get("script") or "").strip()
        if script and Path(script).is_absolute():
            errors.append(f"deploy[{idx}].script 必须是相对路径：{script}")
            continue

        items.append(
            {
                "key": key,
                "object": obj,
                "version": version,
                "package": package,
                "name": str(raw.get("name") or obj),
                "script": script,
                "args": [],
                "target": target,
                "mode": mode,
                "on_failure": on_failure,
                "depends_on": depends,
            }
        )

    for item in items:
        for dep in item["depends_on"]:
            if dep not in keys:
                errors.append(f"deploy[{item['key']}].depends_on 引用了未声明的 key：{dep}")

    if errors:
        return items, errors

    try:
        items = order_items(items)
    except DeployError as exc:
        return items, [str(exc)]
    return items, errors


def order_items(items: list[dict]) -> list[dict]:
    """按 depends_on 拓扑排序：被依赖的先装；出现环即报错。"""
    by_key = {item["key"]: item for item in items}
    ordered: list[dict] = []
    done: set[str] = set()
    visiting: list[str] = []

    def visit(key: str) -> None:
        if key in done:
            return
        if key in visiting:
            raise DeployError("deploy 依赖存在环：" + " -> ".join(visiting + [key]))
        visiting.append(key)
        for dep in by_key[key]["depends_on"]:
            if dep in by_key:
                visit(dep)
        visiting.pop()
        done.add(key)
        ordered.append(by_key[key])

    for item in items:
        visit(item["key"])
    return ordered


# ---------------------------------------------------------------- Controller 交互

def _http_json(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:300]
        raise DeployError(f"ATE Controller 返回 {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise DeployError(f"无法连接 ATE Controller（{CONTROLLER_URL}）：{exc.reason}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pick_version(obj: str, wanted: str) -> tuple[str, dict]:
    detail = _http_json(f"{CONTROLLER_URL}/api/packages/{obj}")
    versions = detail.get("versions") or []
    if not versions:
        raise DeployError(f"ATE Controller 中没有 {obj} 的任何版本")
    if wanted:
        for item in versions:
            if item.get("version") == wanted:
                return wanted, item
        raise DeployError(f"{obj} 不存在版本 {wanted}")
    return versions[0]["version"], versions[0]


def _download(obj: str, version: str, dest: Path) -> str:
    url = f"{CONTROLLER_URL}/api/packages/{obj}/{version}/download"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, dest.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
            return resp.headers.get("X-Package-Sha256", "")
    except urllib.error.HTTPError as exc:
        raise DeployError(f"下载失败 {obj}@{version}：HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise DeployError(f"下载失败 {obj}@{version}：{exc.reason}") from exc


def _run_script(workdir: Path, script_rel: str, args: list[str], log_path: Path) -> int:
    script = (workdir / script_rel).resolve()
    if not str(script).startswith(str(workdir.resolve())):
        raise DeployError(f"脚本路径越界：{script_rel}")
    if not script.is_file():
        raise DeployError(f"包内没有部署脚本：{script_rel}")

    suffix = script.suffix.lower()
    if suffix == ".ps1":
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    elif suffix in (".bat", ".cmd"):
        cmd = ["cmd", "/c", str(script)]
    elif suffix == ".py":
        cmd = [sys.executable, str(script)]
    elif suffix == ".exe":
        cmd = [str(script)]
    else:
        raise DeployError(f"不支持的脚本类型：{suffix}")
    cmd.extend(args)

    with log_path.open("w", encoding="utf-8", errors="ignore") as log:
        log.write("$ " + " ".join(cmd) + "\n\n")
        log.flush()
        proc = subprocess.run(
            cmd,
            cwd=str(workdir),
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=DEFAULT_TIMEOUT,
            shell=False,
        )
    return proc.returncode


# ---------------------------------------------------------------- 批次执行

def _update_batch(batch_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE deploy_batch SET {cols} WHERE batch_id = ?", (*fields.values(), batch_id))


def _update_item(batch_id: str, seq: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE deploy_item SET {cols} WHERE batch_id = ? AND seq = ?", (*fields.values(), batch_id, seq))


def _record_installed(item: dict, version: str, path, batch_id: str, verified: bool = False, probe_version: str = "") -> None:
    """成功部署后记录「目标机上该对象已装版本」，供状态查询。"""
    target_id = str((item.get("target") or {}).get("id") or "local")
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO deploy_installed (target_id, object, version, batch_id, path, updated_at, verified, probe_version) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(target_id, object) DO UPDATE SET "
            "version = excluded.version, batch_id = excluded.batch_id, "
            "path = excluded.path, updated_at = excluded.updated_at, "
            "verified = excluded.verified, probe_version = excluded.probe_version",
            (target_id, item["object"], version, batch_id, str(path), _now(), 1 if verified else 0, probe_version),
        )


def installed_state(object_key: str, target_id: str = "local") -> dict | None:
    """查询某个对象在某目标机上的已部署情况；未部署返回 None。"""
    ensure_tables()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM deploy_installed WHERE target_id = ? AND object = ?",
            (target_id, object_key),
        ).fetchone()
    return dict(row) if row else None


def list_installed(target_id: str = "") -> list[dict]:
    ensure_tables()
    sql = "SELECT * FROM deploy_installed"
    args: tuple = ()
    if target_id:
        sql += " WHERE target_id = ?"
        args = (target_id,)
    sql += " ORDER BY updated_at DESC"
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _fetch_manifest(obj: str, version: str) -> dict:
    """读取包的 manifest（含 deploy.entry / deploy.probe）。"""
    return _http_json(f"{CONTROLLER_URL}/api/packages/{obj}/{version}/manifest", timeout=10)


def _probe_spec(manifest: dict) -> dict | None:
    """取探测规格：manifest.deploy.probe 优先，其次顶层 probe。"""
    deploy = manifest.get("deploy") if isinstance(manifest.get("deploy"), dict) else {}
    probe = deploy.get("probe") or manifest.get("probe")
    if not isinstance(probe, dict):
        return None
    script = str(probe.get("script") or "").strip()
    if not script:
        return None
    return {
        "script": script,
        "args": probe.get("args") or [],
        "timeout_s": int(probe.get("timeout_s") or 10),
        "expect_exit_code": int(probe.get("expect_exit_code") or 0),
    }


def _probe_state(workdir: Path, probe: dict, log_path: Path) -> dict:
    """执行探测脚本，返回 {installed, version, detail}；任何异常都视为未安装。"""
    script = (workdir / probe["script"]).resolve()
    if not str(script).startswith(str(workdir.resolve())) or not script.is_file():
        log_path.write_text("", encoding="utf-8")
        return {"installed": False, "version": "", "detail": f"探测脚本不存在：{probe['script']}"}

    suffix = script.suffix.lower()
    if suffix == ".ps1":
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    elif suffix in (".bat", ".cmd"):
        cmd = ["cmd", "/c", str(script)]
    elif suffix == ".py":
        cmd = [sys.executable, str(script)]
    elif suffix == ".sh":
        cmd = ["bash", str(script)]
    else:
        cmd = [str(script)]
    cmd.extend(probe["args"])

    try:
        proc = subprocess.run(
            cmd, cwd=str(workdir), capture_output=True, text=True, timeout=probe["timeout_s"], shell=False
        )
    except subprocess.TimeoutExpired:
        log_path.write_text(f"探测超时（{probe['timeout_s']}s）\n", encoding="utf-8")
        return {"installed": False, "version": "", "detail": f"探测超时（{probe['timeout_s']}s）"}

    output = (proc.stdout or "") + (proc.stderr or "")
    log_path.write_text(output, encoding="utf-8", errors="ignore")
    if proc.returncode != probe["expect_exit_code"]:
        return {"installed": False, "version": "", "detail": f"探测退出码 {proc.returncode}"}

    payload = ""
    for line in reversed((proc.stdout or "").splitlines()):
        if line.strip().startswith("{"):
            payload = line.strip()
            break
    if not payload:
        return {"installed": False, "version": "", "detail": "探测输出不是 JSON"}
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {"installed": False, "version": "", "detail": "探测输出 JSON 解析失败"}
    return {
        "installed": bool(data.get("installed")),
        "version": str(data.get("version") or ""),
        "detail": str(data.get("detail") or ""),
    }


def _deploy_one(batch_id: str, seq: int, item: dict) -> str:
    obj = item["object"]
    target_id = str((item.get("target") or {}).get("id") or "local")
    _update_item(batch_id, seq, status="running", started_at=_now(), message="")

    workdir = DEPLOY_ROOT / batch_id / f"{seq:02d}-{obj}"
    workdir.mkdir(parents=True, exist_ok=True)
    log_path = DEPLOY_ROOT / batch_id / f"{seq:02d}-{obj}.log"
    probe_log = DEPLOY_ROOT / batch_id / f"{seq:02d}-{obj}.probe.log"

    try:
        version, meta = _pick_version(obj, item.get("version") or "")
        _update_item(batch_id, seq, version=version)

        script = item.get("script") or str(meta.get("entry") or "").strip()
        if not script:
            raise DeployError("包 manifest 未声明部署脚本 entry，且 TPS 未覆盖 script")

        archive = workdir / "package.zip"
        header_sha = _download(obj, version, archive)
        actual = _sha256(archive)
        expected = (header_sha or meta.get("sha256") or "").lower()
        if expected and actual.lower() != expected:
            raise DeployError(f"sha256 校验失败：期望 {expected[:12]}…，实际 {actual[:12]}…")

        extract_dir = workdir / "package"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract_dir)

        probe = _probe_spec(_fetch_manifest(obj, version))
        state = None
        if probe:
            state = _probe_state(extract_dir, probe, probe_log)
            if item.get("mode") == "ensure" and state.get("installed"):
                seen = state.get("version") or version
                if not state.get("version") or state.get("version") == version:
                    _update_item(
                        batch_id, seq, status="skipped",
                        message=f"探测确认目标 {target_id} 已装 {seen}，按 ensure 策略跳过",
                        finished_at=_now(),
                    )
                    return "skipped"
        elif item.get("mode") == "ensure":
            inst = installed_state(obj, target_id)
            if inst and inst.get("version") == version:
                _update_item(
                    batch_id, seq, status="skipped",
                    message=f"部署记录显示已装 {version}（包未提供 probe，未经验证）",
                    finished_at=_now(),
                )
                return "skipped"

        code = _run_script(extract_dir, script, item.get("args") or [], log_path)
        if code != 0:
            raise DeployError(f"部署脚本退出码 {code}，详见日志 {log_path}")

        verified = False
        probe_version = ""
        if probe:
            state = _probe_state(extract_dir, probe, probe_log)
            probe_version = state.get("version") or ""
            if not state.get("installed"):
                raise DeployError(f"部署后探测未通过：{state.get('detail') or '探测结果为未安装'}")
            if probe_version and probe_version != version:
                raise DeployError(f"部署后探测版本不符：期望 {version}，实际 {probe_version}")
            verified = True

        suffix = "（探测已验证）" if verified else "（包未提供 probe，未验证）"
        _update_item(
            batch_id, seq, status="success",
            message=f"{obj}@{version} 部署完成{suffix}",
            log_path=str(log_path), finished_at=_now(),
        )
        _record_installed(item, version, extract_dir, batch_id, verified, probe_version)
        return "success"
    except Exception as exc:  # noqa: BLE001
        _update_item(
            batch_id, seq, status="failed",
            message=str(exc),
            log_path=str(log_path) if log_path.exists() else "",
            finished_at=_now(),
        )
        return "failed"

def _run_batch(batch_id: str, items: list[dict]) -> None:
    """串行执行：默认前序失败即中止，后续包标记 skipped。"""
    success = failed = skipped = 0
    aborted = False
    try:
        for seq, item in enumerate(items, start=1):
            if aborted:
                _update_item(
                    batch_id,
                    seq,
                    status="skipped",
                    message="前序包部署失败，按串行策略跳过",
                    finished_at=_now(),
                )
                skipped += 1
                continue
            outcome = _deploy_one(batch_id, seq, item)
            if outcome == "success":
                success += 1
            elif outcome == "skipped":
                skipped += 1
            else:
                failed += 1
                if str(item.get("on_failure") or "abort") != "continue":
                    aborted = True
        status = "success" if failed == 0 else ("partial" if success else "failed")
        message = f"成功 {success} 项，失败 {failed} 项"
        if skipped:
            message += f"，跳过 {skipped} 项"
        _update_batch(batch_id, status=status, message=message, finished_at=_now())
    except Exception as exc:  # noqa: BLE001
        _update_batch(batch_id, status="failed", message=str(exc), finished_at=_now())


def start_deploy(tps: dict, tps_id: str, uut: str, bench_id: str = "") -> dict:
    ensure_tables()
    busy = running_batch()
    if busy:
        raise DeployBusy(f"已有部署批次正在执行：{busy}；同一时间只允许一个批次")
    items, errors = resolve_items(tps)
    if errors:
        raise DeployError("；".join(errors))
    if not items:
        raise DeployError("TPS 中没有可部署的对象")

    import uuid

    batch_id = f"dep-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    with _lock:
        busy = running_batch()
        if busy:
            raise DeployBusy(f"已有部署批次正在执行：{busy}；同一时间只允许一个批次")
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO deploy_batch (batch_id, tps_id, tps_name, uut, bench_id, status, message, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (batch_id, tps_id, str(tps.get("name") or tps.get("id") or ""), uut, bench_id, "running", "", _now()),
            )
            for seq, item in enumerate(items, start=1):
                conn.execute(
                    "INSERT INTO deploy_item (batch_id, seq, object, name, version, script, status, target, key) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (batch_id, seq, item["object"], item["name"], item["version"], item.get("script") or "",
                     "pending", str((item.get("target") or {}).get("id") or "local"), item.get("key") or ""),
                )
            conn.commit()
        finally:
            conn.close()

    threading.Thread(target=_run_batch, args=(batch_id, items), daemon=True).start()
    return {"ok": True, "batch_id": batch_id, "count": len(items), "items": items}


def get_batch(batch_id: str) -> dict:
    ensure_tables()
    with _connect() as conn:
        batch = conn.execute("SELECT * FROM deploy_batch WHERE batch_id = ?", (batch_id,)).fetchone()
        if not batch:
            raise DeployError(f"批次不存在：{batch_id}")
        rows = conn.execute(
            "SELECT * FROM deploy_item WHERE batch_id = ? ORDER BY seq", (batch_id,)
        ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        inst = installed_state(d["object"], d.get("target") or "local")
        d["installed_version"] = inst["version"] if inst else ""
        d["up_to_date"] = bool(inst and inst["version"] == d.get("version"))
        d["installed_verified"] = bool(inst and inst.get("verified"))
        items.append(d)
    return {"ok": True, "batch": dict(batch), "items": items}


def list_batches(limit: int = 20) -> dict:
    ensure_tables()
    with _connect() as conn:
        batches = conn.execute(
            "SELECT * FROM deploy_batch ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)
        ).fetchall()
        result = []
        for b in batches:
            items = conn.execute(
                "SELECT seq, object, name, version, status, message FROM deploy_item WHERE batch_id = ? ORDER BY seq",
                (b["batch_id"],),
            ).fetchall()
            entry = dict(b)
            entry["items"] = [dict(i) for i in items]
            result.append(entry)
    return {"ok": True, "batches": result}


def controller_status() -> dict:
    try:
        return _http_json(f"{CONTROLLER_URL}/api/health", timeout=5)
    except DeployError as exc:
        return {"ok": False, "error": str(exc)}


try:
    recover_stale()
except Exception:  # noqa: BLE001 - 启动恢复失败不阻断导入
    pass
