# atefr 模块设计（测试用例公共能力层）

版本：v1（草案）　日期：2026-11-21　工程：MockRunner（`sdk/atefr`）

## 一、定位

`atefr`（ATE Framework Runtime）是**测试用例唯一需要 import 的公共入口**，把三类能力收在一处：

| 能力 | 用例侧看到的样子 | 底层由谁实现 |
| --- | --- | --- |
| 日志 | `atefr.log.info/step/measure/fail` | 新增（薄层），输出接入 Runner 日志解析 |
| 归一化驱动 | `atefr.instrument("scope_main").measure_voltage()` | `sdk/ate_drivers`（契约 1.2） |
| 机器常数 | `atefr.constants.get("power.vdd")` | Runner `machine_constants`（已从 Controller 同步） |

三条约束：

1. **不重复造轮子**：`atefr` 不实现驱动协议、不自己读写设备、不直接查常数库，只做装配、归一与格式化。
2. **依赖单向**：`atefr → ate_drivers / machine_constants / ate_rt`，反向禁止；用例只依赖 `atefr`，不直接 import 底层库（底层库仍对外可用，但不再是推荐写法）。
3. **零第三方依赖**：与 `ate_rt`、`ate_drivers` 一致，只用标准库，保证离线现场可用。

## 二、现状盘点（决定设计边界）

| 现有件 | 现状 | 与本模块的关系 |
| --- | --- | --- |
| `conftest.py` 的 `ctx` | 提供 `command()` / `expect()` / `check()` / `record()` / `log()`，用例签名 `def case(ctx, ...)` | `atefr` 在 conftest 里 `attach(ctx)`，复用其判定与记录，不自建结果模型 |
| `ctx.log()` | 行首带 `[ERROR]/[WARNING]/[INFO]`，Runner 解析分级与进度 | `atefr.log` 输出必须兼容这一前缀 |
| `sdk/ate_drivers` | 契约 1.2，`connect(alias)` 按别名取句柄，型号派发、能力契约、错误码齐备 | `atefr.instrument()` 对 `connect()` 做能力归一与返回结构统一 |
| `sdk/ate_rt` | 实时数据发布，失败降级不抛错 | `atefr.log.measure()` 可顺带写一条 `ate_rt`，用开关控制 |
| `machine_constants` | `resolve_binding({version,pn,models})` 解析值 + `missing` 列表；`read_binding(tps_id)` 读 TPS 绑定 | `atefr.constants` 在其上加缓存、键路径、缺失策略、来源信息 |

## 三、包结构

```
sdk/atefr/
├── __init__.py          # 公共门面：log / instrument / constants / attach / version
├── log.py               # 统一日志：格式化、分级、上下文、文件与流输出
├── instrument.py        # 归一化驱动门面：别名解析、能力归一、返回结构
├── constants.py         # 机器常数：绑定、键路径、缺失策略、来源
├── result.py            # Measure / Waveform / Readback 数据结构
├── errors.py            # AtefrError 体系（包装底层错误码）
├── _runtime.py          # 运行时上下文（ctx、tps、task、bench 绑定）
├── pytest_plugin.py     # 可选：提供 atefr fixture，支持脱离 Runner 单测
└── README.md            # 用例作者看的一页纸
```

## 四、公共 API

### 4.1 装配（由 conftest 调用，用例一般不直接调）

```python
import atefr

atefr.attach(ctx)                       # 注入 ctx：决策、记录、TPS/任务信息
atefr.configure(
    log_file=None,                      # 默认 Runner 的运行日志，按 task 归档
    log_level="INFO",
    sim=False,                          # 全局仿真开关（调试用）
    constants_strict=True,              # 缺常数是否直接失败
)
```

`attach()` 之前调用任何能力都抛 `AtefrNotReady`，避免用例在错误上下文里静默跑。

### 4.2 日志

