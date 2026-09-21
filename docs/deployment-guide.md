# ATE Runner 安装指导

面向客户工控机的**离线、双击安装**部署说明。安装包内已经包含前端构建产物、后端程序与嵌入式 Python 运行时，目标机**不需要**安装 Python、Node.js、Docker，也**不需要联网**。

---

## 1. 前置条件

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10 / 11 x64（Windows Server 2019+ 亦可） |
| 权限 | 安装到 `C:\ATERunner` 需管理员权限；绿色便携方式只需目录写权限 |
| 运行时依赖 | 无（Python 3.13 + FastAPI + pytest + cryptography 全部随包携带） |
| 磁盘 | ≥ 1.5 GB 可用（安装后约 70 MB，运行数据随时间增长：日志、报告、TPS 运行副本） |
| 端口 | 默认 TCP 8000（被占用时启动器自动顺延到 8001–8010，也可用 `ATE_PORT` 指定） |
| 网络 | 单机使用无需网络；车间其他电脑要访问界面时，需放行 8000 端口 |
| 浏览器 | Edge / Chrome（仅用于显示界面，服务与界面都在本机） |
| 授权 | 首次使用需导入 lic 文件；预置 lic 放在 `D:\ATE_ENV` 作为备选 |

---

## 2. 交付物

| 文件 | 说明 |
| --- | --- |
| `ATE_Setup-<版本>.exe` | **安装向导（推荐）**：自解压安装器，双击 → 选目录 → 完成，自动创建桌面/开始菜单快捷方式与卸载项 |
| `ATERunner-portable-<版本>.zip` | **绿色便携包**：解压即用，双击 `start-ate.bat` 启动；也可运行 `install.bat` 一键部署到 `C:\ATERunner` |
| `VERSION.txt` | 包内版本与构成说明（构建时生成） |

> 两个包内容完全一致（`app/` 后端 + `web/` 前端 + `runtime/` 嵌入式 Python + 启动器），差别只是部署方式。

---

## 3. 方式 A：安装向导（推荐）

