<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from '@/ui/elementPlusServices'
import { CaretRight, Delete, Edit, Folder, FolderOpened, MoreFilled, Plus } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { businessText, resourceText } from '@/utils/status'
import { formatBeijingDateTime } from '@/utils/time'

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
const scenariosByPlan = computed(() => {
  const groups = new Map<number, any[]>()
  for (const item of scenarios.value) {
    const group = groups.get(item.plan_id) || []
    group.push(item)
    groups.set(item.plan_id, group)
  }
  return groups
})
function togglePlan(id: number) {
  activePlans.value = activePlans.value.includes(id)
    ? activePlans.value.filter(value => value !== id)
    : [...activePlans.value, id]
}
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

        <div v-if="visiblePlans.length" class="plan-list-scroll" role="region" aria-label="方案与场景列表" tabindex="0">
          <table class="plan-list">
            <thead><tr><th scope="col">方案 / 场景名称</th><th scope="col">创建时间</th><th scope="col">更新时间</th><th scope="col">工作流状态</th><th scope="col" class="actions-heading">操作</th></tr></thead>
            <tbody v-for="p in visiblePlans" :key="p.id">
              <tr class="plan-group-row">
                <th colspan="5" scope="rowgroup">
                  <div class="plan-group-heading">
                    <button class="plan-group-toggle" type="button" :aria-expanded="activePlans.includes(p.id)" :aria-label="`${activePlans.includes(p.id) ? '收起' : '展开'}方案 ${p.name}`" @click="togglePlan(p.id)">
                      <el-icon class="plan-chevron" :class="{ open: activePlans.includes(p.id) }"><CaretRight /></el-icon>
                      <strong>{{ p.name }}</strong>
                      <span class="plan-business">{{ businessText[p.business_code] }}</span>
                      <span class="plan-version">v{{ p.config_version }}</span>
                      <span class="plan-count">{{ scenariosByPlan.get(p.id)?.length || 0 }} 个场景</span>
                    </button>
                    <div v-if="auth.canOperate" class="row-actions">
                      <el-button link type="primary" :icon="Plus" @click="openScenario(undefined, p.id)">新增场景</el-button>
                      <el-dropdown trigger="click">
                        <el-button text circle :icon="MoreFilled" :aria-label="`方案 ${p.name} 的更多操作`" />
                        <template #dropdown><el-dropdown-menu>
                          <el-dropdown-item @click="openPlan(p)">编辑方案</el-dropdown-item>
                          <el-dropdown-item @click="copyPlan(p)">复制方案</el-dropdown-item>
                          <el-dropdown-item divided class="danger" @click="removePlan(p)">删除方案</el-dropdown-item>
                        </el-dropdown-menu></template>
                      </el-dropdown>
                    </div>
                  </div>
                  <p v-if="p.description && activePlans.includes(p.id)" class="plan-description">{{ p.description }}</p>
                </th>
              </tr>
              <template v-if="activePlans.includes(p.id)">
                <tr v-for="item in scenariosByPlan.get(p.id) || []" :key="item.id" class="scenario-row">
                  <td class="scenario-name"><span class="scenario-branch" aria-hidden="true">↳</span><strong>{{ item.name }}</strong></td>
                  <td><time>{{ formatBeijingDateTime(item.created_at) }}</time></td>
                  <td><time>{{ formatBeijingDateTime(item.updated_at) }}</time></td>
                  <td><span class="workflow-state" :class="item.is_enabled ? 'success' : (item.published_workflow_version_id ? 'paused' : 'draft')"><i aria-hidden="true" />{{ item.is_enabled ? '已启用' : (item.published_workflow_version_id ? '已暂停' : '未启用') }}</span></td>
                  <td><div v-if="auth.canOperate" class="row-actions">
                    <el-button link type="primary" @click="openWorkflow(item.id)">工作流</el-button>
                    <el-dropdown trigger="click">
                      <el-button text circle :icon="MoreFilled" :aria-label="`场景 ${item.name} 的更多操作`" />
                      <template #dropdown><el-dropdown-menu>
                        <el-dropdown-item @click="openScenario(item)">基础信息</el-dropdown-item>
                        <el-dropdown-item @click="copyScenario(item)">复制场景</el-dropdown-item>
                        <el-dropdown-item divided class="danger" @click="removeScenario(item)">删除场景</el-dropdown-item>
                      </el-dropdown-menu></template>
                    </el-dropdown>
                  </div><span v-else class="muted">只读</span></td>
                </tr>
                <tr v-if="!scenariosByPlan.get(p.id)?.length" class="scenario-empty"><td colspan="5">暂无场景<span v-if="auth.canOperate">，点击“新增场景”开始配置。</span></td></tr>
              </template>
            </tbody>
          </table>
        </div>
        <div v-if="!listLoading && !listError && selectedDirectory && !visiblePlans.length" class="empty-state directory-empty"><div><strong>当前目录暂无方案</strong><span>先创建方案，再从方案中添加场景和工作流。</span><br><el-button v-if="auth.canOperate" type="primary" class="empty-action" @click="openPlan()">创建方案</el-button></div></div>
      </section>
    </div>

    <el-dialog v-model="directoryDialog" :title="directoryEdit ? '重命名目录' : '新建目录'" width="440px">
      <el-form @submit.prevent="saveDirectory" label-position="left" label-width="var(--ui-field-label-width)">
        <el-form-item label="目录名称" required><el-input v-model="directoryName" maxlength="128" autofocus /></el-form-item>
      </el-form>
      <template #footer><el-button @click="directoryDialog = false">取消</el-button><el-button type="primary" @click="saveDirectory">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="planDialog" :title="planEdit ? '编辑方案' : '新增方案'" width="640px" class="plan-config-dialog">
      <div class="dialog-intro">
        <div><strong>方案基础信息</strong><p>定义业务归属、版本和描述，场景会挂载在方案下统一管理。</p></div>
        <div class="dialog-flow" aria-label="方案配置项"><span>基础信息</span><i></i><span>业务</span><i></i><span>版本</span></div>
      </div>
      <el-form class="config-dialog-form" label-position="left" label-width="var(--ui-field-label-width)">
        <div class="plan-form-grid">
          <el-form-item v-if="planEdit" label="所属目录" class="wide"><el-select v-model="plan.directory_id" style="width: 100%"><el-option v-for="item in directories" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item>
          <el-form-item label="名称"><el-input v-model="plan.name" /></el-form-item>
          <el-form-item label="业务"><el-select v-model="plan.business_code" style="width:100%"><el-option v-for="(value, key) in businessText" :key="key" :label="value" :value="key" /></el-select></el-form-item>
          <el-form-item label="配置版本"><el-input v-model="plan.config_version" /></el-form-item>
          <el-form-item label="启用">
            <div class="enable-toggle"><span>{{ plan.is_enabled ? '启用后可创建场景和运行' : '停用后将从可选方案中隐藏' }}</span><el-switch v-model="plan.is_enabled" /></div>
          </el-form-item>
          <el-form-item label="描述" class="wide"><el-input v-model="plan.description" type="textarea" :rows="3" placeholder="补充方案用途、测评范围或特殊说明" /></el-form-item>
        </div>
      </el-form>
      <template #footer><div class="dialog-footer-actions"><el-button @click="planDialog = false">取消</el-button><el-button type="primary" @click="savePlan">保存</el-button></div></template>
    </el-dialog>

    <el-dialog v-model="scenarioDialog" :title="scenarioEdit ? '编辑场景' : '新增场景'" width="820px" class="scenario-config-dialog">
      <div class="dialog-intro">
        <div><strong>场景基础配置</strong><p>选择所属方案、命名场景，并按资源类型绑定默认执行资源。</p></div>
        <div class="dialog-flow" aria-label="场景配置项"><span>方案</span><i></i><span>场景</span><i></i><span>资源</span></div>
      </div>
      <el-form class="config-dialog-form" label-position="left" label-width="var(--ui-field-label-width)">
        <div class="scenario-form-grid">
          <el-form-item label="所属方案"><el-select v-model="scenario.plan_id" :disabled="!scenarioEdit" style="width: 100%" @change="handleScenarioPlanChange"><el-option v-for="item in visiblePlans" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item>
          <el-form-item label="场景名称"><el-input v-model="scenario.name" /></el-form-item>
        </div>
        <section class="scenario-resource-section">
          <div class="resource-heading"><strong>场景资源</strong><span class="muted">按需选择，每种类型最多一个</span></div>
          <div class="scenario-resource-grid">
            <article v-for="type in resourceTypes" :key="type" class="scenario-resource-card" :class="{ required: legacyRequiredTypes.includes(type), empty: !resourceOptions(type).length }">

              <el-form-item :label="resourceText[type] || type" :required="legacyRequiredTypes.includes(type)">
                <el-select v-model="resourceSelections[type]" clearable filterable style="width: 100%" :placeholder="resourceOptions(type).length ? '请选择' : '暂无可用资源'">
                  <el-option v-for="resource in resourceOptions(type)" :key="resource.id" :label="resourceOptionLabel(resource)" :value="resource.id" :disabled="!resource.is_enabled" />
                </el-select>
              </el-form-item>
              <p v-if="legacyRequiredTypes.includes(type)" class="resource-note">原有场景要求保留此类资源</p>
              <p v-else-if="!resourceOptions(type).length" class="resource-note danger">当前业务暂无可用{{ resourceText[type] || type }}</p>
            </article>
          </div>
        </section>
      </el-form>
      <template #footer><div class="dialog-footer-actions"><el-button @click="scenarioDialog = false">取消</el-button><el-button type="primary" @click="saveScenario">保存</el-button></div></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.plan-list-scroll { overflow-x: auto; border-top: 1px solid var(--ui-border-strong); border-bottom: 1px solid var(--ui-border); }
