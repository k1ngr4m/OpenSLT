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
  House,
  Setting,
  MagicStick,
  Cpu,
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
const managementMode = computed(() => route.meta.section === 'management')
const activePath = computed(() => {
  if (route.path.startsWith('/smart-cases/settings')) return '/smart-cases/settings'
  const first = `/${route.path.split('/').filter(Boolean)[0] || 'dashboard'}`
  return ['/dashboard', '/runs', '/plans', '/resources', '/smart-cases', '/models', '/logs', '/users'].includes(first) ? first : '/dashboard'
})
const navToggleLabel = computed(() => {
  if (isMobile.value) return '打开导航'
  return collapsed.value ? '展开导航' : '收起导航'
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

function openHome() {
  return router.push('/dashboard')
}

function openManagementCenter() {
  return router.push('/plans')
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
          <template v-if="managementMode">
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
              <template #title>知识源管理</template>
            </el-menu-item>
            <el-menu-item v-if="auth.isAdmin" index="/models">
              <el-icon><Cpu /></el-icon>
              <template #title>模型管理</template>
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
          </template>
          <template v-else>
            <el-menu-item index="/dashboard">
              <el-icon><DataAnalysis /></el-icon>
              <template #title>工作台</template>
            </el-menu-item>
            <el-menu-item v-if="auth.canOperate" index="/smart-cases">
              <el-icon><MagicStick /></el-icon>
              <template #title>智能用例</template>
            </el-menu-item>
            <div v-if="!collapsed" class="nav-label">测速</div>
            <el-menu-item index="/runs">
              <el-icon><Monitor /></el-icon>
              <template #title>测速运行</template>
            </el-menu-item>
          </template>
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
        </div>

        <nav class="section-nav" aria-label="页面导航">
          <button
            class="section-nav-item"
            :class="{ 'is-active': !managementMode }"
            type="button"
            :aria-current="!managementMode ? 'page' : undefined"
            @click="openHome"
          >
            <el-icon><House /></el-icon>
            <span class="section-nav-label">首页</span>
          </button>
        </nav>

        <div class="topbar-end">
          <el-tooltip content="管理中心" placement="bottom">
            <el-button
              text
              circle
              class="management-center"
              :class="{ 'is-active': managementMode }"
              aria-label="管理中心"
              @click="openManagementCenter"
            >
              <el-icon><Setting /></el-icon>
            </el-button>
          </el-tooltip>
          <VersionHistory />
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
.sidebar{position:sticky;z-index:30;top:0;display:flex;flex:0 0 224px;flex-direction:column;width:224px;height:100dvh;color:#d9e7e8;background:var(--ui-sidebar);transition:width var(--ui-transition),flex-basis var(--ui-transition),transform var(--ui-transition)}
.sidebar.is-collapsed{flex-basis:64px;width:64px}
.brand{display:flex;flex:0 0 64px;align-items:center;gap:11px;padding:0 16px;border-bottom:1px solid rgba(184,218,219,.14)}
.brand-mark{display:grid;flex:0 0 34px;width:34px;height:34px;place-items:center;overflow:hidden;border:1px solid rgba(153,236,220,.24);border-radius:8px;background:#102f34;box-shadow:inset 0 1px 0 rgba(255,255,255,.2)}
.brand-mark img{display:block;width:100%;height:100%;object-fit:cover}
.brand-copy,.account-copy{min-width:0}
.brand-copy strong,.brand-copy small,.account-copy strong,.account-copy small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.brand-copy strong{color:#f4fbfb;font-size:15px;font-weight:650;letter-spacing:-.02em}
.brand-copy small,.account-copy small{margin-top:2px;color:#86a9ae;font-size:10px}
.sidebar-nav{min-height:0;flex:1;overflow:auto}
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
.topbar{position:sticky;z-index:20;top:0;display:grid;grid-template-columns:minmax(40px,1fr) auto minmax(40px,1fr);flex:0 0 52px;align-items:center;gap:16px;height:52px;padding:0 24px;border-bottom:1px solid var(--ui-border);background:rgba(255,255,255,.94);backdrop-filter:blur(12px)}
.topbar-start{display:flex;align-items:center;justify-self:start}
.topbar-start{gap:8px}
.nav-toggle{color:var(--ui-text-secondary)}
.section-nav{display:flex;align-self:stretch;align-items:stretch;justify-self:center;gap:4px;overflow:visible}
.section-nav-item{position:relative;display:flex;min-width:72px;align-items:center;justify-content:center;gap:7px;padding:0 13px;border:0;background:transparent;color:var(--ui-text-secondary);font-size:13px;font-weight:600;cursor:pointer;transition:color var(--ui-transition),background-color var(--ui-transition)}
.section-nav-item::after{position:absolute;right:12px;bottom:0;left:12px;height:2px;border-radius:2px 2px 0 0;background:var(--ui-primary);content:"";opacity:0;transform:scaleX(.55);transition:opacity var(--ui-transition),transform var(--ui-transition)}
.section-nav-item:hover{background:var(--ui-primary-soft);color:var(--ui-primary-hover)}
.section-nav-item:active{transform:translateY(1px)}
.section-nav-item.is-active{color:var(--ui-primary)}
.section-nav-item.is-active::after{opacity:1;transform:scaleX(1)}
.section-nav-item .el-icon{font-size:18px}
.topbar-end{display:flex;align-items:center;justify-self:end;gap:12px;min-width:max-content}
.management-center{color:var(--ui-text-secondary);transition:color var(--ui-transition),background-color var(--ui-transition),transform var(--ui-transition)}
.management-center:hover:not(.is-active){color:var(--ui-text-secondary);background:var(--el-fill-color-light)}
.management-center.is-active,.management-center.is-active:hover{color:#cf6419;background:#fff0e5}
.management-center:active{transform:translateY(1px)}
.management-center :deep(.el-icon){font-size:19px}
.main{min-width:0;flex:1;outline:none}
.nav-scrim{position:fixed;z-index:25;inset:0;border:0;background:rgba(5,25,29,.5)}
@media(max-width:1199px){.topbar{padding-inline:16px}}
@media(max-width:767px){.sidebar{position:fixed;z-index:30;left:0;transform:translateX(-100%);box-shadow:var(--ui-shadow)}.sidebar.is-mobile-open{transform:translateX(0)}.topbar{gap:6px;padding-inline:8px}.section-nav{gap:2px}.section-nav-item{min-width:38px;padding-inline:9px}.section-nav-item::after{right:8px;left:8px}.section-nav-label{display:none}.topbar-end{gap:6px}}
</style>
