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
      { path: '/plans', component: EmptyView, meta: { section: 'management' } },
      { path: '/resources', component: EmptyView, meta: { section: 'management' } },
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
  it('shows the workspace before the speed-test navigation group in home mode', async () => {
    const { wrapper } = await mountLayout('/dashboard', 'admin')
    const navigation = wrapper.get('.el-menu-stub')
    const navigationText = navigation.text()

    expect(wrapper.get('.brand-copy small').text()).toBe('自动化测试平台')
    expect(navigationText).toContain('工作台')
    expect(navigationText).toContain('测速')
    expect(navigationText).toContain('测速运行')
    expect(navigationText.indexOf('工作台')).toBeLessThan(navigationText.indexOf('测速'))
    expect(navigationText.indexOf('测速')).toBeLessThan(navigationText.indexOf('测速运行'))
    expect(navigationText).not.toContain('任务')
    expect(navigationText).not.toContain('方案与场景')
    expect(wrapper.get('.section-nav-item').classes()).toContain('is-active')
    expect(wrapper.find('.management-center').exists()).toBe(true)
    expect(wrapper.get('.management-center').classes()).not.toContain('is-active')
    expect(wrapper.find('.beijing-time').exists()).toBe(false)
    expect(wrapper.get('.topbar-end').element.lastElementChild?.tagName).toBe('VERSION-HISTORY-STUB')
    wrapper.unmount()
  })

  it('shows management navigation according to the current role', async () => {
    const tester = await mountLayout('/plans', 'tester')
    const testerNavigation = tester.wrapper.get('.el-menu-stub')

    expect(testerNavigation.text()).toContain('方案与场景')
    expect(testerNavigation.text()).toContain('资源管理')
    expect(testerNavigation.text()).toContain('日志中心')
    expect(testerNavigation.text()).not.toContain('用户管理')
    expect(testerNavigation.text()).not.toContain('工作台')
    expect(tester.wrapper.get('.management-center').classes()).toContain('is-active')
    tester.wrapper.unmount()

    const admin = await mountLayout('/users', 'admin')
    expect(admin.wrapper.get('.el-menu-stub').text()).toContain('用户管理')
    admin.wrapper.unmount()
  })

  it('lets visitors open the read-only plans area', async () => {
    const { wrapper } = await mountLayout('/dashboard', 'visitor')
    expect(wrapper.find('.management-center').exists()).toBe(true)
    wrapper.unmount()

    const management = await mountLayout('/plans', 'visitor')
    const navigation = management.wrapper.get('.el-menu-stub')
    expect(navigation.text()).toContain('方案与场景')
    expect(navigation.text()).toContain('资源管理')
    expect(navigation.text()).not.toContain('日志中心')
    management.wrapper.unmount()
  })

  it('navigates between home and management with only the home section link', async () => {
    const { wrapper, router } = await mountLayout('/dashboard', 'tester')

    await wrapper.get('.management-center').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/plans')

    await wrapper.get('.section-nav-item:not(.is-disabled)').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/dashboard')

    const sectionLinks = wrapper.findAll('.section-nav-item')
    expect(sectionLinks).toHaveLength(1)
    expect(sectionLinks[0].text()).toContain('首页')
    expect(wrapper.find('.section-nav-item.is-disabled').exists()).toBe(false)
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
