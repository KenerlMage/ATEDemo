# -*- coding: utf-8 -*-
"""L0 · 契约内核（平台与驱动包之间的稳定底座）。

这一层**不认识任何品牌**：没有型号名、没有仪器命令字、没有厂商 SDK。
它只提供四类东西——错误体系、能力与版本契约、连接方式与端点解析、传输链路。

任何驱动、任何家族、任何厂商包都依赖它，它也**不依赖**上层。
"""

from .capabilities import KNOWN_CAPABILITIES, Capability, is_known, matches, validate  # noqa: F401
from .contract import (  # noqa: F401
    API_VERSION,
    CONTRACT_NAME,
    MANIFEST_SCHEMA,
    RESULT_SCHEMA,
    WAVEFORM_SCHEMA,
    check_compatible,
    deprecated,
    describe_contract,
)
from .endpoint import (  # noqa: F401
    FAMILY_INTERFACES,
    LAN,
    SERIAL,
    SUPPORTED_BACKENDS,
    SUPPORTED_INTERFACES,
    Endpoint,
    backends_of,
    endpoint_from,
    interfaces_for,
    interfaces_of,
    normalize_backend,
    normalize_interface,
    normalize_interface_kind,
    resolve_backend,
    visa_resource_for,
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
from .transport import (  # noqa: F401
    SerialTransport,
    SimulateTransport,
    SocketTransport,
    Transport,
    VisaTransport,
    make_transport,
    transport_class,
    visa_available,
)