1. 把 `ATE_Setup-<版本>.exe` 拷到目标机（U 盘 / 共享目录均可，无需联网）。
2. 双击运行；出现 UAC 提示时选择“是”。
3. 选择安装目录，默认 `C:\ATERunner`。
   ⚠️ **不要装到 `C:\Program Files` 或 `C:\Program Files (x86)`**：测试数据（`logs\`、`workspace\`、`app\ate.db`）保存在安装目录内，系统保护目录会导致非管理员运行时无法写数据。
4. 等待解压完成（约 2100 个文件、70 MB，通常几秒到半分钟），窗口内会打印进度。
5. 勾选/确认完成后点击“完成”，桌面出现 **ATE Runner** 快捷方式；勾选“立即启动”时会自动打开浏览器。

**批量/静默安装**（多台工控机统一部署时）：

```
ATE_Setup-1.0.250912.exe /S /DIR=D:\ATERunner /NODESKTOP /NOFIREWALL /NOLAUNCH
```

| 参数 | 含义 |
| --- | --- |
| `/S` | 静默模式（无界面，成功返回 0） |
| `/DIR=<路径>` | 指定安装目录（默认 `C:\ATERunner`） |
| `/NODESKTOP` | 不创建开始菜单/桌面快捷方式 |
| `/NOFIREWALL` | 不添加防火墙规则（默认会尝试放行 TCP 8000，需管理员） |
| `/NOLAUNCH` | 安装完不自动启动 |

---

## 4. 方式 B：绿色便携包

1. 把 `ATERunner-portable-<版本>.zip` 解压到目标盘，例如 `D:\ATERunner`（**路径不要含中文以外的特殊字符**，也不要放在只读介质上）。
2. 直接双击 `start-ate.bat`（或同目录的 `ATE_Launcher.exe`）启动。
3. 需要固定安装时，双击 `install.bat`：
   - 复制到 `C:\ATERunner`（可传参指定，如 `install.bat D:\ATERunner`）
   - 创建桌面 + 开始菜单「ATE Runner」快捷方式
   - 注册“控制面板 → 程序和功能”里的卸载项（HKCU，无需管理员）
4. 卸载：双击 `uninstall.bat`（或控制面板卸载）。默认**保留**测试数据，回答 `y` 才连应用目录一起删除。

---

## 5. 安装目录结构

```
ATERunner\
├── ATE_Launcher.exe        启动器（双击入口：自检 → 起后端 → 开浏览器）
├── start-ate.bat           命令行启动入口（无 exe 时兜底）
├── install.bat            便携包一键部署脚本
├── uninstall.bat          卸载脚本（可选保留数据）
├── VERSION.txt            版本说明
├── app\                   后端程序（FastAPI + pytest + 驱动/运行环境模块）
│   ├── main.py            服务入口（同时托管 web\ 前端静态资源）
│   ├── testresource\      测试资源：预设、注册库 testbenches.json、TPS 包
│   ├── drivers\           仪器驱动（工厂 + 传输层）
│   ├── tps_runtime\       TPS 运行环境（临时副本、公共 conftest 模板）
│   └── ate.db             SQLite 投影（装备/驱动配置，可由注册库重建）
├── web\                   前端构建产物（index.html + assets\）
├── runtime\               嵌入式 Python 3.13 + 全部依赖（离线）
├── logs\                  运行日志、测试报告、硬件自检报告、导出清单
└── workspace\             TPS 实际运行环境（按 TPS 名建目录，内含临时副本）
```

**需要备份/迁移的内容**：`app\testresource\testbenches.json`（测试台注册库）+ `app\ate.db` + `logs\` + `workspace\`。

---

## 6. 首次启动自检

启动器会按顺序检查并打印到控制台与 `logs\launcher.log`：

| 检查项 | 失败表现 | 处理 |
| --- | --- | --- |
| 安装目录可写 | “安装目录不可写”并中止 | 换到非系统保护目录，或以管理员身份运行 |
| `app\main.py` 存在 | “缺少后端程序” | 包不完整，重新解包 |
| `web\index.html` 存在 | “缺少前端页面” | 包不完整，重新解包 |
| `runtime\python.exe` | 自动回退到系统 Python | 建议重新解包以恢复离线运行时 |
| 端口可用 | 自动顺延 8001–8010 | 或用 `ATE_PORT=8010` 指定 |
| 已有实例在跑 | 直接打开界面（不重复启动） | — |

启动成功后浏览器会自动打开 `http://127.0.0.1:8000`。手工核对可访问 `http://127.0.0.1:8000/api/health`，正常返回：

```json
{"success":true,"service":"ATE-Backend","status":"running",
 "web_enabled":true,"web_dir":"C:\\ATERunner\\web",
 "base_dir":"C:\\ATERunner\\app","python":"C:\\ATERunner\\runtime\\python.exe"}
```

`base_dir` / `python` 指向安装目录内部 → 说明用的是包内运行时，与目标机环境无关。

---

## 7. 授权（License）

1. 首次打开界面 → 登录页。
2. 本机无 lic 时进入注册页，按提示**选择文件夹导入 lic**（选中包含 lic 文件的目录即可）。
3. 预置 lic 备选路径：`D:\ATE_ENV`（也可以在安装目录旁放一份 `ATE_ENV`）。
4. 导入成功后返回登录页登录即可；lic 与机器绑定，更换工控机需重新申请。

---

## 8. 验收清单（约 5 分钟）

| 步骤 | 期望结果 |
| --- | --- |
| 1. 双击桌面「ATE Runner」 | 黑窗口打印启动日志，浏览器自动打开界面 |
| 2. 登录进入系统 | 左侧导航显示各页面（测试台导航 / 测试台注册 / 装备属性配置 / 装备助手 / 执行 / 记录 等） |
| 3. 装备属性配置 | 能看到默认测试台「SPM 读头动态测试台」与 7 台设备（含 6 台可编程） |
| 4. TPS 运行环境面板 | workspace 路径在安装目录内、公共 conftest 已部署 v2、driver 配置已同步 |
| 5. 执行页 | 选 TPS → 输入 12 位 UUT（示例 `DEMOUUT00001`）→ 开始测试 → 步骤 7/7 通过 |
| 6. TPS 运行目录面板 | 出现 `run_<任务号>` 临时副本，结果徽标显示通过 |
| 7. 装备报告中心 | 能看到本次测试报告与硬件自检报告；“打开本地资源管理器”能定位到报告目录 |

---

