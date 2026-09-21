# ATE Runner 架构说明

> 本文档面向开发/维护人员，说明 ATE Runner 的系统架构、模块设计、核心流程与数据模型。项目根目录：`D:\ATE`。

---

## 1. 总体架构

前后端分离的浏览器/服务器架构（B/S），后端为执行核心，前端为操作界面：

```
┌──────────────────────────── 浏览器 ────────────────────────────┐
│  Vue 3 SPA                                                     │
│  LoginView（License 注册/登录）   ExecuteView（执行主界面）      │
│     │  API 封装 (src/api.js)                                    │
└─────┼───────────────────────────────────────────────────────────┘
      │ HTTP /api/*（开发态经 Vite 代理 5173→8000；Docker 经 Nginx 同源反代）
┌─────▼───────────────────────────────────────────────────────────┐
│  FastAPI 后端 (backend/main.py, :8000)                          │
│  ├─ License 层    license_utils.py（Ed25519 离线验签）            │
│  ├─ 资源层        TPS 定义 / 测试用例 / 远端 TPS 源               │
│  ├─ 执行引擎      TPS 顺序执行 + pytest 子进程（后台线程）         │
│  ├─ 存储层        SQLite (ate.db) / 报告 HTML / 测试日志           │
│  └─ 辅助能力      AI 分析代理（OpenAI 兼容调用）                  │
└─────┬───────────────────────────────────────────────────────────┘
      │ 子进程
┌─────▼───────────────────────────────────────────────────────────┐
│  pytest（执行 test_cases/*.py）→ 输出解析 → 步骤状态             │
└─────────────────────────────────────────────────────────────────┘
```

## 2. 目录结构

```
D:\ATE
├── backend/                     # FastAPI 后端
│   ├── main.py                  # 服务入口：全部 API 路由与核心逻辑
│   ├── testbench_registry.py    # 测试台注册与自检模块（独立挂载 /api/testbenches*）
│   ├── metadata_registry.py     # 元数据管理模块（装备树 + 测试台 BOM 唯一数据源，挂载 /api/metadata*）
│   ├── license_utils.py         # License 校验模块（公钥验签/机器码）
│   ├── license.dat              # 导入生效后的本地授权文件（自动生成）
│   ├── tools/                   # License 签发工具（仅签发方持有，不随产品分发）
│   │   ├── gen_keypair.py       #   生成 Ed25519 密钥对（一次性）
│   │   ├── gen_license.py       #   签发 .lic 文件
│   │   └── private_key.pem      #   签发私钥（勿分发）
│   ├── testresource/            # TPS 定义目录
│   │   ├── demo_tps.json        #   Demo TPS（8 步：init→6 用例→teardown）
│   │   ├── equipment_demo.xml   #   测试装备清单 XML（title / SPMTS 编号 / 硬件列表）
│   │   ├── testbench_presets.json # 预置测试台类型 + 标准 BOM（种子/回退源，正式数据在 backend/tree/）
│   │   ├── testbenches.json     #   已注册测试台（配置 + 自检结果，自动生成）
│   │   └── ops/                 #   环境初始化/终止脚本
│   │       ├── init_env.py
│   │       └── teardown_env.py
│   ├── tree/                    # 元数据管理数据源（「元数据管理」页维护）
│   │   ├── tree.json            #   产品 / 子系统 / 测试台类型 三级节点与类型属性
│   │   ├── bom/<类型编号>.json  #   每个类型的标准 BOM（设备默认连接参数）
│   │   └── README.md            #   目录职责、JSON 字段口径与手工编辑规则
│   ├── test_cases/              # pytest 用例目录（客户可自由增删）
│   │   └── test_demo.py         #   6 条 Demo 用例（含参数化）
│   ├── logs/                    # 运行产物（自动生成）
│   │   ├── login.log            #   登录日志
│   │   ├── execution.log        #   执行摘要日志
│   │   ├── test_logs/           #   每次 TPS 的测试日志 tps_<task>.log
│   │   └── reports/             #   HTML 测试报告 tps_<task>.html
│   ├── ate.db                   # SQLite 运行记录库（自动生成）
│   ├── .venv/                   # Python 虚拟环境（Python 3.13.7）
│   ├── requirements.txt         # fastapi / uvicorn / pytest / cryptography
│   ├── run_backend.bat          # 后端启动脚本（优先 .venv）
│   └── Dockerfile
├── frontend/                    # Vue3 前端
│   ├── vite.config.js           # /api 代理到 8000
│   ├── nginx.conf               # Docker 用：静态资源 + /api 反代
│   ├── Dockerfile
│   └── src/
│       ├── api.js               # 接口封装（统一 request + 错误处理）
│       ├── router/index.js      # 路由 + 登录守卫
│       ├── App.vue              # 左侧垂直导航外壳（品牌 + 当前测试台 + 导航标签）
│       ├── style.css            # 全局主题变量（紫色 + 白底工业风）
│       └── views/
│           ├── LoginView.vue    # License 三态 + 登录/注册
│           ├── ExecuteView.vue  # 主界面（装备树/记录/环境/步骤/报告/AI）
│           ├── EquipmentView.vue        # 装备属性配置（BOM 设备属性 + 右键改址）
│           ├── AssistantView.vue        # 装备助手（工具卡片 + 数字示波器面板）
│           ├── RecordsView.vue          # 历史测试记录查询
│           ├── TestbenchNavView.vue     # 测试台导航（独立标签 + 自检清单）
│           ├── TestbenchRegisterView.vue # 新增测试台三步注册向导
│           ├── MetadataTreeView.vue     # 元数据管理·装备树管理（产品/子系统/类型 三级节点）
│           └── MetadataBomView.vue      # 元数据管理·测试台BOM管理（行内编辑 BOM + 测试台属性）
├── start_all.bat                # 一键启动（bat 纯 ASCII + CRLF）
├── docker-compose.yml           # backend + frontend(nginx) 双容器
├── docs/
│   ├── overview.html            # 项目说明页
│   ├── docker-deploy.md         # Docker 部署指南
│   └── project-promo.md         # 项目推广介绍
└── README.md
```

