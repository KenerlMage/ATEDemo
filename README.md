# ATE 自动测试装备 (Demo)

前后端分离的自动测试工具 Demo：登录 → 查看可执行用例列表 → 选择用例执行 → 实时查看进度与结果。

- 前端：Vue 3 + Vite + vue-router
- 后端：FastAPI (Python)，通过子进程调用 pytest 执行测试用例
- Demo 用例：`backend/test_cases/test_demo.py`（6 条用例，含参数化用例）

## 功能

| 功能 | 说明 |
| --- | --- |
| 登录页 | 登录前先校验 License（Ed25519 签名），无有效授权时进入注册页，通过文件选择导入 .lic；登录记录写入 `backend/logs/login.log` |
| 装备树 | 左侧按 项目 → 测试对象(DUT) → TPS 三级树导航；左上角支持输入 UUT 查询对应 TPS、导入 TPS 文件；在线时从远端 TPS 服务器拉取，离线自动回退本地默认 TPS（保持默认不变） |
| 测试日志 | 每次 TPS 运行自动生成 `backend/logs/test_logs/tps_<task>.log`（TPS/UUT/逐步 PASS/FAIL/耗时/pytest 输出） |
| AI 失败分析 | 右下角报告区可配置 AI API 地址与 Key（存本机浏览器），一键将测试日志+报告发给 AI 分析失败原因与排查建议（OpenAI 兼容 /chat/completions） |
| 用例执行 | 输入 12 位 UUT 名称后执行，后端按 TPS 步骤顺序运行（环境初始化 → 测试用例 → 环境终止） |
| 进度反馈 | 前端 600ms 轮询，逐步展示每步 待执行/执行中/通过/失败/跳过 状态、进度条、实时日志 |
| 测试报告 | 每次运行生成 HTML 报告（含装备树字段：项目/DUT/UUT），右下角内嵌显示 |
| 记录库 | 运行记录存入 SQLite (`backend/ate.db`)，支持按 UUT 名称查询历史测试记录 |
| 测试装备清单 | 页眉「测试装备」标签进入子页面，卡片式展示测试台硬件列表（名称/型号/厂商/状态），易损件标注并显示剩余使用天数（绿>30 天 / 黄 7-30 天 / 红<7 天）；页眉展示测试台具体名称（title + SPMTS+12 位唯一编号，来自 `backend/testresource/equipment_demo.xml`） |
| 历史测试记录页 | 页眉「测试记录」标签进入独立页面，表格展示规范字段：日期/批次/测试项目/项目/设备编号/UUT/UUT类型(PN)/操作员工号/结果(OK·NOK)/耗时/报告链接；支持按 日期/项目/批次/UUT 组合筛选；通过(OK)绿色加粗、不通过(NOK)红色加粗字色差异 |
| 默认测试台 | 后端启动时自动登记 **SPM读头动态测试台**（预设类型 `SPM-RH-DYN-01`，编号 `SPMTS202609120001`，归属 `1000-A / SPM`），开箱即已注册且自带自检清单：7 台设备（6 台可编程），含 **Fluke 8846A 数字万用表**（`192.168.10.42:3490`）、泰克 MSO54 示波器、ITECH IT6332A 可编程电源、ITECH IT8512A+ 电子负载、Keysight 33500B 信号源、读头转速控制盒（COM6@115200）与读头装夹夹具。种子逻辑**幂等**且删除后不自动复活；需要时在「测试台导航」页点「↺ 恢复默认测试台」重建 |
| 测试台导航页 | 左侧导航栏「测试台导航」进入：顶部为指标块（预设类型/已注册台数/纳管设备/自检状态）；已注册测试台以**独立标签**形式排列，标签上带连通性状态色点，点击标签查看该测试台的产品/子系统归属、产线工位信息、设备连接配置（IP:端口 / 串口@波特率）与最近一次自检清单；支持重新自检、模拟自检、编辑配置、删除 |
| 新增测试台注册 | 三步注册向导：**第一步**按装备树结构**三级级联选型**（产品名称，如 `1000-A` → 子系统，如 `SPM/WA/SD` → 预设测试台类型），选中自动获取该类型标准 BOM 清单，并填写产线/工位信息；**第二步**填写 BOM 中可编程设备（示波器/可编程电源/可编程电流源/电子负载/DAQ/串口等）的配置并保存；**第三步**注册并验证设备连通性，输出逐项自检清单（配置类 + 设备类，含 TCP 连通时延/失败原因/跳过原因） |
| 装备属性配置页 | 左侧导航栏「装备属性配置」进入：设备清单**直接来源于各测试台注册时该类型 BOM 清单中的设备**（按测试台分组、可切换/搜索/筛选）；卡片展示接口类型、当前连接资源（IP:端口 / 串口@波特率 / 资源地址）、协议、用途与配置状态；**可编程设备右键**弹出菜单：修改设备属性 / 恢复 BOM 默认值 / 复制资源地址 / 查看变更历史（双击卡片也可直接打开属性弹窗）；非可编程设备属性只读。设备属性修改走 `PATCH` 接口，带格式校验（IP/端口/串口/波特率/资源地址）与同台地址冲突拦截，每次变更留痕（最多 10 条） |
| 装备运维页（三栏控件） | 装备属性配置页顶部提供三栏切换：**装备属性配置**（设备清单 + 测试台导出）、**测试台装备自检**（初始化 / 终止 / 自检三个动作与报告弹窗）、**装备报告中心**（报告落盘目录 + 历史报告清单）；切换同步地址栏（`#/equipment?tab=selfcheck`、`#/equipment?tab=reports`），刷新后停留在同一栏 |
| 测试台导出（待注册 / 已注册） | 「装备属性配置」栏底部两块：①**待注册测试台导出**——列出草稿状态测试台，一键导出；②**已注册测试台导出**——列出全部已注册测试台，可勾选（含全选 / 清空选择）后「导出选中」或「导出全部已注册」。两者都生成 `ate.testbench.export.v1` JSON（含每台设备型号/接口/IP·端口·串口等连接参数），同时落盘到报告目录并触发浏览器下载，弹窗显示作用范围（待注册/已注册/全部/选中）与状态构成 |
| 测试台装备自检 | 「测试台装备自检」栏：**测试台装备初始化 / 终止 / 自检**三个按钮（作用于所选测试台，支持「离线模拟」与「真实探测」两种模式；真实模式建链并下发 SCPI 指令前二次确认）——初始化按设备类别下发复位/远程/安全态动作，终止做输出关闭/输入关闭/转速归零/退回本地，自检复用注册自检逻辑（13 项）；③执行后**界面直接弹出自检报告**（iframe 渲染完整报告：结论、汇总、逐项清单、设备动作与指令、耗时） |
| 装备报告区 | 「装备报告中心」栏的「报告区」：显示报告落盘目录（`backend/logs/testbench`）、已保存文件数与占用空间，一键**打开本地资源管理器**（Windows 定位到目录，可选「在资源管理器中定位」直接选中某个报告文件）；下方**历史硬件自检报告**表按时间倒序列出所有已保存报告（类型/测试台/模式/结论/大小），支持按「自检 · 初始化 · 终止 · 导出」过滤、界面内查看报告或新窗口打开 |
| 装备助手页 | 左侧导航栏「装备助手」进入：以**卡片形式**提供控制设备的小工具（首期：数字示波器 · 泰克 MSO54 方案，另预留万用表/可编程电源/电子负载工位）。**单击或双击卡片选中**，再点右上角「运行工具」启动；示波器工具支持绑定已注册测试台中的示波器设备、连接与 `*IDN?` 识别、运行/停止/单次采集、自动设置、时基与通道调整、波形读取（Canvas 绘制 + 网格/量程标注）与参数测量（Vpp/频率/周期/有效值/上升时间…），并实时显示 SCPI 命令日志 |
| 装备助手 · 仿真信号源 | 「一键仿真」不选设备也能进入仿真（后端自动使用内置仿真信号源 `SIM-SCOPE`，不依赖注册库）：面板提供 6 种波形（正弦波/方波/三角波/锯齿波/直流/噪声）、频率滑块（10 Hz – 1 MHz 共 16 档）、幅度 Vpp、偏置、噪声、屏内周期（1/2/5/10），按频率与屏内周期**自动配好时基与通道档位**，拖动滑块即下发并刷新波形与测量值 |
| 装备助手 · 离线仿真 | 示波器工具提供「仿真信号源」模式：无仪器时生成与真实命令同构的波形与测量值，可完整演练操作流程（测量值标签标注“模拟”）；真实模式下连不上设备且允许降级时，自动切换为模拟并明确提示，不伪装成实测数据 |
| TPS 运行环境 | TPS 实际运行环境位于 ATE 安装运行目录下的 `workspace/`（**不存在由程序自动创建**）：`workspace/conftest.py` 是程序提供的**公共 conftest**，负责导入 TPS 需要的装备信息、各种测试阈值与所需设备 driver；`workspace/<TPS 名称>/run_<任务号>/` 是**运行时临时副本**——每次运行把整个 TPS 复制一份 temp 版本进去执行，源 TPS 不被污染 |
| TPS 三字段 | TPS 清单由三个主要字段构成：`testconfig`（测试用例的阈值设置——测试套中单个用例的阈值上限/下限子字典）、`device_config`（测试用例使用的设备信息子字典，按别名映射到注册库设备）、`cmd_suit`（测试套排列子字典，其中每一个测试用例对应 TPS `testcase/` 中提供的一个实现）；setup/teardown 与 cmd_suit 归一化成顺序执行的 pytest 节点 |
| 设备 driver 配置（SQLite） | 测试台注册完成后，设备连接参数与解析出的 driver 规格以**表单形式保存到 SQLite**（`backend/ate.db` 的 `bench_registry` / `device_registry` 表；注册库 JSON 仍是唯一事实源，每次变更自动重建投影）；TPS 运行时由公共 conftest 从 SQLite 导入，**改址/换驱动无需改 TPS** |
| 阈值自动判定 | 用例返回值经清单 `checks` 映射到 `testconfig` 阈值自动判定，越限即失败并记录 实测值/下限/上限/单位；结果落盘 `run_result.json`（汇总 + 逐用例 + 每台设备解析出的 driver 规格） |
| TPS 运行环境面板 | 「装备属性配置」栏新增两块：**TPS 运行环境**（workspace 路径、公共 conftest 版本、SQLite driver 配置统计、可解析驱动规格表 + 「初始化/校验」「同步 driver 配置」按钮）与 **TPS 运行目录**（按 TPS 包列出 workspace 下的运行目录、用例通过数与占用空间、清理旧运行目录） |
| 界面结构 | 左侧垂直导航栏（品牌区 + 当前测试台名称/编号 + 测试执行/装备属性配置/装备助手/测试记录/测试台导航五个标签，**测试台导航固定在最下**，窄屏自动变顶部横向导航）；右侧为内容区。工位信息只保留产线/工位/物理位置/备注，不采集班次与人员信息 |
| 界面主题 | 紫色 + 白色背景的工业风主题（工业紫 `#6d28d9` 主色、白色面板、淡紫网格底纹、斜纹分隔条）；所有颜色集中在 `frontend/src/style.css` 的 CSS 变量中，改主题只改这一处 |
| 装备助手设计要点 | 工具以**卡片注册表**形式提供（后端 `TOOL_CARDS` 定义卡片元信息与可用性，前端按卡片渲染）：新增工具只需在后端加一张卡片 + 一个处理路由，前端结构与导航无需改动 |
| 规范测试报告 | 报告含规范字段：日期、批次（UUT 属性，`uut_profiles.json` 预置）、测试项目（TPS 名）、项目（单条用例）、设备编号（测试台）、UUT 类型（SN 前 10 位 PN）、操作员工号（f010392）、结果（OK/NOK）、耗时（秒）、详情、报告链接 |

