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

## 六、常驻置顶监视窗（产线形态）

产线需要一块始终浮在最上层的监视窗。网页没有「把自己置顶」的接口，因此给监视页套一个
独立窗口壳，再由 Win32 置顶，实现位于 `backend/monitor_hud.py`。

### 6.1 形态与取舍

| | 方案 | 说明 |
| --- | --- | --- |
| A | 浏览器画中画 | 仅演示用：必须用户手势触发、不能记忆位置，不适合常驻 |
| B | **Edge 独立窗 + Win32 置顶（已实现）** | 零新增依赖，`msedge --app=` 起无地址栏独立窗，`SetWindowPos` 置顶 |
| C | pywebview 桌面壳 | 未来形态：`on_top=True` 即可，需多一个打包依赖，WebView2 已具备 |

### 6.2 关键实现

- **独立 profile**：`--user-data-dir=<base>/.ate_rt/hud_profile`。不隔离的话 Edge 会复用已运行实例，
  既拿不到新窗口、也控制不了位置与生死。
- **句柄定位**：不能按 `Popen` 的 pid 找窗口（Edge 启动器进程立刻退出，真正的浏览器进程是它的子进程）。
  采用「启动前后可见窗口差集 + 标题令牌」：前端带 `?hud=<令牌>` 打开时会改写 `document.title`，
  差集兜住前端未改写的情况。
- **置顶层用 `HWND_TOPMOST`**，不用最顶层（screen-saver 级），避免压住系统弹窗与急停确认框；
  一律带 `SWP_NOACTIVATE`，不抢现场焦点。
- **位置记忆**：`<base>/.ate_rt/monitor_hud.json` 存 `x/y/w/h/topmost`，下次按上次位置打开；
  默认贴主屏右上角 560×380。
- **单实例**：命名互斥 `Global\ATE_MONITOR_HUD`；已存在实例时按标题令牌接管窗口，不重复开窗。
- **热键**：`Ctrl+Alt+T` 切换置顶、`Ctrl+Alt+H` 隐藏/显示。
- **生命周期**：执行开始 `begin_task` 时自动拉起（`ATE_HUD_AUTO=0` 可关），
  执行结束 `end_task` 后按 `ATE_HUD_AUTOCLOSE`（默认 8 s）自动收起；`0` 表示不收起。

### 6.3 接口

| 接口 | 说明 |
| --- | --- |
| `GET /api/monitor/hud/status` | 运行状态、句柄、置顶/可见、当前矩形、环境自检、热键说明 |
| `POST /api/monitor/hud/start` | `{task_id, force, topmost, base_url}` → 拉起或复用；返回 `reused` |
| `POST /api/monitor/hud/stop` | 关闭（先 `WM_CLOSE`，无响应再按进程结束兜底） |
| `POST /api/monitor/hud/topmost` | `{on}` 切换置顶 |
| `POST /api/monitor/hud/visible` | `{on}` 隐藏 / 显示 |
| `POST /api/monitor/hud/rect` | `{x, y, w, h}` 调整位置尺寸（宽高不小于 320×200，并记忆） |

界面入口两处：「测试执行」页步骤面板的「📌 常驻置顶」按钮；监视页头部的「📌 置顶/已置顶」按钮
（未运行时点它即以置顶窗形态重新打开当前任务）。

### 6.4 现场约定

- 窗口默认位置必须避开急停按钮、安全光栅指示与产线看板等安全区，允许拖动但不得长期停放于安全区内；
- 常驻不等于勤刷新：取数仍是 1 Hz（上限 2 Hz），`age_s > 5 s` 的通道继续灰显告警；
- 窗口随执行链路启停，不留在屏上显示上一条 UUT 的旧值；
- 任何置顶窗异常（找不到 Edge、被组策略拦截、句柄捕获失败）都只返回失败信息，不影响测试执行。
