# 仪器驱动库 · 开发模板与使用说明

面向平台开发人员：如何把一台通用外设（示波器、信号发生器、万用表、电源…）写成平台可加载的驱动包。结论先给：**版本差异只准出现在五处**（命令表 / 应答解析 / 量纲缩放 / 枚举映射 / 仿真模型），其余语义由契约层与家族层保证，所以换品牌的示波器时 TPS 用例零改动。

- 库位置：`D:/ATE/sdk/ate_drivers/`（契约 + 工具链）、`D:/ATE/sdk/samples/tek_mso5/`（示波器示例）
- 契约版本：`ate.driver.api 1.2`；结果结构 `ate.driver.result.v1`
- 上架门禁：`python -m ate_drivers.kit.cli check <包目录>` 必须 19/19 通过
- 自测：`python -m pytest tests -q`（47 项）· 本机实测 **47 passed / 各包 19-19 一致性通过**

## 一分钟上手

```bash
# 1. 生成驱动包骨架（目录名会自动转成合法模块名）
python -m ate_drivers.kit.cli new acme-scope-3000 --dir samples

# 2. 填五处差异面后，跑上架门禁
python -m ate_drivers.kit.cli check samples/acme_scope_3000

# 3. 列出已装驱动包 / 打印契约规则
python -m ate_drivers.kit.cli list samples
python -m ate_drivers.kit.cli contract
```

生成的骨架**开箱即过 19 项检查**（工程上已实测），开发者的工作只是把五处差异面填成真实值。

| 生成物 | 作用 |
| --- | --- |
| `driver.py` | 驱动实现（继承家族类，填五处差异面） |
| `driver.json` | 驱动包清单（key / 版本 / 契约 / 匹配关键字 / 能力） |
| `test_driver.py` | 一致性检查用例（CI 里跑） |
| `__init__.py` / `README.md` | 包导出与开发说明 |

## 库的结构与分层

| 层 | 内容 | 约束 |
| --- | --- | --- |

| **L0 契约层** | `ate_drivers/`（`contract.py` / `capabilities.py` / `errors.py` / `models.py` / `endpoint.py` / `transport.py` / `base.py`） | 随平台内置、**冻结**，只增不改；驱动包 import 它，不 import 平台 |
| **L1 家族层** | `ate_drivers/family/*.py`（`scope.py` 示波器，后续 dmm / psu / awg / motion） | 一类仪器的语义：动作、参数、单位、返回结构、测量算法、默认仿真 |
| **L2 型号层** | 驱动包（如 `samples/tek_mso5/`），装到 `<ATE>/drivers/<key>/` | 只填五处差异面（命令表 / 应答解析 / 量纲缩放 / 枚举映射 / 仿真模型）；可货架分发 |
| **L3 私有层** | 客户专有设备包（侧载、不上架） | 同上，但走本地侧载通道，签名校验可显式豁免并留痕 |

传输层再分两个**后端**：`native`（标准库 socket / pyserial，零依赖）与 `visa`（PyVISA，资源名走 `TCPIP0::host::port::SOCKET` / `ASRL6::INSTR`，波特率作为会话属性下发）；由 `backend=` 选择、由驱动声明的 `BACKENDS` 限定，业务代码与返回结构不变。


依赖方向单向：`TPS 用例 / 装备助手 → 家族层 → 契约层 → transport`。型号驱动**不得** import 平台内部模块（`backend/*`），否则无法独立测试与分发。

## 三步写出一个示波器驱动

| 差异 | 位置 | 说明 | 示例 |
| --- | --- | --- | --- |

| **① 命令表** | `COMMANDS`（dict） | 只覆盖与家族默认不同的键；**键名固定不可改**，平台按键做能力映射 | `"timebase_set": ":TIM:SCAL {scale:g}"` |
| **② 应答解析** | `PREAMBLE_FIELDS` / `_decode_preamble()` | 波形前导字段顺序，按手册填名取值；不填则退化为通用位置启发式 | `("byt_nr", …, "x_incr", "y_mult", "y_off", "y_zero")` |
| **③ 仿真模型** | `_sim_signal()` | 无实机时让用例跑通；**同参数必须同结果**（一致性检查会验证） | 在家族默认模型上叠加 8 bit 量化 |
| **③ 量纲缩放** | 解析函数内 | 裸值 → 物理单位（伏/秒/赫兹），出口统一 | `(raw - y_off) * y_mult` |
| **④ 枚举映射** | 家族枚举 → 本机取值 | 不支持的取值报 `E_PARAM`，**不静默替换** | 耦合 `DC/AC`；`GND` 报错 |

