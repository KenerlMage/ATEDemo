# ATE Runner 仪器驱动库 · 设计说明

> 适用版本：`ate_drivers` 1.2.0 · 契约 `ate.driver.api` **1.2** · 门禁 19 项
> 读者：平台开发人员、驱动作者、测试系统集成者
> 本文回答「这套库为什么长这样」；「怎么用」见《使用手册》，「写新驱动时要守什么」见《设计准则》，「新设备家族怎么落地」见《新设备家族接入指南》。

## 1. 定位与范围

这套库要解决的是**测试程序（TPS）与仪器之间那一层**的问题：一台设备换个品牌、换条链路、换份固件，测试用例不该跟着改。

它提供三样东西：

| 提供 | 说明 |
| --- | --- |
| **契约层** | 动作名、参数名、单位、返回结构、错误码。上层按契约写代码，不按仪器写代码 |
| **家族层** | 一类设备的通用语义。示波器家族把「设时基 / 设通道 / 采波形 / 测量」定义成固定动作与算法 |
| **型号注册表** | 设备档案里的 `model` 字段 → 精确派发到某个驱动类；顺带把「这台设备支持哪些链路」也登记进去 |

它**不**负责这些（别往里塞）：

- 不做设备发现（不扫网段、不枚举 COM 口）——设备档案是唯一事实源；
- 不做业务判定（测合格不合格是 TPS 的事）；
- 不做 UI（工具卡片、报告渲染在平台侧）；
- 不碰数据库（平台侧把档案行交给它，它只读字段）。

## 2. 六层结构与依赖方向

依赖**单向向下**：`api/` → `assembly/` → `families/` → (`base.py`) → `core/`。谁都不许反向 import；厂商包只许依赖家族层、基类与内核，以及它自己的厂商 SDK。

```
                     ┌──────────────────────────────────────────┐
   用户/TPS 用例  →  │ api/            Scope · Camera 门面        │  只暴露业务方法
                     │   instruments.py · camera.py              │
                     ├──────────────────────────────────────────┤
   平台侧接入     →  │ assembly/       型号注册表 · 相机派发       │  按型号精确派发
                     │   registry.py · cameras.py                │
                     ├──────────────────────────────────────────┤
   每类设备一份   →  │ families/       家族契约                   │  动作/单位/算法/仿真基线
                     │   scope.py · camera.py · generic/         │
                     ├──────────────────────────────────────────┤
   每个品牌一份   →  │ vendors/        厂商驱动包（含厂商 SDK 适配）│  只填差异面
                     ├──────────────────────────────────────────┤
   驱动基类       →  │ base.py         会话 · 分发 · 能力         │  厂商链路工厂钩子
                     ├──────────────────────────────────────────┤
   契约内核       →  │ core/           errors · contract ·       │  不认识任何品牌
                     │   capabilities · endpoint · models ·      │  不依赖上层
                     │   transport                               │
                     └──────────────────────────────────────────┘
```

各层职责与「不许做的事」：

| 层 | 目录 / 文件 | 职责 | 不许做 |
| --- | --- | --- | --- |
| 门面 | `api/instruments.py` · `api/camera.py` | 校验参数、挑模式（真机/仿真）、核对型号、把结果整理成用户友好的结构 | 不许出现任何仪器命令字样 |
| 装配 | `assembly/scopes.py` · `assembly/cameras.py` · `assembly/devices.py` | 型号归一化、登记、精确派发、注册表自检、`open_scope()` / `open_camera()`；`devices` 负责别名 → 档案 → 句柄 | 不许打分、不许模糊匹配、不许兜底驱动；设备簿不连数据库 |
| 家族 | `families/<家族>.py` · `families/generic/` | 动作定义、参数单位、测量算法、仿真基线、命令表契约、安全退出 | 不许出现具体品牌的命令拼写；`generic/` 只收「动作 ≤6 且无需特化」的简化家族 |
| 厂商包 | `vendors/<厂商>/` | 命令表、应答前导解析、量纲缩放、枚举映射、仿真模型；**厂商 SDK 只在这里出现** | 不许改动作名、不许改返回结构、不许 import 其它厂商包 |
| 驱动基类 | `base.py` | 会话生命周期、动作分发、能力校验、`TRANSPORT_FACTORY` 链路工厂钩子 | 不许依赖家族及以上任何东西 |
| 契约内核 | `core/` | 错误体系、能力与版本契约、连接方式与端点、数据模型、传输链路（socket / 串口 / VISA / 仿真） | 不许出现品牌名、型号名、仪器命令字 |
| 工具链 | `kit/` | 脚手架、清单校验、19 项门禁、CLI | 不进运行时依赖（生产环境可以不装） |

