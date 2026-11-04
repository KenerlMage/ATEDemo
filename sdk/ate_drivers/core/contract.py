# -*- coding: utf-8 -*-
"""契约版本与兼容性规则（驱动库未来兼容性的唯一判据）

版本号 `major.minor`：

* **major**：契约破坏性变更（删动作、改动作语义、改返回值结构、改单位）。
  驱动包的 major 必须与平台**完全相等**，否则拒绝加载。
* **minor**：只增不减的扩展（新增可选动作、新增 detail 键、新增能力）。
  驱动包 minor **小于等于**平台 minor 即可运行；驱动要求更高 minor 时提示升级平台。

平台侧加载任何驱动包前必须调用 `check_compatible()`，不允许"先加载再报错"。
"""

from __future__ import annotations

import functools
import warnings
from typing import Callable, Optional

CONTRACT_NAME = "ate.driver.api"
API_VERSION = "1.2"

# 动作结果结构版本：`do()` 返回体的键集合只能增加，不能改名或删除
RESULT_SCHEMA = "ate.driver.result.v1"
WAVEFORM_SCHEMA = "ate.driver.waveform.v1"
MANIFEST_SCHEMA = "ate.driver.manifest.v1"

# 已弃用动作登记表：动作名 -> (起始版本, 计划移除版本, 替代动作)
DEPRECATIONS: dict[str, tuple[str, str, str]] = {}

# 已冻结的老动作（保留可调用，仅用于读取历史 TPS）
FROZEN_ACTIONS = {
    "scope.get_waveform": "scope.acquire_waveform",
}


def parse_version(text: str) -> tuple[int, int]:
    """`"1.2"` / `"1.2.0"` -> `(1, 2)`；非法版本抛 ValueError"""
    raw = str(text or "").strip()
    parts = raw.split(".")
    if len(parts) < 2 or not all(p.isdigit() for p in parts[:2]):
        raise ValueError(f"版本号格式非法: {text!r}（应为 major.minor）")
    return int(parts[0]), int(parts[1])


def check_compatible(driver_api: str, platform_api: str = API_VERSION) -> tuple[bool, str]:
    """驱动声明的契约版本 vs 平台契约版本

    返回 `(是否兼容, 原因)`；原因在兼容时也给出（便于写进日志与安装记录）。
    """
    try:
        d_major, d_minor = parse_version(driver_api)
        p_major, p_minor = parse_version(platform_api)
    except ValueError as e:
        return False, f"版本号无法解析：{e}"
    if d_major != p_major:
        return False, (
            f"契约主版本不匹配：驱动要求 {d_major}.x，平台提供 {p_major}.{p_minor}；"
            f"请升级驱动包或改用对应平台版本"
        )
    if d_minor > p_minor:
        return False, f"驱动要求契约 {d_major}.{d_minor}，当前平台仅 {p_major}.{p_minor}；请升级平台"
    return True, f"兼容：驱动契约 {d_major}.{d_minor} ⊆ 平台契约 {p_major}.{p_minor}"


def deprecated(since: str, remove_in: str, replacement: str) -> Callable:
    """标注动作已弃用：仍可调用，但发一次告警并记录登记表

    兼容性铁律：弃用必须给替代动作、必须给移除版本、必须至少跨一个 minor，
    且一致性测试套件会检查登记表里每一项都有替代动作。
    """

    def deco(func: Callable) -> Callable:
        action = getattr(func, "__name__", "?")
        DEPRECATIONS[action] = (since, remove_in, replacement)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            warnings.warn(
                f"动作 {action} 自 {since} 起弃用，将在 {remove_in} 移除；请改用 {replacement}",
                DeprecationWarning,
                stacklevel=2,
            )
            return func(*args, **kwargs)

        wrapper.deprecated_since = since            # type: ignore[attr-defined]
        wrapper.deprecated_remove_in = remove_in    # type: ignore[attr-defined]
        wrapper.deprecated_replacement = replacement  # type: ignore[attr-defined]
        return wrapper

    return deco


def describe_contract() -> dict:
    """契约自描述（写进报告、诊断页与驱动包清单）"""
    return {
        "name": CONTRACT_NAME,
        "api_version": API_VERSION,
        "result_schema": RESULT_SCHEMA,
        "waveform_schema": WAVEFORM_SCHEMA,
        "manifest_schema": MANIFEST_SCHEMA,
        "deprecations": {k: list(v) for k, v in DEPRECATIONS.items()},
        "frozen_actions": dict(FROZEN_ACTIONS),
        "rules": [
            "只增不减：新增动作 / 能力 / detail 键；不删除、不改名",
            "不改语义与单位：单位一律 SI 基本单位（V / A / s / Hz / Ω）",
            "新增参数必须可选并带默认值；位置参数顺序不得调整",
            "破坏性变更必须升 major，并提供 ≥1 个 minor 的弃用过渡",
        ],
    }


def action_of(capability: str) -> str:
    """能力名 -> 动作名（能力用点分命名空间，动作与之同名，便于一一对应）"""
    return capability


def capability_of(action: str) -> Optional[str]:
    return action if "." in action else None
