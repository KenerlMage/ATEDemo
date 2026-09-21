# ATE Runner 仪器驱动库 · 设计准则

> 适用版本：`ate_drivers` 1.2.0 · 契约 **1.2** · 门禁 19 项
> 这份文档是**写驱动与改库时的红线**。每条准则给：为什么、怎么做、反例、对应门禁。评审时逐条过。

## 准则总览

| 号 | 准则 | 一句话 | 门禁 |
| --- | --- | --- | --- |
| G1 | 命令不外露 | 命令只许出现在厂商包里 | C05、C07 |
| G2 | 型号直连派发 | 不猜型号、不打分、不兜底 | C17 |
| G3 | 差异收敛 | 厂商包只填五个差异面，不动别的 | C01、C03、C04 |
| G4 | 物理量出口统一 | 出口是伏/秒/赫兹，不是格数或计数 | C08 |
| G5 | 错误不静默 | 缺参数、非法枚举一律报错，不替换 | C09 |
| G6 | 零硬依赖 | 标准库之外只能惰性可选 + 可注入 | C15 |
| G7 | 仿真与真机同构 | 同一动作、同一返回结构 | C06、C12 |
| G8 | 契约只增不改 | 改语义先加新动作、旧动作进弃用表 | C02、C14、C16 |
| G9 | 会话必须安全可重入 | open/close 幂等，退出不能让仪器留在危险态 | C10、C11 |
| G10 | 实例互不干扰 | 同型号多实例并行不能串数据 | C13 |
| G11 | 连接方式与后端显式声明 | 声明了才算支持；歧义报错 | C18、C19 |
| G12 | 门禁即上架 | 19 项全绿才算完成 | 全部 |

## G1 命令不外露

**为什么**：一旦上层能写命令，就又会退化成「每个品牌一份测试脚本」。真正的解耦点是动作，不是命令。

**怎么做**：命令拼写只出现在 `vendors/<厂商>/driver.py` 的 `COMMANDS` 表与应答解析里；参数校验、单位换算、错误分类都在家族层与厂商包内完成，门面只暴露业务方法。

**反例**：

```python
# 反例：门面里拼命令（把品牌方言泄漏给了上层）
def set_scale(self, v):
    self.write(f":CH1:SCAle {v}")
```

**正例**：`scope.set_channel("CH1", volts_per_div=0.5)` —— 上层不知道这条命令在泰克是 `:CH1:SCAle 0.5`、在 R&S 是 `:CHANnel1:SCALe 0.5`。

自检手段：单测直接扫描 `api.py` 源码与公开返回数据，`:CURVe` / `:WFMOutpre` / `:ACQuire` / `:TIMebase` 等字样出现次数必须为 0。

## G2 型号直连派发

**为什么**：型号是设备档案里的**确定字段**，不是需要猜测的线索。打分选型会在型号写错时「挑一个最像的」去问仪器——这比报错难查得多。

**怎么做**：`normalize_model()`（只留字母数字、转大写）后**精确相等**；别名必须显式登记（`MODEL_ALIASES`，如 `MXO4 → MXO44`）；同名冲突用 `model_conflicts()` 报出来；未登记型号抛 `DriverNotFound` 并列出全部已登记型号。

**反例**：给型号加相似度打分、按 `vendor`/`category` 兜底、留一个 `GenericScope` 万能驱动。

**一句话**：**宁可当场报错，也不猜。**

## G3 差异收敛到五个差异面

厂商驱动包只允许填这五处，其余一切继承家族层：

| 差异面 | 载体 | 示例 |
| --- | --- | --- |
| ① 命令表 | `COMMANDS`（键必须覆盖 `REQUIRED_COMMANDS`） | 泰克 `:AUTOSet EXECute` / R&S `:AUToset` |
| ② 应答解析 | `PREAMBLE_FIELDS` + `simulate_command()` 的解析分支 | `WFMOutpre` 前导字段顺序、逗号 vs 分号 |
| ③ 量纲缩放 | 解析时把裸值换成物理单位 | R&S 头里没缩放系数 → 走 `FORMat ASCii`（手册示例即伏特） |
| ④ 枚举映射 | 家族枚举 → 本机取值 | 耦合 `DC/AC`；仪器不支持的 `GND` → `E_PARAM` |
| ⑤ 仿真模型 | `_sim_signal()` / `simulate_command()` | 泰克叠加 8 bit 垂直量化 |

**反例**：在厂商包里重写 `measure()` 算法、改动作名、改返回结构、把某个品牌的特殊处理写进家族层。

## G4 物理量出口统一

**为什么**：上层不知道（也不该知道）仪器当前的垂直档位、ADC 位数、ASCII/二进制编码。