## 3. 数据模型

### 3.1 `DeviceConfig` —— 设备档案在库里的投影

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `bench_id` / `device_id` / `name` | `""` | 台位、设备编号、显示名 |
| `model` | `""` | **派发依据**，唯一不能省 |
| `vendor` / `category` / `role` | `""` | 展示与报表用，不参与派发 |
| `interface` | `"NONE"` | `LAN` / `SERIAL` / `NONE`（无链路） |
| `host` / `port` | `""` / `None` | 网口端点，必须成对 |
| `serial_port` / `baudrate` | `""` / `None` | 串口端点 |
| `address` / `channel` / `protocol` | `""` | 老式总线地址、通道、协议备注 |
| `programmable` / `required` / `configured` | `False` / `True` / `False` | 是否可编程、是否必需、连接参数是否已配齐 |
| `timeout` | `2.0` | 单次读写超时（秒） |
| `note` / `extra` | `""` / `{}` | 备注与未登记字段（保留原始档案信息） |

配套属性：`backend`（档案 `extra.backend`，未写即 `native`）/ `visa_resource`（规范 VISA 资源名）。配套方法：`from_dict()` / `from_row()`（吃数据库行或 dict）/ `describe()` / `is_net()` / `is_serial()` / `transport_kind()`（`socket` / `serial` / `none`）/ `resource`（VISA 风格资源名）。

### 3.2 `Endpoint` —— 解析后的连接方式

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `kind` | `""` | `LAN` / `SERIAL` |
| `host` / `port` / `protocol` | `""` / `None` / `""` | 网口 |
| `port_name` / `baudrate` / `bytesize` / `parity` / `stopbits` | `""` / `None` / `8` / `"N"` / `1` | 串口 |
| `device_id` / `timeout` / `extra` | `""` / `2.0` / `{}` | 归属与超时 |

`describe()` 产出人话标签：网口 `192.168.10.41:4000`，串口 `COM6@115200,8N1`；`visa_resource` 产出规范 VISA 资源名（`TCPIP0::192.168.10.41::4000::SOCKET` / `ASRL6::INSTR`），只有 `backend="visa"` 时才参与连接。

### 3.3 家族数据模型（示波器）

| 模型 | 字段 | 单位约定 |
| --- | --- | --- |
| `TimebaseSetup` | `scale_s_per_div`、`divisions=10`、`offset_s=0.0` | 秒/格、秒 |
| `ChannelSetup` | `name`、`enabled`、`scale_v_per_div=0.5`、`coupling`、`offset_v`、`probe_ratio` | **伏**/格、伏 |
| `Waveform` | `channel`、`samples`、`x_incr_s`、`x_start_s=0.0`、`unit="V"`、`source` | 采样值恒为**伏**，时间恒为**秒** |
| `Measurement` | `kind`、`value`、`unit`、`quality="good"` | 每项测量自带单位与质量 |

**铁律**：数据模型里的量纲是**物理单位**，不是「仪器当前档位的格数」「ADC 计数」。缩放发生在厂商包内部，出口必须是伏 / 秒 / 赫兹。

## 4. 返回协议

返回结构分两种粒度，**不要混用**：

**① 门面（`Scope`）——给人看的精简结构**

| 方法类型 | 返回 |
| --- | --- |
| 动作类（`auto_setup` / `set_timebase` / `set_channel` / `configure`） | `{ok, action, simulated, value, source}` |
| 采集类（`capture`） | `{ok, simulated, channel, points, volts, seconds_per_sample, first_sample_seconds, unit, source}` |
| 测量类（`measure`） | `{KIND: {value, unit, quality, source}, ...}` |
| 状态类（`status` / `metadata` / `identity`） | 会话与身份明细（含 `mode` / `interface_kind` / `resource` / `endpoint_detail`） |
| 自检（`self_test`） | `{ok, model, vendor, endpoint, simulated, checks[], identity{}}`，`checks` 是逐项清单 |
| 关闭（`close`） | `{ok, device_id, commands}`（`commands` = 本次会话发出的命令条数） |

**② 契约层（`driver.do(action, **params)`）——给平台与工具看的完整结构，固定 16 键**