.plan-list { width: 100%; min-width: 850px; border-collapse: collapse; table-layout: fixed; text-align: left; }
.plan-list thead th { padding: 12px 16px; color: var(--ui-text-secondary); font-size: 12px; font-weight: 500; border-bottom: 1px solid var(--ui-border); }
.plan-list thead th:first-child { width: auto; }
.plan-list thead th:nth-child(2), .plan-list thead th:nth-child(3) { width: 168px; }
.plan-list thead th:nth-child(4) { width: 116px; }
.plan-list thead th:last-child { width: 122px; }
.plan-list .actions-heading { text-align: right; }
.plan-group-row > th { padding: 0 12px; background: var(--ui-surface-subtle); border-top: 1px solid var(--ui-border); font-weight: 400; }
.plan-group-heading { display: flex; align-items: center; gap: 16px; min-height: 58px; }
.plan-group-toggle { display: flex; align-items: center; gap: 12px; flex: 1; min-width: 0; padding: 14px 4px; border: 0; background: transparent; color: var(--ui-text-primary); text-align: left; cursor: pointer; font: inherit; }
.plan-group-toggle strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
.plan-chevron { flex: 0 0 auto; color: var(--ui-text-secondary); transition: transform var(--ui-transition); }
.plan-chevron.open { transform: rotate(90deg); }
.plan-business, .plan-version, .plan-count { flex: 0 0 auto; color: var(--ui-text-secondary); font-size: 12px; font-weight: 400; }
.plan-business { padding-left: 12px; border-left: 1px solid var(--ui-border-strong); }
.plan-count { margin-left: 4px; }
.plan-description { margin: -6px 0 12px 30px; color: var(--ui-text-secondary); font-size: 12px; font-weight: 400; overflow-wrap: anywhere; }
.scenario-row { background: var(--ui-surface); transition: background-color var(--ui-transition); }
.scenario-row:hover { background: var(--ui-primary-soft); }
.scenario-row td { padding: 10px 16px; border-top: 1px solid var(--ui-border); font-size: 13px; }
.scenario-name { overflow-wrap: anywhere; }
.scenario-name strong { font-weight: 500; }
.scenario-branch { margin-right: 12px; color: var(--ui-text-tertiary); }
.scenario-row time { color: var(--ui-text-secondary); font-size: 12px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.row-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex: 0 0 auto; }
.row-actions :deep(.el-button) { margin: 0; }
.workflow-state { display: inline-flex; align-items: center; gap: 7px; font-size: 12px; white-space: nowrap; }
.workflow-state i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.workflow-state.paused { color: var(--ui-text-secondary); }
.workflow-state.draft { color: var(--ui-warning); }
.scenario-empty td { padding: 20px 42px; color: var(--ui-text-secondary); font-size: 13px; background: var(--ui-surface); }

