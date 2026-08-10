# 数据统计节点重复分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 数据统计节点在完成前可原子修改 CSV 输入与异常大值上限、执行任意多次分析并保留每次成功或失败的历史，同时指标和报告只使用最新成功结果。

**Architecture:** 运行步骤的 `config_snapshot` 保存当前异常大值上限，`result_summary` 保存当前输入选择、配置修订、最新成功结果和轻量历史索引。每次执行预留单调递增分析序号并生成不可变 `statistics-analysis-vNNN.json`；现有指标与报告读取顶层最新成功结果，历史详情按需读取并校验产物。

**Tech Stack:** FastAPI、Pydantic、SQLAlchemy、异步持久任务、Vue 3 Composition API、Element Plus、Vitest、pytest。

## Global Constraints

- 仅允许当前数据统计节点在待开始、待重试或成功待完成阶段修改分析配置。
- 仅允许成功待完成的数据统计节点再次分析；成功复算不增加 `retry_count`。
- 配置实际变化才增加 `statistics_config_revision`；当前配置没有对应成功分析时，服务端禁止完成节点。
- 每次新的逻辑执行都占用分析序号：首次执行、用户再次分析、工作流失败后的用户重试均使用新 `analysis_no`；同一个 durable task 因 lease 接管或恢复而重放时复用已预留编号，不重复占号。历史不设业务上限。
- 最新成功分析继续写入兼容字段和指标表；失败分析不得删除或替换上一次成功结果。
- 已完成节点不可再配置或分析；无新历史结构的旧运行继续按原行为展示和完成。
- 用户可见变更写入 `RELEASES.json` 的 `unreleased`，不修改 `VERSION`。

---

