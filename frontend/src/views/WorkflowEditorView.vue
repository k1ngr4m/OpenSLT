<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Bottom, Check, Connection, Delete, Document, Plus, Promotion, Refresh, Search, Tickets, Top } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { resourceText } from '@/utils/status'

type WorkflowNode = { id?: number; node_key: string; position: number; node_type: string; name: string; config: Record<string, any> }

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const scenarioId = Number(route.params.id)
const loading = ref(true)
const saving = ref(false)
const publishing = ref(false)
const dirty = ref(false)
const documentData = ref<any>(null)
const resources = ref<any[]>([])
const plans = ref<any[]>([])
const nodes = ref<WorkflowNode[]>([])
const resourceSelections = reactive<Record<string, number | null>>({})
const selectedKey = ref('')
const pickerOpen = ref(false)
const insertAt = ref(0)
const draggingKey = ref('')
const previewSnapshots = ref<any[]>([])
const previewing = ref(false)
const orderConfigs = ref<any[]>([])
const contractFiles = ref<any[]>([])
const fetchingContracts = ref(false)
const globalKeySearch = ref('')
const resourceTypes = Object.keys(resourceText)

const GLOBAL_KEYS = [
  'CLIENT_REQ_BIND_CPU', 'MARKET_RESP_BIND_CPU', 'RINGBUFFER_RSP_BIND_CPU', 'TCP_SERVER_BIND_CPU',
  'CLIENT_REQ_ENABLE', 'CLIENT_REQ_USING_DEV', 'MARKET_RESP_ENABLE', 'MARKET_RESQ_DEV',
  'REM_TO_MKT_MESSAGE_DROPCOPY_ENABLE', 'CLIENT_TO_REM_MESSAGE_DROPCOPY_ENABLE',
  'MARKET_SESSION_IDLE_REPROT_LOG', 'ACCOUNT_QUANTITY', 'WARM_ORDER_REPORT_USEC', 'ENABLE_PERF_COUNTER',
  'ENABLE_RINGBUFFER_RSP', 'ENABLE_RINGBUFFER_REQ', 'ASYNC_MKT_MSG_PROC', 'USER_TOKEN_CANCEL_ENABLE',
  'CLIENT_OT_CONNECT_MODE', 'EXANIC_IP_FILTER_FLAG', 'ENABLE_REPORT_TIMESTAMP', 'X25_KEY_VALUE',
]
const SERVER_FIELD_OPTIONS: Record<string, { value: string; label: string }[]> = {
  rem: [
    { value: 'ip', label: 'IP 地址' }, { value: 'nic_model', label: '网卡型号' },
    { value: 'machine_model', label: '机器型号' }, { value: 'os_version', label: '操作系统版本' },
    { value: 'cpu_model', label: 'CPU 型号' },
  ],
  market: [{ value: 'ip', label: 'IP 地址' }, { value: 'os_version', label: '操作系统版本' }, { value: 'cpu_model', label: 'CPU 型号' }],
  order: [{ value: 'ip', label: 'IP 地址' }, { value: 'os_version', label: '操作系统版本' }, { value: 'cpu_model', label: 'CPU 型号' }],
}

const scenario = computed(() => documentData.value?.scenario)
const draft = computed(() => documentData.value?.draft)
const selectedNode = computed(() => nodes.value.find(item => item.node_key === selectedKey.value) || null)
const editable = computed(() => auth.canOperate && draft.value?.status === 'draft')
const filteredGlobalKeys = computed(() => {
  const query = globalKeySearch.value.trim().toLowerCase()
  return query ? GLOBAL_KEYS.filter(key => key.toLowerCase().includes(query)) : GLOBAL_KEYS
})
const allGlobalKeysSelected = computed(() => {
  const keys = selectedNode.value?.config.keys || []
  return GLOBAL_KEYS.every(key => keys.includes(key))
})
const selectedPlan = computed(() => plans.value.find(item => item.id === scenario.value?.plan_id))
const selectedResources = computed(() => resourceTypes.map(type => resources.value.find(item => item.id === resourceSelections[type])).filter(Boolean))
const selectedResourceMap = computed(() => Object.fromEntries(selectedResources.value.map(item => [item.resource_type, item])))
const selectedContractFiles = computed(() => {
  const ids = new Set(selectedNode.value?.config.contract_file_ids || [])
  return contractFiles.value.filter(item => ids.has(item.id))
})

