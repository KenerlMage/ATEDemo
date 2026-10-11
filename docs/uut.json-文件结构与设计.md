# uut.json 文件结构与设计

> 本文描述 TPS 清单（`uut.json`，现网文件名 `tps.json`，两者指同一份清单）的文件结构、字段职责与执行流程。
> 清单是受控数据，用例实现是代码；两者通过条目的 `case` 字段一一对应。

## 一、设计意图

本方案把测试用例集从执行代码中剥离为一份受控数据，用以解决三个问题：测试内容散落在代码里、无法评审；同一台装备在不同台架上的判定口径不一致；更换台架必须改动用例实现。

据此确定四条设计约束。

| 约束 | 含义 |
| --- | --- |
| 清单只声明，不实现 | 清单内不出现执行代码、连接参数、调用实参；测试动作全部由 `testcase/` 中的 Python 函数实现 |
| 单一事实源 | 用例顺序只写在 `cmd_suit`，判定算法与阈值只写在 `threshold`，设备引用只写在 `device`，一拖多的适配范围与通道归属只写在 `adaptation`；同一信息只允许出现一次 |
| 引用而非复制 | 清单只写设备别名与注册库编号，IP、端口、串口、波特率由执行器在运行时从注册库查询 |
| 判定集中 | 通过/不通过只由用例调用判定接口产生，清单不承载判定表达式 |

清单的边界：不描述夹具接线细节，不承载测试向量数据。这类内容以配置文件形式放在包内 `resource/`，由用例按包内路径读取。

## 二、TPS 包结构

一个 TPS 就是一个目录，打包后即为压缩包。

```text
spm_rh_dyn/                         ← TPS 包（打包后即压缩包）
├── tps.json                        ← 清单：五键
├── testcase/                       ← 每个 py 文件是一个 testcase 模块
│   ├── __init__.py                 必备
│   ├── env_setup.py                含 setup / teardown 方法
│   ├── power.py                    含 setup / teardown 方法
│   └── comm.py
├── resource/                       清单之外的配置与数据
│   ├── pn_whitelist.json
│   ├── channel_map.json
│   ├── fixture_list.json
│   ├── appearance_check.md         manual 弹窗文案（按条目 resource 名取）
│   └── appearance_check.png        同名图片，与文案一并展示
└── wheel/                          其余 Python 驱动
    ├── scope_sdk-1.2.0-py3-none-any.whl
    └── dmm_driver-0.9.3-py3-none-any.whl
```

各子目录 / 文件的结构与作用：

| 子目录 / 文件 | 结构 | 作用 |
| --- | --- | --- |
| `tps.json` | 单个 JSON 文件，五键 | 清单：声明跑什么、按什么顺序、按什么判 |
| `testcase/` | Python 包，必须含 `__init__.py`，一个 py 文件一个 testcase 模块，模块内含 `setup` 与 `teardown` 方法 | 用例实现：清单条目 `case` 字段引用的目标函数 |
| `resource/` | 配置文件目录，JSON / CSV / YAML / Markdown / 图片均可 | 存放清单之外的配置与数据：大批量 PN 白名单、通道表、夹具清单、测试向量；`manual` 条目的弹窗文案与图片也在这里，由用例按名称取 |
| `wheel/` | `.whl` 文件 | 存放其余 Python 驱动与设备 SDK，随包分发，供离线工控机安装或直接加载 |
| 运行期产物 | 不在包内 | `conftest.py`、`test_tps_generated.py`、`ate_env.json`、`run_<task_id>/` 由执行器在运行目录生成 |

交付约定：导入接口接收单个 JSON 清单文本，后端将其落成 `testresource/<id>/tps.json`；`testcase/`、`resource/`、`wheel/` 三个目录随后整体拷入同一 TPS 目录。

### 2.1 运行时副本

Runner 不在源包上直接执行。每次运行它都在 workspace 下建一个临时副本，把 TPS 内容整体复制进去，之后的校验、注入与执行都发生在副本里。

