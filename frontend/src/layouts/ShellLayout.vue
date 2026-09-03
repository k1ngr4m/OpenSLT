<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import VersionHistory from '@/components/VersionHistory.vue'
import {
  DataAnalysis,
  Monitor,
  SetUp,
  Document,
  Files,
  User,
  SwitchButton,
  Fold,
  Expand,
  MagicStick,
} from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const isCompact = ref(false)
const isMobile = ref(false)
const mobileNavOpen = ref(false)
const manualCollapsed = ref(localStorage.getItem('openslt-nav-collapsed') === '1')

const roleText: Record<string, string> = {
  admin: '系统管理员',
  tester: '测试人员',
  visitor: '访客',
}

const collapsed = computed(() => !isMobile.value && (isCompact.value || manualCollapsed.value))
const sidebarClass = computed(() => ({
  'is-collapsed': collapsed.value,
  'is-mobile-open': mobileNavOpen.value,
}))
const activePath = computed(() => {
  if (route.path.startsWith('/smart-cases/settings')) return '/smart-cases/settings'
  const first = `/${route.path.split('/').filter(Boolean)[0] || 'dashboard'}`
  return ['/dashboard', '/runs', '/plans', '/resources', '/smart-cases', '/logs', '/users'].includes(first) ? first : '/dashboard'
})
const navToggleLabel = computed(() => {
  if (isMobile.value) return '打开导航'
  return collapsed.value ? '展开导航' : '收起导航'
})
const environmentLabel = import.meta.env.MODE === 'production' ? 'PROD ENV' : 'DEV ENV'
const breadcrumb = computed(() => {
  const path = route.path
  if (path.startsWith('/runs/')) return { root: '运行中心', rootPath: '/runs', current: `RUN #${route.params.id}` }
  if (path.endsWith('/database')) return { root: '资源管理', rootPath: '/resources', current: `DATABASE #${route.params.id}` }
  if (path.endsWith('/terminal')) return { root: '资源管理', rootPath: '/resources', current: `TERMINAL #${route.params.id}` }
  if (path.startsWith('/smart-cases/settings')) return { root: '智能用例配置', rootPath: '/smart-cases/settings', current: '' }
  if (path.startsWith('/smart-cases')) return { root: '智能用例', rootPath: '/smart-cases', current: '' }
  if (path.startsWith('/runs')) return { root: '运行中心', rootPath: '/runs', current: '' }
  if (path.startsWith('/plans')) return { root: '方案与场景', rootPath: '/plans', current: '' }
  if (path.startsWith('/resources')) return { root: '资源管理', rootPath: '/resources', current: '' }
  if (path.startsWith('/logs')) return { root: '日志中心', rootPath: '/logs', current: '' }
  if (path.startsWith('/users')) return { root: '用户管理', rootPath: '/users', current: '' }
  return { root: '工作台', rootPath: '/dashboard', current: '' }
})
const accountInitial = computed(() => (auth.user?.display_name || auth.user?.username || 'U').trim().slice(0, 1).toUpperCase())

function syncViewport() {
  isMobile.value = window.innerWidth < 768
  isCompact.value = window.innerWidth >= 768 && window.innerWidth < 1200
  if (!isMobile.value) mobileNavOpen.value = false
}

function toggleNavigation() {
  if (isMobile.value) {
    mobileNavOpen.value = !mobileNavOpen.value
    return
  }
  if (isCompact.value) return
  manualCollapsed.value = !manualCollapsed.value
  localStorage.setItem('openslt-nav-collapsed', manualCollapsed.value ? '1' : '0')
}

function closeMobileNavigation() {
  if (isMobile.value) mobileNavOpen.value = false
}

async function logout() {
  await auth.logout()
  router.push('/login')
}

onMounted(() => {
  syncViewport()
  window.addEventListener('resize', syncViewport)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncViewport)
})
</script>

