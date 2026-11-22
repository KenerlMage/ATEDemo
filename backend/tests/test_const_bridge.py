"""Runner 侧：能直接解析 Controller 写出的两种关联字段（跨系统桥）。"""
import const_runtime


def test_reads_controller_new_field():
    tps = {'machine_constant_bindings': [
        {'version': '2026.11.08', 'pn': 'SPM-RH-01', 'model_key': 'SPM_RH',
         'alias': '读头1', 'source': 'default'},
        {'version': '2026.11.08', 'pn': 'SPM-RH-01', 'model_key': 'SPM_RH',
         'alias': '读头2', 'source': 'sn'},
    ]}
    rows = const_runtime.parse_bindings(tps)
    assert [(r['model_key'], r['alias'], r['source']) for r in rows] == [
        ('SPM_RH', '读头1', 'default'),
        ('SPM_RH', '读头2', 'sn'),
    ]
    assert all(r['version'] == '2026.11.08' for r in rows)


def test_expands_controller_legacy_models():
    tps = {'constant_bindings': [
        {'version': 'v1', 'pn': 'SPM-RH-01', 'models': ['SPM_RH', 'SPM_TEMP'], 'source': 'default'},
    ]}
    rows = const_runtime.parse_bindings(tps)
    assert [r['model_key'] for r in rows] == ['SPM_RH', 'SPM_TEMP']
    assert len({r['alias'] for r in rows}) == 2


def test_runner_field_still_works_and_empty_is_empty():
    rows = const_runtime.parse_bindings(
        {'machine_constants': [{'pn': 'P', 'model_key': 'M', 'alias': 'a', 'source': 'default'}]})
    assert rows and rows[0]['model_key'] == 'M'
    assert const_runtime.parse_bindings({}) == []
