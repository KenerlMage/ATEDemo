# -*- coding: utf-8 -*-
"""下位机部署（SR02-AR05）与装备助手部署/卸载（SR02-AR06、SR02-AR07）。

下位机：Runner 只向 Controller 取包，经 SSH/SFTP 拷贝后远程执行 probe/install。
装备助手：在货架安装目录内执行 scripts/install、scripts/uninstall；
成功删除安装包，失败保留并打开资源管理器。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import assistant_shelf
import deploy

MAX_SCRIPT_SECONDS = 1800

_ERRORS = {
    "remote_connect_failed": "无法连接下位机",
    "remote_auth_failed": "下位机认证失败",
    "remote_transfer_failed": "软件传输到下位机失败",
    "remote_probe_failed": "下位机探测脚本执行失败",
    "remote_install_failed": "下位机安装脚本返回失败",
    "remote_not_configured": "未配置下位机连接",
    "script_missing": "安装包缺少脚本",
    "script_failed": "脚本执行失败",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _error(code: str, message: str = "", **extra) -> dict:
    return {
        "success": False,
        "message": message or _ERRORS.get(code, code),
        "error_code": code,
        **extra,
    }


# ---------------------------------------------------------------- 脚本执行


def run_script(workdir: Path, script_rel: str, timeout: int = MAX_SCRIPT_SECONDS) -> dict:
    """执行包内脚本；Windows 上按扩展名选解释器。返回 {ok, code, stdout, stderr}。"""
    script = (workdir / script_rel).resolve()
    try:
        script.relative_to(workdir.resolve())
    except ValueError:
        return {"ok": False, "code": -1, "stdout": "", "stderr": "脚本路径越界"}
    if not script.is_file():
        return {"ok": False, "code": -1, "stdout": "", "stderr": f"脚本不存在：{script_rel}"}

    suffix = script.suffix.lower()
    if suffix == ".ps1":
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    elif suffix in (".bat", ".cmd"):
        cmd = ["cmd", "/c", str(script)]
    elif suffix == ".py":
        cmd = [sys.executable, str(script)]
    elif suffix == ".sh":
        cmd = ["sh", str(script)]
    else:
        cmd = [str(script)]
    try:
        proc = subprocess.run(
            cmd, cwd=str(workdir), capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -2, "stdout": "", "stderr": f"脚本超时（>{timeout}s）"}
    except OSError as exc:
        return {"ok": False, "code": -3, "stdout": "", "stderr": f"脚本无法执行：{exc}"}
    out = (proc.stdout or "")[-4000:]
    err = (proc.stderr or "")[-4000:]
    return {"ok": proc.returncode == 0, "code": proc.returncode, "stdout": out, "stderr": err}


# ---------------------------------------------------------------- 下位机部署


def remote_deploy(tps: dict, tps_id: str, target_id: str, timeout: int = MAX_SCRIPT_SECONDS) -> dict:
    """把指定目标的部署项拷到下位机并执行。没有真实目标时返回明确错误码。"""
    items, errors = deploy.resolve_items(tps)
    if errors:
        return _error("remote_not_configured", "；".join(errors))
    picked = [it for it in items if str((it.get("target") or {}).get("id") or "local") == target_id]
    if not picked:
        return _error("remote_not_configured", f"TPS 未声明目标 {target_id} 的部署项")

    target = _target_config(target_id)
    if not target:
        return _error("remote_not_configured", f"装备属性配置中没有目标 {target_id} 的连接信息")

    try:
        import paramiko  # 延迟导入：未安装时给出明确提示
    except ImportError:
        return _error("remote_connect_failed", "未安装 paramiko，无法执行下位机部署")

    client = None
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=target["host"], port=int(target.get("port") or 22),
            username=target.get("username") or "",
            password=target.get("password") or None,
            key_filename=target.get("key_path") or None,
            timeout=15, banner_timeout=15, auth_timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        code = "remote_auth_failed" if "auth" in text or "password" in text else "remote_connect_failed"
        return _error(code, f"{_ERRORS[code]}：{exc}")

    results = []
    try:
        sftp = client.open_sftp()
        remote_root = (target.get("remote_dir") or "/tmp/ate_deploy").rstrip("/")
        _sftp_makedirs(sftp, remote_root)
        work = Path(tempfile.mkdtemp(prefix="ate_remote_deploy_"))
        for item in deploy.order_items(picked):
            obj, version = item["object"], item.get("version") or ""
            local = work / f"{obj}-{version}.zip"
            try:
                # 经 Controller 取包（Runner 不直连版本服务器）
                deploy._download(obj, version, local)  # noqa: SLF001
            except Exception as exc:  # noqa: BLE001
                return _error("remote_transfer_failed", f"取包失败 {obj}@{version}：{exc}", results=results)
            remote_zip = f"{remote_root}/{obj}.zip"
            try:
                sftp.put(str(local), remote_zip)
            except Exception as exc:  # noqa: BLE001
                return _error("remote_transfer_failed", f"上传失败 {obj}：{exc}", results=results)
            probe = _run_remote_script(client, remote_zip, "probe", target)
            if probe["code"] == 0:
                results.append({"object": obj, "version": version, "status": "skipped",
                                "message": "探测已安装，按 ensure 跳过"})
                continue
            install = _run_remote_script(client, remote_zip, "install", target)
            ok = install["code"] == 0
            results.append({
                "object": obj, "version": version,
                "status": "success" if ok else "failed",
                "probe_code": probe["code"], "install_code": install["code"],
                "stdout": install["stdout"][-1500:], "stderr": install["stderr"][-1500:],
            })
            if not ok:
                return _error("remote_install_failed", f"{obj} 安装失败（退出码 {install['code']}）",
                              results=results, target=target_id)
        return {"success": True, "message": f"下位机 {target_id} 部署完成", "target": target_id,
                "results": results, "finished_at": _now()}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


def _target_config(target_id: str) -> Optional[dict]:
    """从装备属性配置读取目标连接信息；没有则返回 None。"""
    for path in (
        Path(__file__).resolve().parent / "equipment.json",
        Path(__file__).resolve().parent / "remote_targets.json",
    ):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key in ("devices", "targets", "items"):
            for item in data.get(key) or []:
                if str(item.get("id") or item.get("alias") or "") == target_id:
                    return item
        if isinstance(data.get(target_id), dict):
            return data[target_id]
    return None


def _sftp_makedirs(sftp, remote: str) -> None:
    parts = [p for p in remote.split("/") if p]
    cur = ""
    for part in parts:
        cur += "/" + part
        try:
            sftp.stat(cur)
        except IOError:
            try:
                sftp.mkdir(cur)
            except IOError:
                pass


def _run_remote_script(client, remote_zip: str, kind: str, target: dict) -> dict:
    """解包并执行远程脚本；脚本路径由包内 manifest 约定 scripts/<kind>。"""
    root = (target.get("remote_dir") or "/tmp/ate_deploy").rstrip("/")
    work = f"{root}/work"
    cmd = (
        f"mkdir -p {work} && cd {work} && rm -rf pkg && mkdir pkg && "
        f"unzip -oq {remote_zip} -d pkg && "
        f"cd pkg && (test -f scripts/{kind} || test -f scripts/{kind}.sh) && "
        f"(test -f scripts/{kind} && sh scripts/{kind} || sh scripts/{kind}.sh)"
    )
    try:
        _stdin, stdout, stderr = client.exec_command(cmd, timeout=MAX_SCRIPT_SECONDS)
        code = stdout.channel.recv_exit_status()
        return {"code": code, "stdout": stdout.read().decode("utf-8", "replace"),
                "stderr": stderr.read().decode("utf-8", "replace")}
    except Exception as exc:  # noqa: BLE001
        return {"code": -1, "stdout": "", "stderr": str(exc)}


# ---------------------------------------------------------------- 装备助手部署


def _installed_dir(assistant_id: str) -> Path:
    return assistant_shelf.INSTALLED_DIR / assistant_shelf._safe_id(assistant_id)  # noqa: SLF001


def _assistant_manifest(aid: str) -> dict:
    dest = _installed_dir(aid)
    for name in ("assistant.json", "manifest.json"):
        path = dest / name
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def assistant_status(assistant_id: str) -> dict:
    aid = assistant_shelf._safe_id(assistant_id)  # noqa: SLF001
    dest = _installed_dir(aid)
    downloaded = dest.is_dir()
    manifest = _assistant_manifest(aid) if downloaded else {}
    scripts = (manifest.get("scripts") or {}) if isinstance(manifest, dict) else {}
    return {
        "success": True,
        "id": aid,
        "downloaded": downloaded,
        "needs_deploy": bool(manifest.get("needs_deploy", True)),
        "deployed": bool(manifest.get("deployed")),
        "scripts": scripts,
    }


def deploy_assistant(assistant_id: str, timeout: int = MAX_SCRIPT_SECONDS) -> dict:
    """执行 scripts/install；成功删除安装包，失败保留并打开资源管理器。"""
    aid = assistant_shelf._safe_id(assistant_id)  # noqa: SLF001
    dest = _installed_dir(aid)
    if not dest.is_dir():
        return _error("script_missing", "助手尚未下载到本机，不能部署")
    manifest = _assistant_manifest(aid)
    scripts = manifest.get("scripts") or {}
    install = scripts.get("install") or ""
    if not install:
        # 兼容旧助手包：没有脚本时按"已下载即已部署"处理
        _write_meta(aid, manifest, deployed=True)
        return {"success": True, "message": f"{aid} 无安装脚本，按已部署处理", "deployed": True,
                "no_script": True}

    probe = scripts.get("probe") or ""
    if probe:
        probed = run_script(dest, probe, timeout)
        if probed["code"] == 0:
            _write_meta(aid, manifest, deployed=True)
            _cleanup_installer(aid)
            return {"success": True, "message": f"{aid} 已安装（探测确认），跳过部署",
                    "deployed": True, "skipped": True, "probe_code": 0}

    result = run_script(dest, install, timeout)
    if not result["ok"]:
        _open_explorer(dest)
        return _error("script_failed", f"部署失败（退出码 {result['code']}）：{result['stderr'][-300:]}",
                      deployed=False, installer_kept=True, dir=str(dest))
    _write_meta(aid, manifest, deployed=True)
    _cleanup_installer(aid)
    return {"success": True, "message": f"{aid} 部署完成，安装包已清理", "deployed": True,
            "dir": str(dest)}


def undeploy_assistant(assistant_id: str, timeout: int = MAX_SCRIPT_SECONDS) -> dict:
    """执行 scripts/uninstall；成功后状态回到未部署。"""
    aid = assistant_shelf._safe_id(assistant_id)  # noqa: SLF001
    dest = _installed_dir(aid)
    if not dest.is_dir():
        return _error("script_missing", f"未下载该助手：{aid}")
    manifest = _assistant_manifest(aid)
    scripts = manifest.get("scripts") or {}
    uninstall = scripts.get("uninstall") or ""
    if not uninstall:
        return _error("script_missing", "该助手包未提供卸载脚本")
    result = run_script(dest, uninstall, timeout)
    if not result["ok"]:
        return _error("script_failed", f"卸载失败（退出码 {result['code']}）：{result['stderr'][-300:]}")
    _write_meta(aid, manifest, deployed=False)
    return {"success": True, "message": f"{aid} 已卸载", "deployed": False}


def _write_meta(aid: str, manifest: dict, *, deployed: bool) -> None:
    dest = _installed_dir(aid)
    for name in ("assistant.json", "manifest.json"):
        path = dest / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        data["deployed"] = deployed
        data["deployed_at"] = _now() if deployed else ""
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    (dest / "manifest.json").write_text(
        json.dumps({"id": aid, "deployed": deployed, "deployed_at": _now() if deployed else ""},
                   ensure_ascii=False, indent=2), encoding="utf-8")


def _cleanup_installer(aid: str) -> None:
    """成功部署后清理本地安装包（货架 packages 目录下的源包与临时 zip）。"""
    pkg_dir = Path(getattr(assistant_shelf, "PACKAGES_DIR", Path("."))) / aid
    if pkg_dir.is_dir():
        try:
            shutil.rmtree(pkg_dir)
        except OSError:
            pass


def _open_explorer(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(path)])  # noqa: S603,S607
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 路由


def register_routes(app, base_dir: Path, db_path=None) -> None:
    from fastapi import Body

    @app.post("/api/deploy/remote")
    def deploy_remote_route(payload: dict = Body(default=None)):
        data = payload if isinstance(payload, dict) else {}
        tps_id = str(data.get("tps_id") or "")
        target_id = str(data.get("target_id") or data.get("target") or "")
        if not tps_id or not target_id:
            return _error("remote_not_configured", "需要 tps_id 与 target_id")
        try:
            from deploy_api import _load_tps  # 复用现有 TPS 定位逻辑

            tps = _load_tps(tps_id)
        except Exception as exc:  # noqa: BLE001
            return _error("remote_not_configured", f"未找到 TPS：{exc}")
        try:
            return remote_deploy(tps, tps_id, target_id)
        except Exception as exc:  # noqa: BLE001
            return _error("remote_install_failed", f"下位机部署异常：{exc}")

    @app.get("/api/runner/assistants/{assistant_id}/deploy-status")
    def assistant_deploy_status(assistant_id: str):
        try:
            return assistant_status(assistant_id)
        except Exception as exc:  # noqa: BLE001
            return _error("script_failed", f"读取助手状态失败：{exc}")

    @app.post("/api/runner/assistants/{assistant_id}/deploy")
    def assistant_deploy_route(assistant_id: str):
        try:
            return deploy_assistant(assistant_id)
        except Exception as exc:  # noqa: BLE001
            return _error("script_failed", f"助手部署失败：{exc}")

    @app.post("/api/runner/assistants/{assistant_id}/undeploy")
    def assistant_undeploy_route(assistant_id: str):
        try:
            return undeploy_assistant(assistant_id)
        except Exception as exc:  # noqa: BLE001
            return _error("script_failed", f"助手卸载失败：{exc}")
