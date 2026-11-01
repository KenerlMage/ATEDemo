# ate_rt —— 实时数据发布库

测试用例在运行过程中把测量值、状态量、判据量写入实时数据通道，Runner「测试执行」页的
监视窗口按 1 Hz（最高 2 Hz）刷新查看，并可对数值项绘制折线图。

本库零第三方依赖：写 Redis 走内置 RESP 客户端，缺 Redis 时回落本地 SQLite，
两种情况上限都不引入额外安装包（产线工控机可离线部署）。

## 一、在测试用例里使用

```python
import ate_rt

def case(ctx, **params):
    ate_rt.declare("电源电压", unit="V", group="电源", lo=0, hi=30)
    for _ in range(30):
        v = measure_voltage()
        ate_rt.write("电源电压", v)          # 同名字反复写即为时间序列
        time.sleep(1)
    ate_rt.event("纹波测量开始")             # 事件标记，在监视窗口里作为文本项出现
```

需要显式任务号或分组时用会话对象：

```python
with ate_rt.Monitor(task="T2", name="光学扫描", tps="spm_rh_dyn", uut="ABC123DEF456") as m:
    m.declare("光功率", unit="dBm", group="光学")
    m.write("光功率", -3.2)
    m.write_many({"温度": 23.5, "状态": "OK"})
```

| 调用 | 作用 |
| --- | --- |
| `declare(name, label=, unit=, group=, kind=, lo=, hi=)` | 声明数据项（可重复调用，后写覆盖描述） |
| `write(name, value, ts_ms=)` | 写一个数据点；同名字自动声明 |
| `write_many({...})` | 批量写同一时刻的一组数据 |
| `event(text)` | 写事件标记（文本项 `_event`） |
| `close(status="done")` | 结束任务（`status` 取值 `done` / `error`） |
| `store_info()` | 查看当前存储后端（redis / sqlite） |

约定：

* 任务号默认取环境变量 `ATE_RT_TASK_ID`（Runner 启动用例时注入，等于本轮任务号），
  也可显式传 `task=`；
* 任何存储异常都不会向上抛给用例，只在 stderr 打一行提示 —— 监视通道故障不能影响测试结论；
* 写入频率建议 ≤10 Hz；监视端只按 1–2 Hz 拉取，高频写入的中间点仍会保留在曲线里。

## 二、存储位置

| 环境变量 | 说明 |
| --- | --- |
| `ATE_RT_URL` | `redis://[:password@]host:port/db` 或 `sqlite:///<绝对路径>`；未设置时先试 `redis://127.0.0.1:6379/0` |
| `ATE_RT_DB` | SQLite 文件路径（`ATE_RT_URL` 未指定 sqlite 时使用） |
| `ATE_RT_STRICT` | `1` 表示 Redis 不可用时报错，不回落 SQLite（跑产线可开启，避免静默降级） |
| `ATE_RT_DISABLE` | `1` 表示完全禁用本库（用于验证“关掉监视不影响测试”） |

Redis 不可用且未设 `ATE_RT_STRICT` 时自动回落 SQLite（默认 `backend/.ate_rt/realtime.db`），
监视窗口头部会显示「SQLite 回退」。

## 三、Redis 键空间

| 键 | 类型 | 内容 |
| --- | --- | --- |
| `ate:rt:tasks` | ZSET | `task_id` → 开始时间戳（任务索引） |
| `ate:rt:task:<id>` | HASH | `name` / `tps_id` / `tps_name` / `uut` / `mode` / `status` / `started_at` / `updated_at` / `ended_at` |
| `ate:rt:task:<id>:channels` | ZSET | 数据项名 → 声明顺序 |
| `ate:rt:task:<id>:ch:<name>` | HASH | `label` / `unit` / `group` / `kind` / `lo` / `hi` / `value` / `num` / `ts` |
| `ate:rt:task:<id>:ch:<name>:z` | ZSET | `ts_ms` → 数据点（保留最近 `DEFAULT_KEEP`=3600 点） |

键带 1 天过期时间，任务结束后数据仍在，便于复盘；需要长期留档时由报告产物承担。

## 四、读取侧

后端 `backend/realtime_monitor.py` 用同一个库读，接口：

| 接口 | 说明 |
| --- | --- |
| `GET /api/monitor/info` | 存储后端与 SDK 版本 |
| `GET /api/monitor/tasks?limit=&active_only=` | 任务（测试用例）列表 |
| `GET /api/monitor/snapshot?task=<id>` | 该任务全部数据项的最新值（含 `age_s` / `stale` / `numeric`） |
| `GET /api/monitor/series?task=&channel=&limit=` | 单数据项的最近 N 点（折线图） |
| `GET /api/monitor/store` | 键空间说明（运维排查用） |

## 五、离线演示

```bash
python sdk/samples/monitor_demo.py --seconds 60 --rate 10
```

脚本会创建一个 `demo-<时间戳>` 任务并持续写入四路数据，打开 Runner 的监视窗口即可看到曲线。