```python
from ate_drivers import ScopeDriver


class AcmeScope3000(ScopeDriver):
    driver_key = "acme-scope-3000"
    family = "scope"
    label = "Acme 3000 系列示波器"
    driver_version = "0.1.0"
    sim_idn = "ACME,SCOPE-3000,SIM,0.1.0"

    # ① 只覆盖与家族不同的命令键
    COMMANDS = dict(ScopeDriver.COMMANDS, **{
        "timebase_set": ":TIM:SCAL {scale:g}",
        "waveform_data": ":WAV:DATA?",
    })

    # ② 前导字段顺序（按手册填；填了就按名取值）
    PREAMBLE_FIELDS = ("byt_nr", "bit_nr", "encdg", "bn_fmt", "byt_or", "wfid",
                       "nr_pt", "pt_fmt", "x_incr", "x_zero", "pt_off",
                       "y_off", "y_mult", "y_zero")

    # ③ 仿真模型：在家族默认模型上叠加厂商特征
    def _sim_signal(self, points: int, window_s: float) -> list[float]:
        return super()._sim_signal(points, window_s)
```

调用侧（TPS 用例 / 装备助手）长这样，**与品牌无关**：

```python
dev.do("scope.autoset", freq_hz=1000.0, vpp_v=2.4)
wave = dev.do("scope.acquire_waveform", points=4000)["value"]   # unit: V, schema: ate.driver.waveform.v1
items = dev.do("scope.measure", items=["PK2PK", "FREQUENCY"])["value"]
assert 2.2 < items[0]["value"] < 2.6
```

## 契约清单（能力 / 单位）

| 分组 | 能力 | 作用 | 要求 |
| --- | --- | --- | --- |

| 核心（所有驱动必须有） | `identify` / `reset` / `state` | 识别、复位、取状态 | 必选 |
| 示波器家族 | `scope.acquire_waveform` | 取一段波形（采样点 + 时间轴） | 家族基线·必选 |
| 示波器家族 | `scope.measure` | 测量项（8 项，单位固定） | 家族基线·必选 |
| 示波器家族 | `scope.timebase` / `scope.channel` | 时基与通道配置 | 可选 |
| 示波器家族 | `scope.autoset` | 自动设置 | 可选 |
| 示波器家族 | `scope.run` / `scope.stop` / `scope.single` | 采集控制（启动后自动登记安全退出） | 可选 |
| 厂商扩展 | `vendor.<厂商>.<动作>` | 平台不解释的私有能力，命名必须带厂商前缀 | 可选 |

单位约定（写死在返回结构里，单位不一致视为契约破坏）：

| 物理量 | 单位 | 涉及字段 |
| --- | --- | --- |

| 电压 | **V** | 波形采样值、`PK2PK` / `AMPLITUDE` / `MEAN` / `RMS` |
| 时间 | **s** | `PERIOD` / `RISE` / `FALL` / `x_incr_s` / 时基 `scale_s_per_div` |
| 频率 | **Hz** | `FREQUENCY` / 仿真信号 `freq_hz` |
| 垂直档位 | **V/div** | 通道 `scale_v_per_div` |
| 时基档位 | **s/div** | `scale_s_per_div` |

## 返回结构与错误码

| 键 | 类型 | 作用 |
| --- | --- | --- |

| `ok` | bool | 动作是否成功（失败一律抛异常，不返回 ok=False） |
| `schema` | str | 结果结构版本 `ate.driver.result.v1`；上层按它判兼容 |
| `driver` / `driver_version` / `api` | str | 驱动标识与版本，**运行记录必须落这三个值**（可复现） |
| `action` / `requested` | str | 规范动作名 / 调用方写的原名（旧别名可追溯） |
| `value` | any | 动作主结果（**必须可 JSON 序列化**，报告直接写盘） |
| `detail` | dict | 附加信息（时基、通道、来源…），只增键不改名 |
| `quality` | str | `good` / `approximate` / `simulated`，让报告能标注数据可信度 |
| `source` | str | `instrument`（实机）/ `simulate`（仿真） |
| `elapsed_ms` / `warnings` | int / list | 耗时与告警（不阻断但要在报告里体现） |

| 错误码 | 异常类 | 触发场景 |
| --- | --- | --- |

| `E_DRIVER` | `DriverError` | 驱动通用错误（基类）；`to_dict()` 给结构化输出 |
| `E_PARAM` | `DriverError(code=`E_PARAM`)` | 参数非法：枚举越界、≤0、未知测量项（`detail` 列出可选值） |
| `E_CONFIG` | `ConfigurationError` | 连接参数缺失/非法（缺 IP、端口、串口） |
| `E_NOT_FOUND` | `DriverNotFound` | 注册库里找不到驱动实现，或设备未登记 |
| `E_TRANSPORT` | `TransportError` | 链路错误：连不上、写失败、串口打不开 |
| `E_TIMEOUT` | `DriverTimeout` | 读写超时（与链路错误分开，因为可重试） |
| `E_PROTOCOL` | `ProtocolError` | 应答不符合协议（解析不出前导/曲线为空） |
| `E_UNSUPPORTED` | `UnsupportedCapability` | 驱动未声明该能力（调用方应先 `supports()`） |
| `E_CONTRACT` | `ContractMismatch` | 契约版本不兼容（加载阶段就拒绝） |

