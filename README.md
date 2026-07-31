# OpenSLT 自动化测试平台

OpenSLT 是面向盛立 REM 期货测速工作的内网 Web 平台。它将测试资源、方案、场景、
执行步骤、日志、统计结果和报告集中管理，用可审计、可复现的流程替代分散的脚本与
人工记录。

平台支持软核做市、整合版二期、整合版二期做市等业务类型，可通过 SSH、SFTP 和
MySQL 连接 REM、模拟市场、发单工具、SLNIC、解析机及业务数据库。生产部署不需要
互联网出口。

> 首次启动会创建管理员 `admin / shengli123`。请在首次登录后立即修改密码。
> 如果数据库中已经存在该用户，后续启动不会重置其密码。

## 适用角色

| 角色 | 主要工作 | 系统权限 |
| --- | --- | --- |
| 管理员 `admin` | 用户、资源、凭据、审计和平台维护 | 全部功能；用户和资源增删改仅限管理员 |
| 测试人员 `tester` | 维护方案与工作流、执行任务、复核结果 | 可操作测试流程、终端和数据库工具，不能管理用户 |
| 访客 `visitor` | 查看资源、方案、运行记录和报告 | 只读访问，不能创建方案或执行测试 |
| 运维人员 | 部署、升级、备份、监控和故障处理 | 使用服务器和数据库管理权限，不等同于 Web 角色 |

## 核心能力

- 集中管理 REM、市场、发单、SLNIC、解析机和 MySQL 等测试资源。
- 对测试方案、场景和工作流进行复制、版本化、预览与发布。
- 运行前校验资源关系，执行资源独占、排队、暂停、恢复、重试和超时回收。
- 在浏览器内使用 SSH 终端，并通过 WebSocket 查看持续输出。
- 查看、复制和编辑发单工具 EF/ZF XML 配置，支持结构化与原文两种模式。
- 通过直连或 SSH 隧道访问业务数据库，执行库发现、查询、导出及受约束的更新。
- 编排抓包、合并、解析、统计和人工确认节点，保留每一步的上下文与产物。
- 生成 HTML、Excel 和 PDF 报告，记录 SHA-256、大小和不可变产物标识。
- 记录结构化应用日志、任务日志和只追加审计日志，使用 `trace_id` 串联请求。

## 典型测速流程

1. 管理员录入测试资源和加密凭据，完成连接与健康检查。
2. 测试人员创建测试方案和场景，绑定所需资源。
3. 在工作流编辑器中配置节点、预览输入并发布工作流版本。
4. 创建运行任务，确认接线和资源选择后启动测试。
5. 平台按流程调用远端程序、发单工具、SLNIC、解析器和统计脚本。
6. 测试人员处理人工确认节点，必要时暂停、重试或取消任务。
7. 完成结果复核并提交结论，生成和归档报告。

同一资源不会被多个运行任务同时占用。服务异常重启后，内置调度器会恢复可继续执行的
持久化任务，并回收过期资源锁。

## 系统架构

```mermaid
flowchart LR
    User["内网浏览器"] -->|"HTTP / WebSocket"| Nginx["Nginx 静态站点与反向代理"]
    Nginx -->|"/api 与 /api/v1/ws"| API["FastAPI / Uvicorn"]
    API --> DB["SQLite 开发库或 MariaDB/MySQL 生产库"]
    API --> Files["产物、日志与持久化密钥"]
    API -->|"SSH / SFTP"| Remote["REM、市场、发单、SLNIC、解析机"]
    API -->|"MySQL 或 SSH 隧道"| BusinessDB["业务数据库"]
```

后端使用 FastAPI、SQLAlchemy 2、Alembic、PyMySQL 和 AsyncSSH；前端使用 Vue 3、
Vite 6、Element Plus 和 Pinia。任务调度运行在单个 API 进程内，不依赖 Redis、
Celery 或外部消息队列。

## 快速启动

### 环境要求

| 组件 | 要求 |
| --- | --- |
| Python | 项目要求 3.8+；当前 Windows/Linux 一键开发脚本校验 Python 3.8.2 至 3.8.x |
| Node.js | 20+ |
| npm | 10+ |
| Linux 开发工具 | `tmux`、`curl` |

开发环境默认使用 SQLite，API 监听 `127.0.0.1:4396`，Web 监听
`0.0.0.0:7777`。这些开发脚本会安装依赖，因此只能在能够访问 Python 和 npm 软件源
的环境使用。

### Windows

在仓库根目录双击 `start-web.cmd`，或在 PowerShell 中执行：

```powershell
.\start-web.ps1
```

禁止自动打开浏览器：

```powershell
.\start-web.ps1 -NoBrowser
```

启动完成后访问 `http://127.0.0.1:7777/`。

### Linux

```bash
chmod +x ./start-web.sh
./start-web.sh
```

脚本在名为 `openslt` 的 tmux 会话中启动后端和前端，并输出局域网访问地址。常用命令：

