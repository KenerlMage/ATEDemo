# ATE 元数据管理（装备树管理 / 测试台BOM管理）

在左侧导航栏新增一级入口 **元数据管理**（下挂两个子页面），把原先分散在 `testresource/testbench_presets.json` 中只读的
**装备树节点（产品 → 子系统 → 测试台类型）** 与 **测试台 BOM** 变成可在界面维护的数据，并统一落盘到后端的 `tree` 文件夹。

- 后端模块：`backend/metadata_registry.py`
- 数据目录：`backend/tree/`（说明见 `backend/tree/README.md`）
- 前端页面：`frontend/src/views/MetadataTreeView.vue`、`frontend/src/views/MetadataBomView.vue`
- 路由：`/metadata/tree`（装备树管理）、`/metadata/bom`（测试台BOM管理）

## 一、入口与页面结构

```
左侧导航
├── 测试执行
├── 装备属性配置
├── 装备助手
├── 测试记录
├── 元数据管理 ◈            ← 新增一级入口
│   ├── 装备树管理           /metadata/tree
│   └── 测试台BOM管理        /metadata/bom
└── 测试台导航（固定最下）
```

| 子页面 | 解决的问题 | 关键能力 |
| --- | --- | --- |
| 装备树管理 | 装备树的元数据（有哪些产品 / 子系统 / 测试台类型）原来写死在预设文件里，加新分支要改代码仓里的 JSON | 新增 / 编辑 / 删除 **产品、子系统、测试台类型** 三级节点；节点落盘 `tree/tree.json`；显示每个类型的 BOM 设备数与被引用次数 |
| 测试台BOM管理 | 原来 BOM 只能靠改 `testbench_presets.json` | 按「产品 → 子系统 → 测试台类型」选定类型后，以**行内编辑表格**维护一张 BOM（设备元信息 + 默认连接参数 + 程控/必需标记），并可一并维护**测试台属性**（名称/类别/典型DUT/推荐节拍/描述）；支持从**设备模板库**引用已有型号；保存落盘 `tree/bom/<类型编号>.json` |

## 二、数据流（谁读谁写）

```
                  ┌──────────────────────────────┐
                  │  元数据管理页面 (前端)          │
                  │  装备树管理 / 测试台BOM管理      │
                  └───────────────┬──────────────┘
                                  │ /api/metadata/*
                                  ▼
                  ┌──────────────────────────────┐
                  │ metadata_registry.py          │  唯一数据源（可写）
                  │ tree/tree.json + tree/bom/*   │
                  └───────────────┬──────────────┘
                                  │ load_presets() / load_meta()
                                  ▼
        ┌─────────────────────────┴─────────────────────────┐
        ▼                                                   ▼
  testbench_registry                                前端测试台注册向导
  · /api/testbench/tree（级联选型）                  · 第一步：选中类型 → 自动获取该类型 BOM
  · /api/testbench/presets、/{id}（类型 + BOM）      · 第二步：按 BOM 默认值预填设备配置
  · /api/testbench/overview（指标）                  · 第三步：注册 + 连通性自检
        │
        ▼
  testresource/testbenches.json（已注册测试台，各自保存设备配置副本）
  backend/ate.db（注册库 → SQLite driver 配置投影，TPS 运行时读取）
```

要点：

1. **BOM 只管「类型标准装备组成」**。新建测试台时用 BOM 生成该台测试台的设备清单初值；现场改址只写该台测试台自身的记录，**不回写 BOM**。
2. **改 BOM 不会自动改写已注册测试台**，但之后新建的测试台会立即读到新版本（注册向导第一步即读元数据）。
3. 预设文件 `testresource/testbench_presets.json` 降级为**种子 + 回退**：只在 `tree/` 首次建立时导入一次；若 `tree/` 读取失败，`testbench_registry` 回退到该文件，保证注册流程不因元数据异常而中断。

## 三、存储格式

`backend/tree/tree.json`

```jsonc
{
  "products":   [ { "id": "1000-A", "name": "1000-A 机载电子系统", "note": "…" } ],
  "subsystems": [ { "id": "SPM", "name": "SPM 电源模块", "products": ["1000-A", "2000-B"] } ],
  "preset_types": [ { "id": "PB-PWR-01", "name": "电源模块标准测试台", "category": "电源类",
                      "description": "…", "typical_dut": "…", "recommended_cycle": "38 s / 件",
                      "product": "1000-A", "subsystem": "SPM", "bom_count": 8 } ]
}
```

`backend/tree/bom/<类型编号>.json`

```jsonc
{
  "preset_id": "PB-PWR-01",
  "count": 8,
  "items": [
    { "id": "tek-mso54", "name": "数字示波器", "model": "Tektronix MSO54", "vendor": "Tektronix",
      "category": "示波器", "role": "上电时序 / 纹波测量", "programmable": true, "interface": "LAN",
      "protocol": "VXI-11 (SCPI)", "default_host": "192.168.10.21", "default_port": 4000,
      "default_address": "TCPIP0::192.168.10.21::inst0::INSTR", "required": true, "note": "…" }
  ]
}
```

默认连接参数按接口口径填写：网络设备 `default_host` + `default_port`；串口设备 `default_serial_port` + `default_baudrate`；
USB / GPIB 等不可自动探测的设备填 `default_address`（自检记 skip，需人工确认）。

## 四、接口清单