**怎么做**：`Waveform.samples` 恒为**伏**（`unit="V"`），时间轴恒为**秒**（`x_incr_s` / `x_start_s`），测量项自带 `unit`；换算（`YMULT`/`YOFF`、`XINcr`/`XZEro`、位宽位序）全部在厂商包内完成。

**反例**：返回原始计数、返回「格数」、让上层自己乘 `v_per_div`、把单位写在文档里而不是返回值里。

**门禁**：C08 会检查每个测量项都带单位。

## G5 错误不静默

**为什么**：静默替换物理量、静默降级链路，会把「接错线」「参数写错」拖成难查的偶发故障。

**怎么做**：

- 缺参数（host 没 port、串口名缺失、波特率非法）→ `E_CONFIG`；
- 枚举未登记（耦合写 `GND` 而驱动只登记 `DC/AC`）→ `E_PARAM`，消息里附上**已登记取值清单**；
- 型号回读不符 → `E_CONFIG`，消息里给出档案型号与实测型号；
- 链路不匹配 → `E_CONFIG`，附带该型号支持的链路。

**反例**：把 `GND` 当 `DC` 处理；host 缺 port 时自动转仿真；`*IDN?` 回读不符只打个 warning 继续跑。

## G6 零硬依赖

**为什么**：目标部署环境是客户工控机，可能离线、可能不允许装包。

**怎么做**：运行时只用标准库；确需第三方时（串口 `pyserial`、VISA `pyvisa`）三条都要做到：

1. **函数体内惰性导入**（`_pyserial()` / `_pyvisa()`），模块顶层不许出现；
2. 缺它时报可读的 `E_CONFIG`（消息里带 `pip install …`），**不是** `ImportError`，也不静默降级；
3. 提供注入点（`set_serial_factory()` / `set_visa_factory()`），测试与自研实现都能塞替身。

**反例**：模块顶部 `import serial` / `import pyvisa`；没装 VISA 就悄悄走 socket；把 numpy 引进数据处理（缩放与测量算法必须纯 Python）。

**门禁**：C15 扫 AST 区分「硬依赖 / 惰性可选依赖」，`OPTIONAL_RUNTIME = ("serial", "pyvisa")` 只允许后者。

## G7 仿真与真机同构

**为什么**：没有硬件时要能开发、能做回归、能复现问题。

**怎么做**：仿真是**链路**而不是分支判断；`mode="auto"` 无端点即仿真，`mode="simulate"` 强制仿真；仿真必须支持全部声明能力（C06），且同样输入必须同样输出（C12）；仿真数据要遵循同一套物理模型（正弦 + 噪声 + 量化），不是随机数。

**反例**：真机路径写一套、仿真路径写另一套返回结构；仿真返回随机数导致用例不稳定。

## G8 契约只增不改

**为什么**：TPS 用例是资产，不能因为驱动升级而集体失效。

**怎么做**：动作与参数**只增**；要改语义就加新动作，把旧的写进 `DEPRECATIONS` 指向替代项（C16），旧动作继续可跑（C14）；`driver.json` 里的 `api` 与 `min_platform` 声明兼容边界，`check_compatible()` 在加载阶段拒绝不兼容包。

## G9 会话必须安全可重入

**为什么**：产线上反复开合、异常中断很常见。

**怎么做**：`open()` / `close()` 幂等（重复调用不报错）；`close()` 走 `protect_on_close()` 的安全退出（先停采集、必要时复位）；异常路径也要走同一套退出逻辑，不能把仪器留在「等待触发」之类的挂起态。

## G10 实例互不干扰

**为什么**：一个台位可能挂两台同型号设备。

**怎么做**：状态挂在**实例**上（时基/通道/仿真参数），不挂类；`device_id` 随会话流动；单测里两个实例交叉配置必须互不影响（C13）。

## G11 连接方式与传输后端显式声明

**为什么**：同一台设备网口与串口都能接（`kind`），同一套命令也能走原生 socket 或 PyVISA（`backend`），靠猜会接错线、也会"以为走的是 VISA 其实走的是 socket"。

**怎么做**：

* 驱动类声明 `INTERFACES`（多值，如 `("LAN", "SERIAL")`），`driver.json` 同步 `interfaces` 数组；
  解析顺序 = 显式参数 > 档案 `interface` > 端口填充；两种参数都填又没写明 → 报「连接方式不明确」；
  型号不支持的链路 → 报错并列出支持项；
* 驱动类声明 `BACKENDS`（多值，如 `("native", "visa")`），`driver.json` 同步 `backends` 数组；
  解析顺序 = 显式 `backend=` > 档案 `extra.backend` > 默认 `native`；型号没声明的后端 → 报错；
  `auto` 不写进 `BACKENDS`（它是调用时的选择）；
