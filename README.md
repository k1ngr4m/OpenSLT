# OpenSLT 自动化测速平台

OpenSLT 是面向盛立 REM 期货测速流程的 Windows 原生客户端，实现资源集中管理、版本化方案/场景、资源独占与排队、人工确认节点、自动编排、结构化日志、统计指标、人工复核及 Excel/PDF 报告归档。

当前仓库依据 `docs/product_requirements_document_v2.md` 从零实现。由于 PRD 明确未提供真实交易所、REM、模拟市场、抓包及 Coco 命令模板，开发环境默认使用 `EXECUTION_MODE=simulated` 跑通完整链路；生产接入点位于 `backend/app/adapters/ssh.py` 和编排服务，凭据始终加密存储且日志脱敏。

## 快速启动

要求 Python 3.12。开发模式默认使用 SQLite，生产通过 `DATABASE_URL` 切换到 MySQL 8。

```bash
cp .env.example .env
python -m venv .venv
. .venv/bin/activate             # Windows: .venv\Scripts\activate
pip install -e ".[desktop,test]"
alembic upgrade head
python -m desktop.main
```

Web 开发端可以在 Windows 下双击仓库根目录的 `start-web.cmd` 一键启动。脚本首次运行会自动创建 Python 虚拟环境、安装前后端依赖并执行数据库迁移；启动完成后会打开 `http://127.0.0.1:5173`。关闭启动窗口或按 `Ctrl+C` 即可停止由脚本启动的服务。

也可以从 PowerShell 启动并禁止自动打开浏览器：

```powershell
.\start-web.ps1 -NoBrowser
```

客户端会自动启动本地 API 并打开 Qt 窗口。初始账号 `admin`，初始密码 `shengli123`。首次部署后必须立即修改密码和 `.env` 中的 JWT/凭据加密密钥。

## Windows 原生客户端

客户端不要求目标电脑安装 Python、Node.js、浏览器、MySQL、Redis 或 Celery。完整解压
`release/OpenSLT-Desktop-windows-x64.zip` 后双击 `OpenSLT.exe`，程序会启动 PySide6/Qt
原生窗口，并在后台使用内置 FastAPI、SQLite 和应用内调度器。

首次启动会在 EXE 旁创建 `.env`、`data/` 和 `logs/`。升级程序时保留这些内容即可延续配置和历史数据。

重新构建免安装包：

```powershell
# 在仓库根目录执行；需要 Python 3.12
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\desktop\build-desktop.ps1 -Python python
```

脚本会创建 `.venv-desktop`、安装桌面版依赖并执行 PyInstaller，生成：

```text
release/OpenSLT-Desktop-windows-x64/OpenSLT.exe
release/OpenSLT-Desktop-windows-x64.zip
```

如果已经准备好依赖环境，可直接复用：

```powershell
python -m pip install -e ".[desktop]"
.\deploy\desktop\build-desktop.ps1 -Python python -ReuseEnvironment
Get-FileHash .\release\OpenSLT-Desktop-windows-x64.zip -Algorithm SHA256
```

如果 PowerShell 禁止执行脚本，只对当前窗口临时放开策略即可：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

详细的使用说明见 `deploy/desktop/README-DESKTOP.txt`。

## 服务与验证

- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`GET /health`
- 后端测试：`pytest`
- 客户端启动：`python -m desktop.main`
- Worker：`celery -A app.tasks.celery_app:celery_app worker --app=backend -l INFO`
- Beat：`celery -A app.tasks.celery_app:celery_app beat --app=backend -l INFO`

原生 Linux 部署示例位于 `deploy/`，包括 Nginx、API、Celery Worker、Celery Beat 的 systemd 配置和安装脚本。

## 关键安全设计

- PBKDF2-SHA256（600,000 次）保存用户密码；JWT 使用短期访问令牌和可撤销、轮换的刷新令牌。
- SSH 密码和私钥由 Fernet 加密，API 永不回显密文或原值。
- 管理员、测试人员、访客三层权限；用户、资源、运行、确认、结论、报告和下载操作均进入只追加审计表。
- 每个 HTTP 请求和运行携带 `trace_id`；敏感字段、Bearer Token 和私钥写日志前统一脱敏。
- 下载路径限定在产物根目录；原始与解析产物带 SHA-256、大小和不可变标识。

## 生产接入说明

将 `EXECUTION_MODE` 改为 `remote` 前，需要以版本化场景模板补充真实命令，并在隔离环境验证启动、停止、取消清理和超时行为。生产建议使用 MySQL 8、Redis 7、独立非 root 用户及随机生成的 `JWT_SECRET`、`CREDENTIAL_ENCRYPTION_KEY`；不要沿用默认管理员密码。
