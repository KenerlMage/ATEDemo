# -*- coding: utf-8 -*-
"""L5 · 装配层：型号注册表 + 设备簿。

三件事，都只做「选择与构造」，不碰协议细节：

* ``scopes``：示波器型号注册表与 ``open_scope``；
* ``cameras``：相机型号注册表与 ``open_camera``；
* ``devices``：别名 → 档案 → 句柄，解决同型号多台设备的区分问题。

家族一律用**同构命名**（``scope_*`` / ``camera_*``），新增设备类型照抄一份即可。
"""

from .cameras import (  # noqa: F401
    CAMERA_INTERFACES,
    camera_aliases,
    camera_class,
    camera_entries,
    camera_entry,
    camera_models,
    camera_self_check,
    camera_vendors,
    open_camera,
    register_camera_driver,
    register_camera_model,
    unregister_camera_driver,
)
from .devices import (  # noqa: F401
    connect,
    device_aliases,
    device_config,
    disconnect,
    disconnect_all,
    handle_of,
    handles,
    load_devices,
    register_device,
    register_family,
    registered_families,
    unload_devices,
)
from .scopes import (  # noqa: F401
    open_scope,
    register_scope_driver,
    register_scope_model,
    scope_aliases,
    scope_class,
    scope_entries,
    scope_entry,
    scope_models,
    scope_self_check,
    scope_vendors,
    unregister_scope_driver,
)
