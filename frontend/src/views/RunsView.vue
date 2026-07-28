<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search, RefreshRight, Plus, CopyDocument } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { ApiPlan, ApiResource, ApiScenario } from '@/types/api'
import type { RunDetail } from '@/types/run'
import { businessText, resourceText } from '@/utils/status'
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
const filters = reactive({ keyword: '', business: '', status: '' })
const form = reactive<{ plan_id: number; scenario_id: number | null }>({ plan_id: 0, scenario_id: null })
const resourceSelections = reactive<Record<string, number | null>>({})
const terminalStatuses = new Set(['completed', 'cancelled', 'execution_failed', 'parse_failed', 'precheck_failed', 'timed_out'])

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

function formatTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function copyRunNumber(value: string) {
  await navigator.clipboard.writeText(value)
  ElMessage.success(`已复制运行编号 ${value}`)
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
        <el-table-column label="创建时间" width="175"><template #default="scope"><span class="table-time">{{ formatTime(scope.row.created_at) }}</span></template></el-table-column>
        <el-table-column label="操作" width="86" fixed="right"><template #default="scope"><el-button link type="primary" @click.stop="router.push(`/runs/${scope.row.id}`)">查看</el-button></template></el-table-column>
      </el-table>
      <div v-if="!loading && !runs.length" class="empty-state"><div><strong>尚无测速运行</strong><span>选择方案、场景和资源后即可创建第一条运行。</span><br><el-button v-if="auth.canOperate" class="empty-action" type="primary" @click="open">创建运行</el-button></div></div>
    </section>

    <el-drawer v-model="drawer" title="创建测速运行" size="560px" destroy-on-close class="run-drawer">
      <div class="drawer-intro"><strong>配置一次独立的测速执行</strong><p>先选择方案和场景，再确认本次运行需要锁定的执行资源。</p></div>
      <el-form label-position="top">
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

        <section v-if="selectedScenario" class="resource-section">
          <div class="resource-heading"><div><strong>执行资源</strong><span>已带入场景默认值，可替换同类型资源</span></div><el-tag effect="plain">{{ requiredTypes.length }} 类</el-tag></div>
          <el-form-item v-for="type in requiredTypes" :key="type" :label="resourceText[type] || type" required>
            <el-select v-model="resourceSelections[type]" filterable style="width:100%" :placeholder="resourceOptions(type).length ? '请选择可用资源' : '暂无可用资源'">
              <el-option v-for="resource in resourceOptions(type)" :key="resource.id" :label="resourceOptionLabel(resource)" :value="resource.id" :disabled="resource.health_status === 'unhealthy'" />
            </el-select>
            <p v-if="!resourceOptions(type).length" class="field-help danger">当前业务没有启用的{{ resourceText[type] || type }}</p>
          </el-form-item>
          <el-alert v-if="!requiredTypes.length" title="该场景尚未配置资源，请先编辑场景" type="warning" :closable="false" show-icon />
        </section>

        <section v-if="selectedScenario && requiredTypes.length" class="create-summary">
          <strong>运行摘要</strong>
          <dl><dt>业务</dt><dd>{{ businessText[selectedPlan?.business_code || ''] || '-' }}</dd><dt>方案 / 场景</dt><dd>{{ selectedPlan?.name }} / {{ selectedScenario.name }}</dd><dt>将锁定资源</dt><dd>{{ Object.values(resourceSelections).filter(Boolean).length }} / {{ requiredTypes.length }}</dd></dl>
        </section>
      </el-form>
      <template #footer><div class="drawer-footer"><el-button @click="drawer=false">取消</el-button><el-button type="primary" :loading="creating" :disabled="!canCreate" @click="create">创建并查看运行</el-button></div></template>
    </el-drawer>
  </div>
</template>

<style scoped>
.runs-page{max-width:1600px}.keyword-filter{width:min(360px,32vw)}.short-filter{width:160px}.filter-count{margin-left:auto;color:var(--ui-text-secondary);font-size:11px}.load-error{margin-bottom:14px}.table-panel{overflow:hidden}.clickable :deep(.el-table__row){cursor:pointer}.clickable strong,.clickable small{display:block}.clickable small{margin-top:3px}.run-id-cell{display:flex;align-items:center;gap:5px}.run-id-cell strong{font-size:12px}.run-id-cell .el-button{min-height:28px;opacity:0;transition:opacity var(--ui-transition)}.el-table__row:hover .run-id-cell .el-button,.run-id-cell:focus-within .el-button{opacity:1}.table-time{color:var(--ui-text-secondary);font-size:11px}.empty-action{margin-top:18px}.drawer-intro{margin:-4px 0 20px;padding:14px 15px;border-radius:8px;background:var(--ui-surface-subtle)}.drawer-intro strong{font-size:14px}.drawer-intro p{margin:4px 0 0;color:var(--ui-text-secondary);font-size:12px;line-height:1.6}.resource-section{margin-top:24px;padding-top:20px;border-top:1px solid var(--ui-border)}.resource-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:17px}.resource-heading strong,.resource-heading span{display:block}.resource-heading strong{font-size:14px}.resource-heading span{margin-top:4px;color:var(--ui-text-secondary);font-size:11px}.field-help{margin:5px 0 0;font-size:11px}.create-summary{margin-top:24px;padding:15px;border:1px solid var(--ui-border);border-radius:8px;background:#f8fbfb}.create-summary>strong{font-size:13px}.create-summary dl{display:grid;grid-template-columns:86px 1fr;gap:8px 12px;margin:12px 0 0;font-size:12px}.create-summary dt{color:var(--ui-text-secondary)}.create-summary dd{margin:0;color:var(--ui-text-primary)}.drawer-footer{display:flex;justify-content:flex-end;gap:8px}
@media(max-width:767px){.keyword-filter,.short-filter{width:100%}.filter-count{margin-left:0}.run-id-cell .el-button{opacity:1}}
</style>
