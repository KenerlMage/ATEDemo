# Basler 相机驱动（pylon 8.0.0）

面向 ATE 的工业相机驱动，两套运行时同时提供：

| 层 | 位置 | 用途 |
| --- | --- | --- |
| SDK / 驱动库 | `sdk/ate_drivers/family/camera.py` + `sdk/ate_drivers/vendors/basler_pylon/` | 驱动开发、型号派发、门禁校验、TPS 用 `open_camera()` |
| Runner 运行时 | `backend/drivers/camera.py`（`device_type = basler-pylon`） | 测试台注册后由 `drivers.get_driver()` 实例化，TPS 里 `dev.do(...)` |

两层语义一致、返回结构一致（都是 `{ok, value, detail, ...}`），仿真/真机同一入口。

## 1. 版本基线

- 运行时：Basler pylon Software Suite **8.0.0**（含驱动、pylon Viewer）
- Python 绑定：`pypylon`（与运行时同基线，`8.x`）
- 依赖策略：**惰性导入**。未装 `pypylon` 时仿真模式完全可用；真机模式给出中文处置建议
  （`sdk` 侧已把 `pypylon`、`numpy` 登记进 `kit/conformance.OPTIONAL_RUNTIME` 允许清单）

`state()` / `identity()` 会记录 `pylon_runtime`、`pypylon` 与基线版本，便于现场核对。

## 2. 能力与动作

| 能力 | 动作名 | 说明 |
| --- | --- | --- |
| `camera.enumerate` | `enumerate` | 枚举在线相机（型号、序列号、自定义名、接口、IP） |
| `camera.configure` | `configure` | 设参数：尺寸、ROI、像素格式、曝光、增益、帧率、触发 |
| `camera.grab` | `grab` | 取一帧，返回尺寸 + 统计量（+ 32×24 缩略图） |
| `camera.snap` | `snap` | 取一帧落盘 PNG，返回路径、字节数、sha256 |
| `camera.sequence` | `sequence` | 连拍 N 帧，返回逐帧统计、帧率、均值漂移 |
| `camera.stats` | `stats` | 指定区域统计（`full` / `center` / `{x,y,w,h}`） |
| `camera.trigger` | `trigger` | 软触发（需先 `trigger_mode="On"`） |
| — | `idn` / `reset` / `state` | 识别、复位到默认参数、状态快照 |

**统计量口径**（所有相机一致，TPS 可直接写阈值）：

`mean` / `min` / `max` / `std` / `contrast = max-min` / `saturated_pct(≥250)` / `bright_pct(≥mean+std)` / `pixels`

为什么要统计量：ATE 里相机的判据多半是「亮度、对比度、饱和率、亮区面积」，不是看图。

## 3. TPS 里的用法

Runner 运行时（`backend/drivers`）：

```python
cam = ctx.device("CAM-01")            # 测试台注册时 device_type 选 basler-pylon
cam.do("configure", exposure_us=8000, gain_db=2.0, pattern="checker")
shot = cam.do("grab")
assert shot["value"]["stats"]["contrast"] > 80, shot["value"]["stats"]
cam.do("snap", path=f"{ctx.run_dir}/cam_checker.png")
```

SDK（`ate_drivers`）：

```python
from ate_drivers import open_camera

with open_camera("acA1920-40gm", mode="simulate", setup={"pattern": "spot", "level": 230}) as cam:
    st = cam.grab()["value"]["stats"]
    assert st["mean"] > 20
```

设备档案字段（注册页/`device_registry`）：

| 字段 | 值 |
| --- | --- |
| `device_type` | `basler-pylon` |
| `interface` | `USB3` / `GIGE`（相机不走 LAN/SERIAL） |
| `model` | 型号串，如 `acA1920-40gm`；SDK 侧按型号派发，未登记直接报错 |
| `extra.serial_number` | 指定序列号（多机同站必填） |
| `extra.user_name` | pylon Viewer 里设的自定义名，作为第二选择器 |
| `extra.index` | 序号，默认 0（第一台） |
| `extra.camera` | 采集参数初值，如 `{"exposure_us": 8000, "width": 1920}` |

选机优先级：**序列号 → 自定义名 → 序号**，不做模糊匹配。

## 4. 真机部署要求

1. 安装 pylon 8.0.0 运行时与驱动，用 pylon Viewer 先确认能出图；
2. `pip install pypylon`（与运行时同基线），重启 Runner 进程；
3. GigE 相机：网卡独立网段、开启巨型帧（Jumbo Frame 9K）、关闭节能中断节流；
   PoE 供电不足会导致「枚举得到、打不开」；
4. USB3 相机：主机控制器独占、线缆不超过 3 m、避免与存储设备共用控制器；
5. 单台相机同时只允许一个进程打开（pylon Viewer 与 Runner 并存会互相踢）；
6. 采集参数由驱动统一下发，**不要在 pylon Viewer 里另存 User Set 覆盖**。

## 5. 仿真模型（无硬件可跑通）

6 种图样，按 `(参数, 帧序号)` 确定性生成：

| 图样 | 用途 | 特征 |
| --- | --- | --- |
| `flat` | 亮度/均匀性 | 均值 = `level`，`std` ≈ 噪声 |
| `gradient` | 均匀性/背光 | 水平渐变，`std` 大 |
| `checker` | 解析力/对焦 | 32 px 棋盘，`contrast ≈ 0.75·level` |
| `edge` | 边界定位 | 左暗右亮，`contrast` 接近满幅 |
| `spot` | 光斑能量 | 中心高斯亮斑，中心均值 > 全幅 |
| `noise` | 信噪比 | 均值固定，`std` 由 `noise` 控制 |

同一 `(参数, frame_id)` 永远同结果 —— 阈值断言可复现，报告不抖。连拍时 `frame_id` 递增，
噪声实现随帧变化，因此 `sequence()` 的 `mean_spread` 能反映「稳定性」而不会恒为 0。

## 6. 故障处置

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `未安装 pypylon…` | 缺 Python 绑定 | 装 pypylon（版本与运行时对齐），或先用仿真 |
| `未发现 Basler 相机` | 链路/供电/网段 | pylon Viewer 交叉验证；查 PoE、巨帧、防火墙 |
| `打开相机失败… 被占用` | 其他进程持有 | 关 pylon Viewer / 上个 Runner；必要时重启相机服务 |
| `取帧超时` | 曝光 > 超时 / 触发在等信号 | 调大 `timeout_ms` 或缩短曝光；确认 `trigger_mode` |
| `TriggerMode 置值失败` | 机型不支持该触发源 | 看 `state().warnings`，换 `Line1` 或改软触发 |
| `Gain 节点不存在` | dart/boost 等机型无该节点 | 驱动自动降级 `GainRaw`，再失败则忽略（记 warning） |
| 图像全黑 / 全白 | 曝光或增益极端 / 镜头盖 | 用 `stats()` 看均值与饱和率，先看统计再看图 |

## 7. 自检与门禁

```bash
python -m pytest sdk/tests/test_camera_pylon.py -q   # 19 项：能力声明、仿真确定性、PNG 结构、派发、缺依赖报错
python -c "import sys; sys.path.insert(0,'sdk'); from ate_drivers import camera_factory; print(camera_factory.self_check())"
```

`driver.json` 与类声明（`MODELS` / `CAPABILITIES` / `family` / `key`）必须一致，测试里已做双向校验。
