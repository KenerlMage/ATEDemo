# -*- coding: utf-8 -*-
"""厂商驱动包（一厂一包，包内只填「差异面」）

1. `COMMANDS`   命令表（同一动作在各家的命令串）
2. 前导解析     泰克是按名取值的长前导；R&S 是 4 值短头 + ASCII 直出伏特
3. 通道寻址     泰克 `CH1` 令牌；R&S `CHANnel1` 数字后缀
4. 枚举映射     测量类型、耦合方式等取值表
5. 仿真模型     自家前端特性（量化、带宽限制）

每个驱动还必须声明三件与**型号派发**有关的元数据：

* `MODELS`    —— 本驱动支持的具体型号，是型号派发的唯一依据（门禁 C17 会校验它
                与同目录 `driver.json` 的 `models` 一致）；
* `INTERFACE` —— 连接方式，本项目示波器一律为 `"LAN"`（网口）；
* `vendor`    —— 厂商名，用于设备档案与界面展示。

动作名、参数名、单位、返回结构、测量算法全部由家族层 `ScopeDriver` 提供，
用户代码只接触 `ate_drivers.api.Scope`（通用顶层方法），**看不到任何原始命令**。

`DRIVERS` 由 `ate_drivers.factory` 在导入时读走，用于自动登记型号。
"""

from .rohde_schwarz_mxo.driver import RsMxoScope
from .tektronix_mso.driver import TekMsoScope

DRIVERS = (TekMsoScope, RsMxoScope)

__all__ = ["DRIVERS", "RsMxoScope", "TekMsoScope"]