## 3. 后端模块设计

### 3.1 License 授权层（license_utils.py）

- **算法**：Ed25519 非对称签名。签发方持私钥，部署端仅内置公钥（`PUBLIC_KEY_B64` 常量），完全离线验签。
- **License 文件**：JSON（product / customer / device_id 可选 / issued_at / expires_at / signature hex）。
- **校验流程**：JSON 合法 → 产品名匹配 → 验签（排除 signature、键排序、紧凑 JSON canonical payload）→ 有效期 → 可选设备绑定（机器码 = MAC+主机名 SHA256 前 16 位大写）。
- **状态接口**：`/api/license/status` 返回 `valid / not_found / invalid` + License 信息 + 机器码；导入成功写 `backend/license.dat`。
- **登录前置**：`/api/login` 无有效 License 时返回 `license_required`，前端进入注册页。

### 3.2 资源层：TPS 与测试用例

- **TPS 定义**（`testresource/*.json`）：`{ id, name, project, dut, environment, steps[] }`；steps 三类：
  - `init` / `teardown` → `script`（指向 `ops/` 下 Python 脚本）
  - `test` → `testcase_id`（pytest node id，如 `test_cases/test_demo.py::TestPowerOn::test_device_power_on_voltage`）
- **装备树**：从所有 TPS 的 `project` / `dut` / `id` 聚合出 **项目 → DUT → TPS** 三级（前端 buildTreeFromTps 或后端聚合）。
- **远端 TPS 源**：`REMOTE_TPS_URL`（环境变量 `ATE_REMOTE_TPS_URL` 覆盖，默认 `http://127.0.0.1:8999`，2s 超时）。
  - `GET /api/tps`、`GET /api/tps/{id}`：**在线优先**拉远端，失败回退本地，返回 `source: remote|local` 与 `remote_error`。
- **TPS 导入**：`POST /api/tps/import` 校验 id 命名 / steps 非空 / 每步 name+type / test 需 testcase_id、init/teardown 需 script → 存 `testresource/{id}.json` 立即生效。

### 3.3 执行引擎

- `POST /api/tps/{id}/run`：校验 UUT（`^[A-Za-z0-9]{12}$`）→ 生成 `task_id`（uuid hex 12）→ 存入 `TPS_TASKS` 内存字典 → **后台线程**执行，立即返回 task_id。
- `_run_tps_task`：按 steps 顺序执行——
  1. test 步骤：`subprocess.run([sys.executable, "-m", "pytest", node, "-v", ...])`（300s 超时）
  2. init/teardown 步骤：直接运行脚本
  3. 逐步更新 `task["steps"][i]` 状态（running/passed/failed + detail + duration）与 progress
  4. **失败即中止**，后续步骤标记 `skipped`
  5. 结束生成 HTML 报告 → `logs/reports/tps_<task>.html`；写测试日志 → `logs/test_logs/tps_<task>.log`；写 execution.log；入库 SQLite
- **并发安全**：`TPS_TASKS` 全程 `threading.Lock` 保护；任务字典仅存内存（重启即失，报告/记录持久化）。

### 3.4 存储层

- **SQLite**（`ate.db`，标准库 sqlite3，零新依赖）：表 `test_records`——task_id / uut / project / dut / tps_id / tps_name / status / passed / failed / skipped / total / duration / report_file / start_time / end_time / created_at；索引 `uut`、`task_id`。每次操作短连接。
- **报告**：自包含 HTML（含装备信息卡：项目/DUT/UUT + 逐步状态表），可独立打开。
- **测试日志**：纯文本，头部（TPS/UUT/项目/DUT/步骤数）→ 逐步（时间戳 + PASS/FAIL + 耗时 + pytest 输出摘要）→ 尾部（总结果/耗时）。

### 3.5 AI 分析代理

- `POST /api/ai/analyze`：入参 `{api_url, api_key, task_id}`。
- 流程：读测试日志（末 6000 字符）+ 报告 HTML 转纯文本（`_strip_html`）→ 组装中文 prompt（结论 → 可能原因排序 → 排查建议）→ OpenAI 兼容 `POST {api_url}/chat/completions`（Bearer 鉴权，模型 gpt-4o-mini，60s 超时）。
- **错误全覆盖**：未配置 / URLError 无法连接 / HTTPError（带回显详情）/ 返回格式异常，均返回明确中文 message；无需真实 AI 服务即可验证 UI 流程。

### 3.6 测试台注册与自检（testbench_registry.py）

