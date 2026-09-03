import { createRouter, createWebHistory, type RouteRecordRaw, type RouterHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import ShellLayout from '@/layouts/ShellLayout.vue'

export const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true },
  },
  {
    path: '/plans/scenarios/:id/workflow',
    component: () => import('@/views/WorkflowEditorView.vue'),
    meta: { section: 'management', operator: true },
  },
  {
    path: '/',
    component: ShellLayout,
    children: [
      { path: '', redirect: '/dashboard' },
      { path: 'dashboard', component: () => import('@/views/DashboardView.vue'), meta: { section: 'home' } },
      { path: 'runs', component: () => import('@/views/RunsView.vue'), meta: { section: 'home' } },
      { path: 'runs/:id', component: () => import('@/views/RunDetailView.vue'), meta: { section: 'home' } },
      {
        path: 'plans',
        component: () => import('@/views/PlansView.vue'),
        meta: { section: 'management' },
      },
      {
        path: 'resources',
        component: () => import('@/views/ResourcesView.vue'),
        meta: { section: 'management' },
      },
      {
        path: 'resources/:id/database',
        component: () => import('@/views/DatabaseConsoleView.vue'),
        meta: { section: 'management', operator: true },
      },
      {
        path: 'resources/:id/terminal',
        component: () => import('@/views/TerminalView.vue'),
        meta: { section: 'management', operator: true },
      },
      {
        path: 'smart-cases',
        component: () => import('@/views/SmartCasesView.vue'),
        meta: { section: 'management', operator: true },
      },
      {
        path: 'logs',
        component: () => import('@/views/LogsView.vue'),
        meta: { section: 'management', operator: true },
      },
      {
        path: 'users',
        component: () => import('@/views/UsersView.vue'),
        meta: { section: 'management', admin: true },
      },
      { path: 'forbidden', component: () => import('@/views/ForbiddenView.vue') },
      { path: ':pathMatch(.*)*', component: () => import('@/views/NotFoundView.vue') },
    ],
  },
]

export function createAppRouter(history: RouterHistory = createWebHistory()) {
  const router = createRouter({ history, routes })

  router.beforeEach(async to => {
    const auth = useAuthStore()
    if (!to.meta.public && !auth.loggedIn) return '/login'
    if (auth.loggedIn && !auth.user) {
      try {
        await auth.loadUser()
      } catch {
        return '/login'
      }
    }
    if ((to.meta.admin && !auth.isAdmin) || (to.meta.operator && !auth.canOperate)) return '/forbidden'
    if (to.path === '/login' && auth.loggedIn) return '/dashboard'
  })

  return router
}

export default createAppRouter()
