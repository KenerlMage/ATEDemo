---
name: ate-driver-software
description: ATE 设备驱动软件的设计规范（四层契约架构、三种 tier 发布物判据、能力契约写法、真机直连与不静默降级、能力层仿真边界、制品仓分发与 lock 回滚、一致性门禁与新增包 checklist）。Use when 新增或改造驱动包、定义/修改能力契约、评审驱动分层与发布策略、排查实模式连接失败。
---

# ATE 驱动软件设计规范

本规范来自 ATE 驱动仓 `D:\ATE\drivers` 的实现与评审结论（示波器 / 相机 / 六足三个垂直切片，26 项流程测试、每包 13 项契约一致性、制品仓三链路全绿）。改代码前先读这里；发现规范与代码不一致，以本文件为准并同步改代码。

## 一、基本立场

1. **不否定抽象层，但不自研协议栈。** 连接、寻址、帧同步、厂商命令拼装一律交给厂商库；自研的只有「契约」这一层。
2. **砍掉链路实现（Transport 自研层），保留连接契约。** 相机这类不做「命令-响应」语义的设备，走 `DeviceSession` 语义，不硬套 Transport。
3. **驱动分层按「变化频率」切，不按「设备类型」切。** 变化最慢的是能力契约，最快的是厂商库调用。
4. **驱动不全集成进平台。** 稳定集内置随平台发布，长尾外挂为独立包，按需拉取。

## 二、四层结构与依赖方向

```
L4 用例层     只认别名与能力名 → ctx.driver("scope").measure(1, "vpp")
L3 契约层     contracts/ ：接口 + 数据结构 + 错误层级，禁止 import 任何厂商库
L2 解析/运行时 kit/ + runtime/ ：registry / resolve / conformance / repo / session
L1 包层       packages/<name>/ ：driver.py 调厂商库、simulate.py 出能力层仿真
```

依赖单向向下，**层间只有三种连接，没有第四种**：

| 层间 | 通过什么连接 | 禁则 |
| --- | --- | --- |
| 用例 → 契约 | 别名 + 能力名；只 `import contracts` | 不出现包名、版本、型号、资源名 |
| 契约 → 解析 | `resolve(station, lock)` 返回 `ResolutionSet` | 用例不得传型号或地址；运行时不得改 lock |
| 解析 → 包层 | `session.driver(alias)` 返回契约实例 | 同能力一进程一版本，冲突抛 `VersionConflict` |
| 包层 → 厂商库 | 延迟 import + 包内 `vendor/` | 厂商对象与命令字符串不得越过包层 |
| 启动期 → 运行期 | 装备属性 + lock → 加载计划（纯数据） | 匹配失败即拒绝启动，不做模糊匹配 |

**真正的连接点只有两个**：启动期 `resolve(...)`、运行期 `session.driver(alias)`。其余全是数据传递。错误不改写：包层把厂商异常翻译成契约异常，解析层不吞，运行时只追加台架上下文（bench / capability / resource）。

设备匹配不靠用户传入：连接清单 + 型号 BOM 属**台架装备属性**，由平台在注册时匹配；失败即「型号未登记，拒绝启动并列出缺失项」。

## 三、三种 tier（发布物分类）

| tier | 含义 | 分发 | 回滚粒度 | 判据 |
| --- | --- | --- | --- | --- |
| `baseline` | 标准柜内固定配置 | 平台安装包 | 整平台 | 每台都装、位置固定、不随台型变化 |
| `shared` | 多台型复用但非柜内标配 | 平台统一制品仓按需拉取 | 单包单版本（改 lock） | 复用面 ≥2 台型，且变更风险低 |
| `station` | 单台型专用 | 不入驱动仓，随 TPS 交付 | 单台 | 只为一种产品/工序存在 |

判据看**复用面 × 变更风险**，不看技术栈难度。示例：示波器是柜内固定配置 → baseline；PI 六足 + 自准直仪跨产品复用 → shared；PLC 随某型号 TPS → station（独立包）。

`tier` 是 `manifest.json` 必填字段，非法值直接拒绝加载。

## 四、能力契约（L3）

一个能力 = 一个模块，只放四类东西：数据结构、错误层级、抽象基类、口径统一的共享函数。

```python
# contracts/<capability>.py
CONTRACT_VERSION = "1.0"
CAPABILITY = "camera"

@dataclass(frozen=True)
class Frame: ...
class CameraError(Exception): ...          # 契约异常基类
class CameraNotConnected(CameraError): ...
class CameraUnsupported(CameraError): ...
class CameraTimeout(CameraError): ...
class CameraDataError(CameraError): ...

class CameraDriver(ABC):                    # 9 个方法，不多不少
    def connect(self) -> CameraIdentity: ...
    ...

def compute_statistics(frame, roi=None) -> dict:   # 统计口径唯一实现
    ...
```

