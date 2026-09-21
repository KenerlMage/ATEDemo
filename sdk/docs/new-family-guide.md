# ATE Runner 仪器驱动库 · 新设备家族接入指南

> 适用版本：`ate_drivers` 1.2.0 · 契约 **1.2** · 门禁 19 项
> 本文回答一个具体问题：**以后要接万用表、电源、信号源、继电器、运动控制、以及完全没见过的设备，通用驱动该怎么设计？**
> 结论先给：**别为新设备写新的一整套，先判断它属于「已有家族的新型号」还是「新家族」——前者只填五个差异面，后者才需要在家族层加一份契约。**

## 1. 先做判断（决策树）

```
新设备要接进来
 ├─ 1. 它属于已有家族吗？（能完成同一类语义动作）
 │     · 示波器 → 采波形 + 测量  → 属于 scope 家族
 │     · 万用表 → 读一个量       → 属于 dmm 家族（能力位已预留，契约未落地）
 │     · 电源   → 设输出 + 回读   → 属于 psu 家族（同上）
 │     · 信号源 → 设波形 + 开关   → 属于 awg 家族（同上）
 │     · 运动机构/气缸/转台 → 移动 + 读位置 → 属于 motion 家族（同上）
 │     · 无源件（夹具、线缆、衰减器）→ passive 家族（无动作）
 │     ▼ 是（同一家族）
 │        → 走【情形 A】只写型号驱动包：填五个差异面 + 过 19 项门禁（半天到一天）
 │     ▼ 不是，且家族能力表里没有
 │        → 走【情形 B】先设计家族契约，再写型号包（2~5 天，含门禁与文档）
 └─ 2. 它是「一整台仪器」还是「一条链路」？
       · 一整台可编程仪器 → 家族 + 厂商包（本指南场景）
       · 只是一条通信链路（串口/网口转发）→ 用 transport 层，不要造家族
```

判断依据只有一条：**上层希望用什么「动作」跟它说话**。动作相同 → 同家族；动作不同 → 新家族。

## 2. 情形 A：已有家族新增型号（最常见）

五步，不许跳：

| 步 | 做什么 | 产出 |
| --- | --- | --- |
| 1 | 生成骨架 | `python -m ate_drivers.kit.cli new <key> --model <型号> --interface LAN,SERIAL` |
| 2 | 填五个差异面 | `COMMANDS` / `PREAMBLE_FIELDS` / 量纲缩放 / 枚举映射 / `_sim_signal()` |
| 3 | 声明派发、链路与后端 | 类属性 `MODELS` / `INTERFACES` / `BACKENDS` / `vendor`，与 `driver.json` 一致 |
| 4 | 跑门禁 | `cli check <包>` → 19/19 |
| 5 | 补测试与文档 | 同一种用例在两家上结构一致、命令流不同 |

**关键**：不要 `override` 家族的动作方法。若你觉得非改不可，说明家族契约缺能力——那就走情形 B 的流程去**扩家族**，而不是在厂商包里打补丁。

## 3. 情形 B：新家族设计九步

以「数字万用表 DMM」为例逐步走。每步都写明**产出物**与**验证方式**。

### 第 1 步：家族命名与动作词汇表

- 家族名用**短英文小写**：`dmm`、`psu`、`awg`、`motion`（不要 `digital_multimeter_iso`）；
- 动作命名规则：`<家族>.<动词>`，动词用**业务语义**不用户命令（`measure` 而不是 `read_reg`）；
- 列出**必需动作**与**可选动作**：必需 = 没有它这一类设备就没意义（万用表的 `dmm.measure`）；可选 = 部分型号才有（万用表的 `dmm.range`）。

产出物：动作清单表（本文第 5 节的表就是模板）。

### 第 2 步：登记能力（改 `capabilities.py`）

三个地方都要改，漏一个门禁 C03 就会挂：