| 环节 | 行为 |
| --- | --- |
| 建副本 | `<workspace>/<tps 目录名>/run_<task_id>/`；同名目录先清掉再整体复制源目录 |
| 执行对象 | 副本，不是源包——源包改动后下一次运行立即生效，多次运行之间互不污染 |
| 副本内产物 | `conftest.py`（公共注入层副本）、`test_tps_generated.py`（执行入口）、`ate_env.json`（本次运行环境） |
| 清理 | 按 `keep_runs` 保留最近若干次运行，其余自动清理 |

![Runner 使用 TPS 的运行时流程](assets/tps-runner-flow.png)

## 三、TPS 的使用方法（Runner 与 conftest）

清单本身不可执行。它由两侧消费：后端执行器（Runner）负责读取、校验与编排，`conftest.py` 负责向用例注入所需的一切。

![TPS 运行时软件视图](assets/tps-software-view.png)

**Runner（后端执行器）**

| 步骤 | 动作 | 依赖字段 |
| --- | --- | --- |
| 0 | 在 workspace 下建临时副本 `run_<task_id>/`，把 TPS 内容整体复制进去 | — |
| 1 | 在副本内载入 `tps.json`，校验结构：schema、必填项、引用完整性 | 全量 |
| 2 | 启动门禁与槽位绑定：状态是否允许生产、件号是否在白名单、SN 形态是否匹配；扫到的 SN 数与槽位绑定（少扫按空位处理） | `metadata.status` / `adaptation` |
| 3 | 解析设备与阈值：别名 → 注册库记录 → 连接参数；阈值键 → 算法与参数 | `device` / `threshold` |
| 4 | 按 `cmd_suit` 三段顺序展开执行序列，渲染执行入口与环境描述 | `cmd_suit` / `setup` / `teardown` |
| 5 | 逐条用例以独立 pytest 进程执行，收集判定明细；`manual` 条目弹窗等操作员选 next / abort | 条目的 `type` / `resource` / `parallel` / `exec.timeout_s` |
| 6 | 汇总 `run_result.json`，生成 HTML 报告 | `metadata` / `threshold` |

**conftest（注入通道）**

| 夹具 | 注入内容 | 来源 |
| --- | --- | --- |
| `ate_ctx` | 判定上下文：`expect(key, value, label)`、步骤状态回传、人工确认提示 | `threshold` + 运行时登记 |
| `ate_devices` | `别名 → 设备实例`，连接参数已就绪 | `device` + 注册库 |
| `ate_thresholds` | 阈值键 → 判定算法与参数 | `threshold` |
| `ate_bench` | 台架信息：编号、类型、BOM 摘要 | 运行环境 |
| `driver` | 设备驱动工厂：示波器、电源、DMM 等按型号构造 | `wheel/` + 注册库 |
| `ate_runner` | 当前用例在序列中的位置与状态 | `cmd_suit` |

用例侧只声明需要哪些夹具，取值与判定口径都由注入层提供：

```python
def test_vdd(ate_ctx, ate_devices):
    dmm = ate_devices["dmm"]
    ate_ctx.expect("TC_VDD", dmm.read_voltage())   # 判定口径来自 threshold
```

用例不读清单文件、不连设备、不判断阈值。

### 3.1 运行模式：调试模式 / 生产模式（与仿真模式）

Runner 右上角提供模式选择，模式决定三件事：要不要工单、测试记录留不留、能不能用仿真。

![运行模式与 lic 门禁：有 lic 可选调试 / 生产，无 lic 直接落在调试模式](assets/tps-run-mode-flow.png)

| 维度 | 调试模式 | 生产模式 |
| --- | --- | --- |
| 工单 | 不需要工单，随时可运行 | 必须由工单驱动；没有工单进不去 |
| 测试记录 | 运行结果不上传（本机仍产出报告与日志） | 测试数据记录并上传 |
| lic | 无 lic 时 Runner 直接运行在调试模式 | 需要有效 lic；无 lic 时入口不可选 |
| 仿真模式 | 可选：勾选后无需真实设备即可运行 TPS | 不提供，必须接真实台架 |

- **lic 缺失是降级而不是报错**：校验不通过时 Runner 直接以调试模式启动，生产模式入口不可选。
- **仿真模式只替换设备访问层**：设备读取换成仿真响应，用例与判定逻辑照常执行，适合离线调试与用例验证。
- **两种模式共用同一套用例与判定逻辑**：差别只在工单、测试记录上传与是否接真实设备。


