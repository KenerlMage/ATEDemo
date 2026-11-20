# ATE Controller

装备软件的归档与下载服务。ATE Runner 通过 HTTP 拉取指定 `object` + `version` 的软件包，校验 `sha256` 后解压并执行包内 `deploy` 脚本。

## 启动

```bat
cd D:\ATE_CONTROLLER
start_controller.bat
```

默认监听 `0.0.0.0:8070`，仓库根 `D:\ATE_CONTROLLER\repo`。可用环境变量覆盖：

- `ATE_CONTROLLER_REPO`：仓库根目录
- `ATE_CONTROLLER_HOST` / `ATE_CONTROLLER_PORT`：监听地址与端口

## 目录结构

```
D:\ATE_CONTROLLER\
├── app.py
├── requirements.txt
├── start_controller.bat
└── repo\
    └── <object>\
        └── <version>\
            ├── package.zip      # 装备软件包（zip）
            └── manifest.json    # object / name / version / entry / sha256 / size / uploaded_at
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查，返回仓库路径与包数量 |
| GET | `/api/packages` | 包清单（object + 各版本） |
| GET | `/api/packages/{object}` | 单个包详情 |
| GET | `/api/packages/{object}/{version}/manifest` | 包的 manifest |
| GET | `/api/packages/{object}/{version}/download` | 下载 zip，响应头带 `X-Package-Sha256` |
| POST | `/api/packages` | 归档上传（form：object / name / version / entry / file） |

版本一经入库不允许覆盖；需要修正时发布新版本号。

## 归档示例

```powershell
curl.exe -X POST http://127.0.0.1:8070/api/packages `
  -F "object=spm_rh_dyn" -F "name=SPM读头动态测试软件" `
  -F "version=1.2.0" -F "entry=deploy/install.ps1" `
  -F "file=@D:\build\spm_rh_dyn-1.2.0.zip"
```

包内建议结构：

```
package.zip
├── deploy\install.ps1        # entry 指向的部署脚本（相对包根）
├── bin\...                   # 软件本体
└── README.txt
```