```python
# ate_drivers/capabilities.py

CORE_CAPABILITIES = ("identify", "reset", "state")        # 所有家族共享，不动

FAMILY_CAPABILITIES = {
    "scope":  [...],                                       # 已有
    "dmm":    ("dmm.measure",),                            # ① 家族可选动作（已有占位，按需扩）
    ...
}

REQUIRED_BY_FAMILY = {
    "scope":  ["scope.acquire_waveform", "scope.measure"],
    "dmm":    ["dmm.measure"],                             # ② 家族必需动作
    ...
}

# ③ 已知能力总表（frozenset）——由上面两张表汇总，务必同步
KNOWN_CAPABILITIES = frozenset(
    list(CORE_CAPABILITIES) + [c for caps in FAMILY_CAPABILITIES.values() for c in caps]
)
```

> 现状：`dmm` / `psu` / `awg` / `motion` / `passive` / `generic` 六个家族名**已在能力表里占位**（例如 `dmm: ["dmm.measure"]`、`psu: ["psu.set_output", "psu.measure"]`、`awg: ["awg.set_waveform", "awg.output"]`、`motion: ["motion.move", "motion.read_position"]`），但**家族实现尚未落地**。所以接万用表的现实路径是：能力位已存在 → 直接按第 3~9 步写 `family/dmm.py`，不需要改能力表（要加新动作才改）。

产出物：`capabilities.py` 的 diff。验证：`python -c "from ate_drivers import capabilities as c; print(c.KNOWN_CAPABILITIES)"` 能看到新动作。

### 第 3 步：定义数据模型

家族要定义三层数据：**输入参数**（用户给什么）、**状态**（设备现在什么样）、**返回值**（上层拿到什么）。

| 类别 | 要求 | 例子（DMM） |
| --- | --- | --- |
| 输入参数 | 名字表达物理量；单位写进 docstring；能给默认值就给 | `measure(kind="VDC", range_v=None, nplc=1.0)` |
| 状态 | 可序列化，能进报告 | `{function: "VDC", range_v: 10.0, nplc: 1.0}` |
| 返回值 | **每项测量自带 value + unit + quality + source** | `{"VDC": {"value": 5.0012, "unit": "V", "quality": "good", "source": "real"}}` |

铁律：**任何暴露给上层的量都必须是物理单位**（V / A / Ω / Hz / s），不是 ADC 计数、不是档位编号。

### 第 4 步：定义命令表契约（家族层的关键设计）

家族层**不写**具体命令，但要规定**需要哪些命令插槽**——这是「厂商包只填差异面」能成立的前提。

```python
# family/dmm.py 里定义插槽（示波器家族的写法可作对照）
REQUIRED_COMMANDS = (
    "idn",              # 身份查询
    "reset",            # 复位
    "func_set",         # 设功能：{func}
    "range_set",        # 设量程：{range}
    "rate_set",         # 设速率/积分时间：{nplc}
    "read",             # 取值
    "error_query",      # 错误队列
)

COMMANDS = {            # 家族默认表（通用 SCPI 写法，型号包按需覆盖）
    "idn": "*IDN?",
    "reset": "*RST;*CLS;*OPC?",
    "func_set": ":FUNC {func}",
    "range_set": ":RANG {range:g}",
    "rate_set": ":RATE {nplc:g}",
    "read": ":READ?",
    "error_query": ":SYST:ERR?",
}
```

插槽设计规则：

1. **一个业务动作对应一到多个插槽**，插槽粒度要细到「不同厂家只差这一条」；
2. 占位符用 `{name}` / `{name:g}` / `{state}`，**不要在家族层写死分隔符**（有的仪器用 `;` 拼多命令，有的不支持）；
3. 需要「按通道/按型号改写命令片段」时用钩子（示波器的 `channel_token(channel)` 就是这种钩子），不要在家族层拼 `CH1`；
4. 插槽一旦发布**只增不改**；型号包必须覆盖全部 `REQUIRED_COMMANDS`，`missing_commands()` 为空。

产出物：家族命令表。验证：门禁 C05。

### 第 5 步：定义仿真基线

仿真不是「随便给个数」，而是**一个物理上说得通的模型**：同样的输入必须给同样的输出（C12）。