## 四、执行流程

清单的五个键按固定顺序被消费，形成一条从启动到报告的流程：

| 阶段 | 消费字段 | 动作 | 产物 |
| --- | --- | --- | --- |
| ① 载入与校验 | 全量 | 结构校验与引用完整性检查 | 校验结果；不通过即中止 |
| ② 启动门禁 | `metadata.status` / `adaptation.part_number` / `adaptation.sn` / `adaptation.multi_up` | 状态是否允许生产、件号是否在白名单、SN 形态是否匹配、扫到的 SN 数是否在 `count` 上限内且达到 `required` | 通过；件号未命中白名单、SN 形态不符或路数不足时直接抛异常，流程中止 |
| ③ 槽位绑定 | `adaptation.multi_up.slots` | 扫到的 SN 与槽位绑定，确定每路输入的信号源与通道；未扫到的槽位记为空位 | 槽位 → 通道映射表 + 空位清单，后续数据按此归属 |
| ④ 设备解析 | `device` | 别名 → 注册库 → 连接参数 → 设备实例 | `ate_devices` 夹具 |
| ⑤ 阈值装载 | `threshold` | 按 `mode` 装载判定算法与参数 | `ate_thresholds` 夹具 |
| ⑥ 序列展开 | `cmd_suit` / `setup` / `teardown` | 三段按书写顺序展开；`per_sn` 条目在每个已扫槽位各展开一次（空位不展开），`manual` 条目插入等待点（next / abort） | 执行序列与执行入口 |
| ⑦ 执行与判定 | 条目的 `exec`、阈值算法 | 每条用例独立进程执行，判定按算法取样本算值 | 判定明细、`run_result.json` |
| ⑧ 收尾与报告 | `teardown` / `metadata` | 安全收尾必跑，汇总结果 | HTML 报告、运行记录 |

## 五、metadata · 版本数据

记录这份基线是什么版本、谁定的、什么时候定的，是评审与追溯的落点，不参与执行。

| 字段 | 类型 | 必需 | 作用 |
| --- | --- | --- | --- |
| `version` | string | 是 | 基线版本号，报告据此追溯判据版本 |
| `status` | string | 是 | `draft` / `review` / `released`；只有 `released` 允许用于正式生产运行 |
| `author` | string | 是 | 责任人，判据谁定的 |
| `created_at` | string | 是 | 基线建立日期（`YYYY-MM-DD`） |
| `id` | string | 是 | 基线编号，运行、报告、记录引用它的稳定键 |
| `name` | string | 是 | 基线名称，报告标题与列表展示用 |
| `applies_to` | string | 是 | 适用台架类型编号 |

## 六、adaptation · 一拖多测量与件号白名单

这一键回答三个问题：这份基线适配哪些件号（PN 白名单）；一次同时测几个 SN（一拖多的输入数量上限）；某个 SN 的信号走哪几路通道（SN → 信号通道指定）。

`adaptation` 的存在前提是**一拖多**：一次装夹多个 SN（上线为多个），同一条测试序列在多个槽位上并行测量，**每个 SN 对应一组输入**。所以这一键既声明"能不能测"，也声明"同时测几路、哪路归哪个 SN"。

### 6.1 PN 白名单机制

件号从 UUT 名称中按正则提取，再与白名单逐项比对；不在白名单内即**直接抛异常，拒绝执行**。白名单是"这份基线适配哪些件号"的唯一声明处。

```text
UUT 名称 / 登记信息
        │  part_number.regex 提取
        ▼
      件号 PN ──── 与 part_number.whitelist 比对 ────┬── 命中 ──→ 通过，继续执行
                                                     └── 未命中 → 抛异常，拒绝执行
```

| 字段 | 作用 |
| --- | --- |
| `part_number.regex` | 从 UUT 名称中提取件号的正则（命名组 `pn`） |
| `part_number.whitelist` | 件号白名单；命中才允许执行，未命中直接抛异常 |
| `part_number.whitelist_file` | 可选：白名单文件相对路径（如 `resource/pn_whitelist.json`），用于件号数量大、需独立维护的场景；与内联白名单合并生效 |

