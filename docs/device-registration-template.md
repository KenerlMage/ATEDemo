# ATE Runner · 设备注册模版说明

设备注册模版（JSONC）以注册库 `backend/testresource/testbenches.json` 的 `testbenches[].devices[]` 一条记录为基准；新建设备由「测试台注册 → 类型选型」把预设 BOM（`testbench_presets.json`）预填，现场只改连接参数。

- 模版文件：`docs/ate-device-template.jsonc`（`//` 为字段备注，去掉注释即为合法 JSON）
- 校验依据：`backend/testbench_registry.py`、`backend/drivers/factory.py`
- 字段总数：18（与注册库实际记录字段一一对应，无缺无多）

## 最小可用条目

四个字段就能登记一台非程控设备：`id`（别名）、`name`（显示名）、`programmable: false`、`interface: "NONE"`；程控设备在此基础上必须补齐连接参数。

## 字段参考

### ① 标识与识别

决定这台设备在平台里叫什么、被谁引用。

| 字段 | 类型 | 必填 | 作用 | 示例 |
| --- | --- | --- | --- | --- |
| `id` | string | 必填 | 设备别名，同一测试台内唯一。TPS 的 device_config 用它引用设备（device_id） | `rh-scope` |
| `name` | string | 必填 | 中文显示名，出现在装备树、装备助手工具卡片与测试报告中；参与驱动匹配（权重 1.0） | `数字示波器` |
| `model` | string | 必填 | 型号全称。驱动自动匹配的最高权重字段（3.0），写错会挂到错误驱动上 | `Tektronix MSO54` |
| `vendor` | string | 必填 | 厂商名，驱动匹配权重 2.0 | `Tektronix` |
| `category` | string | 必填 | 设备类别（示波器 / 万用表 / 电源 / 电子负载 / 信号源 / 运动控制 / 数据采集 / 被动设备…），驱动匹配权重 2.0 | `示波器` |
| `role` | string | 必填 | 这台设备在本测试台上的用途，驱动匹配权重 1.5；同款设备用途不同时靠它区分 | `读头回放信号波形 / 边沿与噪声观测` |

### ② 程控与接口

决定平台怎么连它、能不能程控。

| 字段 | 类型 | 必填 | 作用 | 示例 |
| --- | --- | --- | --- | --- |
| `programmable` | bool | 必填 | 是否可编程。false = 被动设备（夹具、探头、工装），只登记、不参与通讯自检 | `true` |
| `interface` | enum | 必填 | 接口类型，决定「配置完整」的判定方式与自检手段，取值不区分大小写、入库统一转大写 | `LAN` |

### ③ 连接参数

注册之后唯一允许修改的一组字段（型号、厂商、类别、用途不允许改）。

| 字段 | 类型 | 必填 | 作用 | 示例 |
| --- | --- | --- | --- | --- |
| `host` | string | 网络类必填 | 设备 IP 或主机名。建议为仪器分配固定 IP（DHCP 保留），否则换网后注册库里的地址会失效 | `192.168.10.41` |
| `port` | int | 网络类必填 | 端口号 1–65535。常见值 4000=VXI-11、5025=SCPI raw socket、3490=Fluke 万用表、30000=ITECH 电源/负载 | `4000` |
| `serial_port` | string | 串口类必填 | 串口号，如 COM6。Windows 下换 USB 口会变号，接入前确认设备管理器里的实际编号 | `COM6` |
| `baudrate` | int | 可选 | 波特率，如 115200。留空时平台按设备默认值尝试 | `115200` |
| `address` | string | USB / GPIB 必填 | VISA 资源串。网络设备可填（便于 VISA 通道复用），USB / GPIB 设备必须填，NONE 类留空 | `TCPIP0::192.168.10.41::inst0::INSTR` |
| `channel` | string | 可选 | 默认通道 / 端口号。多路电源、开关单元用它标明默认通道 | `CH1` |
| `protocol` | string | 可选 | 协议说明，写给人看，不参与自检判定 | `VXI-11 (SCPI)` |

### ④ 管理与约束

决定注册自检会不会被拦下。

| 字段 | 类型 | 必填 | 作用 | 示例 |
| --- | --- | --- | --- | --- |
| `required` | bool | 必填 | 是否必备设备。必备且 programmable 的设备若未配置完整，注册自检报 fail 并阻断注册 | `true` |
| `note` | string | 可选 | 备注。建议写固定 IP 要求、量程 / 带宽、校准周期、负责人等排障时要看的信息 | `8 通道；建议固定 IP` |

### ⑤ 系统回写

