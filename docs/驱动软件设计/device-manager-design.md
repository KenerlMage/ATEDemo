# Runner 设备管理器（DeviceManager）设计与接入方案

> 适用对象：ATE Runner 平台组、TPS 开发组、驱动包维护者。
> 关联现状：`backend/drivers/`（驱动层）、`backend/ate_db.py`（`device_registry` 投影）、
> `conftest_template.py`（TPS 运行时取驱动的地方）、`sdk/ate_drivers/`（驱动 SDK 与一致性套件）。
> 版本：v1 草案 ｜ 更新：2026-10-27

---

## 1 目的与范围

**目的**：在 ATE Runner 中新增独立进程 DeviceManager（以下简称 DM），由 DM 统一持有全部设备会话与句柄；
所有 TPS 通过进程间通信操作设备，不再各自建连；驱动以「通用类别 + 可安装包」的形式支持注册、加载、卸载与热更新。

**范围**

| 在范围内 | 不在范围内 |
| --- | --- |
| 设备句柄的集中持有、复用与回收 | 测试流程编排、用例调度（仍由 Runner 与 pytest 负责） |
| 通用类别（family）契约与驱动包注册表 | 测试判定逻辑与阈值（仍属 TPS） |
| 驱动包的安装、校验、加载、卸载、热更新 | 驱动内部算法实现 |
| 设备调用的协议、错误码、审计与指标 | 测试台注册界面（沿用现有注册流程，只增量落库） |
| 仿真与真机的统一入口（沿用现有 transport 分叉） | 跨主机集群调度 |

**非目标说明**：DM 不改变「一台设备同一时刻由谁使用」的业务规则，只负责把这条规则**可执行**（租约与排队），
规则本身仍由配方与工位配置决定。

---

## 2 现状与问题

现有链路（事实，据代码）：`conftest_template.py` 在**每个测试进程**内读取 `device_registry`，
调用 `drivers.get_driver(row, mode=..., alias=...)` 自行实例化驱动并建链，会话结束时调用 `drivers.release_all()`。

由此产生五类问题：

| 编号 | 问题 | 具体表现 | 影响 |
| --- | --- | --- | --- |
| P1 | 重复建连 | 每个用例进程重新握手、重设参数、重做校准检查 | 单件节拍被建连时间吃掉，串口/继电器类设备尤甚 |
| P2 | 资源争用 | 同一台电源被两个工位同时改写电压 | 测量结果不可信，且难以复现 |
| P3 | 句柄失控 | 进程异常退出后设备停留在未关闭状态 | 下一次打开失败，需人工断电或重启 |
| P4 | 版本漂移 | 驱动以文件形式散落在各测试目录 | 同一产线不同工位跑的驱动版本不一致 |
| P5 | 无审计 | 谁在何时改了哪台设备的哪个参数无从查证 | 异常分析只能靠猜，责任边界模糊 |

---

## 3 总体设计

### 3.1 进程与边界

```
┌─────────────────────────────┐
│ Runner 主进程（UI / 调度）    │
└──────────┬──────────────────┘
           │ 启动 / 守护（单实例 mutex）
┌──────────▼──────────────────────────────────────────┐
│ DeviceManager 进程（唯一持有设备会话）                │
│  TransportServer · SessionMgr · HandleTable          │
│  DriverRegistry · Loader · ConformanceGate · Audit   │
└──────────┬──────────────────────────────────────────┘
           │ 进程内调用 or 子进程 RPC（高风险驱动）
┌──────────▼───────────┐   ┌──────────────────────────┐
│ DriverHost（可选沙箱）│   │ 驱动包（wheel / 目录）     │
└──────────┬───────────┘   └──────────────────────────┘
           │ 总线（LAN / 串口 / USB / PXI）
┌──────────▼───────────┐
│ 仪器与被测对象         │
└──────────────────────┘

TPS 测试进程 ──(命名管道 RPC：open / call / close)──▶ DeviceManager
```

三条设计约束：

1. **会话只在 DM 内建立**。测试进程只持有代理对象与租约，不持有真实连接。
2. **驱动包只在 DM 内导入**。避免同名包在测试进程里互相覆盖。
3. **设备状态可外部观测**。任何时刻可查询句柄表，而不必进入测试进程。

### 3.2 逻辑组件