<template>
  <a class="skip-link" href="#main-content">跳到主要内容</a>
  <div class="shell">
    <aside class="sidebar" :class="sidebarClass">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">
          <img src="/assets/global.logo.jpg" alt="" />
        </div>
        <div v-show="!collapsed || isMobile" class="brand-copy">
          <strong>OpenSLT</strong>
          <small>自动化测试平台</small>
        </div>
      </div>

      <nav class="sidebar-nav" aria-label="主导航">
        <el-menu
          router
          :collapse="collapsed"
          :default-active="activePath"
          class="nav"
          @select="closeMobileNavigation"
        >
          <div v-if="!collapsed" class="nav-label">观察与控制</div>
          <el-menu-item index="/dashboard">
            <el-icon><DataAnalysis /></el-icon>
            <template #title>工作台</template>
          </el-menu-item>
          <el-menu-item index="/runs">
            <el-icon><Monitor /></el-icon>
            <template #title>运行中心</template>
          </el-menu-item>
          <el-menu-item v-if="auth.canOperate" index="/smart-cases">
            <el-icon><MagicStick /></el-icon>
            <template #title>智能用例</template>
          </el-menu-item>

          <div v-if="!collapsed" class="nav-label">配置</div>
          <el-menu-item index="/plans">
            <el-icon><Document /></el-icon>
            <template #title>方案与场景</template>
          </el-menu-item>
          <el-menu-item index="/resources">
            <el-icon><SetUp /></el-icon>
            <template #title>资源管理</template>
          </el-menu-item>
          <el-menu-item v-if="auth.canOperate" index="/smart-cases/settings">
            <el-icon><MagicStick /></el-icon>
            <template #title>智能用例配置</template>
          </el-menu-item>

          <div v-if="auth.canOperate && !collapsed" class="nav-label">系统</div>
          <el-menu-item v-if="auth.canOperate" index="/logs">
            <el-icon><Files /></el-icon>
            <template #title>日志中心</template>
          </el-menu-item>
          <el-menu-item v-if="auth.isAdmin" index="/users">
            <el-icon><User /></el-icon>
            <template #title>用户管理</template>
          </el-menu-item>
        </el-menu>
      </nav>

      <div class="sidebar-foot">
        <div v-show="!collapsed || isMobile" class="account-copy">
          <strong>{{ auth.user?.display_name || auth.user?.username }}</strong>
          <small>{{ roleText[auth.user?.role || ''] || auth.user?.role }}</small>
        </div>
        <el-tooltip content="退出登录" placement="top">
          <el-button text circle aria-label="退出登录" @click="logout">
            <el-icon><SwitchButton /></el-icon>
          </el-button>
        </el-tooltip>
      </div>
    </aside>

    <button
      v-if="isMobile && mobileNavOpen"
      class="nav-scrim"
      type="button"
      aria-label="关闭导航"
      @click="mobileNavOpen = false"
    />

    <section class="workspace">
      <header class="topbar">
        <div class="topbar-start">
          <el-tooltip :content="navToggleLabel" placement="bottom">
            <el-button text circle class="nav-toggle" :aria-label="navToggleLabel" @click="toggleNavigation">
              <el-icon><Expand v-if="collapsed || isMobile" /><Fold v-else /></el-icon>
            </el-button>
          </el-tooltip>
          <nav class="breadcrumb" aria-label="面包屑">
            <button type="button" @click="router.push(breadcrumb.rootPath)">{{ breadcrumb.root }}</button>
            <template v-if="breadcrumb.current"><span aria-hidden="true">/</span><strong>{{ breadcrumb.current }}</strong></template>
          </nav>
        </div>

        <div class="environment-label"><span aria-hidden="true" />{{ environmentLabel }}</div>

        <div class="topbar-end">
          <VersionHistory />
          <div class="topbar-account" :title="roleText[auth.user?.role || ''] || auth.user?.role">
            <span class="account-avatar" aria-hidden="true">{{ accountInitial }}</span>
            <span class="topbar-account-name">{{ auth.user?.display_name || auth.user?.username }}</span>
          </div>
        </div>
      </header>
      <main id="main-content" class="main" tabindex="-1">
        <router-view />
      </main>
    </section>
  </div>
</template>

