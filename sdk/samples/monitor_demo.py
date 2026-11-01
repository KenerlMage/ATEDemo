"""实时数据演示：向 ate_rt 连续写入数据，用于在没有 TPS 的情况下验证监视窗口

用法（在仓库根执行）:
    python sdk/samples/monitor_demo.py                # 默认 30 秒，10 Hz
    python sdk/samples/monitor_demo.py --seconds 60 --rate 2 --task demo-001

说明:
  * 写入目标由 ATE_RT_URL 决定（未设置时用 redis://127.0.0.1:6379/0）；连不上时脚本报错退出；
  * Runner 的监视窗口打开后，在任务列表里选中本脚本的任务名即可看到实时值。
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ate_rt  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="ate_rt 实时数据演示")
    parser.add_argument("--task", default="", help="任务号（默认 demo-<时间戳>）")
    parser.add_argument("--seconds", type=float, default=30.0, help="持续秒数")
    parser.add_argument("--rate", type=float, default=10.0, help="写入频率 Hz")
    args = parser.parse_args()

    task = args.task or f"demo-{time.strftime('%Y%m%d%H%M%S')}"
    mon = ate_rt.Monitor(task=task, name="实时数据演示", tps="demo_monitor")
    if mon.store_backend == "none":
        print(f"[demo] 实时数据存储不可用，请先启动 Redis 或设置 ATE_RT_URL：{mon.last_error}")
        mon.close()
        return 2
    mon.declare("温度", unit="℃", group="温控", lo=15, hi=45)
    mon.declare("压力", unit="kPa", group="气路", lo=90, hi=110)
    mon.declare("振动", unit="g", group="机械", lo=0, hi=2)
    mon.declare("功率", unit="W", group="电源", lo=0, hi=60)
    mon.event("演示开始")

    period = 1.0 / max(args.rate, 0.2)
    end_at = time.time() + max(args.seconds, 1.0)
    i = 0
    print(f"[demo] 任务 {task} 写入 {args.rate} Hz × {args.seconds}s；存储 {mon.info()['backend']}")
    try:
        while time.time() < end_at:
            t = i * period
            mon.write_many({
                "温度": 25 + 3 * math.sin(t / 6.0),
                "压力": 100 + 2.5 * math.sin(t / 3.5 + 0.6),
                "振动": abs(0.35 * math.sin(t * 4.2)) + 0.02,
                "功率": 30 + 8 * math.sin(t / 9.0) + (0.5 if i % 20 == 0 else 0),
            })
            if i and i % int(max(args.rate, 1) * 5) == 0:
                mon.event(f"第 {i // int(max(args.rate, 1))} 秒")
            i += 1
            time.sleep(period)
    except KeyboardInterrupt:
        print("\n[demo] 已中止")
    finally:
        mon.event("演示结束")
        mon.close()
    print(f"[demo] 共写入 {i} 组数据；任务号 {task}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