```python
from atefr import log

log.debug("连接参数", host="192.168.10.41", port=4000)
log.info("开始测项")
log.step("上电", voltage=3.3, unit="V")          # 步骤分隔，Runner 侧显示为步骤节点
log.measure("VDD", 3.312, unit="V", lo=3.2, hi=3.4)   # 测量值 + 判据，可选写 ate_rt
log.warn("读数抖动", std=0.02)
log.error("通信超时", alias="scope_main", timeout_s=5)
log.fail("VDD 超限", value=3.51, lo=3.2, hi=3.4)      # 记失败并抛 AtefrCheckFailed
```

**输出格式（唯一规范）**：

```
[INFO] 2026-11-21 18:20:31.482 spm_rh_dyn/C1 上电 voltage=3.3V
[INFO] 2026-11-21 18:20:32.104 spm_rh_dyn/C1 测量 VDD=3.312V  [3.2, 3.4] PASS
[ERROR] 2026-11-21 18:20:35.008 spm_rh_dyn/C1 通信超时 alias=scope_main timeout_s=5
```

- 行首 `[LEVEL]` 必须存在（Runner 按它分级、计数、筛选）；
- 时间到毫秒，本地时区；`用例/步骤` 来自 ctx，缺省为 `tps_id/-`；
- 键值对统一 `key=value`，数值带单位直接粘在值后（`3.3V`），字符串不加引号；
- 多行消息逐行加前缀，不出现裸换行；
- 文件日志（默认 `runs/<task>/atefr.log`）在流日志前多一列线程名与模块名，便于排障；JSON Lines 模式由 `log_format="json"` 开启（供后续日志分析用）。

**实现要点**：

- 单例 handler + 幂等安装，多次 `attach` 不重复输出（现有 `print` 与 `logging` 混用导致的重复行必须杜绝）；
- 线程安全（`threading.Lock` 包住写入）；
- `print()` 兼容：`atefr.log` 安装后把用例内的裸 `print` 也收进同一格式（`redirect_stdout=True`），避免日志里出现两种风格；
- 等级映射：`debug/info/warn/error/fail` → `DEBUG/INFO/WARNING/ERROR/ERROR`，`warn` 与 `error` 计入 Runner 的 WARNING/ERROR 统计。

### 4.3 归一化驱动

```python
from atefr import instrument

scope = instrument("scope_main")          # 别名来自 BOM 的 alias 字段
psu   = instrument("psu_1", sim=False)    # 单台覆盖仿真开关

psu.set_voltage(3.3)                      # 单位默认 V，可 set_voltage(3300, unit="mV")
psu.set_current_limit(0.5)
psu.output_on()

reading = psu.read_voltage()              # Readback(value=3.31, unit="V", ts=..., status="ok")
wave    = scope.capture(points=2000)      # Waveform(t0, dt, samples, unit="V", channel="CH1")
vpp     = scope.measure_vpp()

print(scope.info)                         # {"alias","model","family","capabilities","sim"}
```

**统一返回结构**（`result.py`）：

| 类型 | 字段 |
| --- | --- |
| `Readback` | `value: float`、`unit: str`、`ts: str`、`status: "ok"｜"sim"`、`raw: str`、`alias: str` |
| `Waveform` | `t0, dt, samples: list[float], unit, channel, points, alias` |
| `Status` | `ok: bool`、`code: str`、`detail: str` |

**能力归一表**（跨厂商同义方法 → 同一名字）：

| 家族 | 归一方法 | 说明 |
| --- | --- | --- |
| 示波器 | `capture` / `measure_vpp` / `measure_freq` / `set_timebase` / `set_channel` | 波形与常用测量 |
| 万用表 | `read_voltage` / `read_current` / `read_resistance` / `read_frequency` | 单次读数 |
| 电源 | `set_voltage` / `set_current_limit` / `output_on` / `output_off` / `read_voltage` / `read_current` | 程控源 |
| 电子负载 | `set_mode` / `set_load` / `input_on` / `input_off` / `read_voltage` / `read_current` | CC/CV/CR/CP |
| 信号源 | `set_frequency` / `set_amplitude` / `set_offset` / `output_on` / `output_off` | 连续波为主 |
| 串口设备 | `write` / `read` / `query` / `set_baudrate` | 设备自定义协议 |
| 夹具/工装 | 无归一方法 | 调用即 `UnsupportedCapability`，提示该类设备不参与程控 |