## 环境要求

- Python 3.10+（开发环境为 3.13）
- Node.js 18+（开发环境为 22）

## VSCode + venv 开发环境（推荐）

项目已预置 `.vscode/` 配置（解释器指向 `backend/.venv`、pytest 集成），venv 已创建并装好依赖。用 VSCode 打开 `D:\ATE` 即可直接使用：

1. 安装扩展 `ms-python.python`（以及可选的 `ms-python.vscode-pytest`）
2. 打开项目后，VSCode 自动选用解释器 `backend\.venv\Scripts\python.exe`（Python 3.13.7）
3. 在「测试」面板可直接运行 `backend/test_cases` 下的 6 条 pytest 用例

venv 相关命令（如需重建）：

```bat
cd D:\ATE
C:\Users\0.0\AppData\Local\Programs\Python\Python313\python.exe -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

> 说明：启动脚本（`start_all.bat` / `run_backend.bat`）会优先使用 `backend\.venv`，不存在时回退到系统 `python`。

## 快速启动

### 方式一：一键启动（推荐）

双击 `D:\ATE\start_all.bat`，脚本会自动：
1. 安装后端依赖（fastapi / uvicorn / pytest）
2. 首次运行自动安装前端依赖（npm install）
3. 弹出两个窗口分别启动前后端

启动完成后浏览器打开 <http://127.0.0.1:5173>。

### 方式二：手动启动

**后端**（终端 1）：

```bat
cd D:\ATE\backend
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

