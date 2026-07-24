# OpenSLT 自动化测速平台

OpenSLT 是面向盛立 REM 期货测速流程的 Web 平台，提供资源集中管理、版本化方案与场景、资源独占与排队、自动编排、结构化日志、统计指标、人工复核以及 Excel/PDF 报告归档。

系统由 Vue Web 前端和 FastAPI 后端组成。开发环境默认使用 SQLite，并通过 `EXECUTION_MODE=simulated` 提供安全的模拟执行；切换到 `remote` 后才会连接真实 SSH 或 MySQL 资源。

## 快速启动

要求 Python 3.8.2（3.8.x）、Node.js 20+ 和 npm 10+。Windows 开发环境可在仓库根目录双击 `start-web.cmd`，脚本会准备依赖、执行数据库迁移并打开：

```text
http://127.0.0.1:5173
```

也可以从 PowerShell 启动并禁止自动打开浏览器：

```powershell
.\start-web.ps1 -NoBrowser
```

手工启动方式：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m alembic upgrade head
python -m uvicorn app.main:app --app-dir backend --reload --port 8000
```

另开一个终端启动前端：

```powershell
npm --prefix frontend install
npm --prefix frontend run dev
```

初始账号为 `admin`，初始密码为 `shengli123`。首次部署后必须立即修改密码，并更新 `.env` 中的 JWT 与凭据加密密钥。

## Linux 一键启动

Linux 开发或测试环境可使用根目录的 `start-web.sh` 自动准备依赖、执行数据库迁移，并在名为 `openslt` 的 tmux 会话中分别启动 FastAPI 和 Vite：

```bash
chmod +x ./start-web.sh
./start-web.sh
```

前端监听 `0.0.0.0:5173`，可通过脚本输出的局域网地址访问；API 只监听 `127.0.0.1:8000`，由 Vite 代理 `/api` 和 WebSocket 请求。脚本要求系统已安装 tmux、curl、Python 3.8.2（3.8.x）、Node.js 20+ 和 npm 10+。

常用管理命令：

```bash
./start-web.sh status
./start-web.sh logs backend
./start-web.sh logs frontend
./start-web.sh attach
./start-web.sh restart
./start-web.sh stop
```

该脚本用于开发和测试，不替代生产环境的 systemd、Nginx 与权限隔离配置。

## Web 功能

- 管理 REM 柜台、模拟市场、发单工具、SLNIC、数据库等测试资源。
- 管理测试方案、场景、运行任务、人工确认节点和测试报告。
- 管理员和测试人员可使用浏览器内 SSH 操作台；模拟模式只运行内置安全命令，不访问本机 Shell。
- 发单工具操作台支持 EF/ZF XML 配置的查看、复制、结构化编辑、原文编辑、重命名和回收删除。
- MySQL 资源支持直连或 SSH 隧道、数据库名称发现、查询、导出及受约束的 UPDATE 操作。

## 配置与运行模式

复制 `.env.example` 为 `.env` 后按环境调整配置。常用配置包括：

```text
DATABASE_URL=sqlite:///./backend/data/openslt.sqlite3
EXECUTION_MODE=simulated
HOST=127.0.0.1
PORT=8000
```

`EXECUTION_MODE=simulated` 不连接真实 SSH 或 MySQL；只有改为 `remote` 并重启服务后才会访问远端资源。生产环境建议使用 MySQL 8、Redis 7、独立非 root 用户和随机生成的 `JWT_SECRET`、`CREDENTIAL_ENCRYPTION_KEY`。

## 生产构建

构建 Web 前端：

```powershell
npm --prefix frontend install
npm --prefix frontend run build
```

FastAPI 会在检测到 `frontend/dist` 后托管 Web SPA。Nginx、systemd API、Celery Worker 和 Celery Beat 的部署示例位于 `deploy/`。

## Windows Portable Web 版

Portable 版将 FastAPI、Vue 构建产物和 Python 运行时打包为免安装程序，启动后仍通过浏览器使用 Web 界面，不包含原生桌面 UI。

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\portable\build-portable.ps1 -Python python
```

输出文件：

```text
release/OpenSLT-Portable/OpenSLT.exe
release/OpenSLT-Portable-windows-x64.zip
```

详细说明见 `deploy/portable/README-PORTABLE.txt`。

## 验证

```powershell
python -m pytest
npm --prefix frontend run build
```

- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`GET /health`

## 安全设计

- 用户密码使用 PBKDF2-SHA256 保存，JWT 使用短期访问令牌和可撤销、轮换的刷新令牌。
- SSH、MySQL 密码和私钥加密存储，API 不回显密文或原值。
- 管理员、测试人员和访客使用分层权限；关键操作写入只追加审计记录。
- HTTP 请求和运行任务携带 `trace_id`，敏感字段、Bearer Token 和私钥写日志前统一脱敏。
- 下载路径限定在产物目录，原始与解析产物记录 SHA-256、大小和不可变标识。
