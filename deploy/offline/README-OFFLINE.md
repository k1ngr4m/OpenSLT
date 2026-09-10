# OpenSLT 离线部署与运维手册

本文对应 **0.2.4** 发布，面向外网制包人员、内网部署人员和数据库管理员。
所有安装命令以仓库 `deploy/offline/` 中的脚本为准；应用版本唯一来源为根目录
`VERSION`，变更记录为 `RELEASES.json`。

**已有系统升级请直接阅读第 9 节，并先完成第 10 节备份。** 首次部署按第 1～8 节执行。
GitHub 自动提供的源码 ZIP/tar.gz 不包含 RPM、Python wheelhouse 或运行时，不能当作
离线安装包。真正可直接部署的包由外网 RHEL 制包机生成。

## 1. 部署范围与准备事项

### 1.1 主机分工

| 主机 | 环境与职责 |
| --- | --- |
| 外网开发/验证机 | Python 3.8+，Node.js 20+、npm 10+；运行测试及构建前端，可以是 macOS 或 Linux |
| 外网制包机 | RHEL 7.9 x86_64、glibc 2.17；具有可用的 RHEL RPM 源和 Python 包源，负责生成目标平台依赖 |
| 内网应用机 | RHEL 7.9 x86_64、systemd；安装时使用 root，运行服务使用 `openslt` 系统账号 |
| 数据库服务器 | MariaDB 5.5.68+ 或 MySQL 5.5.3+，InnoDB、utf8mb4；项目兼容性测试重点覆盖 MariaDB 5.5.68 与 MySQL 8 |
| 内网模型服务器（按需） | 提供应用机可达的 OpenAI-compatible 对话或 Embedding 接口；模型服务和权重需要另外部署 |

普通生产运行只需要已构建的前端，**不要求内网有 Node.js**。需要内网修改前端时才添加
`--bundle-node`。需要随包提供 Python 时添加 `--bundle-python`。

正式制包脚本拒绝在 macOS、ARM 和非 7.9 系统上执行，不应通过修改平台检查交付其他
架构的二进制依赖。本文未验证 RHEL 8/9 或其他发行版。

### 1.2 拓扑与网络

```text
浏览器 → Nginx :7777 → 本机 Uvicorn 127.0.0.1:4396
                         ├─ MySQL/MariaDB
                         ├─ 文件产物、持久化密钥、知识库文件及索引
                         ├─ SSH/SFTP → 业务资源、抓包机、解析机
                         └─ HTTP(S) → 内网 SVN、对话模型、Embedding 服务
```

| 发起方 | 目标 | 默认端口 | 用途 |
| --- | --- | --- | --- |
| 用户浏览器 | 应用机 | TCP 7777 | 页面、API、流式聊天、WebSocket |
| Nginx | 本机 API | TCP 4396，仅回环地址 | 反向代理 |
| 应用机 | 应用数据库、业务数据库 | TCP 3306 或实际端口 | 数据与迁移 |
| 应用机 | REM、市场、发单、SLNIC、解析机、SSH 跳板 | TCP 22 或实际端口 | SSH/SFTP |
| 应用机 | SVN、对话模型、Embedding | 服务实际 HTTP(S) 端口 | 同步知识与模型请求 |

离线指运行、安装和更新依赖时不需要互联网；模型功能仍需要可达的模型服务。
API 进程内包含任务调度和聊天取消状态，保持安装模板的**单个 Uvicorn worker**。
不要直接增加 `--workers` 或部署多个共同操作同一数据库的调度实例。

部署前记录系统架构、解释器路径、数据库模式、端口、目录空间、备份位置与负责人。
空间按 RPM/wheel 缓存、解压包、应用、上传文档、抓包产物和备份实际大小预留，项目没有
统一的容量上限或自动清理全部产物的策略。确认主机时钟和单位时间同步策略正常。

## 2. 外网准备源码与依赖

### 2.1 使用发布源码

在外网下载指定 tag 的完整源码，或在已有仓库中检出发布 tag：

```bash
git clone --branch 0.2.4 --depth 1 https://github.com/k1ngr4m/OpenSLT.git
cd OpenSLT
APP_VERSION="$(cat VERSION)"
python3 tools/release_metadata.py
```

版本校验应输出 `Release metadata is valid for OpenSLT 0.2.4`。`--version` 制包参数
只能断言版本与 `VERSION` 相同，不能覆盖应用版本。不要只替换前端产物而保留旧后端。

制包脚本复制当前工作树，制包前移除部署凭据、业务数据及与发布无关的本地文件。
不要把实际 `.env`、密钥或用户上传资料放入源码交付目录。

### 2.2 常规测试与前端构建

