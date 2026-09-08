<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { CircleCheck, ArrowRight, Plus } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { ApiResource } from '@/types/api'
import type { RunDetail } from '@/types/run'
import { businessText } from '@/utils/status'
import { formatBeijingDateTime } from '@/utils/time'
import StatusBadge from '@/components/StatusBadge.vue'

const router = useRouter()
const auth = useAuthStore()
const runs = ref<RunDetail[]>([])
const resources = ref<ApiResource[]>([])
const loading = ref(true)
const loadError = ref('')

const terminalStatuses = new Set(['completed', 'cancelled', 'execution_failed', 'parse_failed', 'precheck_failed', 'timed_out'])
const active = computed(() => runs.value.filter(run => !terminalStatuses.has(run.status)).length)
const awaiting = computed(() => runs.value.filter(run => run.status.includes('awaiting')).length)
const failed = computed(() => runs.value.filter(run => run.status.includes('failed') || run.status === 'timed_out').length)
const enabledResources = computed(() => resources.value.filter(resource => resource.is_enabled))
const healthy = computed(() => enabledResources.value.filter(resource => resource.health_status === 'healthy').length)
const unhealthyResources = computed(() => enabledResources.value.filter(resource => resource.health_status !== 'healthy'))
const attentionRuns = computed(() => runs.value.filter(run => run.status.includes('awaiting') || run.status.includes('failed') || run.status === 'timed_out'))
const recentRuns = computed(() => [...runs.value].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)).slice(0, 8))

const listMode = ref<'attention' | 'recent'>('recent')
const displayedRuns = computed(() => listMode.value === 'attention' ? attentionRuns.value.slice(0, 8) : recentRuns.value)

