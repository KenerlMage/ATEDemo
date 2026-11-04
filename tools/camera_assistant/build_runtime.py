# -*- coding: utf-8 -*-
"""构建相机助手的自带运行时，并同步到货架发布包。

用法（在本目录下）：

    .venv\\Scripts\\python.exe build_runtime.py

产物：

* ``dist/CameraAssistant/`` —— PyInstaller onedir，自带 Python 与全部依赖；
* ``backend/assistant_shelf/packages/camera-assistant/runtime/`` —— 发布包内副本。

运行时不入 git（``.gitignore`` 已排除 ``**/assistant_shelf/**/runtime/``），
由本脚本在发布前构建；仓库里只保留 ``camera_assistant.spec`` 与这份脚本。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ATE = HERE.parents[1]
SPEC = HERE / "camera_assistant.spec"
DIST = HERE / "dist" / "CameraAssistant"
TARGETS = [
    ATE / "backend" / "assistant_shelf" / "packages" / "camera-assistant" / "runtime",
    ATE / "backend" / "assistant_shelf" / "installed" / "camera-assistant" / "runtime",
]


def human(path: Path) -> str:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    count = sum(1 for f in path.rglob("*") if f.is_file())
    return "%.1f MB / %d files" % (total / 1024 / 1024, count)


def main() -> int:
    if not SPEC.is_file():
        print("找不到 camera_assistant.spec：", SPEC)
        return 2
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("缺少 PyInstaller，先执行：")
        print(r"  .venv\Scripts\python.exe -m pip install pyinstaller -i https://mirrors.aliyun.com/pypi/simple")
        return 2

    print("[1/3] PyInstaller 构建 …")
    code = subprocess.call(
        [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", "--clean"],
        cwd=str(HERE),
    )
    if code != 0 or not DIST.is_dir():
        print("构建失败，退出码", code)
        return code or 1
    print("      dist/CameraAssistant:", human(DIST))

    for target in TARGETS:
        if not target.parent.is_dir():
            print("[2/3] 跳过（发布包不存在）：", target.parent)
            continue
        print("[2/3] 同步 →", target)
        shutil.copytree(DIST, target, dirs_exist_ok=True)
        print("      ", human(target))

    exe = DIST / "CameraAssistant.exe"
    print("[3/3] 完成。双击 start.bat 或直接运行：")
    print("      ", exe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