function makeKey() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function applyDocument(data: any) {
  const preferredKey = selectedKey.value
  documentData.value = data
  nodes.value = JSON.parse(JSON.stringify(data.draft.nodes || []))
  for (const type of resourceTypes) resourceSelections[type] = null
  for (const id of data.draft.resource_ids || []) {
    const resource = resources.value.find(item => item.id === id)
    if (resource) resourceSelections[resource.resource_type] = resource.id
  }
  selectedKey.value = nodes.value.some(item => item.node_key === preferredKey)
    ? preferredKey
    : (nodes.value[0]?.node_key || '')
  dirty.value = false
}

function previewColumns(file: any) {
  return Object.keys(file.preview_rows?.[0] || {})
}

async function load() {
  loading.value = true
  try {
    ;[resources.value, plans.value] = await Promise.all([
      api.get('/resources').then(response => response.data),
      api.get('/plans').then(response => response.data),
    ])
    const response = auth.canOperate
      ? await api.post(`/scenarios/${scenarioId}/workflow/draft`)
      : await api.get(`/scenarios/${scenarioId}/workflow`)
    applyDocument(response.data)
  } catch (error) {
    ElMessage.error(errorMessage(error))
    await router.replace('/plans')
  } finally {
    loading.value = false
  }
}

function resourceOptions(type: string) {
  return resources.value.filter(item => item.resource_type === type && item.business_code === selectedPlan.value?.business_code && item.is_enabled)
}

function markDirty() { if (editable.value) dirty.value = true }

function toggleAllGlobalKeys() {
  if (!editable.value || selectedNode.value?.node_type !== 'database_config') return
  selectedNode.value.config.keys = allGlobalKeysSelected.value ? [] : [...GLOBAL_KEYS]
  markDirty()
}

function nodeMeta(type: string) {
  return {
    server_config: { label: '获取服务器配置', icon: Tickets, tone: 'teal' },
    database_config: { label: '获取数据库配置', icon: Document, tone: 'blue' },
    wiring_confirmation: { label: '接线确认', icon: Connection, tone: 'amber' },
    order_preparation: { label: '发单准备', icon: Promotion, tone: 'rose' },
  }[type] || { label: type, icon: Document, tone: 'teal' }
}

function defaultNode(type: string): WorkflowNode {
  const key = makeKey()
  if (type === 'server_config') return { node_key: key, position: 0, node_type: type, name: '获取服务器配置', config: { targets: [] } }
  if (type === 'database_config') return { node_key: key, position: 0, node_type: type, name: '获取数据库配置', config: { database_name: '', keys: [] } }
  if (type === 'wiring_confirmation') return { node_key: key, position: 0, node_type: type, name: '接线确认', config: { diagram: 'placeholder' } }
  return { node_key: key, position: 0, node_type: type, name: '发单准备', config: { xml_filename: '', xml_checksum: '', network_interface: '', read_symbol_csv: 0, database_node_key: '', trading_database_name: '', contract_file_ids: [] } }
}

function openPicker(position: number) {
  if (!editable.value) return
  insertAt.value = position
  pickerOpen.value = true
}

