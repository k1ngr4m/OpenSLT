# EF-VI 发单工具仿真桩

这是供 OpenSLT 工作流、配置管理和终端联调使用的仿真程序。它不会登录 REM，
不会创建网络连接，也不会发送真实委托或生成交易测速流量。所有动作结果都带有
`SIMULATION ONLY`、`SIM_EVENT` 或 `SIM-` 标识。

## 源码运行

要求 Python 3.8 或更高版本：

```bash
python3 ees_ef_vi_trader_binary_api_test.py ees_ef_vi_trader_api_test_conf.xml
```

支持的 OpenSLT 动作为：

```text
new_order
new_order_simple
new_quote
new_quote_simple
new_arbi_order
new_arbi_order_simple
cxl_order
cxl_quote
stop_order
```

程序也接受旧工具文档中的紧凑写法，例如 `new_ordersimple`、`newquote`、
`newarbiorder`、`cxlorder` 和 `cxlquote`。输入 `help` 查看命令，输入 `exit` 或按 `Ctrl+C`
退出。

独立运行仿真工具测试：

```bash
python -m pytest -q tools/ees_ef_vi_trader_binary_api_test/tests
```

## 构建 Linux x86_64 发布包

构建机需要 Docker，首次构建需要访问镜像仓库和 Python 包仓库：

```bash
./build-linux-x86_64.sh
```

构建使用 manylinux2014、Python 3.8 和 PyInstaller 6.10，输出位于：

```text
build/ees_ef_vi_trader_binary_api_test/
```

脚本随后在 `--network none` 容器中执行一次 `new_order` 冒烟测试。发布包采用
PyInstaller 单目录模式，必须整体部署，不能只复制其中的 ELF 文件。

## 部署到发单服务器

将生成的 `.tar.gz` 上传到 Linux x86_64 服务器并解压：

```bash
tar -xzf ees_ef_vi_trader_binary_api_test-linux-x86_64.tar.gz
cd ees_ef_vi_trader_binary_api_test
chmod +x ./ees_ef_vi_trader_binary_api_test
sha256sum -c SHA256SUMS
./ees_ef_vi_trader_binary_api_test ees_ef_vi_trader_api_test_conf.xml
```

在 OpenSLT 中将发单资源配置为：

- 远端路径：上述解压目录的绝对路径；
- `order_tool`：`ees_ef_vi_trader_binary_api_test`；
- 动作能力：选择需要联调的九个标准动作；
- 服务器需安装 `tmux`，供 OpenSLT 创建和清理发单会话。

样例 XML 使用 RFC 5737 文档专用地址和虚构账号。它可以复制后修改，但仿真程序
只校验和汇总 XML，不会使用其中的地址或凭据建立连接。配置摘要不会输出任何 XML
属性值。