- **挂载方式**：`main.py` 末尾 `import testbench_registry; testbench_registry.register_routes(app, TPS_DIR)`，接口不侵入主文件。
- **预设类型与 BOM**：`testresource/testbench_presets.json` 定义测试台类型（电源类/射频类/功率驱动类/高压类/老化类/伺服类/读头类，各含 6-9 台设备），每台设备声明 `interface`（LAN / SERIAL / USB / GPIB / NONE）、默认 IP·端口·串口、协议、是否 `programmable`、是否 `required`。当前 7 个预设 / 49 台设备。
- **默认测试台（开箱即已注册）**：`register_routes()` 启动时调用 `_seed_default_testbenches()` 把 `DEFAULT_TESTBENCHES` 写入注册库――预置 **SPM读头动态测试台**（`SPM-RH-DYN-01` / `SPMTS202609120001` / `1000-A·SPM` / 7 台设备含 Fluke 8846A 万用表）。种子逻辑幂等（同编号已存在则跳过），并以注册库的 `seeded_defaults` 记录已种过的预设，**删除后不自动复活**；`POST /api/testbenches/restore-defaults` 可显式重建。种子时以 `simulate` 方式先跑一次自检，保证导航页一进去就有自检清单。
- **注册表**：`testresource/testbenches.json`，单文件 JSON + `threading.RLock` + 原子写（先写 .tmp 再 `os.replace`），键含 `id / preset_id / title / serial / station / devices / step / status / registered_at / verification / verify_history`。
- **一致性保障**：保存时以预设 BOM 为基准 `_merge_devices` 合并，BOM 元信息（名称/型号/厂商/用途）不会被前端覆盖；`port/baudrate` 经 `_as_int` 归一化（空串→`None`）；serial 校验 `^SPMTS[0-9]{12}$` 并查重。
- **连通性自检**：配置类 6 项静态校验（编号/名称/工位/可编程设备配置完整/地址冲突/必备设备）+ 每设备 1 项动态探测：网口设备 `socket.create_connection` TCP 探测（记录时延，连通后仅做一次 ≤0.3s 快速探答）；串口用 pyserial（未安装则记为跳过）；USB/GPIB/NONE 记为需人工确认。自检结果持久化并保留最近 10 次历史。
- **设备属性读写（装备属性配置）**：`GET /api/testbench/devices` 把所有测试台的 BOM 设备拍平（带 `bench_id`/`resource` 等上下文）供前端卡片渲染；`PATCH /api/testbenches/{id}/devices/{device_id}` 只允许修改**连接参数**（IP/端口/串口/波特率/资源地址/通道/协议/备注），BOM 元信息（型号/厂商/类别/用途）不可改；写入前做格式与同台地址冲突双重校验，每台设备保留 10 条 `config_history`（字段级前后值），非可编程设备返回明确拒绝。

### 3.7 装备助手与仪器驱动（instrument_tools.py）

- **挂载方式**：`main.py` 末尾 `import instrument_tools; instrument_tools.register_routes(app, TPS_DIR)`，与注册模块同级、互不依赖。
- **工具卡片注册表**：`TOOL_CARDS` 定义每个工具的卡片元信息（名称/副标题/说明/协议/能力清单/是否可用），`GET /api/tools` 下发；前端只按卡片渲染，新增工具 = 后端加一张卡片 + 一组处理逻辑，前端与导航无需改动。
- **设备绑定**：绑定列表不另维护，直接读注册库 `testbenches.json` 的 BOM 设备（`_bindings()`），按“可绑定（网口）→ 是否示波器”排序，界面提示与注册库始终一致。
- **示波器会话**（`ScopeSession`）：每个 `(bench_id, device_id)` 一个会话，持有 TCP socket + RLock + 命令日志；连接时 `*IDN?` 识别并回读时基/通道状态；支持 `run/stop/single/autoset/timebase/channel/acquire/measure/frame/close`。泰克 5 系列 MSO 取波形走 `DATa:*` + `CURVe?` + `WFMOutpre:*` 标定（ASCII 编码直接用已标定值，二进制编码按 `YMULT/YOFF/YZERO` 换算），测量走 `MEASUrement:ADDMEAS` + `MEAS<x>:VALUE?`（单项失败不影响其他项，如实标为 `error`）。
- **仿真信号源（无硬件可用）**：绑定列表末尾内置一台 `SIM-SCOPE`（`bench_id=__sim__`），仿真模式未选设备或设备不存在时自动回退到它，因此「一键仿真」不需要任何仪器；`sim_signal` 动作可调波形类型（正弦/方波/三角/锯齿/直流/噪声）、频率、Vpp、偏置、噪声与相位，并在 `auto_timebase=true` 时按「屏内周期」反推时基、在 `auto_scale=true` 时按幅度挑 1-2-5 档 V/div（手动改过档位后自动适配关闭）；模拟波形由后端算法生成且与真实取波形同构，调制参数只影响仿真路径，切到真实仪器不影响实测。
- **离线模拟与实际降级**：模拟模式生成与真实返回同构的波形（整屏 = 10 格 × 时基，均匀采样）与测量值（从样本自算，避免非物理读数）；真实模式连接失败且 `allow_fallback=true` 时自动降级为模拟并在响应与界面明确标注，不冒充实测。
- **失败不影响流程**：所有操作返回 `{success, message, result, log, state}` 统一结构；未连接、不支持的 action、SCPI 超时均有中文提示，界面不白屏。

### 3.8 测试台装备运维（testbench_lifecycle.py）

