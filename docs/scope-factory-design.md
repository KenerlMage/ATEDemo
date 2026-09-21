# 示波器型号派发与通用顶层接口设计（泰克 + 罗德与施瓦茨）

结论先说：这套设计里**没有“选择”**——型号是设备档案里的确定字段，注册表按它精确派发；也没有“原始命令”——用户只碰 `Scope` 的业务方法。同一台示波器**网口与串口都能接**：网口给 `host` + `port`，串口给 `serial_port` + `baudrate`；该给的两个参数缺一个、或两种链路参数同时出现又没写明用哪种，一律报错，不替用户猜。收发栈也分两套：默认 `native`（标准库 socket / pyserial，零依赖），需要走 VISA 时 `backend="visa"`（资源名 `TCPIP0::…::SOCKET` / `ASRL6::INSTR`），同一段用例、同一套返回结构。

读完两家官方手册可以确认：差异不在测量能力，而在**数据通路**（前导形态、编码、缩放）与**命令拼写**；把这两块关进厂商包，同一段用户代码在两家上就是同一份。

## 设计目标

| 目标 | 做法 | 为什么 |
| --- | --- | --- |

| **① 型号直连，不做选择** | 型号是设备档案里的确定字段，注册表按它**精确派发**驱动；删掉权重打分、关键字模糊匹配与兜底驱动 | 查不到就报 `E_NOT_FOUND` 并列出已登记型号——宁可当场报错，也不“猜一个最像的”去问仪器 |
| **② 一台设备两种链路、两套栈** | 链路（网口 `host` + `port` / 串口 `serial_port` + `baudrate`）与传输后端（`native` 标准库+pyserial / `visa` PyVISA）互相正交，四种组合都能跑；两者都由档案决定，业务代码与返回结构不变 | 网口 host/port 必须成对；串口必须有串口名；两种链路参数都填又没写明 `interface`、或后端写了型号没声明的值，一律报错，不替用户挑、也不静默退回 |
| **③ 用户感知不到原始命令** | 顶层 `Scope` 只暴露业务方法；命令表、前导解析、缩放换算全部关在厂商包内 | `api.py` 源码零命令字样（有单测扫描它），公开方法的返回值里也不出现命令文本 |
| **④ 通用顶层方法** | `open_scope()` / `set_timebase()` / `set_channel()` / `capture()` / `measure()`… | 换型号、换链路都不改用户代码；单测断言两家返回结构的键与单位完全一致 |

## 依据

| 来源 | 具体材料 | 用到什么 |
| --- | --- | --- |

| **罗得与施瓦茨官方手册** | MXO 4 User Manual 1335.5337.02 ─ 19（2,174 页，含远程控制命令章节） | **已下载并本地抽正文**（3,627,278 字符）；`*IDN?` 示例直接给出型号串 `MXO44` |
| **泰克官方资料** | tm_devices 命令文档（厂家编程手册自动生成）+ 官网 FAQ 52596 | 命令树与电压换算式 `V = (Digitized Level − YOFF) × YMULT` |
| **本仓库既有实现** | `sdk/ate_drivers/` 契约 1.2 家族层 `family/scope.py` | 动作名、参数名、单位、返回结构、测量算法——注册表不重新定义这些 |

## 结构：四层职责

| 位置 | 角色 | 放什么 | 约束 |
| --- | --- | --- | --- |

| `endpoint.py` | **连接方式与后端层** | 把档案解析成 `Endpoint`：LAN（host + port）或 SERIAL（串口名 + 波特率 + 数据位/校验位/停止位）；同时解析传输后端（`native` / `visa`）与规范 VISA 资源名，并转成驱动用的 `DeviceConfig` | 解析顺序：链路 = 显式 `interface=` > 档案 `interface` > 端口填充；后端 = 显式 `backend=` > 档案 `extra.backend` > `native`；有歧义或不支持就报错 |
| `family/scope.py` | **家族契约** | 动作名、参数名、单位、返回结构、测量算法、安全退出 | 两家共享；厂商包不许改 |
| `vendors/tektronix_mso/` | **厂商包** | 命令表、前导解码、通道寻址、枚举映射、仿真模型 | 只填差异；含 `MODELS`（可派发型号）与 `INTERFACES`（可接链路） |
| `factory.py` | **型号注册表** | 按型号登记 / 派发驱动，别名归一化，注册表自检 | **无打分**；未登记型号报错并列出可用型号 |
| `transport.py` | **链路层 × 后端** | `SocketTransport`（网口）/ `SerialTransport`（串口）/ `VisaTransport`（PyVISA 后端，网口与串口都走它）/ `SimulateTransport`（仿真），同一套 open/close/write/read/query | pyserial 与 pyvisa 都**惰性导入**，未装时报可读 `E_CONFIG`，可 `set_serial_factory()` / `set_visa_factory()` 注入 |
| `api.py` | **通用顶层接口** | `Scope` 门面 + `open_scope()`；端点校验、型号核对、链路元信息 | 用户唯一入口；源码内不出现任何命令字样 |

