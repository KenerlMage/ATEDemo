# -*- coding: utf-8 -*-
"""ate_drivers · ATE Runner 仪器驱动库公开契约（冻结层）

这一层是**平台与驱动包之间的稳定接口**：包内符号只增不改，任何破坏性变更必须
升 `API_VERSION` 的 major 位，并按 `contract.py` 的弃用流程过渡。

**用户代码只需要 import 两个名字**（网口示波器通用顶层接口）：

    from ate_drivers import open_scope, Scope

    with open_scope("MSO54", host="192.168.10.41", port=4000) as scope:   # 型号来自设备档案
        print(scope.identity())
        wave = scope.capture(points=2000)

型号直接决定用哪个厂商驱动（无打分、无模糊匹配）；原始命令、前导解析、缩放换算
全部收在家族层与厂商包内，用户看到的是 `set_timebase` / `capture` / `measure` 这类业务方法。

驱动开发者用这里的基类与家族契约：

    from ate_drivers import (
        InstrumentDriver, ScopeDriver, PassiveDriver,   # 基类 / 家族契约
        DeviceConfig,                                   # 设备连接参数
        DriverError, UnsupportedCapability,             # 错误
        Capability, API_VERSION, check_compatible,      # 能力与版本
    )

工具链（`ate_drivers.kit`）不在运行时依赖里，只为开发与 CI 服务：

    python -m ate_drivers.kit.cli new   my-vendor-scope --family scope --model ACME-3000
    python -m ate_drivers.kit.cli check samples/tek_mso5
    python -m ate_drivers.kit.cli models           # 看已登记型号
"""

#: 设备簿（别名 → 档案 → 句柄）在 ``assembly.devices``：
#: 同型号多台设备靠别名区分，用例侧用 ``connect("scope_main")`` 取句柄。
from .core.capabilities import KNOWN_CAPABILITIES, Capability  # noqa: F401
from .core.contract import (  # noqa: F401
    API_VERSION,
    CONTRACT_NAME,
    RESULT_SCHEMA,
    check_compatible,
    deprecated,
)
from .core.errors import (  # noqa: F401
    ConfigurationError,
    ContractMismatch,
    DriverError,
    DriverNotFound,
    DriverTimeout,
    ProtocolError,
    TransportError,
    UnsupportedCapability,
)
from .core.models import DeviceConfig  # noqa: F401
from .base import InstrumentDriver, PassiveDriver  # noqa: F401
from .families.scope import (  # noqa: F401
    ChannelSetup,
    Measurement,
    ScopeDriver,
    TimebaseSetup,
    Waveform,
)
from .core.transport import (  # noqa: F401
    SerialTransport, SimulateTransport, SocketTransport, Transport, VisaTransport,
    make_transport, set_serial_factory, set_visa_factory, transport_class, visa_available,
    visa_factory,
)
from .core.endpoint import (  # noqa: F401
    AUTO, BACKEND_CHOICES, DEFAULT_BACKEND, NATIVE, SERIAL_DEFAULTS, SUPPORTED_BACKENDS,
    SUPPORTED_INTERFACES, VISA, Endpoint, backends_of, endpoint_from, interfaces_of,
    normalize_backend, normalize_interface, resolve_backend, visa_resource_for,
)
from .families.camera import (  # noqa: F401
    CameraChannel,
    CameraDriver,
    CameraSetup,
    FrameInfo,
    frame_stats,
    simulate_frame,
    write_png,
)
from .assembly.cameras import (  # noqa: F401
    camera_class,
    camera_entries,
    camera_entry,
    camera_models,
    open_camera,
    register_camera_driver,
)
from .api.camera import Camera  # noqa: F401
from .api import Scope  # noqa: F401
from .assembly.scopes import (  # noqa: F401
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
from .assembly.devices import (  # noqa: F401
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

__version__ = "1.2.0"

__all__ = [
    "API_VERSION",
    "CONTRACT_NAME",
    "RESULT_SCHEMA",
    "Capability",
    "KNOWN_CAPABILITIES",
    "ChannelSetup",
    "ConfigurationError",
    "ContractMismatch",
    "DeviceConfig",
    "DriverError",
    "DriverNotFound",
    "DriverTimeout",
    "InstrumentDriver",
    "Measurement",
    "PassiveDriver",
    "ProtocolError",
    "ScopeDriver",
    "Scope",
    "Camera",
    "CameraChannel",
    "CameraDriver",
    "CameraSetup",
    "FrameInfo",
    "camera_class",
    "camera_entries",
    "camera_entry",
    "camera_models",
    "frame_stats",
    "open_camera",
    "register_camera_driver",
    "simulate_frame",
    "write_png",
    "Endpoint",
    "SERIAL_DEFAULTS",
    "AUTO",
    "BACKEND_CHOICES",
    "DEFAULT_BACKEND",
    "NATIVE",
    "SUPPORTED_BACKENDS",
    "SUPPORTED_INTERFACES",
    "VISA",
    "SerialTransport",
    "Transport",
    "VisaTransport",
    "SimulateTransport",
    "SocketTransport",
    "TimebaseSetup",
    "Transport",
    "TransportError",
    "UnsupportedCapability",
    "Waveform",
    "check_compatible",
    "deprecated",
    "scope_class",
    "backends_of",
    "endpoint_from",
    "scope_entries",
    "normalize_backend",
    "resolve_backend",
    "set_visa_factory",
    "transport_class",
    "visa_available",
    "visa_factory",
    "visa_resource_for",
    "interfaces_of",
    "make_transport",
    "normalize_interface",
    "set_serial_factory",
    "scope_models",
    "open_scope",
    "scope_self_check",
    "camera_aliases",
    "camera_self_check",
    "camera_vendors",
    "register_camera_model",
    "register_scope_driver",
    "register_scope_model",
    "scope_aliases",
    "scope_entry",
    "scope_vendors",
    "unregister_scope_driver",
    "connect",
    "device_aliases",
    "device_config",
    "disconnect",
    "disconnect_all",
    "handle_of",
    "handles",
    "load_devices",
    "register_device",
    "register_family",
    "registered_families",
    "unload_devices",
]
