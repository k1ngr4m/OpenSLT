# OpenSLT Visual Design 2.0：前端视觉圣经

> 文档状态：2.0 目标态规范  
> 适用范围：OpenSLT Web 前端的产品设计、交互设计与工程实现  
> 优先级：本文定义的 2.0 规则高于现有 1.x 页面样式；现有截图仅代表迁移前状态，不作为 2.0 视觉基准。

## 1. 视觉定位

OpenSLT 不应像普通后台管理系统。用户第一眼应当感知到：

> 这是一个用于观察、控制和诊断复杂测试系统的专业工程工具。

视觉语言围绕三个核心概念建立。

### 1.1 CONTROL｜控制

用户始终知道：

- 当前系统状态；
- 当前操作对象；
- 当前运行阶段；
- 下一步能够执行什么。

### 1.2 OBSERVE｜观察

数据是视觉中心。日志、指标、运行状态、设备、拓扑和测试流程应像仪表数据一样被组织，而不是被包装成普通 CRUD 数据。

### 1.3 TRACE｜追溯

运行、节点、错误、日志、设备和时间必须形成连续证据链。页面结构需要帮助用户从系统状态逐层追到具体事件，而不是迫使用户在多个无关卡片之间跳转。

一句话定义：**现代测试实验室的数字控制台。**

## 2. 整体视觉气质

### 2.1 关键词

- Precision
- Quiet Tech
- Instrument UI
- Engineering
- Observable
- Structured

### 2.2 应当呈现

- 深色 Graphite 导航；
- 冷中性工作区；
- 白色结构化表面；
- 信号绿色交互；
- 具有固定含义的数据色；
- 等宽工程数据；
- 极少阴影；
- 紧凑信息密度。

### 2.3 必须避免

- SaaS 模板感；
- 大量彩色卡片；
- Card inside Card inside Card；
- 过度圆润和大量 Pill Button；
- 大面积渐变和毛玻璃泛滥；
- 插画化业务后台；
- 过度 Cyberpunk；
- Dashboard KPI 大数字堆砌；
- 用颜色做无意义装饰。

最终效果应更接近 **Professional Engineering Software**，而不是 **Enterprise Admin Template**。

## 3. 核心设计原则

### 3.1 信息优先

- 页面首先回答“状态、对象、阶段、动作”。
- 数据区获得首屏最多空间，标题和描述不得挤占工作区。
- 表格、日志、拓扑和流程图优先保证扫描效率与内容完整性。

### 3.2 颜色等于数据

- 绿色不是大面积品牌底色，而是系统操作信号色。
- Success、Running、Waiting、Failed 等颜色必须保持稳定语义。
- 没有数据含义的区域使用中性色。

### 3.3 结构胜于装饰

- 主要依靠背景层级、1px 边界、分隔线和留白组织信息。
- 普通页面几乎不使用阴影。
- 大区域优先形成连续工作面，不拆成大量漂浮卡片。

### 3.4 紧凑但不拥挤

- 默认采用 Compact Density。
- 控件更紧凑，但文字、点击目标、错误反馈和任务完成能力不能被压缩掉。
- 同组元素靠近，不同层级通过分区和边界拉开。

### 3.5 动效像仪器响应

- 动效短、准、可预测。
- 动画只解释交互、状态或空间变化。
- 运行状态只让 Signal 或状态指示器变化，整个容器不得闪烁。

## 4. 设计令牌体系

2.0 按三层令牌组织：

```text
Primitive 原始值
        ↓
Semantic 语义角色
        ↓
Component 组件状态
```

- **Primitive**：颜色、字号、间距、圆角、时长等原始值。
- **Semantic**：Canvas、Surface、Text、Border、Interactive、Status 等用途。
- **Component**：Button、Input、Table、Workflow Node、Timeline 等具体组件状态。

组件不得直接依赖无语义的临时色值。专用图形可使用局部令牌，但必须说明含义。

## 5. 色彩系统

### 5.1 Canvas 与 Surface

| 语义 | 色值 | 用途 |
|---|---:|---|
| App Background | `#F5F7F8` | 应用主画布、Level 0 |
| Secondary Background | `#F0F3F4` | 大区域工作区、Level 1 |
| Raised Surface | `#FFFFFF` | Panel、表格、表单、Level 2/3 |
| Panel Border | `#E1E7E9` | 主面板边界 |
| Control Border | `#D6DFE2` | 输入、选择器等控件边界 |
| Strong Border | `#AEBFC4` | 悬停、强调轮廓 |

