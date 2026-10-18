# 后端 driver / tool 模块拆分设计方案

> 目标：把「装备助手工具」与「可编程设备驱动」拆成两个独立模块。`tool` 面向装备助手的工具（示波器等），`driver` 面向可编程设备的驱动；`driver` 用工厂方法按后端注册信息选择具体实现，先落地示波器一族。

## 1. 现状与问题

当前 `backend/instrument_tools.py`（774 行）把三件事揉在一个类里：

| 现状 | 问题 |
| --- | --- |
| `ScopeSession` 同时管 socket 连接、SCPI 命令字、波形/测量解析、工具卡片 | 加一台非泰克示波器要改同一个类，容易碰坏已验证逻辑 |
| 真实/模拟两套分支（`_waveform_real` / `_waveform_sim`、`_measure_real` / `_measure_sim`） | 两条代码路径各自演化，模拟与实测行为漂移 |
| `_is_scope()` 用关键字猜设备是不是示波器 | 判定不权威，注册信息一变就误判 |
| 路由、会话字典、业务编排同处一个文件 | 单元测试必须拉起 FastAPI，无法只测驱动 |

## 2. 分层与依赖方向

约束：**依赖单向**，下层不认识上层。

```
tools/（装备助手工具层）   卡片清单 · 会话管理 · 动作编排 · FastAPI 路由
   │  只依赖抽象接口，不认识任何厂商 SCPI 命令
   ▼
drivers/（可编程设备驱动层）  DeviceDriver / ScopeDriver 抽象 + 具体实现 + 工厂
   │  只接收一个"设备描述字典"，不读注册库、不读 FastAPI
   ▼
drivers/transport/（传输层）  Socket / Serial / VISA(预留) / Simulate
   └─ 唯一与真实仪器打交道的地方；模拟模式只替换这一层
```

收益：驱动层可脱离 Web 单独单测；换厂商只加一个驱动类；新增工具只加卡片与编排，不动驱动。

## 3. 目录结构

```
backend/
├── drivers/
│   ├── __init__.py            # 对外只导出 get_driver / resolve_driver_class / DriverError 族
│   ├── base.py                # DeviceDriver 抽象基类（生命周期 + 能力声明 + 命令日志）
│   ├── models.py              # Waveform / Measurement / ChannelState 等数据类（纯标准库）
│   ├── errors.py              # DriverError 族
│   ├── factory.py             # DRIVER_SPECS 匹配表 + resolve_driver_class() + get_driver()
│   ├── transport/
│   │   ├── base.py            # Transport 协议：write / query / close / describe
│   │   ├── socket_transport.py# SCPI over raw socket（默认端口 4000 / 5025 / 30000）
│   │   ├── serial_transport.py# pyserial（未安装则明确报缺依赖）
│   │   ├── visa_transport.py  # 预留：pyvisa / VXI-11 真地址（TCPIP0::…::INSTR）
│   │   └── simulate.py        # SimulateTransport：命令 → 脚本化应答，含波形数据生成
│   ├── scopes/
│   │   ├── base_scope.py      # ScopeDriver：统一示波器契约 + 通用波形/测量计算
│   │   ├── tektronix_mso5.py  # 泰克 5/6 系 MSO（现 SCPI 方言迁移到这里）
│   │   ├── generic_scpi.py    # 兜底：SCPI 子集（IDN/RUN/STOP/AUTOSet/时基/通道）
│   │   ├── keysight_infiniivision.py   # 预留
│   │   └── rigol_mso5000.py            # 预留
│   ├── dmm/ psu/ eload/       # 同类扩展位（万用表 / 电源 / 电子负载）
│   └── README.md              # 如何接入一台新设备（写驱动类的 5 步清单）
├── tools/
│   ├── __init__.py
│   ├── base.py                # ToolBase：卡片元信息 + 能力声明 + 序列化
│   ├── cards.py               # TOOL_CARDS（沿用现有 4 张，每张声明 driver_kind）
│   ├── bindings.py            # 从注册库读设备 → 交给 driver 层判定可绑定项
│   ├── session.py             # SessionManager：会话键、RLock、空闲回收、命令日志
│   ├── oscilloscope_tool.py   # 示波器工具编排（动作 → 驱动方法 + 参数校验 + 中文错误）
│   ├── dmm_tool.py / psu_tool.py / eload_tool.py   # 预留
│   └── routes.py              # /api/tools 系列路由（路径与响应结构保持不变）
└── instrument_tools.py        # 保留为兼容 shim：一行转发到 tools.routes（1 个版本周期后删）
```