## 9. 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| 双击没反应、界面没起来 | 看 `logs\launcher.log`；被安全软件拦截时加入白名单后重试 |
| 提示端口被占用 | 启动器自动换端口（日志会写明最终端口）；也可 `set ATE_PORT=8020` 后启动 |
| 浏览器打不开 / 显示旧页面 | 手动访问 `http://127.0.0.1:<端口>`；用 `Ctrl+F5` 强刷 |
| 车间其他电脑打不开界面 | 放行防火墙 TCP 8000；确认用本机 IP（`ipconfig`）访问；服务默认监听 `0.0.0.0` |
| 只允许本机访问 | 用 `start-ate.bat --host 127.0.0.1` 启动 |
| 提示安装目录不可写 | 换到 `D:\` 或用户目录；或右键以管理员身份运行 |
| 控制台中文乱码 | 仅显示编码问题，数据与界面不受影响 |
| 界面在但接口 404 | 后端未启动或被旧进程占用，确认 `logs\launcher.log` 里的启动端口 |
| TPS 运行报 pytest 相关错误 | 确认是用包内 `runtime\python.exe` 运行（勿在系统 Python 里跑后端） |
| 换机后登录失败 | lic 与机器绑定，重新导入对应机器的 lic |
| 磁盘占用越来越大 | `workspace\` 里的运行副本默认保留最近 5 次，可在「TPS 运行目录」面板点“清理旧运行目录” |

---

## 10. 升级与卸载

**升级**（保留测试数据）

1. 关闭正在运行的程序（关掉启动器黑窗口）。
2. 用新版安装包安装到**同一目录**（`/S /DIR=...` 亦可），或把新版便携包的 `app\`、`web\`、`runtime\` 解压覆盖进去。
3. `logs\`、`workspace\`、`app\ate.db`、`app\testresource\testbenches.json` 都会保留。

**卸载**

- 控制面板 → 程序和功能 → ATE Runner → 卸载（或运行 `uninstall.bat`）。
- 被询问“是否同时删除测试数据”时：选**否**保留历史记录与报告；选**是**则连 `logs\`、`workspace\`、`ate.db` 一起删除。

---

## 11. 从源码重新打包（开发/交付侧）

```powershell
# 在构建机上（需 Node.js + Python 3.13 + 网络；只需一台）
powershell -ExecutionPolicy Bypass -File packaging\build_package.ps1 -Version 1.1.0
# 只改后端、前端没动时加速：
powershell -ExecutionPolicy Bypass -File packaging\build_package.ps1 -Version 1.1.0 -SkipFrontend -ReuseRuntime
```

构建脚本会依次：构建前端 → 组装 `app/`、`web/` → 下载并配置嵌入式 Python + 安装依赖到 `runtime/` → 生成启动器 exe（需 PyInstaller）→ 清理构建机残留 → 产出便携 zip → 编译安装向导 exe（优先 Inno Setup，没装则用 Windows 自带 csc 编译自带 payload 的单文件安装器）。

产物默认输出到 `D:\ATE_DIST\`：

| 产物 | 大小（参考） |
| --- | --- |
| `ATERunner-portable-<版本>.zip` | ≈ 33 MB |
| `ATE_Setup-<版本>.exe` | ≈ 33 MB（自包含，可单独分发） |

---

## 12. 本项目已实测过的部署路径

| 验证 | 内容 | 结果 |
| --- | --- | --- |
| 嵌入式运行时 | `runtime\python.exe` 可 `import fastapi/uvicorn/pytest/cryptography` | 通过 |
| 便携目录直接运行 | 启动器 → 后端起在安装目录内 → 前端同端口托管 → `/api/health`、`/`、`/assets/*`、SPA 兜底、`/api` 未知路径 404 | 27 项断言全部通过 |
| 安装向导 | `/S /DIR=...` 静默安装 → 2134 个文件落盘、快捷方式与卸载项写入 | exit 0 |
| 安装后运行 | 从安装目录启动 → 默认测试台 7 台设备就绪 → 完整跑通示例 TPS（7/7 通过，2.3 s）→ 报告与运行目录产物齐全 | 27 项断言全部通过 |

> 说明：安装后首次启动会自动创建 `workspace\`、部署公共 `conftest.py`、把注册库投影到 `ate.db`、并种子写入默认测试台，因此新环境开箱即可跑示例 TPS。