```bash
./start-web.sh status
./start-web.sh logs backend
./start-web.sh logs frontend
./start-web.sh attach
./start-web.sh restart
./start-web.sh stop
```

### 手工启动

先准备后端：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
python -m alembic upgrade head
python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 4396
```

在另一个终端准备前端：

```bash
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend run dev
```

Windows 手工启动时，将虚拟环境激活命令替换为
`.\.venv\Scripts\Activate.ps1`。

## 配置说明

开发脚本会在缺少 `.env` 时复制根目录的 `.env.example`。常用配置如下：

```dotenv
ENVIRONMENT=development
TZ=Asia/Shanghai
DATABASE_URL=sqlite:///./backend/data/openslt.sqlite3
ARTIFACT_ROOT=./backend/data/artifacts
LOG_DIR=./backend/logs
ENABLE_INTERNAL_SCHEDULER=true
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=shengli123
```

开发环境切换到 MySQL 兼容数据库时，可以设置：

```dotenv
DATABASE_URL=mysql+pymysql://username:password@127.0.0.1:3306/openslt?charset=utf8mb4
```

离线生产环境推荐使用 `DATABASE_HOST`、`DATABASE_PORT`、`DATABASE_NAME`、
`DATABASE_USER` 和 `DATABASE_PASSWORD` 分字段配置。应用通过 SQLAlchemy
`URL.create` 组装连接地址，密码中的 `@`、`:`、`/`、`#` 等字符不需要手工编码。
旧 `DATABASE_URL` 仍受支持，但不能和分字段配置同时出现。

生产数据库支持 MariaDB 5.5.68+ 和 MySQL 5.5.3+，已重点验证 MariaDB 5.5.68 与
MySQL 8。数据库必须支持 InnoDB 和 `utf8mb4_unicode_ci`，OpenSLT 自有表必须全部
使用 InnoDB。默认 `AUTO_CREATE_DATABASE=true`；目标库不存在时，应用会尝试使用
配置账号创建数据库，权限不足则明确失败。预先建库或设置
`AUTO_CREATE_DATABASE=false` 可避免授予全局 `CREATE` 权限。

JWT 签名密钥和 Fernet 凭据加密密钥在未配置时自动生成并持久化：

- 开发环境：默认位于 `backend/data/secrets/`。
- 离线生产环境：位于 `/var/lib/openslt/secrets/`，文件权限为 `0600`。

丢失凭据加密密钥后，数据库中已经保存的 SSH/MySQL 密码和私钥无法解密。密钥目录
必须与数据库、配置和产物一起备份。

所有页面、API、日志和报告时间按北京时间（UTC+08:00）展示，数据库时间仍以 UTC
保存。

## 版本与更新说明

根目录 `VERSION` 是 OpenSLT 唯一版本号来源，使用不带 `v` 前缀的
`MAJOR.MINOR.PATCH` 格式。Python wheel、FastAPI、主界面和离线部署包都会读取或校验
这个值。主界面右上角在管理中心按钮右侧显示当前版本，点击版本号可以查看历次更新说明。

`RELEASES.json` 保存未发布变更和历史版本记录。修改版本或发布说明后执行：

```bash
python tools/release_metadata.py
```

校验通过后再运行后端测试、前端测试和生产构建。正式离线包的文件名、包内 `VERSION`、
wheel 版本、前端显示版本以及安装后的版本记录必须保持一致。完整制包和升级步骤见
`deploy/offline/README-OFFLINE.md`。

## 项目结构

```text
OpenSLT/
├── backend/
│   ├── app/                 FastAPI API、领域服务、资源适配器和调度器
│   ├── migrations/          Alembic 数据库迁移
│   ├── scripts/             后端维护与一致性检查脚本
│   └── tests/               后端及数据库兼容测试
├── frontend/
│   ├── src/                 Vue 页面、组件、状态和 API 客户端
│   ├── public/              静态资源
│   └── dist/                生产前端构建产物
├── deploy/
│   ├── nginx/               Nginx 配置模板
│   ├── systemd/             API systemd 单元模板
│   ├── scripts/             可联网 Linux 安装脚本
│   └── offline/             RHEL 7.9 离线制包与部署工具
├── tools/                   测试辅助工具和统计脚本
├── .env.example             开发配置模板
├── pyproject.toml           Python 项目与测试配置
└── start-web.*              Windows/Linux 开发启动入口
```

## 测试与构建

后端测试：

```bash
python -m pytest
```

前端测试、类型检查和生产构建：

```bash
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend run test
npm --prefix frontend run build
```

重新生成前端 API 类型：

```bash
npm --prefix frontend run generate:api
```

该命令会先调用后端导出最新 OpenAPI Schema，再生成
`frontend/src/types/api.generated.ts`。

运行中的接口地址：

- 健康检查：`GET http://127.0.0.1:4396/health`
- Swagger API 文档：`http://127.0.0.1:4396/docs`

## 生产部署

`start-web.cmd`、`start-web.ps1` 和 `start-web.sh` 只用于开发或测试，不提供生产环境
所需的 systemd、Nginx、账号隔离、SELinux、备份和离线依赖管理。

