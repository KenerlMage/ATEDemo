# ATE Docker 部署指南

将 ATE 打包为两个容器（Nginx 前端 + FastAPI 后端），一条命令部署到工控机/服务器。

## 架构

```
浏览器 ──> :8080 (Nginx 容器 ate-frontend)
                │  静态资源 /usr/share/nginx/html
                └── /api/* 反代 ──> backend:8000 (FastAPI 容器 ate-backend)
                                        │
                                        ├── pytest 执行 backend/test_cases 下的用例
                                        └── 日志写入 logs/（宿主机挂载卷）
```

## 前置要求（工控机）

- 已安装 Docker Engine 20.10+（Linux 工控机）或 Docker Desktop（Windows 10/11 Pro+，需 WSL2/Hyper-V）
- 工控机能访问镜像仓库（首次构建/拉取需要网络；离线部署见下文）

## 快速部署

```bash
# 1. 进入项目根目录（含 docker-compose.yml）
cd D:\ATE

# 2. 构建并启动
docker compose up -d --build

# 3. 验证
docker compose ps                 # 两个容器均应为 Up
curl http://127.0.0.1:8000/api/health   # 后端健康检查
```

浏览器打开 `http://<工控机IP>:8080` 即可使用。

## 目录挂载（客户可维护）

| 挂载点 | 宿主机目录 | 用途 |
| --- | --- | --- |
| `/app/test_cases` | `D:\ATE\backend\test_cases` | 客户新增/修改 pytest 用例，容器内实时生效 |
| `/app/logs` | `D:\ATE\backend\logs` | 登录日志、执行日志持久化，容器重建不丢失 |

> 加新用例：往 `backend/test_cases/` 放 `test_xxx.py`，刷新页面即可看到，无需重建镜像。

## 离线部署（客户内网无外网时）

在有网机器上构建并导出镜像，再拷到工控机导入：

```bash
# 有网机器：构建
docker compose build

# 导出两个镜像
docker save ate-backend | gzip > ate-backend.tar.gz
docker save ate-frontend | gzip > ate-frontend.tar.gz

# 工控机：导入（注意镜像名需与 compose 中一致，或用 docker compose 指定 tag）
docker load < ate-backend.tar.gz
docker load < ate-frontend.tar.gz

# 工控机：启动（跳过构建）
docker compose up -d
```

## 常用运维命令

```bash
docker compose logs -f backend    # 查看后端日志
docker compose restart backend    # 重启后端
docker compose down               # 停止并删除容器（数据卷 logs 保留）
docker compose down -v            # 连挂载卷一起清理（慎用）
```

## 注意事项

- 端口：前端 8080、后端 8000。若工控机端口被占，修改 `docker-compose.yml` 中 `ports` 映射即可。
- CORS：容器部署时前端通过 Nginx 同源反代访问 `/api`，不涉及跨域，`main.py` 现有 CORS 配置无影响。
- 时区：容器已设 `TZ=Asia/Shanghai`，日志时间与本地一致。
- 重启策略：`restart: unless-stopped`，工控机开机后 Docker 会自动拉起服务。

## Windows 工控机（无 Docker）替代方案

若客户工控机为 Windows 7 / Win10 家庭版 / 无虚拟化环境（装不了 Docker Desktop），建议改用免安装打包：

1. **后端**：PyInstaller 将 `backend/main.py` 打包为单个 exe（含 uvicorn/fastapi/pytest），双击运行
2. **前端**：`npm run build` 生成静态文件，用 Nginx for Windows 或内置静态服务托管，反代 `/api` 到本机 8000
3. 或：后端 exe 直接用 FastAPI `StaticFiles` 挂载前端 dist，单进程单端口，无需 Nginx

（该方案可后续按需实施。）
