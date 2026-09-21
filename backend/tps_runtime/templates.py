# -*- coding: utf-8 -*-
"""运行目录里生成的文件模板（执行入口 pytest 文件）"""

from __future__ import annotations

GENERATED_FILE = "test_tps_generated.py"
ENV_FILE = "ate_env.json"
RESULT_FILE = "run_result.json"

_HEADER = '''# -*- coding: utf-8 -*-
"""ATE Runner 自动生成的 TPS 执行入口 —— 请勿手工修改

TPS      : {name} ({tps_id})   schema: {schema}
任务      : {task_id}    UUT: {uut}
测试台    : {bench}
运行模式  : {mode}
测试用例  : {total} 条（环境初始化 {setup_count} / 测试套 {test_count} / 环境终止 {teardown_count}）
生成时间  : {created_at}

执行方式（由 ATE Runner 逐条调用，也可在本目录手动跑）：
    pytest test_tps_generated.py -v
    pytest "test_tps_generated.py::test_case[TC001]" -v
"""

import pytest

SETUP_CASES = {setup_cases}
TEST_CASES = {test_cases}
TEARDOWN_CASES = {teardown_cases}


@pytest.mark.parametrize("case", SETUP_CASES, ids=[c["id"] for c in SETUP_CASES])
def test_setup(case, ate_runner):
    """环境初始化：TPS 清单 setup 段"""
    ate_runner(case)


@pytest.mark.parametrize("case", TEST_CASES, ids=[c["id"] for c in TEST_CASES])
def test_case(case, ate_runner):
    """测试套：TPS 清单 cmd_suit 段（每条对应 testcase/ 里的一个实现）"""
    ate_runner(case)


@pytest.mark.parametrize("case", TEARDOWN_CASES, ids=[c["id"] for c in TEARDOWN_CASES])
def test_teardown(case, ate_runner):
    """环境终止：TPS 清单 teardown 段"""
    ate_runner(case)
'''


def render_generated(tps: dict, entries: dict, *, task_id: str = "", uut: str = "",
                     mode: str = "simulate", bench: str = "", created_at: str = "") -> str:
    """渲染执行入口文件内容（用例列表用 repr 嵌入，保证是合法 Python 字面量）"""
    total = sum(len(entries[k]) for k in ("setup", "test", "teardown"))
    return _HEADER.format(
        name=tps.get("name", ""),
        tps_id=tps.get("id", ""),
        schema=tps.get("schema", ""),
        task_id=task_id,
        uut=uut,
        bench=bench or "(未指定)",
        mode=mode,
        total=total,
        setup_count=len(entries["setup"]),
        test_count=len(entries["test"]),
        teardown_count=len(entries["teardown"]),
        created_at=created_at,
        setup_cases=repr(entries["setup"]),
        test_cases=repr(entries["test"]),
        teardown_cases=repr(entries["teardown"]),
    )