硬要求：

1. **单位写进契约**：定位平台平移轴 `µm`、旋转轴 `µrad`；包层只做 float 规整，不做无依据换算。
2. **统计口径写进契约**：相机六个统计项（mean_gray / saturation_ratio / snr_db / sharpness / uniformity / defect_ratio）必须调用 `compute_statistics`，换品牌不换口径。
3. **错误层级固定**：`XxxError` 基类 + NotConnected / Unsupported / Timeout / DataError 四类子类，禁止在契约里抛原生 `Exception` 或厂商异常。
4. `contracts/__init__.py` 里维护 `CAPABILITIES = {name: module}`，新增能力只改这一处。

## 五、包结构（L1）

```
packages/<name>/
  manifest.json          必填：name version capability contract tier channel
                         distribution entry vendor_deps simulate_layer
                         verified_on_hardware + tree_sha256
  pkg/<mod>/__init__.py  导出驱动类与仿真类
  pkg/<mod>/driver.py    契约实现；厂商库一律延迟 import
  pkg/<mod>/simulate.py  CapabilitySimulator：接收契约参数、产出契约对象
  pkg/<mod>/<vendor>.py  该厂商的命令/资源细节（如 scpi.py / api.py / gcs.py）
  pkg/<mod>/API 说明     VERIFY 清单写在这个文件顶部
  vendor/                厂商库副本（可缺省，缺则要求现场已装）
  conformance/test_contract.py   随包契约测试
  CHANGELOG.md           版本条目 + 待办（真机需要核对的项）
```

`driver.py` 的固定写法：

```python
def __init__(self, resource, mode="hardware", **kw):
    self._mode = mode
    self._io = None                     # 真机对象，仿真时保持 None
    self._sim = CapabilitySimulator(...) if mode == "simulate" else None

def connect(self):
    if self._mode == "simulate":
        return self._identity()         # 不建会话、不下发命令
    try:
        import pypylon                 # 延迟 import，失败落到 DriverUnavailable
    except ImportError as exc:
        raise DriverUnavailable(
            "缺厂商库 pypylon：请安装 ... 或放入 packages/<name>/vendor/"
        ) from exc
    ...
```

## 六、实模式纪律

1. **缺库 / 设备不在线 → 抛 `DriverUnavailable`，绝不静默降级到仿真。** 仿真只在装备属性显式写 `mode=simulate` 时启用。
2. **写之前先读范围**：曝光、增益、行程这类参数，先 `GetMin/GetMax` 夹紧再写，越界抛 `Unsupported` 而不是让设备自己截。
3. **顺序敏感的操作按厂商要求下发**：相机 ROI = 偏移归零 → 设尺寸 → 再设偏移。
4. **超时抛异常，不返回空对象**：取图用 `GrabStrategy_LatestImageOnly` + `TimeoutHandling_ThrowException`。
5. **未回零拒绝运动**：`reference()` 之前调用 `move_to` 抛 `MotionUnsupported`，与真机行为一致，强迫上层把时序写对。
6. **资源名统一格式**：`TCPIP::<ip>::INSTR`、`serial:<sn>`、`ip:<addr>`、`tcpip://<ip>:<port>`、`usb:<sn>`、`COM3`；解析集中在包内一个函数里。
7. **错误文案要可操作**：写清缺什么（库名 / DLL 名 / 驱动名）、放在哪、怎么装。
8. 每个包的厂商细节文件顶部必须有一份 **`VERIFY` 清单**：列出只能在真机核对的命令与单位。核对通过才把 `verified_on_hardware` 置 `true` 并升版本。

实测应得到这样的结果（库已装、无设备）：

```
R&S 示波器    DriverUnavailable  无法建立会话 ...: Error while accessing RsVisa32.dll
Basler 相机   DriverUnavailable  无法打开相机 serial:00000000: 未枚举到任何相机
PI 六足平台   DriverUnavailable  无法连接 tcpip://...: PI_GCS2_DLL_x64.dll not found
```

## 七、仿真边界

**仿真只在能力层**：`mode="simulate"` 时驱动不建会话、不下发命令，直接委托 `CapabilitySimulator`。

- 输入是契约参数（量程 / 曝光 / 目标位姿），不是命令字符串；
- 输出是契约结构（`Waveform` / `Frame` / `Position` / 统计量），不经过任何解析；
- 不接管连接、寻址与链路；资源名只用于身份串；
- 只验证「能力语义是否正确」，不验证「命令拼写是否正确」；不模拟噪声带宽、镜头光学、机构误差。

