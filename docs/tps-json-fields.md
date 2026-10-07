# tps.json 字段说明 · 设计意图与使用方式

> 分析基准（2026-10-07，逐字段核对代码而非凭印象）：
> 定义与校验 `backend/tps_runtime/manifest.py` · 运行准备与注入 `backend/tps_runtime/workspace.py` · 执行入口生成 `backend/tps_runtime/templates.py` · 运行期注入与判定 `backend/tps_runtime/conftest_template.py` · 列表/报告/落库 `backend/main.py` · 真实样例 `backend/testresource/spm_rh_dyn/tps.json`
>
> 落盘位置：`D:\ATE\docs\tps-json-fields.md`（HTML 版：`tps-json-fields.html`）· 相关文档：`TPS设计.md`
>
> 注意：本文分析的是 **TPS 清单**（`testresource/<TPS 包>/tps.json`）。`sdk/ate_drivers/kit/manifest.json` 是**驱动清单**（`key/entry/models/capabilities/interface` 等），两者不是同一个东西，详见 §7。

---

## 0. 这份文件是什么

tps.json 是**一个 TPS 包的清单**（v2 形态：一个目录里放 `tps.json` + `testcase/` 实现）。它不实现任何测试动作，只声明四件事：

1. **这个包是谁**（id / 名称 / 版本 / 归属项目与 DUT）
2. **用什么装备测**（`device_config`：别名 → 设备编号/角色）
3. **按什么判据判**（`testconfig`：阈值键 → 上下限与单位）
4. **跑哪些用例、按什么顺序**（`setup` / `cmd_suit` / `teardown`，每项指向 `testcase/` 里的一个函数）

一句话设计意图：**清单只声明"是什么"，不实现"怎么做"**；动作在 `testcase/*.py`，注入在 `conftest.py`。

---

## 1. 生命周期：字段在什么时候被谁读

| 阶段 | 载体 | 用到的字段 |
| --- | --- | --- |
| ① 发现 | `load_tps_files()` | 扫 `testresource/` 下的 `*.json`（v1 单文件）与子目录里的 `tps.json`（v2 包） |
| ② 识别 | `is_v2()` | `schema`，或"有 `cmd_suit` 且无 `steps`"兜底 |
| ③ 校验 | `validate_v2()` | `id` / `testconfig` / `device_config` / `cmd_suit` / `setup` / `teardown` 及条目字段 |
| ④ 归一化 | `normalize_tps()` → `case_entries()` → `steps_of()` | 全字段，产出 `steps` 等派生字段 |
| ⑤ 生成与注入 | `stage()` | `workspace_dir`（目录名）、`bench`（定位台架）、三段用例（渲染入口）、其余写进运行副本 |
| ⑥ 执行与记录 | `conftest.py` 读**运行副本** `tps.json` + `ate_env.json` | `testconfig` / `device_config` / `cmd_suit`；`id`/`name`/`bench` 等由 `ate_env.json` 提供 |

关键点：运行期 conftest 读的是**复制到 run 目录里的副本**（`stage()` 每次都 `copytree` 源目录），所以改源 TPS 立即生效、且各次运行互不污染。

---

## 2. 顶层字段总表

