# OpenSLT RHEL 7.9 离线部署与运维手册

本文用于在完全无法访问互联网的 RHEL 7.9 x86_64 内网服务器上部署、升级、备份和
恢复 OpenSLT。命令与当前离线脚本保持一致，数据库兼容基线为 MariaDB 5.5.68。

> `existing` 是默认且最安全的数据库模式：它不会安装、修改、启停或清理 MariaDB，
> 也不会使用 root 账号管理数据库。但是，安装和启动仍会执行 Alembic 迁移，创建或
> 升级 OpenSLT 自有表。

> `initialize` 会处理 MariaDB root 密码、删除匿名用户和远程 root、删除测试库。
> 只有确认数据库实例由 OpenSLT 独占时才能使用。

## 1. 适用环境与边界

| 项目 | 要求 |
| --- | --- |
| 外网制包机 | RHEL 7.9、x86_64、glibc 2.17、可访问 yum/PyPI |
| 内网目标机 | RHEL 7.9、x86_64、glibc 2.17、systemd |
| Python | 3.8+；默认路径 `/opt/rh/rh-python38/root/usr/bin/python3.8` |
| 前端构建 | 普通包使用 Node.js 20+/npm 10+ 构建机；内网开发包可使用 `--bundle-node` |
| Web 服务 | Nginx，默认对外端口 `7777` |
| API 服务 | 单个 Uvicorn 进程，仅监听 `127.0.0.1:4396` |
| 数据库 | MariaDB 5.5.68+ 或 MySQL 5.5.3+；重点验证 MariaDB 5.5.68 和 MySQL 8 |

本文不宣称离线包支持 RHEL 8/9、CentOS、ARM、容器平台或其他未经验证的系统。
RHEL 7.9 上通常不能直接运行官方 Node.js 20 Linux 发行包。普通生产包应在兼容的
外网主机上构建前端；需要内网前端开发时，可由制包脚本下载并验证
`linux-x64-glibc-217` 社区构建及 npm 依赖缓存。

不要在内网服务器运行仓库根目录的 `start-web.sh` 或
`deploy/scripts/install.sh`。它们面向开发或在线环境，可能访问 PyPI 和 npm registry。

## 2. 部署流程

```mermaid
flowchart LR
    Build["构建 frontend/dist，或选择 --bundle-node"] --> Sync["同步源码到外网 RHEL 7.9"]
    Sync --> Package["收集 RPM、wheel 并生成离线包"]
    Package --> Verify1["校验 tar.gz 和 sha256"]
    Verify1 --> Transfer["通过受控介质传入内网"]
    Transfer --> Configure["选择数据库模式并运行 configure.sh"]
    Configure --> Start["start.sh 安装、迁移并启动"]
    Start --> Accept["健康、功能与重启验收"]
```

后续示例统一使用以下版本变量。每次打开新终端时重新定义：

```bash
VERSION="$(<VERSION)"
PACKAGE="openslt-offline-rhel7-x86_64-${VERSION}"
PYTHON=/opt/rh/rh-python38/root/usr/bin/python3.8
```

`VERSION` 是项目唯一版本源，必须使用不带 `v` 前缀的 `MAJOR.MINOR.PATCH` 格式。
`RELEASES.json` 保存当前和历史更新说明。正式制包前执行
`"${PYTHON}" tools/release_metadata.py`；脚本会拒绝缺失、重复、乱序或与 `VERSION`
不一致的发布记录。`--version` 仅用于断言传入值与项目版本相同，不能覆盖项目版本。

## 3. 上线前检查清单

### 3.1 外网制包机

在 RHEL 7.9 制包机上检查：

```bash
cat /etc/os-release
uname -m
uname -r
getconf GNU_LIBC_VERSION
"${PYTHON}" --version
yum repolist enabled
```

预期至少满足：

- `VERSION_ID="7.9"`、`x86_64`、glibc 2.17。
- Python 可执行且版本不低于 3.8。
- RHEL 7 基础仓库可用，并能取得离线清单中的系统依赖。
- 能访问 PyPI；制包过程需要生成完整 Python wheelhouse。
- 使用 `--bundle-node` 时，还能访问 Node unofficial-builds 及 lock 文件引用的 npm
  registry；单位镜像或预下载文件按第 5.3 节配置。
- 仓库工作树是准备发布的版本；普通包还要求 `frontend/dist` 与前端源码一致。

一键脚本缺少 `repotrack` 或 `createrepo` 时会通过 yum 安装 `yum-utils` 和
`createrepo`，这一步需要 root 权限和可用的软件源。

### 3.2 内网目标机

提前确认：

- 主机时间、时区和 NTP 策略正确。
- 安装目录、日志、产物、数据库和备份空间充足。
- 内网用户可以访问计划使用的 `FRONTEND_PORT`。
- OpenSLT 主机可以连接所需 SSH、SFTP 和业务数据库地址。
- 已决定数据库采用 `existing`、`provision` 还是 `initialize`。
- `existing` 模式下，数据库地址、账号和密码已经准备完毕。
- 已明确数据库、配置、密钥和产物的备份责任人与保存位置。

## 4. 准备前端

### 4.1 普通生产包

在支持 Node.js 20+ 和 npm 10+ 的外网开发机上，从准备发布的源码执行：

```bash
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend run test
npm --prefix frontend run build
```

