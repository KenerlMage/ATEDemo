# 示波器通用接口设计（依据泰克编程手册重新总结）

结论先说：泰克手册摊开后，通用接口设计应从「按命令名包一层」升级为**三层封装**——数据通路（编码 + 前导 + 缩放）、语义参数（V/div、s/div、采样率而不是设备内部档位）、口径（参考电平、平均次数、模式）。手册里最容易被做错的两段是 ⑤数据传输 与 ⑥测量：前者是唯一必须吞掉二进制与缩放公式的地方，后者藏着**参考电平**这个口径参数——不显式化，跨品牌数字就不可比。

本文同时给出与现行契约 1.2 的逐条差异，以及门禁需要新增的 6 项检查。

## 依据与方法

| 来源 | 具体材料 | 用到什么 |
| --- | --- | --- |

| **泰克官方 FAQ** | How do I get voltage data after sending the CURVE? or WAVFRM? query?（tek.com，FAQ ID 52596） | **已读原文**：码值→电压换算公式、256 个量化级、8/10.24 格显示、1 V/div 时 YMULT=40 mV 的算例 |
| **泰克命令文档（手册自动生成）** | tm_devices 官方文档：WFMOutpre / DATa / ACQuire / CH&lt;x&gt; / HORizontal / MEASUrement / TRIGger 模块 | **已读原文**：波形前导字段清单与二分法、编码枚举、采集命令、通道/水平命令、29 项测量枚举、触发命令 |
| **手册标识** | 《4, 5, 6 Series MSO Programmer Manual》（文档号 077-1305-12，约 1,610 页；另有 5 Series MSO 版 1,116 页） | 来自泰克支持页的型号—手册对照；本环境无法直接下载该 PDF（download.tek.com 返回 404），故命令树以官方自动生成文档为据 |
| **本平台实现** | `D:/ATE/sdk/ate_drivers/`（契约 1.2）与 `.openclaw/tmp/ate/recon/scope_returns.txt` | 用来对比「手册口径」与「现行契约」的差距，差异逐条列在 §07 |

## 泰克手册的命令族全景（七段）

手册的命令树按子系统组织，抽象后正好对应七段能力：

| 段 | 手册命令（节选） | 抽象成能力 | 设计要点 |
| --- | --- | --- | --- |

| **① 通用与状态** | `*IDN?` `*RST` `*CLS` `*OPC?` `*ESR?` `:SYST:ERR?` | `identify` `reset` `state` `clear_status` | IEEE 488.2 公共面，所有仪器共有 —— 驱动的**最小可运行集** |
| **② 采集控制** | `:ACQuire:STATE {OFF|ON|RUN|STOP}` `:ACQuire:STOPAfter {RUNSTop|SEQuence}` `:ACQuire:MODe {SAMple|AVErage}` `:ACQuire:NUMAVg` `:ACQuire:NUMACq?` `:ACQuire:MAXSamplerate?` | `scope.run` `scope.stop` `scope.single` `scope.acquisition` | 四件事：跑、停、跑一次、**怎么跑**（采样或平均、跑完停不停、平均多少次） |
| **③ 垂直系统** | `:CH<x>:SCAle <NR3>` `:CH<x>:COUPling {AC|DC|GND}` `:CH<x>:OFFSet` `:CH<x>:BANdwidth` `:CH<x>:INVert` `:CH<x>:PRObe:{GAIN|UNIts|ID|RESistance|AUTOZero|DEGAUss}` | `scope.channel` `scope.probe` | 档位用 **V/div**（物理量）而不是数字档位；**探头倍率属于量程的一部分**（手册把 `PRObe:GAIN` 与 `SCAle` 并列） |
| **④ 水平系统** | `:HORizontal:MAIN:SCAle` `:HORizontal:MAIN:SAMPLERate` `:HORizontal:ACQLENGTH?` `:HORizontal:DELay:{TIMe|MODe|POSition}` | `scope.timebase` `scope.acquisition`（采样率/记录长度） | 时基、采样率、记录长度**三者互相约束**：只给「采样点数」会掩盖量程信息 |
| **⑤ 数据传输** | `:DATa:SOUrce {CH<x>|MATH|REF<x>|D<x>}` `:DATa:ENCdg {ASCIi|RIBinary|RPBinary|SRIbinary|SRPbinary|FAStest}` `:DATa:RESOlution {FULL|REDUced}` `:DATa:COMPosition` `:DATa:STARt/:STOP` `:WFMOutpre?` `:CURVe?` | `scope.acquire_waveform` `scope.acquire_raw` | **最需要抽象的一段**：编码 + 前导 + 缩放公式必须被驱动吃掉，用例只该看到「伏特 + 秒」 |
| **⑥ 测量** | `:MEASUrement:IMMed:TYPe {29 项}` `:MEASUrement:IMMed:VALue?` `:MEASUrement:IMMed:UNIts?` `:MEASUrement:IMMed:SOUrce1/2` `:MEASUrement:MEAS<x>:{TYPe|VALue?|COUNt?|MAXimum?|MINImum?|MEAN?|STDdev?}` `:MEASUrement:METHod {Auto|HIStogram|MINMax}` `:MEASUrement:REFLevel:{ABSolute|PERCent}:{HIGH|LOW|MID|MID2}` | `scope.measure` `scope.measure_statistics` `scope.ref_levels` | 枚举各厂不同 → 统一成固定 kind 表；**参考电平（10%/50%/90% 或绝对值）是口径参数**，不显式化就跨品牌不可比 |
| **⑦ 触发** | `:TRIGger:FORCe` `:TRIGger:A:SETLevel` `:TRIGger:A:{EDGE|PULSe|LOGIC|BUS…}` | `scope.trigger`（基线只收边沿 + 电平） | 触发类型是**长尾**（总线解码、串行协议…）：基线只收最通用两种，其余走 `vendor.*` |

