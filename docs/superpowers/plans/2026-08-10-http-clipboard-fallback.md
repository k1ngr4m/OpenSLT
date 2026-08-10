# HTTP Clipboard Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Windows `editcap` 命令、运行编号和 Trace ID 在内网 HTTP 与 HTTPS 环境中都能通过按钮复制，并在彻底失败时给出明确提示。

**Architecture:** 新增无业务依赖的 `copyText(value: string): Promise<void>` 工具。工具优先使用 Async Clipboard API，并在 API 缺失或拒绝时通过临时 `textarea` 和 `document.execCommand('copy')` 降级；三个调用方只负责业务提示。

**Tech Stack:** Vue 3、TypeScript、Vitest、Vue Test Utils、Pinia、Vue Router、浏览器 DOM/Clipboard API。

## Global Constraints

- 不修改 `editcap` 命令生成逻辑、后端接口或数据模型。
- 不修改 Nginx、证书和正式环境部署方式；HTTPS 仍是长期部署方向。
- 不引入第三方剪贴板依赖。
- 只改造系统剪贴板入口：Windows `editcap` 命令、运行编号和 Trace ID。
- Clipboard API 成功时不得调用弃用接口；仅在 API 缺失或拒绝时使用 `document.execCommand('copy')`。
- 复制内容必须逐字保持，包含双引号、空格、UNC 反斜杠和 `<>&` 时不得转换。
- 两条复制路径都失败时才显示失败提示，不记录待复制内容。
- 在 `RELEASES.json` 的 `unreleased` 中记录用户可见修复，不修改 `VERSION`。

---

### Task 1: 共享剪贴板兼容工具

**Files:**
- Create: `frontend/src/utils/clipboard.ts`
- Create: `frontend/src/utils/clipboard.test.ts`

**Interfaces:**
- Consumes: 浏览器 `navigator.clipboard.writeText(value)`、`document.execCommand('copy')`、DOM 焦点与 Selection API。
- Produces: `copyText(value: string): Promise<void>`；复制成功时 resolve，所有可用路径均失败时 reject。

- [ ] **Step 1: 写 Async Clipboard API 成功路径的失败测试**

创建 `frontend/src/utils/clipboard.test.ts`，保存并恢复浏览器属性，避免测试之间泄漏：

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyText } from '@/utils/clipboard'

const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
const execCommandDescriptor = Object.getOwnPropertyDescriptor(document, 'execCommand')

function setClipboard(value: Pick<Clipboard, 'writeText'> | undefined) {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value })
}

function setExecCommand(value: (commandId: string) => boolean) {
  Object.defineProperty(document, 'execCommand', { configurable: true, value })
}

afterEach(() => {
  if (clipboardDescriptor) Object.defineProperty(navigator, 'clipboard', clipboardDescriptor)
  else Reflect.deleteProperty(navigator, 'clipboard')
  if (execCommandDescriptor) Object.defineProperty(document, 'execCommand', execCommandDescriptor)
  else Reflect.deleteProperty(document, 'execCommand')
  document.body.replaceChildren()
})

