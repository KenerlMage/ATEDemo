# rohde-schwarz-mxo

R&S MXO 系列示波器驱动包（能力：`oscilloscope`，契约：`1.0`）。

## 状态

| 项 | 值 |
| --- | --- |
| 版本 | 1.2.0 |
| 真机验证 | 未验证（`verified_on_hardware: false`） |
| 仿真模式 | 支持，契约测试全绿 |
| 目标型号 | MXO44 / MXO54 / MXO58 |
| owner | 仪器驱动组 |

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
3. 通过后把 manifest 的 `verified_on_hardware` 置 `true`，版本升 `1.3.0` 并进入共享包目录。