后端地址 <http://127.0.0.1:8000>，接口文档 <http://127.0.0.1:8000/docs>。

**前端**（终端 2）：

```bat
cd D:\ATE\frontend
npm install
npm run dev
```

前端地址 <http://127.0.0.1:5173>（开发服务器已配置 `/api` 代理到后端 8000 端口）。

### 方式三：Docker 部署（工控机/服务器）

项目已提供 `Dockerfile`（前后端各一）+ `docker-compose.yml`，一条命令部署到 Linux 工控机或装有 Docker 的机器：

```bash
cd D:\ATE
docker compose up -d --build
```

浏览器打开 `http://<工控机IP>:8080`。测试用例目录（`backend/test_cases`）与日志（`backend/logs`）通过卷挂载，客户可直接增删用例、重建容器不丢数据。

详细步骤、离线部署（内网无外网）与 Windows 工控机替代方案见 [docker-deploy.md](docs/docker-deploy.md)。

> 注意：Windows 老系统工控机（Win7/Win10 家庭版）无法装 Docker Desktop，请改用下面的**单文件安装包**方案。

### 方式四：单文件安装包（Windows 工控机，推荐）

前后端 + 嵌入式 Python 运行时打成一个安装包，目标机**无需 Python / Node / Docker / 联网**，双击完成部署：

| 产物 | 用法 |
| --- | --- |
| `ATE_Setup-<版本>.exe` | 双击向导安装（选目录 → 完成 → 自动建快捷方式），也支持静默：`ATE_Setup-1.0.exe /S /DIR=D:\ATERunner` |
| `ATERunner-portable-<版本>.zip` | 解压即用的绿色包：双击 `start-ate.bat` 启动，或 `install.bat` 一键部署到 `C:\ATERunner` |

安装后启动器会自动：自检（目录可写 / 前后端文件 / 运行时 / 端口）→ 起后端 → 用浏览器打开 `http://127.0.0.1:8000`。前端由后端同端口托管（`web/`），测试数据（`logs/`、`workspace/`、`app/ate.db`）全部落在安装目录内。

构建（在构建机上执行，产物输出到 `D:\ATE_DIST\`）：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_package.ps1 -Version 1.1.0
# 仅改后端时加速：-SkipFrontend -ReuseRuntime
```

完整安装步骤、静默部署参数、验收清单与现场排错见 [deployment-guide.html](docs/deployment-guide.html)（Markdown 版：[deployment-guide.md](docs/deployment-guide.md)）。

