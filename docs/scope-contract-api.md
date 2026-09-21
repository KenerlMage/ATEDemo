# 示波器契约层接口设计（API 参考）

对象：`ate_drivers.family.scope.ScopeDriver`（`ate.driver.api` **1.2**）。每个接口的签名、参数、返回结构、单位与错误码都在下面；**全部签名与返回值由反射 + 仿真实跑导出**（`.openclaw/tmp/ate/recon/scope_api.txt`、`scope_returns.txt`），不是手写。

型号驱动继承它、只填三处差异即可（见《驱动库开发模板与使用说明》）；TPS 用例与装备助手只依赖这张表，因此换品牌时用例零改动。

## 契约常量

| 常量 | 类型 | 值 | 作用 |
| --- | --- | --- | --- |

| `CONTRACT_NAME` | str | `"ate.driver.api"` | 契约名（写进驱动清单与运行记录） |
| `API_VERSION` | str | `"1.2"` | 契约版本；驱动声明的 major 必须与平台相等 |
| `RESULT_SCHEMA` | str | `"ate.driver.result.v1"` | 统一返回结构的 schema |
| `WAVEFORM_SCHEMA` | str | `"ate.driver.waveform.v1"` | 波形对象的 schema（`value.schema`） |
| `MANIFEST_SCHEMA` | str | `"ate.driver.manifest.v1"` | 驱动清单 schema（`meta()` 输出同形） |
| `FROZEN_ACTIONS` | dict | `{"scope.get_waveform": "scope.acquire_waveform"}` | 已冻结的旧动作名映射（不可再改） |
| `DEPRECATIONS` | dict | `{}` | 弃用登记：动作 → 替代动作/移除版本（当前为空） |

## 能力与家族基线

能力名 = `<家族>.<动词>`；`identify` / `reset` / `state` 是所有驱动的核心能力，示波器家族的**必选**能力是 `scope.acquire_waveform` 与 `scope.measure`。

| 能力 | 处理方法 | 要求 | 说明 |
| --- | --- | --- | --- |

| `identify` | `identify` | 核心·必选 | 读仪器标识串（`*IDN?`） |
| `reset` | `reset` | 核心·必选 | 复位到已知状态（`*RST` + `*OPC?` 等同步） |
| `state` | `state` | 核心·必选 | 取当前状态快照（设备/链路/时基/通道/仿真信号） |
| `scope.acquire_waveform` | `acquire_waveform` | 家族·必选 | 取一段波形：采样点 + 时间轴 + 单位 |
| `scope.measure` | `measure` | 家族·必选 | 测量项（8 项，单位固定） |
| `scope.timebase` | `timebase` | 家族·可选 | 时基档位 / 屏内格数 / 水平偏移，读改同一接口 |
| `scope.channel` | `channel` | 家族·可选 | 通道开关 / 垂直档位 / 耦合 / 偏置 / 探头比 |
| `scope.autoset` | `autoset` | 家族·可选 | 自动设置（按已知信号参数的确定性档位） |
| `scope.run` | `run` | 家族·可选 | 持续采集 |
| `scope.stop` | `stop` | 家族·可选 | 停止采集 |
| `scope.single` | `single` | 家族·可选 | 单次采集 |
| `vendor.<厂商>.<动作>` | `型号自定义` | 扩展·可选 | 厂商私有能力，必须带 `vendor.` 前缀（平台不解释） |

## 接口签名总表（核心动作）

### `identify`

```python
def identify(self) -> dict
```

- **参数**：无

- **返回 `value`**：`str` — 标识串（仿真返回 `sim_idn`）

- **单位**：—

- **可能错误**：`E_TRANSPORT` `E_TIMEOUT`

### `reset`

```python
def reset(self) -> dict
```

- **参数**：无

- **返回 `value`**：`"OK"`（真机为 `*OPC?` 应答）

- **单位**：—

- **可能错误**：`E_TRANSPORT` `E_TIMEOUT`

### `state`

```python
def state(self) -> dict
```

- **参数**：无

- **返回 `value`**：`dict` — `device_id` / `resource` / `driver*` / `connected` / `idn` / `commands` / `capabilities` / `transport` / `timebase` / `channels` / `running` / `single` / `sim_signal` / `missing_commands`

