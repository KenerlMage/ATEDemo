# ATE Runner 版本升级功能设计文档

| 项 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 状态 | 待实现（方案 A，代码尚未落盘） |
| 适用组件 | ATE Runner（backend + frontend + packaging/launcher） |
| 关联资产 | `packaging/installer/ate_runner.iss`、`packaging/launcher/ate_launcher.py`、`backend/main.py`、`frontend/src/App.vue` |

---

## 1. 背景与目标

ATE Runner 以安装包（Inno Setup，默认安装至 `C:\ATERunner`）形式部署于产线工控机，程序与数据同处安装目录。当前缺少版本升级通道，现场版本更新依赖人工到机替换安装包，存在以下问题：升级动作无版本记录、无法通知现场存在新版本、升级时机不受控、升级过程可能覆盖测试数据。

本设计定义一套"发布—推送—通知—升级"闭环，目标如下：

1. 远端可发布新版本元数据与安装包，Runner 可获取新版本通知。
2. 用户在界面获知新版本信息，并按需触发升级。
3. 升级动作与测试执行严格互斥，升级过程不得发生在测试用例执行状态下。
4. 升级过程完整继承本地已有数据（测试记录、运行目录、TPS 资源、授权文件）。
5. 升级前后均向用户展示该版本的 release note。
6. 在本机以"预置安装包"模拟远端下载，验证全链路可行。

---

## 2. 术语

| 术语 | 含义 |
| --- | --- |
| 频道（channel） | 版本发布通道，如 `stable` / `beta` / `internal` |
| 频道清单 | 描述可用版本及其安装包的元数据文件（`<channel>.json`） |
| 安装包 | 版本交付物，本设计采用 zip 归档（内含 `payload.json` 清单） |
| 活动槽 | 当前正在运行的代码目录 |
| 非活动槽 | 用于写入新版本的备用目录 |
| 指针 | 记录活动槽的少量数据（`current.txt` 或等价配置项） |
| 切换 | 将指针由活动槽指向非活动槽的动作 |
| 待替换清单 | 记录"解压产物 → 目标目录"映射的文件（`pending_swap.json`） |
| 数据区 | 升级过程中不参与替换的本地数据目录集合 |

---

## 3. 总体架构

系统分为三层：

| 层 | 组件 | 职责 |
| --- | --- | --- |
| 发布端 | 远端服务器目录（模拟实现为本机目录） | 提供频道清单与安装包 |
| Runner 服务端 | `backend/runner_info.py`、`backend/updater.py`、`backend/main.py` 升级接口组 | 版本判定、下载、校验、暂存、切换登记、状态机维护 |
| Runner 界面 | `frontend/src/components/UpdateBadge.vue`（挂载于 `App.vue` 侧栏页脚） | 新版本通知、升级交互、release note 展示、进度与错误反馈 |
| 事务执行者 | `packaging/launcher/ate_launcher.py` | 在下次启动时依据待替换清单完成目录切换与健康检查 |

设计原则：运行中的进程无法替换自身正在使用的文件，故**界面与服务端只负责"准备"，真正的目录切换由 launcher 在进程外完成**。

---

## 4. 目录与路径规范

安装根目录记为 `<RUNNER_HOME>`（安装形态为 `C:\ATERunner`，开发形态为仓库根 `D:\ATE`）。

| 路径 | 用途 | 升级时是否保留 |
| --- | --- | --- |
| `<RUNNER_HOME>/launcher/` | 启动器，唯一入口 | 保留 |
| `<RUNNER_HOME>/slots/current.txt` | 活动槽指针 | 保留 |
| `<RUNNER_HOME>/slots/A/`、`slots/B/` | 版本槽（`app/`、`web/`） | 保留（非活动槽可清理） |
| `<RUNNER_HOME>/runtime/` | 嵌入式 Python 运行时 | 保留（不进槽） |
| `<RUNNER_HOME>/updates/download/` | **安装包固定下载路径** | 保留 |
| `<RUNNER_HOME>/updates/staging/<版本>/` | 安装包解压暂存 | 可清理 |
| `<RUNNER_HOME>/updates/backup/<旧版本>/` | 升级前备份登记 | 保留 N-1 份 |
| `<RUNNER_HOME>/updates/state.json` | 升级状态机 | 保留 |
| `<RUNNER_HOME>/updates/installed_version.json` | 当前已安装版本 | 保留 |
| `<RUNNER_HOME>/updates/pending_swap.json` | 待替换清单 | 保留至切换完成 |
| `<RUNNER_HOME>/updates/update.log` | 升级审计日志 | 保留 |
| `<RUNNER_HOME>/logs/` | 测试与运行日志 | **保留（数据区）** |
| `<RUNNER_HOME>/workspace/` | TPS 运行目录 | **保留（数据区）** |
| `<RUNNER_HOME>/testresource/` | TPS 资源 | **保留（数据区）** |
| `<RUNNER_HOME>/ate.db` | 历史记录库 | **保留（数据区）** |
| `<RUNNER_HOME>/cloud_demo.db` | 云端上报演示库 | **保留（数据区）** |
| `<RUNNER_HOME>/license.dat` | 授权文件 | **保留（数据区）** |