| 字段 | 必需 | 类型 | 设计意图 | 使用方式（读取点） |
| --- | --- | --- | --- | --- |
| `schema` | 否（建议写） | `"tps.v2"` | 版本门闩，决定走 v1 还是 v2 代码路径 | `is_v2()`；`validate_v2` 只放行 `tps.v2`；归一化后强制写回 `tps.v2`；前端按它筛运行时 TPS |
| `id` | **是** | 串（`[A-Za-z0-9_.-]{1,64}`） | TPS 的稳定标识 | `find_tps()` 用它匹配；`ate_env.json:tps_id`；报告与 `test_records.tps_id` |
| `name` | 否 | 串 | 人读名称 | 列表/报告标题（`main.py:621/872/897/903`）、`tps_name` 列、`_summar`、见 `steps` 场景文案 |
| `version` | 否 | 串（默认 `2.0.0`） | 程序集版本，便于追溯 | 列表摘要、报告副标题、`test_records` 间接体现；**不参与校验、不参与判定** |
| `description` | 否 | 串 | 给人看的整体说明 | 列表摘要卡片、TPS 列表接口 |
| `project` | 否 | 串 | 归属项目，用于分组与落库 | `main.py:381` 分组名（空则"未分组项目"）、报告、`test_records.project` |
| `dut` | 否 | 串 | 被测对象类型，用于分组与落库 | `main.py:382` 分组名、报告、`test_records.dut` |
| `workspace_dir` | 否 | 串 | 指定运行目录名（多人多台架同 TPS 并行时避免撞目录） | `tps_dir_name()`：优先级 `workspace_dir` → `name` → `id`，非法字符替换为 `_` |
| `environment` | 否 | 对象 | 运行环境的**人读快照**（设备组合、接口、固件、工位） | 列表摘要、报告"运行环境"表：`env.items()` 逐行渲染 |
| `bench` | 否 | 对象 | 声明这台 TPS 该跑在**哪台已注册台架**上 | `_resolve_bench()`：`bench_id` → `preset_id` → `serial` 依次匹配注册库；命中写进 `ate_env.json` |
| `device_config` | **是** | 对象（非空） | 别名 → 设备引用；用例通过别名取驱动 | conftest `_load_devices()`：按 `device_id` 精确匹配，否则 `match_device()` 模糊匹配 |
| `testconfig` | **是** | 对象（非空） | 阈值集中管理，供判定取值 | conftest `limit()` / `check()`；报告阈值明细；`checks` 的引用校验源 |
| `setup` | 否 | 数组 | 环境初始化用例（上电、复位、激励预置） | `case_entries()` → 步骤 `kind=init`，入口函数 `test_setup` |
| `cmd_suit` | **是** | 数组（非空） | 测试套排列：测什么、什么顺序 | `case_entries()` → `kind=test`、`test_case`；conftest 里 `expected=len(cmd_suit)` 断言用例数 |
| `teardown` | 否 | 数组 | 环境终止与设备释放 | `case_entries()` → `kind=teardown`、`test_teardown` |
| `steps` | 否（v1 遗留） | 数组 | v1 的平铺步骤列表 | 与 `cmd_suit` **互斥**（同时出现直接报错）；v1 单文件仍走这条路 |

---

## 3. 逐字段详解

### A. 身份与元信息

#### `schema` — 版本门闩

- **意图**：让解析器用一条判据决定走哪套读取逻辑，而不是"猜结构"。
- **使用**：`is_v2()` 判据是 `schema == "tps.v2"` **或**（`cmd_suit` 存在且 `steps` 不存在）。归一化时被强制写回 `tps.v2`（`manifest.py:232`），因此**下游拿到的永远是 v2**。前端 `EquipmentView.vue:497` 用它过滤可选 TPS（`t.schema === 'tps.v2' || cmd_suit_count > 0`）。
- **约束**：只接受 `"tps.v2"` 或省略；写别的值会被校验拦下。
- **坑**：写成 `tps.v3` 时，`is_v2()` 因兜底判据仍返回 `True`，但 `validate_v2` 报 `schema 只支持 tps.v2` —— 表现为"被当成 v2 却过不了校验"。改版本号必须同时改 `is_v2`、`validate_v2`、归一化三处。

#### `id` — TPS 的稳定标识

- **意图**：运行、报告、落库三处引用同一个稳定键。
- **使用**：`find_tps()` 在 `testresource/` 下按 id 找到包；`stage()` 写进 `ate_env.json:tps_id`；报告与 `test_records.tps_id`；`workspace.py:483` 打包列表。
- **约束**：必填、`[A-Za-z0-9_.-]{1,64}`、不能与目录名冲突（建议与包目录同名）。
- **坑**：`id` 是查询键，改了它历史 `test_records.tps_id` 就对不上同一条时间线。

