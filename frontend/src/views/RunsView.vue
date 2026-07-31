<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from '@/ui/elementPlusServices'
import { Search, RefreshRight, Plus, CopyDocument, Delete } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { ApiPlan, ApiResource, ApiScenario } from '@/types/api'
import type { RunDetail } from '@/types/run'
import { businessText, resourceText } from '@/utils/status'
import { formatBeijingDateTime } from '@/utils/time'
import StatusBadge from '@/components/StatusBadge.vue'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const runs = ref<RunDetail[]>([])
const plans = ref<ApiPlan[]>([])
const scenarios = ref<ApiScenario[]>([])
const resources = ref<ApiResource[]>([])
const loading = ref(false)
const loadError = ref('')
const drawer = ref(false)
const creating = ref(false)
const deletingRunId = ref<number | null>(null)
const filters = reactive({ keyword: '', business: '', status: '' })
const form = reactive<{ plan_id: number; scenario_id: number | null }>({ plan_id: 0, scenario_id: null })
const resourceSelections = reactive<Record<string, number | null>>({})
const terminalStatuses = new Set(['completed', 'cancelled', 'execution_failed', 'parse_failed', 'precheck_failed', 'timed_out'])
const deletableStatuses = new Set([
  ...terminalStatuses,
  'draft',
  'awaiting_wiring',
  'awaiting_review',
  'awaiting_step_start',
  'awaiting_step_completion',
  'awaiting_step_retry',
  'paused',
])

