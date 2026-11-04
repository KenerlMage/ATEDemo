# -*- coding: utf-8 -*-
"""仿真辅助：确定性偏差。

用 md5 而不是 random——同一台设备同一次测量永远得到同一个值，
回归脚本才能断言“仿真结果落在阈值内”。
"""

from __future__ import annotations

import hashlib


def stable_ratio(seed: str, spread: float = 0.004) -> float:
    """由字符串种子生成 ±spread 的确定性偏差。"""
    h = hashlib.md5(str(seed).encode("utf-8")).hexdigest()
    unit = int(h[:8], 16) / 0xFFFFFFFF
    return (unit - 0.5) * 2 * spread
