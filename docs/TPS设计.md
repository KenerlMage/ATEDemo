# TPS 设计说明 · uut.json 驱动测试

> 版本 v2.1 · 2026-10-08 · 适用对象：ATE Runner（`D:\ATE`）
> 交付位置：`D:\ATE\docs\TPS设计.md`（HTML 版：`TPS设计.html`）
> 本文取代此前基于「cmd_suit 承载判定映射」的方案。凡与本文冲突的旧设计，以本文为准。
>
> **2026-10-08 更新**：`metadata` 改为 TPS 的「身份 / 归属 / 追溯」块——
> `tpsname` / `tpsversion` / `subsystem` / `testbenchtype` / `uutcategory` / `processnumber` / `author` / `version` / `productioninfo`，详见 §4.1。

---

## 0. 一句话

uut.json 不是"测试脚本的替身"，而是**一份受控的测试基线清单**：它说明"这一批 UUT 该跑哪些用例、按什么顺序跑、判据取什么值、绑到哪台台架、适配哪个批次"。
用例业务逻辑在 `testcase/*.py`，注入在 `conftest.py`，报告由用例产出，uut.json 只做它不可替代的四件事。

---

## 1. 设计前提（现网事实，均已核对代码）

| 事实 | 位置 | 对设计的影响 |
| --- | --- | --- |
| 公共 conftest 由后端部署到 `workspace/conftest.py`，版本号 `ATECONFTEST_VERSION = 2`，版本一致则保留（避免覆盖现场修改） | `tps_runtime/conftest_template.py:31`、`workspace.py:70` | conftest 是**唯一的注入通道**，改注入契约 = 升版本号 |
| 夹具：`ate_ctx` / `ate_bench` / `ate_devices` / `ate_thresholds` / `driver` / `ate_runner` | `conftest_template.py` | 用例拿装备、阈值、驱动全靠这几个 |
| `AteContext` 从 SQLite 读装备（`bench_registry` / `device_registry`），从清单读 `testconfig` | `conftest_template.py` `_load_bench` / `_load_devices` | 连接参数的事实源是数据库，不是 uut.json |
| 执行器 `run_case()`：`fn = _load_callable(case["case"]); fn(ctx, **case["params"])` | `conftest_template.py` `run_case` | `case` 与 `params` 是**调用契约**，必须留在 cmd_suit |
| 判定 `_apply_checks()`：把用例返回值的字段按 `case["checks"]` 映射到 testconfig 键，调 `ctx.check(key, value)` | 同上 `_apply_checks` | 这是当前 `checks` 唯一的用途——也是本文要移除的东西 |
| `AteContext.expect(key, value)` 可按阈值断言并抛 `LimitError`（继承 `AssertionError`） | 同上 | **用例自我判定的能力已经存在**，移除 checks 无需新机制 |
| 每条用例是**一次独立的 pytest 进程**，`run_result.json` 按 `id` 续写/覆盖 | `_resume_results` 注释 | 编排=Runner 逐条拉起进程；并行是 Runner 层的事 |
| 生成入口 `test_tps_generated.py` 把 setup/cmd_suit/teardown 条目 **repr 成 Python 字面量**，`parametrize(..., ids=[c["id"]])` | `tps_runtime/templates.py` | cmd_suit 的每个字段都会进生成文件；字段越多，生成物越臃肿 |
| 校验 `validate_v2`：`name`、`case` 必填；`checks` 引用未定义阈值会报错；`testconfig` 每项至少给 `min` 或 `max`；`device_config` 必填 | `tps_runtime/manifest.py` | 移除 checks 后，那条唯一的跨字段校验会一并失效（见 §12） |
| 落库表 `test_records`：25 列，15 行（`task_id / uut / project / dut / tps_id / tps_name / status / passed / failed / skipped / total / duration / report_file / start_time / end_time / created_at / batch / part_no / operator / equipment_serial / result`） | `backend/ate.db` | 这就是"标准化固定条目、承载有限"的落点 |
| 现网**不存在** R1/R2 概念（全库 grep 零命中） | — | R1/R2 是你新引入的分级，本文按其约束推导（见 §11、§15） |

---

## 2. 分层职责

五个角色，各自只干一件事。**任何信息只允许有一个事实源**，这是全文的判据。

