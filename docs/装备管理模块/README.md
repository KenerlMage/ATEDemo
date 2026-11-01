# 装备管理模块（Device Manager）

> 一句话：**所有仪器连接由装备管理模块（DM）持有，测试程序、界面、脚本都通过它与设备通信；大块采样数据走共享内存，控制面只传描述符。** 形态参照 NI 的 MAX / TestStand 与仪器会话的关系，但实现成一个独立进程 + 命名管道，不依赖任何 NI 运行时。

## 一、为什么是独立进程

| 取舍 | 进程内驱动（改造前） | 装备管理模块（现在） |
| --- | --- | --- |
| 谁持有连接 | 每个用例进程各自建连 | DM 独占，引用计数共享 |
| 界面看设备 | 看不到，只能猜 | 实时看到句柄、占用者、调用次数、空闲时长 |
| 用例崩溃 | 连接随进程消失，设备状态不明 | DM 仍在，会话按连接断开回收 |
| 多工位共享稀缺仪器 | 做不到 | 同一测试台内按别名复用，跨进程共享 |
| 大数组传输 | 走函数返回，挤占执行栈与日志 | 共享内存环，控制面只走描述符 |

DM 崩了只影响自己，Runner 不受牵连；Runner 重启后按端点文件重新接管，不用重建连接。

## 二、组成

```
backend/dm_broker.py      DM 服务进程：监听管道、持有驱动、共享内存环、请求日志
backend/dm_service.py     Runner 侧服务层：进程拉起/停止/健康检查 + /api/dm/* 路由
sdk/ate_dm/               客户端：协议、传输、数据面读取、驱动代理
  protocol.py             帧格式与错误码
  shm.py                  共享内存块头布局、dtype 映射、读取校验
  client.py               DMClient（connect/call/batch/stream/stats/log/shutdown）
  proxy.py                DmDriverProxy：用例侧写法不变
frontend/src/components/DeviceLinksPanel.vue   连接管理界面
```

## 三、控制面协议

传输用标准库 `multiprocessing.connection`：Windows 下是命名管道 `\\.\pipe\ate_dm_v1[_<bench>]`，其他平台自动退成本地 TCP（地址写进端点文件）。两端都是标准库，产线离线环境不需要装任何东西。

```
请求  {"id": 1, "op": "call", "args": {"handle": 3, "action": "measure", "params": {...}}}
响应  {"id": 1, "ok": true, "result": {...}}
错误  {"id": 1, "ok": false, "error": {"code": "no_handle", "message": "句柄无效或已释放: 9"}}
```

错误码：`bad_request` / `unknown_op` / `no_device` / `no_handle` / `driver_error` / `stream_error` / `stale_block` / `payload_too_large` / `busy` / `internal`。客户端按码分流，不解析文本。

| 操作 | 用途 |
| --- | --- |
| `ping` / `caps` | 握手、能力清单（槽位数、单块上限、支持的 dtype） |
| `connect` / `disconnect` | 建立/释放设备会话，返回句柄 |
| `call` / `batch` | 执行驱动动作（一次或一批） |
| `stream` / `release` | 取大数组（写入共享内存并回描述符）/ 归还槽位 |
| `devices` / `sessions` / `stats` / `log` | 观测：设备列表、调用方、统计、请求日志 |
| `shutdown` | 优雅退出（清理共享内存与端点文件） |

## 四、数据面

| 项 | 值 |
| --- | --- |
| 实现 | `multiprocessing.shared_memory`，DM 创建，客户端只读 attach |
| 槽位 | 默认 8 × 1 MB（`--slots/--block` 可调），单块上限 = 块大小 − 24 B 头 |
| 块头 | `magic(8s) + seq(u64) + nbytes(u32) + crc32(u32)`，共 24 字节 |
| dtype | `f` float32 / `d` float64 / `i` int32 / `l` int64 / `h` int16 / `B` uint8（整型按取值域夹取） |
| 覆盖保护 | 读取前后各取一次块头，`seq` 与 `nbytes` 变化即判 `stale_block`，不让上层拿到撕裂数据 |
| 校验 | crc32 不符判 `stream_error` |

实测：示波器 9.5 万点 float32（380 KB）一次下发 **61 ms**（含驱动仿真取点与写环），控制面往返只有描述符。

## 五、会话与句柄