RHEL 7.9 x86_64 无互联网部署请使用
[离线部署与运维手册](deploy/offline/README-OFFLINE.md)。外网侧入口为
`deploy/offline/make-offline-package.sh`；生成的离线包提供：

- `configure.sh`：内网主机和数据库模式初始化。
- `install.sh`：底层 RPM、应用文件和迁移安装器。
- `start.sh`：生产安装、升级、迁移、启动与健康检查入口。
- `build-frontend.sh`：使用可选的包内 Node.js 与 npm 缓存离线重建前端。

需要在 RHEL 7.9 内网机直接修改前端时，外网制包增加 `--bundle-node`。该模式使用
`linux-x64-glibc-217` 社区构建，不覆盖系统 Node；运行时安装到
`/opt/openslt-node`，npm 缓存安装到 `/var/cache/openslt/npm`。具体校验、风险和使用
方式见离线部署手册。外网制包机连续生成离线包时，可给制包脚本传入 `--cache-dir`
复用 RPM、Node、npm 和 pip 缓存。

默认数据库模式为 `existing`，不会安装、配置、启停或清理 MariaDB，也不会管理
数据库账号。使用 `provision` 或 `initialize` 前必须先阅读离线手册中的影响说明。

## 安全与数据保护

- 用户密码使用 PBKDF2-SHA256 保存；JWT 使用短期访问令牌和可撤销、轮换的刷新令牌。
- SSH、MySQL 密码和私钥使用 Fernet 加密保存，API 不回显原值或密文。
- 管理员、测试人员和访客按最小权限分层，敏感管理操作仅向相应角色开放。
- 关键操作写入只追加审计日志；Bearer Token、密码和私钥在写日志前脱敏。
- 下载路径限制在产物根目录内，产物记录哈希、大小和不可变标识。
- 生产环境应限制 Web 入口来源、为远端账号配置最小权限，并使用内网 CA 提供 HTTPS。
- 数据库、`openslt.env`、密钥和产物必须按同一恢复点备份，并定期执行恢复演练。

## 日志与可观测性

运行时会记录 `/api/v1`、`/health`、WebSocket 会话、平台数据库 SQL 和资源数据库
SQL。结构化权威日志按日期写入 `LOG_DIR/http`、`LOG_DIR/sql` 和
`LOG_DIR/websocket`，数据库中的 `t_log_records` 仅保存日志中心所需的检索索引。

JSON、表单和文本正文默认最多记录 64 KiB；文件、multipart、二进制和超限正文仅
记录内容类型、大小与 SHA-256。密码、JWT、Token、Cookie、私钥和数据库凭据在写入
文件或索引前统一脱敏。日志中心仅允许管理员读取脱敏后的完整详情，测试人员只能查看
访问和 SQL 摘要，访客无法访问这些日志类型。

高频日志默认可检索 30 天，随后压缩归档 90 天并自动删除。相关环境变量为
`OBSERVABILITY_BODY_LIMIT_BYTES`、`OBSERVABILITY_SQL_LIMIT_BYTES`、
`OBSERVABILITY_SQL_PARAMS_LIMIT_BYTES`、`OBSERVABILITY_QUEUE_SIZE`、
`OBSERVABILITY_HOT_RETENTION_DAYS` 和 `OBSERVABILITY_ARCHIVE_RETENTION_DAYS`。

## 常见问题

### 登录后为什么不能编辑或执行任务？

确认账号角色。`visitor` 是只读角色；测试操作需要 `tester` 或 `admin`，用户与资源管理
需要 `admin`。

### 修改默认管理员配置会重置现有管理员密码吗？

不会。`INITIAL_ADMIN_USERNAME` 和 `INITIAL_ADMIN_PASSWORD` 只在数据库中不存在该用户
时用于首次创建。

### 为什么生产环境启动时提示数据库不兼容？

检查服务端版本、InnoDB、`utf8mb4_unicode_ci` 和既有 OpenSLT 表引擎。应用会在迁移
和启动时主动验证这些条件，避免在不兼容数据库上继续运行。

### 为什么重启后已保存的资源凭据无法解密？

检查密钥目录是否被删除、覆盖或从错误恢复点还原。生产环境必须保留
`/var/lib/openslt/secrets/credential_encryption_key`，并与数据库同步恢复。

### 内网服务器可以直接运行在线安装脚本吗？

不可以。无互联网环境不要运行 `deploy/scripts/install.sh` 或开发启动脚本；它们可能
访问 PyPI 或 npm registry。请严格使用离线包中的脚本。

### 内网服务器没有 Node.js，能否修改前端？

可以，但离线包必须在外网制包时使用 `--bundle-node`。部署后修改
`/opt/openslt/frontend/src` 中的代码，再以 root 运行
`/opt/openslt/build-frontend.sh`。旧 npm 缓存只适用于制包时的
`package-lock.json`；新增或升级依赖后必须在外网重新制包。