```python
from ate_drivers import open_scope

# 型号来自设备档案（数据库）；地址来自档案的网口字段
dev = {"device_id": "SCOPE-TEK", "model": "MSO54",
       "host": "192.168.10.41", "port": 4000}

with open_scope(device=dev) as scope:
    scope.auto_setup(freq_hz=1000.0, volts_pp=2.4)
    scope.set_timebase(seconds_per_div=5e-4)
    scope.set_channel("CH1", volts_per_div=0.5, coupling="DC")
    wave = scope.capture(points=1000)          # 伏特 + 秒，无命令
    for kind, item in scope.measure(("PK2PK", "FREQUENCY")).items():
        print(kind, item["value"], item["unit"])
```


## 型号派发（打分机制已删除）

| 面向 | 规则 | 说明 |
| --- | --- | --- |

| **型号来源** | 设备档案（数据库）的 `model` 字段 | `open_scope(device=row)` 直接吃 dict 或 ORM 行对象，`model/host/port/name/device_id` 自动读取 |
| **匹配规则** | 归一化后**精确相等**（去大小写与分隔符） | `mso-54` = `MSO54` = ` MSO 54 `；不做子串、不做前缀、不做模糊 |
| **没有兜底** | 未登记 → `E_NOT_FOUND`（含可用型号清单） | 旧版的 `generic-scope` 兜底驱动已删除；`score_spec` / `WEIGHTS` / `SCOPE_SPECS` 一并删除 |
| **别名** | `MODEL_ALIASES` 显式登记（如系列俗称 `MXO4` → `MXO44`） | 别名只指向一个正式型号；注册表自检会校验别名指向存在 |
| **多包冲突** | 同一型号被两个包声明 → `model_conflicts()` 报出来 | 装载现场驱动包时先跑一次冲突检查，避免“装完才发现派发到了另一个包” |
| **登记新型号** | 改厂商包 `driver.json` 的 `models` + 类属性 `MODELS` | 两处不一致会被上架门禁 C17 拦下；平台代码零改动 |

## 通用顶层接口

| 方法 | 作用 | 返回 |
| --- | --- | --- |

| `open_scope(model=…, host=…, port=…, backend=…)` | 按型号打开网口示波器（`backend` 选原生栈 / VISA） | `Scope` |
| `open_scope(device=<档案行>)` | 直接用数据库档案打开（型号/地址自动读取） | `Scope` |
| `Scope.connect()` / `close()` / `with` | 会话建立与安全退出（退出前补发收尾动作） | `dict` |
| `identity()` | 厂商 / 型号 / 序列号 / 固件（已解析，不是原始字符串） | `dict` |
| `auto_setup(freq_hz=, volts_pp=)` | 一键自动设置 | `dict` |
| `set_timebase(seconds_per_div=, offset_seconds=, divisions=)` | 水平系统（每格时间） | `dict` |
| `set_channel(channel=, volts_per_div=, coupling=, offset_volts=, probe_ratio=, enabled=)` | 垂直系统（每格电压 / 耦合 / 位移 / 探头比） | `dict` |
| `configure(channel=, seconds_per_div=, volts_per_div=, …)` | 一次配好水平 + 垂直 | `dict` |
| `run()` / `stop()` / `single()` | 连续采集 / 停止 / 单次 | `dict` |
| `capture(channel=, points=)` | 取一段波形：伏特序列 + 采样间隔 + 首点时刻 | `dict`（见下） |
| `measure(items=, channel=, points=)` | 测量（`PK2PK` / `FREQUENCY` / `RISE`…） | `{kind: {value, unit}}` |
| `status()` / `self_test()` / `metadata()` | 运行状态 / 连通自检 / 驱动与契约版本 | `dict` |