- **挂载方式**：`main.py` 末尾 `import testbench_lifecycle; testbench_lifecycle.register_routes(app, TPS_DIR)`，与注册模块 / 装备助手模块同级；测试台配置仍以注册库为唯一事实源，本模块不另存一份。
- **测试台导出（待注册 / 已注册）**：`GET /api/testbench/pending` 列出草稿状态测试台；`POST /api/testbenches/export` 按 `status`（`registered` 已注册 / `draft` 草稿 / `all` 全部，缺省 = 待注册）或 `bench_ids`（指定台）生成 `ate.testbench.export.v1` 结构的 JSON（含每台设备的型号/接口/连接参数/是否已配置），落盘到报告目录并回传内容供浏览器下载，供备份现场配置、离线核对或迁移到其他工控机；响应带 `scope`/`scope_label`/`by_status`，界面弹窗直接展示作用范围与状态构成（已注册 / 草稿）。
- **装备初始化 / 终止**：`POST /api/testbenches/{id}/init|teardown` 按设备类别（示波器 / 万用表 / 电源 / 电子负载 / 信号源 / 运动控制）取标准动作序列（初始化：复位 → 远程 → 安全态；终止：输出关闭 / 输入关闭 / 转速归零 → 退回本地），真实模式逐条下发 SCPI 并读回应答，模拟模式只生成同构步骤记录；**结论只看可编程设备**，非程控设备（夹具 / 探针）标为信息项，不拉低结论。
- **硬件自检与报告**：`POST /api/testbenches/{id}/selfcheck` 复用 `testbench_registry._run_verification`，结果同时写回注册库（`verification` + `verify_history`）并生成自包含 HTML 报告（紫白主题，可在界面内 iframe 直接查看）。
- **报告区与本地资源管理器**：所有产物落到 `backend/logs/testbench/` 并登记 `index.json` 清单（含结论 / 模式 / 汇总 / 大小 / 路径），`GET /api/testbench/reports` 支持按 `kind`（自检/初始化/终止/导出）与 `bench_id` 过滤；`POST /api/testbench/reports/open` 调系统资源管理器打开报告目录或定位到具体报告（Windows `explorer /select,`，Linux `xdg-open`），只允许打开固定报告目录、拒绝路径穿越。

### 3.9 TPS 运行环境（tps_runtime/ + drivers/ + ate_db.py）

- **workspace 与临时副本**：`tps_runtime/workspace.py` 的 `workspace_root()` 取 `<ATE 安装运行目录>/workspace`（可用环境变量 `ATE_WORKSPACE` 覆盖），`ensure_workspace()` 在不存在时自动创建；`stage()` 在真正跑用例前把整个 TPS 目录 `copytree` 一份临时副本到 `workspace/<TPS 名称>/run_<task_id>/`，源 TPS 永不被写脏；`cleanup_runs()` 按保留策略（默认 5）清理旧副本，**当前运行目录始终受保护**。
- **公共 conftest**：正本放在 `tps_runtime/conftest_template.py`，部署时**按文件复制**到 `workspace/conftest.py` 并写入每个运行目录（升级带 `ATECONFTEST_VERSION` 版本标记，版本一致则 kept，不一致则备份 + 覆盖）。conftest 读同目录 `ate_env.json`（或 `ATE_ENV_FILE`），据此把后端目录加入 `sys.path`、设置 `ATE_DB_PATH`，然后 `import ate_db` / `import drivers`。
- **注入内容**：夹具 `ate_ctx`（聚合上下文）/ `ate_bench` / `ate_devices`（来自 SQLite 的测试台与设备）/ `ate_thresholds`（本次 `testconfig`）/ `driver(别名)` / `ate_runner`（结果记录）。`ctx.check(别名, 值)` 自动按上下限判定并抛 `LimitError`（带 `checks` 明细），否则失败用例的阈值信息无法落盘。
- **装备信息与 driver 来自 SQLite**：`ate_db.py` 把注册库 JSON 全量投影为 `bench_registry` / `device_registry` / `bench_sync_log` 三表（PK `(bench_id, device_id)`，含解析出的 `driver` 规格名与 `config_json`）；收口点在 `testbench_registry._save_registry()`（每次注册/改址都重建投影，异常不影响注册流程），运行时可 `POST /api/runtime/db/sync` 手动重建——**删库不影响授权与注册数据**。
- **驱动工厂与仿真**：`drivers/factory.py` 的 `DRIVER_SPECS` 按设备字段打分选驱动（先具体后兜底），`transport.py` 把传输层分叉（LAN socket / 串口 pyserial 可选 / `SimulateTransport`），因此**同一驱动既能跑真机也能跑仿真**；`mode=simulate`（默认）时全部动作走仿真且读数稳定可复现，与真实路径返回值同构。
- **TPS v2 清单与生成执行入口**：`manifest.py` 校验 `testconfig` / `device_config` / `cmd_suit` 三字段（中文错误清单），把 setup → cmd_suit → teardown 归一化成顺序步骤；`templates.py` 生成 `test_tps_generated.py`（带 `test_setup` / `test_case` / `test_teardown` 三个参数化测试函数），一个用例对应一个 pytest 节点，进度与报告按节点统计。
- **v1 兼容**：旧单文件 TPS（`demo_tps.json`，`steps` 列表）继续按原逻辑执行（`test` 步骤跑 `testcase_id`，init/teardown 跑脚本），**不建 workspace 副本**；`main.py` 的 `_load_tps_files` / `_read_tps` / `_run_tps_step` 改为委派 `tps_runtime`，v2 分支存在 `step["v2"]` 标记时走临时副本。
- **报告与追溯**：任务执行前写好运行环境日志头（Workspace / Bench / Mode），HTML 报告新增「运行环境 (workspace)」卡片（临时副本目录 / 公共 conftest 路径 / 清单三字段计数）；运行目录里的 `run_result.json` 落汇总、逐用例阈值明细与每台设备解析出的 driver 规格。

### 3.10 元数据管理（metadata_registry.py + tree/）