## 4. 传输层（transport）

| 传输 | 触发条件（来自注册信息） | 实现要点 |
| --- | --- | --- |
| `SocketTransport` | `interface=LAN`，协议含 raw socket / SCPI（泰克 4000、多数 5025） | `socket.create_connection`，行结束符 `\n`，读超时可覆盖；写命令与查询分离 |
| `SerialTransport` | `interface=SERIAL` | `serial_port` + `baudrate`；缺 pyserial 时抛 `DependencyMissing` |
| `VisaTransport`（预留） | `protocol` 含 VXI-11，或 `address` 形如 `TCPIP0::…::INSTR` | 走 pyvisa；缺失则降级为 Socket 并**在日志与界面标注实际通道** |
| `SimulateTransport` | `mode=simulate`，或真实连接失败且允许降级 | 内置命令→应答规则表；未知命令回 `0`/空并不报错；波形在传输层生成样本 |

关键决策：**模拟与真实共用同一个驱动类**，只在传输层分叉。命令序列、状态机、解析逻辑只有一份，模拟能真实地验证驱动逻辑，而不是验证另一套代码。

## 5. 驱动层（drivers）

### 5.1 抽象基类

```python
class DeviceDriver(ABC):
    kind: str                      # 'scope' | 'dmm' | 'psu' | 'eload'
    family: str                    # 'Tektronix MSO5' ...
    def connect(self) -> Identity  # 建立连接 + *IDN? 识别
    def close(self) -> None
    def identify(self) -> Identity # {idn, vendor, model, serial, firmware, transport}
    def capabilities(self) -> set[str]
    def command_log(self) -> list[dict]
```

```python
class ScopeDriver(DeviceDriver):
    def run(self); def stop(self); def single(self); def autoset(self)
    def get_timebase(self) -> float; def set_timebase(self, scale, position=None)
    def get_channels(self) -> dict[str, ChannelState]
    def set_channel(self, ch, *, display=None, scale=None, offset=None, coupling=None)
    def acquire_waveform(self, ch, points) -> Waveform
    def measure(self, ch, types) -> list[Measurement]
```

- **通用实现在 `base_scope.py`**：`measure()` 默认先用仪器原生测量（`MEASUrement:ADDMEAS` + `MEAS<x>:VALUE?`），单点失败回退到「用取回的波形自算」（Vpp / RMS / 频率 / 周期 / 均值 / 上升时间），并标注 `source=instrument|derived`。
- **厂商类只写方言**：命令字、波形前导解析、通道命名上限（4/8 通道）。
- 数据类全部用标准库 `dataclass`，**不引入 numpy**：`Waveform(samples, x_incr, x_zero, points, source, channel, y_unit)`。
- 能力协商：`capabilities()` 返回 `{'waveform','autoset','measure','channel_coupling','timebase'}` 等，工具层据此在动作前拒绝不支持的操作（中文原因 + 该型号实际能力），而不是让仪器返回难懂的 SCPI 错误。

### 5.2 工厂与选择算法

```python
DRIVER_SPECS = [
  DriverSpec(TektronixMSO5Scope, kind='scope', priority=100,
             vendor=r'tektronix|tek\b', model=r'mso\s?(4|5|6)|dpo\s?(5|7)|mdo',
             protocol=r'vxi-11|scpi|socket', transport='socket'),
  DriverSpec(GenericScpiScope, kind='scope', priority=10,
             category=r'示波器|oscilloscope', transport='socket'),
  ...
]

def resolve_driver_class(dev: dict) -> type[DeviceDriver]
def get_driver(dev: dict, mode='real', timeout=2.0, allow_fallback=True) -> DeviceDriver
```

选择顺序（先具体后兜底）：