`capture()` 返回的字段：

| 字段 | 含义 | 单位 | 备注 |
| --- | --- | --- | --- |

| `volts` | 伏特序列（元组） | V | 两家都直接给伏特；泰克已按 `(码值−YOFF)×YMULT` 换算过 |
| `unit` | 单位 | `V` | 恒为伏特，不用 mV 混写 |
| `seconds_per_sample` | 采样间隔 | s | 泰克取前导 `XINcr`；R&S 由 `(XStop−XStart)/(N−1)` 算 |
| `first_sample_seconds` | 首点时刻 | s | R&S 头里的 `XStart` 会保留下来 |
| `points` / `channel` / `source` | 点数 / 通道 / 数据来源 | — | 仿真时 `source=simulate` |

## 连接方式与传输后端：网口 / 串口 / VISA

| 连接方式 / 场景 | 怎么接 | 链路实现 | 备注 |
| --- | --- | --- | --- |

| **网口 LAN** | `host` + `port`（如 `192.168.10.41` + `4000`） | VXI-11 / raw socket（stdlib socket，零第三方依赖） | `TCPIP0::192.168.10.41::4000::SOCKET` |
| **串口 SERIAL** | `serial_port` + `baudrate`（+ 数据位/校验位/停止位，默认 8N1） | RS-232 / RS-485 / USB 转串口，默认 pyserial（惰性导入，可注入） | `ASRL::COM6::115200::INSTR` |
| **都填了但没写明** | —— | 报 `E_CONFIG`「连接方式不明确」 | 档案写清 `interface`，或调用时显式传 |
| **`mode="auto"`（默认）** | 有端点 → 真机（按端点走网口或串口） | 没有端点 → 仿真 | 联调与离线开发同一份代码 |
| **`interface=` 可写别名** | `LAN` / `NET` / `TCPIP` / `ETHERNET` / `SOCKET` | `SERIAL` / `COM` / `RS232` / `RS485` / `UART` / `ASRL` | 归一化大小写与分隔符 |
| **链路元信息（排障）** | `scope.interface_kind` / `scope.endpoint` / `scope.endpoint_detail` | `status()` / `metadata()` 也带这些字段 | 日志里能一眼看出走的是哪条链 |
| **后端 `native`（默认）** | 网口：标准库 socket；串口：pyserial（帧参数直接给 `Serial`） | 零第三方依赖 | 行为只取决于档案——不会因为某台机器装了 VISA 就悄悄换栈 |
| **后端 `visa`** | 网口：`TCPIP0::host::port::SOCKET`；串口：`ASRL6::INSTR` 等规范资源名 | pyvisa（NI-VISA / Keysight VISA / pyvisa-py 任一实现，惰性导入、可注入） | **波特率与帧格式作为会话属性下发**，不写进资源名 |
| **后端 `auto`（仅调用时可写）** | 装了 pyvisa → 走 VISA；否则 → 走 native | 解析顺序：显式 `backend=` > 档案 `extra.backend` > `native` | `auto` 不写进 `BACKENDS`；实际用了哪个从 `scope.backend` 查，不靠猜 |
| **后端元信息（排障）** | `scope.backend` / `scope.backend_detail()` / `scope.visa_resource` | `status()` / `metadata()` 也带 | 结果里始终带 `transport` 与 `backend` |
| **后端该报错就报错** | 未装 pyvisa / 后端写 `usb-tmc` / 型号没声明 `visa` | 一律 `E_CONFIG` | 消息里列出可选项或给出 `pip install pyvisa pyvisa-py`，**没有一种会静默退回原生栈** |

## 两家方言对照（18 条，均取自官方手册）

| 意图 | 泰克（MSO5/MSO6） | 罗得与施瓦茨（MXO44） | 顶层方法 |
| --- | --- | --- | --- |

