# 需求设计（统一入口）

本目录是 ATE 项目**所有需求设计文档的统一查阅入口**，位于 MockRunner 仓库。

```
docs/需求设计/
├── controller/    # Controller（配置与归档中心）的需求设计
└── runner/        # Runner（执行端）的需求设计
```

## 目录约定

| 目录 | 内容 | 说明 |
| --- | --- | --- |
| `controller/` | 机器常数功能模块、机器常数实现契约、机器常数实现说明、软件部署功能（含 Python 三方依赖供给 SR03/SR04） | 需求规范**原件**，同时备份在 `D:\ATE\MockController\docs\需求设计\` |
| `runner/` | 机器常数-Runner侧需求（SR02）、软件部署-Runner侧需求（SR02） | 从 controller 文档抽取的 Runner 章节，附 Runner 实现落点与未覆盖项 |

## 为什么这样分

- 一份需求若同时涉及两个系统，**规范原件放 controller**（Controller 是配置与归档的事实源），Runner 目录放该系统的 SR 抽取与实现落点，避免同一段文字维护两份。
- Runner 目录的文档头部都会标注来源文件，便于回溯。
- Controller 工程内保留同名副本作为备份；**修改时以 controller 原件为准**，改完同步到 Controller 备份，再重新抽取 Runner 侧。

## 索引

| 主题 | Controller 规范 | Runner 侧 |
| --- | --- | --- |
| 机器常数 | [机器常数功能模块](controller/机器常数功能模块.md)、[实现契约](controller/机器常数实现契约.md)、[实现说明](controller/机器常数实现说明.md) | [机器常数-Runner侧需求](runner/机器常数-Runner侧需求.md) |
| 软件部署 | [软件部署功能](controller/软件部署功能.md)（含 3.7 Python 依赖形态方案对比） | [软件部署-Runner侧需求](runner/软件部署-Runner侧需求.md) |

## 格式

按《需求设计》skill（`requirement-design`）：SR / AR 两级，每个需求只写三个字段。

```
【需求价值】≤50 字
【设计目标】≤100 字
【需求描述】≤200 字
```

校验命令（skill 自带脚本）：

```powershell
python <skill>/scripts/check_requirement.py docs/需求设计/controller/机器常数功能模块.md
```

## 同步规则

1. 先改 `controller/` 下的原件。
2. 复制到 `D:\ATE\MockController\docs\需求设计\`（同名覆盖）。
3. 若 SR/AR 有增删，重新生成 `runner/` 下对应抽取文档的「需求清单」与「AR 覆盖核对」两节。
4. 三个副本都跑一遍校验脚本，0 error 再提交。
