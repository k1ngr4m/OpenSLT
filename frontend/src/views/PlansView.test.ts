import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/stores/auth'
import PlansView from './PlansView.vue'

const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }))

vi.mock('@/api/client', () => ({
  api: { get: apiGet, post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  errorMessage: () => '请求失败',
}))

const source = readFileSync(resolve(process.cwd(), 'src/views/PlansView.vue'), 'utf8')
const workflowSource = readFileSync(resolve(process.cwd(), 'src/views/WorkflowEditorView.vue'), 'utf8')

describe('PlansView scenario form', () => {
  it('does not expose scenario type, config version, or enable controls', () => {
    const scenarioDialog = source.match(/<el-dialog v-model="scenarioDialog"[\s\S]*?<\/el-dialog>/)?.[0]
    expect(scenarioDialog).toBeTruthy()
    expect(scenarioDialog).not.toContain('label="场景类型"')
    expect(scenarioDialog).not.toContain('label="配置版本"')
    expect(scenarioDialog).not.toContain('label="启用"')
  })

  it('submits only system-owned defaults for hidden scenario fields', () => {
    const saveScenario = source.match(/async function saveScenario\(\)[\s\S]*?\n}\n\nasync function copyPlan/)?.[0]
    expect(saveScenario).toContain("scenario_type: scenario.scenario_type || 'order'")
    expect(saveScenario).toContain("config_version: scenario.config_version || '1.0'")
    expect(saveScenario).not.toContain('...scenario')
    expect(saveScenario).not.toContain('is_enabled:')
  })

  it('loads and filters plans by the selected directory', () => {
    expect(source).toContain("api.get('/plan-directories')")
    expect(source).toContain('item.directory_id === selectedDirectoryId.value')
    expect(source).toContain("query: { directory_id: String(directoryId) }")
  })

  it('creates scenarios from a concrete plan and preserves the directory in workflow links', () => {
    expect(source).toContain('openScenario(undefined, p.id)')
    expect(source).toContain(':disabled="!scenarioEdit"')
    expect(source).toContain("path: `/plans/scenarios/${scenarioId}/workflow`")
    expect(source).toContain("query: { directory_id: String(selectedDirectoryId.value) }")
    expect(workflowSource).toContain("query: plansReturnQuery")
  })

  it('binds new plans to the selected directory and supports moving edited plans', () => {
    expect(source).toContain('directory_id: selectedDirectoryId.value')
    expect(source).toContain('v-if="planEdit" label="所属目录"')
    expect(source).toContain('directory_id: plan.directory_id')
  })
})

describe('PlansView scenario list', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiGet.mockImplementation((path: string) => Promise.resolve({
      data: path === '/plan-directories'
        ? [{ id: 1, name: '默认目录', is_default: true }]
        : path === '/plans'
          ? [{ id: 10, directory_id: 1, name: '基础方案', business_code: 'fut_mm', config_version: '1.0', description: '' }]
          : path === '/scenarios'
            ? [
                { id: 101, plan_id: 10, name: '已启用场景', created_at: '2026-08-11T01:02:03Z', updated_at: '2026-08-11T04:05:06Z', is_enabled: true, published_workflow_version_id: 11 },
                { id: 102, plan_id: 10, name: '已暂停场景', created_at: '2026-08-10T01:02:03Z', updated_at: '2026-08-10T04:05:06Z', is_enabled: false, published_workflow_version_id: 12 },
                { id: 103, plan_id: 10, name: '未启用场景', created_at: '2026-08-09T01:02:03Z', updated_at: '2026-08-09T04:05:06Z', is_enabled: false, published_workflow_version_id: null },
              ]
            : [],
    }))
  })

  it('renders the required columns, Beijing times, workflow states, and operator actions', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useAuthStore(pinia).user = {
      id: 1, username: 'tester', display_name: '测试员', role: 'tester', is_active: true,
    }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/plans', component: PlansView },
        { path: '/plans/scenarios/:id/workflow', component: { template: '<div />' } },
      ],
    })
    await router.push('/plans')
    await router.isReady()

    const wrapper = mount(PlansView, { global: { plugins: [pinia, router, ElementPlus] } })
    await flushPromises()

    const headers = wrapper.findAll('.el-table__header-wrapper th .cell').map(cell => cell.text())
    expect(headers).toEqual(['场景名称', '创建时间', '更新时间', '工作流状态', '操作'])
    expect(wrapper.text()).not.toContain('场景资源')
    expect(wrapper.text()).toContain('2026-08-11 09:02:03')
    expect(wrapper.text()).toContain('2026-08-11 12:05:06')
    expect(wrapper.text()).toContain('已启用')
    expect(wrapper.text()).toContain('已暂停')
    expect(wrapper.text()).toContain('未启用')
    expect(wrapper.text()).toContain('工作流')
    expect(wrapper.text()).toContain('基础信息')
    expect(wrapper.text()).toContain('复制')
    expect(wrapper.get('[aria-label="删除场景"]')).toBeTruthy()
    wrapper.unmount()
  })
})
