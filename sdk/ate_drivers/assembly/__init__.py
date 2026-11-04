# -*- coding: utf-8 -*-
"""L5 · 装配层：把型号、连接参数与厂商类接起来。

两件事，都只做「选择与构造」，不碰任何协议细节：

* `registry`：示波器/仪器型号注册表与 `open_scope`；
* `cameras`：相机型号注册表与 `open_camera`。

型号是设备档案里的**确定字段**，查不到就报错，不做模糊匹配。
"""

from .cameras import (  # noqa: F401
    CAMERA_INTERFACES,
    camera_class,
    camera_models,
    open_camera,
    register_camera_driver,
    register_camera_model,
)
from .registry import (  # noqa: F401
    driver_class,
    entries,
    models,
    open_scope,
    register_driver,
    register_model,
    self_check,
)
