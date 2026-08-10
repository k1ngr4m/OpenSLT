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
        ElTag: true,
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
