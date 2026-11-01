# -*- coding: utf-8 -*-
"""驱动自动发现：新增驱动只需在 drivers/ 目录放一个模块，不改 factory.py

约定（与《驱动随 Runner 发包 —— 测试用例如何引用驱动》一致）：

1. 模块内定义 `Driver` 子类，类属性 `driver_key` / `family` / `label` 必填；
2. 模块文件名不得与基础设施模块重名（见 `_SKIP`），且不以 `_` 开头；
3. 类文档首行作为设备类型描述，写入「装备属性配置」的下拉说明。

发现的时机是**进程启动时扫描一次**，不监听文件变化、不支持运行时热插拔：
新增或替换驱动必须随 Runner 安装包发布，现场不允许往安装目录投放驱动文件。
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any, Iterable, Optional

from .base import Driver

#: 基础设施模块：不参与驱动发现
_SKIP = {"__init__", "base", "factory", "discovery", "transport", "models", "errors"}


def package_dir() -> Path:
    """驱动包所在目录（即 `backend/drivers/`）"""
    return Path(__file__).resolve().parent


def is_driver_class(obj: Any, module_name: str = "") -> bool:
    """判定模块内定义的驱动类：Driver 子类、非基类、声明了 `driver_key`"""
    if not isinstance(obj, type) or obj is Driver:
        return False
    if not issubclass(obj, Driver):
        return False
    if module_name and getattr(obj, "__module__", "") != module_name:
        return False
    return bool(str(getattr(obj, "driver_key", "") or "").strip())


def _summary(doc: Optional[str]) -> str:
    """取文档首行作为类型描述"""
    for line in str(doc or "").strip().splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _spec_of(cls: Any, module_name: str) -> dict:
    """把驱动类转成受控清单条目（字段与 factory.DRIVER_TYPES 一致）"""
    key = str(cls.driver_key).strip()
    return {
        "key": key,
        "family": str(getattr(cls, "family", "") or "").strip(),
        "label": str(getattr(cls, "label", "") or key).strip(),
        "cls": cls,
        "description": _summary(cls.__doc__) or f"自动发现的新增驱动（{module_name}）",
        "source": "discovered",
        "module": module_name,
    }


def _load_from_path(path: Path, module_name: str):
    """从任意目录的 .py 文件加载模块（只用于自检与测试，不注册到 sys.modules）"""
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载驱动模块: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scan(builtin_keys: Iterable[str] = (), root: Optional[str | Path] = None) -> dict:
    """扫描驱动目录并返回候选类型

    返回 `{"specs", "duplicates", "errors", "skipped", "scanned"}`：

    * `specs`       —— 可并入受控清单的新增类型，按模块名排序；
    * `duplicates`  —— 可发现模块之间的重复 `key`（内置已覆盖的 key 属正常，不记）；
    * `errors`      —— 导入失败的模块，**单个坏文件不影响其余驱动**；
    * `skipped`     —— 没有声明任何驱动类的模块；
    * `scanned`     —— 实际尝试导入的模块数。

    `root` 缺省为驱动包目录；传入其他目录时按文件路径加载（供自检与回归测试使用）。
    """
    base = Path(root or package_dir())
    in_package = base.resolve() == package_dir().resolve()
    builtin = {str(k) for k in builtin_keys}
    taken = set(builtin)
    specs: list[dict] = []
    duplicates: list[dict] = []
    errors: list[dict] = []
    skipped: list[str] = []

    files = sorted(
        (p for p in base.glob("*.py") if p.stem not in _SKIP and not p.stem.startswith("_")),
        key=lambda p: p.stem,
    )
    for path in files:
        name = path.stem
        module_name = f"{__package__}.{name}" if in_package else f"{__package__}._scan_{name}"
        try:
            module = (importlib.import_module(module_name) if in_package
                      else _load_from_path(path, module_name))
        except Exception as e:                      # 隔离：坏模块不拖垮进程启动
            errors.append({"module": module_name, "error": f"{type(e).__name__}: {e}"})
            continue
        found = 0
        for attr in sorted(vars(module)):
            obj = getattr(module, attr)
            if not is_driver_class(obj, module_name):
                continue
            found += 1
            spec = _spec_of(obj, module_name)
            if spec["key"] in builtin:
                continue        # 内置清单已覆盖该 key：属正常情况，不记重复
            if spec["key"] in taken:
                duplicates.append({"key": spec["key"], "module": module_name})
                continue
            taken.add(spec["key"])
            specs.append(spec)
        if not found:
            skipped.append(module_name)

    return {"specs": specs, "duplicates": duplicates, "errors": errors,
            "skipped": skipped, "scanned": len(files)}


__all__ = ["is_driver_class", "package_dir", "scan"]
