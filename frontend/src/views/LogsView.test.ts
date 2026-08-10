import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/stores/auth'
import LogsView from './LogsView.vue'

const source = readFileSync(resolve(process.cwd(), 'src/views/LogsView.vue'), 'utf8')

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

describe('LogsView observability workspace', () => {
  it('uses paginated search for each structured log category', () => {
    expect(source).toContain("api.get<ApiLogSearchPage>('/logs/search'")
    expect(source).toContain("{ name: 'access', label: 'HTTP' }")
    expect(source).toContain("{ name: 'websocket', label: 'WebSocket' }")
    expect(source).toContain('<el-pagination')
    expect(source).toContain('min_duration_ms')
    expect(source).not.toContain("{ name: 'sql', label: 'SQL' }")
    expect(source).not.toContain('sql_fingerprint')
  })

  it('restricts payload details to administrators', () => {
    expect(source).toContain('if (!auth.isAdmin || !row.event_id) return')
    expect(source).toContain('api.get<ApiLogDetail>(`/logs/${row.event_id}`)')
    expect(source).toContain('v-if="auth.isAdmin"')
    expect(source).not.toContain('detail.payload.statement_template')
    expect(source).toContain('detail.payload.request')
    expect(source).toContain('detail.payload.response')
  })
})

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