.plan-workspace{display:grid;grid-template-columns:240px minmax(0,1fr);min-height:420px;border-top:1px solid var(--ui-border)}
.directory-panel{padding:18px 16px 18px 0;border-right:1px solid var(--ui-border)}
.directory-heading,.directory-content-head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.directory-heading{height:36px;padding:0 6px 0 10px}.directory-heading strong{font-size:13px}
.directory-list{display:flex;flex-direction:column;gap:4px;margin-top:10px}
.directory-item{display:flex;min-height:42px;align-items:center;border-radius:6px;color:var(--ui-text-secondary);transition:background var(--ui-transition),color var(--ui-transition)}
.directory-item:hover{background:var(--ui-surface-subtle)}.directory-item.active{color:var(--ui-primary);background:var(--ui-primary-soft)}
.directory-select{display:grid;min-width:0;flex:1;grid-template-columns:18px minmax(0,1fr) auto;align-items:center;gap:8px;padding:10px;border:0;color:inherit;background:transparent;text-align:left;cursor:pointer}
.directory-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.directory-select small{color:var(--ui-text-tertiary)}
.default-label{margin-right:10px;color:var(--ui-text-tertiary);font-size:12px}.directory-actions{display:none;align-items:center;padding-right:4px}.directory-item:hover .directory-actions,.directory-item:focus-within .directory-actions{display:flex}.directory-actions :deep(.el-button){width:28px;height:28px;margin:0}
.directory-content{min-width:0;padding:18px 0 0 24px}.directory-content-head{min-height:42px;margin-bottom:16px}.directory-content-head h2{margin:0;font-size:18px;font-weight:650}.directory-content-head p{margin:3px 0 0;font-size:12px}
.plans{min-height:100px;border:0}
.tag{margin:2px 5px 2px 0}.resource-heading{display:flex;align-items:baseline;gap:12px;margin:2px 0 14px;padding-bottom:10px;border-bottom:1px solid var(--ui-border)}.resource-heading .muted{font-size:12px}.empty-action{margin-top:18px}.directory-empty{min-height:220px;border-top:1px solid var(--ui-border);background:transparent}
:deep(.plan-config-dialog),:deep(.scenario-config-dialog){border-radius:10px;overflow:hidden}.plan-config-dialog :deep(.el-dialog__header),.scenario-config-dialog :deep(.el-dialog__header){margin:0;padding:22px 28px 16px;border-bottom:1px solid var(--ui-border)}.plan-config-dialog :deep(.el-dialog__title),.scenario-config-dialog :deep(.el-dialog__title){color:var(--ui-text-primary);font-size:21px;font-weight:750}.plan-config-dialog :deep(.el-dialog__body),.scenario-config-dialog :deep(.el-dialog__body){padding:18px 28px 22px;background:linear-gradient(180deg,var(--ui-surface) 0%,var(--ui-surface-subtle) 100%)}.plan-config-dialog :deep(.el-dialog__footer),.scenario-config-dialog :deep(.el-dialog__footer){padding:14px 28px 18px;border-top:1px solid var(--ui-border);background:rgba(255,255,255,.96);}
.dialog-intro{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:14px;margin-bottom:16px;padding:12px 14px;border:1px solid #dce8e9;border-radius:8px;background:var(--ui-surface-subtle)}.dialog-intro strong,.dialog-intro p{display:block}.dialog-intro strong{color:var(--ui-text-primary);font-size:14px;line-height:1.35}.dialog-intro p{margin:3px 0 0;color:var(--ui-text-secondary);font-size:12px;line-height:1.45}.dialog-flow{display:flex;align-items:center;gap:7px;padding:7px 8px;border:1px solid var(--ui-border);border-radius:7px;background:var(--ui-surface);color:var(--ui-text-secondary);font-size:12px;font-weight:650;white-space:nowrap}.dialog-flow i{width:16px;height:1px;background:var(--ui-border-strong)}
.config-dialog-form :deep(.el-form-item){margin-bottom:0}.config-dialog-form :deep(.el-form-item__label){color:var(--ui-text-primary);font-size:13px;font-weight:700}.config-dialog-form :deep(.el-input__wrapper),.config-dialog-form :deep(.el-select__wrapper),.config-dialog-form :deep(.el-textarea__inner){border-radius:7px;transition:box-shadow var(--ui-transition),background-color var(--ui-transition)}.config-dialog-form :deep(.el-input__wrapper),.config-dialog-form :deep(.el-select__wrapper){min-height:38px}.config-dialog-form :deep(.el-input__wrapper:hover),.config-dialog-form :deep(.el-select__wrapper:hover),.config-dialog-form :deep(.el-textarea__inner:hover){background:var(--ui-surface-subtle)}
.plan-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px 16px}.plan-form-grid .wide{grid-column:1/-1}.enable-toggle{display:flex;min-height:38px;align-items:center;justify-content:space-between;gap:12px;padding:0 12px;border:1px solid var(--ui-border);border-radius:7px;background:var(--ui-surface)}.enable-toggle span{min-width:0;color:var(--ui-text-secondary);font-size:12px;line-height:1.35}.enable-toggle :deep(.el-switch){flex:0 0 auto}
.scenario-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px 16px;margin-bottom:16px}.scenario-resource-section{padding-top:14px;border-top:1px solid var(--ui-border)}.scenario-resource-section .resource-heading{align-items:center;margin:0 0 12px;padding:0;border:0}.scenario-resource-section .resource-heading strong{color:var(--ui-text-primary);font-size:15px}.scenario-resource-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.scenario-resource-card{position:relative;min-width:0;padding:10px 11px 11px;border:1px solid var(--ui-border);border-radius:8px;background:var(--ui-surface);box-shadow:inset 0 1px 0 rgba(255,255,255,.72);transition:border-color var(--ui-transition),box-shadow var(--ui-transition),transform var(--ui-transition)}.scenario-resource-card:hover{border-color:var(--ui-border-strong);box-shadow:0 5px 16px rgba(19,43,48,.06);transform:translateY(-1px)}.scenario-resource-card.required{border-color:#d4c399;background:#fffdf7}.scenario-resource-card.empty{background:#fbf7f7}.scenario-resource-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:7px}.scenario-resource-head strong{min-width:0;overflow:hidden;color:var(--ui-text-primary);font-size:13px;text-overflow:ellipsis;white-space:nowrap}.scenario-resource-card.required .scenario-resource-head strong::after{color:var(--ui-warning);content:" *"}.scenario-resource-head span{flex:0 0 auto;color:var(--ui-text-tertiary);font-size:12px;font-weight:650}.scenario-resource-head span.danger,.resource-note.danger{color:var(--ui-danger)}.scenario-resource-card :deep(.el-form-item__content){display:block}.resource-note{margin:6px 0 0;color:var(--ui-warning);font-size:12px;line-height:1.45}.dialog-footer-actions{display:flex;align-items:center;justify-content:flex-end;gap:10px}.dialog-footer-actions :deep(.el-button){min-width:88px;margin-left:0}.dialog-footer-actions :deep(.el-button--primary){min-width:104px}
@media(max-width:900px){.plan-workspace{display:block}.directory-panel{padding:12px 0;border-right:0;border-bottom:1px solid var(--ui-border)}.directory-list{overflow-x:auto;flex-direction:row;padding-bottom:4px}.directory-item{min-width:180px;background:var(--ui-surface)}.directory-content{padding:18px 0 0}.directory-actions{display:flex}}
@media(max-width:767px){.directory-content-head{align-items:flex-start}.plan-config-dialog :deep(.el-dialog),.scenario-config-dialog :deep(.el-dialog){width:calc(100vw - 24px)!important}.plan-config-dialog :deep(.el-dialog__header),.scenario-config-dialog :deep(.el-dialog__header){padding:18px 18px 14px}.plan-config-dialog :deep(.el-dialog__body),.scenario-config-dialog :deep(.el-dialog__body){padding:16px 18px 18px}.plan-config-dialog :deep(.el-dialog__footer),.scenario-config-dialog :deep(.el-dialog__footer){padding:12px 18px 16px}.dialog-intro,.plan-form-grid,.scenario-form-grid,.scenario-resource-grid{grid-template-columns:1fr}.dialog-flow{width:100%;justify-content:center}.scenario-resource-card:hover{transform:none}.dialog-footer-actions{display:grid;grid-template-columns:1fr 1fr}.dialog-footer-actions :deep(.el-button),.dialog-footer-actions :deep(.el-button--primary){width:100%;min-width:0}}

.plan-form-grid, .scenario-form-grid, .scenario-resource-grid { grid-template-columns: minmax(0, 1fr); }
.scenario-resource-card { padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
.scenario-resource-card:hover { transform: none; box-shadow: none; }
</style>