在外网开发/验证机项目根目录执行；以下 `.venv` 是构建环境，不是生产目录：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend test
npm --prefix frontend run build
test -f frontend/dist/index.html
```

将同一份源码和 `frontend/dist` 一起交给外网 RHEL 制包机。保留文件时间，或在制包机
重新构建；脚本发现源码、版本记录比 `dist/index.html` 新时会拒绝使用旧产物。

### 2.3 RHEL 制包机检查

在外网 RHEL 制包机的源码根目录执行：

```bash
PYTHON=/opt/rh/rh-python38/root/usr/bin/python3.8
cat /etc/os-release
uname -m
getconf GNU_LIBC_VERSION
"$PYTHON" --version
yum repolist enabled
"$PYTHON" tools/release_metadata.py
```

预期为 RHEL 7.9、`x86_64`、glibc 2.17，Python 不低于 3.8。制包与部署尽量使用相同
Python 主次版本，避免二进制 wheel 不匹配。Python 解释器需事先准备，默认 RPM 清单
不负责取得 RHSCL Python RPM。

`make-offline-package.sh` 在缺少 `repotrack`、`createrepo` 时会通过 yum 安装
`yum-utils` 与 `createrepo`，需要 root。RPM 源需能取得
[系统依赖清单](rpm-packages-rhel7.txt)中的全部包，包括：

- Nginx、curl、SELinux 管理工具；
- MariaDB 客户端、服务端和库，以及 libaio、numactl-libs；
- Subversion；
- LibreOffice Core、Writer、Calc，用于旧版 `.doc`、`.xls` 文档转换；RHEL 7.9 的
  `libreoffice-core` 已包含 headless 能力，不再要求旧的 `libreoffice-headless` 独立包。

如果收集阶段提示 LibreOffice 不可用，先检查软件源是否完整、可访问：

```bash
yum repolist enabled
repoquery --qf '%{name}' libreoffice-core libreoffice-writer libreoffice-calc
```

使用有效订阅的 RHEL 7 Server 官方源时，可启用 Base 和 Optional 后重新查询：

```bash
subscription-manager repos --enable=rhel-7-server-rpms --enable=rhel-7-server-optional-rpms
yum makecache
repoquery --qf '%{name}' libreoffice-core libreoffice-writer libreoffice-calc
```

使用 Satellite 或单位镜像时，由仓库管理员发布对应 RHEL 7 版本的上述包及依赖；
缺包也可能来自镜像裁剪、软件包过滤或仓库访问失败，仅凭缺包列表无法判断。
确认查询包含三个包名后，重试原制包命令，可继续复用 `--cache-dir`，无需清空缓存。

## 3. 生成离线安装包

### 3.1 普通生产包

在外网 RHEL 制包机的项目根目录执行，前端必须已完成第 2.2 节的测试和构建：

```bash
PYTHON=/opt/rh/rh-python38/root/usr/bin/python3.8
APP_VERSION="$(cat VERSION)"
chmod +x deploy/offline/*.sh
deploy/offline/make-offline-package.sh \
  --python "$PYTHON" \
  --version "$APP_VERSION" \
  --cache-dir /var/cache/openslt/packaging \
  --bundle-python
```

`--bundle-python` 仅打包制包机已有的 `/opt/rh/rh-python38`，`--python` 必须指向该目录
中的解释器。目标机已有相同且可用的 Python 时可省略此选项，安装时显式指定路径。
它不会替换操作系统的 `/usr/bin/python`。

### 3.2 带内网前端构建能力的包

需要在内网编辑 Vue、TypeScript 或 CSS 时执行：

```bash
deploy/offline/make-offline-package.sh \
  --python "$PYTHON" \
  --version "$APP_VERSION" \
  --cache-dir /var/cache/openslt/packaging \
  --bundle-python \
  --bundle-node
```

此模式会在制包机构建前端，因此不要求事先存在 `frontend/dist`。脚本固定使用
Node.js `20.20.2` 的 `linux-x64-glibc-217` 社区构建，校验发布方 SHA-256，并实际运行
Node/npm。它来自 [Node.js unofficial-builds](https://unofficial-builds.nodejs.org/)，
不能当作 Node.js 官方 Linux 二进制。需在目标同款 RHEL 环境验证。

脚本从 lock 文件收集 npm 缓存，再执行 `npm ci --offline`、前端测试和生产构建。
已有缓存也必须重新通过离线安装验证；结果与 `package-lock.json` 摘要绑定。

### 3.3 执行内容、缓存与镜像

一键脚本依次收集 RPM 依赖、创建 Python wheelhouse、在独立虚拟环境中执行
`pip install --no-index`、`pip check` 和后端测试，最后复制安装材料并生成校验文件。
带 Node 模式还会完成上述前端校验。任何步骤失败，都不能将中间材料当作交付包。

| 参数 | 作用 |
| --- | --- |
| `--output DIR` | 更改输出目录，默认源码根目录 `release/` |
| `--cache-dir DIR` | 复用 RPM、Node、npm、pip 缓存；必须位于项目目录之外 |
| `--refresh-cache` | 清除指定缓存目录后重建；只传专用制包缓存目录 |
| `--nginx-repo-url URL` | 指定 Nginx RHEL 7 镜像地址 |
| `--node-version VER` | 选择有 glibc-217 产物的完整 Node 20 版本 |
| `--node-base-url URL` | Node 下载镜像，保留 `v版本/` 目录结构 |
| `--node-archive FILE --node-shasums FILE` | 成对提供预下载 Node tar.gz 和 SHA 清单，仍校验文件名与摘要 |
| `--skip-python-tests` | 跳过 Python 离线回装验证和 pytest，仅用于诊断 |
| `--skip-frontend-tests` | 带 Node 制包时跳过前端测试，仅用于诊断 |
| `--skip-tests` | 同时跳过两类测试，仅用于诊断 |

正式发布不使用跳过测试参数。`--refresh-cache` 会删除整个指定目录，不要指向共享
数据目录。Node 下载配置需与 `--bundle-node` 同时使用。

Nginx 在现有软件源中不可用时，脚本临时添加 nginx.org RHEL 7 源并在退出时移除。
使用单位镜像时将示例地址替换为实际地址，并保留 `$basearch` 的单引号保护：

```bash
deploy/offline/make-offline-package.sh \
  --python "$PYTHON" --version "$APP_VERSION" --bundle-python \
  --nginx-repo-url 'https://yum.example.internal/nginx/rhel/7/$basearch/'
```

分步排查可使用：

```bash
deploy/offline/collect-rpms-rhel7.sh --output /var/tmp/openslt-rpms
deploy/offline/build-offline-bundle.sh \
  --python "$PYTHON" --version "$APP_VERSION" \
  --rpm-dir /var/tmp/openslt-rpms --bundle-python
```

自定义 RPM 清单通过收集脚本 `--package-file FILE` 传入；不能遗漏文档转换依赖后仍
宣称支持旧 Office 格式。软件源能否提供完整 RPM 闭包，以制包和断网安装实测为准。

## 4. 交付、校验与解压

输出文件为：

```text
release/openslt-offline-rhel7-x86_64-0.2.4.tar.gz
release/openslt-offline-rhel7-x86_64-0.2.4.tar.gz.sha256
```

包内结构如下，可选运行时仅在使用相应选项时存在：

```text
openslt-offline-rhel7-x86_64-0.2.4/
├── VERSION、RELEASES.json、README-OFFLINE.md、SHA256SUMS
├── configure.sh、install.sh、start.sh、deployment-config.sh
├── openslt.env.example、build-frontend.sh
├── app/                 源码、迁移、部署模板、frontend/dist
├── wheelhouse/          应用 wheel 与 Python 依赖
├── python-packages.txt  验证环境依赖清单
├── rpms/                packages/、keys/、requested-packages.txt
├── python-runtime/      可选 rh-python38
├── node-runtime/        可选 Node/npm 及 METADATA
└── npm-cache/           可选离线 npm 缓存
```

在外网 `release/` 目录和内网接收目录各校验一次，传输压缩包及其 `.sha256` 两个文件：

```bash
PACKAGE=openslt-offline-rhel7-x86_64-0.2.4
sha256sum -c "$PACKAGE.tar.gz.sha256"
tar -xzf "$PACKAGE.tar.gz"
cd "$PACKAGE"
sha256sum -c SHA256SUMS
cat VERSION
```

任何校验失败都应重新获取介质，不能在内网重写 `SHA256SUMS` 绕过检查。保留原始包，
部署后不要在解压包内修改配置或源码；生产配置写入 `/etc/openslt/openslt.env`。
在全新隔离 RHEL 测试机关闭外部仓库后完成第 5～8 节验收，才能认定该包可离线部署。

## 5. 选择数据库模式

| 模式 | 用途及实际影响 |
| --- | --- |
| `existing`（默认） | 使用已管理的本地或远程数据库；跳过 MariaDB RPM、实例配置、root 操作和服务启停，但仍对 OpenSLT 数据库执行应用迁移 |
| `provision` | 配置本机 MariaDB：安装 RPM，写入 InnoDB/utf8mb4 参数并重启服务；env 不存在时创建应用库和账号；不清理 root、匿名用户或测试库 |
| `initialize` | 在 `provision` 基础上处理空 root 密码，删除匿名用户、非 localhost root 及测试库；仅用于允许这些操作的独占新实例 |

`existing` 不等于“数据库只读”。建表、索引及字段变更由 Alembic 执行，需要目标库内
DDL 和 DML 权限。应用默认允许尝试创建不存在的数据库；由 DBA 预建数据库时，建议
在 env 中设置 `AUTO_CREATE_DATABASE=false`，避免依赖全局建库权限。

`configure.sh` 将模式写入 `/etc/openslt/database-mode`。`start.sh` 和 `install.sh`
默认读取它，文件不存在才回退到 `existing`。再次执行 `configure.sh` 时需重新明确
传入原模式，因为它自身默认使用 `existing`。

### 5.1 existing：准备数据库

由 DBA 在指定实例中执行，替换主机地址与密码；不要重复创建已有账号：

```sql
CREATE DATABASE openslt CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'openslt'@'应用机来源IP' IDENTIFIED BY '独立数据库密码';
GRANT ALL PRIVILEGES ON openslt.* TO 'openslt'@'应用机来源IP';
```

授权限制在 `openslt.*` 内，账号不应管理其他业务库。本机 TCP 连接通常使用
`127.0.0.1`，须匹配数据库实际识别的来源，不能仅凭存在 `localhost` 账号判断授权成功。

### 5.2 provision / initialize：准备本机管理凭据

管理账号已有密码时，用 root 创建权限 `0600` 的文件，例如 `/root/openslt-db-admin.cnf`：

```ini
[client]
user=root
password=实际管理密码
```

不要把密码放在命令行参数中。使用 `--mysql-defaults-file` 指定文件；配置脚本实际以
数据库 `root` 连接，并验证为 MariaDB。它不是配置外部 MySQL 8 实例的入口。

`initialize` 的清理语句面向旧 MariaDB 权限表，不能将其当作所有新版数据库的通用
初始化器。共享实例或已有安全策略的实例采用 `existing`，由 DBA 预先准备。

## 6. 首次安装

以下命令在内网应用机上以 root 身份、在已校验的离线包根目录执行。

### 6.1 existing 模式

先运行：

```bash
./configure.sh --database-mode existing
```

如果 env 不存在，脚本会创建模板并**以非零状态退出**，提示填入密码；这是预期行为。
编辑新建文件，不要使用 Shell `source` 载入密码配置：

```bash
vi /etc/openslt/openslt.env
```

示例内容（地址、密码需替换；端口可调整）：

```dotenv
DATABASE_HOST=127.0.0.1
DATABASE_PORT=3306
DATABASE_NAME=openslt
DATABASE_USER=openslt
DATABASE_PASSWORD="填写实际数据库密码"
AUTO_CREATE_DATABASE=false

BACKEND_PORT=4396
FRONTEND_PORT=7777
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD="设置首次登录密码"
```

再次执行：

```bash
./configure.sh --database-mode existing
./start.sh
```

`configure.sh` 安装非数据库 RPM、引导可选 Python、准备配置并按需开放前端 firewalld
端口。`start.sh` 安装应用、离线安装 Python 依赖、执行迁移、渲染服务、启动并检查健康。

### 6.2 provision 模式

**不要先运行 existing 模式生成占位 env**，否则 provision 会保留该文件，而不会自动
创建数据库账号或替换密码。若已有 env，先核实其中的库和账号已由 DBA 准备。

```bash
./configure.sh --database-mode provision \
  --mysql-defaults-file /root/openslt-db-admin.cnf
vi /etc/openslt/openslt.env
./start.sh
```

没有管理密码且空密码登录已获允许时，可省略 `--mysql-defaults-file`。env 不存在时
脚本生成随机应用密码，创建 `openslt@127.0.0.1`，然后写入 env。可通过
`--database-name`、`--database-user` 改名，名称只允许字母、数字、下划线。

`initialize` 的操作流程相同，但会实施第 5 节所述清理。确认独占且允许清理后才运行：

```bash
./configure.sh --database-mode initialize
vi /etc/openslt/openslt.env
./start.sh
```

若脚本为 root 生成密码，会将其保存为 `/root/.my.cnf`（`0600`），需按数据库管理凭据
纳入受控备份。不要将 initialize 用于登录失败或迁移失败的排障。

### 6.3 配置与脚本行为

- 自定义 Python：在 `configure.sh` 和 `start.sh` 均传入 `--python /实际路径/python3`。
- 自定义 env：使用 `--env-file FILE`，校验后仍复制到标准路径；运行服务只读取标准路径。
- `BACKEND_PORT`、`FRONTEND_PORT` 必须为不同的 `1024..65535` 端口。
- `DATABASE_URL` 与拆分的 `DATABASE_*` 只能二选一；推荐拆分字段，避免手工 URL 编码密码。
- env 不执行 Shell 展开；包含空格、`#` 等字符时使用正确引号，避免多行密码。
- 不设置 `JWT_SECRET` 和 `CREDENTIAL_ENCRYPTION_KEY` 时，首次运行会创建持久化密钥。
  已部署系统不得通过重新生成密钥来修复解密失败。
- 初始管理员配置只用于创建账号，不会重置已有管理员密码。模板默认
  `admin / shengli123`，首次部署应在启动前改为独立密码，或首次登录后立即修改。
- `--no-firewall` 只是不自动修改 firewalld，访问放行须另行完成。

`start.sh` 不会自动补装所有新增系统 RPM；首次安装依赖 `configure.sh`，升级见第 9 节。
重复启动同一版本仍执行迁移并重启；`--reinstall` 强制重装应用和虚拟环境。

## 7. 知识库、模型与智能用例配置

### 7.1 模型服务

在“模型管理”配置应用机实际可达的 Base URL（通常以 `/v1` 结尾）、API Key、模型 ID，
执行模型连接测试。Embedding 服务需提供 embeddings 接口；聊天及用例模型需提供
chat/completions 接口，流式聊天还要求 SSE 支持。

- **对话模型按账户隔离**：管理员和测试人员分别保存并启用自己的模型；未配置时不会
  回退使用其他人的 Key。智能助手与智能用例使用同一套当前账户对话模型配置。
- **Embedding 由系统管理员管理**：支持配置维度及自动检测，配置默认维度为 1024，
  应以模型实际输出为准；每个知识库独立绑定 Embedding 模型。
- HTTP 会明文传输凭据和内容，界面要求显式允许；使用 HTTPS 时需配置可信证书链。
- 离线安装包不包含任何 LLM/Embedding 权重，也不启动模型服务器。

升级迁移会把已有个人模型配置归入对应账户，原共享对话模型归首个系统管理员。
请逐账户验证当前模型、连接和历史权限，“我的 LLM”旧入口已统一到模型管理。

### 7.2 建立知识库

管理员在“知识库”中新建库，填写名称、选择已配置的 Embedding 模型。一个库可同时
使用 SVN 与文件上传：

1. SVN：填写一个或多个 HTTP(S) 仓库地址、账号、密码和允许同步的目录，先测试连接。
2. 上传：DOCX、XLSX 单文件最大 500 MiB，其他格式最大 50 MiB，提取文本最多 1000 万字符，同库不得重复上传同名文件；文档列表用于查看处理状态。
3. 执行同步或索引，待任务成功后检查文档数、失败文件、revision 和检索结果。
4. 在智能助手或智能用例中选择相应知识库。多个库时不要假定自动选择默认库。

支持 `.txt`、`.md`、`.csv`、`.json`、`.yaml`、`.yml`、`.htm`、`.html`、`.doc`、
`.docx`、`.xls`、`.xlsx`、`.pdf`。旧 `.doc`、`.xls` 依赖 LibreOffice 转换；PDF 使用
文本提取，不提供扫描件 OCR。先在应用机检查：

```bash
svn --version --quiet
libreoffice --headless --version
locale -a
```

至少提供能处理中文文件名的 UTF-8 locale。索引参数默认分块 1200 字符、重叠 150 字符、
检索数量 10；修改分块或模型相关配置后需重新建立匹配索引。同库索引串行，失败或取消
保留上次发布索引，但配置已变化时旧索引可能被判定为不可用。

旧单知识源记录会迁入“默认知识库”；启动时按条件复制旧索引与工作副本到库 ID 目录。
该迁移发生在配置的知识根目录内，**不会自动跨目录查找旧数据**，升级前检查第 9.2 节。

### 7.3 生成和修改用例

1. 在“智能用例”中选择知识库及已索引需求，按需输入补充提示词，提交生成任务。
2. 完成后在“我的最近任务”预览或下载 Excel。
3. 局部修改：勾选或全选用例，再勾选或全选允许修改的字段，填写修改方向。
4. 提交后生成新记录，原记录保留；新记录可以继续修改和下载。未选用例及字段保持原样。

拆分步骤时同时选择测试步骤与预期结果，保持数量一致。用例修改基于现有内容进行，
不重新检索需求；新增业务规则应在修改方向明确给出，并人工核对。用例结果是草稿，
执行前需复核。生成、预览、下载及修改记录仅本人可访问。

## 8. 安装验收

### 8.1 版本、健康与进程

默认端口下执行；自定义端口先从 env 核实：

```bash
cat /opt/openslt/VERSION
cat /var/lib/openslt/installed-bundle-version
/opt/openslt/.venv/bin/python -c 'from importlib.metadata import version; print(version("openslt"))'
curl -fsS http://127.0.0.1:4396/health
curl -fsSI http://127.0.0.1:7777/
systemctl is-enabled openslt-api nginx
systemctl is-active openslt-api nginx
nginx -t
journalctl -u openslt-api -n 100 --no-pager
```

源码 `VERSION`、安装标记、已安装 wheel、页面版本均应为 **0.2.4**；页面打开版本历史
应显示本次变更。健康检查成功仅说明服务可达，不代替功能验收。

由 DBA 检查迁移与表引擎：

```sql
SELECT VERSION(), @@default_storage_engine;
SELECT version_num FROM alembic_version;
SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'openslt' AND LEFT(TABLE_NAME, 2) = 't_'
  AND UPPER(COALESCE(ENGINE, '')) <> 'INNODB';
```

最后一条应为空；迁移版本与本次包的 `backend/migrations/versions/` 最新 revision
一致。若更改库名，同步修改查询条件。

### 8.2 功能验收表

| 检查项 | 通过条件 |
| --- | --- |
| 登录与权限 | 管理员可登录，初始密码已处理；tester、visitor 权限符合预期 |
| 模型隔离 | 两个账户的对话模型与历史不互相可见，Embedding 管理仅管理员可用 |
| 知识库 | SVN 和上传资料可索引，中文路径正常，检索返回对应库资料 |
| 旧文档 | 实际 `.doc`、`.xls` 样本转换并可检索 |
| 智能助手 | 通用/知识模式均可流式回答、停止和重新提问，知识回答可查看来源 |
| 智能用例 | 生成、预览、下载成功；单选/全选修改只影响选中内容，原稿保留 |
| 资源与工作流 | SSH/SFTP、数据库连接与终端可用；完成一条实际测试流程 |
| 产物与报告 | 解析、统计、复核及 HTML/Excel/PDF 报告可使用 |
| 重启与恢复 | 维护窗口重启服务/主机后历史、凭据、知识索引和产物仍可访问 |
| 备份演练 | 在隔离实例恢复数据库及相同时点文件并通过上述关键检查 |

正式目标机验证应同时保留执行人、时间、包摘要、操作系统和数据库版本、失败项与处理结果。

## 9. 从旧版升级到 0.2.4

本次更新包含数据库迁移、新 Python 运行依赖、LibreOffice 系统依赖、Nginx 流式配置及
知识库存储路径修正。**不能仅复制前端或 pip 安装应用 wheel。**

### 9.1 维护窗口与备份

1. 在隔离环境用生产备份演练升级；确认新包及校验文件齐全。
2. 停止提交任务，等待已有测速、知识索引、用例生成结束或安全取消。
3. 记录当前版本、数据库模式、端口和自定义 systemd/Nginx 配置。
4. 停止 API，按第 10 节备份数据库与文件，之后保持停服直到升级完成。

```bash
cat /var/lib/openslt/installed-bundle-version
cat /etc/openslt/database-mode
systemctl stop openslt-api
```

安装脚本会替换应用文件、重建 `.venv`，不会在替换前替你等待业务结束或安全停止
所有工作，因此必须先停 API。保留上一版离线包及升级前备份。

### 9.2 确认旧知识数据的位置

0.2.4 的服务模板将 `KNOWLEDGE_ROOT` 固定默认到 `/var/lib/openslt/knowledge`，与安装
迁移脚本一致。旧模板缺少该项，未在 env 或 systemd override 中指定时可能使用
`/opt/openslt/backend/data/knowledge`。

检查实际配置和两个目录；仅记录知识目录配置，不导出全部服务环境中的密码：

```bash
systemctl show openslt-api -p WorkingDirectory
grep -n 'KNOWLEDGE_ROOT' /etc/openslt/openslt.env /etc/systemd/system/openslt-api.service
ls -ld /var/lib/openslt/knowledge /opt/openslt/backend/data/knowledge
```

另检查
`/etc/systemd/system/openslt-api.service.d/` 下本地 override。没有匹配配置或目录不存在
本身不代表数据丢失，应以旧运行配置与已存在文件判断。

**停服并备份后，旧目录有数据且目标目录为空时**，使用以下保留原文件的复制方式：

```bash
OLD_KNOWLEDGE=/opt/openslt/backend/data/knowledge
NEW_KNOWLEDGE=/var/lib/openslt/knowledge
if [[ -d "$OLD_KNOWLEDGE" ]]; then
  if [[ -d "$NEW_KNOWLEDGE" && -n "$(find "$NEW_KNOWLEDGE" -mindepth 1 -print -quit)" ]]; then
    printf '目标知识目录已有数据，请核对两份内容后迁移，禁止直接覆盖。\n' >&2
  else
    install -d -o openslt -g openslt -m 0700 "$NEW_KNOWLEDGE"
    cp -a "$OLD_KNOWLEDGE/." "$NEW_KNOWLEDGE/"
    chown -R openslt:openslt "$NEW_KNOWLEDGE"
  fi
fi
```

两个目录都有数据时先核对哪一份属于生产；不要按修改时间盲目合并 SQLite 索引或上传
目录。使用其他自定义目录的站点同样先备份并迁入标准路径；env/override 仍指定旧路径
会覆盖服务默认值，应在确认迁移完成后统一配置。保留旧目录直到新版本验收完成。

### 9.3 安装新依赖与升级应用

在**新版本离线包目录**校验后执行：

```bash
sha256sum -c SHA256SUMS
./install.sh --rpms-only --database-mode existing
./start.sh
```

第一条安装命令只补装非数据库 RPM，包含 LibreOffice，不升级或接管已有 MariaDB。
`start.sh` 随后读取此前保存的数据库模式；原模式为 provision/initialize 时仍会启动
受管 MariaDB，但不会重新执行 configure 阶段的清理。数据库软件升级由 DBA 单独安排。

自定义 Python 路径继续传给 `start.sh`；安装标记已是本版但需要恢复损坏文件时使用
`./start.sh --reinstall`。不要为升级重新生成 env 或运行 initialize。

启动会执行 `alembic upgrade head`，安装新版前端与 Nginx 配置，再进行健康检查。
迁移错误时查看第一条异常并保留停服状态；不要删除已有知识表、手工 stamp 版本或清空
`alembic_version`。本版已修复部分旧 MySQL 索引限制及中断恢复问题，但不是任意错误都
可通过反复启动解决。

安装会重写 `/etc/systemd/system/openslt-api.service` 和
`/etc/nginx/conf.d/openslt.conf`；如有 HTTPS、访问控制等本地改动，按备份重新合并，
保留本版聊天关闭缓冲和 WebSocket Upgrade 配置，不要直接覆盖回旧模板。完成第 8 节验收。

## 10. 备份

### 10.1 备份范围

| 数据 | 内容 |
| --- | --- |
| 应用数据库 | 用户、资源凭据密文、工作流、任务、模型配置、聊天、知识库元数据、用例记录 |
| `/etc/openslt` | env 和数据库模式 |
| `/var/lib/openslt` | 产物、知识库上传文件、SVN 副本、索引、密钥、安装标记 |
| `/var/log/openslt` | 运维与审计排查所需日志 |
| systemd / Nginx 配置 | 服务、端口、TLS、代理及站点自定义配置；外部证书及 override 另行纳入 |
| 旧知识目录 | 升级前仍使用的 `/opt/openslt/backend/data/knowledge` 或其他自定义目录 |
| 发布介质 | 当前与上一版本的离线包、校验文件、部署记录 |

数据库和知识文件必须是相同停服时点。单独恢复数据库不能恢复上传原文或用例 Excel；
丢失 `credential_encryption_key` 会导致已有资源、SVN、模型凭据无法解密。
如果 env 显式配置密钥，它同样是备份的一部分。

### 10.2 停服备份示例

由 DBA 准备具备备份权限的 `/root/openslt-backup.cnf`（`0600`），写入真实实例信息：

```ini
[client]
host=数据库地址
port=3306
user=备份账号
password=备份密码
```

以下以数据库名 `openslt` 为例，在应用机 root Bash 中执行。目标机没有数据库客户端时，
由 DBA 在可达的管理机备份数据库，并与文件备份一起保存；existing 模式不保证安装客户端。

```bash
set -Eeuo pipefail
umask 077
BACKUP_DIR="/srv/openslt-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
systemctl stop openslt-api
mysqldump --defaults-extra-file=/root/openslt-backup.cnf \
  --single-transaction --quick --hex-blob --databases openslt \
  > "$BACKUP_DIR/database.sql"
BACKUP_PATHS=(etc/openslt var/lib/openslt var/log/openslt \
  etc/systemd/system/openslt-api.service etc/nginx/conf.d/openslt.conf)
if [[ -d /opt/openslt/backend/data/knowledge ]]; then
  BACKUP_PATHS+=(opt/openslt/backend/data/knowledge)
fi
if [[ -d /etc/systemd/system/openslt-api.service.d ]]; then
  BACKUP_PATHS+=(etc/systemd/system/openslt-api.service.d)
fi
tar -C / -czf "$BACKUP_DIR/files.tar.gz" "${BACKUP_PATHS[@]}"
(
  cd "$BACKUP_DIR"
  sha256sum database.sql files.tar.gz > SHA256SUMS
  sha256sum -c SHA256SUMS
)
```

使用与服务器兼容的客户端；MySQL 客户端的 GTID、tablespace 或 column statistics
选项由 DBA 根据版本添加，不直接套用到 MariaDB 5.5。备份未成功前不要开始升级。
常规备份完成后可重启 API；升级备份完成后保持停服。备份介质需限制权限、加密并异地保存。

## 11. 恢复与回滚

### 11.1 恢复演练

先在隔离主机、空数据库实例恢复，验证备份可用，避免覆盖运行中的生产库：

1. 使用备份时对应版本的离线包准备系统依赖和 Python，保持 API 停止。
2. 在备份目录执行 `sha256sum -c SHA256SUMS`。
3. 由 DBA 确认恢复目标地址、库名和空库状态，再导入：

   ```bash
   mysql --defaults-extra-file=/root/openslt-restore.cnf < database.sql
   ```

4. 将 `files.tar.gz` 恢复到隔离主机。原 env 内的数据库地址可能仍指向生产，**启动前**
   改为恢复实例；隔离或禁用对生产业务资源、模型和 SVN 的访问，防止恢复的任务连接生产。

   ```bash
   systemctl stop openslt-api
   tar -C / -xzf files.tar.gz
   chown root:openslt /etc/openslt/openslt.env
   chmod 0640 /etc/openslt/openslt.env
   chown -R openslt:openslt /var/lib/openslt /var/log/openslt
   chmod 0700 /var/lib/openslt/secrets /var/lib/openslt/knowledge
   find /var/lib/openslt/secrets -maxdepth 1 -type f -exec chmod 0600 {} \;
   ```

5. 从对应版本原始离线包执行 `./start.sh --reinstall`，重新合并必要的本地代理配置。
6. 核验登录、凭据解密、知识原文和索引、历史任务、报告及用例下载。

原地恢复会覆盖文件和数据，必须先保存当前故障现场，由 DBA 在明确的恢复窗口执行；
不要直接向含有较新表结构的生产库导入旧 dump 并假定已经完整回退。

### 11.2 回滚规则

项目没有自动回滚脚本。前向迁移后只回退应用代码不构成有效回滚，也不建议直接运行
`alembic downgrade`：多知识库和上传数据的变更可能不允许无损降级。

完整回滚顺序：停 API → 保存故障日志 → 恢复升级前数据库 → 恢复同一时点 env、密钥、
知识数据和产物 → 使用上一版离线包重装 → 合并原配置 → 验收。旧版本若使用应用目录
存知识，恢复该目录及原配置，不能让代码和知识根目录错配。

## 12. 日常运维与内网前端修改

### 12.1 目录与服务

| 路径 | 说明 |
| --- | --- |
| `/opt/openslt` | 应用、迁移、前端和 `.venv`，root 管理 |
| `/etc/openslt/openslt.env` | 生产配置，`root:openslt 0640` |
| `/etc/openslt/database-mode` | 已选择的数据库模式 |
| `/var/lib/openslt/artifacts` | 测试及用例产物 |
| `/var/lib/openslt/knowledge` | 知识库根目录，`openslt:openslt 0700` |
| `/var/lib/openslt/secrets` | 持久化密钥，目录 `0700`、文件 `0600` |
| `/var/log/openslt` | 应用日志 |
| `/opt/openslt-node` | 可选隔离 Node/npm 及 `METADATA` |
| `/var/cache/openslt/npm` | 可选离线 npm 缓存 |

```bash
systemctl status openslt-api nginx --no-pager
journalctl -u openslt-api -n 200 --no-pager
nginx -t
systemctl restart openslt-api
systemctl reload nginx
```

existing 模式遇到应用故障时不要擅自重启共享数据库。持久化任务可能在服务重启后恢复，
运维窗口前先处理运行中的业务任务。

### 12.2 修改前端

仅适用于带 `--bundle-node` 的包。修改前备份源码并同步回受版本控制的仓库，以 root 执行：

```bash
/opt/openslt-node/bin/node --version
cat /opt/openslt-node/METADATA
/opt/openslt/build-frontend.sh
```

脚本校验 lock 文件、执行 `npm ci --offline`、前端测试、生产构建、SELinux restorecon、
`nginx -t` 并 reload。`--no-reload` 保留构建和配置校验但不 reload；`--skip-tests`
仅用于诊断。修改依赖或 lock 文件后必须回外网重建缓存及离线包，不能靠旧缓存增加依赖。

升级会覆盖生产目录改动。仅修改前端不需要重启 API；后端改动需重新打包、验证与发布。
不要在离线机运行仓库 `start-web.sh` 或 `deploy/scripts/install.sh`，它们可能访问
在线 Python/npm 包源。

### 12.3 Nginx、SELinux 与 HTTPS

安装器设置静态目录的 SELinux 上下文，工具可用时开启 `httpd_can_network_connect`。
它不负责将任意自定义前端端口添加到 SELinux `http_port_t`，遇到绑定拒绝由运维检查
现有端口标签并按本机策略配置，不能仅靠 firewalld 放行解决。

启用 HTTPS 时使用单位 CA 和受控证书，保留以下代理特性：

- `/api/v1/chat/`：关闭代理缓冲，允许长时间流式响应；
- `/api/v1/ws/`：HTTP/1.1、Upgrade/Connection 和长连接；
- `/api/`：正确代理到当前回环后端端口；
- 静态站点：SPA 路由回退到 `index.html`。

## 13. 故障排查

| 现象 | 检查与处理 |
| --- | --- |
| `release/` 没有包 | 查制包日志第一处失败；只有所有阶段成功才输出最终压缩包 |
| 平台不支持或 Python 缺失 | 核实 RHEL 7.9、x86_64、解释器路径；没有 Python 时重新制带 `--bundle-python` 的包 |
| Nginx/RPM 找不到 | 核实已启用 RHEL 仓库及镜像；使用真实 `--nginx-repo-url`，不要照抄示例域名 |
| `frontend/dist is stale` | 同一源码重新执行前端生产构建；版本元数据更新也需重建 |
| wheel 无法安装 | 确认目标 Python 主次版本及 CPU 架构与制包一致，使用完整 wheelhouse |
| npm cache miss / lock 不匹配 | 在外网重新制带 Node 的包；不修改摘要绕过校验 |
| env 含 `CHANGE_ME` | 修改实际配置项后再运行 configure；注释会被占位符检查忽略 |
| 数据库连接失败 | 检查实例、端口、来源授权、密码和目标库；existing 模式不会使用 root 自动修复 |
| 迁移报表已存在或索引过长 | 确认部署的是本版完整源码与依赖；保留第一处错误，禁止手工删除知识表或版本表 |
| 页面正常但 API 不通 | 对照 Nginx upstream 与 systemd 后端端口，查看 SELinux、代理与服务日志 |
| 聊天不流式或断流 | 核实模型支持 SSE、Nginx 未缓冲、中间代理允许长响应及应用超时配置 |
| 索引未就绪 | 检查知识库 Embedding 绑定、维度、最近索引任务及失败文档，配置变化后重建 |
| SVN 中文路径失败 | 检查 UTF-8 locale、账号白名单权限及错误详情；确认应用机可直达 SVN |
| `.doc` / `.xls` 提取失败 | 验证 LibreOffice 三类依赖及实际样本，修复后重新索引 |
| 升级后知识列表为空或 permission denied | 核对第 9.2 节目录及 owner；确认 env、override 与迁移使用同一知识根目录 |
| 已保存密码或 Key 无法解密 | 恢复与数据库同一时点的密钥或 env；不能生成新密钥覆盖旧值 |
| PDF 中文显示异常 | 用实际客户端打开报告；必要时在验证机用 Poppler 渲染并配置中文 CMap，检查字体支持 |

查看监听端口与近期失败日志：

```bash
ss -lntp
journalctl -u openslt-api -n 200 --no-pager
journalctl -u nginx -n 100 --no-pager
nginx -t
```

对外提供故障日志前删除密码、API Key、数据库连接串及业务原文。日常按数据增长制定备份
与保留策略，并定期演练重启、恢复和上一版本回滚。