由平台计算，请勿手工填写。

| 字段 | 类型 | 必填 | 作用 | 示例 |
| --- | --- | --- | --- | --- |
| `configured` | bool | 只读 | 平台按 interface 判定配置是否完整：网络类看 host + port，串口类看 serial_port，其余看 address | `true` |

## 接口类型与配置完整判定

| interface 取值 | 类别 | 必填连接字段 | 配置完整判定 | 自检方式 |
| --- | --- | --- | --- | --- |
| `LAN / ETHERNET / TCP / IP` | 网络类 | host + port | host 非空 且 port 有值 | TCP 探测（真实模式）；仿真模式记模拟自检 |
| `SERIAL / RS232 / RS485 / UART / COM` | 串口类 | serial_port | serial_port 非空 | 串口探测（真实模式） |
| `USB / GPIB / MANUAL / NONE` | 不自动探测 | address（其余留空） | address 非空 | 记 skip，需人工确认连通性 |

## 注册后可改 / 只读

- **可改（连接参数）**：`host`、`port`、`protocol`、`serial_port`、`baudrate`、`address`、`channel`、`note`，另可「恢复预设默认值」（`reset_defaults`）。
- **只读（BOM 元信息）**：`id`、`name`、`model`、`vendor`、`category`、`role`、`programmable`、`interface`、`required`——来自预设类型，不允许通过属性配置接口改动。

## 驱动自动匹配规则

平台按字段关键字打分（`model` 3.0 / `vendor` 2.0 / `category` 2.0 / `role` 1.5 / `interface` 1.2 / `name` 1.0），取最高分驱动；全部未命中时回落到 `generic-scpi` 兜底。因此**型号、厂商、类别、用途四个字段要填准**。

| 优先级 | 驱动 key | 覆盖设备 | 匹配关键字 |
| --- | --- | --- | --- |
| 20 | `tektronix-mso5` | 示波器 | model: mso5 / mso54 / mso64 / dpo5 / tektronix；vendor: tektronix |
| 14 | `serial-motion` | 运动控制器 | category: 运动控制 / motion / 转速 / spin；interface: SERIAL / RS232 / RS485 / UART / COM |
| 12 | `generic-dmm` | 数字万用表 | category: 万用表 / dmm / multimeter；role: dmm / 万用表 |
| 12 | `generic-psu` | 可编程直流电源 | category: 电源 / psu / power supply；role: psu / 电源 |
| 12 | `generic-eload` | 可编程电子负载 | category: 电子负载 / eload / load；role: eload / 负载 |
| 12 | `generic-awg` | 波形发生器 / 信号源 | category: 信号源 / 波形 / awg / generator；role: awg / 信号源 |
| 5 | `passive-device` | 被动设备（无通讯） | interface: NONE / MANUAL / USB / GPIB |
| 1 | `generic-scpi` | 通用 SCPI 仪器（兜底） | match 为空：上面全部未命中时回落至此 |

## 预设 BOM 与注册库的字段映射

| 预设 BOM（`testbench_presets.json`） | 注册库（`testbenches.json`） | 说明 |
| --- | --- | --- |
| `id / name / model / vendor / category / role` | `同名直传` | BOM 元信息，注册后不可改 |
| `default_host` | `host` | 预填默认 IP，现场按实际改 |
| `default_port` | `port` | 预填默认端口 |
| `default_serial_port` | `serial_port` | 预填默认串口号 |
| `default_baudrate` | `baudrate` | 预填默认波特率 |
| `default_address` | `address` | 预填 VISA 资源串 |
| `default_channel` | `channel` | 预填默认通道 |
| `—（预设 BOM 中不存在）` | `configured` | 注册时由平台按接口类型计算 |

## 常见的五类错误

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 注册自检 fail：必备设备未配置 | programmable=true 且 required=true，但按接口类型所需字段为空 | 网络类补 host + port，串口类补 serial_port；其余补 address |
| 注册自检 fail：设备地址冲突 | 同一测试台内两台设备用了相同的 IP + 端口 | 改端口或换设备 IP；设备属性配置环节也会二次拦截 |
| 设备拿不到工具卡片 / 控制不了 | 驱动自动匹配落到 generic-scpi 兜底，或挂到邻近驱动上 | 检查 model / vendor / category 的拼写与关键字是否命中驱动规格表 |
| 串口类设备时通时不通 | USB 转串口换插口后 COMn 变化 | 固定 USB 插口，或改用串口服务器并固定端口号 |
| 换网络环境后连不上 | 仪器使用 DHCP 动态地址，注册库里的 host 已失效 | 为仪器配置 DHCP 保留或静态 IP，再到设备属性配置里更新 host |