### Task 1: 后端运行时配置与重复分析状态机

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/api/routes/runs.py`
- Modify: `backend/app/services/orchestration.py`
- Modify: `backend/app/services/run_state.py`
- Modify: `backend/app/services/statistics_execution.py`
- Test: `backend/tests/test_statistics_workflow.py`
- Test: `backend/tests/test_run_state.py`

**Interfaces:**
- Produces: `PUT /runs/{run_id}/steps/{step_id}/statistics-config` with `{relative_paths: string[], max_latency_ns: int}`.
- Produces: `POST /runs/{run_id}/steps/{step_id}/analyze` returning `RunOut` and scheduling the existing durable workflow-step task with an analysis-specific idempotency key.
- Produces: shared helpers that update configuration revisions, reserve analysis numbers, and validate completion freshness.

- [ ] **Step 1: Write failing backend tests**
  - Assert atomic CSV/threshold save, revision increments only on actual changes, allowed and rejected states, and legacy input endpoint compatibility.
  - Assert `waiting -> running -> waiting` repeated analysis, monotonic analysis numbers, unchanged retry count, concurrent/duplicate rejection, and completion blocked for stale configuration.
- [ ] **Step 2: Run focused tests and confirm expected failures**
  - Run `.venv/bin/python -m pytest backend/tests/test_statistics_workflow.py backend/tests/test_run_state.py -q`.
- [ ] **Step 3: Implement schemas, route guards, revisioning, analysis reservation, transitions, audit/log events, and completion gate**
  - Snapshot selected input metadata, threshold, script, revision and timestamps in the reserved history item.
  - Make standard start/retry reserve an analysis item for statistics nodes; make `/analyze` use the same reservation without incrementing retries.
- [ ] **Step 4: Run focused tests until green and self-review**
- [ ] **Step 5: Commit task changes**

### Task 2: 不可变分析历史、最新指标与报告

**Files:**
- Modify: `backend/app/services/statistics_execution.py`
- Modify: `backend/app/services/workflow_handlers/statistics.py`
- Modify: `backend/app/api/routes/runs.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/services/reports.py`
- Test: `backend/tests/test_statistics_workflow.py`
- Test: `backend/tests/test_reports.py`

**Interfaces:**
- Consumes: Task 1 reserved history item and active analysis number/revision.
- Produces: `GET /runs/{run_id}/steps/{step_id}/statistics-analyses` metadata list, newest first.
- Produces: `GET /runs/{run_id}/steps/{step_id}/statistics-analyses/{analysis_no}` integrity-checked full detail.
- Produces: immutable artifact `statistics-analysis-vNNN.json` for every success or failure.

- [ ] **Step 1: Write failing history and reporting tests**
  - Assert success and failure artifacts, partial multifile failure detail, checksum validation, newest-first metadata, detail authorization and missing/corrupt artifact errors.
  - Assert failed reanalysis preserves previous metrics/latest fields and successful reanalysis atomically replaces them.
  - Assert reports include the latest successful upper limit and exclude historical results.
- [ ] **Step 2: Run focused tests and confirm expected failures**
- [ ] **Step 3: Implement idempotent versioned artifact finalization and history APIs**
  - Update the reserved history item instead of appending duplicates during task recovery.
  - Keep full results out of the lightweight history index; store artifact ID, status, inputs, upper limit, revision, timestamps, duration and error.
- [ ] **Step 4: Keep top-level compatibility fields tied to the latest success and update report rendering**
- [ ] **Step 5: Run focused tests until green and commit**

### Task 3: 前端统计配置与分析历史数据层

**Files:**
- Modify: `frontend/src/composables/useStatisticsInputs.ts`
- Modify: `frontend/src/composables/useStatisticsInputs.test.ts`
- Modify: `frontend/src/composables/useRunActions.ts`
- Modify: `frontend/src/composables/useRunActions.test.ts`

**Interfaces:**
- Consumes: Task 1 configuration/analyze endpoints and Task 2 history endpoints.
- Produces: threshold draft, dirty/saved/readiness state, save action, reanalyze action, completion freshness, history metadata and lazy detail cache.

- [ ] **Step 1: Write failing composable tests**
  - Assert initialization from saved runtime state, positive-integer validation, atomic request body, editable states, unsaved/stale completion blocking and successful reanalysis action.
  - Assert newest-first history loading, lazy detail fetch, failed records and legacy fallback.
- [ ] **Step 2: Run focused Vitest files and confirm expected failures**
- [ ] **Step 3: Implement minimal composable and action behavior**
- [ ] **Step 4: Run focused tests until green, refactor and commit**

### Task 4: 运行详情配置与历史界面

**Files:**
- Modify: `frontend/src/views/RunDetailView.vue`
- Modify: `frontend/src/views/RunDetailView.test.ts`
- Modify: `frontend/src/styles/run-detail.css`

**Interfaces:**
- Consumes: Task 3 composable state/actions.
- Produces: unified analysis configuration panel, start/reanalyze/complete controls, stale-config explanation and accessible collapsible history.

- [ ] **Step 1: Write failing component tests for rendered controls and disabled-state explanations**
- [ ] **Step 2: Run the focused component test and confirm failure**
- [ ] **Step 3: Implement Element Plus configuration form and analysis history**
  - Keep existing visual tokens, responsive layout and semantic labels; default-expand latest success and lazy-load older details on expansion.
- [ ] **Step 4: Run focused tests, frontend full tests and production build**
- [ ] **Step 5: Commit UI changes**

### Task 5: 契约、文档、发布说明与全量验证

**Files:**
- Modify: `frontend/openapi.json`
- Modify: `frontend/src/types/api.generated.ts`
- Modify: `docs/output/openslt-main-workflow-user-guide.md`
- Modify: `RELEASES.json`

**Interfaces:**
- Consumes: all implemented HTTP schemas and user-facing behavior.
- Produces: generated public contracts and operator documentation.

- [ ] **Step 1: Regenerate OpenAPI and frontend types with repository scripts**
- [ ] **Step 2: Update the statistics workflow guide and add one concise `added` unreleased entry**
- [ ] **Step 3: Run `python tools/release_metadata.py` using the project Python environment**
- [ ] **Step 4: Run all backend tests, all frontend tests and frontend production build**
- [ ] **Step 5: Inspect final diff for generated drift or unrelated changes and commit**
