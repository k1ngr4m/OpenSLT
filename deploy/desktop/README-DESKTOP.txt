OpenSLT Windows 原生客户端

1. 解压 OpenSLT-Desktop-windows-x64.zip。
2. 双击 OpenSLT.exe。客户端会在本机启动内置 API 服务并打开 Qt 窗口，不需要浏览器、Python、Node.js、Redis 或 MySQL。
3. 首次登录默认账号由 OpenSLT.env.example 中的配置决定：admin / shengli123。
4. 数据、日志和报告分别保存在程序目录下的 data、logs 和 .env 中，请勿在升级时删除这些目录和文件。

如果启动失败，请查看 logs 目录，或删除被占用的 OpenSLT-Desktop-windows-x64 进程后重试。
