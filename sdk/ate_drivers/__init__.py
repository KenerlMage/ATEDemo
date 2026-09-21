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

from .capabilities import KNOWN_CAPABILITIES, Capability  # noqa: F401
from .contract import (  # noqa: F401
    API_VERSION,
    CONTRACT_NAME,
    RESULT_SCHEMA,
    check_compatible,
    deprecated,
)
from .errors import (  # noqa: F401
    ConfigurationError,
    ContractMismatch,
    DriverError,
    DriverNotFound,
    DriverTimeout,
    ProtocolError,
    TransportError,
    UnsupportedCapability,
)
from .models import DeviceConfig  # noqa: F401
from .base import InstrumentDriver, PassiveDriver  # noqa: F401
from .family.scope import (  # noqa: F401
    ChannelSetup,
    Measurement,
    ScopeDriver,
    TimebaseSetup,
    Waveform,
)
from .transport import (  # noqa: F401
    SerialTransport, SimulateTransport, SocketTransport, Transport, VisaTransport,
    make_transport, set_serial_factory, set_visa_factory, transport_class, visa_available,
    visa_factory,
)
from .endpoint import (  # noqa: F401
    AUTO, BACKEND_CHOICES, DEFAULT_BACKEND, NATIVE, SERIAL_DEFAULTS, SUPPORTED_BACKENDS,
    SUPPORTED_INTERFACES, VISA, Endpoint, backends_of, endpoint_from, interfaces_of,
    normalize_backend, normalize_interface, resolve_backend, visa_resource_for,
)
from .api import Scope  # noqa: F401
from .factory import driver_class, entries, models, open_scope, self_check  # noqa: F401

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
    "driver_class",
    "backends_of",
    "endpoint_from",
    "entries",
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
    "models",
    "open_scope",
    "self_check",
]