白名单规模小时直接内联在清单里便于评审；件号成百上千时放 `resource/`，清单只留引用。

### 6.2 一拖多：数量上限、少扫可测与通道指定

`multi_up` 管一次运行覆盖几个 SN。`count` 是**输入数量上限**——工位一次能同时接几个 SN（等于槽位数、等于输入组数），不是"必须扫满"的要求。

| 字段 | 作用 |
| --- | --- |
| `multi_up.count` | 输入数量上限：一次最多同时测几个 SN（= 槽位数 = 输入组数） |
| `multi_up.required` | 最少要扫到几个 SN 才允许开测；缺省 1（少扫也能测） |
| `multi_up.allow_partial` | 是否允许少扫，缺省 `true`；写 `false` 等价于把 `required` 写成 `count`，即要求满员 |
| `multi_up.slots[].slot` | 槽位编号（1..count），对应台架上第几个 SN 位 |
| `multi_up.slots[].sn` | 可选：把该槽位钉给指定 SN——这就是"指定某个 SN 对应的信号通道"；留空则按扫码顺序落到空槽位 |
| `multi_up.slots[].inputs[]` | 该 SN 对应的一组输入，每项 `{ signal_source, source_channel }`；同一信号源可服务多个槽位，通道不共用 |
| `multi_up.slots[].fixture` | 可选：该槽位使用的夹具编号 |

"上线为多个、但少扫一两个 SN"是正常工况，处置口径如下：

| 情形 | 行为 |
| --- | --- |
| 扫到 `count` 个 SN | 全部槽位参与测量 |
| 扫到 k 个，`required` ≤ k < `count` | **照常测量**：未扫到的槽位记为空位，其输入组不采集、不判定，报告单列；整轮结论只看参与测量的 SN |
| 扫到 k 个，k < `required` | 抛异常，拒绝执行（没达到最少路数） |
| SN 已在 `slots[].sn` 里钉过槽位 | 精确匹配该槽位：扫码顺序变化也不会把数据串到别的 SN |
| SN 没钉槽位 | 按扫码顺序落到空槽位 |

空位必须显式留空，不能"顺位补一个 SN"：数据归属靠槽位 → 通道的映射，补位就会让 A 的波形进到 B 的判定里。

示例（4 路一拖多，本次扫到 2 个 SN，槽位 3、4 为空位）：

```jsonc
{
  "multi_up": {
    "count": 4,            // 输入数量上限：一次最多同时测 4 个 SN
    "required": 1,       // 至少扫到 1 个就允许开测
    "allow_partial": true,
    "slots": [
      { "slot": 1, "sn": "A1B2C3D4E5F6", "fixture": "FX-01",
        "inputs": [ { "signal_source": "SIG-A", "source_channel": "AWG-CH1" },
                    { "signal_source": "SIG-A", "source_channel": "AWG-CH2" } ] },
      { "slot": 2, "fixture": "FX-02",
        "inputs": [ { "signal_source": "SIG-B", "source_channel": "AWG-CH1" },
                    { "signal_source": "SIG-B", "source_channel": "AWG-CH2" } ] },
      { "slot": 3, "fixture": "FX-03",
        "inputs": [ { "signal_source": "SIG-B", "source_channel": "AWG-CH3" } ] },
      { "slot": 4, "fixture": "FX-04",
        "inputs": [ { "signal_source": "SIG-B", "source_channel": "AWG-CH4" } ] }
    ]
  }
}```

槽位 1 把 SN 钉死了，槽位 2 留空等扫码结果落进来；槽位 3、4 本次没扫到，记为空位——通道已经声明好，只是不采集。

![一拖多：槽位 · SN · 信号通道（含空位）](assets/tps-slot-source-match.png)

### 6.3 SN 形态

| 字段 | 作用 |
| --- | --- |
| `sn.regex` | SN 形态正则（12 位英数），形态不符即抛异常，拒绝执行 |
| `sn.note` | 可选：形态说明，供人阅读 |

## 七、device · 设备信息