function addNode(type: string) {
  const node = defaultNode(type)
  nodes.value.splice(insertAt.value, 0, node)
  normalizePositions()
  selectedKey.value = node.node_key
  pickerOpen.value = false
  dirty.value = true
  nextTick(() => document.querySelector(`[data-node-key="${node.node_key}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
}

function normalizePositions() { nodes.value.forEach((item, index) => { item.position = index + 1 }) }
function moveNode(index: number, offset: number) {
  const target = index + offset
  if (target < 0 || target >= nodes.value.length) return
  const [item] = nodes.value.splice(index, 1)
  nodes.value.splice(target, 0, item)
  normalizePositions(); dirty.value = true
}
function removeNode(index: number) {
  const [removed] = nodes.value.splice(index, 1)
  normalizePositions(); dirty.value = true
  if (selectedKey.value === removed.node_key) selectedKey.value = nodes.value[Math.min(index, nodes.value.length - 1)]?.node_key || ''
}
function dropNode(targetIndex: number) {
  const sourceIndex = nodes.value.findIndex(item => item.node_key === draggingKey.value)
  if (sourceIndex < 0 || sourceIndex === targetIndex) return
  const [item] = nodes.value.splice(sourceIndex, 1)
  nodes.value.splice(targetIndex, 0, item)
  draggingKey.value = ''; normalizePositions(); dirty.value = true
}

async function saveWorkflow(silent = false) {
  if (!editable.value) return
  const resourceIds = resourceTypes.map(type => resourceSelections[type]).filter((value): value is number => value != null)
  if (!resourceIds.length) throw new Error('请至少保留一个场景资源')
  saving.value = true
  try {
    const response = await api.put(`/scenarios/${scenarioId}/workflow`, {
      expected_revision: draft.value.revision,
      resource_ids: resourceIds,
      nodes: nodes.value.map(({ node_key, node_type, name, config }) => ({ node_key, node_type, name, config })),
    })
    applyDocument(response.data)
    if (!silent) ElMessage.success('工作流草稿已保存')
  } catch (error: any) {
    if (error instanceof Error && !(error as any).response) ElMessage.warning(error.message)
    else ElMessage.error(errorMessage(error))
    throw error
  } finally { saving.value = false }
}

async function publishWorkflow() {
  publishing.value = true
  try {
    await saveWorkflow(true)
    const response = await api.post(`/scenarios/${scenarioId}/workflow/publish`)
    applyDocument(response.data)
    ElMessage.success('工作流已发布并启用')
  } catch (error: any) {
    const errors = error?.response?.data?.errors
    if (errors?.length) {
      selectedKey.value = errors[0].node_key || selectedKey.value
      ElMessage.error(errors.map((item: any) => item.message).join('；'))
    } else if (!(error instanceof Error && !(error as any).response)) ElMessage.error(errorMessage(error))
  } finally { publishing.value = false }
}

async function createNextDraft() {
  const response = await api.post(`/scenarios/${scenarioId}/workflow/draft`)
  applyDocument(response.data)
  ElMessage.success('已从已发布版本创建新草稿')
}

function targetFor(role: string) { return selectedNode.value?.config.targets?.find((item: any) => item.resource_type === role) }
function toggleServerTarget(role: string, enabled: boolean) {
  const config = selectedNode.value!.config
  config.targets ||= []
  const index = config.targets.findIndex((item: any) => item.resource_type === role)
  if (enabled && index < 0) config.targets.push({ resource_type: role, fields: SERVER_FIELD_OPTIONS[role].map(item => item.value) })
  if (!enabled && index >= 0) config.targets.splice(index, 1)
  markDirty()
}

async function previewNode() {
  if (!selectedNode.value) return
  previewing.value = true
  try {
    await saveWorkflow(true)
    const response = await api.post(`/scenarios/${scenarioId}/workflow/nodes/${selectedKey.value}/preview`)
    previewSnapshots.value = response.data
    ElMessage.success(response.data.some((item: any) => item.status === 'failed') ? '预采集完成，部分项目失败' : '预采集完成')
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { previewing.value = false }
}

async function loadOrderConfigs() {
  const resource = selectedResourceMap.value.order
  if (!resource) { orderConfigs.value = []; return }
  try { orderConfigs.value = (await api.get(`/resources/${resource.id}/order-configs`)).data.files || [] }
  catch { orderConfigs.value = [] }
}

function xmlFlag(document: any): number {
  const matches: string[] = []
  const visit = (node: any) => {
    if ((node?.name || '').toLowerCase() === 'read_symbol_csv') {
      const value = node.attributes?.find((item: any) => item.name === 'value')?.value
      matches.push(String(value ?? node.children?.filter((item: any) => ['text', 'cdata'].includes(item.type)).map((item: any) => item.text || '').join('').trim() ?? ''))
    }
    node?.children?.filter((item: any) => item.type === 'element').forEach(visit)
  }
  visit(document)
  return matches.length === 1 && matches[0] === '1' ? 1 : 0
}

async function selectXml(filename: string) {
  const resource = selectedResourceMap.value.order
  if (!resource || !selectedNode.value || !filename) return
  try {
    const detail = (await api.get(`/resources/${resource.id}/order-configs/${encodeURIComponent(filename)}`)).data
    selectedNode.value.config.xml_checksum = detail.checksum
    selectedNode.value.config.read_symbol_csv = xmlFlag(detail.document)
    if (selectedNode.value.config.read_symbol_csv) suggestTradingDatabase()
    markDirty()
  } catch (error) { ElMessage.error(errorMessage(error)) }
}

function precedingDatabaseNode() {
  const index = nodes.value.findIndex(item => item.node_key === selectedKey.value)
  return [...nodes.value.slice(0, index)].reverse().find(item => item.node_type === 'database_config')
}
function suggestTradingDatabase() {
  if (!selectedNode.value) return
  const databaseNode = precedingDatabaseNode()
  selectedNode.value.config.database_node_key = databaseNode?.node_key || ''
  const source = String(databaseNode?.config.database_name || '')
  const suggested = source.endsWith('_config') ? source.slice(0, -7) + '_trading_data' : ''
  const names = selectedResourceMap.value.database?.database_names || []
  selectedNode.value.config.trading_database_name = names.includes(suggested) ? suggested : (selectedNode.value.config.trading_database_name || '')
  markDirty()
}

async function loadContractFiles() {
  if (!selectedNode.value || selectedNode.value.node_type !== 'order_preparation') { contractFiles.value = []; return }
  try { contractFiles.value = (await api.get(`/scenarios/${scenarioId}/workflow/nodes/${selectedNode.value.node_key}/contract-files`)).data }
  catch { contractFiles.value = [] }
}

async function fetchContracts(contractTypes: string[]) {
  const database = selectedResourceMap.value.database
  const databaseName = selectedNode.value?.config.trading_database_name
  if (!database || !databaseName) { ElMessage.warning('请先确认交易数据库'); return }
  fetchingContracts.value = true
  try {
    await saveWorkflow(true)
    const response = await api.post(`/scenarios/${scenarioId}/workflow/nodes/${selectedKey.value}/contract-files/fetch`, {
      database_resource_id: database.id, database_name: databaseName, contract_types: contractTypes,
    })
    contractFiles.value = [...response.data, ...contractFiles.value]
    selectedNode.value!.config.contract_file_ids = [...new Set([...(selectedNode.value!.config.contract_file_ids || []), ...response.data.map((item: any) => item.id)])]
    dirty.value = true
    ElMessage.success('最新交易日合约数据已生成并归档')
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { fetchingContracts.value = false }
}

watch(selectedNode, async node => {
  previewSnapshots.value = []
  globalKeySearch.value = ''
  if (node?.node_type === 'order_preparation') { await loadOrderConfigs(); await loadContractFiles() }
})

onBeforeRouteLeave(async () => {
  if (!dirty.value) return true
  try { await ElMessageBox.confirm('工作流有未保存修改，确定离开？', '未保存修改', { type: 'warning', confirmButtonText: '离开', cancelButtonText: '继续编辑' }); return true }
  catch { return false }
})
onMounted(load)
</script>

<template>
  <div v-loading="loading" class="workflow-page">
    <header class="workflow-header">
      <div class="header-left">
        <el-button text circle :icon="ArrowLeft" aria-label="返回方案与场景" @click="router.push('/plans')" />
        <div><div class="title-line"><h1>{{ scenario?.name || '工作流设置' }}</h1><el-tag size="small" :type="draft?.status === 'published' ? 'success' : 'warning'">{{ draft?.status === 'published' ? '已发布' : '草稿' }}</el-tag><span v-if="dirty" class="dirty-mark">有未保存修改</span></div><p>线性主流程 · v{{ draft?.version_no || 1 }} · 修订 {{ draft?.revision || 1 }}</p></div>
      </div>
      <div class="header-actions">
        <template v-if="editable"><el-button :loading="saving" @click="saveWorkflow()">保存草稿</el-button><el-button type="primary" :loading="publishing" :icon="Check" @click="publishWorkflow">发布并启用</el-button></template>
        <el-button v-else-if="auth.canOperate" type="primary" @click="createNextDraft">创建新草稿</el-button>
      </div>
    </header>

    <div class="editor-grid">
      <aside class="resource-panel">
        <div class="panel-heading"><div><strong>场景资源池</strong><small>每种类型最多一个</small></div></div>
        <div class="resource-fields">
          <label v-for="type in resourceTypes" :key="type"><span>{{ resourceText[type] || type }}</span><el-select v-model="resourceSelections[type]" clearable filterable :disabled="!editable" placeholder="未绑定" @change="markDirty"><el-option v-for="resource in resourceOptions(type)" :key="resource.id" :label="resource.name" :value="resource.id" /></el-select></label>
        </div>
        <div class="resource-note"><strong>发布校验</strong><span>节点只能引用资源池中的角色；正式运行可替换同类型资源。</span></div>
      </aside>

      <main class="workflow-canvas">
        <div class="canvas-intro"><strong>主流程</strong><span>拖拽节点调整顺序，点击节点编辑属性</span></div>
        <div class="flow-column">
          <button v-if="editable" class="add-point" type="button" aria-label="在流程开头添加节点" @click="openPicker(0)"><el-icon><Plus /></el-icon></button>
          <div v-if="!nodes.length" class="flow-empty"><el-icon><Tickets /></el-icon><strong>还没有流程节点</strong><span>点击加号添加第一个节点</span></div>
          <template v-for="(node, index) in nodes" :key="node.node_key">
            <article :data-node-key="node.node_key" class="flow-node" :class="[{ selected: selectedKey === node.node_key }, nodeMeta(node.node_type).tone]" :draggable="editable" @dragstart="draggingKey = node.node_key" @dragover.prevent @drop="dropNode(index)" @click="selectedKey = node.node_key">
              <div class="node-icon"><el-icon><component :is="nodeMeta(node.node_type).icon" /></el-icon></div>
              <div class="node-copy"><span>{{ nodeMeta(node.node_type).label }}</span><strong>{{ node.name }}</strong><small v-if="node.node_type === 'server_config'">{{ node.config.targets?.length || 0 }} 台服务器</small><small v-else-if="node.node_type === 'database_config'">{{ node.config.keys?.length || 0 }} 个配置项</small><small v-else-if="node.node_type === 'wiring_confirmation'">需要人工确认</small><small v-else>{{ node.config.xml_filename || '未选择 XML' }}</small></div>
              <div v-if="editable" class="node-actions"><el-button text circle :icon="Top" :disabled="index === 0" aria-label="上移节点" @click.stop="moveNode(index, -1)" /><el-button text circle :icon="Bottom" :disabled="index === nodes.length - 1" aria-label="下移节点" @click.stop="moveNode(index, 1)" /><el-button text circle type="danger" :icon="Delete" aria-label="删除节点" @click.stop="removeNode(index)" /></div>
            </article>
            <div class="flow-link"><span></span><button v-if="editable" class="add-point" type="button" aria-label="在此处添加节点" @click="openPicker(index + 1)"><el-icon><Plus /></el-icon></button></div>
          </template>
          <div v-if="nodes.length" class="flow-end"><span></span>结束流程</div>
        </div>
      </main>

      <aside class="property-panel">
        <template v-if="selectedNode">
          <div class="property-title"><div class="node-icon" :class="nodeMeta(selectedNode.node_type).tone"><el-icon><component :is="nodeMeta(selectedNode.node_type).icon" /></el-icon></div><div><strong>节点属性</strong><small>{{ nodeMeta(selectedNode.node_type).label }}</small></div></div>
          <label class="field"><span>节点名称</span><el-input v-model="selectedNode.name" :disabled="!editable" maxlength="128" @input="markDirty" /></label>

          <template v-if="selectedNode.node_type === 'server_config'">
            <div class="section-label">采集服务器与字段</div>
            <div v-for="role in ['rem', 'market', 'order']" :key="role" class="target-box" :class="{ disabled: !selectedResourceMap[role] }">
              <el-checkbox :model-value="Boolean(targetFor(role))" :disabled="!editable || !selectedResourceMap[role]" @change="value => toggleServerTarget(role, Boolean(value))"><strong>{{ resourceText[role] }}</strong><small>{{ selectedResourceMap[role]?.name || '资源池未绑定' }}</small></el-checkbox>
              <el-checkbox-group v-if="targetFor(role)" v-model="targetFor(role).fields" :disabled="!editable" @change="markDirty"><el-checkbox v-for="field in SERVER_FIELD_OPTIONS[role]" :key="field.value" :label="field.value">{{ field.label }}</el-checkbox></el-checkbox-group>
            </div>
            <el-button :icon="Refresh" :loading="previewing" :disabled="!editable" @click="previewNode">预采集并保存</el-button>
          </template>

          <template v-else-if="selectedNode.node_type === 'database_config'">
            <label class="field"><span>配置数据库</span><el-select v-model="selectedNode.config.database_name" :disabled="!editable" filterable @change="markDirty"><el-option v-for="name in selectedResourceMap.database?.database_names || []" :key="name" :label="name" :value="name" /></el-select></label>
            <div class="section-label">t_global_settings 配置项</div>
            <div class="key-toolbar"><el-input v-model="globalKeySearch" :prefix-icon="Search" clearable placeholder="搜索配置项" /><el-button size="small" :disabled="!editable" @click="toggleAllGlobalKeys">{{ allGlobalKeysSelected ? '取消全选' : '全选' }}</el-button></div>
            <div class="key-grid"><el-checkbox-group v-if="filteredGlobalKeys.length" v-model="selectedNode.config.keys" class="key-options" :disabled="!editable" @change="markDirty"><el-checkbox v-for="key in filteredGlobalKeys" :key="key" :label="key">{{ key }}</el-checkbox></el-checkbox-group><div v-else class="key-grid-empty">无匹配配置项</div></div>
            <el-button :icon="Refresh" :loading="previewing" :disabled="!editable" @click="previewNode">预采集并保存</el-button>
          </template>

          <template v-else-if="selectedNode.node_type === 'wiring_confirmation'">
            <div class="wiring-placeholder"><el-icon><Connection /></el-icon><strong>接线图占位</strong><span>正式运行到达此节点后，机房人员确认接线才能继续。</span></div>
          </template>

          <template v-else-if="selectedNode.node_type === 'order_preparation'">
            <label class="field required"><span>XML 配置</span><el-select v-model="selectedNode.config.xml_filename" :disabled="!editable || !selectedResourceMap.order" filterable @change="selectXml"><el-option v-for="file in orderConfigs" :key="file.name" :label="file.name" :value="file.name" /></el-select></label>
            <label class="field"><span>网卡接口</span><el-input v-model="selectedNode.config.network_interface" :disabled="!editable" placeholder="例如 p4p1" maxlength="15" @input="markDirty" /><small>运行时安全生成 ZF_ATTR，不接受完整 Shell 命令。</small></label>
            <el-alert :title="selectedNode.config.read_symbol_csv ? 'XML 需要合约 CSV' : 'XML 未启用 read_symbol_csv'" :type="selectedNode.config.read_symbol_csv ? 'warning' : 'info'" :closable="false" show-icon />
            <template v-if="selectedNode.config.read_symbol_csv">
              <label class="field"><span>交易数据库</span><el-select v-model="selectedNode.config.trading_database_name" :disabled="!editable" filterable @change="markDirty"><el-option v-for="name in selectedResourceMap.database?.database_names || []" :key="name" :label="name" :value="name" /></el-select><small>根据前置 *_config 自动建议为 *_trading_data，并由你确认。</small></label>
              <div class="contract-toolbar"><strong>合约数据</strong><div><el-button size="small" :loading="fetchingContracts" :disabled="!editable" @click="fetchContracts(['futures'])">获取期货</el-button><el-button size="small" :loading="fetchingContracts" :disabled="!editable" @click="fetchContracts(['options'])">获取期权</el-button></div></div>
              <el-checkbox-group v-model="selectedNode.config.contract_file_ids" class="contract-list" :disabled="!editable" @change="markDirty"><el-checkbox v-for="file in contractFiles" :key="file.id" :label="file.id"><span><strong>{{ file.filename }}</strong><small>{{ file.contract_type === 'futures' ? '期货' : '期权' }} · {{ file.quote_date }} · {{ file.row_count }} 条</small></span></el-checkbox></el-checkbox-group>
              <el-collapse v-if="selectedContractFiles.length" class="contract-previews">
                <el-collapse-item v-for="file in selectedContractFiles" :key="file.id" :name="file.id">
                  <template #title><span class="contract-preview-title"><strong>{{ file.contract_type === 'futures' ? '期货' : '期权' }}</strong><small>{{ file.quote_date }} · {{ file.row_count }} 条</small></span></template>
                  <div class="checksum"><span>SHA-256</span><code>{{ file.checksum }}</code></div>
                  <el-table :data="file.preview_rows" size="small" max-height="210" border>
                    <el-table-column v-for="column in previewColumns(file)" :key="column" :prop="column" :label="column" min-width="120" show-overflow-tooltip />
                  </el-table>
                </el-collapse-item>
              </el-collapse>
            </template>
          </template>

          <div v-if="previewSnapshots.length" class="preview-results"><div class="section-label">最近预采集结果</div><div v-for="snapshot in previewSnapshots" :key="snapshot.id" class="snapshot"><div><strong>{{ snapshot.source_type === 'server' ? `资源 #${snapshot.resource_id}` : snapshot.database_name }}</strong><el-tag size="small" :type="snapshot.status === 'succeeded' ? 'success' : 'danger'">{{ snapshot.status === 'succeeded' ? '成功' : '失败' }}</el-tag></div><dl><template v-for="item in snapshot.items" :key="item.id"><dt>{{ item.item_label }}</dt><dd :class="{ failed: item.status === 'failed' }">{{ item.value_text || item.error_message || '-' }}</dd></template></dl></div></div>
        </template>
        <div v-else class="property-empty"><el-icon><Tickets /></el-icon><strong>选择一个节点</strong><span>节点配置和预览结果会显示在这里</span></div>
      </aside>
    </div>

    <el-drawer v-model="pickerOpen" title="选择一个节点" size="360px" append-to-body>
      <div class="node-picker"><button v-for="type in ['server_config', 'database_config', 'wiring_confirmation', 'order_preparation']" :key="type" type="button" @click="addNode(type)"><span class="node-icon" :class="nodeMeta(type).tone"><el-icon><component :is="nodeMeta(type).icon" /></el-icon></span><span><strong>{{ nodeMeta(type).label }}</strong><small>{{ type === 'server_config' ? '通过 SSH 采集软硬件信息' : type === 'database_config' ? '读取 t_global_settings' : type === 'wiring_confirmation' ? '阻断流程并等待机房确认' : '校验 XML、网卡和合约文件' }}</small></span></button></div>
    </el-drawer>
  </div>
</template>

<style scoped>
.workflow-page{min-height:calc(100vh - 58px);background:#edf2f4;color:#1d2c34}.workflow-header{height:78px;padding:0 20px;background:#fff;border-bottom:1px solid #dce4e8;display:flex;align-items:center;justify-content:space-between}.header-left,.title-line,.header-actions,.property-title,.contract-toolbar{display:flex;align-items:center}.header-left{gap:10px}.header-left h1{font-size:18px;margin:0}.title-line{gap:9px}.header-left p{margin:5px 0 0;color:#7c8991;font-size:12px}.dirty-mark{font-size:12px;color:#b7791f}.header-actions{gap:9px}.editor-grid{display:grid;grid-template-columns:240px minmax(430px,1fr) 390px;height:calc(100vh - 136px);min-height:660px}.resource-panel,.property-panel{background:#fff;overflow:auto}.resource-panel{border-right:1px solid #dce4e8;padding:18px}.property-panel{border-left:1px solid #dce4e8;padding:20px}.panel-heading strong,.panel-heading small,.property-title strong,.property-title small{display:block}.panel-heading small,.property-title small{color:#81909a;font-size:11px;margin-top:4px}.resource-fields{display:grid;gap:13px;margin-top:20px}.resource-fields label,.field{display:grid;gap:6px}.resource-fields label>span,.field>span{font-size:12px;font-weight:600;color:#52616a}.resource-note{margin-top:22px;padding:12px;border-radius:8px;background:#edf7f4;color:#37675b}.resource-note strong,.resource-note span{display:block}.resource-note span{font-size:11px;line-height:1.6;margin-top:5px}.workflow-canvas{overflow:auto;padding:18px 28px 60px;background-color:#f4f7f8;background-image:radial-gradient(#d7e0e4 1px,transparent 1px);background-size:22px 22px}.canvas-intro{display:flex;align-items:baseline;justify-content:space-between;color:#71808a;font-size:12px}.canvas-intro strong{font-size:14px;color:#34444d}.flow-column{width:360px;margin:22px auto;display:flex;flex-direction:column;align-items:center}.add-point{width:30px;height:30px;border:1px solid #cbd7dc;border-radius:7px;background:#fff;color:#268b77;display:grid;place-items:center;cursor:pointer;box-shadow:0 3px 10px rgba(34,61,72,.08)}.flow-node{width:360px;min-height:108px;padding:15px;background:#fff;border:1px solid #d9e2e6;border-left:4px solid #48a895;border-radius:10px;display:grid;grid-template-columns:42px minmax(0,1fr) auto;gap:12px;align-items:start;box-shadow:0 8px 24px rgba(35,58,68,.08);cursor:pointer}.flow-node.selected{border-color:#22a68e;box-shadow:0 0 0 3px rgba(34,166,142,.13),0 10px 28px rgba(35,58,68,.1)}.flow-node.blue{border-left-color:#4f8fbd}.flow-node.amber{border-left-color:#d29b42}.flow-node.rose{border-left-color:#bd6b78}.node-icon{width:36px;height:36px;border-radius:8px;background:#e1f3ee;color:#248b76;display:grid;place-items:center;flex:0 0 auto}.node-icon.blue{background:#e7f0f7;color:#3f7ca7}.node-icon.amber{background:#f8eedc;color:#a77525}.node-icon.rose{background:#f7e7ea;color:#a65361}.node-copy>span,.node-copy>strong,.node-copy>small{display:block}.node-copy>span{font-size:10px;color:#8a98a0}.node-copy>strong{font-size:14px;margin-top:5px}.node-copy>small{font-size:11px;color:#71808a;margin-top:8px}.node-actions{display:flex;flex-direction:column;opacity:.25}.flow-node:hover .node-actions,.flow-node.selected .node-actions{opacity:1}.node-actions :deep(.el-button){margin:0}.flow-link{height:54px;display:flex;flex-direction:column;align-items:center}.flow-link>span{height:24px;border-left:2px solid #ccd7dc}.flow-link .add-point{width:28px;height:28px}.flow-empty,.property-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;color:#87949c}.flow-empty{width:360px;height:180px;margin-top:14px;border:1px dashed #bccbd1;border-radius:12px;background:rgba(255,255,255,.7)}.flow-empty :deep(svg),.property-empty :deep(svg){width:30px;height:30px}.flow-empty strong,.property-empty strong{color:#52616a;margin-top:12px}.flow-empty span,.property-empty span{font-size:12px;margin-top:5px}.flow-end{color:#87949c;font-size:11px;display:flex;flex-direction:column;align-items:center}.flow-end span{height:18px;border-left:2px solid #ccd7dc}.property-title{gap:10px;padding-bottom:16px;border-bottom:1px solid #e4eaed}.property-panel .field{margin-top:17px}.field small{color:#829099;font-size:11px;line-height:1.5}.required>span:after{content:' *';color:#d04b5d}.section-label{font-size:12px;font-weight:700;color:#46565f;margin:20px 0 10px}.target-box{padding:12px;margin-bottom:10px;border:1px solid #e0e7ea;border-radius:8px}.target-box.disabled{background:#f6f8f9}.target-box :deep(.el-checkbox__label) strong,.target-box :deep(.el-checkbox__label) small{display:block}.target-box :deep(.el-checkbox__label) small{font-size:10px;color:#8a979f}.target-box :deep(.el-checkbox-group){display:grid;grid-template-columns:1fr 1fr;margin:10px 0 0 24px}.key-toolbar{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center;margin-bottom:8px}.key-toolbar :deep(.el-button){min-width:64px;margin:0}.key-grid{max-height:310px;overflow:auto;padding:10px;border:1px solid #e0e7ea;border-radius:8px}.key-options{display:grid;grid-template-columns:1fr}.key-options :deep(.el-checkbox){margin-right:0}.key-options :deep(.el-checkbox__label){font:11px/1.4 Cascadia Code,Consolas,monospace}.key-grid-empty{min-height:72px;display:grid;place-items:center;color:#87949c;font-size:11px}.wiring-placeholder{margin-top:20px;min-height:260px;border:1px dashed #d6b56e;border-radius:10px;background:repeating-linear-gradient(45deg,#fffaf0,#fffaf0 10px,#fdf6e8 10px,#fdf6e8 20px);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;color:#9a762d;padding:24px}.wiring-placeholder :deep(svg){width:44px;height:44px}.wiring-placeholder strong{margin-top:14px}.wiring-placeholder span{font-size:12px;line-height:1.6;margin-top:7px;max-width:240px}.contract-toolbar{justify-content:space-between;margin-top:18px}.contract-list{display:grid;margin-top:10px;border:1px solid #e0e7ea;border-radius:8px;max-height:200px;overflow:auto}.contract-list :deep(.el-checkbox){height:auto;margin:0;padding:10px;border-bottom:1px solid #edf1f3}.contract-list :deep(.el-checkbox:last-child){border-bottom:0}.contract-list strong,.contract-list small{display:block}.contract-list small{font-size:10px;color:#85929a;margin-top:3px}.preview-results{margin-top:22px}.snapshot{border:1px solid #e0e7ea;border-radius:8px;margin-bottom:10px;padding:11px}.snapshot>div{display:flex;justify-content:space-between;align-items:center}.snapshot dl{display:grid;grid-template-columns:120px 1fr;margin:10px 0 0;font-size:11px}.snapshot dt,.snapshot dd{padding:5px 0;border-top:1px solid #edf1f3}.snapshot dt{color:#77858e}.snapshot dd{margin:0;word-break:break-word}.snapshot dd.failed{color:#c74d5d}.property-empty{height:100%}.node-picker{display:grid;gap:10px}.node-picker button{width:100%;display:flex;align-items:center;gap:12px;padding:14px;border:1px solid #dfe6ea;border-radius:9px;background:#fff;text-align:left;cursor:pointer}.node-picker button:hover{border-color:#55aa98;background:#f2f9f7}.node-picker strong,.node-picker small{display:block}.node-picker small{margin-top:5px;color:#7c8a93;font-size:11px}.teal{--node-tone:#48a895}@media(max-width:1250px){.editor-grid{grid-template-columns:210px minmax(410px,1fr) 340px}.flow-node,.flow-empty{width:330px}.flow-column{width:330px}}
.flow-node,.flow-empty,.wiring-placeholder,.node-picker button{border-radius:8px}.contract-previews{margin-top:12px}.contract-preview-title{display:flex;align-items:center;gap:8px;min-width:0}.contract-preview-title small{color:#7d8a92}.checksum{display:grid;gap:4px;margin-bottom:10px}.checksum span{font-size:10px;color:#7d8a92}.checksum code{font-size:10px;line-height:1.5;overflow-wrap:anywhere;color:#34444d}.snapshot dl{grid-template-columns:minmax(0,1fr) minmax(96px,35%);column-gap:12px}.snapshot dt,.snapshot dd{min-width:0;line-height:1.45;overflow-wrap:anywhere}.snapshot dt{font-family:Cascadia Code,Consolas,monospace;font-size:10px}.snapshot dd{font-variant-numeric:tabular-nums}@media(max-width:1250px){.editor-grid{grid-template-columns:180px minmax(320px,1fr) 300px}.resource-panel{padding:12px}.property-panel{padding:14px}.workflow-canvas{padding:16px 12px 48px}.flow-column,.flow-node,.flow-empty{width:290px}}
</style>
