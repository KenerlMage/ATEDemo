# ate-drivers · ATE 驱动仓库

按「能力契约 / 驱动包 / 解析器 / 运行时」四层组织的驱动仓库。三类发布物：基础包、共享包、独立包（独立包不入本仓）。

首个实现：`packages/rohde-schwarz-mxo`（基于 PyPI `RsInstrument` 的 R&S MXO 示波器驱动包）。

## 目录

```
contracts/          能力契约（L1）：TPS 唯一依赖的接口，禁止 import 厂商库
kit/                注册表、解析器、契约一致性套件（不含业务与硬件）
runtime/            运行时：会话、资源仲裁、超时、诊断
packages/           驱动包（共享包），一包一目录，独立版本
  rohde-schwarz-mxo/
    manifest.json   身份 / 契约 / 兼容范围 / 入口 / 签名信息
    pkg/rs_mxo/     驱动实现 + SCPI 命令表 + 仿真 IO
    vendor/         包私有第三方依赖（离线随包分发）
    conformance/    随包发布的契约测试
station-types/      台型清单：声明需要哪些能力与版本范围
benches/<BENCH>/    devices.json（设备档案投影）+ bench.lock.json（锁定解析结果）
ci/pack.py          出包：校验 → zip → sha256 → 私有索引；含边界检查
tools/              lock_bench.py（生成 lock）、demo_acquire.py（端到端演示）
tests/              端到端流程测试
```

## 常用命令

```bash
# 契约一致性（无硬件）
python -m kit.conformance --root . --package rohde-schwarz-mxo --mode simulate

# 生成 / 刷新某台测试台的 lock
python tools/lock_bench.py --root . --station station-types/scope-bench.json --bench BENCH-DEMO --runner 1.7.3

# 端到端演示：解析 → 会话 → 配置 → 采集 → 测量
python tools/demo_acquire.py --mode simulate

# 出包与校验
python ci/pack.py --root .
python ci/pack.py --root . --verify
python ci/pack.py --root . --boundaries

# 流程测试
python -m unittest discover -s tests -v
```

## 硬规则

1. **厂商库只能出现在 `packages/*/pkg/**`**，`contracts/` `kit/` `runtime/` 内出现即视为越界（CI 检查）。
2. **用例只认能力与别名**，不出现包名、版本、型号、资源名。
3. **一台机器一个进程只加载一份解析结果**；并发多台架走多进程。同一能力被要求两个版本时抛 `VersionConflict`。
4. **不静默降级**：`mode="hardware"` 下缺库或连不上必须报错；只有台型/设备档案显式声明 `simulate` 才启用仿真。
5. **现场不编译、不联网、不装 pip**：包以 zip 分发，自带 `vendor/`，安装只做「放文件 + 改 lock + 重启服务」。

## 与既有 `sdk/ate_drivers` 的关系

本仓库是 P1 阶段的新建目标结构，先以单包垂直切片验证契约、解析、运行时与出包链路。既有 `sdk/ate_drivers`（transport / families / vendors）保持不动，待共享包试点通过后按其能力逐项迁移；迁移顺序见 `docs/设备驱动` 中的路标页。
