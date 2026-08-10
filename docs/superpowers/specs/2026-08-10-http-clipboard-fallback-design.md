# HTTP 环境复制兼容设计

## 目标

修复正式环境通过内网 HTTP 地址访问 OpenSLT 时，Windows `editcap` 命令、运行编号和 Trace ID 的复制按钮不可用的问题，同时保持 HTTPS 环境优先使用现代 Clipboard API。

## 根因

正式部署默认由 Nginx 通过 `http://<LAN_IP>:7777` 提供页面。当前三个复制入口均直接调用 `navigator.clipboard.writeText()`，但该 API 仅在安全上下文中可用。浏览器从非回环内网 HTTP 地址访问时，`navigator.clipboard` 通常不存在；即使 API 存在，也可能因浏览器权限或 Permissions Policy 拒绝写入。

现有测试只注入了必定成功的 `navigator.clipboard.writeText()`，没有覆盖正式部署的非安全上下文，因此未发现该问题。Windows 命令中的空格、引号、反斜杠和 UNC 路径不会导致 Clipboard API 调用失败。

## 方案

### 共享复制工具

新增单一的前端 `copyText(value)` 工具，三个复制入口统一调用：

1. `navigator.clipboard.writeText` 可用时优先调用。
2. Clipboard API 不存在或写入被拒绝时，降级到临时 `textarea` 与 `document.execCommand('copy')`。
3. 降级复制必须直接由点击事件触发；临时元素放置在视口外并设为只读，避免页面滚动和软键盘干扰。
4. 无论成功或失败，都移除临时元素并恢复调用前的焦点和文本选区。
5. 两条路径都失败时抛出错误，由调用方显示对应的失败提示。

`document.execCommand('copy')` 已被弃用，但浏览器没有可在非安全 HTTP 上替代它的标准化异步剪贴板接口。这里将其严格限制为兼容降级路径；正式环境启用 HTTPS 仍是长期部署方向。

### 调用入口

- Windows `editcap` 命令：成功时继续提示“Windows editcap 命令已复制”，彻底失败时继续提示“复制失败，请手动选择命令”。
- 运行编号：成功时保留当前成功提示，失败时提示用户手动复制运行编号。
- Trace ID：成功时保留当前成功提示，失败时提示用户手动复制 Trace ID。

三个入口不再直接访问 `navigator.clipboard`，避免后续出现不一致的兼容行为。

## 错误处理

- Clipboard API 缺失：直接尝试兼容降级。
- Clipboard API Promise 拒绝：尝试兼容降级。
- `document.execCommand('copy')` 返回 `false`、不可用或抛出异常：判定复制失败。
- 失败提示不暴露浏览器异常详情，也不记录待复制内容，避免将命令或标识写入前端日志。
- 空值仍由各业务入口按现有规则忽略，不创建临时元素。

## 测试

共享工具测试覆盖：

- Clipboard API 成功时逐字复制原文本，且不调用降级路径。
- `navigator.clipboard` 缺失时降级成功。
- Clipboard API 拒绝时降级成功。
- 文本包含双引号、空格、UNC 反斜杠和 `<>&` 时保持原值。
- 降级返回 `false` 或抛出异常时报告失败。
- 临时元素始终清理，并恢复原焦点和文本选区。

调用方测试覆盖三个入口的成功提示和最终失败提示。完成后运行相关前端测试、完整前端测试和生产构建。

## 范围

- 不修改 `editcap` 命令生成逻辑、后端接口或数据模型。
- 不修改 Nginx、证书和正式环境部署方式。
- 不引入第三方剪贴板依赖。
- 不为与系统剪贴板无关的“复制资源”等业务复制操作改造代码。

## 发布说明

在 `RELEASES.json` 的 `unreleased` 中增加一条 `fixed`：修复内网 HTTP 环境下 Windows `editcap` 命令、运行编号和 Trace ID 无法通过按钮复制的问题。