### 5.2 Navigation

| 语义 | 色值 | 用途 |
|---|---:|---|
| Sidebar | `#11191D` | 主导航背景 |
| Sidebar Secondary | `#172227` | 次级区域、分隔区域 |
| Sidebar Hover | `#1D2B30` | 导航悬停 |
| Sidebar Selected | `#20343A` | 当前导航项 |

导航采用 Charcoal Blue / Graphite，而不是明显偏绿的深色。

### 5.3 Text

| 语义 | 色值 | 用途 |
|---|---:|---|
| Primary | `#182328` | 标题、正文重点、核心数值 |
| Secondary | `#58686E` | 正文说明、标签 |
| Tertiary | `#849298` | 元数据、帮助信息 |
| Disabled | `#AEB8BC` | 禁用文字、不可用信息 |
| Placeholder | `#98A5AA` | 输入占位符 |

关键状态、错误原因和任务指令不得使用 Tertiary 或 Disabled 色。

### 5.4 Brand / Interaction

| 语义 | 色值 | 用途 |
|---|---:|---|
| Primary | `#00A88F` | Primary Button、Focus、Active、Interactive、Current、Key Metric |
| Hover | `#008C79` | 可交互元素悬停 |
| Pressed | `#007565` | 按下状态 |
| Soft | `#DDF5F0` | 选中背景、弱提示、Signal 衬底 |
| Focus Ring | `rgba(0,168,143,.12)` | 控件与节点焦点环 |

使用约束：

- 绿色只表示可操作、激活、当前或关键数据。
- 选中状态优先使用 Signal Line、边框或文字，不使用大面积绿色填充。
- 一个操作域只能有一个视觉最强的 Primary Action。

### 5.5 Data Colors

| 数据状态 | 色值 | 默认表现 |
|---|---:|---|
| Success | `#188866` | `● PASSED`、完成、健康 |
| Running | `#3378B7` | `◉ RUNNING`、活动路径 |
| Waiting | `#B7791F` | `○ WAITING`、待处理 |
| Failed | `#D1495B` | `× FAILED`、失败路径 |
| Paused | `#73838A` | 暂停 |
| Unknown | `#9CA8AD` | 未知、不可判定 |

重要状态可使用 Soft Tag。失败标签推荐：

```css
background: #FCECEE;
color: #C63D50;
border-radius: 4px;
```

成功状态通常只显示 `● PASSED`，不铺设绿色背景。

### 5.6 Terminal Colors

| 角色 | 色值 |
|---|---:|
| Background | `#11181D` |
| Toolbar | `#182126` |
| Code | `#D7E2E5` |
| Timestamp | `#72848A` |
| Info | `#62A6D8` |
| Success | `#65C49D` |
| Warning | `#E0A555` |
| Error | `#E56A76` |

终端配色服务于扫描性，而不是营造“酷”的黑色界面。

## 6. 表面层级

### LEVEL 0 — Canvas

- 背景：`#F5F7F8`。
- 承载整个页面，不添加阴影。

### LEVEL 1 — Workspace

- 背景：`#F0F3F4` 或专用画布色。
- 用于 Run Workspace、Workflow Canvas、资源工作区等大区域。
- 通过空间与分隔组织功能，不做漂浮卡片。

### LEVEL 2 — Panel

```css
background: #FFFFFF;
border: 1px solid #E1E7E9;
border-radius: 8px;
box-shadow: none;
```

用于主要信息区、表格容器、属性面板和结构化表单。

### LEVEL 3 — Floating

用于 Drawer、Popover、Dialog、Context Menu。

```css
background: #FFFFFF;
box-shadow: 0 16px 40px rgba(21, 32, 37, .12);
```

Floating 是明显阴影的主要合法场景。

## 7. 圆角与边界

| 类型 | 数值 | 用途 |
|---|---:|---|
| Control | `6px` | Button、Input、Select |
| Panel | `8px` | 常规面板、节点、信息块 |
| Large Panel | `10px` | 大型工作区、特殊容器 |
| Dialog | `12px` | Dialog、Message Box |
| Status Tag | `4px` | 状态标签 |

禁止：

- `16px`、`20px` 的普通卡片圆角；
- 大量胶囊按钮；
- 通过圆角大小制造无意义层级；
- 面板内重复套用同等级圆角卡片。

## 8. 间距与密度

### 8.1 基础间距

使用 4px 基础节奏：