- **唯一数据源**：`backend/tree/tree.json` 存装备树（`products` / `subsystems` / `preset_types` 三级节点与类型属性），`backend/tree/bom/<类型编号>.json` 存该类型的标准 BOM；数据目录可用环境变量 `ATE_TREE_DIR` 覆盖，`tree/` 下另有 `README.md` 说明字段口径。
- **种子导入**：首启 `ensure_store(mode="merge")` 从 `testresource/testbench_presets.json` 导入既有预设类型与 BOM（存量资产纳入管理）；`POST /api/metadata/reseed?mode=reset` 可按预设文件全量重建。
- **读取收口**：`testbench_registry._load_presets()` / `_preset_tree()` 优先调 `metadata_registry.load_presets()` / `load_meta()`，失败才回退预设文件——因此**注册向导与测试台导航零改动即读到新数据**。
- **写入安全**：保存走「先写 `.tmp` 再 `os.replace`」原子写 + `threading.RLock` 串行化；每次请求重新读盘，手工编辑 JSON 无需重启。
- **校验与保护**：节点编号正则（字母/数字开头，允许 `._-`）、类型必须挂在「该产品下已声明的子系统」；BOM 名称/型号必填、设备编号唯一、网络设备 IP + 端口（1-65535）、串口 `COMx` + 波特率（300-921600）、可编程设备必须有默认连接；删除保护：被已注册测试台引用的类型、有下级节点的产品/子系统拒删（支持 `force=true`）。
- **边界**：BOM 只描述「类型标准组成」，不回写已注册测试台的配置副本；现场改址也不反向回写 BOM。

## 4. 前端模块设计

| 模块 | 职责 |
| --- | --- |
| `api.js` | 统一 `request()`（JSON 封装 + 错误抛出），导出全部接口函数 |
| `router` | `/login`、`/`（守卫：无 token 跳登录） |
| `style.css` | 主题变量：`--accent` / `--accent-rgb` / `--bg` / `--panel` / `--border` / `--ok` / `--warn` / `--err`（紫色 + 白底工业风，改配色只改此处） |
| `App.vue` | **左侧垂直导航外壳**：品牌区 + 当前测试台（title/SPMTS 编号）+ 6 个导航入口（测试执行 / 装备属性配置 / 装备助手 / 测试记录 / **元数据管理（展开为「装备树管理」「测试台BOM管理」两个子项）** / **测试台导航置底**）+ 用户与退出；≤900px 自动转顶部横向导航。导航项在 `NAV` 数组中配置，父项带 `children` 时渲染 `.side-sub` 子入口 |
| `LoginView.vue` | 三态：检查中 → 未授权（注册页：导入 .lic 文件）→ 已授权（登录表单） |
| `ExecuteView.vue` | 主界面四区：左列（装备树 + 测试记录查询）、右列（环境卡 / 步骤列表 / 报告） |
| `EquipmentView.vue` | **装备运维页（三栏控件）**：顶部栏切换 **装备属性配置**（设备清单来自各测试台注册 BOM（`GET /api/testbench/devices`），按测试台分组 + 筛选/搜索；可编程设备**右键菜单**（修改属性/恢复 BOM 默认值/复制资源地址/变更历史）+ 属性弹窗按接口类型渲染字段；非可编程设备只读；底部四个面板：待注册测试台导出、**已注册测试台导出（勾选 / 全选 + 导出选中 / 导出全部已注册）**、**TPS 运行环境**（workspace 路径 / 公共 conftest 版本 / SQLite driver 配置统计 / 驱动规格表 + 初始化·同步按钮）与 **TPS 运行目录**（按 TPS 包列运行目录与结果徽标、清理旧目录））→ **测试台装备自检**（目标测试台 + 运行模式 + 初始化/终止/自检三按钮 + 报告弹窗）→ **装备报告中心**（报告目录信息 + 历史报告表 + 打开本地资源管理器）；切换同步地址栏 `#/equipment?tab=selfcheck|reports`，刷新后停留在同一栏 |
| `AssistantView.vue` | **装备助手页**：工具卡片网格（单击/双击选中 → 「运行工具」启动）；数字示波器面板（绑定设备/连接与识别/运行·停止·单次/自动设置/时基/通道/Canvas 波形/测量卡片/SCPI 日志），支持离线模拟 |
| `RecordsView.vue` | 历史测试记录查询（日期/项目/批次/UUT 组合筛选） |
| `TestbenchNavView.vue` | 测试台导航页：指标块 + 已注册测试台**独立标签** + 详情（产品/子系统、工位信息、设备表、自检清单、重新自检/编辑/删除） |
| `TestbenchRegisterView.vue` | 新增测试台三步注册向导（三级级联选型 + BOM 表 + 工位表单 + 设备配置卡 + 验证方式选择 + 自检清单），支持 `?id=` 编辑模式；级联选项与 BOM 来自元数据模块（`/api/testbench/tree`、`/api/testbench/presets/{id}`） |
| `MetadataTreeView.vue` | **元数据管理·装备树管理**（`/metadata/tree`）：指标块 + 存储位置条（tree 文件夹 / tree.json / bom 目录 / 更新时间 + 「从预设文件补齐」「全量重建」）+ 三级节点树（新增/编辑/删除，产品含所属子系统、子系统可多选归属产品、类型含属性与「复制 BOM」）+ 与 BOM 页互跳 |
| `MetadataBomView.vue` | **元数据管理·测试台BOM管理**（`/metadata/bom`，支持 `?preset=` 直达）：产品→子系统→类型三级级联 + 测试台属性区 + BOM 行内编辑大表（增行/复制/上移下移/移除/删库、接口切换默认参数口径、校验）+ 设备模板库弹窗 + 「供新建测试台使用」信息卡与跳转注册向导 |

ExecuteView 关键交互：

- **UUT 反查 TPS**：输入 12 位 UUT → `getTpsByUut` → 自动展开树并选中对应 TPS
- **TPS 执行**：`runTps(tpsId, uut)` → `setInterval` 600ms 轮询 `getTpsTask` → 更新步骤状态/进度 → 完成后刷新报告 iframe
- **数据源徽章**：`tpsSource`（remote/local）→「● 在线 / ● 离线」+ 离线提示条
- **AI 配置**：API 地址/Key 存 `localStorage`（仅本机），分析结果 `pre` 块展示
- **报告查看**：iframe `src=/api/reports/{file}`；点击记录列表项直接加载对应报告