这一键声明"用例需要哪些设备"，只写引用，不写连接参数。

| 字段 | 必需 | 作用 |
| --- | --- | --- |
| 别名（键） | 是 | `scope` / `dmm` / `psu` / `spin` 等，用例按别名取设备 |
| `device_id` | 是 | 注册库（SQLite `device_registry`）中的设备编号，解析时精确命中 |
| `category` | 是 | 设备类别；用于确认该类别确实在本台架 BOM 内 |

不写入清单的内容：IP、端口、串口、波特率等连接参数。这些由注入层在运行时从注册库查询——换台架时连接参数必变，写进受控文件就是维护灾难。

## 八、threshold · 阈值与判定算法

阈值配置只声明"按什么算法判、参数是多少"，不实现判定逻辑。判定由用例调用判定接口触发，接口按键取到算法与参数后执行。

| `mode` | 判定方法 | 判定条件 | 参数 | 典型用途 |
| --- | --- | --- | --- | --- |
| `minmax` | 上下限 | `min ≤ 实测值 ≤ max` | `min`、`max`（至少给其一） | 供电电压、静态电流 |
| `std` | 统计偏差 | `\|x̄ − 均值\| ≤ n·σ` | `n`、`min_samples` | 参数一致性、噪声敏感的模拟量 |
| `mean` | 均值偏差 | `\|x̄ − 标称\| ≤ delta` | `delta`（绝对或百分比） | 转速、频率等带标称值的量 |
| `range` | 极差 | `max(x) − min(x) ≤ max_range` | `max_range` | 纹波、抖动等稳定性指标 |
| `delta` | 差值 | `\|a − b\| ≤ max_delta` | `against`、`max_delta` | 双通道对称性、前后两次测量对比 |
| `count` | 计数 | 异常样本数 ≤ `max_count` | `max_count`、`fail_when` | 丢包、超时次数 |

每个阈值键的公共字段：

| 字段 | 必需 | 作用 |
| --- | --- | --- |
| `mode` | 是 | 判定算法（上表之一） |
| 算法参数 | 是 | 按 `mode` 取值，见上表 |
| `label` | 否 | 阈值名称，用于报告与判定明细 |
| `unit` | 否 | 单位（V / mA / rpm），用于展示与判定消息文案 |

### 8.1 多算法兼容建议：统一结果结构

判定算法会不断增加，但报告列、数据库列、明细渲染不应随之改动。建议所有算法产出同一种结果结构，由用例判定接口填充：

```jsonc
// 统一结果结构：报告、落库、明细只消费这一结构
{
  "key": "TC_VDD",              // 阈值键
  "mode": "minmax",             // 判定算法
  "samples": [3.301, 3.298],    // 参与判定的样本（count/std/range 用）
  "value": 3.301,               // 判定的代表值：minmax 取实测，std/mean/range 取统计值
  "min": 3.135, "max": 3.465,   // 生效的判定边界（按 mode 换算后）
  "unit": "V", "ok": true,      // 结论
  "message": "3.301 V 位于 [3.135, 3.465]"   // 供报告直接展示
}
```

新增算法只需扩展 `mode` 与算法参数，结果结构与消费方不动。

## 九、cmd_suit · 用例清单与执行顺序

这一键声明跑哪些用例、按什么顺序跑。顶层三段平铺，顺序就是书写顺序：

| 段 | 作用 | 执行方式 |
| --- | --- | --- |
| `setup` | 环境初始化：上电、复位、装夹 | 按数组顺序逐条执行 |
| `cmd_suit` | 测试套：全部测试用例 | 按数组顺序逐条执行；`parallel=per_sn` 的条目逐槽位各跑一遍；`type: manual` 条目插入人工确认，`step: true` 条目跑完暂停等放行 |
| `teardown` | 环境终止：断电、卸载、归档 | 收尾段，前面的段跑完必定执行 |

编排上刻意只保留"顺序"这一件事：数组顺序即执行顺序，不引入排序、优先级、依赖、条件分支、失败策略与重试。清单里能让流程停下来的只有两处：人工确认条目（`type: manual`）与单步执行标记（`step: true`）。两类暂停点在等待操作员应答期间均允许取消本轮运行，取消后不再等待超时。