`ok` · `schema` · `device_id` · `alias` · `driver` · `driver_version` · `api` · `action` · `requested` · `value` · `detail` · `quality` · `source` · `simulated` · `elapsed_ms` · `warnings`

真实样例（`do("identify")`）：

```json
{"ok": true, "schema": "ate.driver.result.v1", "device_id": "", "alias": "",
 "driver": "tektronix-mso5", "driver_version": "1.5.0", "api": "1.2",
 "action": "identify", "requested": "identify",
 "value": "TEKTRONIX,MSO54,SIM0001,1.2.3", "detail": {}, "quality": "simulated",
 "source": "simulate", "simulated": true, "elapsed_ms": 0, "warnings": []}
```

波形动作的 `value` 是一个可序列化的波形对象：`{schema, channel, samples, x_incr_s, x_start_s, unit, source}`——`schema` 为 `ate.driver.waveform.v1`。

## 5. 错误模型

八类异常，九个错误码。**上层只许按 `code` 分支，不许解析中文消息文本**（文案会改，码不会）。

| 异常类 | 码 | 触发场景 | 处置建议 |
| --- | --- | --- | --- |
| `DriverError` | `E_DRIVER` | 基类；参数越界等通用失败 | 修参数或报缺陷 |
| `ConfigurationError` | `E_CONFIG` | 连接参数不完整/非法（缺 IP、缺端口、缺串口名、波特率非法、链路歧义、型号不符） | **不重试**，改档案或接线 |
| `DriverNotFound` | `E_NOT_FOUND` | 型号未登记、驱动包缺失 | **不重试**，补登记或装包 |
| `TransportError` | `E_TRANSPORT` | 连不上、写失败、串口打不开 | 查线/查电源，可重试一次 |
| `DriverTimeout` | `E_TIMEOUT` | 读写超时（继承自 `TransportError`） | 可重试；仍失败则查链路 |
| `ProtocolError` | `E_PROTOCOL` | 应答无法解析、字段缺失 | 查指令方言/固件版本 |
| `UnsupportedCapability` | `E_UNSUPPORTED` | 调了驱动未声明的能力 | 先 `supports()` 判断 |
| `ContractMismatch` | `E_CONTRACT` | 驱动契约版本与平台不兼容 | 升级驱动或平台 |
| （`DriverError` 带 `code="E_PARAM"`） | `E_PARAM` | 参数越界或枚举未登记（如耦合写了仪器不支持的值） | 改参数；**库不会静默替换** |

## 6. 型号派发

```
档案 model 字段
   ↓ normalize_model()        只留字母数字、转大写：mso-54 / MSO 54 / mso54 → MSO54
   ↓ 注册表精确查表            MSO54 → tektronix-mso5 → TekMsoScope
   ↓ 别名表（显式登记）        MXO4 → MXO44
   ↓ 命中 → 驱动类；未命中 → DriverNotFound（列出全部已登记型号）
```

- **没有权重打分**：旧版的 `model 3.0 / vendor 2.0 / category 2.0 / role 1.5 / interface 1.2 / name 1.0` 六项打分与「同分比 priority」「最后兜底 generic-scope」全部删除；
- **没有模糊匹配**：不做子串/前缀/相似度；
- **同名冲突**报出来（`model_conflicts()`），不自动挑；
- 注册表自检 `self_check()` 现返回 `{"ok": true, "models": 5, "aliases": 1, "problems": []}`；
- 现已登记 5 个型号：`MSO54 / MSO56 / MSO58 / MSO64`（泰克 MSO5/MSO6 系列）+ `MXO44`（R&S MXO 4 系列，别名 `MXO4`）。

## 7. 连接方式与传输后端

一台设备网口与串口都能接时，解析顺序固定五步（`endpoint_from()`）：

| 步 | 依据 | 失败行为 |
| --- | --- | --- |
| 1 | 显式参数 `interface=` | 值不在 `{LAN, SERIAL}` → `E_CONFIG` |
| 2 | 档案 `interface` 字段（支持 `NET` / `TCPIP` / `ETHERNET` / `SOCKET` / `COM` / `RS232` / `RS485` / `UART` / `ASRL` 等别名归一化；`NONE` / `PASSIVE` / `NA` / `-` = 无链路） | 非法值 → `E_CONFIG` |
| 3 | 没声明时看端口填充 | 网口参数齐 → LAN；串口名有 → SERIAL；**两者都填 → `E_CONFIG`「连接方式不明确」** |
| 4 | 端点参数校验 | host/port 不成对、串口名缺失、波特率非法 → `E_CONFIG` |
| 5 | 型号声明的 `INTERFACES` | 型号不支持该链路 → `E_CONFIG`（并列出支持哪些） |

