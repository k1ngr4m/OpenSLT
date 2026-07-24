<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { businessText, resourceText } from '@/utils/status'

const auth = useAuthStore()
const router = useRouter()
const plans = ref<any[]>([])
const scenarios = ref<any[]>([])
const resources = ref<any[]>([])
const planDialog = ref(false)
const scenarioDialog = ref(false)
const planEdit = ref<number | null>(null)
const scenarioEdit = ref<number | null>(null)
const legacyRequiredTypes = ref<string[]>([])
const resourceTypes = Object.keys(resourceText)

const plan = reactive<any>({ name: '', business_code: 'fut_mm', description: '', default_resource_ids: [], config_version: '1.0', is_enabled: true })
const scenario = reactive<any>({ plan_id: 0, name: '', scenario_type: 'order', config_version: '1.0', default_resource_ids: [], required_resource_types: [], expected_artifacts: [], is_enabled: true })
const resourceSelections = reactive<Record<string, number | null>>({})
const selectedScenarioPlan = computed(() => plans.value.find(item => item.id === scenario.plan_id))

function resetResourceSelections() {
  for (const type of resourceTypes) resourceSelections[type] = null
}

async function load() {
  ;[plans.value, scenarios.value, resources.value] = await Promise.all([
    api.get('/plans').then(response => response.data),
    api.get('/scenarios').then(response => response.data),
    api.get('/resources').then(response => response.data),
  ])
}

function openPlan(row?: any) {
  Object.assign(plan, { name: '', business_code: 'fut_mm', description: '', default_resource_ids: [], config_version: '1.0', is_enabled: true }, row || {})
  planEdit.value = row?.id || null
  planDialog.value = true
}

function openScenario(row?: any, planId?: number) {
  Object.assign(scenario, {
    plan_id: planId || plans.value[0]?.id || 0,
    name: '',
    scenario_type: 'order',
    config_version: '1.0',
    default_resource_ids: [],
    required_resource_types: [],
    expected_artifacts: [],
    is_enabled: true,
  }, row || {})
  resetResourceSelections()
  for (const resourceId of scenario.default_resource_ids || []) {
    const resource = resources.value.find(item => item.id === resourceId)
    if (resource && resourceTypes.includes(resource.resource_type)) resourceSelections[resource.resource_type] = resource.id
  }
  legacyRequiredTypes.value = scenario.default_resource_ids?.length ? [] : [...(scenario.required_resource_types || [])]
  scenarioEdit.value = row?.id || null
  scenarioDialog.value = true
}

function handleScenarioPlanChange() {
  resetResourceSelections()
  legacyRequiredTypes.value = []
}

function resourceOptions(type: string) {
  const selectedId = resourceSelections[type]
  return resources.value.filter(resource =>
    resource.resource_type === type
    && resource.business_code === selectedScenarioPlan.value?.business_code
    && (resource.is_enabled || resource.id === selectedId),
  )
}

function resourceOptionLabel(resource: any) {
  const location = resource.resource_type === 'database'
    ? `${resource.database_host || ''}:${resource.database_port || ''}`
    : resource.host
  return `${resource.name}${location ? ` · ${location}` : ''}${resource.is_enabled ? '' : ' · 已停用'}`
}

function scenarioResourceLabels(row: any) {
  if (row.default_resource_ids?.length) {
    return row.default_resource_ids.map((id: number) => {
      const resource = resources.value.find(item => item.id === id)
      return resource ? `${resourceText[resource.resource_type] || resource.resource_type} · ${resource.name}` : `资源 #${id}`
    })
  }
  return (row.required_resource_types || []).map((type: string) => `${resourceText[type] || type} · 待绑定`)
}

