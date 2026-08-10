# Task 4：运行详情配置与分析历史界面

## 实现

- 统计节点的 CSV 选择和最大延迟上限已合并为语义化“分析配置”表单，统一通过 `saveStatisticsConfig` 保存；上限限制为正整数，并明确显示未保存状态。
- 首次待执行时主操作显示“开始分析”；统计节点处于等待完成状态时提供“开始分析”或“再次分析”。完成按钮接入 `statisticsCompletionBlocked`，未保存草稿或当前配置没有成功分析时会禁用，并以可访问的状态说明原因。
- 选中统计节点会显式请求历史元数据；历史按照接口的倒序显示。最新成功记录默认展开并加载详情，失败记录展示错误代码和可读说明。
- 历史详情使用 Element Plus 父级 `el-collapse` 的 `change` 事件触发。历史采用单项 `accordion`，确保一次只加载一条详情，从 UI 层规避 Task 3 已登记的不同 `analysis_no` 并发详情加载缓存覆盖风险。
- 旧运行在没有历史时仍保留下方既有 `statistics_results` 的回退展示。配置、历史、状态消息均使用现有视觉 token，并补充移动端布局。

## TDD 记录

- RED：新增运行详情视图行为测试后执行 `npm test -- src/views/RunDetailView.test.ts`，得到 `3 failed, 4 passed`；缺失统一配置、复算/完成门禁和历史 UI 接入。
- GREEN：实现后同一命令得到 `7 passed`。
- 回归 RED/GREEN：核对 Element Plus 后发现 `el-collapse-item` 不发出 `change` 事件；先将测试改为要求父级 `el-collapse` 的 `@change="handleStatisticsHistoryChange"`，得到 `1 failed, 6 passed`，再改为单项 accordion 的父级事件处理，恢复 `7 passed`。

## 验证

- `npm test -- src/views/RunDetailView.test.ts`：`7 passed`。
- `npm test`：`30 passed files, 140 passed tests`。
- `npm run build`：成功；仅有既有第三方 `@vueuse/core` Rollup `/* #__PURE__ */` 注释提示。
- `git diff --check`：成功。

## 修改文件

- `frontend/src/views/RunDetailView.vue`
- `frontend/src/views/RunDetailView.test.ts`
- `frontend/src/styles/run-detail.css`

## 顾虑

- 未处理 Task 3 的两个非阻塞 Minor；其中不同详情并发加载的问题通过本界面的单项 accordion 避免成为用户可见问题。

## Fix round 1/5：审查结论

Task 4 首轮审查未通过，需在追加提交中处理以下 Important：

- 历史加载只监听 `selectedStep.id`，默认展开初始化也只按步骤 ID；同一步复算后不会刷新历史或默认展开新的最新成功分析。
- 新运行的顶层 `statistics_results` 与历史详情重复展示；兼容回退必须严格限定为缺少 `statistics_analyses` 历史结构的旧运行。
- 新增测试仅检查源码字符串，未通过真实挂载和交互覆盖配置草稿、完成门禁、保存/复算、历史刷新与懒加载、旧运行兼容行为。
- CSV 选择区存在 `label` 嵌套 checkbox 自带 label 的无效语义；数值输入也需显式标签关联。
- 失败详情缺少 `artifact.error.message`，且冻结节点仍提示可修改/复算。
- 复算请求期间未同步禁用完成与冲突操作，存在 `analyze` 与 `complete` 并发风险。

### 修复

- 历史 watcher 改为监听步骤 ID、最新分析号/状态和最新成功分析号组成的轻量签名；默认展开状态改用 `(stepId, latestSuccessAnalysisNo)`，同一步出现新成功分析时会刷新历史、展开并加载其详情。
- 兼容结果区直接按 `result_summary` 是否拥有 `statistics_analyses` 属性分流；新运行不会重复展示顶层 `statistics_results`，旧运行仍保留回退。
- 使用 `@vue/test-utils` 真实挂载视图，以响应式 composable mock 和 Element Plus 交互 stub 覆盖配置门禁、点击、复算并发、历史刷新/展开/懒加载、新旧运行分流、失败详情和标签语义。
- 生产模板继续使用 Element Plus `el-collapse` 的单项 `accordion`，键盘操作语义由该组件负责；挂载测试只验证 `accordion` 属性与父级 `change` 事件到懒加载动作的接线，不把自定义 stub 视为键盘行为覆盖。
- CSV 组改为内层 `fieldset/legend` 并添加 `aria-labelledby`/`aria-describedby`；最大延迟输入通过显式 `label[for]` 与 ID 关联。
- 失败记录展示不可变产物中的 `artifact.error.message`；冻结节点使用只读/审计提示，并隐藏复算入口。
- 复算请求期间统一禁用完成、复算、CSV 刷新/选择、阈值编辑与配置保存，并显示“统计分析正在执行”的完成门禁原因。

### TDD 与验证

- RED：`npm test -- src/views/RunDetailView.test.ts` 得到 `5 failed, 10 passed`；失败分别对应复算并发门禁、同步骤历史签名、现代运行重复兼容结果、失败详情/冻结提示和无效标签结构。
- GREEN：同一聚焦命令得到 `15 passed`。
- 全量前端测试：`30 passed files, 148 passed tests`。
- 生产构建：`npm run build` 成功；仅保留既有第三方 `@vueuse/core` Rollup 注释提示。
- `git diff --check`：成功。