| Token | 数值 | 典型用途 |
|---|---:|---|
| `space-1` | `4px` | 微间距、图标与文字 |
| `space-2` | `8px` | 同组操作、紧凑内边距 |
| `space-3` | `12px` | 表单字段、工具栏控件 |
| `space-4` | `16px` | Panel 内边距、区块间距 |
| `space-5` | `20px` | 复杂面板、页面次级间距 |
| `space-6` | `24px` | 页面水平留白、大区块 |
| `space-8` | `32px` | 宽屏页面边距 |

### 8.2 默认密度

| 组件 | 高度 |
|---|---:|
| Button | `34px` |
| Input / Select | `34px` |
| Large Input | `40px` |
| Table Header | `36px` |
| Table Row | `44–46px` |
| Navigation Item | `38px` |
| Toolbar | `40–44px` |

紧凑不等于缩小文字。优先减少无效 padding 和容器嵌套。

## 9. 字体与数据排版

### 9.1 字体族

**Interface**

```css
font-family: "PingFang SC", Inter, system-ui, sans-serif;
```

用于导航、标题、正文、表单和按钮。中文环境优先 PingFang SC；Inter 主要服务于英文与数字界面。

**Data**

```css
font-family: "JetBrains Mono", "Cascadia Code", Consolas, monospace;
font-variant-numeric: tabular-nums;
```

用于运行编号、Trace ID、时间戳、IP、端口、命令、日志、SQL、XML、百分比和机器数据。

### 9.2 严格字号体系

整个产品限定为以下 8 个字号：

| 字号 | 层级 | 用途 |
|---:|---|---|
| `26px` | Page Title | 页面主标题、Dashboard 关键值 |
| `20px` | Entity Title | 运行、资源、节点等当前对象 |
| `16px` | Section | Panel 与主要区块标题 |
| `14px` | Body | 主要正文、表单说明 |
| `13px` | Control | Button、Input、Select、导航文字 |
| `12px` | Table | 表格正文、常规数据 |
| `11px` | Metadata | 时间、趋势、辅助标签、Mono Identifier |
| `10px` | Micro Label | Kicker、类型、极次级元数据 |

规则：

- 不新增 15px、17px、18px、21px、23px、28px 等中间字号。
- 标题靠字号、字重和位置建立层级，不依赖彩色。
- 机器数据全部使用等宽字体或 `tabular-nums`。
- 解释性正文建议行高 `1.5–1.65`，日志固定为 `1.65`。
- 长标识符单行省略时必须提供查看完整值的方式。

## 10. 图标系统

- 全部采用线性图标。
- Stroke：`1.7px`。
- 尺寸限定：`14px`、`16px`、`18px`、`20px`。
- 常用隐喻：Dashboard、Box、Server、Activity、Workflow、Terminal、Database、Settings。
- 图标颜色继承文字，不使用独立彩色图标。
- 禁止填充图标、3D 图标和 Emoji。
- 状态图标是例外，但必须使用 Data Colors。
- 单独图标按钮必须提供 Tooltip 与可访问名称。

## 11. 应用骨架

### 11.1 桌面结构

```text
┌──────────┬────────────────────────────────┐
│          │ Top Navigation                 │
│ Sidebar  ├────────────────────────────────┤
│          │                                │
│          │ Workspace                      │
│          │                                │
└──────────┴────────────────────────────────┘
```

| 区域 | 尺寸 |
|---|---:|
| Sidebar | `220px` |
| Collapsed Sidebar | `60px` |
| Topbar | `48px` |

### 11.2 Sidebar

Sidebar 是产品最主要的品牌视觉。

- 背景：`#11191D`。
- Logo 区高度：`44px`，内容为 Logo + OpenSLT。
- Navigation Item：`38px` 高，`6px` 圆角。
- 默认：透明。
- Hover：`#1D2B30`。
- Selected：`#20343A`。
- Selected 左侧增加 `2px`、`#00A88F` Signal Line。
- 选中项不得使用大面积绿色背景。
- 图标使用 `16px` 线性图标。

### 11.3 Topbar

Topbar 是工程工具栏，不承担复杂导航。

```text
左：Breadcrumb       中：Current Environment       右：Health / Notice / Help / Avatar
```

示例：

```text
运行中心 / RUN-20260903-0142      TEST ENV      ● HEALTHY   通知   帮助   用户
```

规则：

