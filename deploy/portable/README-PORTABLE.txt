OpenSLT Windows 免安装版
========================

一、启动

1. 将压缩包完整解压到可写目录，不要直接在压缩包中运行。
2. 双击 OpenSLT.exe。
3. 等待浏览器自动打开 http://127.0.0.1:8765。
4. 初始账号：admin
   初始密码：shengli123
5. 首次登录后请立即修改初始密码。

关闭程序：回到 OpenSLT 控制台窗口，按 Ctrl+C。

二、无需安装的组件

本版本已经包含 Python 运行时、FastAPI 后端和 Vue 前端，使用内置 SQLite
数据库及应用内任务调度器，不需要另外安装 Python、Node.js、MySQL、Redis
或 Celery。

三、运行目录

首次启动会在 OpenSLT.exe 旁生成：

  .env                 本机配置及随机安全密钥
  data/openslt.sqlite3 SQLite 数据库
  data/artifacts/      采集产物和报告
  logs/                应用日志与归档

升级或移动程序时请保留 .env、data 和 logs。备份时至少复制 .env 和 data。

四、配置

可以用文本编辑器修改 .env，重启后生效。常用配置：

  PORT=8765                    本地访问端口
  LOG_LEVEL=INFO               日志级别
  EXECUTION_MODE=simulated     模拟执行模式

免安装版默认只监听 127.0.0.1，局域网其他电脑无法访问。真实远端执行前，必须先
补充并验证 REM、模拟市场、抓包和 Coco 的版本化命令模板，再将 EXECUTION_MODE
改为 remote。

五、常见问题

端口被占用：关闭占用程序，或修改 .env 中的 PORT 后重新启动。
浏览器未自动打开：手动访问 http://127.0.0.1:8765。
杀毒软件告警：PyInstaller 打包程序可能被启发式检测，请使用本包 SHA-256 校验，
不要从非可信来源下载。

六、从源码生成 EXE

在仓库根目录打开 PowerShell，要求 Python 3.8.2（3.8.x）、Node.js 20+ 和 pnpm：

  Set-ExecutionPolicy -Scope Process Bypass
  .\deploy\portable\build-portable.ps1 -Python python

脚本会自动构建前端、创建 .venv-portable、安装便携版依赖并运行 PyInstaller。
输出目录和压缩包为：

  release\OpenSLT-Portable\OpenSLT.exe
  release\OpenSLT-Portable-windows-x64.zip

如果已经完成前端构建并准备好 Python 环境，可以分步执行：

  pnpm --dir frontend install --frozen-lockfile
  pnpm --dir frontend build
  python -m venv .venv-portable
  .\.venv-portable\Scripts\python.exe -m pip install -r deploy\portable\requirements.txt
  .\deploy\portable\build-portable.ps1 -Python .\.venv-portable\Scripts\python.exe -ReuseEnvironment -SkipFrontend

校验发布包：

  Get-FileHash .\release\OpenSLT-Portable-windows-x64.zip -Algorithm SHA256
