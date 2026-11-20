# TPS 与元数据归口 Controller

日期：2026-11-20　范围：ATE Runner / ATE Controller

## 一、职责划分

| 事项 | 归属 | 说明 |
| --- | --- | --- |
| TPS 编辑、导入、校验 | Controller | Controller Web 操作，Runner 只读 |
| 装备树、测试台 BOM、预设类型 | Controller | `/api/metadata/*` |
| 机器常数模型库（版本 / 模型 / 标定值） | Controller | `/api/constants/*`，数据落 `repo/controller.db` |
| TPS 执行、部署、结果与报告 | Runner | 拉取 TPS 后在本机物化运行副本 |
| 软件包归档与下载 | Controller | `/api/packages/*` |

## 二、Runner → Controller 协议

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/health` | 健康检查与统计 |
| GET | `/api/v1/capabilities` | 能力协商 |
| GET | `/api/v1/tps` | TPS 摘要列表 |
| GET | `/api/v1/tps/{id}` | TPS 全文 |
| GET | `/api/v1/tps/{id}/files` | 附带文件清单 |
| GET | `/api/v1/tps/{id}/files/{path}` | 附带文件内容 |
| PUT | `/api/v1/tps/{id}` | 保存（Controller Web 使用） |
| POST | `/api/v1/tps/import`、`/import/zip` | 导入（Controller Web 使用） |

Runner 侧对应接口：

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/tps` | Controller 优先 → 缓存 → 本地 testresource，`source` 标记来源 |
| GET | `/api/tps/{id}` | 同上；首次读取时把附带文件物化为本地运行副本 |
| GET | `/api/tps-source` | 返回 Controller 地址、缓存条目数与拉取时间 |
| POST | `/api/tps/{id}/sync` | 强制重新同步附带文件 |
| POST | `/api/tps/import`、`/import/zip` | 返回 `moved_to_controller`，指向 Controller 控制台 |

## 三、配置

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MOCK_CONTROLLER_URL` | `http://127.0.0.1:8070` | Controller 地址（旧名 `ATE_CONTROLLER_URL` 兼容） |
| `ATE_TPS_SOURCE` | `controller` | `controller` 走协议，`local` 仅用本地目录（验收回退） |
| `ATE_TPS_CACHE_DIR` | `backend/tps_cache` | 缓存目录，不入库 |
| `ATE_TPS_TIMEOUT` | `8` | 单次请求超时（秒） |
| `MOCK_CONTROLLER_REPO` | `<程序目录>\repo` | Controller 数据根（包、TPS、树、库） |

## 四、离线与缓存语义

1. 列表：Controller 成功 → 写缓存并返回 `source=controller`；失败 → 用缓存返回 `source=cache` 并附 `remote_error`；无缓存 → 回退本地 `testresource`；
2. 详情：Controller 成功 → 写单条缓存；失败 → 读缓存；再失败 → 本地目录；
3. 运行副本：首次读取该 TPS 时把 `files` 全量下载到 `testresource/<id>/`（先写 `.sync` 临时目录再整体替换），执行链路不变；
4. 断网可继续执行已缓存 TPS，恢复连线后下次拉取覆盖缓存。

## 五、验证记录

| 场景 | 结果 |
| --- | --- |
| Controller `/api/v1/tps/import` 导入含 deploy 与 constant_model 的 TPS | 200，列表 `has_constants=true`、`deploy_count=1` |
| 缺 `testconfig` 的清单 | 400，返回全部校验错误 |
| zip 导入含 `testcase/`、`resource/` | 200，文件清单与内容落盘正确 |
| 常数库导入 | 200，`/api/constants/versions` 返回新版本 |
| Controller 装备树 | 2 产品 / 3 子系统 / 7 测试台类型 / 49 BOM 器件 |
| Runner `/api/tps`（Controller 在线） | `source=controller`，2 条 |
| Runner `/api/tps`（Controller 停机） | `source=cache`，2 条，附 `remote_error` |
| Runner `/api/tps/{id}/sync` | `synced=1`，`testresource/<id>/tps.json` 生成 |
| Runner `/api/metadata/tree` | 404（已下线） |
| Controller `/ui/` | 200，Vue3 构建产物可访问 |

## 六、回退

把 `ATE_TPS_SOURCE` 设为 `local` 即恢复「只用本地 testresource」的旧行为，无需回退代码。

## 七、常数库同步（Runner 侧运行期副本）

事实源在 Controller，Runner 保留本地 `ate.db` 作为运行期副本：

| 环节 | 行为 |
| --- | --- |
| 导出 | Controller `GET /api/v1/constants/export?version=` 还原本版本的 groups / models / fields 与标定值 |
| 导入 | Runner `constants_sync.sync()` 调 `import_bundle(mode=overwrite, keep_values=True)` |
| 启动 | `ATE_CONSTANTS_SYNC=auto` 时后台线程同步一次，版本未变则跳过 |
| 手动 | `GET /api/constants/sync-status`、`POST /api/constants/sync?force=true` |
| 离线 | Controller 不可达时用 `tps_cache/constants_bundle.json` 恢复 |
| 写操作 | 导入 / 切换版本 / 写标定值 / TPS 绑定均返回 `moved_to_controller`，Runner 只读 |

实测：启动同步得到 `local_version=V-CTL-1 source=controller`；强制同步 `imported=true`；Controller 停机后强制同步 `source=cache`；写接口返回 `moved_to_controller`。

## 八、待办

1. 双跑比对脚本（同一 TPS 在 Controller 与 Runner 侧的摘要一致性）；
2. Controller 鉴权（当前内网明文，无 Token）；
3. Runner 侧 `machine_constants` 的编辑代码可清理（现已全部短路为只读提示）；
4. 常数版本生效切换目前由 Controller 决定，Runner 同步时整体替换为导出包中的生效版本。