**仿真不替代真机验收**：`verified_on_hardware` 保持 `false` 直到真机核对完成。

术语禁用：全文不得说「替换 IO」「IO 层」「仿真层替换」；统一说「能力层按 mode 分派」。

## 八、分发：制品仓，没有离线介质

软件没有离线介质，分发改平台统一制品仓。

- **下载**：`Repo.fetch(name, version)` → 读索引 → 校验 zip 的 `zip_sha256` → 解压 → 再校验目录 `tree_sha256`。两道哈希任一不符即拒绝。
- **推送**：`ci/pack.py` 出 `dist/repo/`，CI 调 `Repo.publish()` 写索引；台架侧只拉不写（单向）。
- **更新**：`Repo.plan(lock)` 得出三态 `install | switch | ok`，`Repo.apply(plan)` 执行。
- **回滚**：改 lock 里的版本号再跑一次 apply，不改代码、不重装平台。
- **管理颗粒度 =「包 + 版本」**：不按整仓、不按文件；生效范围以台架为单位（一台一份 lock）。
- 共享包与基础包**同属一个代码仓**（同 PR、同门禁），差别只在发布通道（`stable` / `canary`）与生效范围。

## 九、硬规则（CI 可检）

1. 厂商库只能出现在 `packages/*/pkg/**`；`contracts/` `kit/` `runtime/` 内出现厂商库名即越界（`ci/pack.py --boundaries`，`VENDOR_HINTS` 含 RsInstrument / pyvisa / pypylon / pipython / pylibfranka）。注意：连文档字符串里写包名都可能被命中。
2. 用例只认能力与别名，不出现包名、版本、型号、资源名。
3. 一台机器一个进程只加载一份解析结果；并发多台架走多进程；同能力两版本抛 `VersionConflict`。
4. 不静默降级（见第六节第 1 条）。
5. 统计口径与单位由契约定义，包层不得私改。
6. `tier` 必填且取值合法。
7. 每个包必须有 `conformance/test_contract.py` 且通过 13 项套件。

## 十、门禁与常用命令

```bash
# 契约一致性（按能力自动选套件：scope / camera / hexapod 各 13 项）
python -m kit.conformance --root . --package <包名> --mode simulate
python -m kit.conformance --root . --package <包名> --mode hardware --resource <资源名>

# 端到端（解析 → 会话 → 采集 / 取图 / 定位）
python tools/demo_station.py --station station-types/standard-bench.json --bench BENCH-CAB

# 制品仓
python ci/pack.py --root . --out dist/repo --channel stable
python ci/pack.py --root . --out dist/repo --verify
python ci/pack.py --root . --out dist/repo --boundaries

# lock 与测试
python tools/lock_bench.py --root . --station station-types/<t>.json --bench <BENCH> --runner 1.7.3
python -m unittest discover -s tests -v
```

注意：`python kit/conformance.py` 直接执行会因 `sys.path[0]=kit` 导致 `contracts` 导入失败，必须走 `python -m kit.conformance` 或外部 harness。

## 十一、新增一个包的 checklist

1. 判断能力是否已有契约；没有就先写 `contracts/<capability>.py`（数据结构 + 错误层级 + ABC + 统一口径函数），并在 `CAPABILITIES` 注册。
2. 定 `tier`，写 `manifest.json`（含 `vendor_deps`、`entry`、`simulate_layer: capability`）。
3. 写 `simulate.py`：只吃契约参数、只吐契约对象。
4. 写 `driver.py`：延迟 import、资源名解析、写前读范围、超时抛异常、缺库抛 `DriverUnavailable`。
5. 在厂商细节文件顶部写 `VERIFY` 清单（5~6 条真机待核对项）。
6. 写 `conformance/test_contract.py`，跑满 13 项。
7. 加台型声明（`station-types/`）与台架档案（`benches/<BENCH>/devices.json`），生成 `bench.lock.json`。
8. 跑 `ci/pack.py`（含 `--verify` 与 `--boundaries`），确认 zip 与索引正常。
9. 写 `CHANGELOG.md`：版本条目 + 待办（真机核对项）；`verified_on_hardware` 保持 `false`。
10. 真机核对 `VERIFY` 全过后，置 `true` 并升 minor 版本；共享包再进灰度。

## 十二、术语表

| 用 | 不用 |
| --- | --- |
| 能力契约 / 契约层 | 接口定义文件 |
| 能力层仿真 | 假 IO、Mock 层、替换 IO |
| 解析（resolve）/ 加载计划 | 找驱动 |
| 制品仓 / 索引 | 离线包、安装介质 |
| 台架装备属性 | 配置参数 |
| 包 + 版本（颗粒度） | 整仓同步 |