## 接口清单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/license/status` | 查询 License 状态（valid / not_found / invalid） |
| POST | `/api/license/import` | 导入 License 文件内容（Ed25519 验签 + 有效期 + 可选设备绑定） |
| POST | `/api/login` | 登录（无有效 License 时拒绝，返回 license_required） |
| GET | `/api/testcases` | 获取可执行用例列表（pytest --collect-only） |
| POST | `/api/tps/{id}/run` | 执行 TPS（需 12 位英数 UUT 名称），返回 task_id；v2 包会先在 workspace 生成临时副本再逐步执行，支持 `bench_id`（指定测试台）与 `mode`（`simulate` 离线仿真 / `real` 真机） |
| GET | `/api/tps-tasks/{id}` | 查询 TPS 执行进度 / 逐步状态 / 报告路径 |
| GET | `/api/reports/{file}` | 获取生成的测试报告 HTML |
| GET | `/api/tree` | 装备树（项目 → DUT → TPS 三级） |
| GET | `/api/tps/by-uut?uut=` | 按 UUT 名称查询最近一次使用的 TPS（记录库匹配） |
| POST | `/api/tps/import` | 导入 TPS JSON 文件（校验结构后保存到 testresource 立即生效） |
| POST | `/api/ai/analyze` | AI 测试失败分析：读取任务测试日志+报告，调用配置的 AI API（OpenAI 兼容）返回分析 |
| GET | `/api/records?uut=&date=&project=&batch=` | 查询测试记录：支持按 UUT（12 位英数）/ 日期（YYYY-MM-DD）/ 项目或 TPS 名称（模糊）/ 批次（模糊）组合过滤，数据存于 SQLite |
| GET | `/api/equipment` | 测试装备清单：测试台 title/serial（SPMTS+12位数字）+ 硬件列表（易损件含剩余天数） |
| GET | `/api/uut/{sn}` | 按 SN 查询 UUT 属性（批次号等；当前预置 `uut_profiles.json`，后续可切远端） |
| GET | `/api/testbench/presets` | 预设测试台类型列表（含标准 BOM 概览：设备数/可编程数/网口数/串口数） |
| GET | `/api/testbench/tree` | 预设测试台类型**三级级联树**：产品名称 → 子系统 → 预设测试台类型（前端下拉菜单数据源，由预设自身的 product/subsystem 字段聚合得出） |
| GET | `/api/testbench/presets/{id}` | 预设测试台类型详情（完整 BOM：型号/厂商/接口/默认 IP·端口·串口/协议/是否程控） |
| GET | `/api/testbench/overview` | 测试台导航页指标：预设类型数/产品数/子系统数/已注册数/草稿数/设备总数/可编程总数/自检通过情况 |
| GET | `/api/testbench/devices` | 装备属性配置页数据源：各测试台 BOM 清单中的设备（拍平，带 `bench_id` 等测试台上下文与 `resource` 资源串） |
| POST | `/api/testbenches/restore-defaults` | 重建默认测试台（删除后恢复预置演示数据；已存在时不重复创建，幂等） |
| GET | `/api/testbench/pending` | 待注册（草稿状态）测试台清单：编号/预设类型/设备与配置统计/当前步骤 |
| POST | `/api/testbenches/export` | 导出测试台为 `ate.testbench.export.v1` JSON：`status=registered` 导已注册 / `draft` 导草稿 / `all` 全导（缺省 = 待注册，兼容旧行为）、`bench_ids` 指定台（`selected`）、`include_registered` 等价 `all`；响应含 `scope`/`scope_label`/`by_status` 分状态计数，落盘报告目录并回传内容供下载 |
| POST | `/api/testbenches/{id}/init` | 测试台装备初始化（`mode=real\|simulate`）：按设备类别复位/远程/安全态动作，生成报告 |
| POST | `/api/testbenches/{id}/teardown` | 测试台装备终止：输出关闭/输入关闭/转速归零/退回本地控制，生成报告 |
| POST | `/api/testbenches/{id}/selfcheck` | 硬件自检并落盘 HTML 报告（结果同时写回注册库 `verification` / `verify_history`） |
| GET | `/api/testbench/reports` | 历史报告清单（可按 `kind`=selfcheck\|init\|teardown\|export、`bench_id` 过滤，带结论/模式/大小/路径） |
| GET | `/api/testbench/reports/dir` | 报告目录信息：绝对路径 / 文件数 / 占用空间 |
| GET | `/api/testbench/reports/{file}` | 单份报告内容（HTML，界面内 iframe 或新窗口查看） |
| POST | `/api/testbench/reports/open` | 调系统资源管理器打开报告目录（传 `filename` 则定位到该报告） |
| PATCH | `/api/testbenches/{id}/devices/{device_id}` | 修改可编程设备连接属性（`host`/`port`/`serial_port`/`baudrate`/`address`/`channel`/`protocol`/`note`，或 `reset_defaults:true` 恢复 BOM 默认值） |
| GET | `/api/tools` | 装备助手工具卡片清单（名称/说明/协议/能力/可用性） |
| GET | `/api/tools/oscilloscope/bindings` | 可绑定的示波器设备（来自注册 BOM，示波器优先排序；末尾附内置仿真信号源 `__sim__` / `sim-scope`，无需硬件） |
| POST | `/api/tools/oscilloscope/connect` | 建立示波器会话（`mode=real\|simulate`，真实连不上可 `allow_fallback` 自动降级） |
| POST | `/api/tools/oscilloscope/action` | 示波器操作：`identify`/`run`/`stop`/`single`/`autoset`/`timebase`/`channel`/`sim_signal`（仿真信号源：type/freq/vpp/offset/noise/cycles/auto_timebase）/`acquire`/`measure`/`frame`/`close` |
| GET | `/api/tools/oscilloscope/state` | 会话状态（连接态/IDN/时基/通道/SCPI 命令日志） |
| GET | `/api/testbenches` | 已注册测试台列表（标签视图用：名称/编号/产线工位/设备数/自检摘要） |
| GET | `/api/testbenches/{id}` | 测试台详情（完整设备配置 + 最近一次自检清单） |
| POST | `/api/testbenches` | 创建/更新测试台（三步流程每步均可调用；`status=registered` 为正式注册，含编号格式、重号、工位完整性校验） |
| DELETE | `/api/testbenches/{id}` | 删除测试台 |
| POST | `/api/testbenches/{id}/verify` | 注册第三步：设备连通性自检（`mode=real` TCP/串口真实探测，`mode=simulate` 离线模拟），返回自检清单并落库 |
| GET | `/api/runtime/workspace` | TPS 运行环境总览（workspace 绝对路径 / 公共 conftest 是否存在与版本 / 运行目录列表） |
| POST | `/api/runtime/workspace/init` | 初始化 workspace（不存在则创建）并部署/更新公共 conftest（`force` 强制覆盖，覆盖前自动备份） |
| GET | `/api/runtime/drivers` | 驱动规格清单（工厂可解析的 `key` / 名称 / 设备类别 / 可用动作） |
| GET | `/api/runtime/db` | SQLite 里的 driver 配置投影（测试台数 / 设备数 / 库路径 / 最近同步时间） |
| POST | `/api/runtime/db/sync` | 手动把注册库重新投影到 SQLite 并返回统计 |
| GET | `/api/runtime/schema` | TPS v2 结构说明（`testconfig` / `device_config` / `cmd_suit` 字段含义） |
| GET | `/api/tps/{id}/manifest` | 读取 TPS 清单（v2 目录包自动归一化，返回三字段与展开后的执行步骤） |
| POST | `/api/tps/{id}/stage` | 准备运行环境：按 `uut` / `bench_id` / `mode` 生成 workspace 临时副本（含生成的 pytest 入口、`ate_env.json` 与 conftest） |
| POST | `/api/tps/{id}/collect` | 收集用例（pytest --collect-only，只读，不生成运行记录） |
| GET | `/api/tps/{id}/runs` | 历史运行目录（临时副本）列表 + 最近一次结果摘要 |
| POST | `/api/tps/{id}/runs/cleanup` | 清理旧运行目录（`keep` 保留最近 N 次，当前运行目录始终保护） |
| POST | `/api/tps/validate` | 校验 TPS v2 清单结构，返回中文错误清单 |
| GET | `/api/health` | 健康检查 |

## License 授权方案
采用 **Ed25519 非对称签名** 离线授权，适合工控机/内网环境：

- 签发方持有私钥（`backend/tools/private_key.pem`，**不随产品部署**），用 `backend/tools/gen_license.py` 生成 .lic 文件
- 部署端后端仅内置公钥（`backend/license_utils.py` 的 `PUBLIC_KEY_B64`），导入时离线验签，防篡改/防伪造
- License 内容：产品名 / 客户 / 有效期 / 可选设备绑定（device_id 与机器码一致才生效）/ Ed25519 签名
- 生效后写入 `backend/license.dat`，登录前强制校验；无有效 License 时前端进入注册页，通过文件选择导入

预置授权文件：`D:/ATE_ENV/ate_runner.lic`（有效期至 2036-08，未绑定设备，可直接导入使用）。

重新签发（签发方操作）：

```bat
cd D:\ATE\backend\tools
..\.venv\Scripts\python.exe gen_license.py --customer "客户名" --days 365 --out D:/ATE_ENV/xxx.lic
```