| **身份 / 复位** | `*IDN?`；`*RST;*CLS;*OPC?` | `*IDN?`（同）；`*RST;*CLS;*OPC?` | `identity()` / `reset()` |
| **跑 / 停 / 单次** | `:ACQuire:STATE RUN|STOP`；`:ACQuire:STOPAfter SEQuence` | `RUN` / `STOP` / `SINGle` / `RUNSingle` | `run()` / `stop()` / `single()` |
| **自动设置** | `:AUTOSet EXECute` | `:AUToset` | `auto_setup()` |
| **垂直档位** | `:CH1:SCAle <V/div>` | `:CHANnel1:SCALe <V/div>` | `set_channel(volts_per_div=…)` |
| **输入耦合** | `:CH1:COUPling {AC|DC|GND}` | `:CHANnel1:COUPling {AC|DC|DCLimit}`（枚举待现场核） | `set_channel(coupling="DC")` |
| **水平档位** | `:HORizontal:MAIN:SCAle` | `:TIMebase:SCALe` | `set_timebase(seconds_per_div=…)` |
| **记录长度 / 采样率** | `:HORizontal:ACQLENGTH?`；`:MAIN:SAMPLERate?` | `:ACQuire:POINts`；`:ACQuire:SRATe` | `acquisition(record_length=…)` |
| **波形来源** | `:DATa:SOUrce CH1` | `:WAVeform:SOURce` / 直接用 `:CHANnel1:DATA?` | `capture(channel=…)` |
| **数据编码** | `:DATa:ENCdg {ASCii|RIBinary|RPBinary|…}` | `FORMat[:DATA] {ASCii|INT8BIT|INT16BIT}` | 驱动内部，不外露 |
| **波形前导** | `:WFMOutpre?` → 约 20 个**具名**字段（含 `YMUlt`/`YOFf`） | `:CHANnel1:DATA:HEADer?` → **4 个位置值** | 驱动内部，产出 `seconds_per_sample` 等 |
| **波形数据** | `:CURVe?`（二进制码值） | `:CHANnel1:DATA?`（ASCII 直出伏特，可带 offset,length） | `capture()["volts"]` 恒为伏特 |
| **电压换算** | `V = (code − YOFF) × YMULT` | 恒等（ASCII 已是伏特） | 同上 |
| **采集模式 / 平均** | `:ACQuire:MODe {SAMple|AVErage}`；`:NUMAVg` | `:ACQuire:TYPE`；`:AVERage`；`:COUNt` | `acquisition(mode=…)` |
| **测量选择** | `:MEASUrement:MEAS1:TYPe PK2Pk` | `:MEASurement1:MAIN PDELta` | `measure(items=["PK2PK"])` |
| **测量读数** | `:MEASUrement:MEAS1:VALue?` | `:MEASurement1:RESult[:ACTual]?` | `measure()` 返回 `V`/`Hz`/`s` |
| **测量统计** | `:MEAS1:{COUNt|MAXimum|MINimum|MEAN|STDdev}?` | `:MEASurement1:RESult:{AVG|RMS|NPeak|PPEak|EVTCount|…}?` | （契约 1.3 规划） |
| **参考电平** | `:MEASUrement:REFLevel:{ABSolute|PERCent}:{HIGH|LOW|MID}` | `:MEASurement1:REFLevel<rl>:*` | （契约 1.3 规划） |
| **触发** | `:TRIGger:A:SETLevel`；`:TRIGger:A:EDGE:SLOPe` | `:TRIGger:EVENt1:LEVel1`；`:EVENt1:EDGE:SLOPe`；`:TRIGger:FORCe` | （基线外，走 vendor.*） |

## 五处差异面（厂商包只填这五处）

| 差异面 | 泰克填法 | R&S 填法 | 用户可见的部分不变 |
| --- | --- | --- | --- |

| **① 命令表 `COMMANDS`** | `:ACQuire:STATE` / `:HORizontal:MAIN:SCAle` / `:CURVe?` | `RUN` / `:TIMebase:SCALe` / `:CHANnel1:DATA?` | 键名冻结，用户代码一行不改 |
| **② 前导解码** | 按 `PREAMBLE_FIELDS` 具名取值（16 字段） | 4 值头 → `x_incr=(XStop−XStart)/(N−1)`，`y_mult=1.0`，保留 `x_start_s` | 对外仍是 `volts` + `seconds_per_sample` |
| **③ 通道寻址 `channel_token()`** | 令牌 `CH1` 原样传入 `{channel}` | 数字后缀 `1`，模板用 `{n}` | 家族钩子，两种写法同时提供 |
| **④ 枚举映射** | `PK2Pk`/`RISe`/`PDUty`；耦合含 `GND` | `PDELta`/`RTIMe`/`PDCYcle`；耦合登记 `DC/AC` | 统一 kind：`PK2PK`/`RISE`/`POS_DUTY` |
| **⑤ 仿真模型** | 叠加 8 bit 前端量化 | 叠加 12 bit 前端量化 | 家族确定性算法不动 |