## 5. API 全景

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/license/status` | License 状态 + 机器码 |
| POST | `/api/license/import` | 导入 .lic 内容（验签/有效期/设备绑定） |
| POST | `/api/login` | 登录（License 前置校验） |
| GET | `/api/testcases` | pytest 用例列表（--collect-only） |
| GET | `/api/tree` | 装备树（项目→DUT→TPS） |
| GET | `/api/tps` | TPS 列表（远端优先，离线回退本地） |
| GET | `/api/tps/by-uut?uut=` | 按 UUT 反查最近使用的 TPS |
| GET | `/api/tps/{id}` | TPS 详情（远端优先） |
| POST | `/api/tps/import` | 导入 TPS JSON（校验+落盘） |
| POST | `/api/testbenches/restore-defaults` | 重建默认测试台（删除后恢复预置演示数据，幂等） |
| POST | `/api/tps/{id}/run` | 执行 TPS（body `{uut, bench_id?, mode?}`，12 位英数；v2 包先在 workspace 生成临时副本） |
| GET | `/api/runtime/workspace` | TPS 运行环境总览（workspace / 公共 conftest 版本 / 运行目录） |
| POST | `/api/runtime/workspace/init` | 创建 workspace + 部署/更新公共 conftest（`force` 覆盖前自动备份） |
| GET | `/api/runtime/drivers` | 驱动规格清单（工厂可解析的 key / 名称 / 类别 / 动作） |
| GET | `/api/runtime/db` · `POST /api/runtime/db/sync` | SQLite driver 配置投影统计 / 手动重建投影 |
| GET | `/api/runtime/schema` | TPS v2 结构说明（三字段含义） |
| GET | `/api/tps/{id}/manifest` | TPS 清单（v2 自动归一化） |
| POST | `/api/tps/{id}/stage` · `/collect` | 准备运行环境（临时副本）/ 用例收集（只读） |
| GET | `/api/tps/{id}/runs` · `POST /api/tps/{id}/runs/cleanup` | 运行目录列表 / 清理旧运行目录 |
| POST | `/api/tps/validate` | TPS v2 清单校验（中文错误清单） |
| GET | `/api/tps-tasks/{id}` | 任务状态/步骤/报告路径（600ms 轮询） |
| GET | `/api/reports/{file}` | 报告 HTML |
| POST | `/api/ai/analyze` | AI 失败分析（日志+报告 → AI API） |
| GET | `/api/records?uut=` | 记录查询（留空查全部，limit≤500） |
| GET | `/api/equipment` | 测试装备清单（title / SPMTS serial / 硬件列表） |
| GET | `/api/testbench/presets` · `/api/testbench/presets/{id}` | 预设测试台类型（列表 / 含 BOM 详情） |
| GET | `/api/testbench/tree` | 预设类型三级级联树：产品 → 子系统 → 测试台类型 |
| GET | `/api/testbench/overview` | 测试台导航页指标汇总（含产品数/子系统数） |
| GET | `/api/testbenches` · `/api/testbenches/{id}` | 已注册测试台列表 / 详情 |
| POST | `/api/testbenches` | 创建或更新测试台（三步流程共用；`status=registered` 为正式注册） |
| DELETE | `/api/testbenches/{id}` | 删除测试台 |
| POST | `/api/testbenches/{id}/verify` | 连通性自检（`mode=real|simulate`）→ 自检清单 |
| GET | `/api/testbench/devices` | 装备属性配置页数据源（各测试台 BOM 设备拍平） |
| GET | `/api/testbench/pending` | 待注册（草稿状态）测试台清单：编号 / 预设类型 / 设备与配置统计 / 当前步骤 |
| POST | `/api/testbenches/export` | 导出测试台（`status=registered` 已注册 / `draft` 草稿 / `all` 全部；`bench_ids` 指定台）→ `ate.testbench.export.v1` JSON，落盘 + 回传 |
| GET | `/api/testbench/reports` | 历史报告清单（按 `kind` / `bench_id` 过滤，带导出范围统计 `scopes`） |
| PATCH | `/api/testbenches/{id}/devices/{device_id}` | 修改可编程设备连接属性（或 `reset_defaults` 恢复默认值） |
| GET | `/api/tools` · `/api/tools/oscilloscope/bindings` | 装备助手工具卡片清单 / 可绑定示波器设备 |
| POST | `/api/tools/oscilloscope/connect` · `/action` · `GET /state` | 示波器会话建立 / SCPI 操作 / 会话状态 |
| GET | `/api/metadata/overview` · `/tree` · `/store` | 元数据指标（产品/子系统/分支/类型/BOM 设备数）+ 装备树（产品→子系统→类型）+ tree.json 原文 |
| POST | `/api/metadata/products` · `/subsystems` · `/presets` | 新增或更新三级节点（`original_id` 改编号；新建类型可 `copy_bom` / `copy_bom_from` 复制 BOM） |
| DELETE | `/api/metadata/products/{id}` · `/subsystems/{id}` · `/presets/{id}` | 删除节点（被引用或有下级时拒绝，`?force=true` 强制） |
| GET / PUT | `/api/metadata/presets/{id}/bom` | 读取 / 整张保存某类型的 BOM（可带 `attributes` 同保存测试台属性） |
| POST / DELETE | `/api/metadata/presets/{id}/bom/items[/{device_id}]` | 追加（可 `before_id` 指定位置）/ 删除 BOM 单台设备 |
| GET | `/api/metadata/device-catalog` | 设备模板库（全部 BOM 按类别 + 型号 + 厂商去重） |
| POST | `/api/metadata/reseed?mode=merge\|reset` | 从预设文件补齐 / 全量重建 tree 数据 |
| GET | `/api/health` | 健康检查 |
| *旧接口* | `/api/execute`、`/api/tasks`、`/api/tasks/{id}` | 早期单用例执行接口，保留兼容 |

## 6. 关键流程时序

### 6.1 TPS 执行

```
前端                    后端                             pytest
 │ runTps(id, uut) ──▶ │ 校验 UUT/生成 task_id           │
 │                     │ 起后台线程 → 立即返回            │
 │ ◀── task_id         │                                │
 │ 轮询 600ms          │ _run_tps_task 逐步骤执行 ─────▶ │ 子进程运行
 │ ◀── 步骤状态/进度    │ 失败中止+跳过 / 全过完成         │
 │                     │ 生成报告 + 测试日志 + 入库       │
 │ 完成 → 加载报告 iframe                                │