## 数据通路设计（核心）

手册把波形前导字段明确分成两类：**formatting**（`ENCdg` `BN_Fmt` `BYT_Or` `BYT_Nr` `BIT_Nr`）决定「怎么解析字节」，其余字段是对该波形的**interpretation**（由 `DATa:SOUrce` 决定）。这个二分法直接就是通用模型的骨架：`SampleInfo` + `ScalingFactors`。

| 泰克前导字段 | 含义 | 通用字段 | 单位 |
| --- | --- | --- | --- |

| `ENCdg` | 编码方式 `ASCii|BINary` | `SampleInfo.encoding` | — |
| `BN_Fmt` | 二进制表示 `RI`（有符号）/ `RP`（正整型） | `SampleInfo.binary_format` | — |
| `BYT_Or` | 字节序 `MSB|LSB` | `SampleInfo.byte_order` | — |
| `BYT_Nr` | 每点字节数 | `SampleInfo.bytes_per_point` | — |
| `BIT_Nr` | 每点位数（8 或 16；手册原文：8 or 16） | `SampleInfo.bits_per_point` | — |
| `PT_Fmt` | 点格式 `Y|XY` | `SampleInfo.point_format` | — |
| `PT_ORder` | 位序 | `SampleInfo.bit_order` | — |
| `NR_Pt` | 点数 | `Waveform.points` | — |
| `PT_Off` | 触发点相对偏移 | `ScalingFactors.point_offset` | — |
| `XINcr` | 水平采样间隔 | `ScalingFactors.x_incr_s` | **s** |
| `XZEro` | 水平零点 | `ScalingFactors.x_zero_s` | **s** |
| `XUNit` | 水平单位 | `ScalingFactors.x_unit` | s |
| `YMUlt` | 垂直缩放因子（伏/码值） | `ScalingFactors.y_mult` | **V** |
| `YOFf` | 垂直偏移（码值电平） | `ScalingFactors.y_off` | — |
| `YZEro` | 垂直零点 | `ScalingFactors.y_zero` | **V** |
| `YUNit` | 垂直单位 | `ScalingFactors.y_unit` | V |
| `WFId` | 波形标识 | `Waveform.waveform_id` | — |
| `RECOrdlength` | 记录长度 | `AcquireSetup.record_length` | — |
| `COMPosition` | 波形形态 `SINGULAR_YT|COMPOSITE_YT|COMPOSITE_ENV` | `Waveform.composition` | — |
| `FILTERFreq` / `FRACTional` | 滤波频率 / 小数位 | `SampleInfo.filter_freq_hz` / `decimals` | **Hz** |