* **实际生效值必须可查**：`scope.interface` / `scope.backend` / `status()` 里都要有，别让现场靠猜。

**反例**：档案里同时写 `host` 与 `serial_port` 就默认走网口；只声明网口的型号收到串口端点时静默忽略串口参数；
某台机器装了 VISA 就自动改走 VISA 而不写进状态（`auto` 也必须把结果落到 `backend` 上）。

## G12 门禁即上架

**怎么做**：交付前必须跑

```bash
python -m ate_drivers.kit.cli check <驱动包>     # 19 项必须全绿，退出码 0
python -m pytest tests -q                        # 库自带 47 项自测必须全绿
```

不绿就不算完成——**没有「先上架、后面补」这一说**。

## 命名与文件规矩

| 对象 | 规矩 | 例子 |
| --- | --- | --- |
| 驱动 key | 小写 + 连字符；`driver.json` 的 `key` 与类属性 `driver_key` 必须一致 | `tektronix-mso5` |
| 包目录 | **合法 Python 模块名**（下划线，不是连字符） | `vendors/rohde_schwarz_mxo/` |
| 型号 | 归一化后精确匹配；别名走 `MODEL_ALIASES` | `MODEL_ALIASES = ("MXO4",)` |
| 链路 | `INTERFACES`（元组，多值）；旧单值 `INTERFACE` 仍兼容 | `INTERFACES = ("LAN", "SERIAL")` |
| 后端 | `BACKENDS`（元组，多值）；旧单值 `BACKEND` 仍兼容；不许写 `auto` | `BACKENDS = ("native", "visa")` |
| 名字不串用 | `ALIASES` 是**家族层的动作别名表**，厂商包**禁止**拿它登记型号 | 型号别名一律用 `MODEL_ALIASES` |
| 清单 | `driver.json` 的 `models` / `interfaces` / `backends` / `capabilities` 必须与类属性一致 | C17 / C18 / C19 / C03 会验 |

> 踩过的坑：在厂商驱动里写 `ALIASES = ("MXO4",)` 覆盖了基类的动作别名字典，导致该驱动**所有动作**报 `'tuple' object has no attribute 'get'`。三个名字（`ALIASES` / `MODEL_ALIASES` / `INTERFACES`）互不串用。

## 编码与文档纪律

- 源码 UTF-8，首行 `# -*- coding: utf-8 -*-`，模块写中文 docstring 说明「这个包负责哪几个差异面」；
- 公开方法必须有 docstring：参数、单位、返回、可能抛的错误码；
- **只写真实核验过的内容**：手册没读到的枚举标注「待现场核对」，不要凭印象补；
- 单位一律写符号（V / s / Hz / s/div / V/div），不要混用「伏特」「V」两种写法。

## 评审清单（合并前逐条过）

- [ ] 新加的动作是否已进 `capabilities` 并更新 `driver.json`？
- [ ] 命令表是否覆盖全部 `REQUIRED_COMMANDS`（`missing_commands()` 为空）？
- [ ] 出错路径是否都带明确错误码（不裸抛 `Exception`、不靠文案判断）？
- [ ] 仿真是否覆盖全部声明能力、且可复现？
- [ ] 是否引入第三方硬依赖（含测试之外的新 import）？pyserial / pyvisa 是否都在函数体内惰性导入并留了注入点？
- [ ] `api.py` 里是否有命令字样？
- [ ] `models` / `interfaces` / `backends` 是否与类属性一致？
- [ ] `cli check` 19/19、`pytest` 全绿是否跑过并把输出留在交付说明里？

## 迁移与统一（未完成事项）

平台侧 `backend/drivers/factory.py` 目前仍是**打分选型版**（`DRIVER_SPECS` 8 条 + 权重 `model/vendor/category/role/interface/name` + `generic-scpi` 兜底）。SDK 侧的目标口径是**型号直连派发**。下列项未落地，落地时按本准则 G2 处理：

- 平台侧 `DRIVER_SPECS` 的 `match` / `priority` 字段清理，改为 `models` + `interfaces`；
- 平台侧打分函数与兜底驱动移除；
- 平台侧 `backend/drivers/transport.py` 仍是原生 socket / pyserial 两分支，**未接 PyVISA**（SDK 侧已支持 `backend="visa"`）；
- 契约层 v1.3 规划中的 6 个新能力与 5 个数据模型（见《示波器通用接口设计》文档）尚未实现，门禁停在 19 项；
- R&S 耦合枚举（`CHANnel<ch>:COUPling` 取值表）未从手册正文取到，当前只登记 `DC/AC`，`GND` 报 `E_PARAM`；需现场核验。
