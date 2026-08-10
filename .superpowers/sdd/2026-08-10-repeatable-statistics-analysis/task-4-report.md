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