换算与解析（`acquire_waveform` / `acquire_raw` 内部必须完成）：

```python
# 电压换算（官方 FAQ 原文）：
#   Voltage = (Digitizing Level - YOFF) * YMULT
#   示例：1 V/div 时 YMULT = 4.0e-2（40 mV/级）；码值 125 → 5 V
#   手册说明：256 个量化级，屏幕只显示 10.24 格中的 8 格 → 每格 25 级
#
# 时间轴（由前导字段语义推得，待真机核验）：
#   t[n] = XZEro + (n - PT_Off) * XINcr

# 驱动内部必须做的事（用例不该看到这一层）：
samples = [ (level - scaling.y_off) * scaling.y_mult for level in raw_levels ]
x_incr  = scaling.x_incr_s           # s
wave    = { "schema": "ate.driver.waveform.v1", "channel": "CH1",
            "points": len(samples), "samples": samples,
            "x_start_s": 0.0, "x_incr_s": x_incr, "unit": "V" }
```

## 测量设计（29 项枚举 → 统一 kind）

泰克 `MEASUrement:IMMed:TYPe` 与 `MEASUrement:MEAS<x>:TYPe` 共用同一组 29 个取值（实测抓取）：

| 泰克枚举 | 统一 kind | 物理量 | 单位 |
| --- | --- | --- | --- |

| `AMPlitude` | `AMPLITUDE` | 幅值 | V |
| `AREa` | `AREA` | 面积 | V·s |
| `BURst` | `BURST_WIDTH` | 猝发宽度 | s |
| `CARea` | `CYCLE_AREA` | 单周期面积 | V·s |
| `CMEan` | `CYCLE_MEAN` | 单周期均值 | V |
| `CRMs` | `CYCLE_RMS` | 单周期有效值 | V |
| `DELay` | `DELAY` | 通道间延时 | s |
| `FALL` | `FALL_TIME` | 下降时间 | s |
| `FREQuency` | `FREQUENCY` | 频率 | Hz |
| `HIGH` | `HIGH_LEVEL` | 高电平 | V |
| `LOW` | `LOW_LEVEL` | 低电平 | V |
| `MAXimum` | `MAX` | 最大值 | V |
| `MEAN` | `MEAN` | 均值 | V |
| `MINImum` | `MIN` | 最小值 | V |
| `NDUty` | `NEG_DUTY` | 负占空比 | % |
| `NEDGECount` | `NEG_EDGE_COUNT` | 下降沿计数 | —（计数） |
| `NOVershoot` | `NEG_OVERSHOOT` | 负过冲 | % |
| `NPULSECount` | `NEG_PULSE_COUNT` | 负脉冲计数 | —（计数） |
| `NWIdth` | `NEG_WIDTH` | 负脉宽 | s |
| `PDUty` | `POS_DUTY` | 正占空比 | % |
| `PEDGECount` | `POS_EDGE_COUNT` | 上升沿计数 | —（计数） |
| `PERIod` | `PERIOD` | 周期 | s |
| `PHAse` | `PHASE` | 相位 | °（待核） |
| `PK2Pk` | `PK2PK` | 峰峰值 | V |
| `POVershoot` | `POS_OVERSHOOT` | 正过冲 | % |
| `PPULSECount` | `POS_PULSE_COUNT` | 正脉冲计数 | —（计数） |
| `PWIdth` | `POS_WIDTH` | 正脉宽 | s |
| `RISe` | `RISE_TIME` | 上升时间 | s |
| `RMS` | `RMS` | 有效值 | V |

此外手册显式提供**口径与统计**两层：`MEASUrement:METHod {Auto|HIStogram|MINMax}`、`MEASUrement:REFLevel:{ABSolute|PERCent}:{HIGH|LOW|MID|MID2}`（参考电平）、`MEAS<x>:{COUNt?|MAXimum?|MINImum?|MEAN?|STDdev?}`（统计）。现行契约一律按 10%/50%/90% 自算、且不暴露统计，因此：① `ref_levels` 需显式化；② 统计要作为独立能力 `scope.measure_statistics`。