链路实现（`kind` × `backend` 的组合，全在 `transport.py` 一处收口）：

| --- | --- | --- | --- |
| 仿真 | `SimulateTransport` | 无 | 与后端无关；没有硬件也能跑全流程 |
| 网口 LAN · `native` | `SocketTransport` | 标准库 socket | `192.168.10.41:4000`（raw socket） |
| 串口 SERIAL · `native` | `SerialTransport` | pyserial，**惰性导入** | `COM6@115200,8N1`（帧参数给 pyserial） |
| 网口 / 串口 · `visa` | `VisaTransport` | pyvisa，**惰性导入** | `TCPIP0::192.168.10.41::4000::SOCKET` / `ASRL6::INSTR` |

两条链路 + 两套栈是**正交**的：`kind` 回答"物理怎么接"（给 `host/port` 还是 `serial_port/baudrate`），
`backend` 回答"用哪套客户端栈收发"。同一台设备四种组合都能跑，业务代码与返回结构完全不变。

**模式语义**：`mode="auto"`（默认）——给了完整端点走真机，什么都不给走仿真；`mode="real"` 没有端点直接 `E_CONFIG`（不给「半残端点悄悄降级成仿真」留缝）；`mode="simulate"` 强制仿真。连接后默认回读 `*IDN?` 与档案型号比对，不符报 `E_CONFIG`（`verify_model=False` 可关）。

### 7.1 传输后端：原生栈还是 PyVISA

| 后端 | 取值 | 网口 | 串口 | 依赖 |
| --- | --- | --- | --- | --- |
| 原生栈 | `native`（默认） | 标准库 socket | pyserial，帧参数直接给 `Serial` | 零硬依赖 |
| PyVISA | `visa` | pyvisa 打开 `TCPIP0::host::port::SOCKET` | pyvisa 打开 `ASRL6::INSTR`，波特率/帧格式走**会话属性** | pyvisa（NI-VISA / Keysight VISA / pyvisa-py 任一实现） |
| 自动 | `auto`（仅调用时可写） | 装了 pyvisa 走 VISA，否则走原生 | 同左 | — |

解析顺序（`endpoint.resolve_backend()`）：**显式 `backend=` > 档案 `extra.backend` > 默认 `native`**。

* 认不出的写法（`usb-tmc`）→ `E_CONFIG`，消息里列出可选项，**不猜也不静默退回原生**；
* 显式 `visa` 但环境没装 pyvisa（真机模式）→ `E_CONFIG`，`detail` 给出 `pip install pyvisa pyvisa-py`；
* 型号声明的 `BACKENDS` 不含所请求后端 → `E_CONFIG`，消息里列出该型号声明的后端；
* `auto` 是**调用时的选择**，不是可声明的能力：驱动 `BACKENDS` 里写 `auto` 会被门禁 C19 挡下；
* **实际生效的后端永远可查**：`scope.backend` / `status()["backend"]` / `metadata()` / `backend_detail()`，
  会话打开时每条结果都带 `transport` 与 `backend`，日志里不会出现"以为走的是 VISA"。

串口帧参数（`baud_rate` / `data_bits` / `parity` / `stop_bits`）**不写进资源名**——
`ASRL::COM6::115200::INSTR` 那种拼法不是 VISA 规范资源名；规范写法是 `ASRL6::INSTR`，
波特率等作为会话属性下发（`_apply_visa_attributes()` 逐项 setattr，会话不认的属性跳过：网口没有 `baud_rate`）。

### 7.1 厂商 SDK 落在 L3，不做第三类传输后端

`native` / `visa` 是**平台提供的传输后端**，对所有驱动生效；而厂商官方库
（R&S 的 `RsInstrument`、Basler 的 `pypylon`）是**某一家的事情**，不能登记成后端——
否则 `transport.py` 就得认识品牌，所有驱动都被某一家的包绑架。

做法是 `base.py` 留一个链路工厂钩子：

```python
class InstrumentDriver:
    TRANSPORT_FACTORY: Any = None      # 留空 → make_transport（原生 / VISA / 仿真）
```

