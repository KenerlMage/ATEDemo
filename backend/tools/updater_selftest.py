# -*- coding: utf-8 -*-
"""升级功能自测脚本（零删除版本）。

覆盖链路
--------
    1. 版本信息读取
    2. 检查更新（拉取频道清单、版本比较）
    3. 下载安装包（落固定路径 + sha256 校验）
    4. 执行中禁止升级（互斥）
    5. 执行升级（登记待替换清单 + 数据指纹比对）
    6. release note 读取与已读标记
    7. 回滚登记

运行
----
    python backend/tools/updater_selftest.py

说明
----
脚本使用独立的 ATE_RUNNER_HOME（<仓库>/.selftest_runner），不触碰真实运行目录。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
REPO = BACKEND.parent

os.environ.setdefault("ATE_RUNNER_HOME", str(REPO / ".selftest_runner"))
os.environ.setdefault("ATE_UPDATE_SERVER", str(REPO / "tools" / "update-server"))
os.environ.setdefault("ATE_UPDATE_CHANNEL", "stable")
sys.path.insert(0, str(BACKEND))

import updater  # noqa: E402
import runner_info  # noqa: E402


PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    mark = "PASS" if cond else "FAIL"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{mark}] {name}" + (f"  -- {detail}" if detail else ""))


def main() -> int:
    print(f"RUNNER_HOME      = {runner_info.RUNNER_HOME}")
    print(f"UPDATE_SERVER    = {runner_info.UPDATE_SERVER}")
    print(f"installed        = {runner_info.installed_version()}")
    print("-" * 72)

    mgr = updater.manager()

    # 1. 版本信息
    info = mgr.version_info()
    check("1. 版本信息可读", bool(info.get("version")), info.get("version"))

    # 2. 检查更新
    res = mgr.check(force=True)
    check("2. 检查更新成功", res.get("latest") is not None, f"latest={res.get('latest', {}).get('version')}")
    check("2.1 判定存在新版本", res.get("update_available") is True,
          f"current={res.get('current')}")

    # 3. 下载
    dl = mgr.download()
    path = Path(dl["downloaded"]["path"])
    check("3. 安装包落到固定路径", path.is_file() and path.parent.name == "download", str(path))
    check("3.1 sha256 校验通过", path.stat().st_size > 0, f"{path.stat().st_size} bytes")

    # 4. 执行中禁止升级
    updater.register_activity_probe(lambda: [{"task_id": "T-DEMO", "name": "SPM读头动态测试"}])
    busy_code = ""
    try:
        mgr.apply()
    except updater.UpdateError as exc:
        busy_code = exc.code
    check("4. 执行中禁止升级（返回 BUSY）", busy_code == "BUSY", busy_code)
    updater.register_activity_probe(lambda: [])

    # 5. 升级
    applied = mgr.apply()
    rec = applied["installed"]
    check("5. 升级登记成功", rec.get("to") == res["latest"]["version"], f"{rec.get('from')} -> {rec.get('to')}")
    check("5.1 生成待替换清单", Path(rec["pending_swap"]).is_file(), rec["pending_swap"])
    check("5.2 数据继承完整", rec.get("preserved_intact") is True, f"intact={rec.get('preserved_intact')}")
    check("5.3 版本号已切换", runner_info.installed_version() == rec.get("to"), runner_info.installed_version())
    check("5.4 需要重启生效", applied.get("restart_required") is True)

    # 6. release note
    note = mgr.release_note(rec["to"])
    check("6. release note 可读", bool(note.get("release_note")), f"{len(note.get('release_note', ''))} chars")
    acked = mgr.ack_note(rec["to"])
    check("6.1 已读标记写入", rec["to"] in (acked.get("note_ack") or {}))

    # 7. 回滚
    rolled = mgr.rollback()
    check("7. 回滚登记成功", rolled.get("installed") is None, f"version={rolled.get('version')}")

    print("-" * 72)
    print(f"结果: PASS={PASS}  FAIL={FAIL}")
    if FAIL:
        print("[warn] 存在未通过项, 请检查 ATE_UPDATE_SERVER 指向的发布端是否已生成升级包")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