| 家族 | 仿真模型建议 |
| --- | --- |
| DMM | 目标值 + 固定噪声种子 + 与档位相关的量化台阶 |
| PSU | 设定值 + 负载响应（可用一阶延迟）+ 回读噪声 |
| AWG | 波形方程（正弦/方波/任意点表），幅度与偏置按设定 |
| Motion | 位置积分模型 + 行程限位 |

型号包可以在家族基线上叠加**本机特性**（示例：泰克 MSO5 在家族正弦上叠加 8 bit 垂直量化）。

### 第 6 步：定义错误与边界

| 场景 | 码 | 行为 |
| --- | --- | --- |
| 参数越界（量程/频率 ≤ 0、格数 < 2） | `E_PARAM` | 报错 + 已登记取值清单 |
| 枚举未登记（功能写 `VAC` 而驱动只登记 `VDC/VAC`） | `E_PARAM` | **不静默替换** |
| 缺连接参数 | `E_CONFIG` | 不重试 |
| 应答解析不了 | `E_PROTOCOL` | 保留原始应答到 `detail` |
| 型号未登记 | `E_NOT_FOUND` | 列出可用型号 |

家族层负责**统一的校验与错误分类**，厂商包只负责「翻译方言」。

### 第 7 步：家族实现骨架（`family/dmm.py`）

```python
# -*- coding: utf-8 -*-
"""数字万用表家族契约：动作、单位、校验、仿真基线、命令表插槽"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..base import InstrumentDriver
from ..errors import DriverError


@dataclass
class Reading:
    """一次测量的读数（物理单位出口）"""
    kind: str                      # VDC / VAC / IDC / RES / FREQ ...
    value: Optional[float]
    unit: str                      # V / A / Ohm / Hz
    quality: str = "good"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "value": self.value, "unit": self.unit,
                "quality": self.quality}


class DmmDriver(InstrumentDriver):
    """万用表家族：只依赖命令表插槽，不依赖任何品牌方言"""

    family = "dmm"
    FUNCTIONS = ("VDC", "VAC", "IDC", "IAC", "RES", "FREQ")
    UNITS = {"VDC": "V", "VAC": "V", "IDC": "A", "IAC": "A", "RES": "Ohm", "FREQ": "Hz"}

    REQUIRED_COMMANDS = ("idn", "reset", "func_set", "range_set", "rate_set",
                         "read", "error_query")
    COMMANDS = {...}               # 见第 4 步
    SIMULATE_SUPPORTED = True

    # —— 状态 ——
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.function = self.FUNCTIONS[0]
        self.range_v: Optional[float] = None
        self.nplc = 1.0

    # —— 动作（方言无关）——
    def measure(self, kind: Optional[str] = None, **_) -> dict:
        kind = self._check_kind(kind or self.function)
        if kind != self.function:
            self.write(self.cmd("func_set", func=kind))
            self.function = kind
        self.write(self.cmd("rate_set", nplc=self.nplc))
        raw = self.query(self.cmd("read"))            # 厂商包负责把 raw 变成读数
        reading = self._parse_reading(kind, raw)      # 默认实现 + 厂商可覆盖
        return {"kind": reading.kind, "value": reading.value,
                "unit": reading.unit, "quality": reading.quality}

    def state(self) -> dict:
        return {"function": self.function, "range_v": self.range_v, "nplc": self.nplc,
                "unit": self.UNITS.get(self.function, "")}

    # —— 校验与仿真 ——
    def _check_kind(self, kind: str) -> str:
        if kind not in self.FUNCTIONS:
            raise DriverError("未登记的功能", device_id=self.device_id,
                              detail="可选: " + ", ".join(self.FUNCTIONS), code="E_PARAM")
        return kind

    def _parse_reading(self, kind: str, raw: str) -> Reading:
        try:
            return Reading(kind, float(str(raw).strip()), self.UNITS[kind], "good")
        except ValueError:
            raise DriverError("读数无法解析", device_id=self.device_id,
                              detail=repr(raw), code="E_PROTOCOL")

    def simulate_command(self, command: str) -> str:
        if command.strip().endswith("?"):
            return "5.0000"
        return ""
```

### 第 8 步：厂商型号包骨架（`vendors/fluke_8808a/`）

