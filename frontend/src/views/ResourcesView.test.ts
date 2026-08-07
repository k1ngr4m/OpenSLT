import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore, type User } from '@/stores/auth'
import ResourcesView from './ResourcesView.vue'

const row = {
  id: 7,
  name: 'REM 复制源',
  resource_type: 'rem',
  business_code: 'fut_mm',
  host: 'rem.example.test',
  ssh_port: 22,
  username: 'tester',
  health_status: 'unknown',
  is_enabled: true,
}

const { apiGet, apiPost, messageSuccess, messageError } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  messageSuccess: vi.fn(),
  messageError: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  api: { get: apiGet, post: apiPost },
  errorMessage: (error: unknown) => error instanceof Error ? error.message : '请求失败',
}))

vi.mock('@/ui/elementPlusServices', () => ({
  ElMessage: { success: messageSuccess, error: messageError, warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))

const ElButtonStub = {
  emits: ['click'],
  template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot /></button>',
}

const ElTableStub = { template: '<div class="table-stub"><slot /></div>' }
const ElTableColumnStub = {
  template: '<div class="column-stub"><slot :row="row" /></div>',
  data: () => ({ row }),
}

async function mountResources(role: User['role']) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore(pinia)
  auth.user = { id: 1, username: role, display_name: role, role, is_active: true }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/resources', component: ResourcesView },
      { path: '/resources/:id/database', component: { template: '<div />' } },
      { path: '/resources/:id/terminal', component: { template: '<div />' } },
    ],
  })
  await router.push('/resources')
  await router.isReady()
  return mount(ResourcesView, {
    global: {
      plugins: [pinia, router],
      directives: { loading: () => {} },
      stubs: {
        ElButton: ElButtonStub,
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElAlert: true,
        ElCheckbox: true,
        ElCheckboxGroup: true,
        ElCol: true,
        ElDivider: true,
        ElDrawer: true,
        ElForm: true,
        ElFormItem: true,
        ElInput: true,
        ElInputNumber: true,
        ElOption: true,
        ElRadioButton: true,
        ElRadioGroup: true,
        ElRow: true,
        ElSelect: true,
        ElStep: true,
        ElSteps: true,
        ElSwitch: true,
        ElTag: true,
      },
    },
  })
}

describe('ResourcesView resource copy', () => {
  beforeEach(() => {
    apiGet.mockResolvedValue({ data: [row] })
    apiPost.mockResolvedValue({ data: { ...row, id: 8, name: 'REM 复制源 - 副本' } })
  })

  it.each<User['role']>(['visitor', 'tester', 'admin'])('lets %s copy a listed resource and refreshes the list', async role => {
    const wrapper = await mountResources(role)
    await flushPromises()

    if (role === 'visitor') {
      const buttonLabels = wrapper.findAll('button').map(button => button.text())
      expect(buttonLabels).not.toContain('连通测试')
      expect(buttonLabels).not.toContain('操作台')
      expect(buttonLabels).not.toContain('编辑')
      expect(buttonLabels).not.toContain('删除')
    }
    const copyButton = wrapper.findAll('button').find(button => button.text() === '复制')
    expect(copyButton).toBeDefined()
    await copyButton!.trigger('click')
    await flushPromises()

    expect(apiPost).toHaveBeenCalledWith('/resources/7/copy')
    expect(messageSuccess).toHaveBeenCalledWith('资源已复制')
    expect(apiGet).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('shows the request error when copying fails', async () => {
    apiPost.mockRejectedValueOnce(new Error('复制失败'))
    const wrapper = await mountResources('visitor')
    await flushPromises()

    const copyButton = wrapper.findAll('button').find(button => button.text() === '复制')
    await copyButton!.trigger('click')
    await flushPromises()

    expect(messageError).toHaveBeenCalledWith('复制失败')
    wrapper.unmount()
  })
})