#### `name` — 人读名称

- **意图**：报告与界面上的展示名，避免暴露 id。
- **使用**：`normalize_tps` 里 `name` 缺省取 `id`；报告标题与副标题、`tps_name` 列、运行目录名（`workspace_dir` 未写时的第二优先级）。
- **约束**：无格式校验；会被用于目录名，因此带 `/ \ : * ? " < > |` 的字符会被替换为 `_`。
- **坑**：改 `name` 会**改运行目录名**（当 `workspace_dir` 为空时），历史运行目录与新目录会分家。

#### `version` — 程序集版本

- **意图**：追溯"这份报告是用哪版 TPS 跑出来的"。
- **使用**：仅展示——列表摘要、报告副标题（`main.py:898` 的 `v{version}`）。
- **约束**：无校验（驱动清单的 `version` 有 `x.y.z` 校验，这里没有）。
- **坑**：`version` 不参与判定、不进 `test_records` 独立列，只间接体现在报告文件里。若现场需要"报告能按版本检索"，需另加列或把版本写进 `description`。

#### `description` — 整体说明

- **意图**：给测试工程师读的一句话范围说明（样例里写了整条测试流程顺序，很实用）。
- **使用**：列表摘要（`_tps_summary`）、TPS 列表接口。
- **约束**：自由文本。

#### `project` / `dut` — 分组与落库维度

- **意图**：让"装备树/记录查询"能把 TPS 归入 项目 → DUT 的层级，并落进固定记录条目。
- **使用**：`main.py:381-382` 用它们做 TPS 列表分组（空值落到"未分组项目/未分组 DUT"）；报告表头（`main.py:857-858`）；运行日志头（`main.py:971-972`）；`test_records.project` / `test_records.dut` 两列（`main.py:1046-1047`）。
- **约束**：自由文本、可空，**但落库后是过滤维度**（`main.py:480/542` 的记录查询支持按项目/批次过滤）。
- **坑**：既然是查询维度，就别写同义不同字的项目名（"SPM 项目" vs "SPM项目"），否则记录查不全。

### B. 运行落位与台架

#### `workspace_dir` — 运行目录名

- **意图**：同一个 TPS 在多台架/多人并发跑时，用不同目录名隔离运行副本；也让现场能按中文名找到自己的运行目录。
- **使用**：`tps_dir_name()` 生成 `workspace/<目录名>/`，其下每次运行建 `run_<task_id>/`（`workspace.py:102-111`、`stage()`）。
- **约束**：非法文件名字符被替换为 `_`；为空则退回 `name`，再退回 `id`。
- **坑**：它是**运行落位的唯一决定项**，一旦改动，旧运行记录仍留在旧目录里（`list_runs()` 按新目录名扫描），现场会以为"运行记录丢了"。

#### `environment` — 运行环境快照（人读）

- **意图**：把"这套测试依赖什么装备组合、什么接口、什么固件、在哪个工位"记录在案，属于**可读性元数据**，不驱动任何逻辑。
- **使用**：列表摘要原样返回（`main.py:626`）；报告里逐行渲染成"运行环境"表（`main.py:829-830`）。
- **约束**：对象，键值自由。
- **坑**：报告是 `f"<td>{v}</td>"` 直接插入。样例把 `interfaces` 写成**数组**，页面上会渲染成 Python 列表字面量（`['LAN (SCPI)', '串口 (COM6 115200)']`）。想要好看就写成字符串，或用多个键（如 `interface_lan` / `interface_serial`）。

#### `bench` — 台架定位声明

