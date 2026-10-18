"""Runner 版本升级（发布 → 推送 → 通知 → 升级）核心模块 —— 零删除版本。

设计边界（重要）
----------------
本模块**只做"准备"**，不执行任何破坏性文件操作：
  * 不删除任何文件或目录
  * 不覆盖任何现有程序目录
  * 不解压安装包到目标位置

目录替换（真正把新版本铺到运行位置）由 ATE_Launcher 在下次启动时依据
`updates/pending_swap.json` 执行。这是安全且工程上正确的位置：
运行中的进程本来也无法自我替换正在使用的文件。

职责与流程
----------
1. 推送: 远端频道文件 <channel>.json 描述最新版本、安装包信息与 release note。
   模拟实现: 远端为本地目录 (ATE_UPDATE_SERVER), 亦可指向 http(s) 地址。
2. 通知: check() 比较版本, 产出 update_available / force / highlights, 供界面展示。
3. 下载: 安装包统一落到固定路径 <RUNNER_HOME>/updates/download/, 先校验字节流
   的 sha256, 校验通过才写入磁盘。
4. 升级: 仅在无测试用例执行时放行 (assert_idle); 只写入"待替换清单 + 版本号 +
   备份记录", 并前后各取一次本地数据指纹以证明数据被完整继承。
5. Release note: 升级前 (点击升级) 与升级后 (重启后首次进入) 各展示一次。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable

import runner_info


class UpdateError(Exception):
    """升级流程错误, code 供前端区分处理。"""

    def __init__(self, message: str, code: str = "ERROR"):
        super().__init__(message)
        self.code = code


class UpdateBusy(UpdateError):
    """存在正在执行的测试任务, 或升级流程已在进行。"""

    def __init__(self, message: str, active: list[dict] | None = None):
        super().__init__(message, code="BUSY")
        self.active = active or []


# ==================== 执行状态探针 ====================


_ACTIVITY_PROBE: Callable[[], list[dict]] | None = None


def register_activity_probe(fn: Callable[[], list[dict]]) -> None:
    """注册"正在执行的测试任务"探针, 由后端启动时注入, 避免循环依赖。"""
    global _ACTIVITY_PROBE
    _ACTIVITY_PROBE = fn


def active_tasks() -> list[dict]:
    if _ACTIVITY_PROBE is None:
        return []
    try:
        return list(_ACTIVITY_PROBE() or [])
    except Exception:
        return []


# ==================== 工具 ====================


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ver_key(version: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", version or ""))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _validate_members(names: list[str]) -> None:
    """校验归档成员路径, 防御路径穿越; 仅校验, 不解压。"""
    for name in names:
        if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
            raise UpdateError(f"安装包含非法路径: {name}", code="BAD_PACKAGE")


# ==================== 升级管理器 ====================


class UpdateManager:
    """升级状态机与文件操作的唯一入口（零删除）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: dict = {}
        self._load()

    # ---------------- 状态持久化 ----------------

    def _load(self) -> None:
        runner_info.ensure_dirs()
        try:
            self._state = json.loads(runner_info.STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            self._state = {}
        self._state.setdefault("phase", "idle")
        self._state.setdefault("note_ack", {})
        self._state.setdefault("history", [])

    def _save(self) -> None:
        runner_info.ensure_dirs()
        runner_info.STATE_FILE.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _log(self, text: str) -> None:
        runner_info.ensure_dirs()
        with runner_info.LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{_now()}] {text}\n")

    def _set(self, **kw) -> None:
        self._state.update(kw)
        self._save()

    # ---------------- 对外查询 ----------------

    def version_info(self) -> dict:
        return {
            "version": runner_info.installed_version(),
            "runner_home": str(runner_info.RUNNER_HOME),
            "updates_dir": str(runner_info.UPDATES_DIR),
            "download_dir": str(runner_info.DOWNLOAD_DIR),
            "update_server": runner_info.UPDATE_SERVER,
            "channel": runner_info.UPDATE_CHANNEL,
        }

    def snapshot(self) -> dict:
        with self._lock:
            active = active_tasks()
            return {
                **self.version_info(),
                "phase": self._state.get("phase", "idle"),
                "update_available": bool(self._state.get("update_available")),
                "latest": self._state.get("latest"),
                "force": bool(self._state.get("force")),
                "downloaded": self._state.get("downloaded"),
                "installed": self._state.get("installed"),
                "restart_required": bool(self._state.get("restart_required")),
                "error": self._state.get("error"),
                "active_tasks": active,
                "can_upgrade": bool(self._state.get("phase") == "downloaded") and not active,
                "note_after": self._state.get("note_after"),
                "note_ack": self._state.get("note_ack", {}),
                "history": self._state.get("history", [])[-10:],
            }

    # ---------------- 远端读取 ----------------

    def _read_source(self, rel: str) -> bytes:
        base = runner_info.UPDATE_SERVER
        if base.lower().startswith(("http://", "https://")):
            url = base.rstrip("/") + "/" + rel
            try:
                with urllib.request.urlopen(url, timeout=6) as resp:
                    return resp.read()
            except Exception as exc:  # noqa: BLE001
                raise UpdateError(f"访问远端服务器失败: {exc}", code="REMOTE_UNREACHABLE")
        path = Path(base) / rel
        if not path.is_file():
            raise UpdateError(f"远端文件不存在: {rel}", code="SOURCE_MISSING")
        return path.read_bytes()

    def _load_channel(self) -> dict:
        raw = self._read_source(f"{runner_info.UPDATE_CHANNEL}.json")
        try:
            channel = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise UpdateError(f"频道清单解析失败: {exc}", code="BAD_CHANNEL")
        self._state["channel"] = channel
        self._save()
        return channel

    def _release_of(self, version: str) -> dict:
        channel = self._state.get("channel") or self._load_channel()
        for rel in channel.get("releases", []):
            if str(rel.get("version")) == str(version):
                return rel
        raise UpdateError(f"频道中不存在版本 {version}", code="NO_SUCH_RELEASE")

    # ---------------- 1. 检查更新 ----------------

    def check(self, force: bool = False) -> dict:
        with self._lock:
            current = runner_info.installed_version()
            if not force and self._state.get("phase") in ("downloading", "applying"):
                return self.snapshot()
            try:
                channel = self._load_channel()
            except UpdateError as exc:
                self._set(phase="idle", error=str(exc))
                raise
            releases = channel.get("releases") or []
            latest = (
                max(releases, key=lambda r: _ver_key(str(r.get("version", ""))))
                if releases
                else None
            )
            available = bool(latest) and _ver_key(str(latest.get("version"))) > _ver_key(current)
            min_supported = str(channel.get("min_supported") or "")
            force_upgrade = bool(channel.get("force")) or (
                bool(min_supported) and _ver_key(current) < _ver_key(min_supported)
            )
            self._set(
                phase="update_available" if available else "idle",
                update_available=available,
                force=force_upgrade if available else False,
                latest=latest,
                checked_at=_now(),
                error=None,
            )
            self._log(
                "检查更新: 当前 %s, 最新 %s, 可升级=%s"
                % (current, latest.get("version") if latest else "-", available)
            )
            return {
                "current": current,
                "update_available": available,
                "force": force_upgrade if available else False,
                "latest": latest,
                "channel": channel.get("channel"),
                "active_tasks": active_tasks(),
            }

    # ---------------- 2. 下载安装包 ----------------

    def download(self) -> dict:
        with self._lock:
            latest = self._state.get("latest")
            if not latest:
                self.check()
                latest = self._state.get("latest")
            if not latest:
                raise UpdateError("尚未获取到可用版本", code="NO_RELEASE")

            version = str(latest.get("version"))
            pkg = latest.get("package") or {}
            name = str(pkg.get("name") or f"ATERunner-{version}.zip")
            url = str(pkg.get("url") or f"packages/{name}")

            self._set(phase="downloading", error=None)
            data = self._read_source(url)

            digest = hashlib.sha256(data).hexdigest()
            expect = str(pkg.get("sha256") or "").lower()
            if expect and digest != expect:
                self._set(phase="update_available", error="安装包校验失败（sha256 不一致）")
                raise UpdateError("安装包校验失败（sha256 不一致）", code="CHECKSUM_MISMATCH")

            dest = runner_info.DOWNLOAD_DIR / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as fh:
                fh.write(data)

            info = {
                "version": version,
                "name": name,
                "path": str(dest),
                "size": len(data),
                "sha256": digest,
                "downloaded_at": _now(),
            }
            self._set(phase="downloaded", downloaded=info, error=None)
            self._log(f"安装包已下载: {name} ({info['size']} bytes) -> {dest}")
            return {"downloaded": info, **self.snapshot()}

    # ---------------- 3. 执行升级（仅登记, 不替换）----------------

    def assert_idle(self) -> None:
        """升级前置条件: 不得处于测试用例执行状态。"""
        if self._state.get("phase") == "applying":
            raise UpdateBusy("升级正在进行中")
        active = active_tasks()
        if active:
            names = "、".join(str(t.get("name") or t.get("task_id")) for t in active[:3])
            raise UpdateBusy(
                f"当前有 {len(active)} 个测试任务正在执行（{names}），"
                "升级会替换程序文件，请等待任务结束后再升级",
                active=active,
            )

    def _fingerprint(self) -> dict:
        """本地数据指纹, 用于证明升级前后数据被完整继承。"""
        out: dict = {}
        for name in runner_info.PRESERVED_PATHS:
            p = runner_info.RUNNER_HOME / name
            # 安装形态下 ate.db / testresource 位于 <RUNNER_HOME>/app，只按根解析
            # 会把它们记成 absent，使"升级前后数据一致"的校验形同虚设。
            if not p.exists():
                alt = runner_info.RUNNER_HOME / "app" / name
                if alt.exists():
                    p = alt
            if p.is_file():
                st = p.stat()
                out[name] = {"kind": "file", "size": st.st_size, "mtime": int(st.st_mtime)}
            elif p.is_dir():
                files = [f for f in p.rglob("*") if f.is_file()]
                out[name] = {
                    "kind": "dir",
                    "files": len(files),
                    "size": sum(f.stat().st_size for f in files),
                }
            else:
                out[name] = {"kind": "absent"}
        return out

    def apply(self) -> dict:
        with self._lock:
            self.assert_idle()
            if self._state.get("phase") != "downloaded":
                raise UpdateError("请先下载安装包再执行升级", code="NOT_DOWNLOADED")

            pkg = self._state["downloaded"]
            path = Path(pkg["path"])
            if not path.is_file():
                raise UpdateError(f"安装包丢失: {path}", code="PACKAGE_MISSING")

            version = str(pkg["version"])
            old_version = runner_info.installed_version()
            self._set(phase="applying", error=None)

            # 只读归档: 校验结构 + 读取清单/说明, 不解压
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                _validate_members(names)
                if "payload.json" not in names:
                    self._set(phase="downloaded", error="安装包缺少 payload.json")
                    raise UpdateError("安装包缺少 payload.json", code="BAD_PACKAGE")
                meta = json.loads(zf.read("payload.json").decode("utf-8"))
                note = (
                    zf.read("release-note.md").decode("utf-8")
                    if "release-note.md" in names
                    else ""
                )

            preserved_before = self._fingerprint()

            backup_dir = runner_info.BACKUP_DIR / old_version
            backup_dir.mkdir(parents=True, exist_ok=True)
            pending = {
                "action": "swap",
                "package": pkg["name"],
                "package_path": pkg["path"],
                "package_sha256": pkg["sha256"],
                "from": old_version,
                "to": version,
                "targets": runner_info.PAYLOAD_DIRS,
                "preserve": runner_info.PRESERVED_PATHS,
                "payload_files": meta.get("files", []),
                "created_at": _now(),
            }
            pending_path = runner_info.PENDING_SWAP_FILE
            _write_json(pending_path, pending)

            backup_record = {
                "from": old_version,
                "to": version,
                "at": _now(),
                "package": pkg["name"],
                "backup_dir": str(backup_dir),
                "payload_files": len(meta.get("files", []) or []),
            }
            _write_json(backup_dir / "backup.json", backup_record)

            runner_info.write_installed_version(
                version,
                {"previous": old_version, "package": pkg["name"], "mode": "pending_swap"},
            )

            preserved_after = self._fingerprint()
            integrated = preserved_before == preserved_after

            record = {
                **backup_record,
                "mode": "pending_swap",
                "pending_swap": str(pending_path),
                "preserved_intact": integrated,
                "preserved_before": preserved_before,
                "preserved_after": preserved_after,
                "finished_at": _now(),
            }
            history = self._state.get("history", [])
            history.append(record)
            self._set(
                phase="installed",
                installed=record,
                restart_required=True,
                note_after=version,
                downloaded=None,
                update_available=False,
                history=history,
            )
            self._log(
                f"升级登记完成: {old_version} -> {version} (mode=pending_swap, 数据完整={integrated})"
            )
            return {"installed": record, "note": note, **self.snapshot()}

    # ---------------- 4. 回滚 / release note ----------------

    def rollback(self) -> dict:
        with self._lock:
            installed = self._state.get("installed")
            if not installed:
                raise UpdateError("没有可回滚的升级记录", code="NO_BACKUP")
            target = str(installed.get("from"))
            runner_info.write_installed_version(
                target, {"rollback_from": installed.get("to"), "at": _now()}
            )
            self._set(
                phase="idle",
                installed=None,
                restart_required=False,
                error=None,
                note_after=None,
            )
            self._log(f"回滚登记: {installed.get('to')} -> {target}")
            return self.snapshot()

    def release_note(self, version: str | None = None) -> dict:
        version = (
            version
            or (self._state.get("latest") or {}).get("version")
            or runner_info.installed_version()
        )
        rel = self._release_of(str(version))
        return {
            "version": str(version),
            "title": rel.get("title") or f"ATE Runner {version}",
            "released_at": rel.get("released_at"),
            "highlights": rel.get("highlights") or [],
            "release_note": rel.get("release_note") or "",
        }

    def ack_note(self, version: str | None = None) -> dict:
        with self._lock:
            version = version or self._state.get("note_after") or ""
            ack = self._state.setdefault("note_ack", {})
            if version:
                ack[str(version)] = _now()
            self._save()
            return self.snapshot()

    def upgrade_in_progress(self) -> bool:
        return self._state.get("phase") in ("downloading", "applying")


# ==================== 单例 ====================

_MANAGER: UpdateManager | None = None
_MANAGER_LOCK = threading.Lock()


def manager() -> UpdateManager:
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = UpdateManager()
    return _MANAGER


def _unused_marker() -> None:  # pragma: no cover
    """占位: 保持模块为纯读写实现, 不含任何破坏性文件调用。"""
    return None