### 9.1 人工确认条目（`type: manual`）

需要操作员判断的步骤（外观检查、装夹确认、人工上下料）用 `type: manual` 表达：运行时弹出确认窗口，操作员在三个动作中选择一个。

| 选项 | 行为 |
| --- | --- |
| 继续下一步 `next` | 关闭弹窗，执行清单里的下一条 |
| 中止 `abort` | 判定当前环节不通过，停止本轮测试，直接进入收尾与报告 |
| 取消本轮 `cancel` | 主动放弃本轮测试（例如装夹错误需要重装）：剩余测试条目全部跳过，收尾与报告照常进行 |

条目本身只写"用哪个名称"，弹窗内容不写在清单里：

```jsonc
{
  "id": "M-01",
  "type": "manual",                     // 条目类型：人工操作环节
  "name": "装夹与外观确认",              // 弹窗标题、步骤列表与报告里的显示名
  "resource": "appearance_check.md"     // 提示文件：包内 resource/ 下的 Markdown 文件名
}
```

Runner 按 `resource` 到包内 `resource/` 目录取原文（`.md` / `.markdown` / `.txt`），把全文渲染进弹窗；同目录的图片可以在 Markdown 里用相对路径引用。改文案、换示意图只改资源文件，不动受控清单。

### 9.2 到达人工条目时会发生什么

| 环节 | 行为 |
| --- | --- |
| 到达条目 | Runner 挂起本轮流程，把 `resource/<名称>` 的 Markdown 原文放进任务快照（`manual.status = waiting`），界面弹出提示窗口 |
| 选「继续下一步」 | 该条目记为**通过**，接着执行清单里的下一条 |
| 选「中止测试」 | 该条目记为**已中止**，后续测试条目全部跳过，**收尾段 `teardown` 照常执行**，报告结论为 NOK |
| 选「取消本轮测试」 | 该条目记为**已取消**，后续测试条目全部跳过，收尾段照常执行，报告结论为 NOK。与「中止」的收尾路径一致，区别在语义与记录：中止用于判定当前环节不通过，取消用于主动放弃本轮运行 |
| 等待期间取消 | 等待操作员应答期间可随时取消本轮：任务立即结束等待并按上表路径收尾，不必等到超时；运行中（未处于等待）的任务同样可取消，此时剩余测试条目直接跳过 |
| 无人应答 | 默认 1800 秒到点按中止处理；条目可用 `timeout_s` 覆盖等待时长 |
| 追溯 | 运行日志与 HTML 报告把该条目类型显示为「人工操作」，并记录操作员的选择与时刻 |
| 接口 | 界面提交 `POST /api/tps-tasks/{task_id}/manual`，请求体 `{"decision": "next" | "abort" | "cancel"}`；取消本轮另可用 `POST /api/tps-tasks/{task_id}/cancel`，运行中与等待期间均有效；进度查询 `GET /api/tps-tasks/{task_id}` |

### 9.3 单步执行（`step: true`）

调试、首件确认、工艺评审时需要一条一条放行：在那一行加 `"step": true`，这条用例跑完后 Runner 暂停，界面上出现「下一步」按钮，点一下才执行清单里的下一条。

```jsonc
{
  "no": 1,
  "id": "TC001",
  "name": "上电电压测试",
  "case": "testcase.power::power_on_voltage",
  "params": { "psu_alias": "psu", "dmm_alias": "dmm", "voltage": 3.3 },
  "checks": { "VDD": "TC_VDD" },
  "step": true                          // 本条执行完暂停，等操作员点「下一步」
}
```

| 环节 | 行为 |
| --- | --- |
| 本条执行完 | 结果照常记录（通过 / 失败），随后挂起：步骤列表上方出现「单步暂停」横幅，该条目行内出现「下一步」按钮 |
| 点「下一步」 | 放行，执行清单里的下一条；运行日志记 `单步暂停 [用例名] -> next` |
| 点「中止测试」 | 本条已得结果保留，后续测试条目全部跳过，收尾段照常执行，报告结论 NOK |
| 点「取消本轮测试」 | 本条已得结果保留，后续测试条目全部跳过，收尾段照常执行，报告结论 NOK；等待期间也可直接取消本轮，无需等待超时 |
| 无人应答 | 默认 1800 秒后按中止处理；条目可用 `timeout_s` 覆盖等待时长 |
| 报告 | 该步骤类型显示「测试用例 · 单步」 |