```
① 注册信息显式指定 driver 字段（现场特殊机型的手工覆盖，最高优先级）
② vendor + model 正则匹配（两者拼接后统一小写匹配，兼容 "Tektronix MSO54" 这种合并写法）
③ category / protocol 关键字兜底（如 "示波器" → generic_scpi）
④ 全部不中 → 抛 DriverNotFound，并返回"当前支持的型号清单"中文提示
```

匹配结果示例（用现有 6 个预设的真实 BOM）：

| 注册设备（预设 BOM） | 判定依据 | 选中的驱动 | 传输 |
| --- | --- | --- | --- |
| `tek-mso54` Tektronix MSO54 · LAN:4000 | vendor=tektronix + model=mso5 | `tektronix_mso5.TektronixMSO5Scope` | Socket |
| 未知品牌 LAN 示波器 | category=示波器 | `generic_scpi.GenericScpiScope` | Socket |
| `itech-it6332a` ITECH IT6332A · LAN:30000 | kind=psu，驱动未实现 | 无 → 明确提示"电源驱动待接入" | — |
| `fluke-8846a` Fluke 8846A · LAN:3490 | kind=dmm，预留 | 后续 `fluke_8846a_dmm.DmmDriver` | Socket |
| `uart-dut` FTDI USB-TTL · SERIAL COM3 | interface=SERIAL | 串口驱动（DUT 通信，非仪器） | Serial |
| 任意设备 + `mode=simulate` | 模式优先 | **同一驱动类** | Simulate |

### 5.3 与注册信息的关系

驱动层**不持久化配置**：注册库（`testbenches.json`）是唯一事实源，工厂只吃一个字典：

```json
{"id":"tek-mso54","vendor":"Tektronix","model":"Tektronix MSO54","category":"示波器",
 "interface":"LAN","host":"192.168.10.21","port":4000,"protocol":"VXI-11 (SCPI)",
 "address":"TCPIP0::192.168.10.21::inst0::INSTR"}
```

好处：装备属性配置页改完 IP 立即生效（下次建会话自然读到新值），驱动层无需感知持久化。

## 6. 工具层（tools）

| 文件 | 职责 |
| --- | --- |
| `base.py` | `ToolBase`：id / name / subtitle / desc / protocol / capabilities / available / `driver_kind` |
| `cards.py` | 卡片清单数据（现有 4 张：数字示波器可用，万用表 / 电源 / 电子负载规划中），新增工具 = 加一条记录 |
| `bindings.py` | 读注册库 → 对每台设备调 `drivers.kind_of(dev)` 判定可绑定项（替代现有 `_is_scope` 关键字猜测），示波器优先排序 |
| `session.py` | `SessionManager`：键 `tool_id:bench_id:device_id`；每会话一把 `RLock`；`idle_ttl=600s` 惰性回收（访问时清理，不起后台线程）；驱动命令日志环形缓冲 200 条 |
| `oscilloscope_tool.py` | 动作编排：`identify/run/stop/single/autoset/timebase/channel/acquire/measure/frame/close` → 驱动方法；参数校验、单位换算、中文错误；响应结构保持 `{success, message, result, log, state}` |
| `routes.py` | `register_routes(app, tps_dir)`，路径与现有 7 个接口完全一致 |

## 7. 接口契约（前端零改动）

| 方法 | 路径 | 变化 |
| --- | --- | --- |
| GET | `/api/tools` | 不变；卡片新增可选字段 `driver_kind` |
| GET | `/api/tools/oscilloscope/bindings` | 不变；`is_scope` 改由驱动层判定（更准） |
| POST | `/api/tools/oscilloscope/connect` | 响应新增 `driver`（驱动类名）与 `transport`（实际通道类型） |
| POST | `/api/tools/oscilloscope/action` | 不变；失败时新增 `code` 字段便于前端分支 |
| GET | `/api/tools/oscilloscope/state` | 不变；新增 `driver` / `transport` |

新增字段均为**追加**，老前端忽略即可；`success` 语义与 HTTP 状态码维持现状（业务失败仍 200 + `success:false`）。

## 8. 并发与超时

- FastAPI 同步路由跑在线程池：**驱动实例自身不加锁**，由 `SessionManager` 对同一设备串行化命令；不同设备天然并行。
- 超时统一在传输层：普通查询 2s（可被请求覆盖），波形查询 5s，连接 2s（沿用现有 `min(timeout, 0.3)` 的快速探测思路只在自检模块保留）。
- 会话空闲 10 分钟自动断开，避免占着仪器的 socket 不放。