- 句柄是整数，DM 侧映射到设备槽位；**同一别名多次 connect 走引用计数**，不会重复打开仪器；
- 客户端断开（进程退出、通道断开）时，DM 自动回收它持有的全部句柄，引用降到 0 才关驱动；
- 驱动实例按设备串行化（每设备一把锁），多客户端并发调用不会互相踩串口/SCPI 会话；
- 未在运行时的任何操作都返回失败码，不抛异常给执行链路。

## 六、界面：装备属性配置 → 连接管理

`装备属性配置` 页新增「连接管理」分栏（`DeviceLinksPanel.vue`），三列：

1. **可连接设备**：来自 SQLite 设备注册表（按测试台），可切测试台、过滤、选仿真/真机，一键建立连接；
2. **已建立的连接**：DM 持有的设备（句柄、驱动、资源地址、IDN、引用数、调用次数、失败次数、空闲时长），可识别、可断开；下方是**调用方**清单（谁连着 DM、多少个句柄、多少次调用）；
3. **模块状态**：管道地址、通道类型、协议版本、共享内存段与槽位占用、已下发数据量、请求/失败数、最近请求日志。

页面只是操作界面：关掉它不影响连接，连接随 DM 生命周期存在。

## 七、测试程序怎么接

### 7.1 推荐：沿用原写法，底层走 DM

在运行环境里设置 `ATE_DM_ENABLE=1`，`conftest` 会把 `ctx.driver(alias)` 换成 DM 驱动代理：

```python
# 用例代码不变
psu = ctx.driver("psu")
psu.do("set_voltage", voltage=3.3)

# 需要大数组时，代理多一个 stream 方法
wave = psu.stream("waveform", dtype="f", points=95000)   # 走共享内存
```

实测（`TC001`，仿真）：日志出现「设备经装备管理模块接入 psu/dmm」，用例结论不变，DM 侧句柄在收尾后归零。

DM 不可用时会自动回退进程内驱动并记一条 `[warn]`，保证执行不被基础设施问题打断——是否允许回退由现场决定，若要求"必须经 DM"，把 `on_driver_error` 配成 `skip` 或另加闸门。

### 7.2 直接调用

```python
from ate_dm import DMClient

with DMClient() as dm:
    scope = dm.connect(device_row, alias="scope", mode="simulate")
    dm.call(scope, "autoset")
    wave = dm.stream(scope, "waveform", dtype="f", points=95000)
    print(wave.count, wave.values()[:4])
```

## 八、环境变量

| 变量 | 作用 |
| --- | --- |
| `ATE_DM_ENDPOINT` | 端点文件路径（优先） |
| `ATE_DM_PIPE` | 直接指定管道名 / TCP 地址 |
| `ATE_DM_ENABLE` | `1` 时用例改用 DM 驱动代理 |
| `ATE_DM_AUTOSTART` | `0` 关闭 Runner 启动时的自动拉起（CI 用） |
| `ATE_DM_BENCH` | 默认测试台标识（管道名后缀） |

## 九、验证

| 项 | 结果 |
| --- | --- |
| 联调脚本（控制面/数据面/并发/容错/收尾） | **34/34** |
| HTTP 接口面（Runner 侧） | **20/20** |
| 端到端跑真实用例（`TC001` 经 DM） | **5/5** |
| 仓库回归 `backend/test_cases/test_dm_client.py` | **13/13**（含 broker 生命周期，0.7 s） |
| 前端构建 | 62 modules / 347.15 kB |

## 十、与设计文档的对应

`docs/驱动软件设计/device-manager-design.md` 里规划的能力与本次实现的关系：

| 设计项 | 现状 |
| --- | --- |
| broker 进程、命名管道、请求-响应 | 已实现 |
| 会话句柄、引用计数、断开回收 | 已实现（租约 TTL 未做，改为按连接回收） |
| 数据面共享内存环 | 已实现（覆盖保护 + crc32） |
| 批量调用、请求日志、统计 | 已实现 |
| 驱动层不感知 DM | 已实现（代理只做转发） |
| 多版本驱动锁定、熔断、跨工位调度 | 未实现（设计保留） |
| WebSocket 推送 / 事件订阅 | 未实现（界面用 2 s 轮询） |

## 十一、待定

- 是否强制"必须经 DM"：目前允许回退进程内驱动，产线若要强约束需加闸门；
- 稀缺仪器跨工位共享：需要排队与优先级策略，当前按别名共享、无跨 bench 调度；
- 数据面保留策略：槽位被覆盖即失效，长耗时消费者需要更大的环或落盘；
- 事件订阅（设备异常、超时告警）目前靠轮询统计。