describe('copyText', () => {
  it('uses the async Clipboard API without creating a fallback element', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    const execCommand = vi.fn(() => true)
    setClipboard({ writeText })
    setExecCommand(execCommand)

    await copyText('RUN-20260810-001')

    expect(writeText).toHaveBeenCalledWith('RUN-20260810-001')
    expect(execCommand).not.toHaveBeenCalled()
    expect(document.querySelector('[data-clipboard-fallback]')).toBeNull()
  })
})
```

- [ ] **Step 2: 运行测试，确认因工具不存在而失败**

Run:

```bash
npm --prefix frontend test -- src/utils/clipboard.test.ts
```

Expected: FAIL，Vitest 无法解析 `@/utils/clipboard` 或找不到 `copyText`。

- [ ] **Step 3: 实现只支持 Async Clipboard API 的最小版本**

创建 `frontend/src/utils/clipboard.ts`：

```ts
export async function copyText(value: string): Promise<void> {
  await navigator.clipboard.writeText(value)
}
```

- [ ] **Step 4: 运行测试，确认成功路径转绿**

Run:

```bash
npm --prefix frontend test -- src/utils/clipboard.test.ts
```

Expected: PASS，1 test passed。

- [ ] **Step 5: 写 HTTP 降级、原文保持和状态恢复的失败测试**

在同一个 `describe` 中追加：

```ts
it('falls back on HTTP and preserves text, focus, selection, and DOM state', async () => {
  const value = '"D:\\Program Files\\Wireshark\\editcap.exe" "\\\\server\\抓包\\in.pcap" <>&'
  const host = document.createElement('div')
  host.tabIndex = 0
  host.textContent = '原选区'
  document.body.appendChild(host)
  host.focus()
  const originalRange = document.createRange()
  originalRange.selectNodeContents(host)
  const selection = document.getSelection()!
  selection.removeAllRanges()
  selection.addRange(originalRange)
  let copiedValue = ''
  setClipboard(undefined)
  setExecCommand(commandId => {
    expect(commandId).toBe('copy')
    copiedValue = (document.activeElement as HTMLTextAreaElement).value
    return true
  })

  await copyText(value)

  expect(copiedValue).toBe(value)
  expect(document.querySelector('[data-clipboard-fallback]')).toBeNull()
  expect(document.activeElement).toBe(host)
  expect(document.getSelection()?.toString()).toBe('原选区')
})

it('falls back when the async Clipboard API rejects', async () => {
  setClipboard({ writeText: vi.fn().mockRejectedValue(new DOMException('Denied', 'NotAllowedError')) })
  const execCommand = vi.fn(() => true)
  setExecCommand(execCommand)

  await copyText('trace-123')

  expect(execCommand).toHaveBeenCalledWith('copy')
})

it.each([
  ['returns false', () => false],
  ['throws', () => { throw new Error('blocked') }],
])('rejects and cleans up when the fallback %s', async (_, implementation) => {
  setClipboard(undefined)
  setExecCommand(implementation)

  await expect(copyText('cannot-copy')).rejects.toThrow('Clipboard copy failed')

  expect(document.querySelector('[data-clipboard-fallback]')).toBeNull()
})
```

- [ ] **Step 6: 运行测试，确认缺失和拒绝场景失败**

Run:

```bash
npm --prefix frontend test -- src/utils/clipboard.test.ts
```

Expected: FAIL；当前实现会在 `navigator.clipboard` 缺失或 `writeText` 拒绝时直接 reject，且没有临时 `textarea`、现场恢复和统一失败处理。

- [ ] **Step 7: 实现兼容降级与现场状态恢复**

将 `frontend/src/utils/clipboard.ts` 替换为：

```ts
function legacyCopyText(value: string): void {
  const activeElement = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null
  const selection = document.getSelection()
  const ranges: Range[] = []
  if (selection) {
    for (let index = 0; index < selection.rangeCount; index += 1) {
      ranges.push(selection.getRangeAt(index).cloneRange())
    }
  }

  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.readOnly = true
  textarea.dataset.clipboardFallback = ''
  textarea.setAttribute('aria-hidden', 'true')
  Object.assign(textarea.style, {
    position: 'fixed',
    inset: '0 auto auto -9999px',
    opacity: '0',
  })
  document.body.appendChild(textarea)

  let copied = false
  try {
    textarea.focus({ preventScroll: true })
    textarea.select()
    copied = typeof document.execCommand === 'function' && document.execCommand('copy')
  } catch {
    copied = false
  } finally {
    textarea.remove()
    activeElement?.focus({ preventScroll: true })
    if (selection) {
      selection.removeAllRanges()
      for (const range of ranges) selection.addRange(range)
    }
  }

  if (!copied) throw new Error('Clipboard copy failed')
}