| 层 | 载体 | 负责 | 明确不负责 |
| --- | --- | --- | --- |
| **基线层** | `uut.json` | 用例基线（跑哪些、怎么排）、PN 适配校验、台架绑定、判据预置值 | 不写用例业务逻辑、不写用例名/描述、不写判定映射、不写连接参数、不定义报告结构 |
| **用例层** | `testcase/*.py` | 业务动作、自我判定（`ctx.expect`）、产出测量值与判定明细 | 不关心执行顺序、超时、重试、在哪个通道跑 |
| **注入层** | `conftest.py` | 环境/装备/驱动/阈值注入，`run_case` 执行与记录 | 不写任何具体判据值 |
| **执行层** | `main.py` + `tps_runtime/` | 校验 → 生成入口 → 逐条拉起 pytest → 汇总 → 报告 → 落库 | 不解释业务语义 |
| **存储层** | `ate.db` | `test_records` 固定条目；`bench_registry`/`device_registry` 提供连接参数 | 不承载明细数据 |

数据流（一张图看懂）：

```
testcase/*.py ──(被 run_case 调用)──► conftest.ctx ──注入──► 装备(SQLite) / 驱动 / 阈值(uut.json)
      │                                                        ▲
      │ ctx.expect(键, 实测值) 自查判定                          │ testconfig 判据（min/max/std）
      ▼                                                        │
run_result.json ──► Runner 汇总 ──► HTML 报告 ──► test_records（固定条目落库）
```

---

## 3. 承载边界：什么进 uut.json，什么不进

这是本文最重要的一张表。判据一句话：**只有"与用例业务逻辑无关、且与实际台架或本批 PN 有关"的配置才进 uut.json。**

| 内容 | 进 uut.json？ | 放哪 | 理由 |
| --- | --- | --- | --- |
| 跑哪些用例、执行顺序 | ✅ | `baseline.cmd_suit` | 编排语义，用例代码无从知道 |
| 超时、重试次数与间隔、失败策略、条件跳过 | ✅ | `baseline.*.exec` / `skip_when` | 运行策略，属编排 |
| 用例名、描述、标签、默认参数值 | ❌ | `testcase/*.py` docstring / marker / 形参 | 用例自身属性，写两遍必然漂移 |
| 测量值与阈值的对应关系（判定映射） | ❌ | 用例内 `ctx.expect("TC_VDD", v)` | 判定逻辑属业务，随用例走 |
| 判据预置值（min / max / std） | ✅ | `testconfig` | 随批次与 PN 调整、需工程师评审，不能埋进代码 |
| 设备种类与别名 | ✅ | `bench.device_config` | 与业务逻辑无关，只与实际台架有关 |
| 设备连接参数（IP / 端口 / 串口） | ❌ | SQLite `device_registry` | 换台架即变，写进受控文件就是灾难 |
| PN 批次号、SN 规则、并行通道结构 | ✅ | `adaptation` | 本 TPS 的适配范围，运行期校验 |
| 报告/记录字段 | ❌ | 用例产出 + 固定记录模板 | R1/R2 是固定条目，uut.json 无权定义 |
| 用例实现引用（`module::function`） | ✅ | `baseline.*.case` | 1:1 映射的唯一锚点 |

---

## 4. uut.json 结构（五块）

```
uut.json
├── metadata      ① 基线身份：这是哪一份基线、什么版本、谁批的
├── adaptation    ② PN 校验：适配哪些批次与 SN、并行几路
├── bench         ③ 台架绑定：设备别名 → 类别/通道/模式（不含 IP）
├── testconfig    ④ 判据预置：min / max / std 等，供 ctx.expect 取用
└── baseline      ⑤ 用例基线 + 编排：setup / cmd_suit / teardown
```

### 完整骨架（JSONC）