### 数据通路：两条路，一个出口

泰克（具名字段 + 缩放系数）：

```python
# 泰克：按名字取值（WFId 自带逗号，家族层在 wfid 位置合并回来）
PREAMBLE_FIELDS = ("byt_nr", "bit_nr", "encdg", "bn_fmt", "byt_or", "wfid",
                   "nr_pt", "pt_fmt", "x_incr", "x_zero", "pt_off",
                   "y_off", "y_mult", "y_zero", "domain", "wf_type")

# :WFMOutpre? -> 2;8;RIBINARY;RI;MSB;CH1,CH1;1000;Y;2e-06;0.0;0;0.0;0.015625;0.0;TIME;ANALOG
# volts = (code - y_off) * y_mult + y_zero        # 官方 FAQ: V = (Digitized Level - YOFF) * YMULT
```

罗得与施瓦茨（4 值头 + ASCII 直出）：

```python
# R&S：4 值头 + ASCII 直出伏特（缩放退化为恒等），并保留绝对起始时间
def _decode_header(self, text):
    x_start, x_stop, n_pt = -1e-07, 9.980000000000001e-08, 1000   # 官方示例
    return (abs((x_stop - x_start) / (n_pt - 1)), 1.0, 0.0, 0.0, x_start)

# 手册示例：FORM ASC ; CHAN1:DATA?  ->  -0.125000,-0.123016,-0.123016, ...   （已是伏特）
```


两条路的出口一样：`volts`（伏特序列）+ `seconds_per_sample` + `unit="V"`。

## 验收证据

| 项目 | 怎么做 | 结果 |
| --- | --- | --- |

| **单元测试** | `python -m pytest tests -q` | **47 passed**（型号派发 / 连接方式解析 / 串口链路 / 双后端 / 命令不外露 / 两家同用例 / 方言流 / 门禁） |
| **上架门禁** | `python -m ate_drivers.kit.cli check <厂商包>` | 泰克、R&S、示例包各自 **19/19 通过、退出码 0**（C19「传输后端可声明」为本次新增） |
| **注册表自检** | `python -m ate_drivers.kit.cli models` | `{'ok': True, 'models': 5, 'aliases': 1, 'problems': []}`；5 个型号 = MSO54/56/58/64 + MXO44 |
| **同一段用户代码跑两家** | `auto_setup → set_timebase → set_channel → capture → measure` | 泰克 `PK2PK=2.438 V` / `FREQUENCY=1002 Hz`；R&S `PK2PK=2.447 V` / `FREQUENCY=1002 Hz`（键与单位完全一致） |
| **同一段用户代码跑两条链** | 网口用假 socket、串口用假串口回环（把命令交给驱动仿真函数） | 两家各自 11 条命令全部从串口发出：泰克 `:AUTOSet EXECute` / `:HORizontal:MAIN:SCAle`；R&S `:AUToset` / `:TIMebase:SCALe`——业务代码与网口完全同一份 |
| **端点解析** | `endpoint_from(档案)` | 网口档案 → `LAN 192.168.10.41:4000`；串口档案 → `SERIAL COM6@115200,8N1`；空档案 → 仿真 |
| **该报错的都报错** | 只给 host / 只给 port / 只给串口名 / 波特率 -1 / 连接方式写 USB / 两种都填没写明 | 六种情况全部 `E_CONFIG` 且文案可读，**没有一种会静默降级成仿真** |
| **命令不外露** | 扫描 `api.py` 源码 + 公开返回数据 | `:ACQuire`/`:WFMOutpre`/`:CURVe`/`:TIMebase` 等字样在顶层接口源码与返回数据里均为 0 次 |
| **型号写错立刻暴露** | 连接后回读 `*IDN?` 与档案型号比对 | 档案 MSO54、实际 R&S → `E_CONFIG`「设备回读型号与档案不符」 |
| **链路不匹配拦下** | 型号只声明 `LAN`，却给串口端点 | `E_CONFIG`「LAN-ONLY-1 不支持 SERIAL 连接」，并列出该型号支持的链路 |
| **同一段用例跑两套栈** | 假网口 / 假串口各跑一遍：`native`（记录型 socket / 假串口）与 `visa`（假 VISA 会话） | 两次结果**逐项一致**，且 VISA 会话发出去的字节序列 == 原生栈记录器记下的字节序列 |
| **VISA 资源名规范** | `visa_resource_for(档案)` | 网口 → `TCPIP0::192.168.10.41::4000::SOCKET`；串口 `COM6` → `ASRL6::INSTR`；POSIX 路径 → `ASRL::/dev/ttyUSB0::INSTR` |
| **VISA 串口会话语义** | 假 VISA 会话记录属性下发 | `baud_rate=19200` / `data_bits=8` / `parity=N` / `stop_bits=1.0` 逐项落到会话上；会话不认的属性（网口的 `baud_rate`）跳过，不报错 |
| **后端元信息可查** | `scope.backend` / `backend_detail()` | `{'requested': 'auto', 'used': 'native', 'visa_available': False}`——`auto` 挑了什么一定落在状态里 |

