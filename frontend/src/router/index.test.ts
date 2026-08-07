import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, describe, expect, it } from 'vitest'
import { useAuthStore, type User } from '@/stores/auth'
import { createAppRouter } from './index'

async function navigateAs(role: User['role'], path: string) {
  localStorage.setItem('access_token', 'test-token')
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

  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  return router.currentRoute.value.path
}

afterEach(() => {
  localStorage.clear()
})

describe('management route permissions', () => {
  it.each([
    '/plans/scenarios/1/workflow',
    '/resources/1/database',
    '/resources/1/terminal',
    '/logs',
  ])('blocks visitors from %s', async path => {
    expect(await navigateAs('visitor', path)).toBe('/forbidden')
  })

  it('allows visitors to view the resource list', async () => {
    expect(await navigateAs('visitor', '/resources')).toBe('/resources')
  })

  it('allows visitors to view plans and scenarios', async () => {
    expect(await navigateAs('visitor', '/plans')).toBe('/plans')
  })

  it('allows testers to enter the management center', async () => {
    expect(await navigateAs('tester', '/plans')).toBe('/plans')
  })

  it('keeps user management admin-only', async () => {
    expect(await navigateAs('tester', '/users')).toBe('/forbidden')
    expect(await navigateAs('admin', '/users')).toBe('/users')
  })
})