## 差异收敛面

此前总结的「三处差异」要精确化：三处**必填**（命令表 / 前导映射 / 解码与缩放实现），两处**需声明**（能力裁剪 / 口径）。

| 类别 | 差异面 | 型号驱动要做什么 | 依据 |
| --- | --- | --- | --- |

| 必填 | **① 命令表 `COMMANDS`** | 每个命令键对应一个真实命令字符串（键名冻结） | 手册命令树；漏填由 `missing_commands()` 与门禁 C05 拦下 |
| 必填 | **② 前导映射 `PREAMBLE_FIELDS`** | 本型号 `:WFMOutpre?` 的字段顺序与命名（按名取值，不按位置猜） | `WFMOutpre?` 的返回顺序（手册逐字段列出） |
| 必填 | **③ 解码与缩放实现** | `_parse_curve()` 与缩放公式（二进制分块、字节序、`YMULT/YOFF`） | 官方 FAQ 的换算式 + `DATa:ENCdg/RESOlution` 枚举 |
| 需声明 | **④ 能力裁剪** | 哪些可选能力真支持（时基/通道/触发/统计/原始数据…） | 以仪器选项与固件版本为准，`supports()` 回答 |
| 需声明 | **⑤ 口径声明** | 参考电平（10%/50%/90% 或绝对）、平均次数、仿真模型 | `MEASUrement:REFLevel:*` 与 `ACQuire:NUMAVg` 对应的语义 |

## 与现行契约 1.2 的差异

新增能力（6 个，含签名）：

### `scope.acquisition`

```python
`def acquisition(self, mode: str | None = None, averages: int | None = None, stop_after: str | None = None, sample_rate_sa_s: float | None = None, record_length: int | None = None, **_) -> dict`
```

- 返回 `value`：`{mode, averages, stop_after, sample_rate_sa_s, record_length, digitizing_levels}`
- 用途：采样/平均模式、平均次数、跑完是否停下、采样率、记录长度一并读写

### `scope.trigger`

```python
`def trigger(self, type: str | None = None, source: str | None = None, level_v: float | None = None, slope: str | None = None, force: bool = False, **_) -> dict`
```

- 返回 `value`：`{type, source, level_v, slope, forced}`
- 用途：基线只做 `edge`（含电平与斜率）；`force` 对应 `TRIGger FORCe`

### `scope.probe`

```python
`def probe(self, channel: str = "CH1", gain: float | None = None, units: str | None = None, autozero: bool = False, degauss: bool = False, **_) -> dict`
```

- 返回 `value`：`{channel, gain, ratio, units, impedance_ohm, autozero_done, degauss_done}`
- 用途：探头倍率/单位/阻抗与自动调零——**量程的一部分**，此前遗漏

### `scope.acquire_raw`

```python
`def acquire_raw(self, points: int = 1000, channel: str = "CH1", encoding: str = "binary", **_) -> dict`
```

- 返回 `value`：`{sample_info: {...}, scaling: {...}, raw_b64, ascii}`
- 用途：给归档留证与自算场景：返回**未经缩放**的码值 + 完整前导，可用同一份数学复现 `acquire_waveform`

### `scope.measure_statistics`

```python
`def measure_statistics(self, items: list | None = None, channel: str = "CH1", **_) -> dict`
```

- 返回 `value`：`[{type, value, unit, count, mean, min, max, stddev}]`
- 用途：对应 `MEAS<x>:{COUNt?|MEAN?|MAXimum?|MINImum?|STDdev?}`，把仪器侧统计暴露出来

### `scope.ref_levels`

```python
`def ref_levels(self, method: str | None = None, low_pct: float | None = None, mid_pct: float | None = None, high_pct: float | None = None, **_) -> dict`
```

- 返回 `value`：`{method, low_pct, mid_pct, high_pct}`
- 用途：把测量口径显式化；`measure(...)` 增加 `ref_levels=` 参数

新增数据模型（5 个）：

| 模型 | 签名 | 作用 |
| --- | --- | --- |