const selectedPlan = computed(() => plans.value.find(plan => plan.id === form.plan_id))
const selectedScenario = computed(() => scenarios.value.find(scenario => scenario.id === form.scenario_id))
const availableScenarios = computed(() => scenarios.value.filter(scenario => scenario.plan_id === form.plan_id))
const requiredTypes = computed<string[]>(() => selectedScenario.value?.required_resource_types || [])
const canCreate = computed(() => Boolean(form.scenario_id && requiredTypes.value.length && requiredTypes.value.every(type => resourceSelections[type])))
const filteredRuns = computed(() => runs.value.filter(run => {
  const keyword = filters.keyword.trim().toLowerCase()
  const text = [run.run_number, run.config_snapshot?.plan?.name, run.config_snapshot?.scenario?.name].filter(Boolean).join(' ').toLowerCase()
  const businessMatch = !filters.business || run.business_code === filters.business
  const statusMatch = !filters.status
    || run.status === filters.status
    || (filters.status === 'active' && !terminalStatuses.has(run.status))
    || (filters.status === 'awaiting' && run.status.includes('awaiting'))
    || (filters.status === 'failed' && (run.status.includes('failed') || run.status === 'timed_out'))
  return (!keyword || text.includes(keyword)) && businessMatch && statusMatch
}))

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    ;[runs.value, plans.value, scenarios.value, resources.value] = await Promise.all([
      api.get<RunDetail[]>('/runs').then(response => response.data),
      api.get<ApiPlan[]>('/plans').then(response => response.data),
      api.get<ApiScenario[]>('/scenarios').then(response => response.data),
      api.get<ApiResource[]>('/resources').then(response => response.data),
    ])
  } catch (error) {
    loadError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

function resetResourceSelections() {
  for (const key of Object.keys(resourceSelections)) delete resourceSelections[key]
}

function open() {
  form.plan_id = plans.value.find(plan => plan.is_enabled)?.id || 0
  form.scenario_id = null
  resetResourceSelections()
  drawer.value = true
}

function resetFilters() {
  Object.assign(filters, { keyword: '', business: '', status: '' })
  router.replace({ query: {} })
}

function handlePlanChange() {
  form.scenario_id = null
  resetResourceSelections()
}

function handleScenarioChange() {
  resetResourceSelections()
  const scenario = selectedScenario.value
  if (!scenario) return
  for (const type of scenario.required_resource_types || []) resourceSelections[type] = null
  for (const resourceId of scenario.default_resource_ids || []) {
    const resource = resources.value.find(item => item.id === resourceId)
    if (resource && resource.is_enabled && resource.business_code === selectedPlan.value?.business_code) {
      resourceSelections[resource.resource_type] = resource.id
    }
  }
}

function resourceOptions(type: string) {
  return resources.value.filter(resource =>
    resource.resource_type === type
    && resource.business_code === selectedPlan.value?.business_code
    && resource.is_enabled,
  )
}

function resourceOptionLabel(resource: ApiResource) {
  const location = resource.resource_type === 'database'
    ? `${resource.database_host || ''}:${resource.database_port || ''}`
    : `${resource.host || ''}:${resource.ssh_port || ''}`
  const health = resource.health_status === 'healthy' ? '健康' : (resource.health_status || '未检查')
  return `${resource.name}  |  ${location || '无地址'}  |  ${health}`
}

async function copyRunNumber(value: string) {
  await navigator.clipboard.writeText(value)
  ElMessage.success(`已复制运行编号 ${value}`)
}

async function removeRun(run: RunDetail) {
  const willCancel = !terminalStatuses.has(run.status)
  const message = willCancel
    ? `运行 ${run.run_number} 尚未结束。删除会先取消运行并释放资源，同时永久删除步骤、日志、结果和本地产物。`
    : `确定永久删除运行 ${run.run_number}？关联的步骤、日志、结果和本地产物也会删除。`
  try {
    await ElMessageBox.confirm(message, '删除测速运行', {
      type: 'warning',
      confirmButtonText: willCancel ? '取消运行并删除' : '删除',
      confirmButtonClass: 'el-button--danger',
    })
    deletingRunId.value = run.id
    await api.delete(`/runs/${run.id}`)
    runs.value = runs.value.filter(item => item.id !== run.id)
    ElMessage.success('测速运行已删除')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  } finally {
    deletingRunId.value = null
  }
}

async function create() {
  if (!canCreate.value) {
    ElMessage.warning('请为场景所需的每种类型选择一个资源')
    return
  }
  creating.value = true
  try {
    const resource_ids = requiredTypes.value.map(type => resourceSelections[type] as number)
    const { data } = await api.post('/runs', { ...form, resource_ids })
    drawer.value = false
    ElMessage.success('测速运行已创建')
    router.push(`/runs/${data.id}`)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    creating.value = false
  }
}

watch(filters, () => {
  const query: Record<string, string> = {}
  if (filters.keyword) query.keyword = filters.keyword
  if (filters.business) query.business = filters.business
  if (filters.status) query.status = filters.status
  router.replace({ query })
}, { deep: true })

onMounted(async () => {
  const shouldOpen = route.query.create === '1'
  filters.keyword = String(route.query.keyword || '')
  filters.business = String(route.query.business || '')
  filters.status = String(route.query.status || '')
  await load()
  if (shouldOpen && auth.canOperate) open()
})
</script>

<template>
  <div class="page runs-page">
    <header class="page-header">
      <div>
        <span class="page-kicker">任务管理</span>
        <h1 class="page-title">测速运行</h1>
        <p class="muted">创建、排队并跟踪每一次独立测速执行</p>
      </div>
      <el-button v-if="auth.canOperate" type="primary" :icon="Plus" @click="open">创建运行</el-button>
    </header>

    <section class="filter-bar" aria-label="运行筛选">
      <el-input v-model="filters.keyword" clearable placeholder="搜索运行编号、方案或场景" :prefix-icon="Search" class="keyword-filter" />
      <el-select v-model="filters.business" clearable placeholder="全部业务" class="short-filter">
        <el-option v-for="(label, value) in businessText" :key="value" :label="label" :value="value" />
      </el-select>
      <el-select v-model="filters.status" clearable placeholder="全部状态" class="short-filter">
        <el-option label="正在处理" value="active" />
        <el-option label="等待人工处理" value="awaiting" />
        <el-option label="异常运行" value="failed" />
        <el-option label="已完成" value="completed" />
        <el-option label="已取消" value="cancelled" />
      </el-select>
      <span class="filter-count">{{ filteredRuns.length }} / {{ runs.length }} 条</span>
      <el-button text @click="resetFilters">重置</el-button>
      <el-button :icon="RefreshRight" :loading="loading" @click="load">刷新</el-button>
    </section>

    <el-alert v-if="loadError" class="load-error" title="运行列表加载失败" :description="loadError" type="error" show-icon :closable="false" />

    <section class="card table-panel">
      <el-table v-loading="loading" :data="filteredRuns" class="clickable" empty-text="没有符合条件的运行" @row-click="row => router.push(`/runs/${row.id}`)">
        <el-table-column label="运行编号" min-width="190">
          <template #default="scope">
            <div class="run-id-cell"><strong class="mono">{{ scope.row.run_number }}</strong><el-button text circle size="small" aria-label="复制运行编号" @click.stop="copyRunNumber(scope.row.run_number)"><el-icon><CopyDocument /></el-icon></el-button></div>
          </template>
        </el-table-column>
        <el-table-column label="业务" min-width="120"><template #default="scope">{{ businessText[scope.row.business_code] }}</template></el-table-column>
        <el-table-column label="方案 / 场景" min-width="210"><template #default="scope"><strong>{{ scope.row.config_snapshot?.plan?.name || '-' }}</strong><small class="muted">{{ scope.row.config_snapshot?.scenario?.name || '-' }}</small></template></el-table-column>
        <el-table-column label="状态" width="145"><template #default="scope"><StatusBadge :status="scope.row.status" show-raw /></template></el-table-column>
        <el-table-column label="进度" width="165"><template #default="scope"><el-progress :percentage="scope.row.progress" :stroke-width="6" /></template></el-table-column>
        <el-table-column label="创建时间" width="190"><template #default="scope"><span class="table-time">{{ formatBeijingDateTime(scope.row.created_at) }}</span></template></el-table-column>
        <el-table-column label="操作" width="124" fixed="right">
          <template #default="scope">
            <el-button link type="primary" @click.stop="router.push(`/runs/${scope.row.id}`)">查看</el-button>
            <el-tooltip v-if="auth.canOperate && deletableStatuses.has(scope.row.status)" content="删除运行" placement="top">
              <el-button
                link
                type="danger"
                :icon="Delete"
                :loading="deletingRunId === scope.row.id"
                aria-label="删除运行"
                @click.stop="removeRun(scope.row)"
              />
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!loading && !runs.length" class="empty-state"><div><strong>尚无测速运行</strong><span>选择方案、场景和资源后即可创建第一条运行。</span><br><el-button v-if="auth.canOperate" class="empty-action" type="primary" @click="open">创建运行</el-button></div></div>
    </section>

    <el-drawer v-model="drawer" title="创建测速运行" size="600px" destroy-on-close class="run-drawer">
      <div class="drawer-intro">
        <div><strong>配置一次独立的测速执行</strong><p>选择方案和场景后，确认本次运行锁定的执行资源。</p></div>
        <div class="drawer-flow" aria-label="创建步骤"><span>方案</span><i></i><span>场景</span><i></i><span>资源</span></div>
      </div>
      <el-form label-position="top" class="create-run-form">
        <div class="plan-grid">
          <el-form-item label="测速方案" required>
            <el-select v-model="form.plan_id" filterable style="width:100%" placeholder="请选择启用的方案" @change="handlePlanChange">
              <el-option v-for="plan in plans.filter(item => item.is_enabled)" :key="plan.id" :label="`${plan.name}  |  ${businessText[plan.business_code]}`" :value="plan.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="测速场景" required>
            <el-select v-model="form.scenario_id" filterable style="width:100%" placeholder="请选择场景" @change="handleScenarioChange">
              <el-option v-for="scenario in availableScenarios.filter(item => item.is_enabled)" :key="scenario.id" :label="`${scenario.name}  |  v${scenario.config_version}`" :value="scenario.id" />
            </el-select>
          </el-form-item>
        </div>

        <section v-if="selectedScenario" class="resource-section">
          <div class="resource-heading"><div><strong>执行资源</strong><span>已带入场景默认值，可替换同类型资源</span></div><el-tag effect="plain">{{ requiredTypes.length }} 类</el-tag></div>
          <div class="resource-list">
            <article v-for="type in requiredTypes" :key="type" class="resource-card" :class="{ empty: !resourceOptions(type).length }">
              <div class="resource-card-head">
                <strong>{{ resourceText[type] || type }}</strong>
                <span class="resource-card-count" :class="{ danger: !resourceOptions(type).length }">{{ resourceOptions(type).length }} 个可用</span>
              </div>
              <el-form-item :label="resourceText[type] || type" required>
                <el-select v-model="resourceSelections[type]" filterable style="width:100%" :placeholder="resourceOptions(type).length ? '请选择可用资源' : '暂无可用资源'">
                  <el-option v-for="resource in resourceOptions(type)" :key="resource.id" :label="resourceOptionLabel(resource)" :value="resource.id" :disabled="resource.health_status === 'unhealthy'" />
                </el-select>
                <p v-if="!resourceOptions(type).length" class="field-help danger">当前业务没有启用的{{ resourceText[type] || type }}</p>
              </el-form-item>
            </article>
          </div>
          <el-alert v-if="!requiredTypes.length" title="该场景尚未配置资源，请先编辑场景" type="warning" :closable="false" show-icon />
        </section>

        <section v-if="selectedScenario && requiredTypes.length" class="create-summary">
          <strong>运行摘要</strong>
          <div class="summary-tiles">
            <div><span>业务</span><strong>{{ businessText[selectedPlan?.business_code || ''] || '-' }}</strong></div>
            <div><span>方案 / 场景</span><strong>{{ selectedPlan?.name }} / {{ selectedScenario.name }}</strong></div>
            <div><span>将锁定资源</span><strong class="mono">{{ Object.values(resourceSelections).filter(Boolean).length }} / {{ requiredTypes.length }}</strong></div>
          </div>
        </section>
      </el-form>
      <template #footer><div class="drawer-footer"><el-button @click="drawer=false">取消</el-button><el-button type="primary" :loading="creating" :disabled="!canCreate" @click="create">创建并查看运行</el-button></div></template>
    </el-drawer>
  </div>
</template>

<style scoped>
.runs-page{max-width:1600px}.keyword-filter{width:min(360px,32vw)}.short-filter{width:160px}.filter-count{margin-left:auto;color:var(--ui-text-secondary);font-size:11px}.load-error{margin-bottom:14px}.table-panel{overflow:hidden}.clickable :deep(.el-table__row){cursor:pointer}.clickable strong,.clickable small{display:block}.clickable small{margin-top:3px}.run-id-cell{display:flex;align-items:center;gap:5px}.run-id-cell strong{font-size:12px}.run-id-cell .el-button{min-height:28px;opacity:0;transition:opacity var(--ui-transition)}.el-table__row:hover .run-id-cell .el-button,.run-id-cell:focus-within .el-button{opacity:1}.table-time{color:var(--ui-text-secondary);font-size:11px}.empty-action{margin-top:18px}.drawer-intro{margin:-4px 0 20px;padding:14px 15px;border-radius:8px;background:var(--ui-surface-subtle)}.drawer-intro strong{font-size:14px}.drawer-intro p{margin:4px 0 0;color:var(--ui-text-secondary);font-size:12px;line-height:1.6}.resource-section{margin-top:24px;padding-top:20px;border-top:1px solid var(--ui-border)}.resource-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:17px}.resource-heading strong,.resource-heading span{display:block}.resource-heading strong{font-size:14px}.resource-heading span{margin-top:4px;color:var(--ui-text-secondary);font-size:11px}.field-help{margin:5px 0 0;font-size:11px}.create-summary{margin-top:24px;padding:15px;border:1px solid var(--ui-border);border-radius:8px;background:#f8fbfb}.create-summary>strong{font-size:13px}.create-summary dl{display:grid;grid-template-columns:86px 1fr;gap:8px 12px;margin:12px 0 0;font-size:12px}.create-summary dt{color:var(--ui-text-secondary)}.create-summary dd{margin:0;color:var(--ui-text-primary)}.drawer-footer{display:flex;justify-content:flex-end;gap:8px}
:deep(.run-drawer .el-drawer__header){align-items:center;margin:0;padding:18px 22px 14px;border-bottom:1px solid var(--ui-border)}
:deep(.run-drawer .el-drawer__title){color:var(--ui-text-primary);font-size:18px;font-weight:750}
:deep(.run-drawer .el-drawer__body){padding:18px 22px 22px;overflow:auto;background:linear-gradient(180deg,#fff 0%,#f8fbfb 100%)}
:deep(.run-drawer .el-drawer__footer){padding:14px 22px;border-top:1px solid var(--ui-border);background:rgba(255,255,255,.96);backdrop-filter:blur(8px)}
.drawer-intro{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:14px;margin:0 0 16px;padding:12px 14px;border:1px solid #dce8e9;border-radius:8px;background:#f5f9f9}.drawer-intro strong{display:block;color:var(--ui-text-primary);font-size:14px;line-height:1.35}.drawer-intro p{margin:3px 0 0;color:var(--ui-text-secondary);font-size:12px;line-height:1.45}.drawer-flow{display:flex;align-items:center;gap:7px;padding:7px 8px;border:1px solid var(--ui-border);border-radius:7px;background:#fff;color:var(--ui-text-secondary);font-size:11px;font-weight:650;white-space:nowrap}.drawer-flow i{width:16px;height:1px;background:var(--ui-border-strong)}
.create-run-form :deep(.el-form-item){margin-bottom:0}.create-run-form :deep(.el-form-item__label){height:auto;margin-bottom:6px;color:var(--ui-text-primary);font-size:13px;font-weight:700;line-height:1.35}.create-run-form :deep(.el-select__wrapper){min-height:36px;border-radius:7px;transition:box-shadow var(--ui-transition),background-color var(--ui-transition)}.create-run-form :deep(.el-select__wrapper:hover){background:#fbfdfd}
.plan-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:18px}.resource-section{margin-top:0;padding-top:16px;border-top:1px solid var(--ui-border)}.resource-heading{align-items:center;margin-bottom:12px}.resource-heading strong{color:var(--ui-text-primary);font-size:15px}.resource-heading span{margin-top:3px;font-size:12px;line-height:1.35}.resource-list{display:grid;gap:9px}.resource-card{padding:10px 11px 11px;border:1px solid var(--ui-border);border-radius:8px;background:#fff;box-shadow:inset 0 1px 0 rgba(255,255,255,.72);transition:border-color var(--ui-transition),box-shadow var(--ui-transition),transform var(--ui-transition)}.resource-card:hover{border-color:var(--ui-border-strong);box-shadow:0 5px 16px rgba(19,43,48,.06);transform:translateY(-1px)}.resource-card.empty{background:#fbf7f7}.resource-card-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:7px}.resource-card-head strong{min-width:0;overflow:hidden;color:var(--ui-text-primary);font-size:13px;text-overflow:ellipsis;white-space:nowrap}.resource-card-head strong::after{color:var(--ui-danger);content:" *"}.resource-card-count{flex:0 0 auto;color:var(--ui-text-tertiary);font-size:11px;font-weight:650}.resource-card-count.danger,.field-help.danger{color:var(--ui-danger)}.resource-card :deep(.el-form-item__label){display:none}.resource-card :deep(.el-form-item__content){display:block}.field-help{margin:6px 0 0;font-size:11px;line-height:1.45}
.create-summary{margin-top:14px;padding:13px;border-color:#dbe7e8;background:#fff}.create-summary>strong{display:block;color:var(--ui-text-primary);font-size:14px}.summary-tiles{display:grid;grid-template-columns:.85fr 1.35fr .8fr;gap:9px;margin-top:10px}.summary-tiles>div{min-width:0;padding:10px 11px;border:1px solid var(--ui-border);border-radius:7px;background:#f7fafa}.summary-tiles span,.summary-tiles strong{display:block;min-width:0}.summary-tiles span{color:var(--ui-text-secondary);font-size:11px;font-weight:650}.summary-tiles strong{margin-top:5px;overflow:hidden;color:var(--ui-text-primary);font-size:13px;line-height:1.35;text-overflow:ellipsis;white-space:nowrap}.summary-tiles .mono{color:var(--ui-primary);font-size:17px;font-weight:750}
.drawer-footer{align-items:center;justify-content:flex-end;gap:10px}.drawer-footer :deep(.el-button){min-width:92px;margin-left:0}.drawer-footer :deep(.el-button--primary){min-width:150px}
@media(max-width:767px){.keyword-filter,.short-filter{width:100%}.filter-count{margin-left:0}.run-id-cell .el-button{opacity:1}:deep(.run-drawer){width:min(100vw,600px)!important}.drawer-intro,.plan-grid,.summary-tiles{grid-template-columns:1fr}.drawer-flow{width:100%;justify-content:center}.resource-card:hover{transform:none}.drawer-footer{display:grid;grid-template-columns:1fr 1fr}.drawer-footer :deep(.el-button),.drawer-footer :deep(.el-button--primary){width:100%;min-width:0}}
</style>
