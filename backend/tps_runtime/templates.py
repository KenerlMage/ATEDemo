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
测试步骤  : {total} 条（环境初始化 {setup_count} / 测试套 {test_count} / 环境终止 {teardown_count}）
人工操作  : {manual_count} 条（由 ATE Runner 弹窗提示操作员选择「继续 / 中止」，不进入 pytest）
生成时间  : {created_at}

执行方式（由 ATE Runner 逐条调用，也可在本目录手动跑）：
    pytest test_tps_generated.py -v
    pytest "test_tps_generated.py::test_case[TC001]" -v
"""

import pytest

SETUP_CASES = {setup_cases}
TEST_CASES = {test_cases}
TEARDOWN_CASES = {teardown_cases}

# 人工操作环节（清单里 type=manual 的条目）：由 Runner 弹窗执行，pytest 不收集
MANUAL_CASES = {manual_cases}


{setup_block}


{test_block}


{teardown_block}
'''

_SECTION = '''@pytest.mark.parametrize("case", {const}, ids=[c["id"] for c in {const}])
def {func}(case, ate_runner):
    """{doc}"""
    ate_runner(case)'''

_SECTION_EMPTY = '''@pytest.mark.skip(reason="{reason}")
def {func}(ate_runner):
    """{doc}"""
    pass'''

# (清单段, 生成文件里的常量名, 测试函数名, 函数说明, 空段原因)
_SECTIONS = (
    ("setup", "SETUP_CASES", "test_setup", "环境初始化：TPS 清单 setup 段",
     "该段只有人工操作条目，由 Runner 弹窗执行"),
    ("test", "TEST_CASES", "test_case", "测试套：TPS 清单 cmd_suit 段（每条对应 testcase/ 里的一个实现）",
     "该段只有人工操作条目，由 Runner 弹窗执行"),
    ("teardown", "TEARDOWN_CASES", "test_teardown", "环境终止：TPS 清单 teardown 段",
     "该段只有人工操作条目，由 Runner 弹窗执行"),
)


def render_generated(tps: dict, entries: dict, *, task_id: str = "", uut: str = "",
                     mode: str = "simulate", bench: str = "", created_at: str = "") -> str:
    """渲染执行入口文件内容（用例列表用 repr 嵌入，保证是合法 Python 字面量）

    人工操作条目（`type: manual`）不进 pytest：运行到该条时由 Runner 弹窗提示操作员，
    因此这里只参数化有实现的用例；某段全是人工条目时用 skip 占位（pytest 不允许空参数集）。
    """
    real = {k: [c for c in entries[k] if not c.get("manual")] for k in ("setup", "test", "teardown")}
    manual_cases = [
        {"id": c["id"], "name": c["name"], "resource": c.get("resource", "")}
        for k in ("setup", "test", "teardown") for c in entries[k] if c.get("manual")
    ]
    blocks = [
        (_SECTION if real[key] else _SECTION_EMPTY).format(const=const, func=func, doc=doc, reason=reason)
        for key, const, func, doc, reason in _SECTIONS
    ]
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
        manual_count=len(manual_cases),
        created_at=created_at,
        setup_cases=repr(real["setup"]),
        test_cases=repr(real["test"]),
        teardown_cases=repr(real["teardown"]),
        manual_cases=repr(manual_cases),
        setup_block=blocks[0],
        test_block=blocks[1],
        teardown_block=blocks[2],
    )