- **意图**：声明"这份 TPS 设计上跑在哪台台架"，让 Runner 不必让操作员每次手选台架。
- **使用**：`_resolve_bench()` 依次尝试：

  | 优先级 | 来源 | 说明 |
  | --- | --- | --- |
  | 1 | 运行请求里的 `bench_id` | 操作员显式指定，最高优先 |
  | 2 | `bench.bench_id` | 指定某台**具体台架** |
  | 3 | `bench.preset_id` | 指定**台架类型**，取该类型下最早注册的一台 |
  | 4 | `bench.serial` | 按台架序列号（`SPMTS+12位`）精确匹配 |

- **约束**：三者皆可空；命中后把 `id/title/serial/preset_id` 写进 `ate_env.json`，conftest 再据此去 SQLite 取设备连接参数。
- **坑**：
  - **匹配失败不阻断**：`_resolve_bench` 返回空 bench + 一条 `bench_warning`，`stage()` 仍继续准备运行。只有显式指定的 `bench_id` 不存在时才给出明确失败原因。
  - `preset_id` 是"找一台同类台架"的**线索**，不是设备清单；实际有哪些设备、什么 IP，一律以注册库为准。凭 `preset_id` 推断设备会漂移。
  - 样例里 `bench_id` 与 `serial` 都是空串（占位），只有 `preset_id` 有值——这正是"按类型选台架"的用法。

### C. 装备引用 `device_config`

- **意图**：用例需要"示波器"，但不该知道示波器的 IP；于是清单提供一层**别名 → 设备引用**，把"业务命名"与"物理连接"解耦。
- **结构**：`{别名: {device_id | role | category | model, note?}}`；也允许简写 `{别名: "设备编号"}`。
- **使用**（conftest `_load_devices()`）：
  1. 先按 `device_id` 在测试台已注册设备里**精确命中**；
  2. 未命中则调 `ate_db.match_device(bench_id, spec)` 按 `role/category/model` **模糊匹配**；
  3. 都失败 → 该别名进 `driver_errors`，用例侧 `ctx.driver(alias)` 报"无法建立驱动"，报告可见；`on_driver_error=skip` 时现场可降级为 skip。
- **约束**：必填、非空；每个别名至少要有 `device_id` / `role` / `category` / `model` 之一。
- **设计意图的边界**：这里是**引用**，不是配置。IP/端口/串口/波特率全部由 `ate_db` 从 SQLite `device_registry` 取，`conftest._resource_of()` 再拼成 `TCPIP0::host::port::SOCKET` 这类资源串。
- **`note` 的定位**：纯注释，**不参与任何匹配**（样例里用它是好习惯："回放信号波形观测"、"被动设备，仅登记信息"）。
- **坑**：别名是**用例与清单之间的隐式契约**——用例里写 `ctx.driver("psu")`，清单里别名必须存在且拼写一致。改名别名 = 改契约，但没有一处校验能发现（除运行期 `driver_errors`）。

### D. 判据 `testconfig`

- **意图**：阈值是**受控数据**，要能被人评审、按批次调整、在报告里显示；因此集中在一个字典里，而不是散落在用例代码中。
- **结构**：`{阈值键: {label, min, max, unit, nominal}}`。
- **使用**：

  | 消费点 | 行为 |
  | --- | --- |
  | conftest `limit(key)` | 取该键配置；键不存在直接 `KeyError`（列明"testconfig 中没有阈值定义"） |
  | conftest `check(key, value)` | 用 `min` / `max` 判定，产出明细 `{key,label,value,min,max,unit,ok,message}`，写进 `run_result.json` |
  | conftest `expect(key, value)` | 同上，但不通过时抛 `LimitError` → pytest 判 FAIL |
  | `validate_v2` | ① 每个键至少要给 `min` 或 `max`；② 值必须是数字；③ `min > max` 报错；④ 用法条目的 `checks` / `config` 引用未定义键时报错 |
  | 报告 | 展示实测值、允许区间与结论 |