厂商包在自己的 `driver.py` 里挂上去：

```python
# vendors/rohde_schwarz_mxo/driver.py —— L3
TRANSPORT_FACTORY = staticmethod(rs_transport)
```

R&S 的实现（`vendors/rohde_schwarz_mxo/rs_link.py`）：

* 真机 + 装了 `RsInstrument` → `RsInstrumentLink`（`kind="rsinstrument"`）；
* 仿真 → 仍是 `SimulateTransport`（仿真不是链路，不该被厂商库替代）；
* 档案 `extra.rsinstrument = off` → 回落 `SocketTransport` / `SerialTransport`。

三条必须守住：

1. `core/transport.py` **不 import** `RsInstrument`，缺它时整库照常可用；
2. 声明要走官方库却打不开时**直接报错**（`E_CONFIG` / `TransportError`），不静默换链路；
3. 官方库缺 VISA 运行时的失败信息要带运维指引（R&S VISA 未装 → 设 `extra.rsinstrument=off`）。

> `RsInstrument` 已收录进 `kit/conformance.py` 的 `OPTIONAL_RUNTIME`，L3 顶层 import 它
> 不判"偷偷引入第三方依赖"；实现上仍用函数内惰性 import，缺包时由驱动降级。

## 8. 设备簿与别名（多台同型号）

一台台架挂两台同型号设备是常态：两块 MSO54、两台同型号相机。**型号派发只能回答
"用哪个驱动"，回答不了"哪一台"**。所以型号之外还得有一层别名。

三种身份，各管一件事：

| 概念 | 存在哪 | 作用 |
| --- | --- | --- |
| 别名 | `device_registry.device_id` | 现场叫法，用例里唯一的凭据 |
| 型号 | `device_registry.model` | 派发到哪个驱动类 |
| 连接参数 | `host` / `port` / `serial_port` / `interface` …… | 怎么连上这一台 |

别名与连接参数**一起存在数据库**，由后端 / Runner 读出来灌进 SDK；SDK 自己
不连库、不读配置文件路径。

```python
from ate_drivers import connect, load_devices

load_devices(rows)          # rows 来自 device_registry：dict / sqlite3.Row / dataclass 都收

def test_rise_time():
    scope = connect("scope_main")     # 别名 → 档案 → 句柄
    aux = connect("scope_aux")        # 同型号的另一台，各连各的端口
    ...
```

约定：

* 同一别名重复 `connect` 返回**同一个句柄**；`disconnect(alias)` 之后才重建。
  换机、掉线重连用 `connect(alias, refresh=True)`。
* `name` 与 `device_id` 不同时，`name` 也当别名（现场常直接说"主示波器"）。
* 别名跨台架重名时用 `connect(alias, bench_id="...")` 指定；不指定就报错，不猜。
* 家族由 `category` / `role` / `extra.device_type` 判定；新增设备类型只需
  `register_family("psu", opener)`，设备簿不认识具体家族。
* 未登记别名 → `DriverNotFound`，提示里会列出当前可用别名。

命名一律对称，方便照着抄：

| 家族 | 文件 | 注册 | 派发类 | 打开 |
| --- | --- | --- | --- | --- |
| 示波器 | `assembly/scopes.py` | `register_scope_model` / `register_scope_driver` | `scope_class` | `open_scope` |
| 相机 | `assembly/cameras.py` | `register_camera_model` / `register_camera_driver` | `camera_class` | `open_camera` |
| 设备簿 | `assembly/devices.py` | `register_device` / `register_family` | — | `connect` |

`scope_models()` / `camera_models()`、`scope_entries()` / `camera_entries()`、
`scope_self_check()` / `camera_self_check()` 全部同名同义。以后加电源、电子负载、
运动台，照抄一份 `xxx.py` 就是完整的一套。

## 9. 版本与兼容

| 常量 | 值 | 作用 |
| --- | --- | --- |
| `API_VERSION` | `1.2` | 契约版本；驱动清单里的 `api` 与之比对 |
| `CONTRACT_NAME` | `ate.driver.api` | 契约名 |
| `MANIFEST_SCHEMA` | `ate.driver.manifest.v1` | 驱动清单（`driver.json`）结构 |
| `RESULT_SCHEMA` | `ate.driver.result.v1` | 单动作返回结构 |
| `WAVEFORM_SCHEMA` | `ate.driver.waveform.v1` | 波形对象结构 |
| `FROZEN_ACTIONS` | `{"scope.get_waveform": "scope.acquire_waveform"}` | 冻结的旧动作仍然可用 |
| `DEPRECATIONS` | `{}`（当前为空） | 已弃用项 → 替代项 |