```jsonc
{
  "schema": "tps.v2",
  "metadata": {
    "tpsname": "spm-wsec-static-v1.0.0-tps-v1.0.0",  // TPS 名称：子系统-测试台-ATE版本-TPS-TPS版本
    "tpsversion": "v1.0.0",              // TPS 版本
    "subsystem": "spm",                  // 子系统，默认 spm
    "testbenchtype": "wsec-static",       // 测试台类型，默认 wsec-static
    "uutcategory": "module",             // UUT 等级：module 模块 / component 部件
    "processnumber": "OP111111111",      // 工序号
    "author": "10001234",                // 创建者工号
    "version": "v1.0.0",                 // 创建该 TPS 的 ATE 规则版本
    "productioninfo": { "machineid": "1000D" },   // 生产信息：工控机编号，默认 1000D
    "status": "released",                // draft | review | released
    "created_at": "2026-10-08"
  },

  "adaptation": {
    "part_number": "SPM-RH-DYN-01",
    "batch": { "extract_regex": "^([A-Z]{3}\\d{8})", "whitelist": ["BATCH-20260801"] },
    "sn": { "regex": "^[A-Z0-9]{12}$", "note": "12 位英数" },
    "channels": {
      "count": 1,                    // 单件测试；并行时改 4
      "slots": [
        { "channel": 1, "fixture": "FX-A", "alias_suffix": "_c1",
          "resource_bindings": [{ "alias": "scope", "instrument_channel": "CH1" }] }
      ],
      "shared_resources": []
    }
  },

  "bench": {
    "device_config": {
      "scope": { "category": "示波器", "device_id": "rh-scope", "mode": "simulate" },
      "dmm":   { "category": "万用表", "device_id": "rh-dmm",   "mode": "simulate" },
      "psu":   { "category": "电源",   "device_id": "rh-psu",   "mode": "simulate" },
      "spin":  { "category": "运动控制", "device_id": "rh-spin", "mode": "simulate" }
    }
  },

  "testconfig": {
    "TC_VDD":  { "label": "供电电压", "type": "range", "min": 3.135, "max": 3.465, "nominal": 3.3, "unit": "V" },
    "TC_IDD":  { "label": "静态电流", "type": "range", "max": 120, "unit": "mA" },
    "TC_RPM":  { "label": "转速", "type": "stats", "nominal": 3600, "std": 30, "k": 3, "n": 5, "unit": "rpm" },
    "TC_SIG_VPP": { "label": "回放信号幅度", "type": "range", "min": 2.28, "max": 2.52, "unit": "V" }
  },

  "baseline": {
    "setup": [
      { "id": "SETUP01", "case": "testcase.env_setup::init_environment",
        "params": { "voltage": 3.3 },
        "exec": { "timeout_s": 120, "retry": { "max": 1, "interval_s": 3.0 }, "on_fail": "abort" } }
    ],
    "cmd_suit": [
      { "id": "TC001", "case": "testcase.power::power_on_voltage",
        "params": { "voltage": 3.3 },
        "exec": { "timeout_s": 30, "retry": { "max": 2, "interval_s": 1.0 }, "on_fail": "abort" } },
      { "id": "TC002", "case": "testcase.power::read_head_current",
        "exec": { "timeout_s": 45, "retry": { "max": 1, "interval_s": 2.0 } } },
      { "id": "TC003", "case": "testcase.comm::spin_speed",
        "params": { "rpm": 3600 },
        "exec": { "timeout_s": 60, "retry": { "max": 3, "interval_s": 2.0, "on": ["error", "timeout"] } },
        "parallel": "shared" },
      { "id": "TC004", "case": "testcase.comm::playback_signal",
        "params": { "freq": 1000, "vpp": 2.4 } },
      { "id": "TC005", "case": "testcase.comm::load_voltage",
        "params": { "current": 0.5 },
        "skip_when": "not device.eload.present" }
    ],
    "teardown": [
      { "id": "TD01", "case": "testcase.env_setup::teardown_environment",
        "exec": { "timeout_s": 90, "on_fail": "warn" }, "always_run": true }
    ]
  }
}
```

> 说明：`setup` / `cmd_suit` / `teardown` 现在位于 `baseline` 之下。若不想动现网 `case_entries()` 的三段取值，可先保持三段平铺在顶层，仅把 `metadata` / `adaptation` / `bench` 作为新增块——**功能等价，命名可后置**。

### 4.1 metadata 字段（2026-10-08 更新）

