# rohde-schwarz-mxo

R&S MXO 系列示波器驱动包（能力：`oscilloscope`，契约：`1.0`，`tier=baseline`）。

## 状态

| 项 | 值 |
| --- | --- |
| 版本 | 1.3.0 |
| 发布单元 | 基础包（`tier: baseline`），随平台版本发布 |
| 分发 | 平台统一制品仓（`distribution: platform-repo`，`channel: stable`） |
| 真机验证 | 未验证（`verified_on_hardware: false`） |
| 仿真模式 | 支持，能力层仿真，契约测试 13/13 |
| 目标型号 | MXO44 / MXO54 / MXO58 |
| owner | 仪器驱动组 |

## 1.3.0

- **仿真改为能力层**：删除 1.2.0 的 SCPI 替身（`SimIo`），新增 `CapabilitySimulator`。仿真不再接收/解析命令字符串，直接按契约参数生成 `Waveform` 与测量值；不建会话、不接管连接与链路。`describe()` 新增 `simulation.layer = capability`。
- **新增 `tier` / `channel` / `distribution` 字段**：`tier=baseline` 表示本包属于标准柜固定配置，随平台发布；共享包（多台型复用）与独立包（随 TPS）用不同 tier 标识。`tier` 为清单必填项，注册表校验非法值直接拒绝。
- **分发改为平台制品仓**：`ci/pack.py` 产出 `dist/repo/`（zip + `index.json`，含 tier/channel/distribution）；新增 `kit/repo.py` 提供 `fetch / plan / apply / publish`。取消「离线介质导入」路径，现场按 `bench.lock.json` 拉取，回滚改 lock 版本即可。
- `bench.lock.json` 增加 `distribution` 段（mode / repo / channel），lock 即部署契约。
- 测试增至 15 项：新增仿真层级断言、制品仓发布/拉取/防篡改、lock 计划三态。

## 1.2.0

- 按 `contracts.scope` 1.0 重写：八个测量项（vpp / vmax / vmin / mean / rms / frequency / period / rise_time）统一从最近一次采集推算，保证跨厂商语义一致。
- 拆出 `scpi.py` 命令表，型号差异集中一处；标记 `VERIFY` 的命令需真机核对。
- 新增 `simulate.py`：仿真走与真机相同的驱动路径，只替换 IO 层，CI 与现场自检可用。
- `mode="hardware"` 下若 RsInstrument 缺失或会话建立失败，抛 `DriverUnavailable`，不静默降级。
- 采集时间轴优先取 `CHANnel<n>:DATA:HEADer?`；不可用时按当前时基回退并在 `describe()` 中体现。

## 1.1.0

- 引入 `ChannelSpec` 与写缓存，减少重复下发量程命令。

## 1.0.0

- 首个可用版本：时基 / 垂直档位 / 边沿触发 / 波形采集。

## 待办（出正式包前必须完成）

1. 在至少一台 MXO 上跑 `kit.conformance --mode hardware`，核对全部标记 `VERIFY` 的 SCPI 路径与返回格式。
2. 确认 `CHANnel<n>:DATA?` 的字节序与点数上限，必要时改用分块读取。
3. 通过后把 manifest 的 `verified_on_hardware` 置 `true`，并把版本推入 `stable` 通道。
