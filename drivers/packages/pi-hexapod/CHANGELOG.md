# pi-hexapod

PI 六足平台驱动包（能力：`hexapod`，契约：`1.0`，`tier=shared`）。

## 状态

| 项 | 值 |
| --- | --- |
| 版本 | 1.0.0 |
| 发布单元 | 共享包（多台型复用、非柜内标配），独立发版 |
| 分发 | 平台统一制品仓（`platform-repo`，`channel: stable`） |
| 厂商库 | `pipython==2.13.0.2`（需 PI GCS2 DLL，随 PI 软件安装） |
| 真机验证 | 未验证（`verified_on_hardware: false`） |
| 仿真模式 | 支持，能力层仿真，契约套件 13/13 |

## 1.0.0

- 按 `contracts.motion` 1.0 实现 9 个方法：`reference` / `move_to` / `move_relative` / `query_position` / `stop`。
- **实模式直连 GCS**：`GCSDevice('C-887')` + `ConnectTCPIP/ConnectUSB/ConnectRS232`；目标位置用 `MOV`（接受轴字典），到位用 `pitools.waitontarget`，停止用 `HLT`。
- 资源名支持 `tcpip://<ip>:<port>` / `usb:<sn>` / `serial://COM3[:baud]` / 裸 `COM3`；解析逻辑集中在 `gcs.parse_resource`。
- **单位由契约固定**：平移轴 µm、旋转轴 µrad；包层只做 float 规整，不做无依据缩放（避免把 mm 当 µm 换算错）。
- **未回零拒绝运动**：`reference()` 之前调用 `move_to` 抛 `MotionUnsupported`，与真机行为一致，强迫上层把时序写对。
- 行程保护：优先读 `qTMN/qTMX`；固件不返回六足空间轴行程时不做软件夹紧，交给控制器拒绝（抛 `MotionLimitError`）。
- **仿真改为能力层**：线性伺服 + 行程限位 + 到位时间估算；`describe()` 暴露 `referenced`、`position`、`limits`、`last_settle_s`。
- 缺 `pipython` 或控制器连不上时抛 `DriverUnavailable`，并提示 GCS2 DLL 缺失这一常见原因。
- 随包契约测试：`python packages/pi-hexapod/conformance/test_contract.py`。

## 待办（转正式版前必须完成）

1. 在真机核对 `gcs.py` 里 `VERIFY` 六项：`qIDN` 字段顺序、`qTMN/qTMX` 对空间轴的返回、`MOV` 的 dict 形式与同步性、`FRF`（或 `GOH/REF`）回零命令、`qPOS` 单位、`HLT` 是否断使能。
2. 六足姿态耦合：`move_to({"X": …})` 只动单轴时是否需要联动保持其它轴位置，确认后写入 `describe()` 的 `coupled` 字段。
3. 核对通过后把 `verified_on_hardware` 置 `true` 并升 1.1.0；同时作为共享包进入 P2 灰度。