- **约束**：必填、非空；`min` / `max` 至少其一；`unit` / `label` 用于消息与展示。
- **坑**：
  - `nominal`（标称值）**不参与判定**，只在报告里给人看——别指望它参与容差计算。
  - `label` 缺省回退为阈值键本身（`limit()` 里 `setdefault("label", key)`），空着也不会报错，但报告上就是一串 `TC_XXX`。
  - 只支持上下限，没有 `std` / `severity` / 判据类型等表达能力（见 §5）。

### E. 用例三段与条目字段

三段结构：`setup[]`（初始化）→ `cmd_suit[]`（测试套）→ `teardown[]`（终止）。**顺序即执行顺序**，`case_entries()` 按下标生成 `no`（未显式指定时）。

| 条目字段 | 必需 | 设计意图 | 使用方式 |
| --- | --- | --- | --- |
| `id` | 否（建议写） | 报告与结果的稳定键 | 缺省自动生成 `SETUP01` / `TC001` / `TD01`；conftest 按 id 做 `_upsert` 续写；`parametrize(ids=[...])` 与 pytest 节点 id |
| `no` | 否 | 早期给前端显示的序号 | 缺省取数组下标；进 `steps[].v2.no` |
| `name` | **是** | 报告与日志里的用例名 | 报告步骤表第一列、运行日志、进度条文案 |
| `case` | **是** | 指向 `testcase/` 实现（`module::function` 或 `module::Class.method`） | `_load_callable()` 动态导入并调用；格式错误在校验期拦下 |
| `description` | 否 | 该用例测什么、怎么测 | 归一化进 `steps[].description`；**当前无界面渲染消费点**（见 §5） |
| `params` | 否 | 传给用例的调用实参 | `fn(ctx, **params)`；样例里既有业务参数（`voltage`/`rpm`/`freq`/`vpp`）也有设备别名（`psu_alias`…） |
| `checks` | 否 | 测量值字段 → 阈值键的映射 | conftest `_apply_checks()`：把用例返回值按此映射逐项 `ctx.check()`；未定义键在校验期报错 |
| `config` | 否 | 单值返回场景的简写：直接把返回值按某个阈值判 | `checks` 缺省时等价于 `{"value": config}`（`conftest_template.py:494-495`） |
| `optional` | 否 | 语义上想表达"这条可以不过" | **执行侧不读**（只有 `validate_v2` 校验布尔类型、`case_entries`/`steps_of` 透传）。见 §5 |

三段的入口函数由 `templates.py` 生成：`test_setup` / `test_case` / `test_teardown` 各一个 `parametrize`，把整段条目**repr 进生成文件**。这意味着：**cmd_suit 里写的每个字段都会出现在生成的 `test_tps_generated.py` 里**，字段越多、生成物越臃肿。

### F. v1 遗留与归一化派生字段（不在手写清单里出现）

| 字段 | 来源 | 说明 |
| --- | --- | --- |
| `steps` | 手写（v1） | v1 平铺步骤；与 `cmd_suit` 互斥，同时出现报错 |
| `steps` / `step_count` | `normalize_tps` 派生 | 由三段用例展开，供既有任务模型、进度、报告复用 |
| `testconfig_count` / `device_alias_count` | `normalize_tps` 派生 | 列表摘要与前端运行面板展示（`EquipmentView.vue:779`） |
| `_raw` | 派生 | 原始清单副本，`stage()` 校验与写副本时用它，避免把派生字段写回文件 |
| `_manifest_file` / `_source` / `_generated` | 派生 | 清单路径 / 来源目录 / 生成入口文件名（拼 pytest 节点 id 用） |
| `kind` / `func` | `case_entries` 派生 | `init|test|teardown` 与 `test_setup|test_case|test_teardown`，跟着所在段落走，不用手写 |

---

## 4. 字段去向：一份 tps.json 的字段分别落在哪

