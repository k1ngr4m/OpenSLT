<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { businessText, statusText, statusType, resourceText } from '@/utils/status'

const auth = useAuthStore()
const runs = ref<any[]>([])
const plans = ref<any[]>([])
const scenarios = ref<any[]>([])
const resources = ref<any[]>([])
const dialog = ref(false)
const form = reactive({ plan_id: 0, scenario_id: 0, timeout_minutes: 120 })
const resourceSelections = reactive<Record<string, number | null>>({})
const selectedPlan = computed(() => plans.value.find(plan => plan.id === form.plan_id))
const selectedScenario = computed(() => scenarios.value.find(scenario => scenario.id === form.scenario_id))
const availableScenarios = computed(() => scenarios.value.filter(scenario => scenario.plan_id === form.plan_id))
const requiredTypes = computed<string[]>(() => selectedScenario.value?.required_resource_types || [])
const canCreate = computed(() => Boolean(form.scenario_id && requiredTypes.value.length && requiredTypes.value.every(type => resourceSelections[type])))

async function load() {
  ;[runs.value, plans.value, scenarios.value, resources.value] = await Promise.all([
    api.get('/runs').then(response => response.data),
    api.get('/plans').then(response => response.data),
    api.get('/scenarios').then(response => response.data),
    api.get('/resources').then(response => response.data),
  ])
}

function resetResourceSelections() {
  for (const key of Object.keys(resourceSelections)) delete resourceSelections[key]
}

function open() {
  form.plan_id = plans.value.find(plan => plan.is_enabled)?.id || 0
  form.scenario_id = 0
  form.timeout_minutes = 120
  resetResourceSelections()
  dialog.value = true
}

function handlePlanChange() {
  form.scenario_id = 0
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

function resourceOptionLabel(resource: any) {
  const location = resource.resource_type === 'database'
    ? `${resource.database_host || ''}:${resource.database_port || ''}`
    : resource.host
  return `${resource.name}${location ? ` · ${location}` : ''}`
}

async function create() {
  if (!canCreate.value) {
    ElMessage.warning('请为场景所需的每种类型选择一个资源')
    return
  }
  try {
    const resource_ids = requiredTypes.value.map(type => resourceSelections[type] as number)
    const { data } = await api.post('/runs', { ...form, resource_ids })
    dialog.value = false
    ElMessage.success('运行已创建')
    location.href = `/runs/${data.id}`
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div><h1 class="page-title">测速运行</h1><p class="muted">创建、排队并跟踪每一次独立测速执行</p></div>
      <el-button v-if="auth.canOperate" type="primary" @click="open">创建运行</el-button>
    </div>
    <div class="card">
      <el-table :data="runs" class="clickable" @row-click="row => $router.push(`/runs/${row.id}`)">
        <el-table-column prop="run_number" label="运行编号" min-width="190" />
        <el-table-column label="业务" min-width="130"><template #default="scope">{{ businessText[scope.row.business_code] }}</template></el-table-column>
        <el-table-column label="方案 / 场景" min-width="200"><template #default="scope"><strong>{{ scope.row.config_snapshot?.plan?.name }}</strong><br><small class="muted">{{ scope.row.config_snapshot?.scenario?.name }}</small></template></el-table-column>
        <el-table-column label="状态" width="140"><template #default="scope"><el-tag :type="statusType(scope.row.status)" effect="plain">{{ statusText[scope.row.status] || scope.row.status }}</el-tag></template></el-table-column>
        <el-table-column label="进度" width="170"><template #default="scope"><el-progress :percentage="scope.row.progress" /></template></el-table-column>
        <el-table-column label="创建时间" width="180"><template #default="scope">{{ new Date(scope.row.created_at).toLocaleString() }}</template></el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialog" title="创建测速运行" width="620px">
      <el-form label-width="110px">
        <el-form-item label="测速方案"><el-select v-model="form.plan_id" style="width: 100%" @change="handlePlanChange"><el-option v-for="plan in plans.filter(item => item.is_enabled)" :key="plan.id" :label="`${plan.name} · ${businessText[plan.business_code]}`" :value="plan.id" /></el-select></el-form-item>
        <el-form-item label="测速场景"><el-select v-model="form.scenario_id" style="width: 100%" @change="handleScenarioChange"><el-option v-for="scenario in availableScenarios.filter(item => item.is_enabled)" :key="scenario.id" :label="`${scenario.name} · v${scenario.config_version}`" :value="scenario.id" /></el-select></el-form-item>
        <template v-if="selectedScenario">
          <div class="resource-heading"><strong>执行资源</strong><span class="muted">已带入场景默认值，可替换同类型资源</span></div>
          <el-form-item v-for="type in requiredTypes" :key="type" :label="resourceText[type] || type" required>
            <el-select v-model="resourceSelections[type]" filterable style="width: 100%" :placeholder="resourceOptions(type).length ? '请选择' : '暂无可用资源'">
              <el-option v-for="resource in resourceOptions(type)" :key="resource.id" :label="resourceOptionLabel(resource)" :value="resource.id" />
            </el-select>
          </el-form-item>
          <el-alert v-if="!requiredTypes.length" title="该场景尚未配置资源，请先编辑场景" type="warning" :closable="false" show-icon />
        </template>
        <el-form-item label="超时时间"><el-input-number v-model="form.timeout_minutes" :min="5" :max="1440" /> 分钟</el-form-item>
      </el-form>
      <template #footer><el-button @click="dialog = false">取消</el-button><el-button type="primary" :disabled="!canCreate" @click="create">创建</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.clickable :deep(.el-table__row){cursor:pointer}.resource-heading{display:flex;align-items:baseline;gap:10px;margin:2px 0 16px;padding-bottom:10px;border-bottom:1px solid #e5eaf0}.resource-heading .muted{font-size:12px}
</style>