| 字段 | 必需 | 作用 | 取值 / 默认 |
| --- | --- | --- | --- |
| `tpsname` | 是 | TPS 名称，命名规则 `子系统-测试台-ATE版本-TPS-TPS版本` | 例 `spm-wsec-static-v1.0.0-tps-v1.0.0` |
| `tpsversion` | 是 | TPS 版本 | 例 `v1.0.0` |
| `subsystem` | 是 | 子系统 | 默认 `spm` |
| `testbenchtype` | 是 | 测试台类型 | 默认 `wsec-static` |
| `uutcategory` | 是 | UUT 等级 | `module`（模块）/ `component`（部件） |
| `processnumber` | 是 | 工序号 | 例 `OP111111111` |
| `author` | 是 | 创建者工号 | 例 `10001234` |
| `version` | 是 | 创建该 TPS 的 **ATE 规则版本**（非 TPS 版本） | 例 `v1.0.0` |
| `productioninfo.machineid` | 是 | 生产信息·工控机编号 | 默认 `1000D` |
| `status` | 否 | 启动门禁：只有 `released` 允许正式生产 | `draft` / `review` / `released` |
| `created_at` | 否 | 创建日期 | `YYYY-MM-DD` |

> **与上一版的差异**：`name` → `tpsname`；`version` 由「基线版本号」改为「ATE 规则版本」，
> TPS 自身版本另立 `tpsversion`；`applies_to` 由 `testbenchtype` 取代；
> 新增 `subsystem` / `uutcategory` / `processnumber` / `tpsversion` / `productioninfo`；`author` 由姓名改为工号。
>
> **落地状态**：`metadata` 目前尚未进入 `validate_v2`，运行期也不消费它（真实样例
> `backend/testresource/spm_rh_dyn/tps.json` 尚未含该块）。本版是**模板与文档先行**，
> 代码接入见 §13 迁移路径「阶段 0」。

---

## 5. cmd_suit 条目字段（收敛后只留 3 个必需 + 4 个可选）

| 字段 | 必需 | 作用 | 为什么不能更省 |
| --- | --- | --- | --- |
| `id` | ✅ | 报告主键、`parametrize` 的用例 id、`run_result.json` 的 upsert 键 | 现网 `record()` / `_upsert()` 全靠它 |
| `case` | ✅ | `module::function` 引用，1:1 映射锚点 | `run_case` 用它 `_load_callable` |
| `params` | ✅ | 调用实参：`fn(ctx, **params)` | 现网契约如此；只放数值，**不放设备别名**，别名从 `ctx` 取 |
| `exec` | 可选 | `timeout_s` / `retry{max,interval_s,on}` / `on_fail` | 编排策略，用例不该自己重试 |
| `parallel` | 可选 | `per_channel`（默认）/ `shared` | 并行调度信息 |
| `skip_when` | 可选 | 条件跳过（设备缺失等） | 编排语义 |
| `resources` | 可选 | 并行时的仪器占用声明 | 与 `adaptation.slots.resource_bindings` 需一致校验 |

**已删除的字段与依据**（相比旧方案 15 个字段）：

| 删除 | 依据 |
| --- | --- |
| `checks` | 判定改由用例 `ctx.expect("键", 值)` 完成。`_apply_checks` 的映射职责消失，测试记录与报告由用例产出 |
| `name` / `description` | 用例自身属性，来自 docstring；写两遍必然漂移 |
| `no` | 数组顺序即顺序，与 `id` 双源 |
| `tags` | 与 pytest marker 双源 |
| `optional` | 与 `exec.on_fail` / `skip_when` 语义重叠 |
| `depends_on` | 线性套里顺序已由数组表达，失败传播由 `on_fail` 表达 |
| `kind` | 由所在段落（setup / cmd_suit / teardown）决定，现网已自动填 |

字段从 15 降到 3 + 4，且**每一个都对应 conftest 里真实读取它的那一行代码**——这是精简的硬标准。

---

## 6. 判定链路：从"清单映射"到"用例自查"

### 旧链路（本文废弃）

```
用例 return {"VDD": 3.31}
   └─► conftest._apply_checks：按 cmd_suit.checks {"VDD": "TC_VDD"} 映射
          └─► ctx.check("TC_VDD", 3.31) ─► 明细进 run_result.json
```

问题：判定归属碎在两边——**清单决定判什么、用例决定测什么**，改一个参数化用例要在两个文件之间来回改。

### 新链路

```
用例内部：v = ctx.command("dmm", "measure_dc_voltage")
          ctx.expect("TC_VDD", v)          # 判定、留痕、超限即抛 LimitError
   └─► conftest 收尾 take_checks() ──────► 明细进 run_result.json
```

### conftest 需要的三处小改动（版本升到 3）