确认产物存在：

```bash
test -f frontend/dist/index.html
```

将完整项目源码和当前 `frontend/dist` 同步到外网 RHEL 7.9 制包机。不要只替换
`dist`，也不要复用其他提交生成的旧前端。制包脚本会比较前端源码和
`frontend/dist/index.html` 的修改时间，发现产物过旧时拒绝制包。

### 4.2 内网前端开发包

如果内网机需要直接修改 Vue、TypeScript 或 CSS，可跳过预构建 `frontend/dist`，在
制包命令中使用 `--bundle-node`。该模式会在 RHEL 7.9 制包机上：

1. 下载固定版本的 `node-v20.20.2-linux-x64-glibc-217.tar.gz` 和
   `SHASUMS256.txt`。
2. 精确匹配文件名并校验 SHA-256，解压后实际运行 `node` 和 `npm`。
3. 从空缓存执行 `npm ci`，收集当前 `package-lock.json` 的全部依赖。
4. 删除 `node_modules`，再执行 `npm ci --offline` 验证断网回装。
5. 执行前端测试和生产构建，并把 Node、npm、来源、归档摘要及 lock 文件摘要写入
   `node-runtime/METADATA`。

> `linux-x64-glibc-217` 来自
> [Node.js unofficial-builds](https://unofficial-builds.nodejs.org/)，属于实验性社区构建，
> 不是 Node.js 官方发布二进制，也不提供与官方发行包相同的支持承诺。正式交付前必须
> 在与生产一致的 RHEL 7.9 测试机验证。

## 5. 在外网生成离线包

### 5.1 一键制包

进入外网 RHEL 7.9 制包机的项目根目录：

```bash
chmod +x deploy/offline/*.sh
deploy/offline/make-offline-package.sh \
  --python "${PYTHON}" \
  --version "${VERSION}" \
  --bundle-node
```

脚本会依次：

1. 收集 Nginx、curl、MariaDB 及其 RHEL 7 RPM 依赖闭包。
2. 构建 OpenSLT wheel 和全部 Python 依赖的 wheelhouse。
3. 在新虚拟环境中使用 `--no-index` 回装 wheelhouse。
4. 执行 `pip check` 和后端测试。
5. 使用 `--bundle-node` 时，校验 Node、生成 npm 缓存并完成断网回装和前端构建。
6. 复制应用、前端产物、RPM、安装脚本、发布说明和文档。
7. 生成包内 `SHA256SUMS`、压缩包和压缩包校验文件。

默认输出到项目根目录的 `release/`：

```text
release/openslt-offline-rhel7-x86_64-${VERSION}.tar.gz
release/openslt-offline-rhel7-x86_64-${VERSION}.tar.gz.sha256
```

可以使用 `--output /指定目录` 修改输出目录。`--skip-tests` 会跳过 Python wheelhouse
回装、后端测试和前端测试；带 Node 的包仍必须通过 `npm ci --offline` 和生产构建验证。
该选项只应用于临时诊断包，不应作为正式交付包。

不需要在内网修改前端时，可以不传 `--bundle-node`，但必须提前按第 4.1 节生成当前
`frontend/dist`。

### 5.2 目标机没有 Python

如果目标机没有 Python 3.8+，或无法保证默认路径存在，在制包时增加：

```bash
deploy/offline/make-offline-package.sh \
  --python "${PYTHON}" \
  --version "${VERSION}" \
  --bundle-python
```

该选项只打包制包机现有的 `/opt/rh/rh-python38`。因此：

- `--python` 必须指向该目录内的解释器。
- 内网配置时会安装到 `/opt/rh/rh-python38`，不会替换系统 `/usr/bin/python`。
- 目标机已有可用 Python 时不会覆盖它。
- 自定义目录中的其他 Python 发行版不能使用此引导方式。

制包机和目标机直接使用预装 Python 时，建议使用相同主次版本，以降低二进制 wheel
兼容风险。

### 5.3 Node 版本、镜像与预下载文件

默认 Node 版本固定为 `20.20.2`，不会在每次制包时静默选择新版本。升级前应先在
RHEL 7.9 测试机验证，新版本必须是带 `linux-x64-glibc-217` 产物的完整 Node 20
版本：

```bash
deploy/offline/make-offline-package.sh \
  --python "${PYTHON}" \
  --version "${VERSION}" \
  --bundle-node \
  --node-version 20.20.2
```

通过单位镜像下载时，镜像目录结构必须保留 `v版本/SHASUMS256.txt` 和归档文件：

```bash
deploy/offline/make-offline-package.sh \
  --python "${PYTHON}" \
  --version "${VERSION}" \
  --bundle-node \
  --node-base-url 'https://mirror.example.internal/node-unofficial/release'
```

也可以传入预下载的两个文件。两者必须同时提供，归档内容仍按清单校验，不能通过改名
绕过：

```bash
deploy/offline/make-offline-package.sh \
  --python "${PYTHON}" \
  --version "${VERSION}" \
  --bundle-node \
  --node-archive /safe/node-v20.20.2-linux-x64-glibc-217.tar.gz \
  --node-shasums /safe/SHASUMS256.txt
```

### 5.4 Nginx 仓库

如果已启用仓库没有 `nginx`，脚本会临时启用 nginx.org 的 RHEL 7 官方仓库，结束时
删除临时 repo 文件，不修改原有仓库配置。

如果制包机只能访问单位镜像，指定镜像地址。地址中的 `$basearch` 必须使用单引号，
避免被当前 Shell 提前展开：

```bash
deploy/offline/make-offline-package.sh \
  --python "${PYTHON}" \
  --version "${VERSION}" \
  --nginx-repo-url 'http://yum.example.internal/nginx/rhel/7/$basearch/'
```

出现 `nginx is still unavailable` 时，先验证仓库地址和元数据，不要使用示例域名：

```bash
curl -I 'http://实际镜像地址/nginx/rhel/7/x86_64/'
```

### 5.5 分步制包

一键脚本失败时，可以分步定位问题：

```bash
deploy/offline/collect-rpms-rhel7.sh \
  --output /tmp/openslt-rpms

deploy/offline/build-offline-bundle.sh \
  --python "${PYTHON}" \
  --rpm-dir /tmp/openslt-rpms \
  --version "${VERSION}" \
  --bundle-node
```

如需自定义 RPM 清单，复制 `deploy/offline/rpm-packages-rhel7.txt` 后通过
`--package-file` 传入收集脚本，不要直接修改默认清单。`repotrack` 的结果取决于制包
时启用的软件源，正式发布前应在关闭所有外部仓库的干净 RHEL 7.9 测试机上验证 RPM
能够完整安装。

## 6. 校验与传输

在外网制包机检查输出：

```bash
ls -lh \
  "release/${PACKAGE}.tar.gz" \
  "release/${PACKAGE}.tar.gz.sha256"

cd release
sha256sum -c "${PACKAGE}.tar.gz.sha256"
cd ..
```

带 `--bundle-node` 的包还应确认以下条目存在：

```bash
tar -tzf "release/${PACKAGE}.tar.gz" | grep -E \
  '/(node-runtime/METADATA|npm-cache/_cacache/|build-frontend.sh)$'
```

将 `.tar.gz` 和对应 `.tar.gz.sha256` 一起通过受控介质传入内网。不要重新打包或修改
其中任何文件。

在内网存放目录重新定义变量并校验：

```bash
VERSION=0.2.0
PACKAGE="openslt-offline-rhel7-x86_64-${VERSION}"

sha256sum -c "${PACKAGE}.tar.gz.sha256"
tar -xzf "${PACKAGE}.tar.gz"
cd "${PACKAGE}"
sha256sum -c SHA256SUMS
chmod +x configure.sh install.sh start.sh build-frontend.sh
```

`configure.sh`、`install.sh` 和 `start.sh` 也会在执行时校验包内文件。校验失败必须停止
部署并重新获取介质，不能忽略或重新生成校验文件。

## 7. 选择数据库模式

| 模式 | 适用场景 | 会执行的操作 | 不会执行的操作 |
| --- | --- | --- | --- |
| `existing`（默认） | 使用运维已管理的本地或远程数据库 | 校验 env；安装非数据库 RPM；执行 Alembic；必要时由应用账号尝试建库 | 不安装 MariaDB RPM，不写 `my.cnf`，不启停 MariaDB，不使用 root，不创建账号，不做安全清理 |
| `provision` | 本机 MariaDB 由 OpenSLT 使用，但需保留现有安全策略 | 安装并启动 MariaDB；写入 InnoDB/utf8mb4 配置；创建应用库和账号；生成数据库密码 | 不修改 root 密码，不删除匿名用户、远程 root 或测试库 |
| `initialize` | OpenSLT 独占的全新本机 MariaDB | 包含 `provision` 的全部操作；处理空 root 密码；删除匿名用户、远程 root 和测试库 | 不保留被清理的 MariaDB 默认对象 |

选择结果保存到 `/etc/openslt/database-mode`，后续 `start.sh` 默认沿用。文件不存在时
按 `existing` 处理。也可以通过 `--database-mode MODE` 显式覆盖本次执行。

### 7.1 existing：使用现有数据库

推荐由 DBA 预先创建库和最小权限账号：

```sql
CREATE DATABASE openslt
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER 'openslt'@'OpenSLT主机地址'
  IDENTIFIED BY '替换为独立强密码';

GRANT ALL PRIVILEGES ON openslt.*
  TO 'openslt'@'OpenSLT主机地址';
FLUSH PRIVILEGES;
```

MariaDB 账号的 Host 必须与数据库实际看到的 OpenSLT 来源地址一致。本机通过
`127.0.0.1` 连接时，不要只授权 `'openslt'@'localhost'`。

默认 `AUTO_CREATE_DATABASE=true`。如果库不存在，应用会尝试使用配置账号创建
`utf8mb4/utf8mb4_unicode_ci` 数据库，此时账号需要全局 `CREATE` 权限。若不希望授予
该权限，请由 DBA 预先建库，或在 env 中显式设置：

```dotenv
AUTO_CREATE_DATABASE=false
```

无论是否允许自动建库，Alembic 都需要在目标库内创建、修改索引和表结构的权限。

### 7.2 provision：配置本机 MariaDB

该模式会安装和重启 MariaDB，并写入 `/etc/my.cnf.d/openslt.cnf`：

```ini
[mysqld]
default-storage-engine=InnoDB
character-set-server=utf8mb4
collation-server=utf8mb4_unicode_ci
innodb-file-per-table=1
```

如果 MariaDB root 已有密码且 `/root/.my.cnf` 不可用，准备权限为 `0600` 的客户端
配置：

```ini
[client]
user=root
password=数据库管理密码
```

配置时传入：

```bash
./configure.sh \
  --database-mode provision \
  --mysql-defaults-file /安全路径/root.cnf
```

当生产 env 尚不存在时，该模式会生成随机数据库密码，创建 `openslt` 库和
`openslt@127.0.0.1` 账号，并写入生产 env。可通过 `--database-name` 和
`--database-user` 修改名称；名称仅允许字母、数字和下划线。

### 7.3 initialize：初始化独占 MariaDB

仅在数据库实例没有承载其他业务、并确认允许清理默认对象时执行：

```bash
./configure.sh --database-mode initialize
```

如果 root 密码为空且未提供其他 root 配置，脚本会生成随机 root 密码并写入权限为
`0600` 的 `/root/.my.cnf`。如果 root 已有密码，按需传入
`--mysql-defaults-file /安全路径/root.cnf`。

`initialize` 不是普通故障修复选项，不能在共享数据库实例上尝试。

## 8. 生产配置

正式配置固定安装到 `/etc/openslt/openslt.env`，所有者和权限为
`root:openslt 0640`。

`existing` 模式首次运行 `configure.sh` 时会从模板创建该文件，然后停止并提示填写
数据库密码：

```bash
./configure.sh
vi /etc/openslt/openslt.env
./configure.sh
```

当前模板为：

```dotenv
DATABASE_HOST=127.0.0.1
DATABASE_PORT=3306
DATABASE_NAME=openslt
DATABASE_USER=openslt
DATABASE_PASSWORD=CHANGE_ME

BACKEND_PORT=4396
FRONTEND_PORT=7777

INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=shengli123
```

配置规则：

- 必须替换实际配置行中的全部 `CHANGE_ME`，否则脚本拒绝继续。
- 数据库分字段必须五项全部提供；`DATABASE_PORT` 必须为 `1..65535`。
- `BACKEND_PORT` 和 `FRONTEND_PORT` 必须为 `1024..65535`，且不能相同。
- 后端始终只监听 `127.0.0.1:${BACKEND_PORT}`；Nginx 对外监听
  `${FRONTEND_PORT}`。
- 数据库密码包含 `@`、`:`、`/`、`#` 或空格时无需 URL 编码，建议使用引号包住值。
- 不要在值中使用未验证的 Shell 展开表达式；systemd 会按 EnvironmentFile 规则读取。
- `INITIAL_ADMIN_*` 只在用户不存在时用于创建管理员，不会覆盖已有密码。
- 首次登录必须修改默认密码 `shengli123`。

兼容旧配置时，可以只设置完整 URL：

```dotenv
DATABASE_URL="mysql+pymysql://openslt:已编码密码@127.0.0.1:3306/openslt?charset=utf8mb4"
BACKEND_PORT=4396
FRONTEND_PORT=7777
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=shengli123
```

`DATABASE_URL` 不能和任何分字段 `DATABASE_*` 配置同时出现。只有旧 URL 方式需要自行
对用户名和密码做 URL 编码。

JWT 和 Fernet 密钥不应写入生产 env。首次迁移或启动时，应用会自动生成：

```text
/var/lib/openslt/secrets/jwt_secret
/var/lib/openslt/secrets/credential_encryption_key
```

安装器会设置目录权限 `0700`、文件权限 `0600` 并归属 `openslt:openslt`。升级和重启
会复用现有密钥，不会重新生成。

## 9. 首次配置与启动

### 9.1 existing 模式

```bash
# 第一次创建 env 后会有意退出
./configure.sh
vi /etc/openslt/openslt.env

# 填写数据库连接后完成系统配置
./configure.sh

# 安装应用、执行迁移、启动并等待健康检查
./start.sh
```

`configure.sh` 在 `existing` 模式下自动排除 `mariadb`、`mariadb-server` 和
`mariadb-libs` RPM，也不会调用本机 MariaDB systemd 服务。

### 9.2 provision 模式

```bash
./configure.sh --database-mode provision
./start.sh
```

MariaDB root 需要认证时，给第一条命令增加 `--mysql-defaults-file`。

### 9.3 initialize 模式

确认数据库实例可以执行完整安全清理后：

```bash
./configure.sh --database-mode initialize
./start.sh
```

### 9.4 自定义 Python 或防火墙策略

非默认 Python 路径必须在配置和启动时保持一致：

```bash
./configure.sh --python /opt/custom/bin/python3
./start.sh --python /opt/custom/bin/python3
```

`configure.sh` 默认在运行中的 firewalld 中放行 `FRONTEND_PORT/tcp`。由外部防火墙或
安全策略统一管理时使用：

```bash
./configure.sh --no-firewall
```

该选项只阻止脚本修改 firewalld，不影响 Nginx 监听端口。

## 10. 四个内网脚本的职责

### configure.sh

用于主机首次配置，也可以在修改端口、数据库模式或系统依赖后重新运行。主要参数：

```text
--python PATH
--database-mode existing|provision|initialize
--mysql-defaults-file FILE
--database-name NAME
--database-user NAME
--env-file FILE
--no-firewall
```

非默认 `--env-file` 会在校验后复制到标准路径 `/etc/openslt/openslt.env`。已有 env 在
`provision` 和 `initialize` 中也会保留，不会重新生成数据库密码。

### install.sh

底层安装器，供 `configure.sh` 和 `start.sh` 调用。常用诊断参数：

```text
--install-rpms
--rpms-only
--skip-database-rpms
--no-start
--database-mode existing|provision|initialize
```

日常首次上线和升级优先使用 `configure.sh`、`start.sh`。只有分步排查时直接运行
`install.sh`，例如只安装非数据库 RPM：

```bash
./install.sh --rpms-only --database-mode existing
```

### start.sh

正式启动和升级入口。它会校验 env 与包哈希、比较
`/var/lib/openslt/installed-bundle-version`、安装新版本、执行 Alembic、渲染 systemd
和 Nginx 配置、重启服务并等待健康检查。

重复运行相同版本时不会重建应用虚拟环境，但仍会执行待处理迁移并重启服务。需要强制
重装同一版本时：

```bash
./start.sh --reinstall
```

### build-frontend.sh

该脚本只在使用 `--bundle-node` 制包时可用。安装后推荐从固定路径执行：

```bash
/opt/openslt/build-frontend.sh
```

它会核对当前 `package-lock.json` 与缓存绑定的 SHA-256，使用
`npm ci --offline` 重建 `node_modules`，运行前端测试和生产构建，执行 `nginx -t`
并 reload Nginx。常用参数：

```text
--project-root DIR
--skip-tests
--no-reload
```

`--skip-tests` 只适合临时诊断。`--no-reload` 会保留构建结果并验证 Nginx 配置，但不
reload 服务。

## 11. 安装结果与日常管理

### 11.1 路径和权限

| 路径 | 用途 | 关键权限或归属 |
| --- | --- | --- |
| `/opt/openslt` | 当前应用、前端和虚拟环境 | 应用文件 `root:root` |
| `/opt/openslt-node` | 可选的 glibc 2.17 Node.js/npm 与校验元数据 | `root:root`，不替换系统 Node |
| `/var/cache/openslt/npm` | 与制包时 lock 文件绑定的 npm 离线缓存 | `root:root` |
| `/etc/profile.d/openslt-node.sh` | 将隔离 Node 加入新登录 Shell 的 PATH | `root:root 0644` |
| `/etc/openslt/openslt.env` | 数据库、端口和初始管理员配置 | `root:openslt 0640` |
| `/etc/openslt/database-mode` | 持久化数据库模式 | `root:root 0644` |
| `/var/lib/openslt/artifacts` | 抓包、解析、统计和报告产物 | `openslt:openslt` |
| `/var/lib/openslt/secrets` | JWT 与凭据加密密钥 | 目录 `0700`、文件 `0600` |
| `/var/lib/openslt/installed-bundle-version` | 已安装离线包版本 | `root:root 0644` |
| `/var/log/openslt` | 应用日志 | `openslt:openslt` |
| `/etc/systemd/system/openslt-api.service` | 渲染后的 API 服务 | `root:root 0644` |
| `/etc/nginx/conf.d/openslt.conf` | 渲染后的 Nginx 配置 | `root:root 0644` |

### 11.2 服务命令

```bash
systemctl status openslt-api nginx
systemctl restart openslt-api nginx
journalctl -u openslt-api -n 100 --no-pager
nginx -t
```

仅在 `provision` 或 `initialize` 模式下由 OpenSLT 管理本机 MariaDB：

```bash
systemctl status mariadb
journalctl -u mariadb -n 100 --no-pager
```

`existing` 模式下不要因为 OpenSLT 故障而擅自启停外部或共享数据库。

### 11.3 在内网修改并构建前端

带 `--bundle-node` 的包部署后，新登录 Shell 可以直接查看隔离运行时：

```bash
node --version
npm --version
cat /opt/openslt-node/METADATA
```

修改 `/opt/openslt/frontend/src` 中的 Vue、TypeScript 或 CSS 后执行：

```bash
/opt/openslt/build-frontend.sh
```

该命令必须以 root 运行，因为生产源码和 npm 缓存属于 root。它不连接 npm registry，
成功后 Nginx 会提供新的 `frontend/dist`。仅修改后端 Python 时不需要 Node，完成测试后
执行 `systemctl restart openslt-api`。

以下变更不能依赖旧缓存：

- 修改 `frontend/package.json` 中的依赖。
- 运行 `npm install` 导致 `package-lock.json` 改变。
- 引入 lock 文件中不存在的构建工具或平台二进制。

发生上述变化时，必须在外网更新 lock 文件并重新生成 `--bundle-node` 离线包。生产机
上的直接改动也会被后续 `start.sh` 升级覆盖，应同步回受版本控制的外网源码，不能把
生产目录作为唯一代码副本。

## 12. 网络、Nginx、SELinux 与远端资源

| 来源 | 目标 | 默认端口 | 用途 |
| --- | --- | --- | --- |
| 内网用户 | OpenSLT/Nginx | TCP 7777 或配置的 `FRONTEND_PORT` | Web、API、WebSocket |
| Nginx | 本机 API | TCP 4396 或配置的 `BACKEND_PORT` | 仅本机反向代理 |
| OpenSLT | REM、市场、发单、SLNIC、解析机 | TCP 22 | SSH 与 SFTP |
| OpenSLT | 业务数据库 | TCP 3306 或实际端口 | 直连 MySQL/MariaDB |
| OpenSLT | SSH 跳板机 | TCP 22 | 数据库 SSH 隧道 |

内网服务器运行时不需要互联网出口。生产使用 HTTPS 时，应由内网 CA 签发证书并由
运维调整 Nginx；不要将 API 后端端口直接暴露给客户端。

安装器会在工具可用时设置前端静态文件的 SELinux 上下文，并启用
`httpd_can_network_connect` 以允许 Nginx 代理本机 API。若严格安全基线不允许脚本
持久修改 SELinux boolean，应在上线前由安全管理员评审替代策略。

远端资源至少应准备：

- REM、市场和发单机：最小权限 SSH 账号、稳定主机密钥、所需二进制及可写工作目录。
- 发单机：正确的 EF/ZF XML、合约数据及运行依赖。
- SLNIC：`start_slnic_dump.sh`、`stop_slnic_dump.sh`、`pcap_merge_tool`、
  `editcap` 和足够抓包空间。
- 解析机：受支持的解析程序、XML 主配置及 SFTP 可读写路径。
- 业务数据库：受限查询/更新账号，或可用的 SSH 隧道入口。

## 13. 升级

升级会执行新版本 Alembic 前向迁移。开始前必须取得可恢复的同一时点备份，并安排
禁止新建任务的维护窗口。

### 13.1 升级前检查

1. 在与生产一致的测试环境验证新离线包和数据库迁移。
2. 确认新包的 `.sha256` 和包内 `SHA256SUMS` 均通过。
3. 记录当前版本、数据库模式、端口和服务状态。
4. 停止新任务，等待运行中的任务完成或安全取消。
5. 按第 14 节备份数据库、env、密钥和产物。

记录当前状态：

```bash
cat /var/lib/openslt/installed-bundle-version
cat /etc/openslt/database-mode
systemctl status openslt-api nginx --no-pager
```

### 13.2 执行升级

校验并解压新包后进入新目录：

```bash
VERSION=0.2.0
PACKAGE="openslt-offline-rhel7-x86_64-${VERSION}"

sha256sum -c "${PACKAGE}.tar.gz.sha256"
tar -xzf "${PACKAGE}.tar.gz"
cd "${PACKAGE}"
sha256sum -c SHA256SUMS
./start.sh
```

当包版本与已安装版本不同时，`start.sh` 会重建 `/opt/openslt/.venv`、替换应用文件、
执行迁移并重启服务。`/etc/openslt/openslt.env`、数据库、密钥、产物和日志不会被应用
文件复制覆盖。

如果发布说明明确新增系统 RPM，先执行：

```bash
DATABASE_MODE="$(cat /etc/openslt/database-mode 2>/dev/null || printf existing)"
./install.sh --rpms-only --database-mode "${DATABASE_MODE}"
./start.sh
```

升级后必须完成健康、登录、资源连接、任务与报告抽样验收。

## 14. 备份与恢复

备份必须覆盖同一恢复点的四类数据：

1. OpenSLT 数据库。
2. `/etc/openslt/openslt.env` 和 `/etc/openslt/database-mode`。
3. `/var/lib/openslt/secrets`。
4. `/var/lib/openslt/artifacts`。

日志和已安装版本文件建议一并保留，便于审计和定位。备份必须存放到另一台设备或受控
备份系统，不能只留在应用服务器本机。

### 14.1 创建一致性备份

先停止新任务，等待任务完成，然后准备一个权限为 `0600`、能够备份和恢复目标库的
数据库管理配置。以下示例假设为 `/root/openslt-backup.cnf`，并且应用服务器已经
安装 `mysqldump`。`existing` 模式默认不安装 MariaDB 客户端；使用远程数据库时，应
在 DBA 备份主机执行相同的数据库导出，并在应用停服期间完成数据库与文件备份。

```bash
(
set -Eeuo pipefail

BACKUP_ROOT=/安全备份挂载/openslt
BACKUP_ID="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="${BACKUP_ROOT}/${BACKUP_ID}"
DATABASE_NAME=openslt
DB_ADMIN_CNF=/root/openslt-backup.cnf

install -d -m 0700 "${BACKUP_DIR}"
systemctl stop openslt-api
trap 'systemctl start openslt-api' EXIT

mysqldump \
  --defaults-extra-file="${DB_ADMIN_CNF}" \
  --single-transaction \
  --routines \
  --triggers \
  --events \
  --databases "${DATABASE_NAME}" \
  > "${BACKUP_DIR}/database.sql"

tar -C / -czf "${BACKUP_DIR}/openslt-files.tar.gz" \
  etc/openslt \
  var/lib/openslt/artifacts \
  var/lib/openslt/secrets \
  var/lib/openslt/installed-bundle-version

sha256sum \
  "${BACKUP_DIR}/database.sql" \
  "${BACKUP_DIR}/openslt-files.tar.gz" \
  > "${BACKUP_DIR}/SHA256SUMS"

)
```

如果数据库由独立 DBA 管理，应使用单位标准快照或备份流程，但仍要记录与文件备份
对应的恢复点。备份完成后检查 API 健康状态。

### 14.2 恢复原则

- 数据库、env、密钥和产物必须恢复到同一时点。
- 恢复前先校验备份哈希，并停止 `openslt-api`。
- 数据库恢复属于破坏性操作，必须确认目标实例、库名和备份文件无误。
- 恢复密钥时不得生成新密钥替代旧密钥，否则既有资源凭据无法解密。
- 恢复后重新设置所有者和权限，再启动对应版本应用。

恢复文件的示例：

```bash
cd /安全备份挂载/openslt/选定恢复点
sha256sum -c SHA256SUMS
systemctl stop openslt-api
tar -C / -xzf openslt-files.tar.gz
chown -R openslt:openslt /var/lib/openslt/artifacts /var/lib/openslt/secrets
chmod 0700 /var/lib/openslt/secrets
find /var/lib/openslt/secrets -maxdepth 1 -type f -exec chmod 0600 {} \;
chown root:openslt /etc/openslt/openslt.env
chmod 0640 /etc/openslt/openslt.env
```

数据库应由 DBA 将 `database.sql` 恢复到确认过的目标实例。下面给出同名数据库恢复
示例；它会删除目标库中的全部现有数据，只能在核对数据库主机、备份时间和库名后由
授权人员执行：

```bash
set -Eeuo pipefail
DATABASE_NAME=openslt
DB_ADMIN_CNF=/root/openslt-backup.cnf

[[ "${DATABASE_NAME}" =~ ^[A-Za-z0-9_]+$ ]]
mysql --defaults-extra-file="${DB_ADMIN_CNF}" \
  -NBe 'SELECT @@hostname, VERSION()'
read -r -p "输入目标库名 ${DATABASE_NAME} 以确认清空并恢复: " CONFIRM_DATABASE
[[ "${CONFIRM_DATABASE}" == "${DATABASE_NAME}" ]]

mysql --defaults-extra-file="${DB_ADMIN_CNF}" \
  -e "DROP DATABASE IF EXISTS \`${DATABASE_NAME}\`"
mysql --defaults-extra-file="${DB_ADMIN_CNF}" < database.sql
```

恢复结束后使用对应版本的离线包执行 `./start.sh --reinstall`，再完成全量验收。

## 15. 回滚

项目没有自动回滚脚本。Alembic 前向迁移完成后，只回退 `/opt/openslt` 代码是不安全
的；旧版本可能无法识别新表结构。必须使用升级前的同一时点备份恢复。

回滚顺序：

1. 停止 `openslt-api`，保留故障现场日志。
2. 校验升级前数据库和文件备份。
3. 由 DBA 恢复升级前数据库，确保升级新增的表和字段不会残留。
4. 恢复同一时点的 env、数据库模式、密钥和产物。
5. 进入升级前版本的原始离线包，校验 `SHA256SUMS`。
6. 执行 `./start.sh --reinstall`。
7. 完成健康、登录、凭据解密、资源连接、历史任务和报告验收。

旧版本包和其 `.sha256` 必须与备份一起保留。若没有可靠的升级前数据库备份，应停止
服务并由开发/DBA 评估迁移差异，不能直接尝试应用回退。

## 16. 上线验收

先从 `/etc/openslt/openslt.env` 确认实际端口，再执行：

```bash
BACKEND_PORT="$(awk -F= '$1 == "BACKEND_PORT" {gsub(/["[:space:]]/, "", $2); print $2}' /etc/openslt/openslt.env)"
FRONTEND_PORT="$(awk -F= '$1 == "FRONTEND_PORT" {gsub(/["[:space:]]/, "", $2); print $2}' /etc/openslt/openslt.env)"

curl -fsS "http://127.0.0.1:${BACKEND_PORT:-4396}/health"
curl -fsSI "http://127.0.0.1:${FRONTEND_PORT:-7777}/"
systemctl is-enabled openslt-api nginx
systemctl is-active openslt-api nginx
journalctl -u openslt-api -n 100 --no-pager
```

`provision` 和 `initialize` 模式还需检查：

```bash
systemctl is-enabled mariadb
systemctl is-active mariadb
```

数据库兼容检查应确认版本、引擎和字符集：

```sql
SELECT VERSION(), @@default_storage_engine,
       @@character_set_server, @@collation_server;

SELECT TABLE_NAME, ENGINE
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'openslt'
  AND LEFT(TABLE_NAME, 2) = 't_'
  AND UPPER(COALESCE(ENGINE, '')) <> 'INNODB';
```

第二条查询必须返回空结果。

Web 验收至少包括：

- 使用 `admin / shengli123` 首次登录并立即修改密码。
- 创建 `tester` 和 `visitor`，验证角色权限边界。
- 验证资源连接、SSH 终端、数据库查询和 WebSocket。
- 发布场景工作流，执行一条完整测速任务并验证资源锁。
- 验证抓包、解析、统计、人工复核和 HTML/Excel/PDF 报告。
- 重启服务器，确认 OpenSLT、Nginx 以及受管 MariaDB 自动恢复。
- 完成一次备份，并在隔离环境执行恢复演练。

## 17. 故障排查

### 17.1 `release/` 中没有压缩包

制包脚本只有全部步骤成功后才写入最终压缩包。向上查找第一个错误；RPM、wheel、测试
或前端新旧检查失败时，`release/` 为空是正常保护行为。

### 17.2 Nginx 显示 `Nothing found to download`

当前 yum 源不包含 Nginx。允许访问 nginx.org 时由脚本自动回退；只能使用内部镜像时
传入真实 `--nginx-repo-url`。先用 `curl -I` 验证地址，不要照抄示例域名。

### 17.3 提示找不到 `rh-python38` RPM

离线 RPM 清单不再依赖 RHSCL Python RPM。目标机已有 Python 3.8+ 时直接使用；没有时
必须在制包机使用 `--bundle-python`。如果当前脚本帮助中不存在该参数，说明外网机
代码不是最新版本，应先同步仓库。

### 17.4 `Required Python executable not found`

检查解释器路径和版本：

```bash
ls -l /opt/rh/rh-python38/root/usr/bin/python3.8
/opt/rh/rh-python38/root/usr/bin/python3.8 --version
```

自定义路径需要同时传给 `configure.sh` 和 `start.sh`。目标机没有 Python 时重新制作
带 `--bundle-python` 的包。

### 17.5 env 仍包含 `CHANGE_ME`

编辑 `/etc/openslt/openslt.env`，替换实际配置行中的占位符。注释也不应保留容易被
误判为实际配置的 `CHANGE_ME` 文本。然后重新运行 `./configure.sh`。

### 17.6 数据库连接、建库或迁移失败

依次检查：

- 主机、端口、用户名、密码和 MariaDB Host 授权是否匹配。
- 数据库是否存在；不存在时账号是否有 `CREATE` 权限。
- 显式 `AUTO_CREATE_DATABASE=false` 是否阻止自动建库。
- 账号是否拥有目标库内建表、改表、索引和数据读写权限。
- MariaDB 是否至少为 5.5.68，InnoDB 和 `utf8mb4_unicode_ci` 是否可用。
- 既有 `t_` 表是否全部为 InnoDB。

`existing` 模式不会尝试使用 root 修复数据库权限，应交由 DBA 处理。

### 17.7 端口非法、重复或被占用

确认前后端端口位于 `1024..65535` 且不同：

```bash
ss -lntp | grep -E ':(4396|7777)[[:space:]]'
```

修改 env 后重新运行 `./start.sh --reinstall`，脚本会重新渲染 systemd 和 Nginx 配置。
如果启用了 firewalld，还需放行新的前端端口并移除不再使用的旧规则。

### 17.8 API 或 Nginx 启动失败

```bash
systemctl status openslt-api nginx --no-pager
journalctl -u openslt-api -n 200 --no-pager
journalctl -u nginx -n 100 --no-pager
nginx -t
```

重点检查数据库错误、env 权限、密钥目录、前端 `index.html`、端口占用和 SELinux 拒绝
记录。`start.sh` 健康检查等待 60 秒，失败后会输出最近 API 日志。

### 17.9 页面可打开但 API 或 WebSocket 不可用

检查 `/etc/nginx/conf.d/openslt.conf` 中的后端端口是否与 systemd 单元一致，并确认
Nginx 可以连接 `127.0.0.1:${BACKEND_PORT}`。WebSocket 路径为 `/api/v1/ws/`，中间
防火墙或代理必须允许 Upgrade/Connection 头和长连接。

### 17.10 PDF 中文或排版异常

PDF 由 ReportLab 使用内置 `STSong-Light` 中文 CID 字体生成，不再依赖
Pango、Cairo 或系统中文字体。若 PDF 是上线验收项，应在相同 RHEL 7.9 环境用
`pdftoppm` 逐页渲染验证；渲染机还需安装 Poppler 的 Adobe-GB1 CMap 数据包。
Excel、HTML 正常不代表 PDF 环境已经合格。

### 17.11 已保存凭据突然无法解密

检查 `/var/lib/openslt/secrets/credential_encryption_key` 是否被删除、覆盖或从不同
恢复点还原。不要生成新密钥覆盖原文件；从与数据库一致的备份恢复密钥。

### 17.12 Node、npm 或离线前端构建失败

先确认当前包确实使用了 `--bundle-node`：

```bash
ls -l /opt/openslt-node/bin/node /opt/openslt-node/bin/npm
test -d /var/cache/openslt/npm/_cacache
cat /opt/openslt-node/METADATA
sha256sum /opt/openslt/frontend/package-lock.json
```

- 提示找不到 Node 或缓存时，使用带 `--bundle-node` 的原始离线包执行
  `./start.sh --reinstall`。
- 提示 `package-lock.json does not match` 时，说明依赖锁已改变，必须在外网重新制包；
  不要删除元数据或改写摘要绕过检查。
- 制包时提示 SHA-256 mismatch，立即丢弃该 Node 归档，检查镜像同步和传输过程。
- 制包时 Node 无法在 RHEL 7.9 执行，不能继续交付。确认使用的文件名包含
  `linux-x64-glibc-217`，而不是官方 `linux-x64` 包。
- `npm ci --offline` 报 cache miss，表示缓存不完整或 lock 文件引用发生变化；在可联网
  制包机重新运行完整制包，不要在内网临时开放互联网补依赖。

## 18. 运维基线

- 每天备份数据库、env、数据库模式、密钥和产物，保存异地副本。
- 每次升级前创建独立恢复点并保留旧离线包。
- 定期检查磁盘、日志增长、产物保留策略、数据库容量和备份校验结果。
- 定期导出和审阅审计日志，停用不再使用的账号与远端凭据。
- 定期演练服务器重启、数据库恢复和整包回滚。
- 任何密钥或数据库备份介质都应加密、限制访问并记录流转。