**别名解析顺序**（与 BOM 三要素衔接）：

1. BOM `alias` 精确匹配；
2. 未命中 → `name` 匹配（兼容旧 BOM）；
3. 仍未命中 → 同 `category`/`device_type` 唯一时自动选中，并在日志里 `warn` 一行提示「按类型唯一匹配到 X，建议改用别名」；
4. 多台同类型 → `AmbiguousDevice`，列出候选别名。

**单位处理**：入参接受 `unit=`，内部换算到家族基准单位（V/A/Ω/Hz/s），返回统一基准单位；换算失败抛 `UnitMismatch`。所有换算只在 `atefr` 做一次，驱动层保持原样。

**仿真**：`sim=True` 或环境变量 `ATE_SIM=1` 时走 `ate_drivers` 的仿真链路，`Readback.status="sim"`，日志行尾追加 `[SIM]`，避免把仿真数据误当实测。

### 4.4 机器常数

```python
from atefr import constants

c = constants.bind()                      # 读当前 TPS 的 constant_model 绑定（version + pn + models）
c.version, c.pn                            # 来源信息
vdd_nom = c.get("power.vdd_nom")           # model.field 键路径
vdd     = c.get("power.vdd_nom", default=3.3)
c.require(["power.vdd_nom", "power.idd_max"])   # 缺任一则抛 MissingConstant

c.info("power.vdd_nom")                    # {value, unit, source:"value|default", model, field, version, pn}
c.check("power.vdd_nom", 3.31)             # 用常数的 min/max 判定，返回 (ok, detail)
```

**解析链路**：`conftest` 已启动时同步过常数库（`constants_sync` → 本地 `ate.db`），`atefr.constants` 直接调用 `machine_constants.resolve_binding(binding)`，不发起 HTTP。

**缺失策略**：

| 策略 | 行为 |
| --- | --- |
| `strict`（默认） | 缺值抛 `MissingConstant`，并在消息里列出所有缺失字段 |
| `lenient` | 用字段 `default` 兜底，日志 `warn`，`info().source="default"` |

**缓存**：进程内按 `(version, pn)` 缓存解析结果；`atefr.constants.refresh()` 强制重读（长时间用例中途换版本时用）。

**校验**：取出值时校验类型（`float/int/bool/str`）与单位；`check()` 用常数模型的 `min/max`；越界不抛错，返回 `(False, detail)`，由用例决定是 `log.fail` 还是继续。

**审计**：`atefr.constants.used()` 返回本次用到的常量列表（字段、值、来源），conftest 收尾时写入报告，便于追溯「这条判定用了哪个版本的标定值」。

## 五、与 conftest / Runner 的集成

```python
# workspace/conftest.py（新增片段）
import atefr

@pytest.fixture(autouse=True)
def _atefr(ctx):
    atefr.attach(ctx)          # 注入 ctx / tps / task / bench
    yield
    atefr.detach()             # 收尾：flush 日志、写 used 常量
```

- `sys.path` 中已有 `sdk/`（conftest 现有逻辑），`import atefr` 无需额外配置；
- Runner 侧不改执行链路，`atefr` 输出的日志仍写 `run_log.txt`，Runer 解析逻辑不变；
- `atefr.pytest_plugin` 提供 `atefr` fixture，便于在 Runner 之外跑用例单测（无 ctx 时 `attach` 用内存 ctx 空实现）。

## 六、错误模型

```
AtefrError
├── AtefrNotReady          未 attach 就调用
├── DeviceError            设备相关（包装 ate_drivers 错误码）
│   ├── DeviceNotFound     别名不存在
│   ├── AmbiguousDevice    同类型多台
│   ├── UnsupportedCapability
│   ├── UnitMismatch
│   └── DriverTimeout
├── ConstantError          常数相关
│   ├── MissingConstant
│   └── ConstantTypeError
└── AtefrCheckFailed       log.fail / check 失败（带 value/lo/hi）
```

规则：错误码以 `E_` 前缀（`E_DEVICE_NOT_FOUND`），消息中文一句话 + `detail` 给排查线索；用例只需捕获 `AtefrError` 即可，底层异常在边界处转换，不泄漏第三方栈。

