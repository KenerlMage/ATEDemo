# ATE Runner 仪器驱动库（SDK）

给平台开发人员写**通用外设驱动**（示波器、信号发生器、万用表、电源…）用的库与模板。
示波器这一类已经落地两家：**泰克 MSO5/MSO6 系列**与**罗德与施瓦茨 MXO44（MXO 4 系列）**，
都是可编程型号：**网口（LAN）与串口（SERIAL）都能接**，链路由设备档案决定、业务代码不变；收发栈也分两套——默认 `native`（标准库 socket / pyserial，零依赖），需要时 `backend="visa"` 走 PyVISA，同一段用例、同一套返回结构。

```bash
# 1. 生成一个驱动包骨架（--model 必填：型号是派发的唯一依据）
python -m ate_drivers.kit.cli new acme-scope-3000 --dir samples --model ACME-3000

# 2. 填差异面，然后跑上架门禁（19 项一致性检查）
python -m ate_drivers.kit.cli check samples/acme_scope_3000

# 3. 看平台已登记型号 / 已装驱动包
python -m ate_drivers.kit.cli models
python -m ate_drivers.kit.cli list samples
```

## 文档

| 文档 | 讲什么 | 什么时候看 |
| --- | --- | --- |
| [设计说明](docs/design.md) | 分层结构与依赖方向、数据模型、返回协议（门面 / 契约两层）、错误模型、型号派发、连接方式与传输后端解析（网口 / 串口 × 原生栈 / VISA）、版本兼容、19 项门禁、设计取舍 | 想搞清楚「这套库为什么长这样」 |
| [设计准则](docs/guidelines.md) | G1–G12 十二条红线（命令不外露 / 型号直连派发 / 差异收敛 / 物理量出口统一 / 错误不静默 / 零硬依赖 …）+ 命名规矩 + 合并前评审清单 | 写驱动、改库、做 review 前 |
| [使用手册](docs/user-guide.md) | 仿真 / 网口 / 串口 / 换后端四种上手方式、门面方法全表、返回结构解读、错误处理、CLI 手册、故障排查表 | 写 TPS 用例、接平台、现场排障 |
| [新设备家族接入指南](docs/new-family-guide.md) | 已有家族 vs 新家族的决策树、新家族九步设计法、万用表 DMM 完整示例、五差异面模板、反模式与版本演进 | 要接万用表 / 电源 / 信号源 / 运动机构、或完全没见过的设备 |

每份文档都有同名的 `.html` 可读版（单文件自包含，双击即看）。


## 目录

```
sdk/
├── ate_drivers/                # 库本体（平台内置、对外冻结）
│   ├── contract.py             # 契约版本 1.2 + 兼容规则 + 弃用机制
│   ├── capabilities.py         # 能力命名空间与家族基线
│   ├── errors.py               # 带稳定错误码的异常
│   ├── models.py               # DeviceConfig（连接参数：网口 host/port，串口 serial_port/baudrate）
│   ├── transport.py            # 链路层 × 后端：仿真 / 网口 socket / 串口 / PyVISA（同一套 open·read·write·query）
│   ├── base.py                 # InstrumentDriver（会话/分发/能力/统一返回结构）
│   ├── family/scope.py         # 示波器家族契约（动作、单位、测量算法、默认仿真）
│   ├── vendors/                # 厂商驱动包（一厂一包，只填差异）
│   │   ├── tektronix_mso/      # 泰克 MSO54 / MSO56 / MSO58 / MSO64
│   │   └── rohde_schwarz_mxo/  # 罗德与施瓦茨 MXO44（系列俗称 MXO4 作为别名）
│   ├── factory.py              # 型号注册表：按型号精确派发（无打分、无模糊匹配、无兜底）
│   ├── endpoint.py             # 连接方式与后端层：LAN / SERIAL + native / visa 解析、校验、转 DeviceConfig
│   ├── api.py                  # 通用顶层接口：Scope 门面 + open_scope()（链路与后端对用户透明）
│   └── kit/                    # 开发工具链（不进运行时依赖）
│       ├── conformance.py      # 19 项一致性检查（含 C18 连接方式可声明 / C19 传输后端可声明）
│       ├── registry.py         # driver.json 清单校验 + 规格合并 + 型号派发
│       ├── fake.py             # 脚本化传输（无硬件也能测真机分支）
│       └── cli.py              # new / check / list / models / contract
├── samples/tek_mso5/           # 示例：泰克 MSO5 系列示波器驱动（可加载）
├── tests/test_sdk.py           # 11 项自测（契约、清单、派发、一致性、真机解析、CLI）
└── tests/test_scope_factory.py # 47 项自测（派发、连接方式、串口链路、双后端、命令不外露、两家同用例、门禁）
```