| 组件 | 职责 | 关键行为 |
| --- | --- | --- |
| TransportServer | 接入测试进程与 Runner | 命名管道 `\\.\pipe\ate_dm_v1`（本机默认），可选本机回环 TCP |
| SessionMgr | 连接认证、租约发放与回收 | 令牌校验、租约 TTL、超时回收、断线清理 |
| HandleTable | 设备句柄表 | 键为 `(bench_id, device_id)`，别名与键双向索引 |
| DriverRegistry | 驱动包与类别的注册表 | 版本并存、状态机、来源与校验值留痕 |
| Loader | 导入驱动包、创建实例 | 命名空间隔离、惰性加载、依赖检查 |
| ConformanceGate | 准入检查 | 复用 `sdk/ate_drivers/kit/conformance` 的用例集 |
| AuditSink | 审计与指标 | 调用留痕、延迟直方图、错误分类计数 |

### 3.3 三个通道

| 通道 | 承载 | 说明 |
| --- | --- | --- |
| 控制通道 | open / call / close / status | 小消息，JSON-RPC，单连接串行 + 请求多路复用 |
| 数据通道 | 波形、图像、长数组 | 超过 64 KB 的返回值写入共享内存环，RPC 只回 `{shm, offset, len, dtype}` |
| 事件通道 | 设备状态变化、故障、驱动重载 | 订阅制，避免测试进程轮询 |

---

## 4 通用类 driver 模型

### 4.1 三层概念

| 层 | 含义 | 现有对应物 | 谁维护 |
| --- | --- | --- | --- |
| 类别 Class | 一组设备的公共契约：必需动作、返回结构、错误语义 | `Driver.ACTIONS`、`family`（dmm / psu / scope / camera / …） | 平台组 |
| 包 Package | 一个可安装单元：manifest + Python 包 + 依赖声明 | 目前是 `backend/drivers/*.py` 内嵌实现 | 驱动开发者 |
| 实例 Instance | 绑定到一台具体设备的运行对象 | `Driver` 子类实例（`get_driver` 返回） | DM 创建与持有 |

**类别是契约，包是实现，实例是运行态。** 注册针对包，加载针对实例，卸载两者皆可。

### 4.2 类别契约

沿用现有驱动层的约定，并补充三条硬要求：

1. **动作集**：每个 family 声明 `required_actions`（如 `dmm`：`idn` / `reset` / `configure` / `measure`；`psu`：`idn` / `reset` / `set_voltage` / `set_current` / `output` / `measure`）。包的 `ACTIONS` 必须覆盖必需集合，缺失即注册失败。
2. **返回结构**：统一 `{"ok": true, "action": ..., "value": ..., "detail": {...}}`；异常必须映射为错误码，不得抛裸异常穿透 RPC。
3. **生命周期**：构造函数**不得访问硬件**（只解析配置）；硬件访问仅发生在 `open()`；`close()` 必须幂等，重复调用不报错。

补充能力查询（避免类别接口拉平仪器特性）：

```
capabilities() -> {
  "channels": 2, "ranges": [[0, 10], [0, 100]], "resolution_bits": 16,
  "max_rate": 1000, "optional": ["external_trigger", "4wire"],
  "limits": {"notes": "量程切换需 30 ms 稳定"}
}
```

TPS 侧在编排前调用 `dm.capabilities(alias)`，据此选择档位或跳过工步，而不是假设所有同类设备一致。

### 4.3 驱动包 manifest（`driver.json`）

```json
{
  "id": "ate.driver.psu.generic",
  "key": "generic-psu",
  "family": "psu",
  "label": "可编程直流电源（通用 SCPI）",
  "version": "1.4.2",
  "api_level": 1,
  "entry": "ate_driver_psu.driver:PsuDriver",
  "requires": {"python": ">=3.10", "packages": [], "system": ["VISA runtime>=6"]},
  "resources": {"bus": ["LAN", "SERIAL"], "pattern": ["TCPIP*", "ASRL*"]},
  "capabilities_decl": ["idn", "reset", "set_voltage", "set_current", "output", "measure"],
  "isolation": "inproc",
  "checksum": "sha256:9f2c…",
  "signature": "ed25519:…"
}
```

字段约定：`isolation` 取 `inproc`（装入 DM 进程）或 `subproc`（装入 DriverHost 子进程）；
`resources.pattern` 用于注册时的**资源冲突检测**——两个包声明同一总线前缀并同时启用时给出警告。

### 4.4 与 `device_registry` 的关系

两张表分工明确，不合并：

