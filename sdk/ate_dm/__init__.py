# -*- coding: utf-8 -*-
"""ate_dm - 装备管理模块（Device Manager）客户端

一句话
    所有仪器连接由装备管理模块（DM）持有，测试程序只向 DM 发指令；
    大块采样数据走共享内存，控制面只传描述符。

典型用法
    from ate_dm import DMClient

    with DMClient() as dm:
        scope = dm.connect(device_row, alias="scope", mode="simulate")
        dm.call(scope, "autoset")
        wave = dm.stream(scope, "capture", dtype="f", points=200000)
        print(wave.count, wave.values()[:4])
        dm.disconnect(scope)

    用例里继续保持原有写法：
        from ate_dm.proxy import open_proxy
        scope = open_proxy(row, alias="scope", mode=mode)
        scope.do("autoset")

环境变量
    ATE_DM_ENDPOINT  端点文件路径（优先）
    ATE_DM_PIPE      管道名 / TCP 地址（无端点文件时使用）
    ATE_DM_ENABLE    用例侧是否改用 DM 驱动（"1" 生效，见 tps_runtime/conftest）
"""

from __future__ import annotations

from .client import DMClient, DMHandle, available, client, open_device  # noqa: F401
from .proxy import DmDriverProxy, open_proxy  # noqa: F401
from .protocol import PROTOCOL_VERSION, DmError  # noqa: F401
from .shm import StreamData  # noqa: F401

__version__ = "1.0.0"
__all__ = [
    "DMClient",
    "DMHandle",
    "DmDriverProxy",
    "DmError",
    "StreamData",
    "open_proxy",
    "open_device",
    "client",
    "available",
    "PROTOCOL_VERSION",
    "__version__",
]