| 去向 | 涉及的字段 |
| --- | --- |
| **运行身份**（`ate_env.json`） | `id` → `tps_id`、`name` → `tps_name`、`bench.*` → `bench_id/title/serial/preset_id`；`uut`/`task_id`/`mode` 来自运行请求而非清单 |
| **运行期判定与设备**（conftest 读运行副本 `tps.json`） | `testconfig`、`device_config`、`cmd_suit`（仅用于算 `expected` 用例数） |
| **HTML 报告** | `name`、`version`、`id`、`environment`（逐行）、`project`、`dut`、`steps[].name`、`run_result.json` 的实测与判定明细 |
| **`test_records` 落库**（25 列） | `project`、`dut`、`id`→`tps_id`、`name`→`tps_name`；`batch`/`part_no`（SN 前 10 位）/`operator`/`equipment_serial`/`result`(OK-NOK) 来自 UUT 档案与测试台信息，不来自清单 |
| **前端** | `schema`（筛选用）、`testconfig_count`、`device_alias_count`、`cmd_suit_count`、`step_count`、`environment`、`bench`、`workspace_dir` |

---

## 5. 现网观察：死字段、双源与坑

以下每条都对应可复查的代码位置，不是风格偏好。

| # | 观察 | 证据 | 影响 / 建议 |
| --- | --- | --- | --- |
| 1 | **`optional` 是死字段** | 全库仅 `manifest.py:158/197/221` 出现（校验类型、透传），conftest 与 runner 均不读 | 写了"可跳过"不产生任何效果。要么在 `run_case` 里实现对 `optional` 的失败策略，要么删掉 |
| 2 | **判定双轨**：`checks` 映射与用例内 `ctx.expect` 并存 | `_apply_checks()` 只在"用例返回值 + checks 映射"路径生效；用例若自己 `expect` 了，返回值再被映射一次就是**重复判定** | 二选一并写进准则：清单映射（现状）或用例自查（更贴近"报告由用例生成"） |
| 3 | **`params` 混入设备别名** | 样例 `psu_alias`/`awg_alias`/`dmm_alias`/`scope_alias`/`motion_alias`/`fixture_alias`/`eload_alias` | 别名已在 `device_config` 有唯一事实源，多通道并行时人工拼别名必错；建议用例统一 `ctx.driver("psu")`，`params` 只留数值 |
| 4 | **`no` 与数组顺序双源** | `case_entries`：`"no": case.get("no", i)` | 重排顺序忘了改 `no`，报告序号与执行次序不一致。建议只留顺序 |
| 5 | **`name` 必填但纯展示** | `_validate_case`：`if not case.get("name"): errors.append(...)` | 与 `testcase/` 函数名/docstring 重复；如果名字以代码为准，需把这条必填降级 |
| 6 | **条目 `description` 无渲染消费点** | 仅 `steps_of()` 透传到 `steps[].description`；报告步骤表用的是 `steps[].name` 与运行期 `detail` | 写了没人看。要么在报告里展示，要么与 docstring 合并去重 |
| 7 | **`version` 无校验、无独立落库列** | `normalize_tps` 只 `setdefault("version", "2.0.0")` | 报告能看、但无法按版本检索记录；如需追溯要加列 |
| 8 | **`unit` / `nominal` 不参与判定** | `check()` 只用 `min`/`max`；`unit` 仅用于消息文本 | `nominal` 只作展示；若将来要支持"标称 ± 容差"，需要改解析 |
| 9 | **`environment` 的数组值会被原样字符串化** | `main.py:830`：`f"<tr><td>{k}</td><td>{v}</td></tr>"` | 样例的 `interfaces` 是数组，报告上显示为 Python 列表字面量。建议值统一为字符串 |
| 10 | **`workspace_dir` 变更等于换落位** | `tps_dir_name()` → `workspace/<名>/run_<task_id>` | 改名后旧运行记录留在旧目录，`list_runs()` 不再扫到，现场会误判"记录丢失" |
| 11 | **台架匹配失败不阻断** | `_resolve_bench()` 返回空 bench + `bench_warning`，`stage()` 继续 | 设备解析全失败要等到用例调 `ctx.driver()` 才暴露（走 `driver_errors`）。建议准备阶段就把"一个设备都没匹配上"升级为显式确认 |
| 12 | **`project` / `dut` 是自由文本却是查询维度** | `main.py:480/542` 记录查询按项目过滤；`test_records.project/dut` | 同义不同字会让记录查不全，建议在元数据管理里收敛候选值 |
| 13 | **`device_config.note` 不参与匹配** | `_load_devices` 只按 `device_id` / `match_device(spec)` | 混淆为"配置项"会误以为它有作用；它是注释 |
| 14 | **v1/v2 双路径并存** | `read_tps()`：v2 归一化、v1 原样返回；`validate_v2` 检查 `steps` 与 `cmd_suit` 互斥 | 两种清单都能被加载，维护时注意不要混写 |