## 远端 TPS 源与导入

- **远端 TPS**：后端通过环境变量 `ATE_REMOTE_TPS_URL` 指定远端 TPS 服务器（默认 `http://127.0.0.1:8999`），在线时优先拉取 TPS 列表/详情（2s 超时）；离线/不可达时自动回退本地 `backend/testresource/` 的默认 TPS，前端显示「● 在线 / ● 离线」徽章并提示保持默认 TPS。
- **UUT 查询 TPS**：左上角输入 12 位 UUT 名称，后端从 SQLite 记录库匹配该 UUT 最近一次使用的 TPS 并自动定位/加载。
- **TPS 导入**：点击「导入 TPS 文件」选择本地 .json，后端校验 id/steps/type/script 等结构后保存到 `testresource/` 并立即加入装备树（重名覆盖）。

## 测试台导航与注册（三步流程）

**页面入口**：左侧导航栏「测试台导航」（`/testbenches`）；新增注册为独立页（`/testbenches/register`），也支持 `?id=xxx` 进入编辑模式。

**三级级联选型**（按装备树结构组织）：产品名称（如 `1000-A`）→ 子系统（如 `SPM` / `WA` / `SD`）→ 预设测试台类型（如 `PB-PWR-01`）；未选中上级时下级不可选，选中类型后自动拉取标准 BOM。数据源为 `GET /api/testbench/tree`（后端由每个预设的 `product` / `subsystem` 字段聚合，单一数据源，无需手工维护两处结构）。

| 步骤 | 用户动作 | 后端行为 |
| --- | --- | --- |
| 第一步 | 三级级联选型（产品 → 子系统 → 预设测试台类型）→ 自动获取标准 BOM → 填写产线/工位/物理位置/备注 | `GET /api/testbench/tree` 返回级联树；`GET /api/testbench/presets/{id}` 返回 BOM；`POST /api/testbenches`（step=1, status=draft）落库为草稿 |
| 第二步 | 逐台填写可编程设备配置（网口：IP+端口+协议+VISA 资源串；串口：串口号+波特率） | `POST /api/testbenches`（step=2）保存；后端以预设 BOM 为基准合并，前端无法覆盖型号/用途等 BOM 元信息 |
| 第三步 | 选择验证方式（真实探测 / 离线模拟）→ 注册并验证 | `POST /api/testbenches`（step=3, status=registered）+ `POST /api/testbenches/{id}/verify`；返回配置类与设备类逐项自检清单 |

**自检清单构成**：配置类 6 项（编号格式 SPMTS+12 位数字、名称、产线工位完整、可编程设备配置完整、设备地址无冲突、必备设备已配置）+ 每台设备 1 项（网口设备 TCP 连通探测并记录时延，串口设备尝试打开串口，USB/GPIB/无接口设备标注为需人工确认）。其中 USB / GPIB 等不支持自动探测的接口，以“是否登记资源地址”判定配置完整度，连通性仍由人工确认并记为跳过。整体结论：`fail`（存在失败项）> `warn`（存在跳过项）> `pass`。

**数据文件**：`backend/testresource/testbench_presets.json`（预设类型 + 产品/子系统 + 标准 BOM，只读）、`backend/testresource/testbenches.json`（已注册测试台，含设备配置、自检结果与最近 10 次自检历史；`seeded_defaults` 记录已播过种的预设，保证删除后不复活）。

## 测试台装备运维（初始化 / 终止 / 自检 / 报告区）

在「装备属性配置」页顶部，除设备卡片外还提供面向测试台整体的运维动作（后端模块 `backend/testbench_lifecycle.py`）：

| 能力 | 说明 |
| --- | --- |
| 测试台导出（待注册 / 已注册） | **待注册**：列出草稿状态测试台；点「导出待注册测试台」生成 JSON。**已注册**：列出全部已注册测试台，可勾选后「导出选中」或「导出全部已注册」（不勾选则导全部）。两者都生成 `ate.testbench.export.v1` JSON（含设备清单与连接参数），**同时落盘报告目录 + 浏览器下载**，弹窗显示条数、状态构成（已注册/草稿）、作用范围、文件名与落盘路径 |
| 测试台装备初始化 | 按设备类别下发标准动作：示波器 `*RST`/默认时基、万用表复位+直流电压+自动量程、电源/负载/信号源输出关闭+安全态、运动控制盒串口握手+转速归零；逐条记录指令与结果 |
| 测试台装备终止 | 输出关闭 / 输入关闭 / 转速归零 / 退回本地控制，保证下电前设备处于安全态 |
| 测试台装备自检 | 复用注册自检逻辑（配置类 6 项 + 每设备 1 项），结果写回注册库并生成 HTML 报告 |
| 界面显示报告 | 执行完任一动作后直接弹出报告：结论徽标、汇总数字、逐项清单、设备动作与指令、耗时；支持新窗口打开 |
| 报告区 | 显示报告目录路径与占用空间，一键「打开本地资源管理器」（或定位到具体报告文件）；历史报告表可按类型过滤、界面内查看 |

- **两种运行模式**：`离线模拟` 只生成与真实流程同构的步骤记录，**不下发任何指令**，结果在报告中标注“模拟”；`真实探测` 才真正建链下发 SCPI/串口指令（前端会二次确认）。真实模式失败时如实记失败原因（如现场无仪器），不冒充成功。
- **结论只看可编程设备**：夹具/探针等非程控设备标为信息项，不会把整体结论拉成“待人工确认”。
- **报告落盘位置**：`backend/logs/testbench/`（`init_*` / `teardown_*` / `selfcheck_*.html`、`export_*.json`），并在 `index.json` 登记清单；报告目录可在界面上用资源管理器直接打开。

**主题与配色修改指南**：主色、底色、边框、语义色（成功/警告/失败）全部定义在 `frontend/src/style.css` 的 `:root` 变量中（`--accent` / `--accent-rgb` / `--bg` / `--panel` / `--border` / `--ok` …）；各页面 scoped 样式只用变量与 `rgba(var(--accent-rgb), 透明度)` 形式，**改主题只需改 `style.css` 一处**。左侧导航项（名称/副标题/图标/顺序）在 `frontend/src/App.vue` 的 `NAV` 数组中配置。