- 高度 `48px`，背景使用 Raised Surface。
- 主导航全部交给 Sidebar。
- Breadcrumb 必须突出当前对象，并允许返回上级。
- 环境名称保持短小、明确，避免与状态混用。
- System Health 使用状态点 + 文本，不使用大色块。
- 不使用大面积毛玻璃；仅在确有滚动层叠需求时使用极轻透明度。

## 12. 页面 Header

取消高占用的 `Kicker + Title + Description + Button` 纵向结构，改为紧凑双列：

```text
Page Title                         Primary Action
description                        Secondary Action
```

- 总高度控制在 `72–84px`。
- 页面标题 `26px`，描述 `14px`。
- Primary Action 位于右上，Secondary Action 与其同组。
- Kicker 仅用于确有必要的 Micro Label，不作为所有页面的固定模板。
- Header 后立即进入数据或工作区。

## 13. Dashboard

### 13.1 System Overview

不使用独立 KPI 卡片。所有指标组合成连续信息条：

```text
SYSTEM OVERVIEW
┌────────────────────────────────────────────┐
│ Active Runs │ Pass Rate │ Failed │ SLA     │
│     12      │   94.2%   │   3    │ 99%     │
└────────────────────────────────────────────┘
```

- 指标之间使用 1px Divider。
- Label：`11px`。
- Value：`26px`，Mono 或 tabular number。
- Trend：`11px`。
- 指标默认使用主文字色，只有具有数据意义时使用 Data Color 或 Signal Green。
- 不给每个指标独立阴影、圆角和背景色。

### 13.2 主区域

采用约 8 / 4 布局：

```text
┌──────────────────────────┬─────────────────┐
│ RECENT RUNS              │ SYSTEM HEALTH   │
│                          │ API       ● OK   │
│                          │ DB        ● OK   │
│                          │ Worker    ● OK   │
│                          ├─────────────────┤
│                          │ ATTENTION       │
└──────────────────────────┴─────────────────┘
```

- 左侧是 Recent Runs，保持列表连续性。
- 右侧是 System Monitor，由 System Health 与 Pending Actions 组成。
- 右栏不是普通小卡片堆叠，而是一个连续监控区域。
- 异常与待处理事项提高视觉权重，健康状态保持安静。

## 14. Table

表格是 OpenSLT 最重要的组件之一，必须降低传统“表格框”感。

### 14.1 基础结构

| 属性 | 规范 |
|---|---|
| Header Height | `36px` |
| Header Background | `#F4F6F7` |
| Header Font | `11px` |
| Row Height | `44–46px` |
| Separator | 仅水平 1px 分隔 |
| Vertical Border | 默认无 |
| Hover | `#F3F8F7` |
| Selected | `#EAF7F4` + 左侧 2px Signal Line |

### 14.2 数据呈现

- 运行编号统一为 `RUN-20260903-0192` 一类可扫描格式。
- Identifier 使用 Mono `11px`。
- 数值、时间、百分比使用 tabular numbers。
- 状态使用 Dot Label，不使用大色块 Badge。
- 行内主操作采用文本或 Ghost Button；Danger 默认只使用红色文字。
- 行选择与当前对象通过 Signal Line 表达，不将整行涂成主色。

## 15. Status

默认状态形式：

```text
● RUNNING
```

- Dot：`5px`。
- Label：`11px`。
- Dot 和文字使用对应 Data Color。
- 普通状态没有边框和背景。
- 只有需要提高辨识度的重要异常或结果才使用 `4px` 圆角 Soft Tag。
- 状态不能只依赖颜色，必须同时有文字或符号。

推荐符号：

| 状态 | 表现 |
|---|---|
| Completed | `● COMPLETED` |
| Running | `◉ RUNNING` |
| Waiting | `○ WAITING` |
| Failed | `× FAILED` |
| Paused | `Ⅱ PAUSED` |
| Unknown | `— UNKNOWN` |

## 16. Filter Toolbar

Filter 不再是浅色卡片，而是透明工具栏。

```text
Search   Status   Type   Owner                         Results 128   Reset
─────────────────────────────────────────────────────────────────────────
```

- 总高度 `40–44px`。
- 背景透明。
- Filter Control 高度 `32px`。
- Filter 与数据表之间通过水平分隔线建立关系。
- Results 与 Reset 靠右。
- 不使用完整圆角背景包住整条 Filter。
- 小屏下可换行或堆叠，但仍保持工具栏语义。

## 17. Button

### 17.1 Primary

```css
height: 34px;
border-radius: 6px;
background: #00A88F;
color: #FFFFFF;
```

- Hover：`#008C79`。
- Pressed：`#007565`。
- 用于操作域唯一主动作。