```python
class AteContext:
    def __init__(self, ...):
        ...
        self._pending_checks: list = []          # 新增：本轮用例的判定留痕

    def expect(self, key: str, value, label: str = "") -> float:
        ok, detail = self.check(key, value, label=label)
        self._pending_checks.append(detail)      # 新增：成功也要留痕，报告才拿得到 min/max
        if not ok:
            raise LimitError(..., checks=[detail])
        return detail["value"]

    def take_checks(self) -> list:               # 新增：run_case 收尾取走
        out, self._pending_checks = self._pending_checks, []
        return out
```

```python
# run_case() 内：优先取用例自报的判定明细，退化为旧的映射模式
checks = ctx.take_checks()
if not checks:
    measurements, checks = _apply_checks(ctx, case, returned)
else:
    ctx.record(case, "passed", measurements=measurements_from(returned), checks=checks, duration=duration)
```

要点：
- **保留 `_apply_checks` 作为降级路径**，迁移期老 TPS 仍可跑；但**新基线一律不写 `checks`**。
- `take_checks` 必须在 `record()` 之前调用，顺序错了明细就丢。
- `ATECONFTEST_VERSION` 必须从 `2` 升到 `3`，否则已部署的 workspace 不会更新（`deploy_conftest` 版本一致即保留）。

---

## 7. testconfig：判据预置

testconfig 是**判据值库**，不是判定逻辑。通用预置只放标量：`min` / `max` / `std` 及必要元信息。

| 字段 | 说明 |
| --- | --- |
| `label` / `unit` | 报告展示用，落 `checks[]` 明细 |
| `type` | `range`（上下限）/ `stats`（统计判据）/ `discrete`（枚举） |
| `min` / `max` | `type=range` 至少给其一（现网已强制） |
| `nominal` / `std` / `k` / `n` | `type=stats`：实测均值应落在 `nominal ± k·std`，`n` 为采样次数 |
| `severity` | 可选：`critical` / `warning` / `info`。**warning 只记录不拦**，报告着色 |

**承载有限**这条约束在判据上同样生效：`checks[]` 明细保持标量键值（现网形状 `key/label/value/min/max/unit/ok/message`），统计类只追加 `stat` 与 `samples_n`，不允许嵌套结构——`_apply_checks` 现在已过滤掉 dict/list 值，这是既有约束，不是新加的。

---

## 8. PN 校验（adaptation）

PN 校验发生在**跑之前**，目的是"这份 TPS 能不能测这台 UUT"。

| 校验 | 依据 | 失败处置 |
| --- | --- | --- |
| 批次提取 | `adaptation.batch.extract_regex` 从 UUT 名称/件号提批次 | 提出来的批次不在 `whitelist` → **拒绝启动**（不是跳过） |
| SN 形态 | `adaptation.sn.regex`（现网 12 位英数） | 形态不符 → 拒绝启动 |
| 件号匹配 | `adaptation.part_number` vs 本次登记件号 | 不匹配 → 拒绝启动 |
| 通道数 | `channels.count` vs `slots` 数量 | 不一致 → 校验期报错 |

**产出有明确去处**：批次落到 `test_records.batch`、件号落 `part_no`、台架序列号落 `equipment_serial`。PN 校验不是"跑之前的额外礼貌"，它的结果直接进固定记录条目——这也是它必须在 uut.json 里的原因。

---

## 9. 台架绑定（bench.device_config）

```jsonc
"scope": { "category": "示波器", "device_id": "rh-scope", "mode": "simulate" }
```

| 写什么 | 不写什么 |
| --- | --- |
| 别名（用例里用的名字）、设备类别、注册库 `device_id`、运行模式 | IP、端口、串口、波特率、SCPI 地址——全部由 conftest 从 SQLite 查 |

- 别名与 `device_id` 的匹配逻辑现网已实现（先按 `device_id` 精确命中，再 `ate_db.match_device(bench_id, spec)` 模糊匹配）。
- 用例侧一律 `ctx.driver("scope")` / `ctx.command("dmm", "measure_dc_voltage")`，**永远不拼 IP**。
- 找不到设备时进 `driver_errors`，报告里可见；`on_driver_error=skip` 可让现场降级为 skip 而非 fail。

---

## 10. 执行时序（Runner 视角，端到端）