---

## 5. 数据模型

### 5.1 频道清单 `<channel>.json`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `channel` | string | 频道名 |
| `server` | string | 发布端标识（便于问题定位） |
| `updated_at` | string | 清单生成时间（ISO 8601） |
| `min_supported` | string | 最低支持版本，低于该版本强制升级 |
| `force` | boolean | 是否全量强制升级 |
| `rollout` | object | 灰度配置，含 `percent`、`groups` |
| `releases` | array | 版本条目列表 |

`releases[]` 条目字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `version` | string | 版本号（数字点分，如 `1.1.260113`） |
| `title` | string | 版本标题 |
| `released_at` | string | 发布日期 |
| `kind` | string | 版本类型：`feature` / `fix` / `security` |
| `highlights` | array | 要点列表，界面以条目呈现 |
| `release_note` | string | 完整说明（Markdown） |
| `package.name` | string | 安装包文件名 |
| `package.size` | integer | 字节数 |
| `package.sha256` | string | 校验值 |
| `package.url` | string | 相对发布端的路径 |
| `package.signature` | string | 清单签名（P0 增补） |

### 5.2 升级状态 `state.json`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `phase` | string | 状态机当前状态 |
| `update_available` | boolean | 是否存在可升级版本 |
| `force` | boolean | 是否强制升级 |
| `latest` | object | 最新版本条目 |
| `downloaded` | object | 已下载安装包信息（版本、路径、大小、sha256） |
| `installed` | object | 最近一次升级记录 |
| `restart_required` | boolean | 是否待重启生效 |
| `note_after` | string | 升级后待展示 release note 的版本 |
| `note_ack` | object | release note 已读标记（版本 → 时间） |
| `history` | array | 升级历史记录 |
| `error` | string | 最近一次错误信息 |

### 5.3 版本文件 `installed_version.json`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `version` | string | 当前已安装版本 |
| `previous` | string | 上一版本 |
| `package` | string | 来源安装包文件名 |
| `mode` | string | `swap`（就地替换）或 `pending`（待切换） |
| `updated_at` | string | 写入时间 |

---

## 6. 状态机

| 状态 | 进入条件 | 允许动作 | 出口 |
| --- | --- | --- | --- |
| `idle` | 初始状态、检查后无新版本 | 检查更新 | → `update_available` |
| `checking` | 发起检查 | — | → `update_available` / `idle` |
| `update_available` | 存在更高版本 | 展示通知、查看 release note、下载 | → `downloading` |
| `downloading` | 发起下载 | 查看进度 | → `downloaded` / `update_available`（失败） |
| `downloaded` | 安装包落盘且校验通过 | 查看 release note、执行升级 | → `applying` |
| `applying` | 开始解压与登记 | — | → `installed` |
| `installed` | 切换登记完成 | 重启生效、回滚、查看 release note | → `idle`（确认后） |
| 异常分支 | 任意阶段失败 | 重试、查看错误 | 维持前一可用状态 |

互斥规则：`checking` / `downloading` / `applying` 期间拒绝新的检查请求；`applying` 期间拒绝执行升级。

---

## 7. 接口定义