| `SampleInfo` | `(encoding: str, binary_format: str = "RI", byte_order: str = "MSB", bytes_per_point: int = 2, bits_per_point: int = 16, bit_order: str = "MSB", point_format: str = "Y", filter_freq_hz: float = 0.0, decimals: int = 0)` | 手工册前导的 formatting 部分——**决定怎么解析字节** |
| `ScalingFactors` | `(x_incr_s: float, x_zero_s: float = 0.0, x_unit: str = "s", y_mult: float = 1.0, y_off: float = 0.0, y_zero: float = 0.0, y_unit: str = "V", point_offset: int = 0)` | 手工册前导的 interpretation 部分——**决定怎么变成伏特与秒** |
| `AcquireSetup` | `(mode: str = "sample", averages: int = 1, stop_after: str = "run_stop", sample_rate_sa_s: float = 0.0, record_length: int = 0, digitizing_levels: int = 256)` | 采集方式与约束；`digitizing_levels` 来自官方 FAQ 的 256 级 |
| `TriggerSetup` | `(type: str = "edge", source: str = "CH1", level_v: float = 0.0, slope: str = "rising")` | 触发基线配置 |
| `RefLevels` | `(method: str = "percent", low_pct: float = 10.0, mid_pct: float = 50.0, high_pct: float = 90.0)` | 测量口径（百分位或绝对值），跨品牌可比的前提 |

修订既有接口：`scope.timebase` 增加 `sample_rate_sa_s` / `record_length` 只读回显；`scope.measure` 增加 `ref_levels=` 参数、返回项增加 `ref_levels` 字段；`scope.acquire_waveform` 返回项增加 `sample_info` / `scaling` 摘要（便于归档对照）。

## 上架门禁同步调整

16 项 → **22 项**，新增 6 项全部针对本轮手册核对暴露的风险：

| 编号 | 检查项 | 判据 |
| --- | --- | --- |

| C17 | 前导字段全覆盖 | 型号驱动声明的 `PREAMBLE_FIELDS` 必须覆盖 `SampleInfo`/`ScalingFactors` 所需字段，且按名取值（禁止按位置猜） |
| C18 | 波形结构自检 | 仿真下 `len(samples) == points` 且 `x_incr_s > 0`、`unit == "V"` |
| C19 | 缩放公式可验证 | 给定码值 + 前导断言 `V = (level − YOFF) × YMULT` 成立，误差 < 1e-9 |
| C20 | 口径已声明 | `scope.measure` 返回项必须带 `ref_levels` 口径（默认值也要显式标注） |
| C21 | 参数单位后缀 | 物理参数名必须带单位后缀（`_v` / `_s` / `_hz` / `_sa_s`），禁止无量纲裸数当量程 |
| C22 | 原始数据可追溯 | `acquire_raw` 的 `sample_info + scaling` 必须能复现 `acquire_waveform` 的浮点结果（同一份数学） |

## 未核验项与风险

| 事项 | 情况 | 处理 |
| --- | --- | --- |

| **未能直接读到手册 PDF** | download.tek.com 的 PDF 直链返回 404；Rigol 手册为扫描件或被 403 拦截 | 命令树以泰克官方自动生成文档（含逐字段语法与枚举）+ 官方 FAQ 为据；现场仍需用 `*IDN?` 与固件版本对应的手册页码复核 |
| **ASCII 编码下的量纲** | FAQ 只给了二进制（RPBinary）的换算公式，未说明 ASCII 下采样值是否已是伏特 | 驱动对两种编码都返回伏特，但 ASCII 分支需真机核验；`acquire_raw` 保留原始应答便于比对 |
| **x 轴公式** | `XZEro` / `PT_Off` / `XINcr` 三字段语义明确，但把三者串成公式属推导 | 以真机 `:CURVe?` + 前导与仪器屏上刻度对照验证 |
| **单位与统计口径细节** | `PHAse` 的单位、计数类测量项是否有单位/统计口径，手册页未逐条取到 | 在 §05 表中已标注「待核」；实现时以真机 `MEAS<x>:UNIts?` 回读为准 |
| **触发长尾与序列采集** | `TRIGger:A:BUS:*`（I2C/SPI/CAN/RS232）与 `ACQuire:STOPAfter SEQuence` 只留了接口位 | 归入 `vendor.*` 扩展与后续迭代，不进基线（避免基线被长尾拖垮） |
