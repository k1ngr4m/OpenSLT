<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Warning, CircleCheck, Connection, ArrowRight } from '@element-plus/icons-vue'
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
const healthy = computed(() => resources.value.filter(resource => resource.health_status === 'healthy').length)
const unhealthyResources = computed(() => resources.value.filter(resource => resource.is_enabled && resource.health_status && resource.health_status !== 'healthy'))
const attentionRuns = computed(() => runs.value.filter(run => run.status.includes('awaiting') || run.status.includes('failed') || run.status === 'timed_out').slice(0, 5))
const recentRuns = computed(() => [...runs.value].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)).slice(0, 8))

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
    <header class="page-header">
      <div>
        <span class="page-kicker">运行总览</span>
        <h1 class="page-title">工作台</h1>
        <p class="muted">查看测速任务、待处理事项和基础资源健康状态</p>
      </div>
      <el-button v-if="auth.canOperate" type="primary" @click="router.push('/runs?create=1')">创建测速运行</el-button>
    </header>

    <el-alert
      v-if="loadError"
      class="load-error"
      title="工作台数据加载失败"
      :description="loadError"
      type="error"
      show-icon
      :closable="false"
    >
      <template #default><el-button size="small" @click="load">重新加载</el-button></template>
    </el-alert>

    <el-skeleton v-if="loading" :rows="8" animated />
    <template v-else>
      <section class="overview-strip" aria-label="运行概览">
        <button type="button" class="overview-item" @click="router.push('/runs')">
          <span>运行总数</span><strong>{{ runs.length }}</strong><small>全部运行记录</small>
        </button>
        <button type="button" class="overview-item" @click="router.push('/runs?status=active')">
          <span>正在处理</span><strong>{{ active }}</strong><small>执行中与排队中</small>
        </button>
        <button type="button" class="overview-item is-warning" @click="router.push('/runs?status=awaiting')">
          <span>等待人工处理</span><strong>{{ awaiting }}</strong><small>确认、复核与节点操作</small>
        </button>
        <button type="button" class="overview-item" :class="{ 'is-danger': failed }" @click="router.push('/runs?status=failed')">
          <span>异常运行</span><strong>{{ failed }}</strong><small>失败与超时</small>
        </button>
        <button type="button" class="overview-item is-success" @click="router.push('/resources')">
          <span>健康资源</span><strong>{{ healthy }} / {{ resources.length }}</strong><small>当前可用资源</small>
        </button>
      </section>

      <div class="dashboard-grid">
        <section class="card recent-panel">
          <div class="section-heading">
            <div><h2>最近运行</h2><p>按创建时间显示最近 8 条记录</p></div>
            <el-button text @click="router.push('/runs')">查看全部<el-icon class="el-icon--right"><ArrowRight /></el-icon></el-button>
          </div>
          <el-table :data="recentRuns" class="run-table" @row-click="row => router.push(`/runs/${row.id}`)">
            <el-table-column label="运行编号" min-width="175">
              <template #default="scope"><strong class="mono run-number">{{ scope.row.run_number }}</strong></template>
            </el-table-column>
            <el-table-column label="方案 / 场景" min-width="190">
              <template #default="scope"><strong>{{ scope.row.config_snapshot?.plan?.name || '-' }}</strong><small>{{ scope.row.config_snapshot?.scenario?.name || businessText[scope.row.business_code] }}</small></template>
            </el-table-column>
            <el-table-column label="状态" width="130">
              <template #default="scope"><StatusBadge :status="scope.row.status" show-raw /></template>
            </el-table-column>
            <el-table-column label="进度" width="145">
              <template #default="scope"><el-progress :percentage="scope.row.progress" :stroke-width="6" /></template>
            </el-table-column>
            <el-table-column label="创建时间（北京时间）" width="190">
              <template #default="scope"><span class="table-time">{{ formatBeijingDateTime(scope.row.created_at) }}</span></template>
            </el-table-column>
          </el-table>
          <div v-if="!recentRuns.length" class="empty-state">
            <div><strong>尚无测速运行</strong><span>创建运行后，可在这里快速查看进度与结果。</span><br><el-button v-if="auth.canOperate" class="empty-action" type="primary" @click="router.push('/runs?create=1')">创建首个运行</el-button></div>
          </div>
        </section>

        <aside class="attention-column">
          <section class="card attention-panel">
            <div class="section-heading"><div><h2>需要处理</h2><p>优先展示人工节点和运行异常</p></div><span class="count">{{ attentionRuns.length }}</span></div>
            <div v-if="attentionRuns.length" class="attention-list">
              <button v-for="run in attentionRuns" :key="run.id" type="button" class="attention-row" @click="router.push(`/runs/${run.id}`)">
                <el-icon :class="run.status.includes('failed') || run.status === 'timed_out' ? 'danger-icon' : 'warning-icon'"><Warning /></el-icon>
                <span><strong class="mono">{{ run.run_number }}</strong><small>{{ run.config_snapshot?.scenario?.name || businessText[run.business_code] }}</small></span>
                <StatusBadge :status="run.status" />
              </button>
            </div>
            <div v-else class="compact-empty"><el-icon><CircleCheck /></el-icon><div><strong>当前没有待处理事项</strong><span>运行与资源状态均无需人工介入</span></div></div>
          </section>

          <section class="card resource-panel">
            <div class="section-heading"><div><h2>资源健康</h2><p>{{ healthy }} 个健康，{{ unhealthyResources.length }} 个需关注</p></div><el-icon><Connection /></el-icon></div>
            <div v-if="unhealthyResources.length" class="resource-list">
              <button v-for="resource in unhealthyResources.slice(0, 5)" :key="resource.id" type="button" @click="router.push('/resources')">
                <span><strong>{{ resource.name }}</strong><small class="mono">{{ resource.host || resource.database_host || '-' }}</small></span>
                <el-tag type="danger" effect="plain" size="small">{{ healthText(resource.health_status) }}</el-tag>
              </button>
            </div>
            <div v-else class="compact-empty is-success"><el-icon><CircleCheck /></el-icon><div><strong>资源状态正常</strong><span>已启用资源未报告异常</span></div></div>
          </section>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.dashboard-page{max-width:1600px}.load-error{margin-bottom:16px}.overview-strip{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));overflow:hidden;margin-bottom:16px;border:1px solid var(--ui-border);border-radius:var(--ui-radius-panel);background:var(--ui-border)}.overview-item{display:grid;min-width:0;padding:17px 18px 18px;border:0;background:var(--ui-surface);color:var(--ui-text-primary);text-align:left;cursor:pointer;transition:background-color var(--ui-transition)}.overview-item+*{border-left:1px solid var(--ui-border)}.overview-item:hover{background:#f8fbfb}.overview-item:focus-visible{position:relative;z-index:1}.overview-item span{color:var(--ui-text-secondary);font-size:12px;font-weight:500}.overview-item strong{margin-top:6px;font-family:"Cascadia Code",Consolas,monospace;font-size:26px;font-weight:650;letter-spacing:-.04em}.overview-item small{margin-top:5px;color:var(--ui-text-tertiary);font-size:10px}.overview-item.is-warning strong{color:var(--ui-warning)}.overview-item.is-danger strong{color:var(--ui-danger)}.overview-item.is-success strong{color:var(--ui-success)}.dashboard-grid{display:grid;grid-template-columns:minmax(0,1fr) 340px;align-items:start;gap:16px}.section-heading{padding:15px 17px}.section-heading h2{font-size:15px}.section-heading p{margin:3px 0 0;color:var(--ui-text-tertiary);font-size:11px}.recent-panel{min-width:0;overflow:hidden}.run-table{border-radius:0}.run-table :deep(.el-table__row){cursor:pointer}.run-number{font-size:12px;white-space:nowrap}.run-table strong,.run-table small{display:block}.run-table small{margin-top:3px;color:var(--ui-text-secondary);font-size:11px}.table-time{color:var(--ui-text-secondary);font-size:11px}.empty-action{margin-top:18px}.attention-column{display:grid;gap:16px}.count{display:grid;width:26px;height:26px;place-items:center;border-radius:6px;color:var(--ui-warning);background:#f8ecda;font:600 12px/1 "Cascadia Code",Consolas,monospace}.attention-list,.resource-list{display:grid}.attention-row,.resource-list button{display:flex;width:100%;align-items:center;gap:10px;padding:12px 15px;border:0;border-bottom:1px solid var(--ui-border);background:transparent;color:var(--ui-text-primary);text-align:left;cursor:pointer;transition:background-color var(--ui-transition)}.attention-row:last-child,.resource-list button:last-child{border-bottom:0}.attention-row:hover,.resource-list button:hover{background:#f5f9f9}.attention-row>.el-icon{flex:0 0 auto;font-size:16px}.warning-icon{color:var(--ui-warning)}.danger-icon{color:var(--ui-danger)}.attention-row>span,.resource-list button>span{min-width:0;flex:1}.attention-row strong,.attention-row small,.resource-list strong,.resource-list small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.attention-row strong{font-size:11px}.attention-row small,.resource-list small{margin-top:3px;color:var(--ui-text-secondary);font-size:10px}.compact-empty{display:flex;align-items:center;gap:11px;padding:22px 16px;color:var(--ui-warning)}.compact-empty.is-success{color:var(--ui-success)}.compact-empty>.el-icon{font-size:24px}.compact-empty strong,.compact-empty span{display:block}.compact-empty strong{color:var(--ui-text-primary);font-size:12px}.compact-empty span{margin-top:3px;color:var(--ui-text-tertiary);font-size:10px}.resource-panel .section-heading>.el-icon{color:var(--ui-primary);font-size:18px}.resource-list button{justify-content:space-between}.resource-list strong{font-size:12px}
@media(max-width:1199px){.dashboard-grid{grid-template-columns:1fr}.attention-column{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:900px){.overview-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.overview-item:nth-child(odd){border-left:0}.overview-item:nth-child(n+3){border-top:1px solid var(--ui-border)}.overview-item:last-child{grid-column:1/-1}}
@media(max-width:767px){.overview-strip{grid-template-columns:1fr}.overview-item+*{border-top:1px solid var(--ui-border);border-left:0}.attention-column{grid-template-columns:1fr}.run-table :deep(.el-table__body-wrapper),.run-table :deep(.el-table__header-wrapper){overflow-x:auto}}
</style>
