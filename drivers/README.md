# ate-drivers · ATE 驱动仓库

按「能力契约 / 驱动包 / 解析器 / 运行时」四层组织的驱动仓库。三类发布物由 `manifest.tier` 声明：

| tier | 含义 | 分发方式 | 回滚粒度 |
| --- | --- | --- | --- |
| `baseline` | 标准柜内固定配置，随平台版本发布 | 平台安装包 | 整平台 |
| `shared` | 多台型复用且非柜内标配 | 平台统一制品仓，按包+版本拉取 | 单包单版本（改 lock） |
| `station` | 单一台型使用、随 TPS 自洽 | 不入本仓，随 TPS 交付 | 单台 |

## 现有包（三个能力，均为真机库直连 + 能力层仿真）

| 包 | 能力 | tier | 厂商库 | 真机入口 |
| --- | --- | --- | --- | --- |
| `rohde-schwarz-mxo` 1.3.0 | `oscilloscope` | baseline | `RsInstrument` 1.13x（底层 VISA） | `TCPIP::<ip>::INSTR` |
| `basler-pypylon` 1.0.0 | `camera` | baseline | `pypylon` 26.10（Basler pylon） | `serial:<sn>` / `ip:<addr>` / `first` |
| `pi-hexapod` 1.0.0 | `hexapod` | shared | `pipython` 2.13（PI GCS2） | `tcpip://<ip>:50000` / `usb:<sn>` / `COM3` |

三类包的分发差异见 `docs/设备驱动` 中的方案页；共享包的后续示例是同族六足与自准直仪，独立包示例是 PLC 控制器（随 TPS 交付，不入本仓）。

## 目录

```
contracts/          能力契约（L3）：TPS 唯一依赖的接口，禁止 import 厂商库
  scope.py          示波器：9 方法 / 8 测量项
  camera.py         相机：9 方法 / 6 统计项（口径由 compute_statistics 统一）
  motion.py         定位平台：9 方法，轴 X..W，单位 µm / µrad
kit/                注册表、解析器、一致性套件、制品仓客户端（不含业务与硬件）
  registry.py       清单校验 / 版本约束 / 目录哈希 / tier 分组
  resolve.py        台架声明 + lock → 加载计划
  conformance.py    契约一致性套件（按能力分派：scope / camera / hexapod 各 13 项）
  repo.py           制品仓客户端：fetch / plan / apply / publish
runtime/            运行时：会话、资源仲裁、超时、诊断
packages/           驱动包，一包一目录，独立版本
  rohde-schwarz-mxo/  manifest.json + pkg/rs_mxo/{driver,scpi,simulate}.py + vendor/ + conformance/
  basler-pypylon/     manifest.json + pkg/basler_pypylon/{driver,api,simulate}.py + conformance/
  pi-hexapod/         manifest.json + pkg/pi_hexapod/{driver,gcs,simulate}.py + conformance/
station-types/      台型清单：scope-bench（示波器台）/ standard-bench（示波器+相机+六足）
benches/<BENCH>/    devices.json（设备档案投影）+ bench.lock.json（锁定解析结果）
  BENCH-DEMO/       单能力台：示波器
  BENCH-CAB/        综合台：示波器 + 相机 + 六足，三包同进程
ci/pack.py          出制品：校验 → zip → sha256 → 制品仓索引；含边界检查
tools/              lock_bench.py（生成 lock）、demo_acquire.py、demo_station.py（三能力端到端）
tests/              流程测试（26 项）
```

## 实模式（hardware）需要什么

驱动代码直接调用厂商库，没有自研协议栈。实模式能不能跑，取决于三件事是否就位：

| 包 | 需要 | 缺失时的表现 |
| --- | --- | --- |
| rohde-schwarz-mxo | `RsInstrument` + 厂商 VISA（RsVisa32.dll / NI-VISA） | `DriverUnavailable: 无法建立会话 …` |
| basler-pypylon | `pypylon` + pylon Runtime（含 GigE 传输层与 IP 配置） | `DriverUnavailable: 未枚举到任何相机` |
| pi-hexapod | `pipython` + `PI_GCS2_DLL_x64.dll`（PI 软件安装后自带） | `DriverUnavailable: … GCS2 DLL not found` |

本机实测（已装三个库、未接设备）：