async function savePlan() {
  try {
    planEdit.value ? await api.put(`/plans/${planEdit.value}`, plan) : await api.post('/plans', plan)
    planDialog.value = false
    ElMessage.success('方案已保存')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function saveScenario() {
  const selectedIds = resourceTypes.map(type => resourceSelections[type]).filter((id): id is number => id != null)
  if (!selectedIds.length) {
    ElMessage.warning('请至少选择一个场景资源')
    return
  }
  const missingLegacyTypes = legacyRequiredTypes.value.filter(type => !resourceSelections[type])
  if (missingLegacyTypes.length) {
    ElMessage.warning(`请为原有所需类型补选资源：${missingLegacyTypes.map(type => resourceText[type] || type).join('、')}`)
    return
  }
  const unavailable = selectedIds.some(id => !resources.value.find(resource => resource.id === id)?.is_enabled)
  if (unavailable) {
    ElMessage.warning('已停用的资源不能用于场景，请先替换或清除')
    return
  }
  try {
    const data = {
      ...scenario,
      default_resource_ids: selectedIds,
      required_resource_types: resourceTypes.filter(type => resourceSelections[type]),
    }
    const response = scenarioEdit.value ? await api.put(`/scenarios/${scenarioEdit.value}`, data) : await api.post('/scenarios', data)
    scenarioDialog.value = false
    ElMessage.success('场景已保存')
    await load()
    if (!scenarioEdit.value) await router.push(`/plans/scenarios/${response.data.id}/workflow`)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function copyPlan(row: any) {
  await api.post(`/plans/${row.id}/copy`)
  ElMessage.success('方案及场景已复制')
  await load()
}

async function copyScenario(row: any) {
  await api.post(`/scenarios/${row.id}/copy`)
  ElMessage.success('场景已复制')
  await load()
}

async function removePlan(row: any) {
  try {
    await ElMessageBox.confirm(`确定删除方案“${row.name}”及其全部场景？`, '删除方案', { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' })
    await api.delete(`/plans/${row.id}`)
    ElMessage.success('方案已删除')
    await load()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  }
}

async function removeScenario(row: any) {
  try {
    await ElMessageBox.confirm(`确定删除场景“${row.name}”？`, '删除场景', { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' })
    await api.delete(`/scenarios/${row.id}`)
    ElMessage.success('场景已删除')
    await load()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div><h1 class="page-title">方案与场景</h1><p class="muted">版本化配置测速流程，历史运行保存独立快照</p></div>
      <el-button v-if="auth.canOperate" type="primary" @click="openPlan()">新增方案</el-button>
    </div>
    <el-collapse class="plans">
      <el-collapse-item v-for="p in plans" :key="p.id" :name="p.id">
        <template #title>
          <div class="plan-head">
            <div><strong>{{ p.name }}</strong><el-tag size="small" effect="plain">{{ businessText[p.business_code] }}</el-tag><span class="muted">v{{ p.config_version }}</span></div>
            <div v-if="auth.canOperate" @click.stop><el-button link type="primary" @click="openScenario(undefined, p.id)">新增场景</el-button><el-button link @click="openPlan(p)">编辑</el-button><el-button link @click="copyPlan(p)">复制</el-button><el-tooltip content="删除方案" placement="top"><el-button link type="danger" :icon="Delete" aria-label="删除方案" @click="removePlan(p)" /></el-tooltip></div>
          </div>
        </template>
        <p class="muted">{{ p.description || '暂无描述' }}</p>
        <el-table :data="scenarios.filter(item => item.plan_id === p.id)" size="small">
          <el-table-column prop="name" label="场景名称" />
          <el-table-column prop="scenario_type" label="场景类型" />
          <el-table-column prop="config_version" label="配置版本" />
          <el-table-column label="场景资源" min-width="260">
            <template #default="scope"><el-tag v-for="label in scenarioResourceLabels(scope.row)" :key="label" size="small" class="tag">{{ label }}</el-tag></template>
          </el-table-column>
          <el-table-column label="工作流状态" width="120"><template #default="scope"><el-tag size="small" :type="scope.row.workflow_status === 'published' ? 'success' : 'warning'">{{ scope.row.workflow_status === 'published' ? '已发布' : '草稿' }}</el-tag></template></el-table-column>
          <el-table-column v-if="auth.canOperate" width="220"><template #default="scope"><el-button link type="primary" @click="router.push(`/plans/scenarios/${scope.row.id}/workflow`)">工作流</el-button><el-button link @click="openScenario(scope.row)">基础信息</el-button><el-button link @click="copyScenario(scope.row)">复制</el-button><el-tooltip content="删除场景" placement="top"><el-button link type="danger" :icon="Delete" aria-label="删除场景" @click="removeScenario(scope.row)" /></el-tooltip></template></el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>

    <el-dialog v-model="planDialog" :title="planEdit ? '编辑方案' : '新增方案'" width="600px">
      <el-form label-width="90px">
        <el-form-item label="名称"><el-input v-model="plan.name" /></el-form-item>
        <el-form-item label="业务"><el-select v-model="plan.business_code"><el-option v-for="(value, key) in businessText" :key="key" :label="value" :value="key" /></el-select></el-form-item>
        <el-form-item label="配置版本"><el-input v-model="plan.config_version" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="plan.description" type="textarea" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="plan.is_enabled" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="planDialog = false">取消</el-button><el-button type="primary" @click="savePlan">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="scenarioDialog" :title="scenarioEdit ? '编辑场景' : '新增场景'" width="760px">
      <el-form label-width="110px">
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="所属方案"><el-select v-model="scenario.plan_id" style="width: 100%" @change="handleScenarioPlanChange"><el-option v-for="item in plans" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="场景名称"><el-input v-model="scenario.name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="场景类型"><el-input v-model="scenario.scenario_type" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="配置版本"><el-input v-model="scenario.config_version" /></el-form-item></el-col>
          <el-col :span="24"><div class="resource-heading"><strong>场景资源</strong><span class="muted">按需选择，每种类型最多一个</span></div></el-col>
          <el-col v-for="type in resourceTypes" :key="type" :span="12">
            <el-form-item :label="resourceText[type] || type" :required="legacyRequiredTypes.includes(type)">
              <el-select v-model="resourceSelections[type]" clearable filterable style="width: 100%" :placeholder="resourceOptions(type).length ? '请选择' : '暂无可用资源'">
                <el-option v-for="resource in resourceOptions(type)" :key="resource.id" :label="resourceOptionLabel(resource)" :value="resource.id" :disabled="!resource.is_enabled" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="24"><el-form-item label="启用"><el-switch v-model="scenario.is_enabled" /></el-form-item></el-col>
        </el-row>
      </el-form>
      <template #footer><el-button @click="scenarioDialog = false">取消</el-button><el-button type="primary" @click="saveScenario">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.plans{border:0}.plans :deep(.el-collapse-item){background:#fff;border:1px solid #e5eaf0;border-radius:8px;margin-bottom:12px;padding:0 18px}.plan-head{width:100%;display:flex;align-items:center;justify-content:space-between;padding-right:16px}.plan-head strong{font-size:16px;margin-right:12px}.plan-head .el-tag{margin-right:10px}.tag{margin:2px 5px 2px 0}.resource-heading{display:flex;align-items:baseline;gap:12px;margin:2px 0 14px;padding-bottom:10px;border-bottom:1px solid #e5eaf0}.resource-heading .muted{font-size:12px}
</style>