| 编号 | 方法 | 路径 | 入参 | 出参要点 |
| --- | --- | --- | --- | --- |
| API-01 | GET | `/api/runner/version` | — | 当前版本、安装根目录、升级目录、发布端地址 |
| API-02 | GET | `/api/updates/check` | `force`（可选） | 当前版本、是否有新版本、是否强制、最新版本条目、占用任务列表 |
| API-03 | GET | `/api/updates/status` | — | 状态机快照、已下载信息、能否升级、待展示 release note |
| API-04 | GET | `/api/updates/release-note/{version}` | 版本号 | 标题、发布日期、要点列表、完整说明 |
| API-05 | POST | `/api/updates/download` | — | 安装包路径、大小、sha256 |
| API-06 | POST | `/api/updates/apply` | — | 升级记录（含数据保留校验结论） |
| API-07 | POST | `/api/updates/ack-note` | `version` | 更新后的状态快照 |

统一响应约定：成功为 `{"success": true, ...}`，失败为 `{"success": false, "code": "<错误码>", "message": "<说明>"}`。

错误码：

| 错误码 | 触发条件 | 界面处置 |
| --- | --- | --- |
| `REMOTE_UNREACHABLE` | 发布端不可达 | 提示网络异常，可重试 |
| `SOURCE_MISSING` | 频道清单或安装包缺失 | 提示发布端配置异常 |
| `BAD_CHANNEL` | 频道清单格式错误 | 提示发布端数据异常 |
| `NO_RELEASE` | 无可用版本 | 提示已是最新 |
| `CHECKSUM_MISMATCH` | 校验值不一致 | 提示安装包异常，禁止升级 |
| `BAD_PACKAGE` | 安装包结构不合法 | 提示安装包损坏 |
| `NOT_DOWNLOADED` | 未下载即执行升级 | 引导先下载 |
| `BUSY` | 存在执行中的测试任务 | 提示占用任务，禁用升级按钮 |
| `NO_BACKUP` | 无升级记录时请求回滚 | 提示无可回滚版本 |

---

## 8. 待实现功能点清单

编号规则：FR 为功能需求，NFR 为非功能需求。优先级：P0 必做，P1 建议，P2 可延后。

### 8.1 发布与通知

| 编号 | 名称 | 描述 | 验收标准 | 优先级 |
| --- | --- | --- | --- | --- |
| FR-01 | 版本信息暴露 | 提供当前版本、安装根目录、升级目录、发布端地址的查询能力 | `GET /api/runner/version` 返回全部字段；版本号来源为 `installed_version.json`，缺省回退至编译期常量 | P0 |
| FR-02 | 频道清单拉取 | 从发布端获取 `<channel>.json`，支持本机目录与 HTTP(S) 两种来源 | 两种来源均可解析；发布端不可达时返回 `REMOTE_UNREACHABLE` 且不影响既有状态 | P0 |
| FR-03 | 版本比较与判定 | 按数字点分规则比较版本，判定是否存在更高版本 | 版本 `1.1.260113` 判定高于 `1.0.250913`；无更高版本时 `update_available=false` | P0 |
| FR-04 | 新版本通知 | 界面在固定位置展示新版本提示，包含版本号与要点摘要 | 有新版时展示醒目徽标；点击进入升级流程；无新版时展示当前版本号且不展示徽标 | P0 |
| FR-05 | 强制升级策略 | 支持 `force` 与 `min_supported` 两种强制条件 | 低于 `min_supported` 或 `force=true` 时，界面不提供"稍后"入口 | P1 |
| FR-19 | 灰度与频道 | 支持多频道与按分组/百分比灰度 | 分组不在范围内的机器不展示该版本提示 | P2 |

### 8.2 下载与校验

| 编号 | 名称 | 描述 | 验收标准 | 优先级 |
| --- | --- | --- | --- | --- |
| FR-06 | 安装包下载 | 将安装包下载至固定路径 `<RUNNER_HOME>/updates/download/` | 下载完成后文件存在于固定路径；文件名与频道清单一致 | P0 |
| FR-07 | 完整性校验 | 对安装包计算 sha256 并与清单比对 | 不一致时返回 `CHECKSUM_MISMATCH`，不产生"已下载"状态 | P0 |
| FR-18 | 离线包导入 | 允许将安装包人工放入固定路径，视为已下载 | 放入后 `GET /api/updates/status` 可识别并允许升级 | P1 |

### 8.3 时序与互斥

