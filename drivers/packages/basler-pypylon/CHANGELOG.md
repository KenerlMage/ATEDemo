# basler-pypylon

Basler 工业相机驱动包（能力：`camera`，契约：`1.0`，`tier=baseline`）。

## 状态

| 项 | 值 |
| --- | --- |
| 版本 | 1.0.0 |
| 发布单元 | 基础包（柜内固定配置），随平台版本发布 |
| 分发 | 平台统一制品仓（`platform-repo`，`channel: stable`） |
| 厂商库 | `pypylon==26.10`（pylon Runtime 需随现场安装） |
| 真机验证 | 未验证（`verified_on_hardware: false`） |
| 仿真模式 | 支持，能力层仿真，契约套件 13/13 |

## 1.0.0

- 按 `contracts.camera` 1.0 实现 9 个方法；六个统计项统一调用 `contracts.camera.compute_statistics`，换品牌不换口径。
- **实模式直连 pypylon**：`TlFactory.EnumerateDevices()` → `CreateDevice` → `InstantCamera.Open()`；曝光走 `ExposureTime` node，失败前先读 `GetMin/GetMax` 夹紧范围；ROI 按「偏移归零 → 设尺寸 → 再设偏移」的顺序下发。
- 资源名支持 `serial:<sn>` / `ip:<addr>` / `user:<name>` / `model:<name>` / `first`，多相机柜内可按序列号固定绑定。
- 取图使用 `GrabStrategy_LatestImageOnly` + `TimeoutHandling_ThrowException`，失败抛 `CameraTimeout` 而不是返回空帧。
- **仿真改为能力层**：`simulate.py` 接收曝光/增益/ROI，直接产出 `Frame`；亮度随曝光单调上升，含竖直亮条（锐度）与高曝光中心饱和斑（saturation_ratio）。
- 缺 `pypylon` 或枚举不到相机时抛 `DriverUnavailable`，不静默降级到仿真。
- 随包契约测试：`python packages/basler-pypylon/conformance/test_contract.py`。

## 待办（转正式版前必须完成）

1. 在真机核对 `api.py` 里 `VERIFY` 五项：`ExposureTime` 单位与上下限、`Gain` 是否 dB、ROI 步长与写入顺序、`StartGrabbing` 策略、`GetArray()` 的 dtype 与行序。
2. 确认 GigE 相机的心跳超时（`GevHeartbeatTimeout`）与断线重连策略，写入 `describe()`。
3. 核对通过后把 `verified_on_hardware` 置 `true` 并升 1.1.0。
