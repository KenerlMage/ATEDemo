# -*- coding: utf-8 -*-
"""驱动开发工具链（开发/CI 用，不进运行时依赖链）

* `conformance`：一致性测试套件——任何新驱动必须全过才算"可上架"
* `registry`：驱动包清单（`driver.json`）发现、校验、规格合并、**型号派发**
* `fake`：脚本化传输（没有硬件、也不想走仿真模型时，精确构造应答）
* `cli`：`python -m ate_drivers.kit.cli new|check|list|contract`
"""

from .conformance import run_conformance, CHECKS  # noqa: F401
from .fake import FakeTransport  # noqa: F401
from .registry import (  # noqa: F401
    load_manifest,
    merge_specs,
    model_conflicts,
    resolve_entry,
    resolve_spec,
    scan_dir,
)

__all__ = [
    "CHECKS",
    "FakeTransport",
    "load_manifest",
    "merge_specs",
    "model_conflicts",
    "resolve_entry",
    "resolve_spec",
    "run_conformance",
    "scan_dir",
]
