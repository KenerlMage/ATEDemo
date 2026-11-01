# ate_rt —— 实时数据发布库

测试用例在运行过程中把测量值、状态量、判据量写入实时数据通道，Runner「测试执行」页的
监视窗口按 1 Hz（最高 2 Hz）刷新查看，并可对数值项绘制折线图。

本库零第三方依赖：写 Redis 走内置 RESP 客户端，**只支持 Redis**，不再保留本地 SQLite 兜底。
安装包仍然可以离线部署，但部署时必须提供一个可连通的 Redis 实例。

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
| `store_info()` | 查看当前存储后端（redis；不可用时返回 `ok=False`） |

约定：

* 任务号默认取环境变量 `ATE_RT_TASK_ID`（Runner 启动用例时注入，等于本轮任务号），
  也可显式传 `task=`；
* 任何存储异常都不会向上抛给用例，只在 stderr 打一行提示 —— 监视通道故障不能影响测试结论；
* 写入频率建议 ≤10 Hz；监视端只按 1–2 Hz 拉取，高频写入的中间点仍会保留在曲线里。

> 用例侧完整接入说明（模板、写入节奏、排障表）见 `docs/实时数据监视/用例接入指南.md`。

## 二、存储位置

| 环境变量 | 说明 |
| --- | --- |
| `ATE_RT_URL` | `redis://<host>:<port>/<db>`，带凭据时写 `redis://user:secret@host:port/db`；未设置时使用 `redis://127.0.0.1:6379/0` |
| `ATE_RT_DISABLE` | `1` 表示完全禁用本库（用于验证“关掉监视不影响测试”） |

`ATE_RT_URL` 只接受 Redis 地址。写成 `sqlite://` 会被直接拒绝并提示改回 Redis，
避免旧配置文件在升级后静默生效。

Redis 不可用时的行为（三处一致，都不影响测试执行）：

| 位置 | 表现 |
| --- | --- |
| 用例侧 | `Monitor` 降级为空操作，`store_backend` 返回 `none`，stderr 提示一次后照常执行 |
| 接口侧 | `GET /api/monitor/info` 返回 `ok=false` 与 `error`，不抛 500 |
| 界面侧 | 监视窗口显示未连接与失败原因，不展示上一轮的陈旧数据 |

### 为什么只保留 Redis

| 取消 SQLite 的理由 | 说明 |
| --- | --- |
| 数据面单一 | 一套键空间、一份 TTL 语义，排障时不必再判断这台机器落在哪个库 |
| 跨进程一致 | 多台架、多 Runner 读同一实例，才能看到同一份实时数据 |
| 责任边界清晰 | 存储不可用属部署问题，由 `/api/monitor/info` 直接暴露，不再用降级掩盖 |
| 交付形态收敛 | 现场组件清单稳定为「Runner + 一个 Redis 实例」，不再有隐藏的本地库文件 |

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