```

### 6.2 License 注册

```
打开页面 → GET /api/license/status
   ├─ valid      → 显示登录表单
   ├─ not_found  → 注册页：选择 .lic 文件
   │                → POST /api/license/import（验签→写 license.dat）→ 刷新状态 → 登录表单
   └─ invalid    → 注册页 + 错误提示（过期/篡改/设备不匹配）
```

## 7. 部署架构

### 7.1 Docker（推荐 Linux 工控机/服务器）

```
浏览器 ──▶ :8080 Nginx（frontend 容器）
                ├─ /        → 静态 SPA（dist）
                └─ /api/*   → 反代 backend 容器 :8000
卷挂载：backend/test_cases、backend/logs（增删用例/报告不丢）
```

```bash
docker compose up -d --build
```

### 7.2 本地开发

- 后端 `uvicorn main:app --port 8000`；前端 `npm run dev`（5173，Vite 代理 /api）。

### 7.3 Windows 工控机（无 Docker）

- 免安装打包**已实施**：`packaging/build_package.ps1` 产出 `ATE_Setup-<版本>.exe`（优先 Inno Setup，无则用系统自带 `csc.exe` 编自包含安装器）与 `ATERunner-portable-<版本>.zip`；整包 = `app/`（后端源码）+ `web/`（前端 dist）+ `runtime/`（嵌入式 Python，离线），PyInstaller 仅用于编 `ATE_Launcher.exe` 启动器（未装则回退 `start-ate.bat`）。
- License 支持按客户机器码绑定，适配离线交付。

## 8. 数据模型

```
test_records
├── id          INTEGER PK
├── task_id     TEXT  (uuid hex 12)
├── uut         TEXT  (12 位英数, 索引)
├── project / dut / tps_id / tps_name
├── status      TEXT  (completed | failed)
├── passed / failed / skipped / total   INTEGER
├── duration    REAL
├── report_file TEXT  (reports/tps_<task>.html)
├── start_time / end_time / created_at  TEXT
```

```
testbench_presets.json      # 预设测试台类型 + 标准 BOM（只读）
├── products[]     { id(1000-A / 2000-B), name, note }
├── subsystems[]   { id(SPM/WA/SD), name, products[] }
└── presets[]      { id, name, category,
                     product(1000-A) / product_name / subsystem(SPM) / subsystem_name,
                     description, typical_dut, recommended_cycle,
                     bom[] { id, name, model, vendor, category, role, programmable,
                             interface(LAN/SERIAL/USB/GPIB/NONE), default_host, default_port,
                             default_serial_port, default_baudrate, default_address,
                             default_channel, protocol, required, note } }
      /api/testbench/tree 由 presets 的 product / subsystem 字段聚合得出（单一数据源）
```

```
testbenches.json          # 测试台注册表（JSON 单文件 + 原子写）
├── version
├── seeded_defaults[]   # 已播过默认测试台种子的 preset_id（删除后不复活的关键标记）
├── testbenches[]       # 已注册/草稿测试台
│   ├── id / preset_id / preset_name / title / serial(SPMTS+12位) / status(draft|registered)
│   ├── station        { line, station, location, remark }
│   ├── devices[]      { id, name, model, vendor, category, role, programmable, interface,
│   │                    host, port, protocol, serial_port, baudrate, address, channel,
│   │                    required, note, configured,
│   │                    config_history[] { at, reset_defaults, changes{字段:{from,to}} } }   ← 装备属性配置页可改
│   ├── step / created_at / updated_at / registered_at / seeded(默认测试台标记)
│   ├── verification   { overall(pass|warn|fail), mode(real|simulate), checked_at, timeout,
│   │                    summary{total,pass,fail,skip}, checklist[], conclusion }
│   └── verify_history[]  (最近 10 次: at / mode / overall / pass / fail / skip)
   （接口返回时额外补 product / product_name / subsystem / subsystem_name，代表该测试台服务的装备树分支）
```

```
backend/ate.db（表）              # 注册库 → SQLite 投影（JSON 注册库仍是唯一事实源，可随时重建）
├── bench_registry      (bench_id PK, preset_id, title, serial, product, subsystem, status,
│                        station_json, verify_overall, verify_mode, device_count,
│                        programmable_count, synced_at)
├── device_registry     (bench_id + device_id PK, name, model, vendor, category, role,
│                        programmable, interface, host, port, protocol, serial_port, baudrate,
│                        address, channel, driver, timeout, config_json, synced_at)
│                        # driver = 工厂解析出的规格名（tektronix-mso5 / generic-dmm / …）
└── bench_sync_log      (id PK, synced_at, source, bench_count, device_count, note)
```

```
workspace/                        # TPS 运行环境（安装运行目录下，不存在自动创建）
├── conftest.py                   # 公共 conftest（程序提供，版本标记 ATECONFTEST_VERSION）
└── <TPS 名称>/                    # 以 TPS 名称命名的目录
    └── run_<task_id>/            # 运行时临时副本（tps.json / testcase/ / test_tps_generated.py /
                                  #   ate_env.json / conftest.py / run_result.json / run_log.txt）
```

### 8.4 元数据文件（backend/tree/）

```
tree.json                 # products[] / subsystems[] / preset_types[]：三级节点与类型属性
                          #   （名称 / 类别 / 典型 DUT / 推荐节拍 / 描述 / 归属产品与子系统 / bom_count）
bom/<类型编号>.json        # preset_id / count / items[]：设备名称·型号·厂商·类别·用途、接口与协议、程控标记、
                          #   default_host / default_port / default_serial_port / default_baudrate /
                          #   default_address / required / note
```

写入均为「先写 .tmp 再 `os.replace`」原子替换；`testresource/testbench_presets.json` 降级为种子与回退源（仅在 `tree/` 首次建立时导入）。

## 9. 关键设计决策与约束

| 决策 | 理由 |
| --- | --- |
| pytest 子进程执行 | 复用 pytest 收集/断言/报告生态，客户新增用例零代码接入 |
| TPS 定义 JSON + 脚本分离 | 测试编排与实现解耦；非开发人员可维护流程 |
| 内存任务字典 + 轮询 | 实现简单、无额外依赖；任务量小（单工位）足够 |
| SQLite 零依赖 | 工控机免装数据库，单文件随目录迁移 |
| 报告/日志/记录三方留存 | 满足质量追溯；报告自包含可独立分发 |
| Ed25519 离线验签 | 工控机可能无外网；非对称签名防伪造、可绑定设备 |
| 元数据单一数据源 | 预设测试台类型与 BOM 收口到 `backend/tree/`，`testbench_registry` 优先读它并保底回退预设文件；注册向导/测试台导航零改动即生效，现场也能手工改 JSON（每次请求重读，无需重启） |
| 远端 TPS 在线优先+本地回退 | 产线断网不中断测试，默认 TPS 始终可用 |
| AI 代理走后端 | 规避浏览器 CORS；Key 不落服务器（仅浏览器 localStorage） |
| bat 脚本纯 ASCII + CRLF | cmd 按 GBK 解析 UTF-8 中文会变乱码令牌（历史踩坑教训） |
| 测试台注册独立成模块 | 注册/自检与测试执行解耦，`main.py` 只多一行挂载；后续接仪器驱动不影响执行引擎 |
| 装备页数据不另存一份 | 装备属性配置页直接读注册库 BOM 设备（`GET /api/testbench/devices`），避免“两份设备清单对不上” |
| 设备属性只允许改连接参数 | 型号/厂商/类别属于 BOM 元信息，由预设类型决定；防止现场改乱后与 BOM 不一致 |
| 工具以卡片注册表提供 | 新增仪器工具只加卡片 + 路由，前端网格与导航自动适配，不因工具变多而改页面结构 |
| 示波器支持离线模拟 | 产线/客户现场无仪器时也能演示与培训；降级时明确标注“模拟”，不冒充实测数据 |
| 仪器控制放后端 | 浏览器无法直接建 TCP/SCPI 会话；后端统一处理超时、日志与错误，前端只管交互 |
| 自检分「真实探测 / 离线模拟」两种模式 | 现场无仪器时也能演练注册流程；模拟结果在清单与标签上标注“模拟”，不冒充真实探测 |
| BOM 由预设类型下发、后端合并 | 避免前端伪造/漏填设备元信息；同类型测试台设备清单统一，便于产线复制工位 |
| 级联选型按装备树结构 | 产品 → 子系统 → 测试台类型与现场心智一致；逐级过滤，不用一次性铺满卡片 |
| 级联树由预设字段聚合 | `product`/`subsystem` 写在每个预设上，接口现算树；单一数据源，不会出现目录与实际类型对不上 |
| TPS 运行时复制临时副本到 workspace | 源 TPS 只读不被写脏，可并行/重跑互不影响，产线可事后翻查任何一次的输入与产物 |
| 公共 conftest 集中注入装备/阈值/driver | TPS 用例只写业务逻辑，不关心 IP·端口与驱动细节；现场改址只改注册库一处 |
| 注册信息投影到 SQLite | 运行时只读查询（不受 JSON 文件读写竞争影响），且可随时从事实源重建；删库不丢授权与注册 |
| 驱动按字段打分选工厂 | 新增设备型号只需加一条规格（或复用通用 SCPI 兜底），不改 TPS 与执行引擎 |
| 仿真与真实共用同一驱动，只在传输层分叉 | 仿真路径与真机路径返回结构同构，无硬件也能跑通全流程；避免两套代码走样 |
| 阈值判定放在 conftest 而非用例里 | 阈值集中维护在 `testconfig`，用例只回传测量值；改阈值不改用例代码 |
| 主题全部走 CSS 变量 | 换配色只改 `style.css` 一处；各页面 scoped 样式用 `rgba(var(--accent-rgb), x)`，不再写死色值 |
| 工位信息只留产线与位置 | 班次/责任人工号属于运行期人员信息，不应绑在测试台硬件注册上（可由登录用户或工单系统提供） |

## 10. 可扩展方向

- JWT 用户鉴权、多角色（操作员/工程师/管理员）
- 测试台注册扩展：仪器驱动自动生成（接口说明书 + skill 喂给 AI）、注册后自动生成 equipment XML、测试台与 UUT/工位绑定
- WebSocket/SSE 推送替代轮询；多任务并行队列 + 失败重试
- TPS 版本管理、远端源鉴权与增量同步
- AI 分析结果入库、多模型/自定义 prompt、分析结果导出
- 对接真实仪器驱动替换 Demo 模拟读数；报告按时间/状态组合筛选 + CSV 导出
