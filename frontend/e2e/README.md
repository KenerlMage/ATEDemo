# Runner 端到端用例（Playwright）

面向 `frontend/dist`（由后端 8020 端口托管）的浏览器级回归用例。使用系统已安装的 Microsoft Edge 运行，不下载 Playwright 自带浏览器；用例串行执行，避免 Runner 的单实例状态（TPS 任务、记录库、workspace）互相干扰。

## 一、用例清单

| 文件 | 覆盖点 | 前置 | 主要断言 |
| --- | --- | --- | --- |
| `01-shell.spec.js` | 应用外壳：左侧 6 个导航入口、导航跳转、登录用户展示 | 已注入登录态 | 导航链接数量为 6 且 6 个名称均可见；点击「测试记录」后 URL 为 `/records` 且页头「🗂 历史测试记录」可见；外壳用户标识为 `e2e` |
| `02-execute-page.spec.js` | 测试执行页核心区：装备树、执行按钮、分级日志、测试报告 | 后端可列出至少 1 条 TPS | `equipment-tree` 容器与 TPS 节点可见；「执行 TPS」按钮可见且可用；ERROR / WARNING / INFO 三个计数为 `级别 + 数字`；测试报告区与标题可见 |
| `03-tps-import.spec.js` | 导入 TPS：文件选择、扩展名限制、本地导入成功、树内定位 | 后端离线（Controller 不可达） | 隐藏的 `input[type=file]` 的 `accept` 同时含 `.json` 与 `.zip`；点击「导入 TPS」触发 filechooser；上传合法 `tps.v2` 清单后出现 `✅` 成功提示，装备树出现「E2E 导入冒烟 TPS」 |
| `04-manual-decision.spec.js` | 人工确认弹窗（**UI 级**）：导入自备 TPS → 执行 → 弹窗显示 SOP → 判定失败 + 备注 | 无（用例内用 `request` 导入只含 1 条 manual 步骤的 zip，自备 `resource/M1.md`） | 人工确认弹窗标题为「人工外观确认」，SOP Markdown「请逐项确认」渲染；提交失败判定后弹窗关闭，该步结果含「操作员判定失败」与备注原文；任务状态为「执行失败」 |
| `05-assistant-shelf.spec.js` | 装备助手货架：入口跳转、清单来源 chip、卡片/空态、无 JS 报错 | 后端 assistant 接口可用（离线时返回本地缓存/兜底） | 由「装备助手」页点「装备货架」进入 `/assistant/shelf`；来源 chip 文案为 `MockController 下发 / 本地缓存 / 本地兜底清单` 之一；有卡片则首卡可见，无卡片则显示「没有匹配的助手。」；`pageerror` 为空 |
| `06-settings.spec.js`（可选） | 系统设置页渲染 | 已注入登录态 | 标题「系统设置」与「权限矩阵」可见；`pageerror` 为空 |

## 二、启动被测服务

后端必须先把 `frontend/dist` 构建好（见下一节），再在 8020 端口启动：

```powershell
cd D:\ATE\MockRunner\backend

# 离线运行：Controller 指向不可达地址；TPS 列表固定取本地 testresource
$env:MOCK_CONTROLLER_URL = 'http://127.0.0.1:59999'
$env:ATE_TPS_SOURCE = 'local'
# 隔离 TPS 缓存与运行期数据
$env:ATE_TPS_CACHE_DIR = Join-Path $env:TEMP 'ate-e2e-cache'
# 可选：执行 TPS 时不自动拉起置顶监视窗，避免 e2e 期间弹窗干扰
$env:ATE_HUD_AUTO = '0'

.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8020
```

cmd.exe 等价写法：

```bat
cd /d D:\ATE\MockRunner\backend
set MOCK_CONTROLLER_URL=http://127.0.0.1:59999
set ATE_TPS_SOURCE=local
set ATE_TPS_CACHE_DIR=%TEMP%\ate-e2e-cache
set ATE_HUD_AUTO=0
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8020
```

环境变量说明：

* `MOCK_CONTROLLER_URL`：指向不可达地址，等价于「Controller 离线」，导入走本地兜底。
* `ATE_TPS_SOURCE=local`：让 `/api/tps` 始终从 `backend/testresource` 读取。若不设，离线时列表优先取 `tps_cache`，导入用例写入的缓存条目会让后续用例只看到被导入的那条 TPS。
* `ATE_TPS_CACHE_DIR`：TPS 缓存目录，指向临时目录以隔离数据。
* `ATE_HUD_AUTO=0`：执行 TPS 时不自动拉起 Edge 置顶监视窗（仅影响桌面体验，不影响断言）。

## 三、运行方式

