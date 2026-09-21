ATE Runner — 便携包安装说明（离线部署）
==================================================

一、最快用法（不安装）
  1) 把本文件夹放到目标机任意可写目录，例如 D:\ATERunner
  2) 双击 start-ate.bat（或 ATE_Launcher.exe）
  3) 启动器会自动：检查目录可写 → 起后端 → 打开浏览器 http://127.0.0.1:8000
  4) 关闭那个黑窗口即停止服务

二、固定安装到 C:\ATERunner（推荐）
  1) 双击 install.bat
     - 需要装到别处：install.bat D:\ATERunner
  2) 自动创建桌面 + 开始菜单「ATE Runner」快捷方式，并登记卸载项
  3) 之后双击桌面图标即可启动

三、卸载
  双击 uninstall.bat（或控制面板 → 程序和功能 → ATE Runner）
  询问“是否删除测试数据”时：
    选 否 → 保留 logs\、workspace\、app\ate.db（历史报告与运行目录）
    选 是 → 一并删除

四、目录说明
  ATE_Launcher.exe  启动器（双击入口）
  start-ate.bat     命令行入口（无 exe 时兜底）
  app\              后端程序（含 testresource\ 注册库与 TPS 包、drivers\、tps_runtime\）
  web\              前端页面（由后端同端口托管，无需 Node）
  runtime\          嵌入式 Python 3.13 + 全部依赖（目标机无需装 Python）
  logs\             启动日志、测试报告、硬件自检报告、测试台导出清单
  workspace\        TPS 实际运行环境（按 TPS 名建目录，内含临时运行副本）

五、注意
  * 不要装到 C:\Program Files：测试数据写在安装目录内，系统目录会导致写入失败。
  * 端口默认 8000，被占用会自动顺延 8001~8010（日志里会写明最终端口）。
    也可以先 set ATE_PORT=8020 再启动。
  * 车间其他电脑要访问界面：放行防火墙 TCP 8000，用本机 IP 访问。
  * 首次使用需导入 lic（授权文件）。预置 lic 备选目录 D:\ATE_ENV。
  * 启动异常先看 logs\launcher.log。