> 真实探测依赖现场网络：工控机调试阶段若无仪器，可用「模拟自检（离线演示）」走通注册流程，结果会标注“模拟”，不会冒充真实探测结果。

## TPS 运行环境（workspace / 公共 conftest / 设备 driver 配置）

TPS 不是直接在 `backend/testresource/` 里跑，而是**先复制一份临时副本到 workspace 再跑**；装备信息、阈值与 driver 全部由程序提供的公共 conftest 注入（后端模块 `backend/tps_runtime/` + `backend/drivers/` + `backend/ate_db.py`）。

### 1. 目录与流程

```
D:\ATE\workspace\                     # ATE 安装运行目录下的 workspace（不存在自动创建）
├── conftest.py                       # 公共 conftest：导入装备信息 / 阈值 / driver（版本号自动同步）
└── SPM读头动态测试\                   # 以 TPS 名称命名的目录
    └── run_df0d1b653f88\              # 运行时临时副本（run_<task_id>）
        ├── tps.json                  #   TPS 清单副本
        ├── testcase/                 #   用例实现副本（原样复制）
        ├── test_tps_generated.py     #   由 cmd_suit 生成的执行入口（setup / case / teardown）
        ├── ate_env.json              #   本次运行环境（任务/UUT/测试台/模式/后端路径/库路径）
        ├── conftest.py               #   公共 conftest 副本（运行时读同目录 ate_env.json）
        ├── run_result.json           #   运行结果（汇总 / 逐用例 / 阈值判定 / 设备 driver）
        └── run_log.txt               #   pytest 输出
```

| 步骤 | 行为 |
| --- | --- |
| ① 取清单 | `tps_runtime.read_tps()` 读 `testresource/<id>/tps.json`（v2）或单文件 JSON（v1 兼容） |
| ② 建环境 | `stage()` 校验清单 → 确保 workspace 与公共 conftest 就绪 → `copytree` 临时副本 → 生成 `test_tps_generated.py` / `ate_env.json` → 清理超保留策略的旧运行目录 |
| ③ 配测试台 | 按 TPS 清单 `bench.preset_id` 自动匹配已注册测试台（也可用 `bench_id` 指定），把测试台编号/模式写入 `ate_env.json` |
| ④ 执行 | 每个步骤在 run 目录里跑 `pytest <节点> -v`（cwd=run 目录），逐步回传进度；公共 conftest 自动注入 `ate_ctx` 夹具 |
| ⑤ 判定与落盘 | 用例返回值 → `checks` 映射 `testconfig` 阈值 → 越限即失败；`run_result.json` + `run_log.txt` 落盘，HTML 报告新增「运行环境 (workspace)」栏 |

### 2. 公共 conftest.py 提供什么

| 注入内容 | 说明 |
| --- | --- |
| 装备信息 | `ate_bench` / `ate_devices`：从 SQLite `bench_registry` / `device_registry` 读本次测试台与设备（含 IP·端口·串口·driver 规格），TPS 里不写死地址 |
| 测试阈值 | `ate_thresholds`：本次 TPS 的 `testconfig` 子字典；`ctx.check(别名, 实测值)` 自动判定上下限并给出中文提示 |
| 设备 driver | `driver(别名)` / `command(别名, 动作, **参数)`：由 `device_config` 别名 → 注册库设备 → 工厂解析出具体 driver（示波器/万用表/电源/电子负载/信号源/运动控制/夹具），统一返回 `{ok, value, detail}` |
| 结果记录 | `ate_runner` 与 pytest 钩子：逐用例写 `run_result.json`（阈值明细、测量值、driver 规格），异常/中断也会兜底落盘 |
| 运行环境 | 读同目录 `ate_env.json`（或环境变量 `ATE_ENV_FILE`），据此把后端目录加入 `sys.path`、设置 `ATE_DB_PATH`，因此**公共 conftest 升级只需换一个文件** |

### 3. TPS 三个主要字段

| 字段 | 作用 | 示例 |
| --- | --- | --- |
| `testconfig` | 测试用例的阈值设置（测试套中单个用例的阈值上下限子字典） | `{"TC_VDD": {"label": "读头上电电压", "min": 3.135, "max": 3.465, "unit": "V"}}` |
| `device_config` | 测试用例使用的设备信息子字典（别名 → 注册库设备/角色） | `{"scope": {"device_id": "rh-scope"}, "dmm": {"role": "万用表"}}` |
| `cmd_suit` | 测试套排列子字典，每一条对应 `testcase/` 中一个用例实现 | `[{"no": 1, "id": "TC001", "name": "读头上电电压", "case": "testcase.power::power_on_voltage", "checks": {"VDD": "TC_VDD"}}]` |

- 一个用例函数的签名是 `def 用例(ctx, **params) -> None | float | dict`：返回 `float` 时按清单 `checks` 里第一个键判定，返回 `dict` 时按 `{检查项: 值}` 逐项映射到 `testconfig`。
- `setup` / `teardown` 与 `cmd_suit` 一起展开为 7 个顺序步骤（以示例包为例：1 个初始化 + 5 条用例 + 1 个终止），前端进度、日志、报告都按这个顺序展示。
- 清单校验（`POST /api/tps/validate` 或导入时自动）会拦截：阈值上下限非数字/下限大于上限、`cmd_suit` 引用未定义阈值、`case` 不是 `模块::函数` 形式、id 重复、与旧 `steps` 字段混用等。

### 4. 设备 driver 配置为什么放 SQLite

- **注册即入库**：第三步注册完成（或设备属性 PATCH）后，`testbench_registry._save_registry()` 会把注册库全量投影到 SQLite；`bench_registry` 存测试台元信息，`device_registry` 存每台设备的连接参数与 `driver` 规格名（PK = `(bench_id, device_id)`）。
- **运行时只读 SQLite**：公共 conftest 从 SQLite 取设备，而不是让每个 TPS 自己写 IP/端口；现场改址只需在「装备属性配置」页改一次（或重新注册），**TPS 代码零改动**。
- **仍以 JSON 注册库为唯一事实源**：SQLite 是可随时重建的投影（`POST /api/runtime/db/sync`），删库重建不影响授权与注册数据。
- **驱动工厂**：`drivers/factory.py` 的 `DRIVER_SPECS` 按设备字段（厂商/型号/类别/接口）打分选择驱动实现，先具体（泰克 MSO5 示波器）后兜底（通用 SCPI）；未识别设备走 `passive-device`。

