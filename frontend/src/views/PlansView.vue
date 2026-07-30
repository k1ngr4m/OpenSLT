<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Edit, Folder, FolderOpened, Plus } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { businessText, resourceText } from '@/utils/status'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const directories = ref<any[]>([])
const plans = ref<any[]>([])
const scenarios = ref<any[]>([])
const resources = ref<any[]>([])
const selectedDirectoryId = ref<number | null>(null)
const activePlans = ref<number[]>([])
const listLoading = ref(false)
const listError = ref('')
const directoryDialog = ref(false)
const directoryEdit = ref<number | null>(null)
const directoryName = ref('')
const planDialog = ref(false)
const scenarioDialog = ref(false)
const planEdit = ref<number | null>(null)
const scenarioEdit = ref<number | null>(null)
const legacyRequiredTypes = ref<string[]>([])
const resourceTypes = Object.keys(resourceText)

const plan = reactive<any>({ directory_id: null, name: '', business_code: 'fut_mm', description: '', default_resource_ids: [], config_version: '1.0', is_enabled: true })
const scenario = reactive<any>({ plan_id: 0, name: '', scenario_type: 'order', config_version: '1.0', default_resource_ids: [], required_resource_types: [], expected_artifacts: [], is_enabled: true })
const resourceSelections = reactive<Record<string, number | null>>({})
const selectedDirectory = computed(() => directories.value.find(item => item.id === selectedDirectoryId.value) || null)
const visiblePlans = computed(() => plans.value.filter(item => item.directory_id === selectedDirectoryId.value))
const selectedScenarioPlan = computed(() => plans.value.find(item => item.id === scenario.plan_id))

function resetResourceSelections() {
  for (const type of resourceTypes) resourceSelections[type] = null
}

function defaultDirectoryId() {
  return directories.value.find(item => item.is_default)?.id || directories.value[0]?.id || null
}

async function selectDirectory(directoryId: number, updateUrl = true) {
  if (!directories.value.some(item => item.id === directoryId)) return
  selectedDirectoryId.value = directoryId
  const firstPlan = plans.value.find(item => item.directory_id === directoryId)
  activePlans.value = firstPlan ? [firstPlan.id] : []
  if (updateUrl && String(route.query.directory_id || '') !== String(directoryId)) {
    await router.replace({ path: '/plans', query: { directory_id: String(directoryId) } })
  }
}

async function load(preferredDirectoryId?: number | null) {
  listLoading.value = true
  listError.value = ''
  try {
    ;[directories.value, plans.value, scenarios.value, resources.value] = await Promise.all([
      api.get('/plan-directories').then(response => response.data),
      api.get('/plans').then(response => response.data),
      api.get('/scenarios').then(response => response.data),
      api.get('/resources').then(response => response.data),
    ])
    const queryId = Number(route.query.directory_id)
    const requestedId = preferredDirectoryId || (Number.isInteger(queryId) && queryId > 0 ? queryId : null)
    const nextId = directories.value.some(item => item.id === requestedId)
      ? requestedId
      : (directories.value.some(item => item.id === selectedDirectoryId.value) ? selectedDirectoryId.value : defaultDirectoryId())
    if (nextId) await selectDirectory(nextId)
  } catch (error) {
    listError.value = errorMessage(error)
  } finally {
    listLoading.value = false
  }
}

function directoryPlanCount(directoryId: number) {
  return plans.value.filter(item => item.directory_id === directoryId).length
}

function openDirectory(row?: any) {
  directoryEdit.value = row?.id || null
  directoryName.value = row?.name || ''
  directoryDialog.value = true
}