| 表 | 回答的问题 | 关键列 |
| --- | --- | --- |
| `device_registry`（现存） | 这台设备**是什么**、接在哪、用哪个 key | `bench_id`、`device_id`、`device_type`/`driver`、`interface`、`host`、`port` … |
| `driver_package`（新增） | 这个 key 有哪些**已注册的包与版本** | `key`、`version`、`state`、`path`、`checksum`、`registered_at` |

运行时解析链：`device_registry.device_type` → `driver_package` 中该 key 的**锁定版本**（由环境锁文件或配方指定）→ Loader 导入 → 实例化。

> 若 `device_registry` 未指定版本，取该 key 的 `active` 版本；多版本并存时必须显式锁定，否则拒绝启动（避免隐式漂移）。

---

## 5 注册（Register）

### 5.1 流程

```
扫描来源 → 校验完整性与签名 → 解析 manifest → 类别契约检查
        → 导入试加载（不触硬件） → 一致性用例（可选） → 资源冲突检测
        → 写 driver_package（state=registered） → 发事件
```

**来源**：① 随 Runner 内置的 `drivers_builtin/`；② 货架下载（沿用 `docs/驱动软件设计/driver-distribution.md` 的分发口径）；
③ 本地目录导入（仅开发环境，需显式 `--allow-unsigned`）。

### 5.2 校验项与失败处理

| 校验 | 通过条件 | 失败处理 |
| --- | --- | --- |
| 完整性 | 文件哈希与 manifest `checksum` 一致 | 拒绝入库，返回 `DM-2001` |
| 签名 | 发布者签名可验证（生产环境强制） | 拒绝入库，返回 `DM-2002` |
| api_level | 与 DM 支持的 `api_level` 兼容 | 拒绝入库，返回 `DM-2003` |
| 契约 | `ACTIONS` 覆盖 `required_actions`，返回结构符合约定 | 拒绝入库，返回 `DM-2004` |
| 试加载 | `entry` 可导入，构造不抛异常、不触硬件 | 拒绝入库，返回 `DM-2005` |
| 资源冲突 | 与已启用包的总线模式不重叠，或已确认并存 | 入库但标 `warning`，不阻断 |
| 依赖 | `requires` 满足（Python 版本、系统运行时） | 拒绝入库，返回 `DM-2006` |

### 5.3 版本并存与锁定

- 同一 `key` 允许多版本共存，各自独立目录：`drivers_store/<key>/<version>/`。
- `driver_package.state`：`registered` / `active` / `disabled` / `quarantined`。
- **锁定优先于最新**：环境锁（`environment.lock`）或配方中的 `driver_version` 决定实例使用哪个版本；
  两者都未指定时取 `active`，若同时存在多个 `active` 则拒绝加载（`DM-3001`），强制显式化。

### 5.4 隔离与隔离级别

| 级别 | 实现 | 适用 |
| --- | --- | --- |
| inproc | 独立 `sys.path` 命名空间 + 包级导入器 | 常规 SCPI、相机等成熟驱动，调用开销最低 |
| subproc | DriverHost 子进程，DM 与之 RPC | 第三方 SDK、可能崩溃或泄漏内存的驱动 |

---

## 6 加载（Load）

### 6.1 触发时机

| 模式 | 说明 | 使用场景 |
| --- | --- | --- |
| 预加载 | DM 启动时按 `bench` 配置把 `eager` 包与已登记设备全部打开 | 产线常态：避免首个用例承担建连时间 |
| 惰性 | 首次 `dm.open(alias)` 时才导入与建链 | 开发与调试、设备多的测试台 |
| 预热 | 空闲时对已知设备做 `idn` 探测，保持会话温热 | 串口类设备，减少冷启动抖动 |

### 6.2 句柄表结构

| 字段 | 说明 |
| --- | --- |
| `handle` | 单调递增整数，跨进程有效但不可猜测（含会话位） |
| `key` | `(bench_id, device_id)` |
| `alias` | TPS 使用的逻辑别名，来自 `device_registry.role` 或注册时的别名 |
| `driver_key` / `version` | 解析后的包与版本 |
| `mode` | `real` / `simulate`（沿用现有 transport 分叉，仿真不分支驱动代码） |
| `session` | 真实连接对象（仅 DM 内可见） |
| `state` | `opening` / `ready` / `busy` / `faulted` / `closing` |
| `refcount` | 当前持有租约的测试进程数 |
| `owner` | 最近一次操作的 `tps_id` 与工位号 |
| `stats` | 调用次数、失败次数、P50/P95 延迟、最近错误 |
| `opened_at` / `last_used_at` | 用于 TTL 回收与健康判定 |