- **单位**：内含 s/div、V/div

- **可能错误**：—

### `scope.timebase`

```python
def timebase(self, scale_s_per_div: float | None = None,
             divisions: int | None = None,
             offset_s: float | None = None, **_) -> dict
```

- **参数**：三个参数**都可选**，只传要改的；不传即读

- **返回 `value`**：`TimebaseSetup.to_dict()` → `{scale_s_per_div, divisions, offset_s, window_s, unit}`

- **单位**：s/div、s

- **可能错误**：`E_PARAM`（≤0、格数 <2）

### `scope.channel`

```python
def channel(self, name: str = "CH1",
            enabled: bool | None = None,
            scale_v_per_div: float | None = None,
            coupling: str | None = None,
            offset_v: float | None = None,
            probe_ratio: float | None = None, **_) -> dict
```

- **参数**：`coupling` ∈ `DC|AC|GND`；`scale_v_per_div` > 0

- **返回 `value`**：`ChannelSetup.to_dict()` → `{name, enabled, scale_v_per_div, coupling, offset_v, probe_ratio, unit}`

- **单位**：V/div、V

- **可能错误**：`E_PARAM`

### `scope.autoset`

```python
def autoset(self, kind: str | None = None,
            freq_hz: float | None = None,
            vpp_v: float | None = None, **_) -> dict
```

- **参数**：`kind` ∈ `sine|square|triangle|sawtooth|dc|noise`；`freq_hz` > 0；`vpp_v` > 0

- **返回 `value`**：`{timebase: {...}, sim_signal: {...}}`

- **单位**：s/div、Hz、V

- **可能错误**：`E_PARAM`

### `scope.acquire_waveform`

```python
def acquire_waveform(self, points: int = 1000,
                     channel: str = "CH1", **_) -> dict
```

- **参数**：`points` 采样点数（按当前时基换算时间轴）

- **返回 `value`**：`Waveform.to_dict()` → `{schema, channel, points, samples, x_start_s, x_incr_s, unit, source}`

- **单位**：V（采样值）、s（时间轴）

- **可能错误**：`E_PROTOCOL` `E_TRANSPORT` `E_TIMEOUT`

### `scope.measure`

```python
def measure(self, items: Optional[list] = None,
            channel: str = "CH1",
            points: int = 2000, **_) -> dict
```

- **参数**：`items` ⊆ `MEASURE_ITEMS`；默认 `(PK2PK, FREQUENCY, MEAN, RMS)`

- **返回 `value`**：`list[Measurement.to_dict()]` → `[{type, value, unit, source}, …]`

- **单位**：V / Hz / s

- **可能错误**：`E_PARAM`（未知测量项）

### `scope.run`

```python
def run(self, **_) -> dict
```

- **参数**：无

- **返回 `value`**：`True`（运行中）

- **单位**：—

- **可能错误**：—

### `scope.stop`

```python
def stop(self, **_) -> dict
```

- **参数**：无

- **返回 `value`**：`False`（已停止）

- **单位**：—

- **可能错误**：—

### `scope.single`

```python
def single(self, **_) -> dict
```

- **参数**：无

- **返回 `value`**：`True`；`detail = {"single": true}`

- **单位**：—

- **可能错误**：—

## 数据模型

| 模型 | 签名 | 说明 |
| --- | --- | --- |

| `TimebaseSetup` | `(scale_s_per_div: float, divisions: int = 10, offset_s: float = 0.0)` | `window_s` 属性 = 格数 × 档位；`to_dict()` 附 `unit="s/div"` |
| `ChannelSetup` | `(name: str = "CH1", enabled: bool = True, scale_v_per_div: float = 0.5, coupling: str = "DC", offset_v: float = 0.0, probe_ratio: float = 1.0)` | `to_dict()` 附 `unit="V/div"` |
| `Waveform` | `(channel: str, samples: tuple[float, ...], x_incr_s: float, x_start_s: float = 0.0, unit: str = "V", source: str = "simulate")` | `points` = 采样点数；`to_dict()` 附 `schema="ate.driver.waveform.v1"` |
| `Measurement` | `(kind: str, value: Optional[float], unit: str, quality: str = "good")` | `to_dict()` 序列化成 `{"type": kind, "value", "unit", "source"}`（报告里字段名是 `type`） |
| `DeviceConfig` | `(bench_id="", device_id="", name="", model="", vendor="", category="", role="", interface="NONE", protocol="", host="", port=None, serial_port="", baudrate=None, address="", channel="", programmable=False, required=True, configured=False, timeout=2.0, note="", extra=<factory>)` | 与平台注册库字段一对一；`timeout` 是默认读写超时（秒） |