export async function copyText(value: string): Promise<void> {
  const clipboard = navigator.clipboard
  if (clipboard && typeof clipboard.writeText === 'function') {
    try {
      await clipboard.writeText(value)
      return
    } catch {
      // Continue with the HTTP-compatible fallback.
    }
  }
  legacyCopyText(value)
}
```

- [ ] **Step 8: 运行测试，确认降级路径转绿**

Run:

```bash
npm --prefix frontend test -- src/utils/clipboard.test.ts
```

Expected: PASS，5 tests passed。

- [ ] **Step 9: 提交共享工具**

```bash
git add frontend/src/utils/clipboard.ts frontend/src/utils/clipboard.test.ts
git commit -m "fix: add HTTP clipboard fallback"
```

---

### Task 2: Windows editcap 命令接入共享工具

**Files:**
- Modify: `frontend/src/components/run-detail/WindowsEditcapCommand.vue:1-14`
- Modify: `frontend/src/components/run-detail/WindowsEditcapCommand.test.ts:1-46`

**Interfaces:**
- Consumes: Task 1 的 `copyText(value: string): Promise<void>`。
- Produces: Windows `editcap` 复制按钮在共享工具成功时显示成功提示，在共享工具 reject 时显示现有手动复制提示。

- [ ] **Step 1: 将组件测试改为覆盖共享工具成功和最终失败**

在 `WindowsEditcapCommand.test.ts` 中从 Vue Test Utils 导入 `flushPromises`，并把 hoisted mock 扩展为：

```ts
const { copyTextMock, message } = vi.hoisted(() => ({
  copyTextMock: vi.fn(),
  message: { error: vi.fn(), success: vi.fn() },
}))
vi.mock('@/utils/clipboard', () => ({ copyText: copyTextMock }))
vi.mock('@/ui/elementPlusServices', () => ({ ElMessage: message }))
```

`beforeEach` 同时执行 `copyTextMock.mockReset()`。把现有成功测试中的 `navigator.clipboard` 注入删除，设置 `copyTextMock.mockResolvedValue(undefined)`，并将复制断言改为：

```ts
expect(copyTextMock).toHaveBeenCalledWith(command)
expect(message.success).toHaveBeenCalledWith('Windows editcap 命令已复制')
expect(message.error).not.toHaveBeenCalled()
```

追加失败测试：

```ts
it('asks the operator to select the command when every copy path fails', async () => {
  copyTextMock.mockRejectedValue(new Error('Clipboard copy failed'))
  const wrapper = mount(WindowsEditcapCommand, {
    props: { command: 'editcap command' },
    global: {
      components: {
        ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
      },
    },
  })

  await wrapper.get('button').trigger('click')
  await flushPromises()

  expect(message.success).not.toHaveBeenCalled()
  expect(message.error).toHaveBeenCalledWith('复制失败，请手动选择命令')
})
```

- [ ] **Step 2: 运行组件测试，确认因组件未调用共享工具而失败**

Run:

```bash
npm --prefix frontend test -- src/components/run-detail/WindowsEditcapCommand.test.ts
```

Expected: FAIL；`copyTextMock` 未被调用，现有组件仍直接访问 `navigator.clipboard`。

- [ ] **Step 3: 修改组件使用共享工具**

在图标导入后增加：

```ts
import { copyText } from '@/utils/clipboard'
```

将 `copyCommand()` 改为：

```ts
async function copyCommand() {
  try {
    await copyText(props.command)
    ElMessage.success('Windows editcap 命令已复制')
  } catch {
    ElMessage.error('复制失败，请手动选择命令')
  }
}
```

- [ ] **Step 4: 运行工具和组件测试**

Run:

```bash
npm --prefix frontend test -- src/utils/clipboard.test.ts src/components/run-detail/WindowsEditcapCommand.test.ts
```

Expected: PASS，工具和组件测试全部通过。

- [ ] **Step 5: 提交 editcap 接入**

```bash
git add frontend/src/components/run-detail/WindowsEditcapCommand.vue frontend/src/components/run-detail/WindowsEditcapCommand.test.ts
git commit -m "fix: copy editcap commands over HTTP"
```

---

### Task 3: 运行编号与 Trace ID 接入共享工具

**Files:**
- Create: `frontend/src/views/RunsView.test.ts`
- Modify: `frontend/src/views/RunsView.vue:1-12,125-128`
- Modify: `frontend/src/views/LogsView.test.ts`
- Modify: `frontend/src/views/LogsView.vue:1-9,102-106`

**Interfaces:**
- Consumes: Task 1 的 `copyText(value: string): Promise<void>`。
- Produces: 运行编号和 Trace ID 的复制成功、失败用户反馈；两处不再直接访问 `navigator.clipboard`。

- [ ] **Step 1: 写运行编号复制入口的失败测试**

创建 `frontend/src/views/RunsView.test.ts`：

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/stores/auth'
import RunsView from './RunsView.vue'

const run = {
  id: 7,
  run_number: 'RUN-20260810-001',
  business_code: 'fut_mm',
  status: 'completed',
  progress: 100,
  created_at: '2026-08-10T10:00:00+08:00',
  config_snapshot: {},
}

const { apiGet, copyTextMock, messageSuccess, messageError } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  copyTextMock: vi.fn(),
  messageSuccess: vi.fn(),
  messageError: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  api: { get: apiGet, post: vi.fn(), delete: vi.fn() },
  errorMessage: () => '请求失败',
}))
vi.mock('@/utils/clipboard', () => ({ copyText: copyTextMock }))
vi.mock('@/ui/elementPlusServices', () => ({
  ElMessage: { success: messageSuccess, error: messageError, warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))

const ElButtonStub = {
  emits: ['click'],
  template: '<button v-bind="$attrs" @click="$emit(\'click\', $event)"><slot /></button>',
}
const ElTableStub = { template: '<div><slot /></div>' }
const ElTableColumnStub = {
  data: () => ({ row: run }),
  template: '<div><slot :row="row" /></div>',
}

async function mountRuns() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore(pinia).user = {
    id: 1, username: 'tester', display_name: '测试员', role: 'tester', is_active: true,
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/runs', component: RunsView }],
  })
  await router.push('/runs')
  await router.isReady()
  const wrapper = mount(RunsView, {
    global: {
      plugins: [pinia, router],
      directives: { loading: () => {} },
      stubs: {
        ElAlert: true,
        ElButton: ElButtonStub,
        ElDrawer: true,
        ElForm: true,
        ElFormItem: true,
        ElIcon: true,
        ElInput: true,
        ElOption: true,
        ElProgress: true,
        ElSelect: true,
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElTooltip: true,
        StatusBadge: true,
      },
    },
  })
  await flushPromises()
  return wrapper
}

describe('RunsView clipboard action', () => {
  beforeEach(() => {
    apiGet.mockImplementation((path: string) => Promise.resolve({ data: path === '/runs' ? [run] : [] }))
    copyTextMock.mockReset()
    messageSuccess.mockReset()
    messageError.mockReset()
  })

  it('copies a run number and reports success', async () => {
    copyTextMock.mockResolvedValue(undefined)
    const wrapper = await mountRuns()

    await wrapper.get('[aria-label="复制运行编号"]').trigger('click')
    await flushPromises()

    expect(copyTextMock).toHaveBeenCalledWith('RUN-20260810-001')
    expect(messageSuccess).toHaveBeenCalledWith('已复制运行编号 RUN-20260810-001')
    expect(messageError).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('asks for manual copying when the run number cannot be copied', async () => {
    copyTextMock.mockRejectedValue(new Error('Clipboard copy failed'))
    const wrapper = await mountRuns()

    await wrapper.get('[aria-label="复制运行编号"]').trigger('click')
    await flushPromises()

    expect(messageSuccess).not.toHaveBeenCalled()
    expect(messageError).toHaveBeenCalledWith('复制运行编号失败，请手动复制')
    wrapper.unmount()
  })
})
```