## 七、版本与兼容

| 项 | 约定 |
| --- | --- |
| 模块版本 | `atefr.__version__`，与 `ate_drivers.API_VERSION`（当前 1.2）在 `compat` 里声明兼容区间 |
| 弃用 | 方法先 `warn` 一轮再删，至少保留一个发布周期 |
| 用例兼容 | `ctx.command()` 继续可用（内部改走 `atefr.instrument`），迁移期双轨 |
| 打包 | `sdk/atefr` 随 Runner 发布，`pyproject.toml` 增加 include；不单独发 PyPI |

## 八、性能与稳定性

1. 句柄复用：`instrument()` 复用 `ate_drivers.connect()` 的句柄缓存，用例里反复调用不重连；
2. 日志缓冲：默认行缓冲直写，`log_file` 用 `flush` 保证崩溃前日志不丢；
3. 不吞异常：`ate_rt` 的降级策略只用于实时监视，日志与常数失败必须显式；
4. 超时：所有设备调用透传 `timeout`，默认 5s，可用 `atefr.configure(device_timeout=...)` 调整。

## 九、测试策略

| 层级 | 内容 |
| --- | --- |
| 单元 | 日志格式化（含多行、中文、单位）、键路径解析、缺失策略、单位换算表 |
| 契约 | 每个家族归一方法用仿真驱动跑通，断言返回结构字段 |
| 快照 | 固定输入下 `atefr.log` 输出与金标准文本逐行比对（防止格式漂移影响 Runner 解析） |
| 集成 | 一条真实 TPS（如 `spm_rh_dyn`）改用 `atefr` 后结果与原实现一致 |

## 十、分期落地

| 期 | 内容 | 验收 |
| --- | --- | --- |
| P0 | 包骨架 + `attach/configure` + 日志（格式、分级、去重、文件） | 用例改 `atefr.log` 后 Runner 日志分级正常、无重复行 |
| P1 | `constants`（绑定、键路径、strict/lenient、info、used） | 缺常数按策略表现正确；来源信息与 Controller 版本一致 |
| P2 | `instrument`（示波器/万用表/电源三类先上） + 别名解析 + 单位换算 | 仿真与真机各跑一条；返回结构一致 |
| P3 | 其余家族（电子负载/信号源/串口/夹具）+ `log.fail` + 报告审计 + pytest_plugin | 全家族仿真通过；失败信息可定位到字段与判据 |

## 十一、验收用例

| 用例 | 期望 |
| --- | --- |
| 未 `attach` 就 `log.info` | 抛 `AtefrNotReady`，提示先 attach |
| 同一用例连续 3 次 `attach` | 日志只输出一次，无重复 handler |
| `log.measure` 传 `lo/hi` 且值越界 | 输出 `FAIL` 行并计入 ERROR，`log.fail` 抛 `AtefrCheckFailed` |
| 别名不存在 | `DeviceNotFound`，消息列出可用别名 |
| 同类型两台、只给类型 | `AmbiguousDevice`，列出候选 |
| 电源 `set_voltage(3300, unit="mV")` | 实际下发 3.3V，日志显示 `3.3V` |
| 夹具调用 `capture` | `UnsupportedCapability`，提示该类设备不程控 |
| 常数缺失 + strict | `MissingConstant` 列出全部缺失键 |
| 常数缺失 + lenient | 用 default，日志 warn，`info().source="default"` |
| 仿真模式读数 | `Readback.status="sim"`，日志行尾 `[SIM]` |
| 日志含多行消息 | 每行都有 `[LEVEL] ts case/step` 前缀 |

## 十二、非目标

1. 不做测试编排（步骤、依赖、重试）——那是 TPS 与 Runner 的职责；
2. 不替代 `ate_drivers` 的驱动开发契约，也不允许用例绕开 `atefr` 直接用私有命令（除 `query` 显式降级口）；
3. 不做结果判定引擎，判据仍由 `testconfig` + `ctx.check/expect` 决定；
4. 不引入第三方日志库（loguru 等），保持零依赖。