| 编号 | 名称 | 描述 | 验收标准 | 优先级 |
| --- | --- | --- | --- | --- |
| FR-08 | 执行状态互斥 | 测试用例执行状态下禁止升级 | 存在 `running`/`waiting` 任务时，`POST /api/updates/apply` 返回 `BUSY` 并列出占用任务；界面禁用升级按钮并说明原因 | P0 |
| FR-09 | 反向互斥 | 升级流程进行中禁止启动新的测试执行 | 处于下载或升级阶段时，启动接口被拒绝并给出提示 | P0 |
| FR-20 | 升级排队 | 允许用户登记"空闲后自动升级" | 任务结束后自动进入升级流程，无需用户再次操作 | P1 |
| FR-21 | 环境前置检查 | 升级前检查磁盘余量、目录写权限、残留子进程 | 任一不满足时阻断升级并给出具体原因 | P1 |

### 8.4 落地与切换

| 编号 | 名称 | 描述 | 验收标准 | 优先级 |
| --- | --- | --- | --- | --- |
| FR-10 | 非活动槽写入 | 将安装包解压至非活动槽，活动槽不受影响 | 写入期间活动槽文件指纹不变 | P0 |
| FR-11 | 数据继承 | 升级仅替换代码目录，数据区完整保留 | 升级前后数据区指纹（文件数、字节数、修改时间）完全一致；结论随升级记录落盘 | P0 |
| FR-12 | 原子切换指针 | 由 launcher 在启动时切换活动槽指针 | 指针写入为单文件原子操作；切换失败时保持原活动槽 | P0 |
| FR-13 | 健康检查与确认 | 新槽启动后执行健康检查与仪器自检 | 通过则写入确认标记并清理待替换清单；不通过则不确认 | P0 |
| FR-22 | A/B 双槽目录结构 | 采用 `slots/A`、`slots/B` 双槽布局与 `current.txt` 指针 | 两槽可交替升级，旧槽保留至下次升级 | P1 |

### 8.5 回滚与容错

| 编号 | 名称 | 描述 | 验收标准 | 优先级 |
| --- | --- | --- | --- | --- |
| FR-14 | 自动回滚 | 新槽连续启动失败达阈值时自动切回旧槽 | 连续两次失败后指针回到旧槽，服务可正常启动；过程中的尝试次数落盘 | P0 |
| FR-16 | 手动回滚 | 提供回滚入口，恢复至上一版本 | 回滚后版本号恢复，耗时在秒级，无需重新下载 | P1 |
| FR-23 | 数据降级兼容 | 版本回滚后旧版本可读取既存数据 | 数据库结构迁移仅做新增字段；迁移前生成快照 | P0 |

### 8.6 说明展示与审计

| 编号 | 名称 | 描述 | 验收标准 | 优先级 |
| --- | --- | --- | --- | --- |
| FR-25 | 前置 release note | 升级前展示该版本 release note | 用户点击升级后先展示说明，确认后方可继续 | P0 |
| FR-15 | 后置 release note | 升级完成后再次展示该版本 release note | 重启后首次进入界面展示说明，确认后写入已读标记，不再重复展示 | P0 |
| FR-17 | 升级审计日志 | 记录检查、下载、校验、升级、回滚各阶段 | `updates/update.log` 含时间戳与结果；界面可见最近记录 | P0 |
| FR-24 | 版本可追溯 | 将 Runner 版本写入测试记录与报告 | 报告头部包含执行时的 Runner 版本 | P1 |

### 8.7 非功能需求

| 编号 | 名称 | 描述 | 验收标准 | 优先级 |
| --- | --- | --- | --- | --- |
| NFR-01 | 清单签名 | 频道清单采用非对称签名，客户端内置公钥验证 | 签名不合法时拒绝使用该清单 | P0 |
| NFR-02 | 防降级 | 记录并校验最低允许版本 | 低于最低允许版本的安装包被拒绝 | P1 |
| NFR-03 | 阶段可观测 | 每个阶段有明确状态与错误码 | 失败可定位至具体阶段与原因 | P0 |
| NFR-04 | 升级耗时可预期 | 下载与解压提供进度 | 界面可见阶段与进度，非"无响应" | P1 |
| NFR-05 | 断电安全 | 任意阶段断电后系统可正常启动 | 见 FR-12、FR-14 的验收项 | P0 |

---

## 9. 关键约束