## 基类通用接口

| 分组 | 签名 | 说明 |
| --- | --- | --- |

| 会话 | `def open(self) -> dict` | 建立链路并取 IDN；返回 `{ok, device_id, alias, driver, driver_version, api, mode, transport, resource, idn, simulated}` |
| 会话 | `def close(self) -> dict` | **先下发安全退出命令**（`protect_on_close` 登记的），再关链路；返回 `{ok, device_id, commands}` |
| 会话 | `def ensure_open(self) -> InstrumentDriver` | 未开则开，返回 `self`（可链式：`dev.ensure_open().do(...)`） |
| 会话 | `def protect_on_close(self, command: str) -> None` | 登记会话结束时要下发的命令（如 `:OUTP OFF`、停止采集）；重复登记自动去重 |
| 分发 | `def do(self, action: str, **params) -> dict` | 执行动作 → 统一返回结构；失败抛 `DriverError` 子类 |
| 分发 | `def resolve(self, action: str) -> str` | 动作名 → 处理方法名；未登记抛 `E_UNSUPPORTED`（detail 列出可用动作） |
| 分发 | `def normalize_action(self, action: str) -> str` | 旧名/别名 → 规范能力名（`waveform` → `scope.acquire_waveform`） |
| 能力 | `def supports(self, capability: str) -> bool` | 支持 `scope.*` 通配（新能力加入时通配调用不失效） |
| 能力 | `def require(self, capability: str) -> None` | 不支持则抛 `E_UNSUPPORTED`（detail 列出已声明能力） |
| 能力 | `def capabilities(self) -> list[str]` | 已声明能力（排序后） |
| 能力 | `def actions(self) -> list[str]` | 全部可调用动作名（规范名 + 别名） |
| 自省 | `def meta(self) -> dict` | 驱动自描述，形如清单：`schema/key/version/api/family/label/capabilities/actions/simulate/class` |
| 自省 | `def contract_report(self) -> dict` | 契约快照：`missing_required` / `unknown` / `vendor_extensions` / `known_capabilities` |
| 自省 | `def validate_declaration(self) -> tuple[list, list, list]` | `(缺失的家族基线能力, 平台不认识的能力, 厂商扩展能力)` |
| 自省 | `def missing_commands(self) -> list[str]` | 未覆盖的**必需命令键**（型号驱动漏填在这里暴露） |
| 自省 | `def log_lines(self) -> list[str]` | 本次会话实际下发的命令序列（排查用） |
| 链路 | `def write(self, command: str) -> None` | 写命令；链路异常统一包装成 `DriverError` 并带 `[alias]` 前缀 |
| 链路 | `def query(self, command: str, timeout: Optional[float] = None) -> str` | 写 + 读，返回去空白后的应答 |
| 链路 | `def read(self, timeout: Optional[float] = None) -> str` | 只读 |
| 链路 | `def cmd(self, key: str, **params) -> str` | 按 `COMMANDS` 表渲染命令（型号驱动只改表，动作代码不动） |
| 仿真 | `def simulate_command(self, command: str) -> str` | 仿真模式下对任意命令的应答；型号驱动按自己协议覆盖 |
| 换算 | `@staticmethod auto_v_per_div(vpp_v: float) -> float` | 从 `V_DIV_STEPS` 选不溢出的最接近档位（家族级，跨品牌同口径） |

## 家族算法与枚举常量

| 函数 | 说明 |
| --- | --- |

| `sim_samples(kind, freq_hz, vpp_v, offset_v, points, window_s, noise, phase=0.0) -> list[float]` | 家族默认仿真模型：按信号参数生成确定性采样（同参数同结果）；`noise` 用参数派生种子 |
| `measure_samples(samples, x_incr_s, freq_hint_hz=0.0) -> dict` | 家族测量算法：对采样点自算全部 8 项（不依赖仪器 `:MEASure`），跨品牌口径一致 |
| `ScopeDriver.auto_v_per_div(vpp_v) -> float` | 垂直档位优选：从 `V_DIV_STEPS` 里选档 |