```python
# vendors/fluke_8808a/driver.py
from ate_drivers import DmmDriver          # 家族层导出后可用


class Fluke8808AScope(DmmDriver):
    driver_key = "fluke-8808a"
    family = "dmm"
    vendor = "Fluke"
    label = "数字万用表 · Fluke 8808A"
    driver_version = "1.0.0"
    INTERFACES = ("SERIAL",)               # 该型号只有串口
    BACKENDS = ("native", "visa")          # 两套栈都支持（原生 pyserial / PyVISA）
    MODELS = ("8808A",)
    sim_idn = "FLUKE,8808A,SIM0001,1.0"

    # 只填差异面①：命令表
    COMMANDS = dict(DmmDriver.COMMANDS, **{
        "func_set": ":FUNC {func}",
        "range_set": ":RANG {range:g}",
        "read": ":VAL1?",
    })

    # 差异面②③：应答解析与量纲（这里读数已直接是伏特/安培/欧姆）
    def _parse_reading(self, kind, raw):
        return super()._parse_reading(kind, raw)

    # 差异面⑤：仿真模型（在家族基线上叠加本机 5.5 位量化）
    def _sim_signal(self, *a, **k):
        ...
```

```json
// vendors/fluke_8808a/driver.json
{
  "schema": "ate.driver.manifest.v1",
  "key": "fluke-8808a",
  "version": "1.0.0",
  "family": "dmm",
  "api": "1.2",
  "label": "数字万用表 · Fluke 8808A",
  "vendor": "Fluke",
  "entry": "ate_drivers.vendors.fluke_8808a.driver:Fluke8808AScope",
  "interfaces": ["SERIAL"],
  "models": ["8808A"],
  "capabilities": ["identify", "reset", "state", "dmm.measure"],
  "simulate": true,
  "min_platform": "1.0.0",
  "depends": [],
  "signature": ""
}
```

### 第 9 步：门面、测试、文档

| 项 | 要求 |
| --- | --- |
| 门面 | 新家族配一个门面（`Dmm` + `open_dmm()`）或复用统一入口 `open_instrument(device=...)`；门面里**不许出现命令字样**（单测扫源码） |
| 测试 | 家族级用例（仿真 + 假链路各跑一遍）、型号派发用例、门禁用例；同一段用户代码在**两家同家族型号**上结构一致 |
| 文档 | 家族动作表 + 单位表 + 差异面模板 + 已登记型号清单，进 `sdk/docs/` |
| 门禁 | 19 项全绿；如新家族有特有规则，追加检查项（C20、C21…），不许删旧项 |

## 4. 需要改动的文件清单（新家族）

| # | 文件 | 改什么 |
| --- | --- | --- |
| 1 | `ate_drivers/capabilities.py` | `FAMILY_CAPABILITIES` / `REQUIRED_BY_FAMILY` / `KNOWN_CAPABILITIES` |
| 2 | `ate_drivers/family/<家族>.py` | 新增家族契约（动作、模型、校验、仿真基线、命令插槽） |
| 3 | `ate_drivers/family/__init__.py` | 导出家族类 |
| 4 | `ate_drivers/api.py` | 家族门面（可选，若走统一门面则不新增） |
| 5 | `ate_drivers/__init__.py` | 导出新家族类与门面函数 |
| 6 | `ate_drivers/vendors/<厂商>_<型号>/` | 型号驱动包 + `driver.json` |
| 7 | `ate_drivers/vendors/__init__.py` | 追加 `DRIVERS` 元组（内置登记） |
| 8 | `ate_drivers/kit/conformance.py` | 家族特有门禁项（可选） |
| 9 | `ate_drivers/transport.py` | 一般**不用改**：链路与后端已收口（`native` / `visa`）；新后端才在此登记 |
| 9 | `tests/test_<家族>_family.py` | 家族级测试 |
| 10 | `sdk/docs/` + `sdk/README.md` | 文档与型号清单 |

## 5. 五差异面模板（照抄这张表去填）

