# 驱动包功能点与三视图

版本 v1.0 · 与代码同步

## 1 范围与口径

- 运行期驱动包 = `app/drivers`，8 类实现；SDK（`sdk/ate_drivers`）不进入安装包。
- 驱动集合版本 = Runner 版本，无独立驱动版本号。
- 功能点以 `ACTIONS` 键为准，8 类合计 64 项。
- 部署单位为整包 `app/ + web/ + runtime/`。
- 保留项：`logs`、`workspace`、`testresource`、`ate.db`、`license.dat`。

## 2 功能点清单

| 类型键 | 实现类 | 家族 | 动作数 | 动作键 |
|---|---|---|---|---|
| tektronix-mso5 | ScopeDriver | scope | 11 | idn, reset, autoset, timebase, channel, waveform, measure, run, stop, single, state |
| generic-dmm | DmmDriver | dmm | 9 | idn, reset, measure, measure_dc_voltage, measure_dc_current, measure_resistance, measure_frequency, measure_ac_voltage, state |
| generic-psu | PsuDriver | psu | 10 | idn, reset, set_voltage, set_current, output, output_on, output_off, measure_voltage, measure_current, state |
| generic-eload | ELoadDriver | eload | 10 | idn, reset, set_current, set_mode, input, input_on, input_off, measure_voltage, measure_current, state |
| generic-awg | AwgDriver | awg | 9 | idn, reset, set_waveform, set_frequency, set_amplitude, output, output_on, output_off, state |
| serial-motion | MotionDriver | motion | 7 | idn, handshake, set_speed, read_speed, home, stop, state |
| passive-device | PassiveDriver | passive | 3 | idn, state, describe |
| generic-scpi | GenericScpiDriver | generic | 5 | idn, reset, query, write, state |

功能族覆盖：识别 8/8、状态 8/8、复位 6/8、测量 4/8、波形 1/8、采集控制 1/8、激励 3/8、输出开关 3/8、运动 1/8、透传 1/8、描述 1/8。

## 3 逻辑视图

五层，上层仅依赖相邻下层：

1. L1 用例层：TPS 步骤、测试工具页
2. L2 门面层：AteContext、`do()` / `supports()`
3. L3 派发层：`resolve_spec` / `resolve_driver_class`
4. L4 驱动层：家族基类 + 8 类驱动 + `ACTIONS`
5. L5 传输层：simulate / socket / serial / visa

调用链：别名 + 动作名 → 补默认参数 → 按 `device_type` 取实例 → 动作名转能力键 → 查 `ACTIONS` 调方法 → 组装统一结果。

实例状态机：created → opened → busy → closed，异常态 error。开启失败不得继续下发命令，关闭必须幂等。

## 4 开发视图

| 位置 | 内容 | 进包 |
|---|---|---|
| `backend/drivers/` | factory、base、models、各设备实现 | 是 |
| `backend/tps_runtime/` | 用例执行与结果收集 | 是 |
| `sdk/ate_drivers/` | base、capabilities、transport、errors、family、kit、vendors、demos | 否 |
| `packaging/` | 打包脚本、驱动门禁、安装器、启动器 | 否 |

厂商包四件套：`driver.json`（清单，`ate.driver.manifest.v1`）、`driver.py`（实现含 `ACTIONS` 与命令表）、`__init__.py`（导出）、仿真应答（确定性）。

构建六步：前端构建 → 后端装载 → 驱动门禁（第 2.5 步，失败终止）→ 运行时装填 → 安装器封装 → 出包校验。

## 5 部署视图

```
<RUNNER_HOME>/
├─ app/                 运行期代码（整包替换）
│  ├─ drivers/          8 类驱动
│  ├─ tps_runtime/
│  ├─ testresource/     种子数据
│  └─ ate.db            运行期生成
├─ web/                 前端静态资源
├─ runtime/             嵌入 Python 与依赖
├─ logs/                保留
├─ workspace/           保留
└─ updates/             installed_version.json
```

升级四段：空闲校验 → 数据指纹 → 备份与挂起 → 替换与确认。失败整包回滚。

已知偏差：`ate.db` 与可写 `testresource` 位于 `app/` 内，指纹解析已加 `app/` 回退；目标态迁至 `data/`。

## 6 追踪矩阵

| 功能点 | 逻辑单元 | 开发位置 | 部署位置 |
|---|---|---|---|
| 动作解析 | L3 | `backend/drivers/factory.py` | `app/drivers/factory.py` |
| 统一结果结构 | L4 | `backend/drivers/base.py` | `app/drivers/base.py` |
| 设备参数模型 | L4 | `backend/drivers/models.py` | `app/drivers/models.py` |
| 能力契约 | 契约 | `sdk/ate_drivers/capabilities.py` | 不部署 |
| 契约门禁 | 质量门 | `sdk/ate_drivers/kit/conformance.py` | 不部署 |
| 打包门禁 | 质量门 | `packaging/check_drivers.py` | 不部署 |
| 升级与回滚 | 运维 | `backend/updater.py` | `app/updater.py` |
| 保留清单 | 运维 | `backend/runner_info.py` | `app/runner_info.py` |