### 6.3 打开语义：single-flight 与锁策略

- 同一 `key` 的并发 `open` 合并为一次真实建链（single-flight），其余请求等待结果。
- 打开失败时按 `attempts` 重试（默认 2 次，退避 200 ms / 500 ms），仍失败则进入 `faulted`。
- **访问控制**：`read` 类动作允许并发；`write` 类动作在句柄级互斥（队列 FIFO，超时可取消）。
  分类写在类别契约里（`ACTIONS` 中每个动作标注 `rw: read|write`），避免运行时猜测。

### 6.4 租约

```
dm.open(alias, ttl=120) -> {handle, lease_id, expires_at}
dm.call(handle, "measure", ...) -> {value, ...}
dm.close(handle)  // 或租约到期自动回收
```

- 租约到期且无在途调用 → DM 关闭句柄并释放资源（TTL 默认 120 s，可按设备类调整）。
- 测试进程异常退出 → 管道断开即视为释放，DM 主动回收（解决 P3）。

### 6.5 状态机

```
registered ──load──▶ loading ──ok──▶ loaded ──open──▶ opening ──ok──▶ ready
                                     │                 │fail
                                     │                 ▼
                                     │              faulted ──retry/cooldown──▶ opening
                                     ▼
                                 unloading ──▶ unloaded（元数据保留）
```

任何状态下收到 `unregister`：先转入 `quarantined`（拒绝新租约），等待 `refcount=0` 后卸载。

### 6.6 熔断与冷却

连续 `N=3` 次打开失败 → `faulted` 并进入冷却（默认 30 s，指数退避至 5 min）；
冷却期间调用直接返回 `DM-4004`（设备不可用，冷却中），避免单台坏设备拖垮整线节拍。

---

## 7 卸载（Unload）

### 7.1 正常卸载

1. 注册表状态置 `disabled` / `quarantined`，**拒绝新租约**；
2. 等待在途调用完成（`grace_ms`，默认 3000，可配）；
3. `close()` 所有相关句柄，释放会话与共享内存；
4. 从 Loader 的命名空间移除模块（inproc）或终止 DriverHost（subproc）；
5. 更新 `driver_package.state=unloaded`，保留元数据与审计。

### 7.2 强制卸载

`grace_ms` 超时仍未收敛 → 终止 DriverHost 子进程；inproc 驱动无法安全强杀时，
将其所有句柄置 `faulted` 并通知持有者（返回 `DM-3003`），DM 主循环不受影响。
**原则：宁可让一个驱动失效，不可让 DM 崩溃。**

### 7.3 热更新（side-by-side）

```
register(v_new) → load(v_new) → 健康检查通过
      → 别名切换（alias 指向 v_new）→ 旧版本等待 refcount=0 → unload(v_old)
      → 失败则回切别名到 v_old（v_new 标记 quarantined）
```

切换窗口内新旧并存，故要求：**同类别的两个版本不得共享同一资源锁**，句柄各自独立。

### 7.4 禁止项

| 场景 | 处理 |
| --- | --- |
| 有活跃租约时请求卸载 | 拒绝，返回 `DM-3004`（附持有者清单） |
| 测试执行中请求热更新 | 拒绝（Runner 在执行态时对 DM 下发 `freeze`），返回 `DM-3005` |
| 卸载内置包 | 拒绝（`DM-3006`），内置包仅可禁用 |

---

## 8 通信协议

### 8.1 传输与帧格式

- 默认：Windows 命名管道 `\\.\pipe\ate_dm_v1`；备选：`127.0.0.1:<port>`（便于跨容器调试）。
- 帧：`4 字节长度（小端） + JSON-RPC 2.0 报文`，UTF-8。
- 单连接在途请求上限 32，超过则背压（`DM-1003`）。

### 8.2 方法表

