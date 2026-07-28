# OpenSLT RHEL 7.9 离线部署

本文适用于以下已确认环境：

- 外网制包机：RHEL 7.9、x86_64、glibc 2.17、Python 3.8.13。
- 内网服务器：RHEL 7.9、x86_64、glibc 2.17、Python 3.8.13。
- 生产形态：Nginx、systemd、MariaDB 5.5.68、单个 OpenSLT API 进程。

不要在内网服务器运行仓库根目录的 `start-web.sh` 或
`deploy/scripts/install.sh`。这两个脚本面向开发或在线环境，会尝试访问 PyPI 和
npm registry。

## 1. 准备前端产物

RHEL 7.9 的 glibc 2.17 无法直接运行大多数官方 Node.js 20 Linux 发行包。请在
支持 Node.js 20 和 npm 10 的外网开发机上构建前端；构建结果是静态文件，可以在
RHEL 7.9 上运行。

```bash
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend run test
npm --prefix frontend run build
```

将生成的 `frontend/dist` 连同当前源码同步到外网 RHEL 7.9 制包机。必须确保
`dist` 对应准备发布的同一份源码，不能复用旧构建。

## 2. 收集系统 RPM

在外网 RHEL 7.9 制包机上配置并验证以下软件源：

- RHEL 7 基础源、MariaDB 5.5.68 及 Red Hat Software Collections。
- 提供 RHEL 7 x86_64 包的 Nginx 软件源。

安装制包工具后收集依赖闭包：

```bash
yum install -y yum-utils createrepo
chmod +x deploy/offline/collect-rpms-rhel7.sh
deploy/offline/collect-rpms-rhel7.sh --output /tmp/openslt-rpms
```

脚本使用 `rpm-packages-rhel7.txt`。如果服务器已经由运维统一安装 MariaDB 或
Nginx，可以编辑一份清单副本并通过 `--package-file` 指定，但不要直接删改默认
清单。

`repotrack` 的结果取决于当时启用的软件源。正式交付前必须在一台干净的 RHEL
7.9 测试机上关闭所有外部仓库，验证这组 RPM 能独立安装。

## 3. 制作应用离线包

在外网 RHEL 7.9 制包机的仓库根目录执行：

```bash
chmod +x deploy/offline/build-offline-bundle.sh
deploy/offline/build-offline-bundle.sh \
  --python /opt/rh/rh-python38/root/usr/bin/python3.8 \
  --rpm-dir /tmp/openslt-rpms \
  --version 0.1.0
```

脚本会完成以下工作：

1. 复制当前工作树，同时排除 Git 元数据、密钥、数据、日志和开发依赖。
2. 在线解析并构建 Python wheelhouse。
3. 在全新虚拟环境中使用 `--no-index` 回装 wheelhouse。
4. 执行 `pip check` 和后端测试。
5. 写入逐文件 `SHA256SUMS`，再生成压缩包及压缩包校验文件。

输出位于：

```text
release/openslt-offline-rhel7-x86_64-0.1.0.tar.gz
release/openslt-offline-rhel7-x86_64-0.1.0.tar.gz.sha256
```

通过受控介质传入内网后，先校验外层压缩包：

```bash
sha256sum -c openslt-offline-rhel7-x86_64-0.1.0.tar.gz.sha256
tar -xzf openslt-offline-rhel7-x86_64-0.1.0.tar.gz
```

## 4. 准备 MariaDB 5.5.68

进入内网解压后的离线包，先只安装 RPM，然后启动 MariaDB 并完成安全初始化：

```bash
chmod +x install.sh
./install.sh --rpms-only
systemctl enable --now mariadb
mysql_secure_installation
```

在 `/etc/my.cnf.d/server.cnf` 的 `[mysqld]` 段确认以下设置并重启 MariaDB：

```ini
[mysqld]
default-storage-engine=InnoDB
character-set-server=utf8mb4
collation-server=utf8mb4_unicode_ci
innodb-file-per-table=1
```

```bash
systemctl restart mariadb
mysql -uroot -p -NBe \
  "SELECT VERSION(), @@default_storage_engine, @@character_set_server, @@collation_server"
```

输出必须显示 MariaDB 5.5.68、InnoDB、utf8mb4 和 utf8mb4_unicode_ci。OpenSLT
迁移和启动时还会验证版本、InnoDB 与排序规则，不满足要求时会直接给出错误，避免
在 MyISAM 或错误字符集上运行。

应用账号只需要目标数据库内的权限，不应授予全局管理权限。

