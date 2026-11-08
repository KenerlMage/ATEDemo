# ate-drivers · ATE 驱动仓库

按「能力契约 / 驱动包 / 解析器 / 运行时」四层组织的驱动仓库。三类发布物由 `manifest.tier` 声明：

| tier | 含义 | 分发方式 | 回滚粒度 |
| --- | --- | --- | --- |
| `baseline` | 标准柜内固定配置，随平台版本发布 | 平台安装包 | 整平台 |
| `shared` | 多台型复用且非柜内标配 | 平台统一制品仓，按包+版本拉取 | 单包单版本（改 lock） |
| `station` | 单一台型使用、随 TPS 自洽 | 不入本仓，随 TPS 交付 | 单台 |

首个实现：`packages/rohde-schwarz-mxo`（R&S MXO 示波器，`tier=baseline`）。共享包的后续示例是 PI 六足机器人（hexapod）与子准直仪；独立包示例是 PLC 控制器。

## 目录

```
contracts/          能力契约（L3）：TPS 唯一依赖的接口，禁止 import 厂商库
kit/                注册表、解析器、一致性套件、制品仓客户端（不含业务与硬件）
  registry.py       清单校验 / 版本约束 / 目录哈希
  resolve.py        台架声明 + lock → 加载计划
  conformance.py    契约一致性套件（13 项）
  repo.py           制品仓客户端：fetch / plan / apply / publish
runtime/            运行时：会话、资源仲裁、超时、诊断
packages/           驱动包，一包一目录，独立版本
  rohde-schwarz-mxo/
    manifest.json   身份 / tier / channel / 契约 / 兼容范围 / 入口
    pkg/rs_mxo/     驱动实现 + SCPI 命令表 + 能力层仿真
    vendor/         包私有第三方依赖（随制品分发）
    conformance/    随包发布的契约测试
station-types/      台型清单：声明需要哪些能力与版本范围
benches/<BENCH>/    devices.json（设备档案投影）+ bench.lock.json（锁定解析结果）
ci/pack.py          出制品：校验 → zip → sha256 → 制品仓索引；含边界检查
tools/              lock_bench.py（生成 lock）、demo_acquire.py（端到端演示）
tests/              端到端流程测试
```

## 分发：平台统一制品仓（没有离线介质）

代码仓与制品仓分工固定，现场只读制品仓：

- **下载**：`Repo.fetch(name, version)` —— 读 `index.json` → 校验 zip 的 `zip_sha256` → 解压 → 再校验目录 `tree_sha256`。两道校验任一不符即拒绝，不回退本地缓存。
- **推送**：`ci/pack.py` 出 `dist/repo/`，CI 调 `Repo.publish()` 写索引条目；台架侧**不推送**，只拉取（单向），避免现场回写制品仓。
- **更新**：`Repo.plan(lock)` 比对 lock 与本机已装版本，得到 `install / ok / switch`；`Repo.apply(plan)` 执行。回滚 = 把 lock 里的版本改回旧值再跑一次 update，不动其它包。
- **管理颗粒度**：最小单元是「包 + 版本」，不是整仓也不是文件级；生效以台架为单位（一台一份 lock）。

```bash
python ci/pack.py --root . --out dist/repo --channel stable     # CI：出制品 + 索引
python -m kit.repo --help                                       # 说明见模块头

# 台架侧（示意）
python -c "from kit.repo import Repo; r=Repo('dist/repo'); p=r.plan('benches/BENCH-DEMO/bench.lock.json','dist/site'); print(p); r.apply(p, dest='dist/site')"
```

## 仿真：只在能力层

`mode="simulate"` 时驱动**不建会话、不下发命令**，直接委托 `CapabilitySimulator`：

- 输入是契约参数（时基、垂直档位、触发），不是命令字符串；
- 输出是契约结构（`Waveform` / 测量值 / 身份），不经过任何解析；
- 不接管连接、寻址与链路，资源名只用于身份串。

它验证「能力语义是否正确」，不验证「命令拼写是否正确」；不模拟噪声带宽与探头衰减，不得替代真机验收（`verified_on_hardware` 在真机核对前保持 `false`）。

## 常用命令

```bash
# 契约一致性（无硬件）
python -m kit.conformance --root . --package rohde-schwarz-mxo --mode simulate

# 生成 / 刷新某台测试台的 lock
python tools/lock_bench.py --root . --station station-types/scope-bench.json --bench BENCH-DEMO --runner 1.7.3

# 端到端演示：解析 → 会话 → 配置 → 采集 → 测量
python tools/demo_acquire.py --mode simulate

# 出制品与校验
python ci/pack.py --root . --out dist/repo
python ci/pack.py --root . --out dist/repo --verify
python ci/pack.py --root . --out dist/repo --boundaries

# 流程测试
python -m unittest discover -s tests -v
```

## 硬规则

1. **厂商库只能出现在 `packages/*/pkg/**`**，`contracts/` `kit/` `runtime/` 内出现即视为越界（CI 检查）。
2. **用例只认能力与别名**，不出现包名、版本、型号、资源名。
3. **一台机器一个进程只加载一份解析结果**；并发多台架走多进程。同一能力被要求两个版本时抛 `VersionConflict`。
4. **不静默降级**：`mode="hardware"` 下缺库或连不上必须报错；只有台架装备属性显式声明 `simulate` 才启用仿真。
5. **现场不编译、不联网装包**：制品是「解压即用」的目录，`vendor/` 随包；生效只做「拉取 + 改 lock + 重启服务」。
6. **`tier` 必填**：`baseline` / `shared` / `station`，决定发布节奏与回滚粒度，不是技术栈分类。

## 与既有 `sdk/ate_drivers` 的关系

本仓库是 P1 阶段的新建目标结构，先以单包垂直切片验证契约、解析、运行时与出制品链路。既有 `sdk/ate_drivers`（transport / families / vendors）保持不动，待共享包试点通过后按其能力逐项迁移；迁移顺序见 `docs/设备驱动` 中的路标页。