| 方法 | 用途 | 主要参数 | 返回 |
| --- | --- | --- | --- |
| `dm.ping` | 存活与版本 | — | `{version, api_level, uptime}` |
| `dm.list_devices` | 句柄表快照 | `bench_id?` | 句柄数组（不含 session） |
| `dm.list_drivers` | 注册表快照 | `family?`、`key?` | 包与状态数组 |
| `dm.register` | 注册包 | `path`、`allow_unsigned?` | `{key, version, state, warnings[]}` |
| `dm.unregister` | 卸载包 | `key`、`version`、`force?` | `{state}` 或错误 |
| `dm.activate` | 设为 active / 锁定 | `key`、`version` | `{state}` |
| `dm.open` | 打开设备 | `alias` 或 `(bench_id, device_id)`、`mode`、`ttl` | `{handle, lease_id, expires_at, capabilities}` |
| `dm.capabilities` | 能力查询 | `handle` | 能力字典 |
| `dm.call` | 执行动作 | `handle`、`action`、`params`、`timeout_ms`、`request_id` | `{ok, value, detail}` |
| `dm.close` | 释放句柄 | `handle` | `{refcount}` |
| `dm.status` | 指标 | `handle?` | 统计与健康 |
| `dm.subscribe` | 事件订阅 | `topics` | 事件流 |

### 8.3 调用示例

```json
{"jsonrpc":"2.0","id":17,"method":"dm.call","params":{
  "handle": 4097, "action": "set_voltage", "params": {"channel": 1, "volt": 3.3},
  "timeout_ms": 800, "request_id": "tps7-0012-0003"}}
```

```json
{"jsonrpc":"2.0","id":17,"result":{"ok":true,"value":3.3,
  "detail":{"driver":"generic-psu@1.4.2","elapsed_ms":6.1,"raw":"VOLT 3.3"}}}
```

```json
{"jsonrpc":"2.0","id":17,"error":{"code":4002,"message":"设备占用冲突",
  "data":{"key":["bench-3","psu-1"],"holder":"tps12","hint":"等待租约或改用其它通道"}}}
```

### 8.4 错误码

| 段 | 范围 | 含义 |
| --- | --- | --- |
| 协议 | DM-1001 ~ 1005 | 帧错误、版本不匹配、背压、认证失败、未知方法 |
| 注册 | DM-2001 ~ 2006 | 哈希、签名、api_level、契约、试加载、依赖 |
| 加载/卸载 | DM-3001 ~ 3006 | 版本不唯一、导入失败、强杀、有租约、执行中拒绝、内置包 |
| 设备 | DM-4001 ~ 4006 | 无此设备、占用冲突、总线超时、冷却中、设备报错、参数非法 |
| 权限 | DM-5001 ~ 5003 | 未授权操作、只读驱动、工位不匹配 |

### 8.5 超时与幂等

- 三级超时：`connect_ms`(1000) / `open_ms`(5000，可被包 manifest 覆盖) / `call_ms`(默认 2000)。
- `request_id` 幂等：同 id 重复到达时返回上次结果（缓存 60 s），用于管道重连后的安全重试。
- 取消：`dm.cancel(request_id)`，驱动需在动作边界检查取消标志；不可中断的动作（如长积分）按超时处理。

### 8.6 数据面

返回值 > 64 KB（波形、图像、长数组）时：DM 写入共享内存环（默认 64 MB，按 1 MB 分块），
RPC 返回 `{shm:"ate_dm_shm_1", offset, len, dtype, shape}`；测试进程读取后调用 `dm.release(offset)`。
环满时回退到临时文件句柄（`DM-1005` 仅当两者皆失败）。

---

## 9 TPS 侧接入

### 9.1 SDK 形态：代理对象

`sdk/ate_dm/client.py` 提供与现有 `Driver` 同形的代理：

```python
from ate_dm import DeviceManagerClient
dm = DeviceManagerClient(pipe=r"\\.\pipe\ate_dm_v1", token=...)

dev = dm.get_driver(row, mode="real", alias="psu_main")   # 与 drivers.get_driver 同签名
dev.do("set_voltage", channel=1, volt=3.3)                # 与现有调用一致
dev.idn          # 代理属性，来自 open 时缓存
dev.close()
```

代理对象对外暴露 `do()`、`ACTIONS`、`idn`、`device_id`、`alias`、`mode`、`opened`、`capabilities()`，
**TPS 代码的调用形态不变**；差异只在错误类型（`RemoteDriverError`，带 `code` 与 `holder`）。

### 9.2 conftest 改动点

`conftest_template.py` 现有两处（约 325 行 `get_driver`、363 行 `release_all`）改为按开关分派：