共 **14 条路径、17 个接口操作**（部分路径同时支持多个方法，如 `/presets/{id}/bom` 支持 GET / PUT）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/metadata/overview` | 指标块（产品/子系统/类型/BOM 设备数）+ 存储路径 + 最近更新时间 |
| GET | `/api/metadata/tree` | 装备树（产品 → 子系统 → 类型，含 BOM 摘要与引用次数） |
| GET | `/api/metadata/store` | `tree.json` 原始内容（排障 / 导出） |
| POST | `/api/metadata/reseed?mode=merge\|reset` | 从预设文件补齐 / 全量重建 |
| POST | `/api/metadata/products` | 新增或更新产品节点（`original_id` 表示改编号） |
| DELETE | `/api/metadata/products/{id}` | 删除产品（有下级节点时拒绝，可 `?force=true`） |
| POST | `/api/metadata/subsystems` | 新增或更新子系统节点（`products` 多选归属） |
| DELETE | `/api/metadata/subsystems/{id}` | 删除子系统（下仍有类型时拒绝） |
| POST | `/api/metadata/presets` | 新增或更新测试台类型节点（属性；新建可 `copy_bom` / `copy_bom_from` 复制 BOM） |
| DELETE | `/api/metadata/presets/{id}` | 删除测试台类型（被已注册测试台引用时拒绝，可 `?force=true`） |
| GET | `/api/metadata/presets/{id}` | 类型详情（属性 + BOM + 被引用台数） |
| GET | `/api/metadata/presets/{id}/bom` | 读取 BOM 清单与属性 |
| PUT | `/api/metadata/presets/{id}/bom` | 整张 BOM 保存（可选 `attributes` 一并保存测试台属性） |
| POST | `/api/metadata/presets/{id}/bom/items` | 追加一台设备（可 `before_id` 指定插入位置） |
| DELETE | `/api/metadata/presets/{id}/bom/items/{device_id}` | 删除一台设备 |
| GET | `/api/metadata/device-catalog` | 设备模板库（所有已纳管 BOM 按类别+型号+厂商去重） |

## 五、校验与保护

**节点编号**：字母/数字开头，仅允许 `字母 数字 . _ -`（如 `PB-PWR-02`、`DSP`、`3000-C`）；产品/子系统归属必须已存在，类型必须挂在产品下已声明的子系统分支上。

**BOM 条目**：设备名称与型号必填；同一张 BOM 内设备编号唯一；网络设备 `IP/主机名` 与端口（1–65535）格式校验，**可编程**网络设备必须有默认 IP + 端口；串口设备 `COMx` / `/dev/tty*` 与波特率（300–921600）校验，可编程串口设备必须有默认串口号。

**删除保护**：仍被已注册/草稿测试台引用的测试台类型不能删（提示引用台数）；产品有子系统或类型、子系统下仍有类型时不能删。

**写入安全**：保存使用「先写 `.tmp` 再 `os.replace`」的原子写；所有写操作在进程内互斥锁下进行。

## 六、验证记录（2026-09-21 实机运行 backend `.venv` + uvicorn）

| 验证项 | 结果 |
| --- | --- |
| 首次启动自动纳入既有预设 | 通过 · `tree/tree.json` + 7 份 BOM 文件生成，2 产品 / 3 子系统 / 7 类型 / 49 台设备（43 台可编程） |
| 新增产品 → 新增子系统 → 新增类型（复制 BOM） | 通过 · 新类型 BOM 自动复制 8 台设备 |
| 保存 BOM（整张写法）+ 保存测试台属性 | 通过 · 2 台设备落盘，属性同步更新 |
| 追加 / 删除单台设备 | 通过 |
| 校验拦截（缺型号等） | 通过 · 返回 `型号不能为空`，未落盘 |
| 注册向导读取新 BOM | 通过 · `/api/testbench/tree` 出现新分支，`/api/testbench/presets/{id}` 返回新 BOM 与新属性 |
| 删除保护 | 通过 · 被 1 台已注册测试台引用的类型拒绝删除；有下级节点的产品拒绝删除 |
| 前端构建 | 通过 · `npm run build`（vite 5.4.21，47 modules，无报错），后端同端口托管新包 |
| 冒烟测试后状态回滚 | 通过 · 测试节点与 BOM 文件已清除，`tree/` 只剩 7 份正式 BOM |

## 七、现场使用步骤

1. 进入 **元数据管理 → 装备树管理**，点「＋ 新增产品」建产品节点；在产品行点「＋子系统」建子系统（可勾多个所属产品）。
2. 在子系统行点「＋类型」建测试台类型，填名称/类别/典型 DUT/推荐节拍/描述，勾选「从同子系统内 BOM 最全的类型自动复制」可避免空白 BOM。
3. 切换到 **测试台BOM管理**，按三级级联选中该类型：核对属性 → 用「＋ 新增设备行」逐台录入，或「从设备模板库添加」引用已有型号 → 填默认连接参数 → 「校验」→「保存 BOM 与属性」。
4. 到 **测试台导航 → 新增测试台**：第一步选中该类型即自动带出这份 BOM，第二步按默认值预填后再按现场改址，第三步注册并自检。
5. 需要回退时：装备树管理页「全量重建」以预设文件恢复出厂元数据（手工新增节点会丢失）。

## 八、已知边界

- **不影响已注册测试台**：改 BOM / 改属性不会同步到已注册测试台的配置副本；如需统一改址，请到「装备属性配置」逐台修改（有校验与留痕）。
- **类型编号重命名**会同步迁移 BOM 文件并更新子系统内的归属引用，但**已注册测试台记录里的 `preset_id` 不会自动改写**，需重新注册或手工改配。
- 前端 BOM 表格为行内编辑，字段较宽，窄屏需横向滚动（表格设 `min-width` 保证不挤压）。
- `testresource/testbench_presets.json` 不再被写入，仅作种子与回退源；如团队约定以该文件为发布物，请用「全量重建」后再导出 `tree/`。