<style scoped>
.shell{display:flex;min-height:100dvh;background:var(--ui-canvas)}
.sidebar{position:sticky;z-index:30;top:0;display:flex;flex:0 0 220px;flex-direction:column;width:220px;height:100dvh;color:#d7e2e5;background:var(--ui-sidebar);transition:width var(--ui-transition),flex-basis var(--ui-transition),transform var(--ui-transition-drawer)}
.sidebar.is-collapsed{flex-basis:60px;width:60px}
.brand{display:flex;flex:0 0 44px;align-items:center;gap:9px;padding:0 12px;border-bottom:1px solid rgba(215,226,229,.08);background:var(--ui-sidebar-secondary)}
.brand-mark{display:grid;flex:0 0 28px;width:28px;height:28px;place-items:center;overflow:hidden;border-radius:6px;background:#fff}
.brand-mark img{display:block;width:100%;height:100%;object-fit:cover}
.brand-copy,.account-copy{min-width:0}
.brand-copy strong,.brand-copy small,.account-copy strong,.account-copy small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.brand-copy strong{color:#f5f8f9;font-size:14px;font-weight:600;letter-spacing:-.02em}
.brand-copy small,.account-copy small{margin-top:1px;color:#73858b;font-size:10px}
.sidebar-nav{min-height:0;flex:1;overflow:auto}
.nav{padding:8px 7px 16px;border:0;background:transparent;--el-menu-bg-color:transparent;--el-menu-text-color:#9aabb0;--el-menu-hover-bg-color:var(--ui-sidebar-hover);--el-menu-active-color:#f4fbfa}
.nav-label{padding:14px 9px 5px;color:#607279;font-size:10px;font-weight:600;letter-spacing:.1em}
.nav :deep(.el-menu-item){position:relative;height:38px;margin:2px 0;border-radius:6px;font-size:13px;font-weight:500;transition:color var(--ui-transition-fast),background-color var(--ui-transition-fast)}
.nav :deep(.el-menu-item::before){position:absolute;top:7px;bottom:7px;left:0;width:2px;border-radius:0 2px 2px 0;background:var(--ui-primary);content:"";opacity:0;transform:scaleY(.45);transition:opacity var(--ui-transition-fast),transform var(--ui-transition-fast)}
.nav :deep(.el-menu-item.is-active){background:var(--ui-sidebar-active)}
.nav :deep(.el-menu-item.is-active::before){opacity:1;transform:scaleY(1)}
.nav :deep(.el-icon){font-size:16px}
.sidebar.is-collapsed .nav{padding-inline:4px}
.sidebar-foot{display:flex;min-height:58px;align-items:center;justify-content:space-between;gap:8px;padding:10px 12px;border-top:1px solid rgba(215,226,229,.08);background:var(--ui-sidebar-secondary)}
.account-copy strong{color:#dfe8ea;font-size:12px;font-weight:600}
.sidebar-foot :deep(.el-button){flex:0 0 auto;color:#74878d}
.sidebar-foot :deep(.el-button:hover){color:#dffaf5;background:rgba(255,255,255,.06)}
.workspace{display:flex;min-width:0;flex:1;flex-direction:column}
.topbar{position:sticky;z-index:20;top:0;display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);flex:0 0 48px;align-items:center;gap:16px;height:48px;padding:0 20px;border-bottom:1px solid var(--ui-border);background:var(--ui-surface)}
.topbar-start,.topbar-end,.breadcrumb,.topbar-account,.environment-label{display:flex;align-items:center}
.topbar-start{min-width:0;gap:10px;justify-self:start}
.nav-toggle{flex:none;color:var(--ui-text-secondary)}
.breadcrumb{min-width:0;gap:8px;color:var(--ui-text-tertiary);font-size:11px}
.breadcrumb button{overflow:hidden;padding:4px 0;border:0;background:transparent;color:var(--ui-text-secondary);font-size:12px;cursor:pointer;text-overflow:ellipsis;white-space:nowrap}
.breadcrumb button:hover{color:var(--ui-primary-hover)}
.breadcrumb strong{overflow:hidden;color:var(--ui-text-primary);font:600 11px/1 var(--ui-font-mono);text-overflow:ellipsis;white-space:nowrap}
.environment-label{justify-self:center;gap:7px;color:var(--ui-text-secondary);font:600 10px/1 var(--ui-font-mono);letter-spacing:.08em}
.environment-label>span{width:5px;height:5px;border-radius:50%;background:var(--ui-success)}
.topbar-end{justify-self:end;gap:12px;min-width:max-content}
.topbar-account{gap:7px;color:var(--ui-text-secondary);font-size:11px}
.account-avatar{display:grid;width:26px;height:26px;place-items:center;border-radius:6px;background:var(--ui-sidebar);color:#fff;font:600 11px/1 var(--ui-font-mono)}
.main{min-width:0;flex:1;outline:none}
.nav-scrim{position:fixed;z-index:25;inset:0;border:0;background:rgba(17,25,29,.54)}
@media(max-width:1199px){.topbar{padding-inline:14px}.topbar-account-name{display:none}}
@media(max-width:767px){.sidebar{position:fixed;z-index:30;left:0;transform:translateX(-100%);box-shadow:var(--ui-shadow)}.sidebar.is-mobile-open{transform:translateX(0)}.topbar{grid-template-columns:minmax(0,1fr) auto;gap:8px;padding-inline:8px}.environment-label{display:none}.breadcrumb strong{max-width:120px}.topbar-end{gap:8px}}
</style>
