# OpenSLT 自动化测速平台

OpenSLT 是面向盛立 REM 期货测速流程的 Web 平台，提供资源集中管理、版本化方案与场景、资源独占与排队、自动编排、结构化日志、统计指标、人工复核以及 Excel/PDF 报告归档。

系统由 Vue Web 前端和 FastAPI 后端组成。开发环境默认使用 SQLite；资源操作台、配置文件管理和数据库操作会连接已配置的真实 SSH 或 MySQL 资源。

## 快速启动

要求 Python 3.8+、Node.js 20+ 和 npm 10+。Windows 开发环境可在仓库根目录双击 `start-web.cmd`，脚本会准备依赖、执行数据库迁移并打开：

```text
http://127.0.0.1:7777
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
python -m uvicorn app.main:app --app-dir backend --reload --port 4396
```

另开一个终端启动前端：

```powershell
npm --prefix frontend install
npm --prefix frontend run dev
```

初始账号为 `admin`，初始密码为 `shengli123`。首次部署后必须立即修改密码。JWT 与
凭据加密密钥在未配置时自动生成并持久化，不需要写入 `.env`。

## Linux 一键启动

Linux 开发或测试环境可使用根目录的 `start-web.sh` 自动准备依赖、执行数据库迁移，并在名为 `openslt` 的 tmux 会话中分别启动 FastAPI 和 Vite：

```bash
chmod +x ./start-web.sh
./start-web.sh
```

前端监听 `0.0.0.0:7777`，可通过脚本输出的局域网地址访问；API 只监听 `127.0.0.1:4396`，由 Vite 代理 `/api` 和 WebSocket 请求。脚本要求系统已安装 tmux、curl、Python 3.8+、Node.js 20+ 和 npm 10+。

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
- 管理员和测试人员可使用浏览器内 SSH 操作台连接已配置的远端 Shell。
- 发单工具操作台支持 EF/ZF XML 配置的查看、复制、结构化编辑、原文编辑、重命名和回收删除。
- MySQL 资源支持直连或 SSH 隧道、数据库名称发现、查询、导出及受约束的 UPDATE 操作。

## 配置与运行模式

复制 `.env.example` 为 `.env` 后按环境调整配置。常用配置包括：

```text
TZ=Asia/Shanghai
DATABASE_URL=sqlite:///./backend/data/openslt.sqlite3
ENABLE_INTERNAL_SCHEDULER=true
```

OpenSLT 自身产生的 API、页面、日志、报表和导出时间统一使用北京时间（UTC+08:00）；数据库中的时间仍按 UTC 保存，不需要迁移历史数据。

任务调度默认由 FastAPI 进程内置循环承担，不需要外部消息队列或独立任务进程。生产环境支持 MariaDB 5.5.68 和 MySQL 8，数据库必须启用 InnoDB、`utf8mb4` 与 `utf8mb4_unicode_ci`；服务启动和迁移会主动验证这些能力。请使用独立非 root 数据库用户。JWT 签名密钥与资源凭据加密密钥会在未显式配置时自动生成，并持久保存在数据目录下；也可以通过 `CREDENTIAL_ENCRYPTION_KEY` 显式配置由 `Fernet.generate_key()` 生成的固定密钥。请在资源管理中配置并验证 REM、模拟市场、发单工具、SLNIC、解析工具和 MySQL 资源后再运行自动化任务。

当 `DATABASE_URL` 指向 MariaDB/MySQL 时，启动和在线迁移会自动创建尚不存在的目标数据库，再执行 Alembic 建表。首次启动使用的数据库账号需要具备 `CREATE` 权限；数据库创建完成后可按生产安全策略收紧权限。MariaDB 仍使用 SQLAlchemy 的 `mysql+pymysql://` URL。

离线生产配置使用 `DATABASE_HOST`、`DATABASE_PORT`、`DATABASE_NAME`、
`DATABASE_USER` 和 `DATABASE_PASSWORD` 分字段配置，应用会安全组装连接 URL；旧的
`DATABASE_URL` 继续兼容，但不能与分字段同时使用。

## 生产构建

构建 Web 前端：

```powershell
npm --prefix frontend install
npm --prefix frontend run build
```

FastAPI 会在检测到 `frontend/dist` 后托管 Web SPA。Nginx 和 systemd API 的部署示例位于 `deploy/`。

RHEL 7.9 x86_64 无互联网环境的制包、安装、网络和验收步骤见
[`deploy/offline/README-OFFLINE.md`](deploy/offline/README-OFFLINE.md)。离线部署不要直接运行
会访问 PyPI/npm registry 的 `deploy/scripts/install.sh`。

外网一键制包入口为 `deploy/offline/make-offline-package.sh`；生成的压缩包内提供
`configure.sh` 和 `start.sh`，分别用于内网环境初始化和正式服务启动。
离线部署默认使用现有数据库，不会管理 MariaDB 实例或账号；本地数据库初始化必须
通过 `configure.sh --database-mode provision` 或 `initialize` 显式启用。

## 验证

```powershell
python -m pytest
npm --prefix frontend run build
```

- API 文档：`http://127.0.0.1:4396/docs`
- 健康检查：`GET /health`

## 安全设计

- 用户密码使用 PBKDF2-SHA256 保存，JWT 使用短期访问令牌和可撤销、轮换的刷新令牌。
- SSH、MySQL 密码和私钥加密存储，API 不回显密文或原值。
- 管理员、测试人员和访客使用分层权限；关键操作写入只追加审计记录。
- HTTP 请求和运行任务携带 `trace_id`，敏感字段、Bearer Token 和私钥写日志前统一脱敏。
- 下载路径限定在产物目录，原始与解析产物记录 SHA-256、大小和不可变标识。