## 与 TPS 的衔接

TPS 侧不重复登记设备，只用别名引用：`device_config` 里每个功能槽写 `device_id`（即设备注册的 `id`）、`role`、`note`；运行前平台拿它和注册库设备清单交叉核对，缺设备或契约版本不匹配会阻断并给出补齐清单。设备注册的 `id` 一旦改名，所有引用它的 TPS 都要同步改。

## 字段保留策略

结论：**存储层保留全部 18 个字段**（与接口无关的留空占位），**表单 / 校验 / 提交层按 interface 只处理相关字段**；两侧都不裁剪存储结构。

注册库里 LAN / SERIAL / NONE 三类设备的实际记录都是同一组 18 个键：网口示波器带 `"serial_port": ""`、`"baudrate": null`，串口转速盒带 `"host": ""`、`"port": null`；
而前端 `payload()` 只提交本接口分支的键（网口交 host/port/protocol，串口交 serial_port/baudrate/protocol，其余交 address/channel/protocol，外加 note）。

| 字段 | LAN 网络类 | SERIAL 串口类 | USB / GPIB | NONE 被动 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `元信息 10 项：id / name / model / vendor / category / role / programmable / interface / required / note` | 必填 | 必填 | 必填 | 必填 | 与接口无关，所有设备一致，注册后只读（note 可改） |
| `host` | 必填 | 不适用 | 不适用 | 不适用 | IP / 主机名；网口类判定「配满」的必要项 |
| `port` | 必填 | 不适用 | 不适用 | 不适用 | 端口；网口类判定「配满」的必要项 |
| `serial_port` | 不适用 | 必填 | 不适用 | 不适用 | 串口号；串口类判定「配满」的唯一必要项 |
| `baudrate` | 不适用 | 选填 | 不适用 | 不适用 | 留空按设备默认值尝试，不阻断判定 |
| `address` | 选填（VISA 通道复用） | 选填 | 必填 | 不适用 | VISA 资源串；USB / GPIB 以它判定「配满」 |
| `channel` | 不适用 | 不适用 | 选填 | 不适用 | 多路电源 / 开关单元的默认通道 |
| `protocol` | 选填 | 选填 | 选填 | 不适用 | 协议说明，写给人看，不参与判定 |
| `configured` | 系统回写 | 系统回写 | 系统回写 | 系统回写 | 平台按接口计算的配置完整标记，只读 |

表单实际提交的键（`payload()` 按接口分组）：

| interface 分支 | 提交键 | 校验规则 |
| --- | --- | --- |
| 网络类（LAN / ETHERNET / TCP / IP） | host / port / protocol + note | IP 或主机名非空且格式合法；端口为 1–65535 整数 |
| 串口类（SERIAL / RS232 / RS485 / UART / COM） | serial_port / baudrate / protocol + note | 串口号形如 COM3 合法性校验；波特率 300–1000000（留空不报错） |
| 其余（USB / GPIB / MANUAL / NONE） | address / channel / protocol + note | 仅当 programmable 时要求资源地址非空；通道与资源地址做字符集校验 |

**为什么存储不裁**：① 一份 `DeviceConfig` 模型覆盖所有设备，注册库 / TPS 契约 / 导出 / 报告共用同一套键；② 接口临时改动（把网口设备借来试串口）不会丢掉原有 host/port；③ 空值语义明确（字符串 `""`、数字 `null`），`_device_configured` 按接口只看该看的字段，空值不会误判；④ 读方不用按接口分支。代价是每台设备多带几个空键（一台几十字节），阅读噪音由表单层吸收。

**若要按接口裁剪存储**，代价是：① 读方（校验 / 导出 / 报告 / 联调脚本）都要按接口分支；② 换接口会丢值；③ 「键不存在」与「被清空」无法区分；④ 预设 BOM、注册库、TPS 三处 schema 容易漂移；⑤ 文件 diff 与评审按设备各异。

**落地三条**：① 注册库永不裁剪，所有设备写满 18 键，空值统一 `""` / `null`；② 需要「干净」外观时只在导出 / 展示层裁剪（导出可加 `omit_empty`，报告与页面按接口只渲染相关字段）；③ 确实要改存储形态时，必须同步改 `DeviceConfig`、`_devices_from_preset`、`_device_configured`、TPS 契约与全部读方，不建议。

## 附：模版本体

完整模版（含逐字段备注与三类接口示例）见 `docs/ate-device-template.jsonc`。