- [ ] **Step 2: 写 Trace ID 复制入口的失败测试**

扩展 `frontend/src/views/LogsView.test.ts`。保留现有源码结构测试，并将导入区调整为：

```ts
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/stores/auth'
import LogsView from './LogsView.vue'
```

新增 hoisted mocks：

```ts
const { apiGet, copyTextMock, messageSuccess, messageError } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  copyTextMock: vi.fn(),
  messageSuccess: vi.fn(),
  messageError: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  api: { get: apiGet },
  errorMessage: () => '请求失败',
}))
vi.mock('@/utils/clipboard', () => ({ copyText: copyTextMock }))
vi.mock('@/ui/elementPlusServices', () => ({
  ElMessage: { success: messageSuccess, error: messageError },
}))
```

使用完整的日志摘要夹具：

```ts
const logRow = {
  id: 11,
  created_at: '2026-08-10T10:00:00+08:00',
  database_scope: null,
  duration_ms: 12,
  event: 'http.request',
  event_id: 'event-11',
  http_method: 'GET',
  http_status: 200,
  level: 'INFO',
  log_type: 'http',
  message: 'GET /health -> 200',
  result: 'success',
  run_id: null,
  source: 'api',
  sql_fingerprint: null,
  step_id: null,
  trace_id: 'trace-copy-123',
  user_id: 1,
}
```

