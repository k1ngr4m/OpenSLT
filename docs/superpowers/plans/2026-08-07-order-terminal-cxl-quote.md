# 发单 SSH 交互与撤销报价动作 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让发单节点 SSH 终端可交互，并完整支持 `cxl_quote` 发单动作。

**Architecture:** 沿用现有终端输入通道和动作白名单。后端把 `cxl_quote` 纳入标准动作与请求模型，前端资源配置、运行按钮和高风险确认复用已有动作机制；运行详情只针对 order 终端移除只读属性。

**Tech Stack:** FastAPI/Pydantic、pytest、Vue 3、TypeScript、Vitest、Vite。

## Global Constraints

- 保留工作区中已有未提交改动，不覆盖无关文件内容。
- `VERSION` 不变；用户可见变更记录在 `RELEASES.json` 的 `unreleased` 数组。
- 遵循测试先行：先写失败测试，再写最小实现。

---

### Task 1: 后端动作契约

**Files:**
- Modify: `backend/app/workflow_node_configs.py`
- Modify: `backend/tests/test_order_sessions.py`
- Modify: `tools/ees_ef_vi_trader_binary_api_test/ees_ef_vi_trader_binary_api_test.py`
- Modify: `tools/ees_ef_vi_trader_binary_api_test/tests/test_simulator.py`
- Modify: `tools/ees_ef_vi_trader_binary_api_test/README.md`

- [ ] 写测试：验证支持 `cxl_quote` 的资源可发送该动作，未支持时仍返回 `ORDER_ACTION_UNSUPPORTED`。
- [ ] 运行目标测试确认先失败。
- [ ] 将 `cxl_quote` 加入 `ORDER_ACTIONS` 和 `OrderAction` Literal。
- [ ] 将 `cxl_quote` 和 `cxlquote` 加入 EF-VI 仿真桩及其文档。
- [ ] 运行后端动作测试确认通过。

### Task 2: 前端资源配置与运行按钮

**Files:**
- Modify: `frontend/src/views/ResourcesView.vue`
- Modify: `frontend/src/composables/useOrderActions.ts`
- Modify: `frontend/src/composables/useOrderActions.test.ts`
- Modify: `frontend/openapi.json`
- Modify: `frontend/src/types/api.generated.ts`

- [ ] 写测试：验证 `cxl_quote` 被列为高风险动作并参与可发送动作判断。
- [ ] 运行目标测试确认先失败。
- [ ] 将 `cxl_quote` 加入资源页动作选项和高风险集合。
- [ ] 重新生成 API 类型，使动作联合类型包含 `cxl_quote`。
- [ ] 运行前端相关测试确认通过。

### Task 3: 发单 SSH 可交互与发布说明

**Files:**
- Modify: `frontend/src/views/RunDetailView.vue`
- Modify: `RELEASES.json`

- [ ] 写测试：验证发单终端不再传入 `read-only`，其他终端行为不变。
- [ ] 运行目标测试确认先失败。
- [ ] 移除发单终端的 `read-only` 属性。
- [ ] 在 `RELEASES.json` 的 `unreleased` 中记录用户可见变化。
- [ ] 运行前端测试和生产构建。
