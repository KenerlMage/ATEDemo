# TPS 加载至用例执行：DM 参与时序

本图描述一次 TPS 执行从界面下发到用例收尾的完整链路，重点标出装备管理模块（Device Manager，DM）在其中的位置与每一次交互。
配图：`dm-tps-execution-sequence.png`（3200×1800）、`dm-tps-execution-sequence.svg`（矢量）。

## 一、参与者

| 参与者 | 运行形态 | 职责 |
| --- | --- | --- |
| 操作员界面 | 浏览器（执行页） | 选择 TPS、测试台、UUT 与仿真/真机模式，下发执行 |
| Runner 后端 | 主进程 | 读取 TPS 清单、暂存运行目录、拉起 pytest、收尾落库与报告 |
| pytest 子进程 | 独立进程（cwd = 运行目录） | 加载 conftest 与用例，构造 `ctx`，驱动测试步骤 |
| DM 客户端 | 用例进程内的库（`ate_dm.proxy`） | 持有句柄与连接，把设备调用翻译为管道请求 |
| DM 服务 | **独立进程**（`dm_broker`） | 唯一持有设备连接，派发动作、维护句柄与租约、承载共享内存环 |
| 仪器驱动 | 服务进程内 | 真实仪器或仿真实现，对上层屏蔽 SCPI/VISA/pylon 等细节 |

## 二、四个阶段

### 阶段 A · 加载与暂存

1. 界面 `POST` 执行请求，携带 TPS 标识、`bench_id`、UUT 与模式；
2. 后端 `read_tps()` 解析清单，`stage()` 生成运行目录 `run_<task_id>`，复制用例、生成 `conftest.py` 与 `env.json`（含 `task_id`、`bench`、`rt_url`、`conftest_version`）；
3. 以子进程拉起 pytest，工作目录即该运行目录，并注入 `ATE_DM_ENABLE`、`ATE_DM_ENDPOINT`、`ATE_RT_TASK_ID`。

此时 DM 尚未参与；运行目录的存在保证同机多任务互不覆盖。

### 阶段 B · 建立装备连接（DM 参与的起点）

4. 用例首次调用 `ctx.driver(alias)`，conftest 判定 `_dm_enabled()` 为真后走 `open_proxy(alias, row, mode)`；
5. 客户端读取端点文件 `backend/.ate_rt/dm_endpoint.json`，连接命名管道 `\\.\pipe\ate_dm_v1`（多台架为 `..._<bench_id>`）；
6. 服务端回 `hello`，客户端比对 `result.protocol` 与自身协议版本，不一致直接失败；
7. 客户端发 `open`，携带设备类型、端点、参数与仿真/真机模式；
8. 服务端实例化驱动或复用既有连接（同一设备多次 open 返回不同句柄）；
9. 驱动返回 `idn`、状态与动作表；
10. 服务端返回 `handle`，句柄计入上限并附租约（默认 120 s，调用续期）。

**异常路径**：握手失败、协议不匹配、服务未启动、连接超时，conftest 记一条 `[warn] 装备管理模块不可用，回退进程内驱动`，随后按原有方式在用例进程内直连设备。用例结论不受影响，但设备连接将不再由 DM 统一持有——这一条是现场排障时必须先确认的分支。

### 阶段 C · 用例执行

11. 用例通过 `ctx.driver()` / `ctx.command()` 发起动作；
12. 代理把动作转为 `call(handle, action, params)`，默认超时 2000 ms；
13. 服务端派发到驱动实例；
14. 驱动返回结果字典；
15. 服务端回传：标量直接返回；波形、图像等大数组走**共享内存环**，只回 `seq + nbytes + shm 名`；
16. 客户端按需 `AttachedRing.read()` 读取（双读头防撕裂），读毕发 `release` 释放槽位。

**旁路**：用例同时通过 `ate_rt` 把过程数据写入 Redis 供实时监视，该通道不经过 DM，也不参与判定与结果文件。

### 阶段 D · 收尾

17. 会话结束，conftest 对每个别名调用 `close(handle)`；
18. 客户端发 `close` / `disconnect`，句柄归还；
19. 服务端调用驱动 `on_close()`：真机断开连接，仿真无操作；
20. pytest 退出，后端更新任务状态、写结果文件、生成报告；
21. 界面收到结束状态与报告入口。

## 三、关键常量

| 项 | 取值 | 说明 |
| --- | --- | --- |
| 传输 | 命名管道 | `\\.\pipe\ate_dm_v1(_<bench_id>)`，本机进程间 |
| 协议版本 | `PROTOCOL_VERSION` | 握手时强校验，不匹配即失败 |
| 超时 | connect 1000 / open 5000 / call 2000 ms | 客户端侧，超时走异常分支 |
| 句柄上限 | 512（软）/ 1024（硬） | 防泄漏，超限拒绝 open |
| 租约 | 120 s | 客户端崩溃后句柄自动回收 |
| 数据面 | 共享内存环 64 MB / 块 1 MB / 深度 256 | 头 24 B：`ATEDM001` + seq + nbytes + crc32 |
| 在途请求 | 32 / 会话 | 超出排队，队列 64 / 256 |

## 四、代码对应

| 图中环节 | 实现位置 |
| --- | --- |
| 暂存运行目录、注入环境 | `backend/tps_runtime/workspace.py` 的 `stage()` |
| `_dm_enabled()` / `_dm_proxy()` / `driver()` | `backend/tps_runtime/conftest_template.py` |
| 连接、握手、call、stream | `sdk/ate_dm/client.py` |
| 代理与句柄封装 | `sdk/ate_dm/proxy.py`（`DmDriverProxy`） |
| 共享内存环 | `sdk/ate_dm/shm.py`（`AttachedRing` / `StreamBlock`） |
| 服务端与驱动派发 | `backend/dm_broker.py`、`backend/dm_service.py` |
| HTTP 管理面 | `GET/POST /api/dm/{status,devices,sessions,connect,call,batch,log,disconnect,stop}` |

## 五、读图要点

- **DM 只在阶段 B 之后出现**：加载与暂存完全由后端完成，DM 不参与文件与清单处理。
- **一次握手、多次调用**：会话级首连，之后每次设备动作只是一次 `call`，不再重建连接——这是"避免每次重连设备"的落点。
- **两个通道并行**：设备动作走管道，过程数据走 Redis；前者决定结果，后者只影响观感，二者互不阻塞。
- **回退是单向的**：一旦回退到进程内驱动，本会话不再尝试接回 DM，避免半途切换造成句柄状态不一致。