增加以下真实组件挂载辅助代码：

```ts
const LogsElButtonStub = {
  emits: ['click'],
  template: '<button v-bind="$attrs" @click="$emit(\'click\', $event)"><slot /></button>',
}
const LogsElTableStub = { template: '<div><slot /></div>' }
const LogsElTableColumnStub = {
  data: () => ({ row: logRow }),
  template: '<div><slot :row="row" /></div>',
}

async function mountLogs() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore(pinia).user = {
    id: 1, username: 'tester', display_name: '测试员', role: 'tester', is_active: true,
  }
  apiGet.mockResolvedValue({
    data: { items: [logRow], total: 1, page: 1, page_size: 50 },
  })
  const wrapper = mount(LogsView, {
    global: {
      plugins: [pinia],
      directives: { loading: () => {} },
      stubs: {
        ElAlert: true,
        ElButton: LogsElButtonStub,
        ElDatePicker: true,
        ElDrawer: true,
        ElIcon: true,
        ElInput: true,
        ElInputNumber: true,
        ElOption: true,
        ElPagination: true,
        ElSelect: true,
        ElTabPane: true,
        ElTable: LogsElTableStub,
        ElTableColumn: LogsElTableColumnStub,
        ElTabs: true,
        ElTag: true,
      },
    },
  })
  await flushPromises()
  return wrapper
}
```

追加完整的调用方测试块：

```ts
describe('LogsView clipboard action', () => {
  beforeEach(() => {
    apiGet.mockReset()
    copyTextMock.mockReset()
    messageSuccess.mockReset()
    messageError.mockReset()
  })

  it('copies a Trace ID and reports success', async () => {
    copyTextMock.mockResolvedValue(undefined)
    const wrapper = await mountLogs()

    await wrapper.get('[aria-label="复制 Trace ID"]').trigger('click')
    await flushPromises()

    expect(copyTextMock).toHaveBeenCalledWith('trace-copy-123')
    expect(messageSuccess).toHaveBeenCalledWith('已复制')
    expect(messageError).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('asks for manual copying when the Trace ID cannot be copied', async () => {
    copyTextMock.mockRejectedValue(new Error('Clipboard copy failed'))
    const wrapper = await mountLogs()

    await wrapper.get('[aria-label="复制 Trace ID"]').trigger('click')
    await flushPromises()

    expect(messageSuccess).not.toHaveBeenCalled()
    expect(messageError).toHaveBeenCalledWith('复制 Trace ID 失败，请手动复制')
    wrapper.unmount()
  })
})
```

