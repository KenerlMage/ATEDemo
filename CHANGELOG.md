# 变更日志

本文件记录 ATE Runner 的对外可见变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号与打包脚本默认取值一致（`1.0.yyMMdd`，见 `packaging/build_package.ps1`），按时间倒序排列。

## [1.0.250921] — 2026-09-21

### 新增

- **元数据管理页面**（左侧导航新增一级入口，下挂两个子页面）：
  - **装备树管理**（`/metadata/tree`）：维护装备树三级节点 **产品 → 子系统 → 测试台类型**；产品/子系统可新增、改名、删除（子系统支持多产品归属），新增类型可填属性（名称 / 类别 / 典型 DUT / 推荐节拍 / 描述）并可选「从同子系统内 BOM 最全的类型复制 BOM」；页头提供存储位置信息与「从预设文件补齐」「全量重建」两个回退动作。
  - **测试台BOM管理**（`/metadata/bom`）：产品→子系统→类型三级级联选定后，以行内编辑表格生成/维护该类型的标准 BOM（设备名称·型号·厂商·类别·用途、接口与协议、程控标记、默认连接参数、必需与备注），支持增行 / 复制 / 上下移 / 移除 / 删库与「从设备模板库添加」（按类别 + 型号 + 厂商去重）；测试台属性与 BOM 一并保存；页面提供「去新建测试台」直达入口。
- **后端模块 `backend/metadata_registry.py`**：17 个 `/api/metadata/*` 接口（overview / tree / store / reseed / products / subsystems / presets / presets/{id} / presets/{id}/bom / bom/items / device-catalog），含节点编号与 BOM 条目校验、删除保护、原子写与 RLock 串行化。
- **数据目录 `backend/tree/`**：`tree.json`（三级节点与类型属性）+ `bom/<类型编号>.json`（标准 BOM）+ `README.md`（目录说明与手工编辑规则）；可用环境变量 `ATE_TREE_DIR` 覆盖位置。
- **前端**：`MetadataTreeView.vue`、`MetadataBomView.vue` 两个视图；`api.js` 新增 15 个接口封装；路由 `/metadata` → `/metadata/tree`、`/metadata/bom`；`App.vue` 导航支持父项 `children` 子入口与移动端适配样式。
- **文档**：`docs/metadata-management.html` / `.md`（页面结构、数据流、存储格式、接口清单、校验规则、实机验证记录、使用步骤与已知边界）；`backend/tree/README.md`；`docs/packaging-alternatives.html`（PyInstaller 替代方案选型：三条迁移路线 + 8 个打包器对比 + 按现场约束的选型表）。

### 变更

- `backend/testbench_registry.py`：`_load_presets()` / `_preset_tree()` 改为**优先读取元数据模块**，读取失败才回退原预设文件，因此「新建测试台」注册向导与测试台导航无需改动即使用最新 BOM 与属性。
- `testresource/testbench_presets.json`：由「只读预设」降级为**种子 + 回退源**（仅在 `backend/tree/` 首次建立时导入，之后不再写入）。
- 既有资产纳管：7 个预置测试台类型属性与 49 台设备 BOM（其中 43 台可编程）在首启时自动导入 `backend/tree/`。
- `README.md`：功能表补充元数据管理页、接口与设计要点三行；接口清单新增 7 行 `/api/metadata/*`；目录结构补充 `metadata_registry.py`、`tree/`、两个新视图与文档条目（并修正重复的 `EquipmentView.vue` 行与过期的导航标签数）。
- `docs/architecture.md`：目录结构、新增 §3.10 元数据管理、§4 前端模块表（新增两个视图 + 修正导航入口清单）、§5 API 全景（新增 7 行）、§7.3 打包实施状态、§8.4 元数据文件、§9 新增「元数据单一数据源」决策行。

### 验证

- 后端冒烟（uvicorn 临时端口 8123）：17 个接口 CRUD、校验拦截、删除保护、注册向导读取新 BOM 全部通过；契约字段 13 组与前端读取字段逐项匹配。
- 前端：`npm run build` 成功（vite 5.4.21，47 modules）；两个新页面经 Vite SSR `renderToString` 渲染无运行时错误；后端同端口托管新包可访问 `/metadata/bom`。
- 元数据回滚：冒烟测试节点与临时 BOM 已清理，`backend/tree/` 仅保留 7 份正式 BOM。

## [初始提交] — 2026-09-21

- 首个提交：`chore: initial commit - ATE Runner demo (backend / frontend / sdk / docs / packaging)`。
- 内容：FastAPI 后端（License / 登录 / 装备树 / TPS 执行与运行环境 / 记录库 / 装备属性配置 / 装备助手 / 测试台注册与运维）、Vue 3 前端、仪器驱动库 SDK（含 4 份设计文档与自测）、文档集、Windows 打包脚本（嵌入式 Python 整包 + Inno/csc 安装器）。