```python
USE_DM = os.environ.get("ATE_DEVICE_MANAGER", "1") == "1"
if USE_DM:
    from ate_dm import DeviceManagerClient
    drivers = DeviceManagerClient()
    driver = drivers.get_driver(row, mode=mode, alias=alias)   # 失败即 fail fast
else:
    from drivers import get_driver                              # 保留直连模式
    driver = get_driver(row, mode=mode, alias=alias)
```

`release_all()` 对应 `dm.close_all()`：只释放本进程租约，**不关闭其它工位仍在使用的设备**。

### 9.3 参数注入与仿真

- 连接参数仍来自 `device_registry`（`host` / `port` / `serial_port` / `baudrate` / `timeout` / `extra`），由 DM 在 `open` 时注入驱动配置，测试进程不接触地址。
- 仿真模式由 `mode="simulate"` 决定，走现有 `SimulateTransport`，**驱动代码零改动**。
- 仿真设备与真实设备共用同一别名，配方切换即可，无需改 TPS。

### 9.4 降级策略

| 场景 | 行为 |
| --- | --- |
| DM 未启动 | `DM-1001`，测试进程立即失败（不静默直连），由 Runner 拉起 DM 后重试 |
| DM 崩溃 | 客户端重连（3 次，间隔 500 ms），句柄失效需重新 `open`；已打开设备由 DM 重启后按预热表恢复 |
| 单设备故障 | 该句柄 `faulted`，其它设备不受影响 |
| 调试直连 | `ATE_DEVICE_MANAGER=0`，仅供单机调试，产线环境禁用（启动时校验并告警） |

---

## 10 可观测与治理

| 项目 | 内容 | 落地 |
| --- | --- | --- |
| 审计 | 时间、工位、TPS、句柄、动作、参数摘要、结果、耗时 | 结构化 JSONL，按日滚动，保留 30 天 |
| 指标 | 调用 QPS、P50/P95/P99、错误率、重连次数、租约等待时长 | `dm.status` 与本地指标端点 |
| 健康检查 | 周期 `idn` 探测（默认 60 s，可关） | 失败 2 次转 `faulted` 并告警 |
| 容量 | 句柄上限（默认 512）、单连接在途上限（32）、共享内存环大小 | 超限返回明确错误码而不是排队到超时 |

---

## 11 部署与守护

1. **单实例**：以 `Global\ate_dm_v1` 互斥体保证唯一；重复启动直接退出并交由既有实例服务。
2. **随 Runner 启动**：Runner 启动时以子进程方式拉起，Windows 下走 `ate_dm.exe`（PyInstaller，与 Runner 同一构建流水线）。
3. **崩溃拉起**：Runner 侧看护，退避 1 s / 3 s / 10 s，5 分钟内超过 3 次则停止并告警（防崩溃循环）。
4. **资源限制**：内存上限（默认 1 GB）、日志滚动（单文件 20 MB × 5）、共享内存环独立配额。
5. **升级**：DM 与驱动包版本独立演进；DM 升级需空载窗口（Runner 进入维护态），驱动包升级走热更新路径。

---

## 12 落地改动清单

| 类型 | 路径 | 内容 |
| --- | --- | --- |
| 新增 | `backend/devicemanager/__init__.py` | 对外导出 `DeviceManager`、`serve()` |
| 新增 | `backend/devicemanager/server.py` | 管道服务、帧编解码、方法分派 |
| 新增 | `backend/devicemanager/protocol.py` | 消息模型、错误码枚举、request_id 幂等缓存 |
| 新增 | `backend/devicemanager/registry.py` | 包扫描、manifest 校验、`driver_package` 读写、状态机 |
| 新增 | `backend/devicemanager/loader.py` | 命名空间隔离导入、实例构造、DriverHost 子进程管理 |
| 新增 | `backend/devicemanager/handles.py` | 句柄表、租约、single-flight、读写锁、TTL 回收 |
| 新增 | `backend/devicemanager/audit.py` | 审计 JSONL、指标聚合 |
| 新增 | `sdk/ate_dm/{__init__,client,proxy,errors}.py` | TPS 侧客户端与代理对象 |
| 新增 | `tools/dm_selftest.py` | 注册/加载/卸载/热更新/并发冲突的自测脚本 |
| 修改 | `conftest_template.py` | 按 `ATE_DEVICE_MANAGER` 开关分派（约 2 处） |
| 修改 | `backend/ate_db.py` | 新增 `driver_package` 表；`device_registry` 增列 `driver_version`、`lease_ttl` |
| 修改 | `backend/drivers/factory.py` | 保留 `get_driver` 供直连模式；补 `resolve_key()` 与动作读写分类 `ACTION_RW` |
| 不变 | `backend/drivers/{base,transport,models,scpi,camera,scope}.py` | 驱动实现不动，只补 `capabilities()` |