## 9. 错误模型

| 异常 | 触发 | 工具层返回 |
| --- | --- | --- |
| `DriverNotFound` | 型号未登记且无兜底驱动 | "未找到 XX 的驱动，当前支持：…" |
| `DependencyMissing` | 串口驱动缺 pyserial | "缺少 pyserial，请 pip install pyserial" |
| `NotConnected` | 未建会话就操作 | "尚未连接设备，请先运行工具并连接" |
| `Timeout` | SCPI 无应答 | "仪器无响应（超时 Xs），请检查 IP/端口" |
| `Unsupported` | 动作超出 `capabilities()` | "该型号不支持自动设置" |
| `ProtocolError` | 应答无法解析 | 原样带响应片段，便于排查 |

## 10. 迁移步骤（每步独立可验证）

<div class="step">1</div> 抽 `transport/`：把 socket 与模拟收发从 `ScopeSession` 搬出，**只搬运不改行为** → 跑现有 `test_tools.py`，应全绿。

<div class="step">2</div> 抽 `ScopeDriver` + `tektronix_mso5.py`：迁移 SCPI 方言与波形解析；`_waveform_real`/`_waveform_sim` 合并为「驱动 + SimulateTransport」，模拟样本生成下沉到传输层 → 模拟频率 1.000 MHz 等既有断言保持通过。

<div class="step">3</div> 建 `factory.py` + `DRIVER_SPECS`：表驱动单测（型号 → 驱动类），覆盖合并 model 串、未知型号、显式覆盖三种情形。

<div class="step">4</div> 建 `tools/*`：迁移卡片、会话、路由；`instrument_tools.py` 转 shim，`main.py` 改挂 `tools.routes` → 12 个既有接口回归 + 装备助手端到端。

<div class="step">5</div> 收尾：预设 JSON 增加可选 `driver` 字段说明；`drivers/README.md` 写"接入新设备 5 步"；更新架构文档。

## 11. 测试计划

- 单测（新增 `backend/tests/`，pytest）：工厂匹配表（表驱动）、波形前导解析（固定样本）、SimulateTransport 命令回放、`capabilities()` 拒绝路径、同设备命令串行化。
- 端到端：沿用现有 `test_tools.py`（注册测试台 → 绑定 → 模拟全流程 → 真实模式降级）。
- 回归：既有 12 个接口（测试执行 / 记录 / 装备属性配置 / 测试台注册）。
- 一条硬指标：**拆分前后，同一套端到端脚本的断言必须逐条不变地通过**（模拟模式下测量值、波形点数、日志条数一致）。

## 12. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 预设里 `protocol` 写 VXI-11 但实现走 raw socket | 传输层显式映射并把**实际通道类型**回传前端与日志，不靠字符串自欺 |
| `model` 是合并串（"Tektronix MSO54"） | 匹配时用 `vendor + model` 拼接后统一小写再正则，不依赖字段拆分 |
| pyserial / pyvisa 未必装 | 构造传输时抛 `DependencyMissing`，工具层转"跳过/待补依赖"提示，不阻断自检 |
| 拆分期间老代码仍被 import | 保留 `instrument_tools.py` shim 一个版本周期，`main.py` 一次性改挂 |
| 模拟与真实再次漂移 | 硬约束：模拟只许改传输层，驱动层出现 `if simulated:` 分支视为设计违规（代码评审拦） |
| 零依赖约束 | 驱动与工具层只用标准库（socket / threading / dataclasses），pyserial、pyvisa 均为可选 |

## 13. 后续扩展

- 驱动能力表驱动新增设备，接入一台新仪器 = 一条 `DriverSpec` + 一个驱动类（呼应"仪器说明书喂 AI 自动生成适配层"的探索方向）。
- 驱动注册信息可直接生成 `equipment_demo.xml`，与测试执行侧的装备清单打通。
- 示波器工具稳定后，`tools/` 复用到万用表 / 电源 / 电子负载，并演进为"多设备协同的测试动作编排"。
