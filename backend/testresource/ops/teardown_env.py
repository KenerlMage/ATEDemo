"""ATE 测试环境终止 (TPS teardown 步骤)

模拟真实仪器环境释放流程:
  1. 断开串口通道
  2. 关闭模拟电源
  3. 释放资源

退出码 0 = 成功; 非 0 = 失败。
"""

import sys
import time


def main() -> int:
    print("[teardown] 开始终止测试环境...")
    time.sleep(0.5)

    print("[teardown] 1/3 断开串口通道 ...")
    time.sleep(0.3)
    print("[teardown]    UART 通道已释放 OK")

    print("[teardown] 2/3 关闭模拟电源 ...")
    time.sleep(0.3)
    print("[teardown]    电源通道 A 已断电 OK")

    print("[teardown] 3/3 释放资源 ...")
    time.sleep(0.3)
    print("[teardown]    资源释放完成 OK")

    print("[teardown] 测试环境终止完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