### 5. 示例与验证

- 示例 TPS 包：`backend/testresource/spm_rh_dyn/`（SPM 读头动态测试：3 个字段 + 5 条用例 + `testcase/` 三个实现模块），对应默认测试台 `SPMTS202609120001`（预设 `SPM-RH-DYN-01`）。
- 一键走通：`start_all.bat` → 执行页选 TPS → 输入 12 位 UUT → 开始测试；运行目录出现在「装备属性配置 → TPS 运行目录」栏。
- 无硬件时：TPS 默认 `mode=simulate`，所有仪器动作走仿真驱动（测量值稳定可复现），阈值判定、报告、`run_result.json` 与真机路径完全同构。

## 测试日志与 AI 失败分析

- **测试日志**：每次 TPS 运行自动生成 `backend/logs/test_logs/tps_<task_id>.log`——含 TPS 信息（名称/UUT/项目/DUT）、每个步骤的时间戳、PASS/FAIL 状态、耗时与 pytest 输出摘要，任务结束写入总结果与耗时。
- **AI 失败分析**：右下角「测试报告」面板点击「🤖 AI 分析」→ 展开「AI 配置」填写 API 地址与 Key（保存在本机浏览器 localStorage，不上传服务器）→ 点击 AI 分析后，后端读取该任务的测试日志 + 报告纯文本，组装分析 prompt，以 OpenAI 兼容 `POST {api_url}/chat/completions` 调用（模型 gpt-4o-mini，Bearer 鉴权），返回失败原因（按可能性排序）与排查建议。
  - 未配置地址/Key、无法连接、HTTP 错误、返回格式异常均有明确中文提示；无需真实 AI 服务也能验证 UI 全流程。

## 如何新增测试用例

在 `backend/test_cases/` 下新建 `test_xxx.py` 文件，按 pytest 规范写用例即可（函数名以 `test_` 开头，或用 `TestXxx` 类）。刷新前端用例列表即可看到新用例，无需改任何代码。

> 提示：想演示「执行失败」状态，可把 test_demo.py 里任意断言的预期值改错，如 `assert 3.3 * 0.95 <= measured_voltage ...` 改成明显越界的读数。

## 进度反馈机制

后端收到执行请求后立即返回 `task_id`，并在后台线程中启动 pytest 子进程，实时解析 `-v` 输出：

- 按测试结果行（`PASSED` / `FAILED` / `SKIPPED`）累计已执行条数，换算进度百分比
- 前端定时轮询 `GET /api/tasks/{task_id}`，刷新状态徽标、进度条与日志控制台
- 进程退出码 0 → `completed`，否则 → `failed`

## 常见问题

- **前端提示无法连接后端**：先启动后端（8000 端口），再刷新前端页面。
- **端口被占用**：`netstat -ano | findstr 8000` 找到 PID 后结束进程，或修改 `vite.config.js` 代理目标与 uvicorn 端口。
- **npm 安装慢**：可设置镜像 `npm config set registry https://registry.npmmirror.com` 后重试。
- **用例列表为空**：确认 `backend/test_cases/` 下存在 `test_*.py`，且后端启动目录为 `backend`。

## 目录结构

