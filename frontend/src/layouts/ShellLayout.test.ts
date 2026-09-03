import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'
import { useAuthStore, type User } from '@/stores/auth'
import ShellLayout from './ShellLayout.vue'

const EmptyView = { template: '<div />' }

const ElMenuStub = {
  props: ['defaultActive'],
  template: '<div class="el-menu-stub" :data-active="defaultActive"><slot /></div>',
}

const ElMenuItemStub = {
  props: ['index'],
  template: '<div class="el-menu-item-stub" :data-index="index"><slot /><slot name="title" /></div>',
}

const ElTooltipStub = {
  props: ['content'],
  template: '<span class="el-tooltip-stub" :data-content="content"><slot /></span>',
}

const ElButtonStub = {
  emits: ['click'],
  template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot /></button>',
}

const ElIconStub = { template: '<span class="el-icon-stub"><slot /></span>' }

async function mountLayout(path: string, role: User['role']) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 })
  localStorage.clear()

  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore(pinia)
  auth.user = {
    id: 1,
    username: role,
    display_name: role,
    role,
    is_active: true,
  }

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/dashboard', component: EmptyView, meta: { section: 'home' } },
      { path: '/runs', component: EmptyView, meta: { section: 'home' } },
      { path: '/runs/:id', component: EmptyView, meta: { section: 'home' } },
      { path: '/plans', component: EmptyView, meta: { section: 'management' } },
      { path: '/resources', component: EmptyView, meta: { section: 'management' } },
      { path: '/smart-cases', component: EmptyView, meta: { section: 'home' } },
      { path: '/smart-cases/settings', component: EmptyView, meta: { section: 'management' } },
      { path: '/logs', component: EmptyView, meta: { section: 'management' } },
      { path: '/users', component: EmptyView, meta: { section: 'management' } },
      { path: '/login', component: EmptyView },
    ],
  })
  await router.push(path)
  await router.isReady()

  const wrapper = mount(ShellLayout, {
    global: {
      plugins: [pinia, router],
      stubs: {
        ElMenu: ElMenuStub,
        ElMenuItem: ElMenuItemStub,
        ElTooltip: ElTooltipStub,
        ElButton: ElButtonStub,
        ElIcon: ElIconStub,
        VersionHistory: true,
      },
    },
  })

  return { wrapper, router }
}

describe('ShellLayout navigation', () => {
  it('shows all available work areas in the engineering sidebar', async () => {
    const { wrapper } = await mountLayout('/dashboard', 'admin')
    const navigation = wrapper.get('.el-menu-stub')
    const navigationText = navigation.text()

    expect(wrapper.get('.brand-copy small').text()).toBe('自动化测试平台')
    expect(navigationText).toContain('观察与控制')
    expect(navigationText).toContain('工作台')
    expect(navigationText).toContain('运行中心')
    expect(navigationText).toContain('智能用例')
    expect(navigationText).toContain('方案与场景')
    expect(navigationText).toContain('资源管理')
    expect(navigationText).toContain('日志中心')
    expect(navigationText).toContain('用户管理')
    expect(navigationText.indexOf('运行中心')).toBeLessThan(navigationText.indexOf('配置'))
    expect(wrapper.get('.breadcrumb').text()).toBe('工作台')
    expect(wrapper.get('.environment-label').text()).toContain('ENV')
    expect(wrapper.find('.management-center').exists()).toBe(false)
    expect(wrapper.get('.topbar-end').element.firstElementChild?.tagName).toBe('VERSION-HISTORY-STUB')
    expect(wrapper.get('.account-avatar').text()).toBe('A')
    wrapper.unmount()
  })

  it('shows management navigation according to the current role', async () => {
    const tester = await mountLayout('/plans', 'tester')
    const testerNavigation = tester.wrapper.get('.el-menu-stub')

    expect(testerNavigation.text()).toContain('方案与场景')
    expect(testerNavigation.text()).toContain('资源管理')
    expect(testerNavigation.text()).toContain('日志中心')
    expect(testerNavigation.text()).toContain('智能用例')
    expect(testerNavigation.text().indexOf('运行中心')).toBeLessThan(testerNavigation.text().indexOf('方案与场景'))
    expect(testerNavigation.text().indexOf('智能用例配置')).toBeLessThan(testerNavigation.text().indexOf('系统'))
    expect(testerNavigation.text()).not.toContain('用户管理')
    expect(testerNavigation.text()).toContain('工作台')
    expect(tester.wrapper.get('.breadcrumb').text()).toBe('方案与场景')
    tester.wrapper.unmount()

    const admin = await mountLayout('/users', 'admin')
    expect(admin.wrapper.get('.el-menu-stub').text()).toContain('用户管理')
    admin.wrapper.unmount()
  })

  it('lets visitors open the read-only plans area', async () => {
    const { wrapper } = await mountLayout('/dashboard', 'visitor')
    expect(wrapper.get('.el-menu-stub').text()).toContain('方案与场景')
    expect(wrapper.get('.el-menu-stub').text()).toContain('资源管理')
    expect(wrapper.get('.el-menu-stub').text()).not.toContain('日志中心')
    wrapper.unmount()

    const management = await mountLayout('/plans', 'visitor')
    const navigation = management.wrapper.get('.el-menu-stub')
    expect(navigation.text()).toContain('方案与场景')
    expect(navigation.text()).toContain('资源管理')
    expect(navigation.text()).not.toContain('日志中心')
    expect(navigation.text()).not.toContain('智能用例')
    management.wrapper.unmount()
  })

  it('uses the breadcrumb to return from a run to the run center', async () => {
    const { wrapper, router } = await mountLayout('/runs/42', 'tester')

    expect(wrapper.get('.breadcrumb').text()).toContain('运行中心')
    expect(wrapper.get('.breadcrumb').text()).toContain('RUN #42')
    await wrapper.get('.breadcrumb button').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/runs')
    wrapper.unmount()
  })
})
