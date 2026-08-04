# Remove Run Config Snapshot Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the visible “运行配置快照” section from the run detail page while retaining all snapshot data and dependent functionality.

**Architecture:** Keep the backend contract and frontend run model unchanged. Protect the UI decision with the existing source-level `RunDetailView` test pattern, then remove only the dedicated template section and its unused CSS selectors.

**Tech Stack:** Vue 3, TypeScript, Vitest, CSS, JSON release metadata.

## Global Constraints

- Preserve `config_snapshot` data and every other feature that consumes it.
- Do not modify backend APIs, models, or run creation behavior.
- Record the user-visible change in `RELEASES.json` under `unreleased`.

---

### Task 1: Remove Snapshot Display

**Files:**
- Modify: `frontend/src/views/RunDetailView.test.ts`
- Modify: `frontend/src/views/RunDetailView.vue:720`
- Modify: `frontend/src/styles/run-detail.css:433`
- Modify: `RELEASES.json:2`

**Interfaces:**
- Consumes: Existing `RunDetailView.vue` template and `run-detail.css` selectors.
- Produces: A run detail page without the “运行配置快照” section; no API or type changes.

- [x] **Step 1: Write the failing test**

Add this test to `RunDetailView.test.ts`:

```typescript
it('does not show the run configuration snapshot summary', () => {
  expect(source).not.toContain('<h3>运行配置快照</h3>')
  expect(source).not.toContain('class="detail-section compact-snapshot"')
})
```

- [x] **Step 2: Run test to verify it fails**

Run: `npm test -- src/views/RunDetailView.test.ts`

Expected: FAIL because the current template contains `<h3>运行配置快照</h3>` and `compact-snapshot`.

- [x] **Step 3: Remove the dedicated UI and styles**

Delete the `<section class="detail-section compact-snapshot">` block from `RunDetailView.vue`. Delete only the `.compact-snapshot` rules from `run-detail.css`, including its responsive selector; retain shared `.info-list` rules because node configuration still uses them.

- [x] **Step 4: Add the release note**

Prepend this object to `RELEASES.json` → `unreleased`:

```json
{
  "type": "changed",
  "text": "运行详情不再展示运行配置快照摘要。"
}
```

- [x] **Step 5: Verify the targeted test passes**

Run: `npm test -- src/views/RunDetailView.test.ts`

Expected: PASS.

- [x] **Step 6: Validate metadata and frontend**

Run: `python tools/release_metadata.py`

Expected: Exit code 0.

Run: `npm test`

Expected: All frontend tests pass.

Run: `npm run build`

Expected: Type checking and Vite production build pass.