### 17.2 Default

```css
height: 34px;
border: 1px solid #D8E1E3;
background: #FFFFFF;
color: #182328;
```

用于取消、刷新、返回和普通辅助操作。

### 17.3 Ghost

- 背景透明。
- 用于 Topbar、Toolbar、Table Row 的低强度操作。
- Hover 可使用 Secondary Background。

### 17.4 Danger

- 默认使用红色文字或红色边框，不使用实心红色。
- 只有最终危险确认使用实心红色。
- 不可逆操作必须有明确对象和确认语义。

## 18. Input 与表单

### 18.1 Input

| 属性 | 默认 | Large |
|---|---:|---:|
| Height | `34px` | `40px` |
| Radius | `6px` | `6px` |
| Background | `#FFFFFF` | `#FFFFFF` |
| Border | `#D6DFE2` | `#D6DFE2` |

Focus：

```css
border-color: #00A88F;
box-shadow: 0 0 0 3px rgba(0, 168, 143, .12);
```

Placeholder：`#98A5AA`。

### 18.2 表单规则

- Label 使用 `13px`，Help 与 Error 使用 `11px`。
- 必填项使用标签后的红色 `*`，不依赖 Placeholder 表示字段含义。
- 错误信息紧邻字段，并说明恢复方式。
- 长表单放入 Drawer 或专用 Workspace；底部操作保持可见。
- 同一层级字段对齐，避免每个字段额外套卡片。
- 技术参数、命令和标识符使用 Mono。

## 19. Panel、Dialog 与 Drawer

### 19.1 Panel

- 默认为 Level 2 Surface。
- Header 与 Body 通过水平分隔线区分。
- 常规内边距 `16px`，复杂表单可用 `20px`。
- 不使用默认阴影。

### 19.2 Dialog

- Radius：`12px`。
- 用于聚焦确认、简短编辑和不可逆操作。
- Header / Body / Footer 明确分区。
- Primary Action 位于右下，取消在其左侧。

### 19.3 Drawer

- 用于长表单、创建流程和不离开当前数据上下文的编辑。
- 属于 Level 3 Floating，可使用标准 Floating Shadow。
- Footer 固定或粘性，Body 独立滚动。
- 不在 Drawer 内继续堆叠同级 Card。

## 20. Workflow Editor

Workflow 是 OpenSLT 最应建立视觉辨识度的页面。

### 20.1 Canvas

| 属性 | 规范 |
|---|---|
| Background | `#F5F7F8` |
| Grid | `20px` Dot Grid |
| Dot | `#D6DEE1` |
| Node Width | `320px` |

画布是工程线路图，不使用大面积彩色分区。

### 20.2 Node Anatomy

```text
┌─────────────────────────────┐
│ ● HTTP REQUEST        ⋮     │  Type Header
│─────────────────────────────│
│ GET                         │
│ /api/device/status          │
│                             │
│ Timeout            3000 ms  │
│ Retry                     2 │
│─────────────────────────────│
│ INPUT 3             OUTPUT 2│
└─────────────────────────────┘
```

- 节点顶部建立 Type Header。
- 不同 Node Type 只通过 `4px` Type Indicator 和线性 Icon 区分。
- 节点主体保持白色，不整块染色。
- 方法、路径、超时、重试、输入输出等工程数据使用 Mono。
- 更多操作放在右上角 Ghost Menu。

### 20.3 Node State

| 状态 | 表现 |
|---|---|
| Default | `border: #DCE4E6` |
| Hover | `border: #AEBFC4` |
| Selected | `border: #00A88F` |
| Selection Ring | `0 0 0 3px rgba(0,168,143,.12)` |
| Running | 左侧 Signal Pulse，Data Blue |
| Success | Success 图标 `✓` |
| Failure | Failed 图标 `!` 或 `×` |

动画只发生在状态指示器，整个 Node 不闪烁、不呼吸、不缩放。

### 20.4 Connection

| 状态 | 颜色 | 线宽 |
|---|---:|---:|
| Default | `#AAB8BC` | `1.5px` |
| Active Path | `#00A88F` | `1.5px` |
| Running | `#3378B7` | `1.5px` |
| Failed | `#D1495B` | `1.5px` |
| Selected | 对应状态色 | `2px` |

默认连接线不使用绿色。箭头、端点和连线共享状态语义。

## 21. Run Detail

Run Detail 是产品视觉样板，必须形成以下连续阅读：