```sql
CREATE DATABASE openslt
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER 'openslt'@'127.0.0.1'
  IDENTIFIED BY '替换为独立强密码';

GRANT ALL PRIVILEGES ON openslt.* TO 'openslt'@'127.0.0.1';
FLUSH PRIVILEGES;
```

MariaDB 只需监听本机或受控管理网地址。虽然服务端是 MariaDB，`DATABASE_URL`
仍使用 `mysql+pymysql://`。数据库密码写入 URL 前必须进行百分号编码，
避免 `@`、`:`、`/`、`#` 等字符破坏 `DATABASE_URL`。

## 5. 生成生产配置

在安全目录复制 `openslt.env.example`，替换所有 `CHANGE_ME`。值必须符合 Bash
赋值语法；包含空格或 shell 特殊字符时使用双引号。

生成 JWT 和凭据加密密钥：

```bash
/opt/rh/rh-python38/root/usr/bin/python3.8 -c \
  'import secrets; print(secrets.token_urlsafe(64))'

/opt/rh/rh-python38/root/usr/bin/python3.8 -c \
  'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'
```

生产配置至少应满足：

- `JWT_SECRET`、`CREDENTIAL_ENCRYPTION_KEY` 和初始管理员密码各自独立随机生成。
- `DATABASE_URL` 使用 OpenSLT 专用数据库账号。
- 配置文件不进入 Git、邮件或普通聊天记录。
- 将配置文件和凭据加密密钥纳入加密备份；丢失加密密钥后，已保存的 SSH/MySQL
  凭据无法恢复。

## 6. 内网安装

先确认 MariaDB 可以登录，再进入解压后的离线包目录：

```bash
chmod +x install.sh
./install.sh \
  --env-file /root/openslt-production.env
```

安装器会执行平台检查、逐文件哈希校验、可选 RPM 安装、离线 Python 安装、
Alembic 迁移、SELinux 上下文设置、Nginx 检查和服务启动。应用路径为：

```text
/opt/openslt
/etc/openslt/openslt.env
/var/lib/openslt/artifacts
/var/log/openslt
```

如果 MariaDB 使用内网已有实例，也可以跳过前面的 `--rpms-only`，由运维预装 Nginx、
Python 和 curl。如果希望先检查迁移结果而不启动服务，增加 `--no-start`。

安装完成后通过内网服务器地址访问，立即修改初始管理员密码。

## 7. 网络和远端资源

至少配置以下网络路径：

| 来源 | 目标 | 端口 | 用途 |
| --- | --- | --- | --- |
| 内网用户 | OpenSLT/Nginx | TCP 80 | Web 和 WebSocket |
| OpenSLT | REM、市场、发单、SLNIC、Coco、解析机 | TCP 22 | SSH/SFTP |
| OpenSLT | 业务数据库 | TCP 3306 或跳板机 22 | 数据采集与数据库操作 |

内网服务器不需要互联网出口。生产使用 HTTPS 时，应由内网 CA 签发证书，并将
Nginx 改为监听 443。

远端资源还必须提前准备：

- 发单机：`tmux`、发单二进制、XML 配置和可写工作目录。
- SLNIC：`start_slnic_dump.sh`、`stop_slnic_dump.sh`、
  `pcap_merge_tool`、`editcap` 和足够的抓包空间。
- 解析机：受支持的解析二进制、XML 主配置及 SFTP 可读写目录。
- 所有 SSH 账号：最小权限、稳定主机密钥和无需公网的运行环境。

## 8. 验收和备份

按顺序完成：

```bash
curl -fsS http://127.0.0.1:8000/health
systemctl status openslt-api nginx mariadb
journalctl -u openslt-api -n 100 --no-pager
```

随后验证登录、权限、SSH 终端、资源健康检查、数据库查询、WebSocket、完整测速
工作流、HTML/Excel/PDF 报告和服务器重启后的自动恢复。

确认迁移生成的所有 OpenSLT 表都是 InnoDB；下面的查询必须返回空结果：

```sql
SELECT TABLE_NAME, ENGINE
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'openslt'
  AND LEFT(TABLE_NAME, 2) = 't_'
  AND UPPER(COALESCE(ENGINE, '')) <> 'INNODB';
```

每天使用 `mysqldump` 备份 MariaDB，同时备份 `/var/lib/openslt/artifacts` 和
`/etc/openslt/openslt.env`。备份应放到另一存储设备并定期执行恢复演练。

WeasyPrint 在 RHEL 7 上可能因系统 Pango 版本较旧而退回简化 PDF。若正式中文
PDF 是验收项，需要在外网同环境机器上单独验证 Pango/Cairo/中文字体组合。
