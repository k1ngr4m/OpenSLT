<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
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
  CircleCheck,
} from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const now = ref(new Date())
const isCompact = ref(false)
const isMobile = ref(false)
const mobileNavOpen = ref(false)
const manualCollapsed = ref(localStorage.getItem('openslt-nav-collapsed') === '1')
let timer = 0

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
  const first = `/${route.path.split('/').filter(Boolean)[0] || 'dashboard'}`
  return ['/dashboard', '/runs', '/plans', '/resources', '/logs', '/users'].includes(first) ? first : '/dashboard'
})
const utcText = computed(() => {
  const parts = now.value.toISOString().replace('T', ' ').slice(0, 19)
  return `${parts} UTC`
})

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
  timer = window.setInterval(() => { now.value = new Date() }, 1000)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncViewport)
  window.clearInterval(timer)
})
</script>

<template>
  <a class="skip-link" href="#main-content">跳到主要内容</a>
  <div class="shell">
    <aside class="sidebar" :class="sidebarClass">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">SL</div>
        <div v-show="!collapsed || isMobile" class="brand-copy">
          <strong>OpenSLT</strong>
          <small>自动化测速平台</small>
        </div>
      </div>

      <nav aria-label="主导航">
        <el-menu
          router
          :collapse="collapsed"
          :default-active="activePath"
          class="nav"
          @select="closeMobileNavigation"
        >
          <div v-if="!collapsed" class="nav-label">任务</div>
          <el-menu-item index="/dashboard">
            <el-icon><DataAnalysis /></el-icon>
            <template #title>工作台</template>
          </el-menu-item>
          <el-menu-item index="/runs">
            <el-icon><Monitor /></el-icon>
            <template #title>测速运行</template>
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

          <div v-if="!collapsed" class="nav-label">系统</div>
          <el-menu-item index="/logs">
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
          <el-tooltip :content="isMobile ? '打开导航' : (collapsed ? '展开导航' : '收起导航')" placement="bottom">
            <el-button text circle class="nav-toggle" :aria-label="collapsed ? '展开导航' : '收起导航'" @click="toggleNavigation">
              <el-icon><Expand v-if="collapsed || isMobile" /><Fold v-else /></el-icon>
            </el-button>
          </el-tooltip>
          <div class="service-health" role="status">
            <el-icon><CircleCheck /></el-icon>
            <span>系统服务正常</span>
          </div>
        </div>
        <time class="utc-time mono" :datetime="now.toISOString()">{{ utcText }}</time>
      </header>
      <main id="main-content" class="main" tabindex="-1">
        <router-view />
      </main>
    </section>
  </div>
</template>

<style scoped>
.shell{display:flex;min-height:100dvh;background:var(--ui-canvas)}
.sidebar{position:sticky;z-index:30;top:0;display:flex;flex:0 0 224px;flex-direction:column;width:224px;height:100dvh;color:#d9e7e8;background:var(--ui-sidebar);transition:width var(--ui-transition),flex-basis var(--ui-transition),transform var(--ui-transition)}
.sidebar.is-collapsed{flex-basis:64px;width:64px}
.brand{display:flex;flex:0 0 64px;align-items:center;gap:11px;padding:0 16px;border-bottom:1px solid rgba(184,218,219,.14)}
.brand-mark{display:grid;flex:0 0 34px;width:34px;height:34px;place-items:center;border:1px solid rgba(153,236,220,.24);border-radius:8px;color:#082e2a;background:#28b59e;font-size:13px;font-weight:800;letter-spacing:-.04em;box-shadow:inset 0 1px 0 rgba(255,255,255,.2)}
.brand-copy,.account-copy{min-width:0}
.brand-copy strong,.brand-copy small,.account-copy strong,.account-copy small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.brand-copy strong{color:#f4fbfb;font-size:15px;font-weight:650;letter-spacing:-.02em}
.brand-copy small,.account-copy small{margin-top:2px;color:#86a9ae;font-size:10px}
nav{min-height:0;flex:1;overflow:auto}
.nav{padding:10px 8px 18px;border:0;background:transparent;--el-menu-bg-color:transparent;--el-menu-text-color:#aac1c4;--el-menu-hover-bg-color:var(--ui-sidebar-hover);--el-menu-active-color:#ecfffb}
.nav-label{padding:13px 10px 6px;color:#658b90;font-size:10px;font-weight:600;letter-spacing:.12em}
.nav :deep(.el-menu-item){position:relative;height:42px;margin:2px 0;border-radius:6px;font-size:13px;font-weight:500;transition:color var(--ui-transition),background-color var(--ui-transition)}
.nav :deep(.el-menu-item::before){position:absolute;top:10px;bottom:10px;left:0;width:3px;border-radius:0 3px 3px 0;background:#52d7bf;content:"";opacity:0;transform:scaleY(.5);transition:opacity var(--ui-transition),transform var(--ui-transition)}
.nav :deep(.el-menu-item.is-active){background:var(--ui-sidebar-active)}
.nav :deep(.el-menu-item.is-active::before){opacity:1;transform:scaleY(1)}
.nav :deep(.el-icon){font-size:17px}
.sidebar.is-collapsed .nav{padding-inline:5px}
.sidebar-foot{display:flex;min-height:66px;align-items:center;justify-content:space-between;gap:8px;padding:12px 14px;border-top:1px solid rgba(184,218,219,.14)}
.account-copy strong{color:#e8f3f4;font-size:12px;font-weight:600}
.sidebar-foot :deep(.el-button){flex:0 0 auto;color:#7da1a6}
.sidebar-foot :deep(.el-button:hover){color:#dff7f2;background:rgba(255,255,255,.08)}
.workspace{display:flex;min-width:0;flex:1;flex-direction:column}
.topbar{position:sticky;z-index:20;top:0;display:flex;flex:0 0 52px;align-items:center;justify-content:space-between;gap:20px;height:52px;padding:0 24px;border-bottom:1px solid var(--ui-border);background:rgba(255,255,255,.94);backdrop-filter:blur(12px)}
.topbar-start,.service-health{display:flex;align-items:center}
.topbar-start{gap:8px}
.nav-toggle{color:var(--ui-text-secondary)}
.service-health{gap:7px;color:var(--ui-success);font-size:12px;font-weight:500}
.service-health .el-icon{font-size:15px}
.utc-time{color:var(--ui-text-tertiary);font-size:11px}
.main{min-width:0;flex:1;outline:none}
.nav-scrim{position:fixed;z-index:25;inset:0;border:0;background:rgba(5,25,29,.5)}
@media(max-width:1199px){.topbar{padding-inline:16px}}
@media(max-width:767px){.sidebar{position:fixed;z-index:30;left:0;transform:translateX(-100%);box-shadow:var(--ui-shadow)}.sidebar.is-mobile-open{transform:translateX(0)}.topbar{padding-inline:12px}.utc-time{font-size:10px}.service-health span{display:none}}
</style>