```
D:\ATE
├── .vscode/                # VSCode 配置（解释器指向 backend/.venv + pytest 集成）
├── backend/                 # FastAPI 后端
│   ├── main.py              # 服务入口: License/登录/装备树/TPS 执行/记录库
│   ├── testbench_registry.py # 测试台注册与自检模块（预设类型/BOM/注册表/连通性自检/设备属性 PATCH）
│   ├── testbench_lifecycle.py # 测试台装备运维模块（待注册/已注册导出、初始化/终止/自检报告、报告区）
│   ├── instrument_tools.py   # 装备助手模块（工具卡片 + 示波器 SCPI 控制 + 仿真信号源）
│   ├── ate_db.py            # 注册库 → SQLite 投影（设备 driver 配置，TPS 运行时读取源）
│   ├── drivers/             # 仪器驱动层（传输层 / 通用 SCPI / 示波器 / 万用表 / 电源 / 负载 / 信号源 / 运动控制 + 工厂）
│   ├── tps_runtime/         # TPS 运行环境模块（workspace 临时副本 / 公共 conftest / 清单校验 / 运行路由）
│   ├── license_utils.py     # License 校验模块（Ed25519 公钥验证）
│   ├── license.dat          # 导入生效后的本地授权文件（自动生成）
│   ├── tools/               # 签发工具（仅开发/签发方持有）
│   │   ├── gen_keypair.py   #   生成密钥对（一次性）
│   │   ├── gen_license.py   #   签发 .lic 文件
│   │   └── private_key.pem  #   签发私钥（勿随产品分发）
│   ├── requirements.txt
│   ├── run_backend.bat      # 后端启动脚本（优先使用 .venv）
│   ├── .venv/               # Python 虚拟环境（Python 3.13.7）
│   ├── testresource/        # TPS 定义（demo_tps.json 旧格式 + <TPS 包>/tps.json 新格式 + ops/ 环境脚本）
│   │   ├── testbench_presets.json  # 预设测试台类型 + 标准 BOM 清单
│   │   ├── testbenches.json        # 已注册测试台（自动生成）
│   │   └── spm_rh_dyn/             # 示例 TPS v2 包（testconfig / device_config / cmd_suit + testcase/）
│   ├── ate.db               # SQLite 测试记录库（自动生成）
│   ├── test_cases/
│   │   └── test_demo.py      # Demo pytest 用例
│   └── logs/                # 登录日志 + 执行日志 + 报告 + 装备运维报告（自动生成）
│       └── testbench/       # 测试台装备运维产物：初始化/终止/自检报告 + 测试台导出清单 + index.json
├── workspace/               # TPS 运行环境（ATE 安装运行目录下，自动创建）：公共 conftest.py + <TPS 名称>/run_<任务号>/ 临时副本
├── frontend/                # Vue3 前端
│   ├── package.json
│   ├── vite.config.js       # /api 代理配置
│   └── src/
│       ├── api.js           # 后端接口封装（含 License/TPS/记录）
│       ├── router/          # 路由 + 登录守卫
│       ├── views/
│       │   ├── LoginView.vue    # 登录/注册（License 导入）页
│       │   ├── ExecuteView.vue  # 执行页（装备树/步骤/报告/记录）
│       │   ├── EquipmentView.vue      # 测试装备清单页
│       │   ├── RecordsView.vue        # 历史测试记录页
│       │   ├── EquipmentView.vue       # 装备属性配置页（BOM 设备属性 + 右键改址）
│       │   ├── AssistantView.vue       # 装备助手页（工具卡片 + 数字示波器面板 + 仿真信号源控制）
│       │   ├── TestbenchNavView.vue   # 测试台导航页（已注册测试台独立标签 + 产品/子系统归属）
│       │   └── TestbenchRegisterView.vue # 新增测试台三步注册向导（产品→子系统→类型 级联选型）
│       ├── App.vue          # 左侧垂直导航外壳（品牌 + 当前测试台 + 4 个导航标签）
│       └── style.css        # 主题变量（紫色 + 白底工业风，改配色只改这里）
├── start_all.bat            # 一键启动脚本
├── docker-compose.yml       # Docker 编排（backend + frontend）
├── .dockerignore
├── backend/Dockerfile       # 后端镜像（python:3.13-slim + uvicorn）
├── frontend/Dockerfile      # 前端镜像（node 构建 → nginx 托管）
├── frontend/nginx.conf      # Nginx: 静态资源 + /api 反代到后端
├── docs/overview.html       # 项目说明页（浏览器打开）
├── docs/docker-deploy.md    # Docker 部署指南
├── docs/deployment-guide.html # Windows 单文件安装包部署指导（离线双击安装）
├── docs/requirements-equipment-assistant.html # 需求文档：装备属性配置 / 装备助手
├── docs/ate-system-topology.html # 系统网络拓扑图（五层结构、黄区大网/小网、终端接入）
├── docs/ate-system-topology-devices.html # 系统网络拓扑图·带设备图片版（第二~五层附典型设备图片，AI 拟真图；图例在右上角）
├── docs/device-registration-template.html # 设备注册模版说明（字段参考 / 接口校验 / 驱动自动匹配 / 常见错误）
├── docs/ate-device-template.jsonc # 设备注册模版本体（JSONC，逐字段备注；去注释即合法 JSON）
├── docs/driver-sdk-guide.html # 仪器驱动库开发模板与使用说明（契约 / 家族 / 型号包差异面 / 19 项一致性检查）
├── docs/scope-contract-api.html # 示波器契约层接口设计（每个接口的签名 / 参数 / 返回结构 / 单位 / 错误码）
├── docs/scope-generic-interface-design.html # 示波器通用接口设计（依据泰克编程手册重新总结：七段命令族 / 数据通路 / 29 项测量 / v1.3 新增能力）
├── docs/scope-factory-design.html # ★ 示波器型号派发、连接方式与传输后端设计（型号直连无打分 / 网口 + 串口 / 原生栈 + PyVISA / 命令不外露 / 方言对照 18 条）
├── sdk/docs/design.md         # ★ 驱动库设计说明（分层 / 数据模型 / 返回协议 / 错误模型 / 派发 / 连接方式与传输后端）
├── sdk/docs/guidelines.md     # ★ 驱动库设计准则（十二条红线 + 命名规矩 + 评审清单）
├── sdk/docs/user-guide.md     # ★ 驱动库使用手册（仿真 / 网口 / 串口 / 换后端上手 + 方法全表 + 排障）
├── sdk/docs/new-family-guide.md # ★ 新设备家族接入指南（九步设计法 + 万用表示例 + 反模式）
├── sdk/                      # ★ 仪器驱动库 SDK（平台开发人员写通用外设驱动用）
│   ├── ate_drivers/          # 契约层（contract/capabilities/errors/models/endpoint/transport/base）
│   ├── ate_drivers/family/scope.py # 示波器家族契约（动作 / 单位 / 测量算法 / 默认仿真）
│   ├── ate_drivers/vendors/    # ★ 厂商驱动包：tektronix_mso（MSO54/56/58/64）/ rohde_schwarz_mxo（MXO44，别名 MXO4）；均声明 LAN + SERIAL 与 native/visa 双后端
│   ├── ate_drivers/factory.py  # ★ 型号注册表：按设备档案型号精确派发（无打分 / 无模糊匹配 / 无兜底）
│   ├── ate_drivers/endpoint.py  # ★ 连接方式与传输后端层：LAN / SERIAL + native / visa 解析与校验、规范 VISA 资源名
│   ├── ate_drivers/kit/      # 开发工具链：一致性检查 19 项（含 C19 传输后端可声明）/ 清单校验 / 假传输 / CLI
│   ├── ate_drivers/api.py      # ★ 通用顶层接口：Scope 门面 + open_scope()（链路与后端对用户透明，命令不外露）
│   ├── samples/tek_mso5/     # 示例驱动：泰克 MSO5 系列示波器（19/19 通过）
├── tests/test_sdk.py     # 驱动库自测（11 项）
│   └── tests/test_scope_factory.py # ★ 型号派发、连接方式与后端自测（36 项：派发 / 网口+串口 / 双后端 / 命令不外露 / 两家同用例 / 门禁）
├── packaging/               # 打包脚本：前端 + 后端 + 嵌入式 Python → 安装包/绿色包
│   ├── build_package.ps1    # 一键构建（产物：ATE_Setup-<ver>.exe + ATERunner-portable-<ver>.zip）
│   ├── runtime-requirements.txt # 随包携带的运行时依赖（锁版本）
│   ├── launcher/ate_launcher.py # 启动器源码（自检/端口/开浏览器/日志）
│   ├── installer/setup.cs   # 免 Inno 的自包含安装器源码（Windows 自带 csc 编译）
│   ├── installer/ate_runner.iss # Inno Setup 安装脚本（构建机装了 Inno 时优先用）
│   └── portable/            # 绿色包装配：start-ate.bat / install.bat / uninstall.bat / README.txt
└── README.md
```

## 后续可扩展方向

- 用户密码 + Token 鉴权（JWT）
- WebSocket / SSE 实时推送执行日志，替代轮询
- 多任务并行执行队列、执行报告（HTML/JSON 报表）
- 用例分类、批量执行、失败重试
- 对接真实仪器/设备驱动，替换 demo 中的模拟读数