## 型号派发：不猜型号

型号来自**数据库设备档案**的 `model` 字段，注册表按它精确派发（归一化大小写与分隔符）：

```python
from ate_drivers import open_scope

dev = {"device_id": "SCOPE-TEK", "model": "MSO54",      # 档案里的确定型号
       "host": "192.168.10.41", "port": 4000}           # 网口端点

with open_scope(device=dev) as scope:                   # 型号 → 驱动，一次派发
    scope.auto_setup(freq_hz=1000.0, volts_pp=2.4)
    scope.set_timebase(seconds_per_div=5e-4)
    scope.set_channel("CH1", volts_per_div=0.5, coupling="DC")
    wave = scope.capture(points=1000)                   # 伏特 + 秒，没有命令
    for kind, item in scope.measure(("PK2PK", "FREQUENCY")).items():
        print(kind, item["value"], item["unit"])
```

* **没有打分**：权重、关键字命中、`priority` 抢位一律不存在（`score_spec` / `WEIGHTS` 已删除）；
* **没有兜底**：未登记型号报 `E_NOT_FOUND`，并把可用型号列出来；
* **别名显式登记**：`MODEL_ALIASES`（如 `MXO4` → `MXO44`）；
* **冲突可见**：同一型号被两个包声明时 `model_conflicts()` 会报出来。

## 通用顶层接口（用户不感知原始命令）

`ate_drivers.api.Scope` 只暴露业务方法；命令表、前导解析、缩放换算全部收在厂商包内，
`api.py` 源码里连一个命令字样都没有（单测会扫描它）。

| 方法 | 作用 |
| --- | --- |
| `connect()` / `close()` / `with` | 会话建立与安全退出 |
| `identity()` / `metadata()` / `status()` / `self_test()` | 身份、驱动版本、运行状态、连通自检 |
| `auto_setup(freq_hz=, volts_pp=)` | 一键自动设置 |
| `set_timebase(seconds_per_div=, offset_seconds=, divisions=)` | 水平系统 |
| `set_channel(channel=, volts_per_div=, coupling=, offset_volts=, probe_ratio=, enabled=)` | 垂直系统 |
| `configure(...)` | 一次配好水平 + 垂直 |
| `run()` / `stop()` / `single()` | 连续采集 / 停止 / 单次 |
| `capture(channel=, points=)` | 伏特序列 + 采样间隔 + 首点时刻 |
| `measure(items=, channel=, points=)` | `{kind: {value, unit}}`，单位恒为 SI |

**连接方式**：一台设备网口与串口都能接——网口给 `host` + `port`，串口给 `serial_port` + `baudrate`（帧格式默认 8N1，`bytesize`/`parity`/`stopbits` 可选）。该给的两个参数缺一个直接报 `E_CONFIG`；两种链路参数同时出现在档案里又没写明 `interface` → 报「连接方式不明确」，不替用户猜。`mode="auto"` 下给了端点即真机（按端点走网口或串口），都不给即仿真；连接后默认回读 `*IDN?` 与档案型号核对（`verify_model=False` 可关）。
都不给即仿真；连接后默认回读 `*IDN?` 与档案型号核对，不符立即报错（`verify_model=False` 可关）。

## 型号驱动只需要填差异面

| 差异面 | 位置 | 说明 |
| --- | --- | --- |
| ① 命令表 | `COMMANDS` | 只覆盖与家族默认不同的键；键名固定不可改 |
| ② 应答解析 | `PREAMBLE_FIELDS` / `_decode_preamble()` | 前导字段顺序按型号手册填名取值，不靠位置猜 |
| ③ 通道寻址 | `channel_token()` | 泰克 `CH1` 令牌 vs R&S `CHANnel1` 数字后缀 |
| ④ 枚举映射 | `MEASURE_TYPES` / `COUPLING_MAP` | `PK2Pk` vs `PDELta`、耦合取值差异 |
| ⑤ 仿真模型 | `_sim_signal()` | 无实机时让用例跑通；参数相同必须结果一致 |
| ⑥ 连接方式 | `INTERFACES` | `("LAN",)` 或 `("LAN", "SERIAL")`；与 `driver.json` 的 `interfaces` 一致 |