```powershell
cd D:\ATE\MockRunner\frontend

# 1) 构建被测产物（后端托管 dist；改前端后必须重新构建，否则新增 testid 不生效）
npm run build

# 2) 全量（等价 npm run e2e）
npx playwright test

# 3) 单文件 / 单个用例
npx playwright test e2e/04-manual-decision.spec.js
npx playwright test --grep "TPS 导入"

# 4) 有头模式（观察浏览器操作过程）
npm run e2e:headed

# 5) 指向其它地址的被测服务（默认 http://127.0.0.1:8020）
$env:ATE_RUNNER_URL = 'http://127.0.0.1:8030'
npx playwright test
```

失败现场：`frontend/test-results/` 下保留截图、`error-context.md` 与 trace；查看 trace：

```powershell
npx playwright show-trace test-results\<用例目录>\trace.zip
```

## 四、登录态注入

Runner 登录成功后把用户名写入 `localStorage.ate_username`，路由守卫（`src/router/index.js` 的 `beforeEach`）据此放行 `/execute` 等页面。e2e 不走登录接口，由 `e2e/auth.setup.js` 的 `seedLogin(page)` 在页面脚本执行前注入：

```js
await page.addInitScript((name) => {
  window.localStorage.setItem('ate_username', name)
}, 'e2e')
await page.goto('/execute')
```

每个 spec 的 `beforeEach` 先调用 `seedLogin(page)`，因此不会落到登录页；`01-shell.spec.js` 额外断言外壳用户标识为 `e2e`。

## 五、用例对页面的最小依赖（新增 testid）

选择器以可见文本为主；以下位置补了 `data-testid`，只用于稳定定位，不改变渲染与业务逻辑：

| testid | 位置 | 用途 |
| --- | --- | --- |
| `current-user` | `App.vue` 侧栏底部用户名 chip | 断言登录态与用户名 |
| `equipment-tree` | `ExecuteView.vue` 装备树面板 | 限定树内查找 TPS 节点 |
| `tps-file-input` | `ExecuteView.vue` 隐藏的 `input[type=file]` | 断言 `accept`、定位导入输入 |
| `step-hold-banner` | `ExecuteView.vue` 单步暂停横幅 | 等待暂停并点「下一步」 |
| `manual-card` | `ExecuteView.vue` 人工确认弹窗卡片 | 断言弹窗结构与判定操作 |
| `log-count-error` / `log-count-warning` / `log-count-info` | `ExecuteView.vue` 日志分级计数按钮 | 断言三个计数存在 |
| `report-panel` | `ExecuteView.vue` 测试报告面板 | 断言报告区存在 |
| `shelf-source-chip` | `AssistantShelfView.vue` 清单来源 chip | 断言来源三态之一 |
| `assistant-card` | `AssistantShelfView.vue` 助手卡片 | 断言卡片渲染（或空态） |

同时修复了一个被用例暴露的前端缺陷：`ExecuteView.vue` 中「新暂停点到达时清空上一次提交态」的 `watch` 原先只看 `requested_at`（秒级）；单步暂停与紧随其后的人工确认落在同一秒时不会触发，人工确认弹窗的按钮会保持禁用。现改为以 `kind + id + requested_at` 作为 watch 键。

## 六、已知限制

1. **只跑系统 Edge**：`playwright.config.js` 固定 `channel: 'msedge'`、`headless: true`，不下载 Playwright 浏览器；Edge 需装在默认路径（本机为 `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`）。
2. **串行执行**：`workers: 1`、`fullyParallel: false`。Runner 的任务表、记录库与 workspace 都是单实例状态，并行会互相干扰。
3. **依赖 dist 构建产物**：被测页面来自 `frontend/dist`，不是 Vite dev server；改前端后未 `npm run build` 时，用例可能命中旧产物（新 testid 找不到）。
4. **人工确认用例是真实 UI 级**（不是接口级）：依赖内置 TPS `spm_rh_dyn` 的两类暂停点（`step=true` 与 `type=manual`）。该 TPS 被移除或改名时，用例需同步调整。
5. **导入用例有落盘副作用**：离线导入会把 `e2e-import-smoke` 写到 `backend/testresource/`，用例结束在 `afterAll` 里尽力删除；若 `ATE_RUNNER_URL` 指向远端机器，则不会清理对端文件。
6. **离线前置**：`03` / `04` 的预期结果按「Controller 不可达 + `ATE_TPS_SOURCE=local`」编写；Controller 在线时导入会转发到 Controller，成功文案与文件落点不同。
7. **未覆盖**：登录页表单与注册流程、真实 Controller 在线导入、`.zip` 导入、装备属性配置 / 测试台导航 / 测试记录筛选等页面交互、生产模式云端上报。