| 常量 | 值 |
| --- | --- |

| `MEASURE_ITEMS` | `("PK2PK", "AMPLITUDE", "FREQUENCY", "PERIOD", "MEAN", "RMS", "RISE", "FALL")` |
| `COUPLINGS` | `("DC", "AC", "GND")` |
| `WAVEFORM_KINDS` | `("sine", "square", "triangle", "sawtooth", "dc", "noise")` |
| `V_DIV_STEPS` | `(0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)` |
| `DEFAULT_PREAMBLE` | `"0;2;1;0;0;0;0;1.0E-3;0.0;128.0;1"`（前导字段的通用兜底顺序） |
| `DEFAULT_CHANNEL` | `"CH1"` |

## 型号驱动可覆盖点

| 覆盖点 | 类型 | 说明 |
| --- | --- | --- |

| `COMMANDS` | dict | 命令表：只覆盖与家族不同的键（**键名固定**）；`REQUIRED_COMMANDS` 里的键必须都在 |
| `PREAMBLE_FIELDS` | tuple | 波形前导字段名顺序；**填了就按名取值**，留空则退化为 `DEFAULT_PREAMBLE` 位置启发式 |
| `_sim_signal()` | 方法 | 仿真模型：通常在 `super()._sim_signal()` 上叠加厂商特征（量化位宽、噪声底） |
| `simulate_command()` | 方法 | 仿真模式下对任意命令的应答（型号私有命令必须实现，否则仿真跑不通） |
| `driver_key` / `label` / `driver_version` / `sim_idn` | str | 驱动标识与自描述（写进清单、报告与运行记录） |
| `ALIASES` | dict | 额外动作别名（家族已有 9 个：`idn/autoset/timebase/channel/waveform/measure/run/stop/single`） |
| `CAPABILITIES` | tuple | 追加厂商能力时用 `vendor.` 前缀；**不得删掉家族基线能力** |

## 统一返回结构（16 键）

| 键 | 类型 | 说明 |
| --- | --- | --- |

| `ok` | bool | 恒为 `true`（失败抛异常，不返回 `ok=false`） |
| `schema` | str | `ate.driver.result.v1`，上层按它判兼容 |
| `device_id` / `alias` | str | 设备标识与报告里用的别名 |
| `driver` / `driver_version` / `api` | str | **必须落进运行记录**（可复现） |
| `action` / `requested` | str | 规范能力名 / 调用方原名 |
| `value` | any | 动作主结果（必须可 JSON 序列化） |
| `detail` | dict | 附加信息，只增键 |
| `quality` | str | `good` / `approximate` / `simulated` |
| `source` / `simulated` | str / bool | `instrument` / `simulate` |
| `elapsed_ms` / `warnings` | int / list | 耗时与告警 |

## 错误码

| 错误码 | 异常类 | 触发场景 |
| --- | --- | --- |

| `E_DRIVER` | `DriverError` | 驱动通用错误（基类）；`to_dict()` 给结构化输出 |
| `E_PARAM` | `DriverError(code="E_PARAM")` | 参数非法：枚举越界、≤0、未知测量项；`detail` 列出可选值 |
| `E_CONFIG` | `ConfigurationError` | 连接参数缺失或非法（缺 IP / 端口 / 串口） |
| `E_NOT_FOUND` | `DriverNotFound` | 注册库里找不到驱动实现，或设备未登记 |
| `E_TRANSPORT` | `TransportError` | 链路错误：连不上、写失败、串口打不开 |
| `E_TIMEOUT` | `DriverTimeout` | 读写超时（继承 `TransportError`，因为可重试） |
| `E_PROTOCOL` | `ProtocolError` | 应答不符合协议（前导解析不出、曲线为空） |
| `E_UNSUPPORTED` | `UnsupportedCapability` | 动作未登记或能力未声明；`detail` 列出可用动作 / 已声明能力 |
| `E_CONTRACT` | `ContractMismatch` | 契约版本不兼容（加载阶段即拒绝） |