另外每个驱动必须声明**派发、链路与后端元数据**：`MODELS`（支持的具体型号）、`INTERFACES`（可接链路，`("LAN",)` / `("LAN", "SERIAL")`）、`BACKENDS`（可用收发栈，`("native",)` / `("native", "visa")`）、`vendor`。

```python
from ate_drivers import ScopeDriver

class AcmeScope3000(ScopeDriver):
    driver_key = "acme-scope-3000"
    family = "scope"
    vendor = "Acme"
    label = "Acme 3000 系列示波器"
    driver_version = "0.1.0"
    INTERFACE = "LAN"
    MODELS = ("ACME-3000",)
    COMMANDS = dict(ScopeDriver.COMMANDS, timebase_set=":TIM:SCAL {scale:g}")
    PREAMBLE_FIELDS = ("byt_nr", "bit_nr", "encdg", "bn_fmt", "byt_or", "wfid",
                       "nr_pt", "pt_fmt", "x_incr", "x_zero", "pt_off",
                       "y_off", "y_mult", "y_zero")
```

## 上架门禁：19 项一致性检查

`python -m ate_drivers.kit.cli check <包目录>` 必须 19/19 通过，覆盖契约风险（元数据、契约版本、
能力声明与实现是否一致、命令表完整性）、行为风险（仿真可执行全部能力、返回结构可序列化、
测量项带单位、错误可分类、会话可重入、安全退出、仿真可复现、实例互不干扰）、
**派发风险（C17：型号清单已声明、与 `driver.json` 的 `models` 一致；C18：连接方式已声明、在 `LAN`/`SERIAL` 支持范围内、能解析出真实端点、与 `driver.json` 的 `interfaces` 一致；C19：传输后端已声明、在 `native`/`visa` 支持范围内、每种后端在网口与串口都能解析到链路类、与 `driver.json` 的 `backends` 一致）**、
依赖风险（零第三方依赖、旧别名有效、弃用项有替代——`pyserial` 与 `pyvisa` 属「惰性可选依赖」，必须函数体内导入并留注入点，模块顶层 import 会被 C15 拦下）。

## 平台侧接入（型号派发）

```python
from ate_drivers.kit import scan_dir, merge_specs, resolve_spec

specs = merge_specs(DRIVER_SPECS, scan_dir("<ATE>/drivers"))  # 内置 + 已装包合成一张注册表
spec = resolve_spec(device_model, specs)                      # 按型号精确派发（无打分）
driver = resolve_entry(spec)(cfg, mode=mode)                  # 实例化（或直接用 open_scope）
```

## 兼容性铁律（驱动开发者必须遵守）

1. 只增不减：新增能力/动作/`detail` 键；不删除、不改名、不改语义。
2. 单位一律 SI 基本单位（V / A / s / Hz / Ω），不用 mV/ms 混写。
3. 新增参数必须可选并带默认值；位置参数顺序不得调整。
4. 破坏性变更必须升 `API_VERSION` 的 major，并提供 ≥1 个 minor 的弃用过渡。
5. 错误必须抛 `DriverError` 子类并带 `E_*` 错误码；上层不解析文案。
6. 仿真必须实现（`simulate: true`）且可复现；一致性检查会验证。
7. 连接方式在 `INTERFACES` 里声明、传输后端在 `BACKENDS` 里声明（`auto` 不许写进声明）；串口用 pyserial、VISA 用 pyvisa 时都必须**函数体内惰性导入**（否则门禁 C15 会拦），缺它时给出可读 `E_CONFIG`，也可用 `set_serial_factory()` / `set_visa_factory()` 注入。

详细字段说明、真机调试、常见错误与版本治理见 **`docs/driver-sdk-guide.html`**；
示波器型号派发与顶层接口设计见 **`docs/scope-factory-design.html`**。
