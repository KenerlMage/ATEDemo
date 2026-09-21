"""ATE 自动测试装备 - Demo 测试用例集

模拟对一个被测设备(DUT)的典型测试流程:
上电电压检查 / 断电电流检查 / UART 通信握手 / 固件校验和验证。

单独运行: python -m pytest test_cases/demo_test.py -v
"""

import time

import pytest


class TestPowerOn:
    """电源上电测试"""

    def test_device_power_on_voltage(self):
        """模拟上电: DUT 输出电压应在 3.3V ±5% 范围内"""
        measured_voltage = 3.31  # 模拟万用表读数 (V)
        assert 3.3 * 0.95 <= measured_voltage <= 3.3 * 1.05, f"电压越界: {measured_voltage}V"
        time.sleep(0.8)

    def test_device_power_off_current(self):
        """模拟断电: DUT 待机电流应小于 0.1 mA"""
        standby_current = 0.03  # 模拟电流读数 (mA)
        assert standby_current < 0.1, f"待机电流过大: {standby_current}mA"
        time.sleep(0.8)


class TestCommunication:
    """通信接口测试"""

    @pytest.mark.parametrize("baud_rate", [9600, 115200, 460800])
    def test_uart_handshake(self, baud_rate):
        """UART 握手: 对应波特率下应答帧校验必须通过"""
        response_ok = True  # 模拟 DUT 应答结果
        assert response_ok, f"{baud_rate} baud 握手失败"
        time.sleep(0.8)


class TestFirmware:
    """固件检查"""

    def test_firmware_checksum(self):
        """固件校验和: 与出厂值比对一致"""
        checksum_ok = True  # 模拟固件校验结果
        assert checksum_ok, "固件校验和不匹配"
        time.sleep(0.8)
