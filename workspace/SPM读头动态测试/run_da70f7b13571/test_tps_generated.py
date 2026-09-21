# -*- coding: utf-8 -*-
"""ATE Runner 自动生成的 TPS 执行入口 —— 请勿手工修改

TPS      : SPM读头动态测试 (spm_rh_dyn)   schema: tps.v2
任务      : da70f7b13571    UUT: SPMDYN000042
测试台    : SPM读头动态测试台/SPMTS202609120001
运行模式  : simulate
测试用例  : 7 条（环境初始化 1 / 测试套 5 / 环境终止 1）
生成时间  : 2026-09-12 23:19:53

执行方式（由 ATE Runner 逐条调用，也可在本目录手动跑）：
    pytest test_tps_generated.py -v
    pytest "test_tps_generated.py::test_case[TC001]" -v
"""

import pytest

SETUP_CASES = [{'id': 'SETUP01', 'no': 1, 'name': '环境初始化', 'kind': 'init', 'func': 'test_setup', 'case': 'testcase.env_setup::init_environment', 'params': {'psu_alias': 'psu', 'awg_alias': 'awg', 'spin_alias': 'spin', 'scope_alias': 'scope', 'fixture_alias': 'fixture', 'voltage': 3.3}, 'checks': {}, 'config': '', 'optional': False, 'description': '上电供电、设置回放激励、读头复位、示波器自检，并登记夹具信息'}]
TEST_CASES = [{'id': 'TC001', 'no': 1, 'name': '上电电压测试', 'kind': 'test', 'func': 'test_case', 'case': 'testcase.power::power_on_voltage', 'params': {'psu_alias': 'psu', 'dmm_alias': 'dmm', 'voltage': 3.3}, 'checks': {'VDD': 'TC_VDD'}, 'config': '', 'optional': False, 'description': '供电 3.3V 后由数字万用表测量读头上电电压'}, {'id': 'TC002', 'no': 2, 'name': '读头工作电流测试', 'kind': 'test', 'func': 'test_case', 'case': 'testcase.power::read_head_current', 'params': {'dmm_alias': 'dmm', 'spin_alias': 'spin', 'rpm': 3600}, 'checks': {'IDD': 'TC_IDD'}, 'config': '', 'optional': False, 'description': '转速稳定后测量读头工作电流'}, {'id': 'TC003', 'no': 3, 'name': '转速闭环测试', 'kind': 'test', 'func': 'test_case', 'case': 'testcase.comm::spin_speed', 'params': {'motion_alias': 'spin', 'rpm': 3600}, 'checks': {'RPM': 'TC_RPM', 'HS_MS': 'TC_MOTION_HS'}, 'config': '', 'optional': False, 'description': '串口握手后设定 3600 rpm，回读实际转速与握手响应时间'}, {'id': 'TC004', 'no': 4, 'name': '回放信号幅度与频率测试', 'kind': 'test', 'func': 'test_case', 'case': 'testcase.comm::playback_signal', 'params': {'scope_alias': 'scope', 'signal': 'sine', 'freq': 1000, 'vpp': 2.4, 'points': 2000}, 'checks': {'VPP': 'TC_SIG_VPP', 'FREQ': 'TC_SIG_FREQ'}, 'config': '', 'optional': False, 'description': '波形发生器回放 1 kHz / 2.4 Vpp 正弦，示波器测量峰峰值与频率'}, {'id': 'TC005', 'no': 5, 'name': '负载端电压测试', 'kind': 'test', 'func': 'test_case', 'case': 'testcase.comm::load_voltage', 'params': {'eload_alias': 'eload', 'current': 0.5}, 'checks': {'VLOAD': 'TC_ELOAD_V'}, 'config': '', 'optional': False, 'description': '电子负载 CC 0.5A 加载后测量负载端电压'}]
TEARDOWN_CASES = [{'id': 'TD01', 'no': 1, 'name': '环境终止', 'kind': 'teardown', 'func': 'test_teardown', 'case': 'testcase.env_setup::teardown_environment', 'params': {'psu_alias': 'psu', 'awg_alias': 'awg', 'spin_alias': 'spin'}, 'checks': {}, 'config': '', 'optional': False, 'description': '关闭供电与激励输出、停止转速，释放装备'}]


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