兼容规则：**动作只增不改**；改语义必须先加新动作并把旧的写进 `DEPRECATIONS`；`min_platform` 声明平台最低版本，`check_compatible()` 在加载阶段就拒绝不兼容的包（报 `E_CONTRACT`）。

## 10. 上架门禁（19 项）

`python -m ate_drivers.kit.cli check <驱动包>` 会逐项跑下面 19 条。**门禁不绿，不准上架。**

| 号 | 检查 | 号 | 检查 |
| --- | --- | --- | --- |
| C01 | 类元数据完整 | C10 | 会话可重入 |
| C02 | 契约版本兼容 | C11 | 安全退出 |
| C03 | 能力声明合法 | C12 | 仿真可复现 |
| C04 | 能力均有实现 | C13 | 实例互不干扰 |
| C05 | 命令表完整 | C14 | 旧动作别名有效 |
| C06 | 仿真可执行全部能力 | C15 | 零第三方依赖（可选依赖须惰性导入） |
| C07 | 返回结构可序列化 | C16 | 弃用项有替代 |
| C08 | 测量项带单位 | C17 | 型号可派发 |
| C09 | 错误可分类 | C18 | 连接方式可声明 |
| C19 | 传输后端可声明（`native` / `visa`） | | |

## 11. 与平台侧的关系

平台侧（`D:\ATE\backend\drivers/`）是**另一套实现**，两者通过「型号 + 清单」对齐：

| 环节 | 平台侧现状 | SDK 侧 |
| --- | --- | --- |
| 规格来源 | 内置 `DRIVER_SPECS` 8 条 | 内置 `vendors/` + 目录扫描 `driver.json` |
| 选型 | **仍按 `match` 权重打分兜底** | **已删除打分**，型号精确派发 |
| 合并方式 | `merge_specs(builtin_specs, manifests)` | `resolve_spec(model, specs)` |

> 说明：平台侧仍是打分版，属既定待办（见《设计准则》末节「迁移与统一」）。SDK 侧的口径是目标口径：**型号直连派发、命令不外露、网口与串口平权**。

平台侧接入一段最小代码即可对齐：

```python
from ate_drivers.kit.registry import merge_specs, resolve_spec
from ate_drivers.kit.cli import scan_dir

specs = merge_specs(platform_builtin_specs, scan_dir("<ATE>/drivers"))
spec = resolve_spec(device_model, specs)      # 未登记 → 抛错，不猜
driver_cls = load_entry(spec["entry"], spec["_base_dir"])
```

## 12. 设计取舍

| 取舍 | 选择了 | 代价 / 换来什么 |
| --- | --- | --- |
| 打分 vs 精确派发 | **精确派发** | 未登记型号必须人工登记；换来「不会悄悄套用相似驱动去问仪器」 |
| 门面暴露命令 vs 完全封装 | **完全封装** | 排障时不能直接敲命令；换来 TPS 用例与品牌解耦（单测扫描 `api.py` 无命令字样） |
| 依赖 pyserial / pyvisa vs 零依赖 | **零硬依赖 + 惰性导入 + 注入点** | 串口与 VISA 各要多装一个包；换来离线工控机也能装、测试可用假串口/假 VISA 会话 |
| VISA 默认开 vs 默认关 | **默认原生栈，`visa` 显式开（`auto` 才自动挑）** | 想全现场统一走 NI-VISA 要多写一个字段；换来「行为只取决于档案」，不会因为某台机器装了 VISA 就悄悄换栈 |
| 后端放进返回结构 vs 只放进状态 | **只进 `status()` / `metadata()`**，契约层 16 键不动 | 单看一次动作的返回看不出后端；换来契约结构与既有报告零改动（契约只增不改） |
| 静默降级 vs 直接报错 | **直接报错** | 现场必须把参数填对；换来「接错线不会被当成仿真」 |
| 家族层做多少 | **只做通用语义与算法**，命令拼写全下放 | 家族层不能替厂商包「顺手兼容」；换来厂商包边界清晰 |
| 仿真是不是一等公民 | **是一等公民** | 需要维护仿真模型；换来无硬件可开发、可回归、可复现（C12） |