```
R&S 示波器    DriverUnavailable  无法建立会话 TCPIP::192.0.2.10::INSTR: Error while accessing RsVisa32.dll
Basler 相机   DriverUnavailable  无法打开相机 serial:00000000: 未枚举到任何相机
PI 六足平台   DriverUnavailable  无法连接 tcpip://192.0.2.31:50000: PI_GCS2_DLL_x64.dll not found
```

即：库缺失或设备不在线一律显式失败，不静默降级到仿真。仿真只在台架装备属性写 `mode=simulate` 时启用。

三个包各自有一份 `VERIFY` 清单（`scpi.py` / `api.py` / `gcs.py`），列出必须在真机核对的命令与单位，核对通过后才把 `verified_on_hardware` 置 `true`。

## 分发：平台统一制品仓（没有离线介质）

- **下载**：`Repo.fetch(name, version)` —— 读 `index.json` → 校验 zip 的 `zip_sha256` → 解压 → 再校验目录 `tree_sha256`。两道校验任一不符即拒绝。
- **推送**：`ci/pack.py` 出 `dist/repo/`，CI 调 `Repo.publish()` 写索引条目；台架侧只拉取（单向），不回写制品仓。
- **更新**：`Repo.plan(lock)` 比对 lock 与本机已装版本，得到 `install / ok / switch`；`Repo.apply(plan)` 执行。回滚 = 把 lock 里的版本改回旧值再跑一次。
- **管理颗粒度**：最小单元是「包 + 版本」，生效以台架为单位（一台一份 lock）。

```bash
python ci/pack.py --root . --out dist/repo --channel stable
python -c "from kit.repo import Repo; r=Repo('dist/repo'); p=r.plan('benches/BENCH-CAB/bench.lock.json','dist/site'); r.apply(p, dest='dist/site')"
```

## 仿真：只在能力层

`mode="simulate"` 时驱动**不建会话、不下发命令**，直接委托各自的 `CapabilitySimulator`：

- 输入是契约参数（量程 / 曝光 / 目标位姿），不是命令字符串；
- 输出是契约结构（`Waveform` / `Frame` / `Position` / 统计量），不经过任何解析；
- 不接管连接、寻址与链路；资源名只用于身份串。

它验证「能力语义是否正确」，不验证「命令拼写是否正确」；不模拟噪声带宽、镜头光学与机构误差，不得替代真机验收。

## 常用命令

```bash
# 契约一致性（按能力自动选套件）
python -m kit.conformance --root . --package rohde-schwarz-mxo --mode simulate
python -m kit.conformance --root . --package basler-pypylon  --mode simulate
python -m kit.conformance --root . --package pi-hexapod      --mode simulate

# 三能力端到端（解析 → 会话 → 采集 / 取图 / 定位）
python tools/demo_station.py --station station-types/standard-bench.json --bench BENCH-CAB

# 生成 / 刷新 lock
python tools/lock_bench.py --root . --station station-types/standard-bench.json --bench BENCH-CAB --runner 1.7.3

# 出制品与校验
python ci/pack.py --root . --out dist/repo
python ci/pack.py --root . --out dist/repo --verify
python ci/pack.py --root . --out dist/repo --boundaries

# 流程测试
python -m unittest discover -s tests -v
```

## 硬规则

1. **厂商库只能出现在 `packages/*/pkg/**`**；`contracts/` `kit/` `runtime/` 内出现厂商库名即越界（CI 检查，含 `pipython` / `pypylon` / `RsInstrument` / `pyvisa`）。
2. **用例只认能力与别名**，不出现包名、版本、型号、资源名。
3. **一台机器一个进程只加载一份解析结果**；并发多台架走多进程。同一能力被要求两个版本时抛 `VersionConflict`。
4. **不静默降级**：`mode="hardware"` 下缺库、枚举不到设备、连不上控制器必须报错；只有装备属性显式声明 `simulate` 才启用仿真。
5. **统计口径写在契约里**：相机六个统计项必须调用 `contracts.camera.compute_statistics`，换品牌不换口径。
6. **单位写在契约里**：定位平台平移轴 µm、旋转轴 µrad，包层负责换算，用例永远看到契约单位。
7. **`tier` 必填**：`baseline` / `shared` / `station`，决定发布节奏与回滚粒度，不是技术栈分类。

## 与既有 `sdk/ate_drivers` 的关系

本仓库是 P1 阶段的新建目标结构，先用三个垂直切片（示波器 / 相机 / 六足）验证契约、解析、运行时与出制品链路。既有 `sdk/ate_drivers`（transport / families / vendors）保持不动，待共享包试点通过后按其能力逐项迁移。