- [ ] **Step 3: 运行两个视图测试，确认当前直接调用 Clipboard API 而失败**

Run:

```bash
npm --prefix frontend test -- src/views/RunsView.test.ts src/views/LogsView.test.ts
```

Expected: FAIL；`copyTextMock` 未被调用，失败提示不存在。

- [ ] **Step 4: 修改 RunsView 使用共享工具并处理失败**

在 `RunsView.vue` 的工具导入区增加：

```ts
import { copyText } from '@/utils/clipboard'
```

替换函数：

```ts
async function copyRunNumber(value: string) {
  try {
    await copyText(value)
    ElMessage.success(`已复制运行编号 ${value}`)
  } catch {
    ElMessage.error('复制运行编号失败，请手动复制')
  }
}
```

- [ ] **Step 5: 修改 LogsView 使用共享工具并处理失败**

在 `LogsView.vue` 的工具导入区增加：

```ts
import { copyText } from '@/utils/clipboard'
```

替换函数：

```ts
async function copy(value?: string | null) {
  if (!value) return
  try {
    await copyText(value)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制 Trace ID 失败，请手动复制')
  }
}
```

- [ ] **Step 6: 运行三个调用方和共享工具测试**

Run:

```bash
npm --prefix frontend test -- src/utils/clipboard.test.ts src/components/run-detail/WindowsEditcapCommand.test.ts src/views/RunsView.test.ts src/views/LogsView.test.ts
```

Expected: PASS，三个入口均覆盖成功与最终失败，工具覆盖 HTTP 降级。

- [ ] **Step 7: 提交视图接入**

```bash
git add frontend/src/views/RunsView.vue frontend/src/views/RunsView.test.ts frontend/src/views/LogsView.vue frontend/src/views/LogsView.test.ts
git commit -m "fix: copy run identifiers over HTTP"
```

---

### Task 4: 发布说明与完整验证

**Files:**
- Modify: `RELEASES.json:2`

**Interfaces:**
- Consumes: Tasks 1–3 的已通过测试实现。
- Produces: 有效的未发布变更记录和可发布的前端生产构建。

- [ ] **Step 1: 添加用户可见发布说明**

将 `unreleased` 数组开头调整为：

```json
"unreleased": [
  {
    "type": "fixed",
    "text": "修复 Python 3.8 环境下交互式解析节点完成时误报 CSV 下载失败的问题。"
  },
  {
    "type": "fixed",
    "text": "修复内网 HTTP 环境下 Windows editcap 命令、运行编号和 Trace ID 无法通过按钮复制的问题。"
  }
]
```

- [ ] **Step 2: 验证发布元数据**

Run:

```bash
python tools/release_metadata.py
```

Expected: `Release metadata is valid for OpenSLT 0.2.2`。

- [ ] **Step 3: 运行完整前端测试**

Run:

```bash
npm --prefix frontend test
```

Expected: 所有 test files 和 tests 通过，无 unhandled rejection。

- [ ] **Step 4: 运行前端生产构建**

Run:

```bash
npm --prefix frontend run build
```

Expected: `vue-tsc -b` 与 `vite build` 均成功，`frontend/dist/index.html` 生成。

- [ ] **Step 5: 检查变更范围与格式**

Run:

```bash
git diff --check
git status --short
git diff --stat HEAD~3
```

Expected: 无空白错误；只包含共享剪贴板工具、三个调用入口、对应测试和 `RELEASES.json`。

- [ ] **Step 6: 提交发布说明**

```bash
git add RELEASES.json
git commit -m "docs: record HTTP clipboard fix"
```

完成后再次运行 `python tools/release_metadata.py`，并记录最终测试、构建和 Git 状态作为交付证据。