```text
状态 → 时间线 → 当前节点 → 证据
```

### 21.1 Run Header

```text
RUN-20260903-00129          ◉ RUNNING          42%
```

- 运行编号使用 Mono Entity Title。
- 状态与进度在同一视觉层级，但不压过编号。
- 关键操作位于 Header 右侧。
- Trace ID、环境和创建时间作为次级元数据。

### 21.2 Pipeline Timeline

取消传统 Stepper，改为执行流水线：

```text
○━━━━●━━━━◉━━━━○━━━━○
PREPARE   CONNECT   TEST   VALIDATE   REPORT
          14.2s
```

| 状态 | 符号 |
|---|---|
| Completed | `●` |
| Running | `◉` |
| Waiting | `○` |
| Failed | `×` |

- 节点下显示阶段名和耗时。
- 活动路径与失败路径使用对应 Data Color。
- 当前阶段使用 Signal，而不是整个卡片闪烁。

### 21.3 Current Step 与 Live Log

```text
┌────────────────────────────┬──────────────────────────┐
│ CURRENT STEP               │ LIVE LOG                 │
│ HTTP Request               │ 14:31:02 request.start  │
│ 192.168.2.2                │ 14:31:02 socket.open    │
│ GET /status                │ 14:31:03 response.200   │
└────────────────────────────┴──────────────────────────┘
```

- 左侧解释当前节点与参数。
- 右侧提供实时证据。
- 两区共享相同时间线与状态语义，避免用户在 Tabs 中寻找当前证据。
- 低宽度下改为单列，先 Current Step，后 Live Log。

## 22. Log 与 Terminal

### 22.1 目标

日志首先服务于 Scanability：用户能够快速扫过时间、级别、来源、事件和消息，并沿 Trace ID 追溯。

### 22.2 规范

- Background：`#11181D`。
- Toolbar：`#182126`。
- Font：JetBrains Mono，`12px / 1.65`。
- Timestamp 使用低对比色 `#72848A`。
- Info、Success、Warning、Error 使用专用 Terminal Colors。
- 只高亮 Level、关键事件和异常片段，不让整行持续高亮。
- 长命令与 Trace ID 支持复制、换行或横向滚动。
- 实时日志新增内容不得造成页面大幅跳动；用户主动滚离底部后不强制抢回滚动位置。

## 23. Login Page

### 23.1 定位

登录页改为统一的 Brand Environment，取消传统“左插画 + 右登录 Card”。

### 23.2 背景与构成

```text
OPENSLT
AUTOMATED TESTING CONTROL PLATFORM

NODE ───── RUN ───── DEVICE ───── LOG                  SIGN IN
```

- 背景从 `#0D171C` 过渡到 `#13242A`。
- 左侧使用 NODE、RUN、DEVICE、LOG 等抽象数据线路表达系统环境。
- 不使用具象人物插画、3D 设备或大面积蓝色科技渐变。
- 右侧 Sign in 极简，表单可置于透明或轻微抬升表面。
- 品牌绿色只用于 Login Button、Focus 和 System Signal。
- 登录页与主应用共享 Graphite、Signal Green、Mono Data 和线性图标。

## 24. Signal Line：品牌微视觉语言

Signal Line 是 OpenSLT 2.0 的品牌 DNA。

### 24.1 形式

- 线宽：`2px`。
- 主色：`#00A88F`。
- 位置：通常位于元素左侧或路径上。
- 长度：与被标记对象的有效内容区域一致，不做无意义延伸。

### 24.2 使用场景

- Active Navigation；
- Current Workflow Node；
- Selected Table Row；
- Live Run；
- Active Device；
- Current Panel 或活动路径。

### 24.3 限制

- 同一局部区域最多一个主 Signal。
- Signal 必须表示“当前、活动、选中或正在传递”。
- 静态装饰线、普通 Divider 和输入边框不得伪装成 Signal。
- Running Signal 可脉冲，其他 Signal 保持静止。

## 25. Motion

| 类别 | 时长 | 用途 |
|---|---:|---|
| Button | `120ms` | Hover、Pressed |
| Hover | `120ms` | Row、Node、Icon |
| Panel | `160ms` | 展开、切换、轻位移 |
| Drawer | `180ms` | 进入与退出 |
| Workflow State | `200ms` | Signal、路径状态 |

推荐缓动：

```css
cubic-bezier(.2, 0, 0, 1)
```

禁止：

- Bounce；
- Spring；
- Scale Pop；
- Large Fade；
- Parallax；
- 整个 Node、Card 或页面持续闪烁。