| # | 步骤 | 载体 | 关键点 |
| --- | --- | --- | --- |
| 1 | 接收运行请求（TPS + UUT + 测试台 + 模式） | 前端 → API | UUT 为单个 SN（并行见 §12 待办） |
| 2 | 读 uut.json，`is_v2` → `validate_v2` | `manifest.py` | 结构校验，含本方案新增项（§12） |
| 3 | **PN 校验** | `adaptation` | 不通过直接拒绝，不进入执行 |
| 4 | 展开通道 | `adaptation.channels` | `count=1` 时退化为单件 |
| 5 | 建运行目录、部署 conftest、写 `ate_env.json` | `workspace.py` | conftest 版本比对后部署 |
| 6 | 渲染 `test_tps_generated.py` | `templates.py` | 三个段落 repr 进文件，`ids=[c["id"]]` |
| 7 | **逐条拉起 pytest 进程** | Runner | 每条用例一个进程，按 `id` 续写 `run_result.json` |
| 8 | 用例执行 + `ctx.expect` 自查判定 | `conftest` + `testcase/` | 明细留痕 → `run_result.json` |
| 9 | 会话收尾释放驱动 | `pytest_sessionfinish` | 含安全收尾（设备断电等） |
| 10 | 汇总 → HTML 报告 → 落库 | Runner | 写入 `test_records` 固定条目 |

> 注意第 7 步：因为每条用例是独立进程，`exec.timeout_s` / `retry` 的天然实现位置是 **Runner 侧**（进程级超时与重启进程），而不是 conftest 内部。这一点决定 §5 的 `exec` 由谁消费。

---

## 11. 报告与 R1/R2 记录

**"报告由测试用例生成、承载有限、固定条目落库"** 这三句话合起来，推出三条设计约束：

1. **uut.json 不参与报告结构定义**。报告条目由用例产出的测量值与判定明细决定；uut.json 里任何面向报告的字段（旧方案的 `checks`、`description`）都该删——它们既改变不了记录条目，又制造了第二份"看起来权威"的描述。
2. **记录条目必须标量**。`test_records` 是 25 个固定列（`task_id / uut / project / dut / tps_id / tps_name / status / passed / failed / skipped / total / duration / report_file / start_time / end_time / created_at / batch / part_no / operator / equipment_serial / result`），**没有一处能塞任意结构**。所以用例产出必须是标量键值；明细全量只在 `run_result.json` 与报告 HTML 里，超出固定条目的部分**不入库**——这就是"承载有限"。
3. **两次记录的分工**：R1 / R2 都是固定条目记录，区别在于粒度（例如任务级 vs 单品/单通道级）。设计上只需保证**同一份 `run_result.json` 能同时投影出这两个粒度的固定条目**，无需为不同级别准备不同的 uut.json 结构。

> ⚠️ 待确认：R1/R2 的具体条目清单与落库表归属。现网只有 `test_records` 一张记录表，第二个级别的落点尚不明确（见 §15）。

---

## 12. 校验清单（`validate_v2` 需要的新增与调整）

| # | 校验 | 类型 | 说明 |
| --- | --- | --- | --- |
| 1 | `name` 由必填降为可选 | 调整 | 用例名来自 docstring。**若保留 `name`，须加"与 docstring 不一致则告警"** |
| 2 | `checks` 引用阈值校验**失效** | ⚠️ 移除副作用 | 移除 `checks` 后，现网唯一一条跨字段校验随之消失 |
| 3 | 用例判据键的静态扫描（补 #2） | 新增 | 扫 `testcase/*.py` 里 `ctx.expect("KEY"` / `ctx.check("KEY"` 字面量，校验 KEY 已在 `testconfig` 定义。**不做这条，错误会退化成运行时 `KeyError`** |
| 4 | `case` 存在性校验 | 新增 | 升级为"在 `testcase/` 下确实能找到该函数"，现在只校验字符串格式 |
| 5 | 清单 ↔ 目录双向一致 | 新增 | 清单引用不存在 → 报错；目录有用例未被引用 → 提示（可能是漏排，也可能是刻意不跑） |
| 6 | `testconfig` 判据类型校验 | 新增 | `type=range` 需 `min` 或 `max`；`type=stats` 需 `std` 且 `k>0`、`n>=2` |
| 7 | `adaptation` 自洽 | 新增 | 批次正则必须能匹配 `part_number`；`channels.count == len(slots)`；`resource_bindings` 无重复占用 |
| 8 | `device_config` 跨表一致 | 新增 | 别名指向的 `category` / `device_id` 必须在该测试台 BOM 内；`fixture` 必须存在 |