function healthText(value?: string | null) {
  return value === 'healthy' ? '健康' : value === 'unhealthy' ? '异常' : '未知'
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    ;[runs.value, resources.value] = await Promise.all([
      api.get<RunDetail[]>('/runs').then(response => response.data),
      api.get<ApiResource[]>('/resources').then(response => response.data),
    ])
    listMode.value = attentionRuns.value.length ? 'attention' : 'recent'
  } catch (error) {
    loadError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page dashboard-page">
    <header class="page-header dashboard-header">
      <div><span class="page-kicker">测速运行</span><h1 class="page-title">工作台</h1><p class="muted">从这里继续任务，查看进度与结果。</p></div>
      <el-button v-if="auth.canOperate" type="primary" :icon="Plus" @click="router.push('/runs?create=1')">创建测速运行</el-button>
    </header>
    <el-alert v-if="loadError" class="load-error" title="工作台数据加载失败" :description="loadError" type="error" show-icon :closable="false">
      <template #default><el-button size="small" @click="load">重新加载</el-button></template>
    </el-alert>
    <el-skeleton v-if="loading" :rows="8" animated />
    <template v-else-if="!loadError">
      <section class="overview-strip" aria-label="运行概览">
        <button type="button" @click="router.push('/runs')"><span>全部运行</span><strong>{{ runs.length }}</strong></button>
        <button type="button" @click="router.push('/runs?status=active')"><span>未结束</span><strong>{{ active }}</strong></button>
        <button type="button" :class="{ warning: awaiting }" @click="router.push('/runs?status=awaiting')"><span>等待人工处理</span><strong>{{ awaiting }}</strong></button>
        <button type="button" :class="{ danger: failed }" @click="router.push('/runs?status=failed')"><span>失败 / 超时</span><strong>{{ failed }}</strong></button>
      </section>
      <div class="dashboard-grid">
        <section class="task-section" aria-label="运行任务">
          <div class="task-heading">
            <div class="task-filters" role="group" aria-label="运行筛选">
              <button type="button" :aria-pressed="listMode === 'attention'" @click="listMode = 'attention'">待处理<span>{{ attentionRuns.length }}</span></button>
              <button type="button" :aria-pressed="listMode === 'recent'" @click="listMode = 'recent'">最近运行</button>
            </div>
            <el-button link @click="router.push('/runs')">全部运行<el-icon class="el-icon--right"><ArrowRight /></el-icon></el-button>
          </div>
          <p class="list-caption">{{ listMode === 'attention' ? '需要人工确认、复核或排查的任务' : '按创建时间查看最近的测速任务' }}<span v-if="displayedRuns.length"> · 显示 {{ displayedRuns.length }} 条</span></p>
          <div v-if="displayedRuns.length" class="task-list">
            <div class="task-list-labels" aria-hidden="true"><span>任务 / 运行编号</span><span>状态与进度</span><span>创建时间</span><span></span></div>
            <RouterLink v-for="run in displayedRuns" :key="run.id" class="task-row" :to="`/runs/${run.id}`">
              <div class="task-identity">
                <strong>{{ run.config_snapshot?.scenario?.name || businessText[run.business_code] || '测速任务' }}</strong>
                <span>{{ run.config_snapshot?.plan?.name || '-' }}<i> / </i><span class="mono">{{ run.run_number }}</span></span>
              </div>
              <div class="task-status"><StatusBadge :status="run.status" /><div class="task-progress"><progress :value="run.progress" max="100" :aria-label="`${run.run_number} 进度`" /><span>{{ run.progress }}%</span></div></div>
              <time class="task-time">{{ formatBeijingDateTime(run.created_at) }}</time>
              <el-icon class="task-arrow"><ArrowRight /></el-icon>
            </RouterLink>
          </div>
          <div v-else class="task-empty">
            <el-icon><CircleCheck /></el-icon><strong>{{ listMode === 'attention' ? '当前没有待处理任务' : '还没有测速运行' }}</strong>
            <p>{{ listMode === 'attention' ? '可以切换到最近运行，查看进度与结果。' : '创建第一次运行，开始采集与分析。' }}</p>
            <el-button v-if="listMode === 'attention'" link type="primary" @click="listMode = 'recent'">查看最近运行</el-button>
            <el-button v-else-if="auth.canOperate" link type="primary" @click="router.push('/runs?create=1')">创建首个运行<el-icon class="el-icon--right"><ArrowRight /></el-icon></el-button>
          </div>
        </section>
        <aside class="resource-section" aria-label="执行资源">
          <div class="resource-heading"><h2>执行资源</h2><el-button link aria-label="管理资源" @click="router.push('/resources')"><el-icon><ArrowRight /></el-icon></el-button></div>
          <div class="resource-total"><strong>{{ healthy }}</strong><span>/ {{ enabledResources.length }} 健康</span></div>
          <p class="resource-summary">{{ !enabledResources.length ? '暂无启用资源' : unhealthyResources.length ? `${unhealthyResources.length} 个资源需关注` : '已启用资源均正常' }}</p>
          <div v-if="unhealthyResources.length" class="resource-list">
            <RouterLink v-for="resource in unhealthyResources.slice(0, 5)" :key="resource.id" to="/resources"><span><strong>{{ resource.name }}</strong><small>{{ resource.host || resource.database_host || '-' }}</small></span><span :class="resource.health_status === 'unhealthy' ? 'danger' : 'warning'">{{ healthText(resource.health_status) }}</span></RouterLink>
          </div>
          <div v-else-if="enabledResources.length" class="resource-ready"><span aria-hidden="true"></span>可以开始新的测速</div>
          <el-button link class="resource-link" @click="router.push('/resources')">查看全部资源<el-icon class="el-icon--right"><ArrowRight /></el-icon></el-button>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.dashboard-page { max-width: 1600px; }
.dashboard-header { align-items: center; margin-bottom: 28px; }
.load-error { margin-bottom: 20px; }
.overview-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 24px; padding: 22px 0 28px; border-top: 1px solid var(--ui-border); border-bottom: 1px solid var(--ui-border); }
.overview-strip button { display: flex; align-items: baseline; gap: 20px; border: 0; padding: 4px 0; color: var(--ui-text-primary); background: transparent; text-align: left; cursor: pointer; }
.overview-strip button:hover span { color: var(--ui-primary); }
.overview-strip button > span { color: var(--ui-text-secondary); font-size: 13px; transition: color var(--ui-transition); }
.overview-strip strong { font-size: 32px; line-height: 1.2; font-weight: 600; letter-spacing: -.04em; font-variant-numeric: tabular-nums; }
.overview-strip button.warning strong { color: var(--ui-warning); }
.overview-strip button.danger strong { color: var(--ui-danger); }
.warning { color: var(--ui-warning); }
.dashboard-grid { display: grid; grid-template-columns: minmax(0, 1fr) 240px; gap: 36px; margin-top: 32px; }
.task-section { min-width: 0; }
.task-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--ui-border); }
.task-filters { display: flex; gap: 28px; }
.task-filters button { display: flex; align-items: center; gap: 8px; position: relative; padding: 0 0 14px; border: 0; background: transparent; color: var(--ui-text-secondary); font: inherit; font-weight: 600; cursor: pointer; }
.task-filters button[aria-pressed=true] { color: var(--ui-primary); }
.task-filters button[aria-pressed=true]::after { position: absolute; right: 0; bottom: -1px; left: 0; height: 2px; background: var(--ui-primary); content: ''; }
.task-filters button > span { color: var(--ui-text-secondary); font-size: 12px; font-weight: 400; }
.task-heading > .el-button { align-self: flex-start; }
.list-caption { margin: 14px 0 18px; color: var(--ui-text-secondary); font-size: 12px; }
.task-list-labels, .task-row { display: grid; grid-template-columns: minmax(180px, 1fr) 140px 148px 16px; align-items: center; gap: 20px; }
.task-list-labels { padding: 0 16px 10px; color: var(--ui-text-secondary); font-size: 12px; }
.task-row { min-height: 86px; padding: 18px 16px; border-top: 1px solid var(--ui-border); color: var(--ui-text-primary); background: var(--ui-surface); text-decoration: none; transition: background-color var(--ui-transition); }
.task-row:last-child { border-bottom: 1px solid var(--ui-border); }
.task-row:hover { background: var(--ui-primary-soft); }
.task-identity { min-width: 0; }
.task-identity > strong { display: block; margin-bottom: 6px; overflow-wrap: anywhere; font-size: 15px; font-weight: 600; }
.task-identity > span { display: block; color: var(--ui-text-secondary); font-size: 12px; overflow-wrap: anywhere; }
.task-identity i { padding: 0 4px; font-style: normal; color: var(--ui-border-strong); }
.task-status { display: grid; justify-items: start; gap: 8px; }
.task-progress { display: flex; align-items: center; gap: 8px; color: var(--ui-text-secondary); font-size: 12px; }
.task-progress progress { width: 68px; height: 3px; border: 0; border-radius: 2px; background: var(--ui-border); appearance: none; }
.task-progress progress::-webkit-progress-bar { background: var(--ui-border); }
.task-progress progress::-webkit-progress-value { background: var(--ui-primary); }
.task-progress progress::-moz-progress-bar { background: var(--ui-primary); }
.task-time { color: var(--ui-text-secondary); font-size: 12px; font-variant-numeric: tabular-nums; }
.task-arrow { color: var(--ui-text-tertiary); transition: transform var(--ui-transition); }
.task-row:hover .task-arrow { transform: translateX(2px); color: var(--ui-primary); }
.resource-section { align-self: start; padding-left: 28px; border-left: 1px solid var(--ui-border); }
.resource-heading { display: flex; align-items: center; justify-content: space-between; }
.resource-heading h2 { margin: 0; font-size: 14px; font-weight: 600; }
.resource-total { display: flex; align-items: baseline; gap: 10px; margin-top: 24px; }
.resource-total strong { font-size: 36px; font-weight: 500; line-height: 1; font-variant-numeric: tabular-nums; }
.resource-total > span, .resource-summary { color: var(--ui-text-secondary); font-size: 12px; }
.resource-summary { margin: 12px 0 20px; }
.resource-ready { display: flex; align-items: center; gap: 8px; color: var(--ui-success); font-size: 12px; }
.resource-ready > span { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.resource-link { margin-top: 24px; font-size: 12px; }
.resource-list a { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 0; border-top: 1px solid var(--ui-border); color: var(--ui-text-primary); text-decoration: none; font-size: 12px; }
.resource-list a > span:first-child { min-width: 0; }
.resource-list strong, .resource-list small { display: block; overflow-wrap: anywhere; font-size: 12px; }
.resource-list small { margin-top: 5px; color: var(--ui-text-secondary); }
.task-empty { display: grid; justify-items: start; gap: 12px; padding: 40px 0; border-top: 1px solid var(--ui-border); }
.task-empty > .el-icon { color: var(--ui-text-tertiary); font-size: 24px; }
.task-empty > strong { font-size: 16px; font-weight: 500; }
.task-empty p { margin: 0; color: var(--ui-text-secondary); font-size: 13px; }
@media(max-width:1199px) { .dashboard-grid { grid-template-columns: minmax(0,1fr) 200px; gap: 24px; } .resource-section { padding-left: 20px; } .task-list-labels, .task-row { grid-template-columns: minmax(130px,1fr) 135px 16px; gap: 16px; } .task-time, .task-list-labels > span:nth-child(3) { display: none; } }
@media(max-width:767px) { .overview-strip { grid-template-columns: repeat(2,minmax(0,1fr)); gap: 20px; padding: 20px 0; } .overview-strip button { flex-direction: column; gap: 8px; } .overview-strip strong { font-size: 28px; } .dashboard-header { align-items: stretch; } .dashboard-grid { grid-template-columns: 1fr; gap: 32px; margin-top: 24px; } .resource-section { padding: 24px 0 0; border-left: 0; border-top: 1px solid var(--ui-border); } .task-filters { gap: 20px; } .task-list-labels { display: none; } .task-row { grid-template-columns: minmax(0,1fr) 125px; gap: 12px; padding: 16px 10px; } .task-arrow { display: none; } .task-identity > strong { font-size: 14px; } .task-time { display: block; grid-column: 1/-1; } }
</style>
