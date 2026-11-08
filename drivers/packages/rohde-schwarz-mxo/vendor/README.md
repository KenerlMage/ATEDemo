# vendor/ —— 包私有第三方依赖

本目录存放本包私有的第三方库，随 zip 一起分发，现场无需联网安装。

## 当前依赖

| 库 | 版本 | 用途 | 许可 |
| --- | --- | --- | --- |
| RsInstrument | 1.12.1 | R&S 仪器 VISA 会话与 SCPI 传输 | MIT |

## 放置方式

出包机执行（离线介质制备，不在现场执行）：

```
python -m pip download RsInstrument==1.12.1 -d packages/rohde-schwarz-mxo/vendor --no-deps
```

运行时由包的加载器把 `vendor/` 加入 `sys.path`（在 `pkg/rs_mxo/driver.py` 的延迟 import 之前）。
未放置 vendor 目录时，`mode="hardware"` 会抛 `DriverUnavailable` 并提示补充依赖；`mode="simulate"` 不受影响。

## 约束

- 公共运行时依赖（numpy 等）不属于本目录，由平台基线提供，通过 manifest 的 `runtime` 字段声明版本范围。
- 本目录内容计入包目录哈希，属于 lock 完整性校验范围；升级依赖必须升包版本。