## 实测返回样例

**scope.timebase（只读）**

```json
{
  "ok": true, "schema": "ate.driver.result.v1",
  "driver": "generic-scope", "driver_version": "1.0.0", "api": "1.2",
  "action": "scope.timebase", "requested": "scope.timebase",
  "value": {"scale_s_per_div": 0.0005, "divisions": 10, "offset_s": 0.0,
            "window_s": 0.005, "unit": "s/div"},
  "detail": {}, "quality": "simulated", "source": "simulate", "simulated": true,
  "elapsed_ms": 0, "warnings": []
}
```

**scope.acquire_waveform（value 只摘录前 3 点）**

```json
{
  "value": {
    "schema": "ate.driver.waveform.v1", "channel": "CH1", "points": 200,
    "samples": [0.022949, 0.208294, 0.379139, "… 共 200 项"],
    "x_start_s": 0.0, "x_incr_s": 2.5125628140703518e-05,
    "unit": "V", "source": "simulate"
  },
  "detail": {"channel": "CH1", "unit": "V", "timebase": {...}, "sim_signal": {...}}
}
```

**scope.measure（8 项）**

```json
{
  "value": [
    {"type": "PK2PK",     "value": 2.44667,      "unit": "V",  "source": "simulate"},
    {"type": "AMPLITUDE", "value": 2.398504,     "unit": "V",  "source": "simulate"},
    {"type": "MEAN",      "value": 0.000134,     "unit": "V",  "source": "simulate"},
    {"type": "RMS",       "value": 0.848366,     "unit": "V",  "source": "simulate"},
    {"type": "FREQUENCY", "value": 1002.005013,  "unit": "Hz", "source": "simulate"},
    {"type": "PERIOD",    "value": 0.000997998999, "unit": "s","source": "simulate"},
    {"type": "RISE",      "value": 0.000500250125, "unit": "s","source": "simulate"},
    {"type": "FALL",      "value": 0.000500250125, "unit": "s","source": "simulate"}
  ],
  "detail": {"measurements": [ "同上 8 项" ], "channel": "CH1"}
}
```

**错误对象（`DriverError.to_dict()`）**

```json
{
  "code": "E_PARAM",
  "error": "不支持的测量项: NOPE",
  "device_id": "rh-scope",
  "detail": "可选: PK2PK, AMPLITUDE, FREQUENCY, PERIOD, MEAN, RMS, RISE, FALL"
}
```

**旧别名调用 do('waveform')**

```json
{
  "action": "scope.acquire_waveform",   // 规范化后的能力名
  "requested": "waveform",             // 调用方写的原名，可追溯
  "warnings": []
}
```

## 设计取舍（明确不做的事）

| 取舍 | 理由 | 需要时怎么办 |
| --- | --- | --- |

| **参数只校验合法性，不校验仪器量程** | `scale_s_per_div=1e9` 不会在契约层被拒（只拦 ≤0）。各型号量程不同，契约层不做假校验；超量程由仪器回 NACK 或截断，驱动按 `E_PROTOCOL` 暴露。 | 需要提前拦住时：在型号驱动里覆盖 `timebase()` 加上该型号的量程判断，仍抛 `E_PARAM`。 |
| **动作方法带 `**_`，多余参数被静默忽略** | 换来的是**向后兼容**：家族新增参数不会让老调用报错；代价是参数名拼错也不报错。 | 调用侧统一走 `do(action, **params)`；拼写检查交给 TPS 用例的静态检查或编辑器插件。 |
| **`scope.measure` 一律由采样点自算** | 跨品牌口径一致（同一份用例在泰克/是德/普源上判定一致），代价是与仪器内部测量（开了平均/带宽限制）可能有偏差。 | 确需仪器口径时新增独立动作（如 `scope.measure_instrument`），**不修改** `scope.measure` 语义。 |
| **仿真模式下 `identify()`/`reset()` 不经过链路** | 无实机也能跑通用例（产线多数时间没有仪器）；这两个动作在仿真下直接返回 `sim_idn` / `"OK"`。 | 需要验证真机分支时用 `mode="real"` + `FakeTransport`，真实走命令与解析。 |
