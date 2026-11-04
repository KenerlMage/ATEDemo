# -*- coding: utf-8 -*-
"""设备簿：别名取句柄、同型号多台、家族派发。"""

import sqlite3

import pytest

import ate_drivers
from ate_drivers import (
    ConfigurationError,
    DriverNotFound,
    connect,
    device_aliases,
    device_config,
    disconnect,
    disconnect_all,
    handle_of,
    load_devices,
    register_family,
    registered_families,
    unload_devices,
)


def _transport(obj):
    """门面/驱动都能取到链路对象（不同门面包装层数不一样）"""
    for path in (("transport",), ("_driver", "transport"), ("driver", "transport")):
        cur, ok = obj, True
        for name in path:
            cur = getattr(cur, name, None)
            if cur is None:
                ok = False
                break
        if ok:
            return cur
    return None


SCOPE_ROWS = [
    {"bench_id": "bench-a", "device_id": "scope_main", "name": "主示波器", "model": "MSO54",
     "category": "scope", "interface": "LAN", "host": "192.168.10.41", "port": 4000},
    {"bench_id": "bench-a", "device_id": "scope_aux", "name": "辅示波器", "model": "MSO54",
     "category": "scope", "interface": "LAN", "host": "192.168.10.42", "port": 4000},
]


@pytest.fixture(autouse=True)
def _clean_book():
    unload_devices()
    yield
    disconnect_all()
    unload_devices()


def test_same_model_two_units_are_distinguished_by_alias():
    """同型号两台：别名不同 → 两个实例，连接参数各归各的"""
    load_devices(SCOPE_ROWS)

    a = connect("scope_main", mode="simulate")
    b = connect("scope_aux", mode="simulate")

    assert a is not b
    assert _transport(a).cfg.host == "192.168.10.41"
    assert _transport(b).cfg.host == "192.168.10.42"
    assert device_config("scope_main").model == device_config("scope_aux").model == "MSO54"


def test_connect_returns_the_same_handle_until_disconnected():
    load_devices(SCOPE_ROWS)

    first = connect("scope_main", mode="simulate")
    again = connect("scope_main", mode="simulate")
    assert first is again
    assert handle_of("scope_main") is first

    assert disconnect("scope_main") is True
    assert handle_of("scope_main") is None

    fresh = connect("scope_main", mode="simulate")
    assert fresh is not first
    assert disconnect("scope_main") is True
    assert disconnect("scope_main") is False


def test_refresh_rebuilds_the_handle():
    load_devices(SCOPE_ROWS)
    first = connect("scope_main", mode="simulate")
    second = connect("scope_main", mode="simulate", refresh=True)
    assert second is not first


def test_name_works_as_alias_too():
    load_devices(SCOPE_ROWS)
    handle = connect("主示波器", mode="simulate")
    assert handle is handle_of("scope_main")
    assert "scope_main" in device_aliases()


def test_unknown_alias_is_reported_with_hint():
    load_devices(SCOPE_ROWS)
    with pytest.raises(DriverNotFound) as exc:
        connect("scope_nope", mode="simulate")
    message = "%s %s" % (exc.value, getattr(exc.value, "detail", ""))
    assert "scope_nope" in message
    assert "open_scope" in message or "open_camera" in message


def test_duplicate_alias_in_same_bench_is_rejected():
    load_devices(SCOPE_ROWS)
    with pytest.raises(ConfigurationError) as exc:
        ate_drivers.register_device(SCOPE_ROWS[0])
    assert "别名重复" in str(exc.value)


def test_load_devices_accepts_dataclass_and_row():
    load_devices([
        ate_drivers.DeviceConfig(bench_id="bench-b", device_id="scope_c", model="MSO64",
                                 category="scope", interface="LAN", host="10.0.0.3", port=4000),
    ])
    assert device_config("scope_c").host == "10.0.0.3"

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("create table d (bench_id, device_id, name, model, category, interface, host, port)")
    conn.execute("insert into d values (?,?,?,?,?,?,?,?)",
                 ("bench-c", "scope_d", "D 台", "MSO58", "scope", "LAN", "10.0.0.4", 4000))
    rows = conn.execute("select * from d").fetchall()
    load_devices(rows)
    assert device_config("scope_d").model == "MSO58"
    assert "scope_d" in device_aliases()
    conn.close()


def test_camera_family_is_dispatched_by_category():
    load_devices([
        {"bench_id": "bench-a", "device_id": "cam_main", "name": "主相机", "model": "acA1920-40gm",
         "category": "camera", "interface": "USB", "extra": {"serial_number": "40012345"}},
    ])
    handle = connect("cam_main", mode="simulate")
    assert type(handle).__name__ == "Camera"
    assert handle.model in ("acA1920-40gm", "ACA1920-40GM", "acA1920-40GM")


def test_unknown_family_is_rejected():
    load_devices([
        {"bench_id": "bench-a", "device_id": "meter", "model": "34461A", "category": "dmm",
         "interface": "LAN", "host": "10.0.0.9", "port": 5025},
    ])
    with pytest.raises(ConfigurationError) as exc:
        connect("meter", mode="simulate")
    message = "%s %s" % (exc.value, getattr(exc.value, "detail", ""))
    assert "家族" in message
    assert "register_family" in message


def test_register_family_extends_dispatch():
    calls = {}

    def opener(cfg, *, mode, timeout, open):
        calls["alias"] = cfg.device_id
        calls["mode"] = mode
        return object()

    register_family("dmm", opener)
    load_devices([
        {"bench_id": "bench-a", "device_id": "meter", "model": "34461A", "category": "dmm"},
    ])
    connect("meter", mode="real")
    assert calls == {"alias": "meter", "mode": "real"}
    assert "dmm" in registered_families()


def test_bench_id_disambiguates_same_alias():
    load_devices([
        {"bench_id": "bench-a", "device_id": "scope_main", "model": "MSO54",
         "category": "scope", "interface": "LAN", "host": "10.1.0.1", "port": 4000},
        {"bench_id": "bench-b", "device_id": "scope_main", "model": "MSO54",
         "category": "scope", "interface": "LAN", "host": "10.2.0.1", "port": 4000},
    ])
    a = connect("scope_main", bench_id="bench-a", mode="simulate")
    b = connect("scope_main", bench_id="bench-b", mode="simulate")
    assert _transport(a).cfg.host == "10.1.0.1"
    assert _transport(b).cfg.host == "10.2.0.1"

    with pytest.raises(ConfigurationError) as exc:
        connect("scope_main", mode="simulate")
    assert "多个台架" in str(exc.value) or "bench_id" in str(exc.value)