## 已知坑与未核验项

| 事项 | 情况 | 处理 |
| --- | --- | --- |

| **`ALIASES` 是家族层的动作别名表** | 在厂商驱动里写 `ALIASES = ("MXO4",)` 会覆盖基类的动作别名字典，导致该驱动所有动作报 `'tuple' object has no attribute 'get'` | 型号别名必须用 `MODEL_ALIASES`；连接方式用 `INTERFACES`，三个名字互不串用 |
| **中文/短型号会被归一化清空** | 归一化只保留字母数字：`"未知型号"` 归一化后是空串 | 空型号单独报「型号为空，无法派发驱动」，与「未登记」区分开 |
| **pyserial / pyvisa 不能变成硬依赖** | 串口链路要 import pyserial、VISA 后端要 import pyvisa，门禁 C15（零第三方依赖）会直接拦下 | 两者都改为**惰性导入**（函数体内）+ 缺它时报可读 `E_CONFIG`；C15 区分「硬依赖」与「惰性可选依赖」（`OPTIONAL_RUNTIME = ('serial', 'pyvisa')`），并可用 `set_serial_factory()` / `set_visa_factory()` 注入自研/测试替身 |
| **两种链路参数同时出现在档案里** | 读档案的人不知道工时现场接的是哪条线 | 报 `E_CONFIG`「连接方式不明确」并提示写 `interface`——**不替用户挑一条**；想强制走某条链可在调用时显式 `interface="LAN"/"SERIAL"` |
| **手册里的方括号不是命令** | `FORMat[:DATA]`、`CHANnel<n>:DATA[:VALues]?` 是文档写法，照抄会发非法命令 | 已改为 `FORMat ASCii` / `:CHANnel{n}:DATA?`；单测断言命令流内容 |
| **R&S 头里没有缩放系数** | 4 值头不给 `YMULT/YOFF` | 走 `FORMat ASCii` 路线（手册示例即伏特）；若改二进制传输，需叠加 `CHANnel1:SCALe/OFFSet` 与位宽换算，用前现场核验 |
| **别把波特率写进 VISA 资源名** | `ASRL::COM6::115200::INSTR` 看着像资源名，其实不是 VISA 规范写法 | 规范名是 `ASRL6::INSTR`；波特率/帧格式走**会话属性**下发（`_apply_visa_attributes()`），改档案 `baudrate` 即生效，不用改资源名 |
| **`auto` 不是一种能力** | 在驱动 `BACKENDS` 里写 `auto` 会被门禁 C19 挡下（声明只能是 `native` / `visa`） | `auto` 只作调用参数：按可用性挑，结果落到 `scope.backend`；要全现场统一 VISA 就写死 `visa` |
| **串口参数要跟仪器对表** | 型号手册给的默认串口参数（波特率 / 数据位 / 校验位 / 停止位）不统一 | 档案里写全；默认按 8N1 + 115200，写错会在读数解析阶段暴露（乱码或超时），先跑 `scope.self_test()` 再上产线 |
| **未做真机联调** | 两家均为仿真 + 记录型端点 + 假串口回环验证 | 现场改档案里的 host/port 或 serial_port/baudrate 即可复跑同一段代码；顺带核验 R&S 耦合枚举、ASCII 量纲、串口参数 |