## 十二条兼容性铁律

| 铁律 | 内容 | 对应检查项 |
| --- | --- | --- |

| **只增不减** | 新增能力 / 动作 / `detail` 键可以；删除、改名、改语义不行 | C03 / C04 / C07 |
| **单位写进结构** | 所有物理量字段带单位并统一 SI 基本单位，避免"1.5 是 V 还是 mV" | C08 |
| **参数只增可选** | 新参数必须可选 + 有默认值；位置参数顺序不得调整 | C06 |
| **破坏性变更升 major** | `API_VERSION` major 变化即不兼容；驱动包 major 必须与平台相等 | C02 |
| **弃用跨一个 minor** | 弃用动作必须给替代动作与移除版本，期间保留可调用 | C16 |
| **错误码稳定** | 抛 `DriverError` 子类 + `E_*` 码；上层按码分支，不解析中文文案 | C09 |
| **仿真必须实现** | `simulate: true` 是上架硬要求：产线多数时间没有实机 | C06 / C12 |
| **仿真必须可复现** | 同参数同结果（用参数派生的种子，不用随机时钟） | C12 |
| **实例不共享状态** | 状态挂在实例上，不挂类属性；两台设备互不干扰 | C13 |
| **会话可重入** | open/close 幂等，关掉能再开；关闭前先下发安全退出命令 | C10 / C11 |
| **零第三方依赖** | 只用标准库（产线工控机可能离线且不许装包）；串口 `pyserial` 与 VISA 后端 `pyvisa` 是**唯一例外**：必须函数体内惰性导入 + 留注入点 | C15 / C19 |
| **旧名保留一版** | 历史 TPS 里的短动作名（`waveform`）保留映射，用告警引导迁移 | C14 |

## 一致性检查 19 项

```bash
python -m ate_drivers.kit.cli check samples/tek_mso5   # 退出码 0 = 可上架
```

| 编号 | 检查项 | 判据 |
| --- | --- | --- |

| C01 | 类元数据完整 | key / family / label / version 非空且格式合法 |
| C02 | 契约版本兼容 | 驱动声明的 api 与平台契约可兼容 |
| C03 | 能力声明合法 | 家族基线能力不缺；未登记能力须带 `vendor.` 前缀 |
| C04 | 能力均有实现 | 每个声明的能力都能解析到方法 |
| C05 | 命令表完整 | 家族要求的命令键全部存在（型号驱动漏填会在这里拦下） |
| C06 | 仿真可执行全部能力 | 仿真模式下逐个能力动作能跑通且 `ok` |
| C07 | 返回结构可序列化 | 结果含全部规定键且能 `json.dumps`（报告落盘不炸） |
| C08 | 测量项带单位 | 测量结果单位齐全且与约定一致（PK2PK→V、FREQUENCY→Hz） |
| C09 | 错误可分类 | 未知动作抛 `UnsupportedCapability`；非法参数抛带 `E_*` 的 `DriverError` |
| C10 | 会话可重入 | open/close 幂等，close 后能重开并识别 |
| C11 | 安全退出 | 启动采集后登记 stop，关闭会话时下发（不把仪器留在危险状态） |
| C12 | 仿真可复现 | 同参数两次调用、以及跨实例结果一致 |
| C13 | 实例互不干扰 | 两台设备实例状态不串（类属性共享会在这里暴露） |
| C14 | 旧动作别名有效 | `ALIASES` 里每个旧名都指向存在的动作 |
| C15 | 零第三方依赖 | AST 扫描驱动包模块：只允许标准库与 `ate_drivers`；`pyserial` / `pyvisa` 只允许惰性导入 |
| C16 | 弃用项有替代 | 弃用登记表每项都有替代动作与移除版本 |
| C17 | 型号可派发 | `MODELS` 已声明、与清单 `models` 一致，未登记型号能被明确拒绝 |
| C18 | 连接方式可声明 | `INTERFACES` ⊆ {LAN, SERIAL}、与清单 `interfaces` 一致，且能解析出真实端点 |
| C19 | 传输后端可声明 | `BACKENDS` ⊆ {native, visa}、与清单 `backends` 一致；每种后端在网口/串口都能解析到链路类 |

## 驱动包清单 driver.json

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |

| `schema` | str | 必填 | `ate.driver.manifest.v1`（固定） |
| `key` | str | 必填 | 驱动唯一标识，形如 `vendor-model`；同 key 只能有一个 active 版本 |
| `version` | str | 必填 | 驱动自身版本 `x.y.z`；写入运行记录便于复现 |
| `family` | str | 必填 | 家族：`scope` / `dmm` / `psu` / `awg` / `motion` / `passive` / `generic` |
| `api` | str | 必填 | 实现哪套契约（`1.2`）；major 必须与平台相等 |
| `entry` | str | 必填 | `包.模块:类名`，如 `tek_mso5.driver:TekMso5Scope` |
| `label` | str | 建议 | 中文名，出现在工具卡片、报告与驱动库页 |
| `match` | dict | 必填 | 命中字段与关键字：`model` / `vendor` / `category` / `role` / `interface` / `name` |
| `priority` | int | 建议 | 同分时的优先级；型号专用驱动应高于通用驱动（示例 20 vs 12） |
| `capabilities` | list | 建议 | 声明能力清单，平台据此做界面与用例能力过滤 |
| `simulate` | bool | 必填 | 是否自带仿真模型；上架要求 `true` |
| `min_platform` | str | 可选 | 要求的最低平台版本，安装前预检 |
| `signature` | str | 可选 | Ed25519 签名（离线可验），L3 私有包可显式豁免 |

## 平台侧接入

```python
from ate_drivers.kit import scan_dir, merge_specs, resolve_spec

specs = merge_specs(DRIVER_SPECS, scan_dir("<ATE>/drivers"))  # 内置 + 已装包合成一张注册表
spec  = resolve_spec(device, specs)                           # 打分选择（权重与现有实现一致）
```

| 阶段 | 内容 | 行为变化 | 预估 |
| --- | --- | --- | --- |

| 阶段 1 | `DRIVER_SPECS` 改为「内置 + 目录扫描合并」；定义 `driver.json` 规范 | 零行为变化（纯重构，回归全绿为准） | 1–2 天 |
| 阶段 2 | `<ATE>/drivers/` 目录 + 安装/卸载/启停/回滚 + 前端「驱动库」页 | 新增界面与接口 | 2–3 天 |
| 阶段 3 | 签名校验（复用现有 Ed25519）+ 离线目录源 + `index.json` | 安装需过校验 | 1–2 天 |
| 阶段 4 | 把内置 MSO5 驱动抽成第一个货架包（本 SDK 的 `samples/tek_mso5` 即样板） | 第一个真实货架条目 | 1–2 天 |

## 真机调试与常见错误

没有硬件时用 `FakeTransport` 精确构造应答，连真机分支一起测：

```python
from ate_drivers.kit import FakeTransport

t = FakeTransport()
t.expect("*IDN?", "TEKTRONIX,MSO54,SN123456,1.2.3")
t.expect(":WFMOUTPre?", "2;8;RIBINARY;RI;MSB;CH1;3;Y;1e-05;0;0;0.0;0.01;0.0;TIME;ANALOG")
t.expect(":CURVe?", "128,130,132")

dev = TekMso5Scope(DeviceConfig.from_dict({"device_id": "dut-scope", "interface": "LAN",
                                           "host": "192.168.10.41", "port": 4000}),
                   mode="real", transport=t)
dev.open()
assert dev.do("scope.acquire_waveform", points=3)["value"]["samples"] == [1.28, 1.3, 1.32]
```

| 现象 | 原因 | 处理 |
| --- | --- | --- |

| 一致性检查 C05 失败：命令表缺键 | 型号驱动覆盖 `COMMANDS` 时漏了家族要求的键 | 对照 `ScopeDriver.REQUIRED_COMMANDS` 补齐；只覆盖差异键，其余继承 |
| C15 失败：引入第三方硬依赖 | 模块顶部 import 了 `numpy` / `serial` / `pyvisa` | 只有 `pyserial`（串口）与 `pyvisa`（VISA 后端）允许函数体内惰性导入并留注入点；其余改用标准库 |
| C12 失败：仿真不可复现 | 仿真里用了 `random.random()` / `time.time()` 做种子 | 用参数派生种子（`random.Random(f"{参数}")`），同参数必得同结果 |
| C13 失败：实例状态串台 | 把状态写成类属性（所有实例共享） | 状态一律在 `__init__` 里挂 `self` |
| 真机取不到波形 | 前导字段顺序与 `PREAMBLE_FIELDS` 不符，或曲线是二进制编码 | 按手册核对顺序；二进制编码时覆盖 `_parse_curve()` |
| 测量值与仪器读数不一致 | 仪器内测量用了平均/带宽限制，而家族算法按采样点自算 | 这是**设计取舍**：跨品牌口径一致优先；需要仪器口径时新增独立动作，不改 `scope.measure` |
| 设备被挂到错误的驱动 | `match` 关键字过宽（如只写厂商名），未知型号也被吸走 | 关键字要写到型号级；厂商级兜底交给 `generic-scpi`，必要时调 `priority` |