> 关键约束：**驱动层不感知 DM 的存在**。DM 通过 `get_driver`/`Driver.do()` 使用驱动，
> 驱动若为 DM 写专属逻辑，则直连模式立刻失效——这是本设计刻意避免的耦合。

---

## 13 分阶段计划

| 阶段 | 内容 | 出口条件 |
| --- | --- | --- |
| P0（骨架） | DM 进程 + 管道协议 + 注册/加载/卸载 + `psu` 一类驱动 + 客户端代理；conftest 开关 | 单工位 1 个 TPS 用例通过；注册-打开-调用-卸载全链路可复现 |
| P1（可用） | 全类别接入、句柄复用与 TTL、审计与指标、健康检查、`dm.status` 看板 | 三工位并发无冲突；单件节拍不低于直连模式 |
| P2（稳健） | DriverHost 子进程隔离、热更新与回切、熔断冷却、共享内存数据面 | 热更新不影响在途调用；杀 DriverHost 不影响 DM |

---

## 14 风险与取舍

| 决策 | 取 | 舍 | 缓解 |
| --- | --- | --- | --- |
| 独立进程 vs 进程内库 | 崩溃隔离、句柄集中、可跨语言接入 | 每次调用多一次 IPC | 批量动作（`dm.call_batch`）、本地管道零拷贝、P95 目标 < 5 ms |
| 集中持有 vs 各自持有 | 解决 P1~P5 | DM 成为单点 | 单实例 + 看护拉起 + 句柄表可重建；故障时降级到直连仅限调试 |
| 版本锁定 vs 取最新 | 产线结果可比、可回滚 | 升级需显式操作 | 环境锁文件 + `dm.activate` 一条命令切换 |
| 类别契约 vs 按型号直调 | 仪器可替换、TPS 稳定 | 可能拉平仪器特性 | 强制 `capabilities()` + 动作级可选特性；必要时允许包内扩展动作 |

**未解决/留待后续**：跨主机共享设备（多测试台争用同一台稀缺仪器）需要额外的分布式锁与网络鉴权，本版不做，
但句柄表已按 `(bench_id, device_id)` 设计，为后续扩展留出键空间。

---

## 15 验收标准

| 编号 | 标准 | 判定方式 |
| --- | --- | --- |
| A1 | 三工位并发调用同一台电源，无参数串扰 | 自测脚本制造写冲突，比对最终值与审计序列 |
| A2 | 测试进程被强杀后，设备在下一次 `open` 前已释放 | 杀进程后立即 `open` 成功，且审计有 `lease_expired` 记录 |
| A3 | 单次 `dm.call` P95 < 5 ms（不含设备自身耗时） | 1 万次 `idn` 压测 |
| A4 | 注册非法包七类错误全部可复现且码值正确 | `tools/dm_selftest.py` 覆盖 100% 错误码 |
| A5 | 热更新期间在途调用不中断、旧版本可回切 | 注入 3 个长调用，期间执行切换 |
| A6 | DriverHost 被强杀后 DM 主进程存活且其它设备可用 | 杀子进程后继续调用其它句柄 |
| A7 | 句柄复用生效：同一设备连续 200 次打开不重复建链 | 审计中 `open_count` 与 `reuse_count` 比例 |
| A8 | 直连模式（`ATE_DEVICE_MANAGER=0`）仍可用 | 回归现有 TPS 用例集 |

---

## 16 附：与现有文档的关系

| 文档 | 关系 |
| --- | --- |
| `docs/驱动软件设计/driver-kernel-design.html` | 驱动内核契约；DM 沿用其 `ACTIONS` 与 transport 分叉，不重复定义 |
| `docs/驱动软件设计/driver-distribution.md` | 包分发与货架；DM 的注册来源之一 |
| `docs/驱动软件设计/driver-package-views.md` | 包视图；`driver.json` 在此之上增加 `isolation` 与 `resources` |
| `docs/tps-runtime-workspace.html` | TPS 运行时工作区；DM 客户端挂载点 |
| `docs/runner-update-release.md` | 升级机制；DM 自身升级复用同一套发布与回滚流程 |