![cmd_suit 执行环上的两类暂停点：step 单步执行与 manual 人工操作环节](assets/tps-step-hold-flow.png)

用法要点：

- 只写在需要停的那一条上：整段逐条单步就每条都写，只在关键节点停就只写关键条目。
- 与人工操作环节的区别：`step` 是「跑完再停」，提示由执行器生成，不需要 `resource/`；`type: manual` 是「执行前等人工动作做完」，提示文案与图片来自 `resource/`。
- 用例失败时不再停留：失败直接结束测试段，不会先弹单步暂停。
- 等待期间均可取消：两类暂停点出现后，界面提供「取消本轮测试」与「中止测试」两个出口，取消或中止后剩余测试条目跳过、收尾段照常执行。

### 9.4 执行顺序与作用域

- **顺序**：条目按数组顺序执行，上一条结束才开始下一条；失败记录后继续跑后续条目，整轮跑完再汇总。
- **作用域 `parallel`**：只决定这条用例在几个槽位上跑，不改变先后顺序。
  - `serial`（默认）：跑一次。
  - `per_sn`：每个已扫槽位各跑一次（空位不跑），数据按 `adaptation.multi_up.slots` 的输入组归属到对应 SN。
  - `shared`：所有槽位共用一次执行（如整机总上电、总功耗）。

### 9.5 用例条目字段

| 字段 | 必需 | 作用 |
| --- | --- | --- |
| `id` | 是 | 条目标识：报告主键、执行入口的用例 id、运行结果的续写键 |
| `case` | 除 `type: manual` 外必填 | `module::function` 用例引用，清单与代码 1:1 映射的唯一锚点 |
| `type` | 否 | 条目类型：缺省为普通自动用例；`manual` 为人工确认条目 |
| `resource` | `type: manual` 时必填 | `resource/` 下的名称，弹窗文案与图片按名取 |
| `step` | 否 | `true` = 本条执行完暂停，等操作员点「下一步」再继续（单步执行，见 9.3） |
| `parallel` | 否 | 作用域：`serial`（默认）/ `per_sn` / `shared` |
| `exec.timeout_s` | 否 | 单条超时秒数，防止卡死；超时按下不通过记录 |

不进入条目的字段（设计边界）：

| 字段 | 为何不进清单 | 它的去处 |
| --- | --- | --- |
| `params`（调用实参） | 调用实参不是基线数据 | 用例以自身默认值为准，调用形如 `fn(ctx)`；确实需要外部改动的量走 `threshold` 与 `adaptation` |
| `resources`（资源占用声明） | 资源信息不属于基线 | 槽位与信号源的关系由 `adaptation.multi_up.slots` 表达 |
| `name` / `description` / `no` / `tags` | 属用例自身属性 | 用例名与描述取自代码（**当前 `validate_v2` 仍要求 `name`，迁移中**） |
| `checks` / `optional` | 属判定逻辑 | 判定由用例调用判定接口完成 |
| `on_fail` / `retry` / `skip_when` / `always_run` / `depends_on` | 属编排策略，清单只保留"顺序" | 跑完一条再跑下一条、失败记录后继续、收尾段必定执行；中止由人工确认条目的 `abort` 或单步暂停点的中止表达 |
| `manual.message` / `expect` / `timeout_s` | 弹窗内容是资源，不是基线声明 | 按 `resource` 名称从包内 `resource/` 取 |

## 十、完整骨架