1. **升级动作不得在测试用例执行状态下发起或执行**（FR-08）；判定依据为运行中的任务集合，包含普通用例任务与 TPS 任务，状态取值覆盖 `running`、`waiting`。
2. **数据区必须位于版本槽之外**（FR-11）；若实现采用双槽布局，数据目录不得随槽切换而变化。
3. **快捷方式、开机自启动项、防火墙规则一律指向 launcher**，不得指向槽内可执行文件。
4. **升级动作与重启动作分离**：升级阶段仅完成"准备与登记"，实际生效发生在重启。
5. **运行中的进程不得执行目录替换**：替换动作由 launcher 在进程外完成。
6. 本设计以模拟方式实现远端：发布端为本机目录，安装包为预置 zip；切换为 `ATE_UPDATE_SERVER` 环境变量后即可对接真实服务器。

---

## 10. 异常与回滚设计

| 异常 | 处置 |
| --- | --- |
| 发布端不可达 | 保持当前版本，记录错误，界面提供重试 |
| 校验失败 | 丢弃安装包，回到 `update_available` |
| 升级过程中断电 | 指针未切换，重启后仍进入旧槽；解压残留由启动清理 |
| 新槽启动失败 | 尝试次数 +1；达到阈值后指针回切旧槽 |
| 新槽健康检查不通过 | 标记未确认，按 FR-14 回滚 |
| 数据区指纹不一致 | 记录异常并终止升级流程，保留现场 |

回滚耗时目标：指针回切 + 重启，不包含下载与解压。

---

## 11. 界面设计要点

| 位置 | 内容 |
| --- | --- |
| 侧栏页脚（现有版本位） | 当前版本号；有新版时叠加提示徽标（版本号 + "发现新版本"） |
| 升级对话框 · 第一步 | 版本对比（当前 → 新版本）、安装包大小、要点列表、完整 release note |
| 升级对话框 · 第二步 | 前置检查结果；若存在执行中任务，展示占用任务清单并禁用"升级"按钮 |
| 升级对话框 · 第三步 | 下载与解压进度、阶段名称 |
| 升级对话框 · 第四步 | 完成信息、是否需重启、release note 回顾、回滚入口 |
| 失败提示 | 错误码对应的可读说明与重试入口 |

---

## 12. 实现计划

| 阶段 | 交付物 | 前置条件 |
| --- | --- | --- |
| 阶段一 | 版本常量与路径定义、升级服务模块、升级接口组 | 无 |
| 阶段二 | 界面组件与通知挂载 | 阶段一接口可用 |
| 阶段三 | launcher 切换与健康检查、A/B 双槽目录结构 | 阶段一 |
| 阶段四 | 自测脚本与模拟发布端（预置安装包） | 阶段一 |
| 阶段五 | 清单签名、灰度、离线导入 | 阶段一至四 |

涉及文件（新增）：`backend/runner_info.py`、`backend/updater.py`、`backend/tools/updater_selftest.py`、`frontend/src/components/UpdateBadge.vue`、`tools/make_update_package.py`、`tools/update-server/channel.json`。
涉及文件（修改）：`backend/main.py`（新增接口组，置于 SPA 兜底路由之前）、`frontend/src/App.vue`（侧栏页脚挂载组件）、`packaging/launcher/ate_launcher.py`（切换与健康检查）、`.gitignore`（忽略 `updates/` 运行目录）。

---

## 13. 待确认事项

| 编号 | 事项 | 说明 |
| --- | --- | --- |
| Q-01 | 槽结构采用目录级还是文件级 | 目录级实现简单；文件级可减少重复运行时占用 |
| Q-02 | 嵌入式运行时是否入槽 | 入槽保证一致性，代价为磁盘翻倍 |
| Q-03 | 强制升级的适用范围 | 是否仅限安全类版本 |
| Q-04 | 灰度分组依据 | 测试台编号、产线分组或授权号 |
| Q-05 | 签名算法与密钥管理 | 复用授权体系或独立密钥对 |

---

## 14. 附录：模拟实现说明

1. 发布端：`tools/update-server/` 目录，内含 `stable.json` 与 `packages/`。
2. 安装包：由 `tools/make_update_package.py` 生成 zip，内含 `app/`、`web/`、`payload.json`、`VERSION.txt`、`release-note.md`。
3. 下载：实现为文件复制至 `<RUNNER_HOME>/updates/download/`，仍执行 sha256 校验。
4. 升级：默认登记待替换清单并切换版本号，不修改当前工作副本；置 `ATE_UPDATE_ALLOW_SWAP=1` 后就地替换代码目录。
5. 自测：`backend/tools/updater_selftest.py` 覆盖检查、下载、校验、数据继承、升级、回滚全链路，并输出每步证据。
