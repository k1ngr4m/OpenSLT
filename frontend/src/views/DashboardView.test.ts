import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import DashboardView from './DashboardView.vue'
const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/api/client', () => ({ api: { get }, errorMessage: () => '连接失败' }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ canOperate: true }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }), RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } }))
const global = { stubs: { ElButton: { template: '<button><slot /></button>' }, ElIcon: { template: '<span><slot /></span>' }, ElSkeleton: true, ElAlert: { props: ['title'], template: '<div>{{title}}</div>' }, StatusBadge: true } }
const run = (id: number, status: string) => ({ id, status, run_number: `R-${id}`, progress: 80, created_at: '2026-08-10T01:16:23Z', config_snapshot: { plan: { name: '软核做市' }, scenario: { name: `场景${id}` } } })

describe('Dashboard task list', () => {
  it('prioritizes attention, switches to recent runs and distinguishes unknown resource health', async () => {
    get.mockImplementation(async (path: string) => ({ data: path === '/runs' ? [run(1, 'completed'), run(2, 'awaiting_review')] : [{ id: 1, name: '未检测资源', is_enabled: true, health_status: null }, { id: 2, name: '停用资源', is_enabled: false, health_status: 'healthy' }] }))
    const wrapper = mount(DashboardView, { global })
    await flushPromises()
    expect(wrapper.findAll('.task-row')).toHaveLength(1)
    expect(wrapper.get('.task-row').attributes('href')).toBe('/runs/2')
    expect(wrapper.get('.resource-total').text()).toContain('/ 1 健康')
    expect(wrapper.get('.resource-list').text()).toContain('未知')
    await wrapper.findAll('.task-filters button')[1]!.trigger('click')
    expect(wrapper.findAll('.task-row')).toHaveLength(2)
    wrapper.unmount()
  })
  it('shows one empty state without claiming absent resources are healthy', async () => {
    get.mockResolvedValue({ data: [] })
    const wrapper = mount(DashboardView, { global })
    await flushPromises()
    expect(wrapper.findAll('.task-empty')).toHaveLength(1)
    expect(wrapper.text()).toContain('暂无启用资源')
    expect(wrapper.text()).not.toContain('已启用资源均正常')
    wrapper.unmount()
  })
})