Loading 可使用低幅度 Signal Pulse，并支持 reduced motion。

## 26. 响应式原则

2.0 保持现有响应式能力，但以紧凑工程工作区为核心。

| 断点 | 主要变化 |
|---:|---|
| `≤1200px` | 压缩页面水平留白与多栏比例 |
| `≤1024px` | Dashboard、Run Detail 等双栏改为单栏；属性面板变滑层 |
| `≤768px` | Sidebar 变覆盖式导航；页面边距降至 16px；Header 与 Toolbar 可换行 |
| `≤640px` | 多列表单、指标条和复杂元数据改为单列或横向滚动 |

规则：

- 优先重排，不把控件和文字缩小到不可读。
- 表格、拓扑、时间线和 Workflow Canvas 可横向滚动以保留结构。
- 移动端不能依赖 Hover 才能完成操作。
- Floating Body 独立滚动，Header 与 Footer 保持可见。
- 终端与全屏工作区使用 `dvh`，避免浏览器工具栏遮挡。

## 27. 可访问性底线

- 所有键盘可操作元素都有可见 `:focus-visible`。
- Focus 使用 `#00A88F` 与 `rgba(0,168,143,.12)`，不能只改变文字色。
- 页面保留“跳到主要内容”能力。
- 状态使用符号或文字补充颜色。
- 单独图标按钮具有 Tooltip 和 `aria-label`。
- 装饰线路、网格与 Signal 动画不进入可访问名称。
- 错误信息与字段就近关联，并说明恢复动作。
- 关键正文不使用 Tertiary 或 Disabled 色。
- 支持 `prefers-reduced-motion: reduce`，停止 Signal Pulse 和非必要过渡。
- 触控目标不得因 34px 视觉高度而失去可操作性；可通过透明点击区满足至少约 40px 的有效触控范围。

## 28. 文案与数据格式

- 页面标题直接使用任务对象，如“运行中心”“资源管理”“日志中心”。
- 描述只解释用户能做什么，不重复标题。
- 按钮使用“动词 + 对象”，如“创建运行”“新增资源”“提交结论”。
- 状态词保持稳定：RUNNING、PASSED、FAILED、WAITING、PAUSED、UNKNOWN。
- 用户界面正文可使用中文；机器状态码、事件名和协议术语保留原始英文。
- 运行编号统一使用可扫描格式，例如 `RUN-20260903-0192`。
- 时间根据任务选择 `HH:mm:ss.SSS` 或完整时间，统一使用 Mono。
- IP、端口、百分比、耗时、Trace ID 等保持原始精度。

## 29. CSS 令牌蓝图

以下变量是 2.0 实现时的最小语义层，不要求一次性引入额外 Token 工具链。

```css
:root {
  /* Canvas & surface */
  --ui-canvas: #f5f7f8;
  --ui-workspace: #f0f3f4;
  --ui-surface: #ffffff;
  --ui-border: #e1e7e9;
  --ui-border-control: #d6dfe2;
  --ui-border-strong: #aebfc4;

  /* Navigation */
  --ui-sidebar: #11191d;
  --ui-sidebar-secondary: #172227;
  --ui-sidebar-hover: #1d2b30;
  --ui-sidebar-selected: #20343a;

  /* Text */
  --ui-text-primary: #182328;
  --ui-text-secondary: #58686e;
  --ui-text-tertiary: #849298;
  --ui-text-disabled: #aeb8bc;
  --ui-placeholder: #98a5aa;

  /* Interaction */
  --ui-primary: #00a88f;
  --ui-primary-hover: #008c79;
  --ui-primary-pressed: #007565;
  --ui-primary-soft: #ddf5f0;
  --ui-focus-ring: 0 0 0 3px rgba(0, 168, 143, .12);

  /* Data */
  --ui-success: #188866;
  --ui-running: #3378b7;
  --ui-waiting: #b7791f;
  --ui-failed: #d1495b;
  --ui-paused: #73838a;
  --ui-unknown: #9ca8ad;

  /* Shape & motion */
  --ui-radius-control: 6px;
  --ui-radius-panel: 8px;
  --ui-radius-large-panel: 10px;
  --ui-radius-dialog: 12px;
  --ui-shadow-floating: 0 16px 40px rgba(21, 32, 37, .12);
  --ui-duration-fast: 120ms;
  --ui-duration-panel: 160ms;
  --ui-duration-drawer: 180ms;
  --ui-duration-state: 200ms;
  --ui-ease-instrument: cubic-bezier(.2, 0, 0, 1);
}
```