---

## 13. 迁移路径

| 阶段 | 内容 | 风险 |
| --- | --- | --- |
| **阶段 0**（可立即做） | uut.json 切新结构：新增 `metadata` / `adaptation` / `bench`；cmd_suit 去 `checks` / `name` / `description` / `no` / `depends_on` / `optional` / `tags`；校验器放宽 `name`、补 #3 #4 #6 | 低（配置层，不动执行） |
| **阶段 1** | conftest v3：`expect` 成功留痕 + `take_checks`；`type=stats` 判据；Runner 侧实现 `exec.timeout_s` / `retry` | 中（升版本号后所有台架会更新 conftest） |
| **阶段 2** | PN 校验接入执行链路，结果落 `batch` / `part_no` / `equipment_serial` | 中（会拦住不合规批次，需先确认白名单） |
| **阶段 3** | 并行：`channels` / `slots` / 资源互斥 / Runner 多通道拉起 | 高（见风险） |
| **阶段 4** | R1/R2 固定条目与报告模板固化；第二级记录落库 | 中（依赖 §15 的确认） |

---

## 14. 风险与取舍

| 风险 / 取舍 | 说明 | 缓解 |
| --- | --- | --- |
| 判定移入用例后，"这条用例判了哪些项"需要读代码 | 参数化调整不再改 uut.json（收益），但可见性下降 | 由校验 #3 的静态扫描顺带生成「用例判据地图」，给前端展示 |
| 删 `checks` 会让旧跨字段校验失效 | 错误从"导入时拦下"退化为"运行时 `KeyError`" | 校验 #3 必须先于 `checks` 的移除上线 |
| 保留降级路径会留下两种判定方式 | 迁移期必要，长期会分叉 | 明确时间窗：阶段 0 后新基线一律不写 `checks`；阶段 2 结束移除降级代码 |
| 并行把偶发串扰放大成批量误判 | 仪器是共享资源，通道间抢同一串口会产出"看起来正常"的错误数据 | 强制 `resource_bindings` + `shared_resources` 声明，校验 #7 拦冲突 |
| 记录承载有限 vs 数据丰富 | 现场往往想留更多原始数据 | 明细留在 `run_result.json` 与报告；如需归档，另建附件目录并在 `report_file` 旁记录路径，不动固定条目 |
| `baseline` 嵌套 vs 顶层平铺 | 嵌套更清晰，平铺改动更小 | 先平铺上线，命名后置调整；`case_entries()` 的取值路径同步改即可 |

---

## 15. 待确认问题

1. **R1 / R2 的固定条目清单**分别是什么？现网只有 `test_records` 一张记录表——第二个级别的落点（同表加 `level` 列？另建表？）需要你给出。
2. **用例判定是否统一走 `ctx.expect`**？若是，`_apply_checks` 的映射模式可以定死退出时间；若某些用例仍需"返回 dict 自动判定"，就要在准则里写清两种模式的适用边界。
3. **是否需要「用例判据地图」**（静态提取 `ctx.expect` 的键 → 前端展示 + 校验）？这决定校验 #3 是做一次扫描还是做成常驻接口。
4. **`params` 里是否允许出现设备别名**？本文的立场是禁止（别名从 `ctx` 取），若现场已有大量 TPS 这么做，需要一份迁移清单。

---

## 附录：与旧方案的差异

| 项 | 旧方案 | 本方案 | 依据 |
| --- | --- | --- | --- |
| uut.json 定位 | 半脚本（承载判定映射与用例描述） | 受控基线清单（编排 + PN + 台架 + 判据值） | §0 / §3 |
| 判定映射 | `cmd_suit.checks` | 用例内 `ctx.expect` | §6 |
| 用例名 / 描述 | uut.json 里写 | 取自 docstring | §5 |
| 字段数（每条用例） | 15 | 3 必需 + 4 可选 | §5 |
| 判据 | `min` / `max` | `min` / `max` / `std`（`range` / `stats`） | §7 |
| 报告 | uut.json 参与定义 | 用例产出 + 固定记录条目 | §11 |
| 连接参数 | 曾在 TPS 里写 IP | 全部走 SQLite 注册库 | §9 |