---

## 6. 最小可用清单

必需项只有 4 个：`id`、`testconfig`、`device_config`、`cmd_suit`。条目必需 2 个：`case`、`name`。

```jsonc
{
  "schema": "tps.v2",
  "id": "spm_rh_dyn",                                  // 必需 · 稳定标识
  "name": "SPM 读头动态测试",                            // 建议（报告展示名）
  "testconfig": {                                       // 必需 · 非空
    "TC_VDD": { "label": "读头上电电压", "min": 3.135, "max": 3.465, "unit": "V" }
  },
  "device_config": {                                    // 必需 · 非空
    "dmm": { "device_id": "rh-dmm", "role": "dmm" }
  },
  "cmd_suit": [                                         // 必需 · 非空
    { "id": "TC001",                                    // 建议（结果主键）
      "name": "上电电压测试",                             // 必需
      "case": "testcase.power::power_on_voltage",       // 必需
      "checks": { "VDD": "TC_VDD" } }                   // 可选：返回值字段 → 阈值键
  ]
}
```

---

## 7. 别混淆：TPS 清单 vs 驱动清单

| | TPS 清单 | 驱动清单 |
| --- | --- | --- |
| 文件 | `testresource/<包>/tps.json` | `sdk/ate_drivers/kit/manifest.json` |
| 解析代码 | `backend/tps_runtime/manifest.py` | `sdk/ate_drivers/kit/registry.py` |
| schema 值 | `tps.v2` | `MANIFEST_SCHEMA`（驱动清单自有的值） |
| 关键字段 | `testconfig` / `device_config` / `cmd_suit` | `key` / `entry` / `models` / `api` / `capabilities` / `interfaces` |
| 作用 | 描述"测什么、用什么装备、按什么判据" | 描述"这个驱动支持哪些型号、暴露哪些能力" |

两者都叫 manifest、都有 `schema` / `version` / `name`，grep 时极易串台。

---

## 8. 小结：字段设计的三条意图

1. **声明式**：清单只声明"测什么、按什么判"，实现留在 `testcase/`——所以 `case` 是引用，`description` 只是说明，**没有任何执行逻辑藏在清单里**。
2. **引用而非配置**：装备只写"别名 → 设备编号/角色"，连接参数一律走 SQLite 注册库；台架只写"该跑在哪台/哪类台架"，具体设备由注册库决定。这让同一份 TPS 能跨台架复用。
3. **判据集中、判定分离**：`testconfig` 集中放阈值（可评审、可按批次调整），判定动作由消费方执行（conftest 的 `check` / 用例的 `expect`）。阈值是数据，判定是行为，两者不混。

按这三条回看那份样例，会发现它的问题不在"写错了"，而在**清单里塞了本该属于代码的东西**（别名参数、用例描述、序号），以及**声明了却不生效的东西**（`optional`）。这也是 §5 那 14 条观察的共同根源。