| 差异面 | 载体 | 怎么填 | 常见坑 |
| --- | --- | --- | --- |
| ① 命令表 | `COMMANDS` | 覆盖家族默认表中与本机不同的键；键名**不许改** | 手册里的方括号 `<...>`/`[...]` 是文档写法，不是命令正文 |
| ② 应答解析 | `_parse_*` / `PREAMBLE_FIELDS` | 按**字段名**取值，不靠位置猜 | 品牌间分隔符不同（`,` vs `;`）、可选字段会导致字段数变化 |
| ③ 量纲缩放 | 解析函数内 | 出口必须是物理单位 | 头里没给缩放系数时（如 R&S 走 ASCII 伏特）别硬乘 |
| ④ 枚举映射 | 家族枚举 → 本机取值 | 不支持的取值报 `E_PARAM` | 别把 `GND` 静默当 `DC` |
| ⑤ 仿真模型 | `_sim_signal()` / `simulate_command()` | 在家族基线上叠加本机特性 | 用随机数会让回归不稳定（C12 会挂） |

## 6. 上架清单（合并前逐条勾）

- [ ] 能力位已在 `capabilities.py` 三处同步（C03/C04）；
- [ ] 命令表覆盖全部 `REQUIRED_COMMANDS`（`missing_commands()` 为空，C05）；
- [ ] 仿真覆盖全部声明能力且可复现（C06/C12）；
- [ ] 测量项都带单位（C08）；
- [ ] 所有失败路径有明确错误码（C09）；
- [ ] 未引入第三方硬依赖（C15）；
- [ ] `MODELS` / `INTERFACES` / `BACKENDS` 与 `driver.json` 一致（C17/C18/C19）；
- [ ] `cli check` 19/19、`pytest` 全绿；
- [ ] 门面源码无命令字样；
- [ ] 两家同家族型号跑同一段用例，返回结构一致、命令流不同；
- [ ] 文档更新（家族动作表 + 单位表 + 型号清单）。

## 7. 反模式（踩过的与最容易踩的）

| # | 反模式 | 后果 | 正确做法 |
| --- | --- | --- | --- |
| 1 | 把家族逻辑写进厂商包 | 第二个品牌又要复制一遍 | 家族层加能力，厂商包只填差异 |
| 2 | 在门面里拼命令 | 品牌方言泄漏给上层，解耦失效 | 命令关进厂商包，门面只给业务方法 |
| 3 | 用 `vendor`/`category` 打分兜底选型 | 型号写错时「猜一个最像的」，故障难查 | 型号精确派发，未登记就报错 |
| 4 | 用 `ALIASES` 登记型号别名 | 覆盖家族动作别名表 → 该驱动所有动作崩 | 型号别名用 `MODEL_ALIASES` |
| 5 | 顶层 `import serial` / `import pyvisa` | 门禁 C15 直接挂；离线机装不上 | 函数体内惰性导入 + `set_serial_factory()` / `set_visa_factory()` 注入 |
| 6 | 两种链路参数都填却不报错 | 接错线被当成仿真/走错链路 | 报「连接方式不明确」，强制写明 `interface` |
| 7 | 把「格数」「ADC 计数」返回给上层 | 上层被迫懂仪器档位 | 出口物理单位，缩放关在厂商包 |
| 8 | 枚举不支持时静默替换 | 物理量含义被悄悄改掉 | 报 `E_PARAM` 并列出已登记取值 |
| 9 | 改动作语义而不加新动作 | TPS 用例集体失效 | 只增不改，旧动作进 `DEPRECATIONS` |

## 8. 版本演进规则

| 变更 | 版本动作 |
| --- | --- |
| 新增型号（同家族） | 只改 `MODELS` + `driver.json` → 驱动版本 +0.0.1 |
| 新增可选动作 | 加能力位 + 家族默认实现 + 文档；驱动版本 +0.1.0 |
| 新增必填动作 | 属破坏性变更：先加可选，过渡期后再提升为必需；契约版本 +0.1 |
| 修改动作语义 | **禁止直接改**：加新动作，旧的写进 `DEPRECATIONS`（C16 会验） |
| 平台不兼容 | 用 `min_platform` 声明，加载阶段报 `E_CONTRACT` |