async function saveDirectory() {
  try {
    const response = directoryEdit.value
      ? await api.put(`/plan-directories/${directoryEdit.value}`, { name: directoryName.value })
      : await api.post('/plan-directories', { name: directoryName.value })
    directoryDialog.value = false
    ElMessage.success('目录已保存')
    await load(response.data.id)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function removeDirectory(row: any) {
  try {
    await ElMessageBox.confirm(`确定删除目录“${row.name}”？`, '删除目录', { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' })
    await api.delete(`/plan-directories/${row.id}`)
    ElMessage.success('目录已删除')
    selectedDirectoryId.value = defaultDirectoryId()
    await load(defaultDirectoryId())
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  }
}

function openPlan(row?: any) {
  Object.assign(plan, { directory_id: selectedDirectoryId.value, name: '', business_code: 'fut_mm', description: '', default_resource_ids: [], config_version: '1.0', is_enabled: true }, row || {})
  planEdit.value = row?.id || null
  planDialog.value = true
}

function openScenario(row?: any, planId?: number) {
  const targetPlanId = row?.plan_id || planId
  if (!targetPlanId) return
  Object.assign(scenario, {
    plan_id: targetPlanId,
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
  const data = {
    directory_id: plan.directory_id,
    name: plan.name,
    business_code: plan.business_code,
    description: plan.description || '',
    default_resource_ids: plan.default_resource_ids || [],
    config_version: plan.config_version || '1.0',
    is_enabled: plan.is_enabled,
  }
  try {
    const response = planEdit.value ? await api.put(`/plans/${planEdit.value}`, data) : await api.post('/plans', data)
    planDialog.value = false
    ElMessage.success('方案已保存')
    await load(response.data.directory_id)
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
      plan_id: scenario.plan_id,
      name: scenario.name,
      scenario_type: scenario.scenario_type || 'order',
      config_version: scenario.config_version || '1.0',
      expected_artifacts: scenario.expected_artifacts || [],
      default_resource_ids: selectedIds,
      required_resource_types: resourceTypes.filter(type => resourceSelections[type]),
    }
    const response = scenarioEdit.value ? await api.put(`/scenarios/${scenarioEdit.value}`, data) : await api.post('/scenarios', data)
    scenarioDialog.value = false
    ElMessage.success('场景已保存')
    await load(selectedDirectoryId.value)
    if (!scenarioEdit.value) {
      await router.push({ path: `/plans/scenarios/${response.data.id}/workflow`, query: { directory_id: String(selectedDirectoryId.value) } })
    }
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function copyPlan(row: any) {
  try {
    await api.post(`/plans/${row.id}/copy`)
    ElMessage.success('方案及场景已复制')
    await load(selectedDirectoryId.value)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function copyScenario(row: any) {
  try {
    await api.post(`/scenarios/${row.id}/copy`)
    ElMessage.success('场景已复制')
    await load(selectedDirectoryId.value)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function removePlan(row: any) {
  try {
    await ElMessageBox.confirm(`确定删除方案“${row.name}”及其全部场景？`, '删除方案', { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' })
    await api.delete(`/plans/${row.id}`)
    ElMessage.success('方案已删除')
    await load(selectedDirectoryId.value)
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
    await load(selectedDirectoryId.value)
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  }
}

function openWorkflow(scenarioId: number) {
  return router.push({ path: `/plans/scenarios/${scenarioId}/workflow`, query: { directory_id: String(selectedDirectoryId.value) } })
}

watch(() => route.query.directory_id, value => {
  const directoryId = Number(value)
  if (Number.isInteger(directoryId) && directoryId > 0 && directoryId !== selectedDirectoryId.value) {
    void selectDirectory(directoryId, false)
  }
})

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div><span class="page-kicker">流程配置</span><h1 class="page-title">方案与场景</h1></div>
    </div>
    <el-alert v-if="listError" type="error" :closable="false" show-icon class="load-alert">
      <template #title><span>方案数据加载失败：{{ listError }}</span><el-button link type="danger" @click="load()">重试</el-button></template>
    </el-alert>

    <div v-loading="listLoading" class="plan-workspace">
      <aside class="directory-panel" aria-label="方案目录">
        <div class="directory-heading">
          <strong>目录</strong>
          <el-tooltip v-if="auth.canOperate" content="新建目录" placement="top">
            <el-button text circle :icon="Plus" aria-label="新建目录" @click="openDirectory()" />
          </el-tooltip>
        </div>
        <div class="directory-list">
          <div v-for="item in directories" :key="item.id" class="directory-item" :class="{ active: item.id === selectedDirectoryId }">
            <button type="button" class="directory-select" @click="selectDirectory(item.id)">
              <el-icon><component :is="item.id === selectedDirectoryId ? FolderOpened : Folder" /></el-icon>
              <span class="directory-name">{{ item.name }}</span>
              <small>{{ directoryPlanCount(item.id) }}</small>
            </button>
            <span v-if="item.is_default" class="default-label">默认</span>
            <div v-else-if="auth.canOperate" class="directory-actions">
              <el-tooltip content="重命名目录" placement="top"><el-button text circle :icon="Edit" aria-label="重命名目录" @click="openDirectory(item)" /></el-tooltip>
              <el-tooltip content="删除目录" placement="top"><el-button text circle type="danger" :icon="Delete" aria-label="删除目录" @click="removeDirectory(item)" /></el-tooltip>
            </div>
          </div>
        </div>
      </aside>

      <section class="directory-content">
        <div class="directory-content-head">
          <div><h2>{{ selectedDirectory?.name || '方案目录' }}</h2><p class="muted">{{ visiblePlans.length }} 个方案</p></div>
          <el-button v-if="auth.canOperate && selectedDirectory" type="primary" :icon="Plus" @click="openPlan()">新增方案</el-button>
        </div>

        <el-collapse v-model="activePlans" class="plans">
          <el-collapse-item v-for="p in visiblePlans" :key="p.id" :name="p.id">
            <template #title>
              <div class="plan-head">
                <div><strong>{{ p.name }}</strong><el-tag size="small" effect="plain">{{ businessText[p.business_code] }}</el-tag><span class="muted">v{{ p.config_version }}</span></div>
                <div v-if="auth.canOperate" @click.stop><el-button link type="primary" @click="openScenario(undefined, p.id)">新增场景</el-button><el-button link @click="openPlan(p)">编辑</el-button><el-button link @click="copyPlan(p)">复制</el-button><el-tooltip content="删除方案" placement="top"><el-button link type="danger" :icon="Delete" aria-label="删除方案" @click="removePlan(p)" /></el-tooltip></div>
              </div>
            </template>
            <p class="muted">{{ p.description || '暂无描述' }}</p>
            <el-table :data="scenarios.filter(item => item.plan_id === p.id)" size="small">
              <el-table-column prop="name" label="场景名称" />
              <el-table-column label="场景资源" min-width="260">
                <template #default="scope"><el-tag v-for="label in scenarioResourceLabels(scope.row)" :key="label" size="small" class="tag">{{ label }}</el-tag></template>
              </el-table-column>
              <el-table-column label="工作流状态" width="120"><template #default="scope"><el-tag size="small" :type="scope.row.is_enabled ? 'success' : (scope.row.published_workflow_version_id ? 'info' : 'warning')">{{ scope.row.is_enabled ? '已启用' : (scope.row.published_workflow_version_id ? '已暂停' : '未启用') }}</el-tag></template></el-table-column>
              <el-table-column v-if="auth.canOperate" width="220"><template #default="scope"><el-button link type="primary" @click="openWorkflow(scope.row.id)">工作流</el-button><el-button link @click="openScenario(scope.row)">基础信息</el-button><el-button link @click="copyScenario(scope.row)">复制</el-button><el-tooltip content="删除场景" placement="top"><el-button link type="danger" :icon="Delete" aria-label="删除场景" @click="removeScenario(scope.row)" /></el-tooltip></template></el-table-column>
            </el-table>
          </el-collapse-item>
        </el-collapse>
        <div v-if="!listLoading && !listError && selectedDirectory && !visiblePlans.length" class="empty-state directory-empty"><div><strong>当前目录暂无方案</strong><span>先创建方案，再从方案中添加场景和工作流。</span><br><el-button v-if="auth.canOperate" type="primary" class="empty-action" @click="openPlan()">创建方案</el-button></div></div>
      </section>
    </div>

    <el-dialog v-model="directoryDialog" :title="directoryEdit ? '重命名目录' : '新建目录'" width="440px">
      <el-form label-width="80px" @submit.prevent="saveDirectory">
        <el-form-item label="目录名称" required><el-input v-model="directoryName" maxlength="128" autofocus /></el-form-item>
      </el-form>
      <template #footer><el-button @click="directoryDialog = false">取消</el-button><el-button type="primary" @click="saveDirectory">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="planDialog" :title="planEdit ? '编辑方案' : '新增方案'" width="600px">
      <el-form label-width="90px">
        <el-form-item v-if="planEdit" label="所属目录"><el-select v-model="plan.directory_id" style="width: 100%"><el-option v-for="item in directories" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item>
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
          <el-col :span="12"><el-form-item label="所属方案"><el-select v-model="scenario.plan_id" :disabled="!scenarioEdit" style="width: 100%" @change="handleScenarioPlanChange"><el-option v-for="item in visiblePlans" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="场景名称"><el-input v-model="scenario.name" /></el-form-item></el-col>
          <el-col :span="24"><div class="resource-heading"><strong>场景资源</strong><span class="muted">按需选择，每种类型最多一个</span></div></el-col>
          <el-col v-for="type in resourceTypes" :key="type" :span="12">
            <el-form-item :label="resourceText[type] || type" :required="legacyRequiredTypes.includes(type)">
              <el-select v-model="resourceSelections[type]" clearable filterable style="width: 100%" :placeholder="resourceOptions(type).length ? '请选择' : '暂无可用资源'">
                <el-option v-for="resource in resourceOptions(type)" :key="resource.id" :label="resourceOptionLabel(resource)" :value="resource.id" :disabled="!resource.is_enabled" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer><el-button @click="scenarioDialog = false">取消</el-button><el-button type="primary" @click="saveScenario">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.plan-workspace{display:grid;grid-template-columns:240px minmax(0,1fr);min-height:420px;border-top:1px solid var(--ui-border)}
.directory-panel{padding:18px 16px 18px 0;border-right:1px solid var(--ui-border)}
.directory-heading,.directory-content-head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.directory-heading{height:36px;padding:0 6px 0 10px}.directory-heading strong{font-size:13px}
.directory-list{display:flex;flex-direction:column;gap:4px;margin-top:10px}
.directory-item{display:flex;min-height:42px;align-items:center;border-radius:6px;color:var(--ui-text-secondary);transition:background var(--ui-transition),color var(--ui-transition)}
.directory-item:hover{background:var(--ui-surface-subtle)}.directory-item.active{color:var(--ui-primary);background:var(--ui-primary-soft)}
.directory-select{display:grid;min-width:0;flex:1;grid-template-columns:18px minmax(0,1fr) auto;align-items:center;gap:8px;padding:10px;border:0;color:inherit;background:transparent;text-align:left;cursor:pointer}
.directory-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.directory-select small{color:var(--ui-text-tertiary)}
.default-label{margin-right:10px;color:var(--ui-text-tertiary);font-size:11px}.directory-actions{display:none;align-items:center;padding-right:4px}.directory-item:hover .directory-actions,.directory-item:focus-within .directory-actions{display:flex}.directory-actions :deep(.el-button){width:28px;height:28px;margin:0}
.directory-content{min-width:0;padding:18px 0 0 24px}.directory-content-head{min-height:42px;margin-bottom:16px}.directory-content-head h2{margin:0;font-size:18px;font-weight:650}.directory-content-head p{margin:3px 0 0;font-size:12px}
.plans{min-height:100px;border:0}.plans :deep(.el-collapse-item){overflow:hidden;margin-bottom:12px;padding:0 18px;border:1px solid var(--ui-border);border-radius:var(--ui-radius-panel);background:#fff;transition:border-color var(--ui-transition),box-shadow var(--ui-transition)}.plans :deep(.el-collapse-item:hover){border-color:var(--ui-border-strong)}.plans :deep(.el-collapse-item__header){height:58px;border-bottom:0}.plans :deep(.el-collapse-item__wrap){border-bottom:0}.plans :deep(.el-collapse-item__content){padding-bottom:18px}
.plan-head{display:flex;width:100%;align-items:center;justify-content:space-between;gap:16px;padding-right:16px}.plan-head>div:first-child{display:flex;align-items:center;min-width:0}.plan-head strong{overflow:hidden;margin-right:12px;font-size:15px;text-overflow:ellipsis;white-space:nowrap}.plan-head .el-tag{margin-right:10px}.plan-head .muted{font-size:11px}
.tag{margin:2px 5px 2px 0}.resource-heading{display:flex;align-items:baseline;gap:12px;margin:2px 0 14px;padding-bottom:10px;border-bottom:1px solid var(--ui-border)}.resource-heading .muted{font-size:12px}.empty-action{margin-top:18px}.directory-empty{min-height:260px;border:1px dashed var(--ui-border);border-radius:var(--ui-radius-panel);background:transparent}
@media(max-width:900px){.plan-workspace{display:block}.directory-panel{padding:12px 0;border-right:0;border-bottom:1px solid var(--ui-border)}.directory-list{overflow-x:auto;flex-direction:row;padding-bottom:4px}.directory-item{min-width:180px;background:var(--ui-surface)}.directory-content{padding:18px 0 0}.directory-actions{display:flex}}
@media(max-width:767px){.directory-content-head{align-items:flex-start}.plans :deep(.el-collapse-item){padding-inline:12px}.plan-head{align-items:flex-start;flex-direction:column;padding:10px 14px 10px 0}.plan-head>div:last-child{display:flex;flex-wrap:wrap}.plans :deep(.el-collapse-item__header){height:auto;min-height:58px}}
</style>