组件层变量只在同一组件出现多个稳定状态时增加，例如：

```css
:root {
  --table-row-hover: #f3f8f7;
  --table-row-selected: #eaf7f4;
  --workflow-connection: #aab8bc;
  --workflow-node-border: #dce4e6;
  --terminal-bg: #11181d;
  --terminal-toolbar: #182126;
}
```

## 30. 1.x → 2.0 迁移边界

| 领域 | 1.x 倾向 | 2.0 目标 |
|---|---|---|
| 品牌主色 | 深绿色占比较高 | 更明亮 Signal Green，仅用于操作与数据 |
| Sidebar | 深青绿、224/64px | Graphite、220/60px、2px Signal Line |
| Topbar | 52px、承担页域导航 | 48px 工具栏、Breadcrumb / Environment / Health |
| Page Header | Kicker + Title + Description | 72–84px 紧凑双列 |
| Dashboard | KPI 视觉权重偏高 | 连续 Overview Strip + System Monitor |
| Filter | 浅色 Filter Card | 透明 Filter Toolbar |
| Table | 表格容器感明显 | 无纵线、36px Header、选择 Signal |
| Status | 边框 Tag 较多 | Dot Label 为默认，Soft Tag 为例外 |
| Workflow Node | 摘要卡片式节点 | Type Header + 工程参数 + I/O Footer |
| Workflow Path | 默认线色不统一 | 中性默认线 + 状态路径色 |
| Run Flow | 横向步骤卡片 | Pipeline Timeline |
| Run Detail | Tabs 与信息块并列 | 状态 → Timeline → Current Step → Live Log |
| Login | 深蓝插画 + 白色表单 | Graphite Brand Environment + 抽象数据线路 |
| Typography | 存在较多近似字号 | 严格限制 8 个字号 |
| Motion | 统一约 160ms | 按 Button / Panel / Drawer / State 分级 |

迁移时优先改全局令牌和高频组件，再处理页面结构。不得同时长期维护两套同义视觉变量。

## 31. 设计与开发验收清单

### 31.1 全局语言

- [ ] 界面是否像工程控制台，而非 SaaS 后台模板？
- [ ] Graphite Navigation、Cold Neutral Workspace、White Surface 是否清晰成立？
- [ ] 绿色是否只表示操作、激活、当前或关键数据？
- [ ] 页面是否几乎无普通卡片阴影？
- [ ] 是否避免 Card inside Card？

### 31.2 信息与状态

- [ ] 用户能否快速识别当前状态、对象、阶段和下一步？
- [ ] 状态是否同时提供颜色与文字/符号？
- [ ] 运行、节点、日志、设备和时间是否形成可追溯证据链？
- [ ] 工程数据是否使用 Mono 或 tabular numbers？
- [ ] 是否避免无意义彩色卡片和 KPI 堆砌？

### 31.3 组件与密度

- [ ] Button、Input、Navigation、Toolbar、Table 是否符合 Compact Density？
- [ ] 圆角是否限制在 4/6/8/10/12px？
- [ ] Table 是否默认无纵向边框？
- [ ] Status 是否默认使用 Dot Label？
- [ ] Filter 是否表现为 Toolbar，而不是 Card？
- [ ] Signal Line 是否只标记当前、活动或选中状态？

### 31.4 专业页面

- [ ] Workflow Node 是否保持白色并通过 Type Indicator 区分类别？
- [ ] Workflow Connection 是否默认中性、按运行状态着色？
- [ ] Run Detail 是否遵循“状态 → 时间线 → 当前节点 → 证据”？
- [ ] Log / Terminal 是否优先支持快速扫描和追溯？
- [ ] Login 是否与主应用共享 Graphite 与 Signal Green，而非独立插画风？

### 31.5 交互与可访问性

- [ ] 是否只有一个最强 Primary Action？
- [ ] Danger 是否仅在最终危险确认时使用实心红色？
- [ ] 键盘焦点、错误、空、载入与禁用状态是否完整？
- [ ] 移动端是否不依赖 Hover？
- [ ] reduced motion 下是否停止非必要动画？
- [ ] 紧凑视觉高度是否仍保留足够触控范围？

---

**最终判断标准：** OpenSLT 2.0 应让用户在几秒内看清系统状态、当前对象、执行阶段和证据链；它安静、紧凑、精确，像真实测试实验室中的专业仪器。