```jsonc
{
  "schema": "tps.v2",                        // 现网判据：v2 走本结构

  // ① 版本数据
  "metadata": {
    "version": "1.3.0",
    "status": "released",
    "author": "测试工艺",
    "created_at": "2026-10-07",
    "id": "SPM-RH-DYN",
    "name": "SPM 高温动态测试",
    "applies_to": "SPM-RH-DYN-01"
  },

  // ② PN 白名单 + 多路 SN 信号源匹配
  "adaptation": {
    "part_number": {
      "regex": "(?P<pn>[A-Z0-9]{10})",
      "whitelist": ["SPM1234567", "SPM1234568"],
      "whitelist_file": "resource/pn_whitelist.json"   // 可选，与内联合并
    },
    "sn": { "regex": "[A-Z0-9]{12}", "note": "12 位英数" },
    "multi_up": {
      "count": 2,            // 输入数量上限 = 槽位数：一次最多同时测 2 个 SN
      "required": 1,         // 至少扫到 1 个就允许开测（少扫一两个 SN 照常测）
      "allow_partial": true,
      "slots": [
        { "slot": 1, "sn": "A1B2C3D4E5F6", "fixture": "FX-01",   // 钉死 SN：该 SN 就走这组通道
          "inputs": [ { "signal_source": "SIG-A", "source_channel": "AWG-CH1" },
                      { "signal_source": "SIG-A", "source_channel": "AWG-CH2" } ] },
        { "slot": 2, "fixture": "FX-02",                          // 留空：按扫码顺序落位
          "inputs": [ { "signal_source": "SIG-B", "source_channel": "AWG-CH1" },
                      { "signal_source": "SIG-B", "source_channel": "AWG-CH2" } ] }
      ]
    }
  },

  // ③ 设备信息：只写引用
  "device": {
    "psu":  { "device_id": "DEV-PSU-01",  "category": "power_supply" },
    "scope":{ "device_id": "DEV-SCOPE-03","category": "oscilloscope" },
    "dmm":  { "device_id": "DEV-DMM-02",  "category": "multimeter" }
  },

  // ④ 判定算法与阈值
  "threshold": {
    "TC_VDD":  { "mode": "minmax", "min": 3.135, "max": 3.465, "label": "供电电压", "unit": "V" },
    "TC_IDD":  { "mode": "std",    "n": 3, "min_samples": 8, "label": "静态电流", "unit": "mA" },
    "TC_RPM":  { "mode": "mean",   "delta": 50, "label": "转速", "unit": "rpm" },
    "TC_RIP":  { "mode": "range",  "max_range": 20, "label": "纹波极差", "unit": "mV" },
    "TC_BAL":  { "mode": "delta",  "against": "TC_VDD_A", "max_delta": 0.05, "label": "双通道压差", "unit": "V" },
    "TC_LOSS": { "mode": "count",  "max_count": 0, "fail_when": "value > 0", "label": "丢包次数", "unit": "次" }
  },

  // ⑤ 三段：环境初始化 / 测试套 / 环境终止
  "setup": [
    { "id": "S-01", "case": "testcase.env_setup::power_on", "exec": { "timeout_s": 60 } }
  ],
  "cmd_suit": [
    { "id": "T-01", "case": "testcase.power::test_vdd", "parallel": "per_sn" },
    { "id": "T-02", "case": "testcase.comm::test_link", "parallel": "shared" },
    // 人工确认条目：弹窗给操作员 next / abort 两个选项
    { "id": "M-01", "case": "testcase.manual::appearance_check",
      "type": "manual", "resource": "appearance_check" }   // 弹窗文案与图片取自 resource/
  ],
  "teardown": [
    { "id": "E-01", "case": "testcase.env_setup::power_off" }   // 收尾段必定执行
  ]
}
```

## 十一、设计要点

- 五键职责互不重叠：`metadata` 只管版本、`adaptation` 只管能不能测、一次同时测几个 SN 与数据归属、`device` 只管设备引用、`threshold` 只管判定算法、`cmd_suit` 只管编排。
- 三类变化的改动面：换台架只改 `device` 引用或注册库记录；改判据只改 `threshold`；加用例只加 `testcase/` 模块与 `cmd_suit` 条目。
- 编排只有顺序：`cmd_suit` 是按书写顺序执行的清单，人工确认条目是唯一的控制点，弹窗内容走 `resource/`，改文案不改清单。
- 需要评审的内容集中在清单，需要维护的代码集中在 `testcase/`，需要随包分发的依赖集中在 `wheel/`，配置与数据集中在 `resource/`。
