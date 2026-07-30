<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from '@/ui/elementPlusServices'
import { ArrowLeft, Bottom, Check, Close, Connection, Delete, Document, Edit, Files, Plus, Promotion, Refresh, Search, SwitchButton, Tickets, Top, VideoPause, VideoPlay } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import WiringTopologyDiagram from '@/components/WiringTopologyDiagram.vue'
import { useAuthStore } from '@/stores/auth'
import { cloneWorkflowValue, useStagedWorkflowNode } from '@/composables/useStagedWorkflowNode'
import type { EditableWorkflowNode as WorkflowNode, WorkflowNodeType } from '@/types/api'
import { resourceText } from '@/utils/status'
import { buildWiringSnapshot, wiringInterfaceNameDefaults } from '@/utils/wiring'
import { parserXmlRole, type ParserXmlRole } from '@/utils/parserConfig'
import { marketScriptSelectionStatus, moveMarketScriptSelection, toggleMarketScriptSelection } from '@/utils/marketScripts'
import { DEFAULT_REM_STARTUP_COMMANDS, normalizeShellCommands, remStartupCommandText } from '@/utils/remCommands'
import { defaultSlnicCommands, slnicCommandText } from '@/utils/slnicCommands'
import {
  applyDatabaseConfigTemplate,
  filterDatabaseConfigItems,
  staleDatabaseConfigKeys,
  type DatabaseConfigItem,
  type DatabaseConfigTemplate,
} from '@/utils/databaseConfig'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const scenarioId = Number(route.params.id)
const plansReturnQuery = computed(() => {
  const directoryId = Number(route.query.directory_id)
  return Number.isInteger(directoryId) && directoryId > 0
    ? { directory_id: String(directoryId) }
    : {}
})
const loading = ref(true)
const saving = ref(false)
const publishing = ref(false)
const pausing = ref(false)
const dirty = ref(false)
const documentData = ref<any>(null)
const versions = ref<any[]>([])
const includeArchived = ref(false)
const versionsLoading = ref(false)
const versionActionLoading = ref(false)
const selectedVersionId = ref<number | null>(null)
const activeTab = ref<'resources' | 'workflow'>('workflow')
const resources = ref<any[]>([])
const plans = ref<any[]>([])
const nodes = ref<WorkflowNode[]>([])
const resourceSelections = reactive<Record<string, number | null>>({})
const selectedKey = ref('')
const stagedNode = useStagedWorkflowNode<WorkflowNode>()
const nodeForm = stagedNode.form
const resourceBaseline = ref('[]')
const pickerOpen = ref(false)
const insertAt = ref(0)
const draggingKey = ref('')
const previewSnapshots = ref<any[]>([])
const previewing = ref(false)
const orderConfigs = ref<any[]>([])
const parserConfigs = ref<any[]>([])
const loadingParserConfigs = ref(false)
const statisticsScripts = ref<any[]>([])
const loadingStatisticsScripts = ref(false)
const marketScripts = ref<any[]>([])
const loadingMarketScripts = ref(false)
const contractFiles = ref<any[]>([])
const fetchingContracts = ref(false)
const scanningContracts = ref(false)
let contractFilesRequestId = 0
const globalKeySearch = ref('')
const shellCommandEditorText = ref('')
const databaseConfigItems = ref<DatabaseConfigItem[]>([])
const databaseConfigCatalogLoading = ref(false)
const databaseConfigCatalogLoaded = ref(false)
const databaseConfigCatalogError = ref('')
const databaseConfigTemplates = ref<DatabaseConfigTemplate[]>([])
const databaseConfigTemplatesLoading = ref(false)
const selectedDatabaseConfigTemplateId = ref<number | null>(null)
let databaseConfigCatalogRequestId = 0
const resourceTypes = Object.keys(resourceText)
const slnicNodeTypes = new Set(['slnic_start_capture', 'slnic_stop_capture', 'slnic_merge_capture'])
const nodeCategories = [
  { title: '获取配置', types: ['server_config', 'database_config'] },
  { title: '流程准备', types: ['wiring_confirmation', 'rem_startup', 'market_startup'] },
  { title: '发单', types: ['order_preparation'] },
  { title: 'SLNIC', types: ['slnic_start_capture', 'slnic_stop_capture', 'slnic_merge_capture'] },
  { title: '数据处理', types: ['parser_parse', 'data_statistics'] },
  { title: '结果交付', types: ['report_generation'] },
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
const currentVersionId = computed(() => scenario.value?.draft_workflow_version_id || scenario.value?.published_workflow_version_id || null)
const viewingCurrentVersion = computed(() => selectedVersionId.value === currentVersionId.value)
const selectedStoredNode = computed(() => nodes.value.find(item => item.node_key === selectedKey.value) || null)
const selectedNode = computed(() => nodeForm.value)
const editable = computed(() => auth.canOperate && viewingCurrentVersion.value && draft.value?.status === 'draft' && !scenario.value?.is_enabled)
const nodeDirty = computed(() => editable.value && stagedNode.dirty.value)
const resourceIds = computed(() => resourceTypes.map(type => resourceSelections[type]).filter((value): value is number => value != null))
const resourceDirty = computed(() => editable.value && JSON.stringify(resourceIds.value) !== resourceBaseline.value)
const selectedVersion = computed(() => versions.value.find(item => item.id === selectedVersionId.value) || draft.value)
const canArchiveSelectedVersion = computed(() => auth.canOperate && selectedVersion.value && ['draft', 'retired'].includes(selectedVersion.value.status) && !(scenario.value?.is_enabled && selectedVersion.value.id === scenario.value?.published_workflow_version_id))
const displayedDatabaseConfigItems = computed<DatabaseConfigItem[]>(() => {
  if (databaseConfigCatalogLoaded.value) return databaseConfigItems.value
  return (selectedNode.value?.config.keys || []).map(key => ({ key, description: null }))
})
const filteredGlobalKeys = computed(() => {
  return filterDatabaseConfigItems(displayedDatabaseConfigItems.value, globalKeySearch.value)
})
const staleGlobalKeys = computed(() => {
  if (!databaseConfigCatalogLoaded.value) return []
  return staleDatabaseConfigKeys(selectedNode.value?.config.keys || [], databaseConfigItems.value)
})
const allGlobalKeysSelected = computed(() => {
  const keys = selectedNode.value?.config.keys || []
  return databaseConfigItems.value.length > 0
    && databaseConfigItems.value.every(item => keys.includes(item.key))
})
const selectedPlan = computed(() => plans.value.find(item => item.id === scenario.value?.plan_id))
const selectedResources = computed(() => resourceTypes.map(type => resources.value.find(item => item.id === resourceSelections[type])).filter(Boolean))
const selectedResourceMap = computed(() => Object.fromEntries(selectedResources.value.map(item => [item.resource_type, item])))
const wiringPreview = computed(() => buildWiringSnapshot(
  selectedPlan.value?.business_code || '',
  selectedResourceMap.value.rem,
  selectedResourceMap.value.market,
  selectedResourceMap.value.slnic,
  selectedNode.value?.node_type === 'wiring_confirmation' ? selectedNode.value.config : {},
))
const wiringValidationMessage = computed(() => {
  if (!selectedResourceMap.value.rem) return '场景资源池尚未绑定 REM 柜台'
  if (!selectedResourceMap.value.market) return '场景资源池尚未绑定模拟市场'
  if (!selectedResourceMap.value.slnic) return '场景资源池尚未绑定 SLNIC 节点'
  if (!selectedResourceMap.value.rem.trade_ip) return '所选 REM 尚未配置交易 IP'
  if (!wiringPreview.value) return '模拟市场或 SLNIC 的 Linux 地址不是有效 IPv4 地址'
  if (!wiringPreview.value.client_interface.name.trim()) return '请输入第 1 个接口名称'
  if (!wiringPreview.value.market_interface.name.trim()) return '请输入第 2 个接口名称'
  if (selectedPlan.value?.business_code !== 'fut_mm') {
    if (wiringPreview.value.auxiliary_interfaces.length !== 2) return '整合版接线图需要配置第 3、4 个接口名称'
    if (!wiringPreview.value.auxiliary_interfaces[0]?.trim()) return '请输入第 3 个接口名称'
    if (!wiringPreview.value.auxiliary_interfaces[1]?.trim()) return '请输入第 4 个接口名称'
  }
  return ''
})
const selectedContractFiles = computed(() => {
  const ids = new Set(selectedNode.value?.config.contract_file_ids || [])
  return contractFiles.value.filter(item => ids.has(item.id))
})
const parserXmlOptions = computed<Record<ParserXmlRole, any[]>>(() => ({
  config: parserConfigs.value.filter(file => parserXmlRole(file.name) === 'config'),
  instance: parserConfigs.value.filter(file => parserXmlRole(file.name) === 'instance'),
  analysis: parserConfigs.value.filter(file => parserXmlRole(file.name) === 'analysis'),
}))
const selectedMarketScriptRows = computed(() => {
  const selections = Array.isArray(selectedNode.value?.config.scripts) ? selectedNode.value.config.scripts : []
  return selections.map((selection: any) => {
    const file = marketScripts.value.find(item => item.name === selection.filename)
    const status = marketScriptSelectionStatus(selection, file)
    return { selection, file, status }
  })
})
const shellCommandCount = computed(() => normalizeShellCommands(shellCommandEditorText.value).length)

function makeKey() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function syncNodeForm() {
  stagedNode.stage(selectedStoredNode.value)
}

function applyDocument(data: any) {
  const preferredKey = selectedKey.value
  documentData.value = data
  nodes.value = JSON.parse(JSON.stringify(data.draft.nodes || []))
  const defaults = wiringInterfaceNameDefaults(
    plans.value.find(item => item.id === data.scenario?.plan_id)?.business_code || '',
  )
  for (const node of nodes.value) {
    if (node.node_type !== 'wiring_confirmation') continue
    node.config.client_interface_name ??= defaults.client_interface_name
    node.config.market_interface_name ??= defaults.market_interface_name
    node.config.auxiliary_interface_names ??= [...defaults.auxiliary_interface_names]
  }
  for (const type of resourceTypes) resourceSelections[type] = null
  for (const id of data.draft.resource_ids || []) {
    const resource = resources.value.find(item => item.id === id)
    if (resource) resourceSelections[resource.resource_type] = resource.id
  }
  selectedKey.value = nodes.value.some(item => item.node_key === preferredKey)
    ? preferredKey
    : (nodes.value[0]?.node_key || '')
  selectedVersionId.value = data.draft.id
  resourceBaseline.value = JSON.stringify(resourceTypes.map(type => resourceSelections[type]).filter(value => value != null))
  syncNodeForm()
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
    const response = await api.get(`/scenarios/${scenarioId}/workflow`)
    applyDocument(response.data)
    await loadVersions()
  } catch (error) {
    ElMessage.error(errorMessage(error))
    await router.replace('/plans')
  } finally {
    loading.value = false
  }
}

async function loadVersions() {
  versionsLoading.value = true
  try {
    versions.value = (await api.get(`/scenarios/${scenarioId}/workflow/versions`, { params: { include_archived: includeArchived.value } })).data || []
  } finally {
    versionsLoading.value = false
  }
}

function resourceOptions(type: string) {
  return resources.value.filter(item => item.resource_type === type && item.business_code === selectedPlan.value?.business_code && item.is_enabled)
}

function markDirty() { if (editable.value) dirty.value = true }

function toggleAllGlobalKeys() {
  if (!editable.value || selectedNode.value?.node_type !== 'database_config') return
  selectedNode.value.config.keys = allGlobalKeysSelected.value
    ? []
    : [...databaseConfigItems.value.map(item => item.key), ...staleGlobalKeys.value]
  markDirty()
}

async function loadDatabaseConfigItems() {
  const requestId = ++databaseConfigCatalogRequestId
  const resource = selectedResourceMap.value.database
  const databaseName = String(selectedNode.value?.config.database_name || '')
  databaseConfigCatalogError.value = ''
  databaseConfigCatalogLoaded.value = false
  if (!editable.value || selectedNode.value?.node_type !== 'database_config' || !resource || !databaseName) {
    databaseConfigItems.value = []
    databaseConfigCatalogLoading.value = false
    return
  }
  databaseConfigCatalogLoading.value = true
  try {
    const response = await api.get(`/resources/${resource.id}/database/config-items`, {
      params: { database_name: databaseName },
    })
    if (requestId !== databaseConfigCatalogRequestId) return
    databaseConfigItems.value = response.data || []
    databaseConfigCatalogLoaded.value = true
  } catch (error) {
    if (requestId !== databaseConfigCatalogRequestId) return
    databaseConfigItems.value = []
    databaseConfigCatalogError.value = errorMessage(error)
  } finally {
    if (requestId === databaseConfigCatalogRequestId) databaseConfigCatalogLoading.value = false
  }
}

async function loadDatabaseConfigTemplates() {
  if (!editable.value) return
  databaseConfigTemplatesLoading.value = true
  try {
    databaseConfigTemplates.value = (await api.get('/database-config-templates')).data || []
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    databaseConfigTemplatesLoading.value = false
  }
}

function selectDatabaseConfigDatabase() {
  selectedDatabaseConfigTemplateId.value = null
  markDirty()
}

function applySelectedDatabaseConfigTemplate() {
  const template = databaseConfigTemplates.value.find(
    item => item.id === selectedDatabaseConfigTemplateId.value,
  )
  if (!template || !selectedNode.value || !databaseConfigCatalogLoaded.value) return
  const result = applyDatabaseConfigTemplate(template.keys, databaseConfigItems.value)
  selectedNode.value.config.keys = result.selected
  markDirty()
  if (result.missing.length) {
    ElMessage.warning(`已应用 ${result.selected.length} 项，跳过 ${result.missing.length} 个当前库不存在的配置项`)
  } else {
    ElMessage.success(`已应用模板“${template.name}”`)
  }
}

async function saveDatabaseConfigTemplate() {
  const keys = selectedNode.value?.config.keys || []
  if (!keys.length) { ElMessage.warning('请先选择至少一个配置项'); return }
  try {
    const { value } = await ElMessageBox.prompt(
      `将当前选择的 ${keys.length} 个配置项保存为个人模板`,
      '保存配置模板',
      { confirmButtonText: '保存', cancelButtonText: '取消', inputPlaceholder: '模板名称', inputValidator: value => Boolean(value.trim()) || '请输入模板名称' },
    )
    const response = await api.post('/database-config-templates', { name: value, keys })
    await loadDatabaseConfigTemplates()
    selectedDatabaseConfigTemplateId.value = response.data.id
    ElMessage.success('模板已保存')
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  }
}

async function renameDatabaseConfigTemplate() {
  const template = databaseConfigTemplates.value.find(
    item => item.id === selectedDatabaseConfigTemplateId.value,
  )
  if (!template) return
  try {
    const { value } = await ElMessageBox.prompt('输入新的模板名称', '重命名配置模板', {
      confirmButtonText: '保存', cancelButtonText: '取消', inputValue: template.name,
      inputValidator: value => Boolean(value.trim()) || '请输入模板名称',
    })
    await api.patch(`/database-config-templates/${template.id}`, { new_name: value })
    await loadDatabaseConfigTemplates()
    selectedDatabaseConfigTemplateId.value = template.id
    ElMessage.success('模板已重命名')
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  }
}

async function deleteDatabaseConfigTemplate() {
  const template = databaseConfigTemplates.value.find(
    item => item.id === selectedDatabaseConfigTemplateId.value,
  )
  if (!template) return
  try {
    await ElMessageBox.confirm(`确定删除模板“${template.name}”？`, '删除配置模板', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
    await api.delete(`/database-config-templates/${template.id}`)
    selectedDatabaseConfigTemplateId.value = null
    await loadDatabaseConfigTemplates()
    ElMessage.success('模板已删除')
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  }
}

function removeStaleGlobalKeys() {
  if (!selectedNode.value) return
  const stale = new Set(staleGlobalKeys.value)
  selectedNode.value.config.keys = (selectedNode.value.config.keys || []).filter(key => !stale.has(key))
  markDirty()
}

function nodeMeta(type: string) {
  return {
    server_config: { label: '获取服务器配置', icon: Tickets, tone: 'teal' },
    database_config: { label: '获取数据库配置', icon: Document, tone: 'blue' },
    wiring_confirmation: { label: '接线确认', icon: Connection, tone: 'amber' },
    rem_startup: { label: '启动rem柜台', icon: SwitchButton, tone: 'amber' },
    market_startup: { label: '启动模拟市场', icon: VideoPlay, tone: 'amber' },
    order_preparation: { label: '发单执行', icon: Promotion, tone: 'rose' },
    slnic_start_capture: { label: '启动 SLNIC', icon: VideoPlay, tone: 'violet' },
    slnic_stop_capture: { label: '关闭 SLNIC', icon: VideoPause, tone: 'violet' },
    slnic_merge_capture: { label: '合并 pcapng', icon: Files, tone: 'violet' },
    parser_parse: { label: '数据解析', icon: Document, tone: 'blue' },
    data_statistics: { label: '数据统计', icon: Tickets, tone: 'teal' },
    report_generation: { label: '生成报告', icon: Files, tone: 'blue' },
  }[type] || { label: type, icon: Document, tone: 'teal' }
}

function nodeDescription(type: string) {
  return {
    server_config: '通过 SSH 采集软硬件信息',
    database_config: '读取 t_global_settings',
    wiring_confirmation: '阻断流程并等待机房确认',
    rem_startup: '按配置顺序在共享 Shell 中执行 REM 启动命令',
    market_startup: '按配置顺序执行模拟市场根目录下的启动脚本',
    order_preparation: '启动发单工具，运行时按需发单',
    slnic_start_capture: '调用脚本开始四路抓包',
    slnic_stop_capture: '调用脚本结束抓包',
    slnic_merge_capture: '合并并转换为单一 pcapng 产物',
    parser_parse: '导出订单数据、上传抓包并执行解析工具',
    data_statistics: '选择解析 CSV 并调用交易所统计脚本',
    report_generation: '汇总执行时已有的配置和测速结果，生成 HTML、Excel 与 PDF',
  }[type] || ''
}

function slnicWorkdir() {
  const root = String(selectedResourceMap.value.slnic?.remote_path || '').replace(/\/+$/, '')
  return root ? `${root}/tcpdump` : '未配置远端路径'
}

function remWorkdir() {
  return String(selectedResourceMap.value.rem?.remote_path || '').replace(/\/+$/, '') || '未配置远端路径'
}

function updateShellCommands(value: string) {
  if (!selectedNode.value) return
  if (selectedNode.value.node_type !== 'rem_startup' && !slnicNodeTypes.has(selectedNode.value.node_type)) return
  selectedNode.value.config.commands = normalizeShellCommands(value)
  markDirty()
}

function commandTextForNode(node: WorkflowNode | null) {
  if (!node) return ''
  if (node.node_type === 'rem_startup') return remStartupCommandText(node.config.commands)
  if (slnicNodeTypes.has(node.node_type)) return slnicCommandText(node.node_type, node.config.commands)
  return ''
}

function defaultNode(type: string): WorkflowNode {
  const key = makeKey()
  if (type === 'server_config') return { node_key: key, position: 0, node_type: type, name: '获取服务器配置', config: { targets: [] } }
  if (type === 'database_config') return { node_key: key, position: 0, node_type: type, name: '获取数据库配置', config: { database_name: '', keys: [] } }
  if (type === 'wiring_confirmation') {
    const names = wiringInterfaceNameDefaults(selectedPlan.value?.business_code || '')
    return {
      node_key: key,
      position: 0,
      node_type: type,
      name: '接线确认',
      config: { diagram: 'resource', ...names },
    }
  }
  if (type === 'rem_startup') return { node_key: key, position: 0, node_type: type, name: '启动rem柜台', config: { commands: [...DEFAULT_REM_STARTUP_COMMANDS] } }
  if (slnicNodeTypes.has(type)) return { node_key: key, position: 0, node_type: type as WorkflowNodeType, name: nodeMeta(type).label, config: { commands: defaultSlnicCommands(type) } }
  if (type === 'market_startup') return { node_key: key, position: 0, node_type: type, name: '启动模拟市场', config: { scripts: [] } }
  if (type === 'order_preparation') return { node_key: key, position: 0, node_type: type, name: '发单执行', config: { xml_filename: '', xml_checksum: '', network_interface: '', read_symbol_csv: 0, trading_database_name: '', contract_file_ids: [] } }
  if (type === 'parser_parse') return {
    node_key: key,
    position: 0,
    node_type: type,
    name: '数据解析',
    config: {
      database_name: '',
      config_xml_filename: '', config_xml_checksum: '',
      instance_xml_filename: '', instance_xml_checksum: '',
      analysis_xml_filename: '', analysis_xml_checksum: '',
    },
  }
  if (type === 'data_statistics') {
    return {
      node_key: key,
      position: 0,
      node_type: type,
      name: '数据统计',
      config: {
        script_filename: '',
        script_checksum: '',
        max_latency_ns: 999999999,
      },
    }
  }
  return { node_key: key, position: 0, node_type: type as WorkflowNodeType, name: nodeMeta(type).label, config: {} }
}

async function guardNodeChanges() {
  if (!nodeDirty.value) return true
  try {
    await ElMessageBox.confirm(
      '当前节点中有尚未保存的修改，你在继续操作前是否需要保存这些修改？',
      '要保存对节点的修改吗？',
      {
        type: 'warning',
        confirmButtonText: '是，前往保存',
        cancelButtonText: '否，放弃修改',
        distinguishCancelAndClose: true,
      },
    )
    await nextTick()
    document.querySelector<HTMLElement>('#node-save-actions')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    document.querySelector<HTMLButtonElement>('#node-save-actions .el-button--primary')?.focus()
    return false
  } catch (action) {
    if (action === 'cancel') {
      cancelNodeChanges()
      return true
    }
    return false
  }
}

async function selectNode(nodeKey: string) {
  if (nodeKey === selectedKey.value) return
  if (!await guardNodeChanges()) return
  selectedKey.value = nodeKey
  syncNodeForm()
}

async function openPicker(position: number) {
  if (!editable.value) return
  if (!await guardNodeChanges()) return
  insertAt.value = position
  pickerOpen.value = true
}

async function addNode(type: string) {
  if (type === 'report_generation' && nodes.value.some(item => item.node_type === type)) return
  const previousNodes = cloneWorkflowValue(nodes.value)
  const previousKey = selectedKey.value
  const node = defaultNode(type)
  nodes.value.splice(insertAt.value, 0, node)
  normalizePositions()
  selectedKey.value = node.node_key
  pickerOpen.value = false
  syncNodeForm()
  try {
    await saveWorkflow(true)
    ElMessage.success('节点已添加')
    nextTick(() => document.querySelector(`[data-node-key="${node.node_key}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
  } catch {
    nodes.value = previousNodes
    selectedKey.value = previousKey
    syncNodeForm()
  }
}

function normalizePositions() { nodes.value.forEach((item, index) => { item.position = index + 1 }) }
async function moveNode(index: number, offset: number) {
  if (!await guardNodeChanges()) return
  const target = index + offset
  if (target < 0 || target >= nodes.value.length) return
  const previousNodes = cloneWorkflowValue(nodes.value)
  const [item] = nodes.value.splice(index, 1)
  nodes.value.splice(target, 0, item)
  normalizePositions()
  try { await saveWorkflow(true) }
  catch { nodes.value = previousNodes; syncNodeForm() }
}
async function removeNode(index: number) {
  if (!await guardNodeChanges()) return
  try {
    await ElMessageBox.confirm(`确定删除节点“${nodes.value[index].name}”？`, '删除节点', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  const previousNodes = cloneWorkflowValue(nodes.value)
  const previousKey = selectedKey.value
  const [removed] = nodes.value.splice(index, 1)
  normalizePositions()
  if (selectedKey.value === removed.node_key) selectedKey.value = nodes.value[Math.min(index, nodes.value.length - 1)]?.node_key || ''
  syncNodeForm()
  try { await saveWorkflow(true); ElMessage.success('节点已删除') }
  catch { nodes.value = previousNodes; selectedKey.value = previousKey; syncNodeForm() }
}

function updateWiringInterfaceName(
  slot: 'client' | 'market' | 'auxiliary',
  value: string,
  index?: number,
) {
  const node = selectedNode.value
  if (!editable.value || node?.node_type !== 'wiring_confirmation') return
  if (slot === 'client') node.config.client_interface_name = value
  else if (slot === 'market') node.config.market_interface_name = value
  else {
    node.config.auxiliary_interface_names ||= ['', '']
    node.config.auxiliary_interface_names[index ?? 0] = value
  }
  markDirty()
}
async function dropNode(targetIndex: number) {
  if (!await guardNodeChanges()) return
  const sourceIndex = nodes.value.findIndex(item => item.node_key === draggingKey.value)
  if (sourceIndex < 0 || sourceIndex === targetIndex) return
  const previousNodes = cloneWorkflowValue(nodes.value)
  const [item] = nodes.value.splice(sourceIndex, 1)
  nodes.value.splice(targetIndex, 0, item)
  draggingKey.value = ''; normalizePositions()
  try { await saveWorkflow(true) }
  catch { nodes.value = previousNodes; syncNodeForm() }
}

async function saveWorkflow(silent = false) {
  if (!editable.value) return
  if (!resourceIds.value.length) throw new Error('请至少保留一个场景资源')
  saving.value = true
  try {
    const response = await api.put(`/scenarios/${scenarioId}/workflow`, {
      expected_revision: draft.value.revision,
      resource_ids: resourceIds.value,
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

function cancelNodeChanges() {
  stagedNode.reset()
  dirty.value = resourceDirty.value
}

async function saveNode(silent = false) {
  if (!editable.value || !nodeForm.value) return
  const index = nodes.value.findIndex(item => item.node_key === nodeForm.value?.node_key)
  if (index < 0) return
  const previousNodes = cloneWorkflowValue(nodes.value)
  nodes.value[index] = stagedNode.snapshot()!
  try {
    await saveWorkflow(true)
  } catch (error) {
    nodes.value = previousNodes
    throw error
  }
  if (!silent) ElMessage.success('节点配置已保存')
}

function cancelResourceChanges() {
  for (const type of resourceTypes) resourceSelections[type] = null
  for (const id of draft.value?.resource_ids || []) {
    const resource = resources.value.find(item => item.id === id)
    if (resource) resourceSelections[resource.resource_type] = resource.id
  }
  resourceBaseline.value = JSON.stringify(resourceIds.value)
  dirty.value = nodeDirty.value
}

async function saveResourcePool() {
  await saveWorkflow(true)
  ElMessage.success('场景资源池已保存')
}

async function publishWorkflow() {
  if (nodeDirty.value) {
    activeTab.value = 'workflow'
    ElMessage.warning('请先保存当前节点配置')
    await nextTick()
    document.querySelector<HTMLElement>('#node-save-actions')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    return
  }
  if (resourceDirty.value) {
    activeTab.value = 'resources'
    ElMessage.warning('请先保存场景资源池')
    return
  }
  publishing.value = true
  try {
    const response = await api.post(`/scenarios/${scenarioId}/workflow/enable`)
    applyDocument(response.data)
    await loadVersions()
    ElMessage.success('流程已启用')
  } catch (error: any) {
    const errors = error?.response?.data?.errors
    if (errors?.length) {
      selectedKey.value = errors[0].node_key || selectedKey.value
      syncNodeForm()
      activeTab.value = 'workflow'
      ElMessage.error(errors.map((item: any) => item.message).join('；'))
    } else if (!(error instanceof Error && !(error as any).response)) ElMessage.error(errorMessage(error))
  } finally { publishing.value = false }
}

async function pauseWorkflow() {
  try {
    await ElMessageBox.confirm('暂停后将禁止创建新运行，已经进行中的运行不受影响。', '暂停流程', { type: 'warning', confirmButtonText: '暂停流程', cancelButtonText: '取消' })
  } catch { return }
  pausing.value = true
  try {
    const response = await api.post(`/scenarios/${scenarioId}/workflow/pause`)
    applyDocument(response.data)
    await loadVersions()
    ElMessage.success('流程已暂停，可以继续编辑当前版本')
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { pausing.value = false }
}

function versionStatusText(version: any) {
  if (!version) return '加载中'
  if (version.status === 'archived') return '已归档'
  if (version.status === 'draft') return '设计中'
  if (version.status === 'published' && scenario.value?.is_enabled && version.id === scenario.value?.published_workflow_version_id) return '已启用'
  return '历史'
}

function versionTagType(version: any) {
  if (!version) return 'info'
  if (version.status === 'published' && scenario.value?.is_enabled && version.id === scenario.value?.published_workflow_version_id) return 'success'
  if (version.status === 'draft') return 'warning'
  return 'info'
}

async function viewVersion(versionId: number) {
  if (versionId === selectedVersionId.value) return
  if (!await guardNodeChanges()) return
  if (resourceDirty.value) {
    ElMessage.warning('请先保存或取消场景资源池修改')
    activeTab.value = 'resources'
    return
  }
  try {
    if (versionId === currentVersionId.value) {
      const response = await api.get(`/scenarios/${scenarioId}/workflow`)
      applyDocument(response.data)
      return
    }
    const version = (await api.get(`/scenarios/${scenarioId}/workflow/versions/${versionId}`)).data
    applyDocument({ ...documentData.value, draft: version })
  } catch (error) { ElMessage.error(errorMessage(error)) }
}

async function returnToCurrentVersion() {
  if (!currentVersionId.value) return
  await viewVersion(currentVersionId.value)
}

async function createWorkflowVersion() {
  if (!auth.canOperate || scenario.value?.draft_workflow_version_id) return
  if (!selectedVersionId.value) return
  if (!await guardNodeChanges()) return
  versionActionLoading.value = true
  try {
    const response = await api.post(`/scenarios/${scenarioId}/workflow/versions`, { source_version_id: selectedVersionId.value })
    applyDocument(response.data)
    await loadVersions()
    activeTab.value = 'workflow'
    ElMessage.success(`流程版本 v${response.data.draft.version_no} 已创建`)
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { versionActionLoading.value = false }
}

async function archiveSelectedVersion() {
  const version = selectedVersion.value
  if (!canArchiveSelectedVersion.value || !version) return
  try {
    await ElMessageBox.confirm(`归档流程版本 v${version.version_no} 后将只能查看，不能继续编辑。`, '归档流程版本', { type: 'warning', confirmButtonText: '归档', cancelButtonText: '取消' })
  } catch { return }
  versionActionLoading.value = true
  try {
    await api.post(`/scenarios/${scenarioId}/workflow/versions/${version.id}/archive`)
    const scenarioRows = (await api.get('/scenarios', { params: { include_archived: true } })).data || []
    const refreshedScenario = scenarioRows.find((item: any) => item.id === scenarioId)
    if (refreshedScenario) documentData.value.scenario = refreshedScenario
    await loadVersions()
    const fallback = currentVersionId.value || versions.value[0]?.id
    if (fallback) await viewVersion(fallback)
    ElMessage.success('流程版本已归档')
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { versionActionLoading.value = false }
}

async function toggleArchivedVersions(value: boolean) {
  includeArchived.value = value
  await loadVersions()
}

async function beforeTabLeave(nextName: string) {
  if (nextName === activeTab.value) return true
  if (!await guardNodeChanges()) return false
  if (activeTab.value === 'resources' && resourceDirty.value) {
    try {
      await ElMessageBox.confirm('场景资源池有未保存修改，确定放弃并切换？', '未保存修改', { type: 'warning', confirmButtonText: '放弃修改', cancelButtonText: '继续编辑' })
      cancelResourceChanges()
      return true
    } catch { return false }
  }
  return true
}

async function closePropertyPanel() {
  if (!await guardNodeChanges()) return
  selectedKey.value = ''
  syncNodeForm()
}

function targetFor(role: string) { return selectedNode.value?.config.targets?.find((item: any) => item.resource_type === role) }
function toggleServerTarget(role: string, enabled: boolean) {
  const config = selectedNode.value!.config
  config.targets ||= []
  const index = config.targets.findIndex((item: any) => item.resource_type === role)
  if (enabled && index < 0) config.targets.push({
    resource_type: role as 'rem' | 'market' | 'order',
    fields: SERVER_FIELD_OPTIONS[role].map(item => item.value as 'ip' | 'nic_model' | 'machine_model' | 'os_version' | 'cpu_model'),
  })
  if (!enabled && index >= 0) config.targets.splice(index, 1)
  markDirty()
}

async function previewNode() {
  if (!selectedNode.value) return
  previewing.value = true
  try {
    await saveNode(true)
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

async function loadParserConfigs() {
  const resource = selectedResourceMap.value.parser
  if (!resource) { parserConfigs.value = []; return }
  loadingParserConfigs.value = true
  try {
    parserConfigs.value = (await api.get(`/resources/${resource.id}/parser-configs`)).data.files || []
  } catch (error) {
    parserConfigs.value = []
    ElMessage.error(errorMessage(error))
  } finally {
    loadingParserConfigs.value = false
  }
}

async function loadStatisticsScripts() {
  const resource = selectedResourceMap.value.parser
  if (!resource) { statisticsScripts.value = []; return }
  loadingStatisticsScripts.value = true
  try {
    statisticsScripts.value = (await api.get(`/resources/${resource.id}/statistics-scripts`)).data.files || []
  } catch (error) {
    statisticsScripts.value = []
    ElMessage.error(errorMessage(error))
  } finally {
    loadingStatisticsScripts.value = false
  }
}

async function loadMarketScripts() {
  const resource = selectedResourceMap.value.market
  if (!resource) { marketScripts.value = []; return }
  loadingMarketScripts.value = true
  try {
    marketScripts.value = (await api.get(`/resources/${resource.id}/market-scripts`)).data.files || []
  } catch (error) {
    marketScripts.value = []
    ElMessage.error(errorMessage(error))
  } finally {
    loadingMarketScripts.value = false
  }
}

function marketScriptSelected(filename: string) {
  return Boolean(selectedNode.value?.config.scripts?.some((item: any) => item.filename === filename))
}

function toggleMarketScript(script: any, checked: boolean) {
  const node = selectedNode.value
  if (!editable.value || !node || node.node_type !== 'market_startup') return
  const scripts = Array.isArray(node.config.scripts) ? node.config.scripts : []
  node.config.scripts = toggleMarketScriptSelection(scripts, script, checked)
  markDirty()
}

function moveMarketScript(index: number, offset: number) {
  const scripts = selectedNode.value?.config.scripts
  if (!editable.value || !Array.isArray(scripts)) return
  selectedNode.value!.config.scripts = moveMarketScriptSelection(scripts, index, offset)
  markDirty()
}

function removeMarketScript(index: number) {
  const scripts = selectedNode.value?.config.scripts
  if (!editable.value || !Array.isArray(scripts)) return
  scripts.splice(index, 1)
  markDirty()
}

function reconfirmMarketScript(index: number) {
  const row = selectedMarketScriptRows.value[index]
  if (!editable.value || !row?.file?.executable || !row.selection) return
  row.selection.checksum = row.file.checksum
  markDirty()
}

function selectStatisticsScript(filename: string) {
  const node = selectedNode.value
  if (!node || node.node_type !== 'data_statistics') return
  const script = statisticsScripts.value.find(item => item.name === filename && item.executable)
  node.config.script_filename = script?.name || ''
  node.config.script_checksum = script?.checksum || ''
  markDirty()
}

function ensureStatisticsScriptSelection() {
  const node = selectedNode.value
  if (!editable.value || !node || node.node_type !== 'data_statistics') return
  const script = statisticsScripts.value.find(item => item.name === node.config.script_filename && item.executable)
  if (!script || (node.config.script_checksum && node.config.script_checksum !== script.checksum)) {
    node.config.script_filename = ''
    node.config.script_checksum = ''
    markDirty()
  } else if (!node.config.script_checksum) {
    node.config.script_checksum = script.checksum
    markDirty()
  }
}

const parserXmlFields: Record<ParserXmlRole, { filename: string; checksum: string }> = {
  config: { filename: 'config_xml_filename', checksum: 'config_xml_checksum' },
  instance: { filename: 'instance_xml_filename', checksum: 'instance_xml_checksum' },
  analysis: { filename: 'analysis_xml_filename', checksum: 'analysis_xml_checksum' },
}

async function selectParserXml(role: ParserXmlRole, filename: string, quiet = false) {
  const resource = selectedResourceMap.value.parser
  const node = selectedNode.value
  if (!resource || !node || node.node_type !== 'parser_parse') return
  const fields = parserXmlFields[role]
  const config = node.config as Record<string, any>
  if (!filename) {
    config[fields.filename] = ''
    config[fields.checksum] = ''
    markDirty()
    return
  }
  try {
    const detail = (await api.get(`/resources/${resource.id}/parser-configs/${encodeURIComponent(filename)}`)).data
    config[fields.filename] = detail.name
    config[fields.checksum] = detail.checksum
    markDirty()
  } catch (error) {
    config[fields.filename] = ''
    config[fields.checksum] = ''
    if (!quiet) ElMessage.error(errorMessage(error))
  }
}

async function ensureParserXmlSelections() {
  const node = selectedNode.value
  const resource = selectedResourceMap.value.parser
  if (!editable.value || !node || node.node_type !== 'parser_parse' || !resource) return
  const config = node.config as Record<string, any>
  const defaults: Record<ParserXmlRole, string> = {
    config: parserXmlOptions.value.config.find(file => file.name === 'config.xml')?.name || parserXmlOptions.value.config[0]?.name || '',
    instance: parserXmlOptions.value.instance.find(file => file.name === 'instance.xml')?.name || parserXmlOptions.value.instance[0]?.name || '',
    analysis: parserXmlOptions.value.analysis.find(file => file.name === resource.capabilities?.parser_config_filename)?.name || parserXmlOptions.value.analysis[0]?.name || '',
  }
  for (const role of ['config', 'instance', 'analysis'] as ParserXmlRole[]) {
    const fields = parserXmlFields[role]
    const current = String(config[fields.filename] || '')
    const valid = parserXmlOptions.value[role].some(file => file.name === current)
    if (!valid) {
      config[fields.filename] = ''
      config[fields.checksum] = ''
    }
    const filename = valid ? current : defaults[role]
    if (filename && (!valid || !config[fields.checksum])) await selectParserXml(role, filename, true)
  }
}

function xmlFlag(document: any): 0 | 1 {
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
    markDirty()
  } catch (error) { ElMessage.error(errorMessage(error)) }
}

async function loadContractFiles(throwOnError = false, scanRemote = editable.value) {
  const requestId = ++contractFilesRequestId
  const node = selectedNode.value
  if (!node || node.node_type !== 'order_preparation') { contractFiles.value = []; return }
  const nodeKey = node.node_key
  scanningContracts.value = scanRemote
  try {
    const response = scanRemote
      ? await api.post(`/scenarios/${scenarioId}/workflow/nodes/${nodeKey}/contract-files/scan`)
      : await api.get(`/scenarios/${scenarioId}/workflow/nodes/${nodeKey}/contract-files`)
    if (requestId === contractFilesRequestId && selectedNode.value?.node_key === nodeKey) contractFiles.value = response.data
  } catch (error) {
    if (requestId !== contractFilesRequestId || selectedNode.value?.node_key !== nodeKey) return
    if (!throwOnError) contractFiles.value = []
    else throw error
  } finally {
    if (requestId === contractFilesRequestId) scanningContracts.value = false
  }
}

async function refreshContractFiles() {
  try {
    await loadContractFiles(true, true)
    ElMessage.success('已刷新发单目录中的 CSV 文件')
  } catch (error) { ElMessage.error(errorMessage(error)) }
}

async function fetchContracts(contractTypes: string[]) {
  const database = selectedResourceMap.value.database
  const nodeKey = selectedNode.value?.node_key
  const databaseName = selectedNode.value?.config.trading_database_name
  if (!database || !nodeKey || !databaseName) { ElMessage.warning('请先确认交易数据库'); return }
  fetchingContracts.value = true
  try {
    await saveNode(true)
    const response = await api.post(`/scenarios/${scenarioId}/workflow/nodes/${nodeKey}/contract-files/fetch`, {
      database_resource_id: database.id, database_name: databaseName, contract_types: contractTypes,
    })
    if (selectedNode.value?.node_key === nodeKey) {
      selectedNode.value.config.contract_file_ids = [...new Set([...(selectedNode.value.config.contract_file_ids || []), ...response.data.map((item: any) => item.id)])]
      markDirty()
    }
    if (selectedKey.value === nodeKey) await loadContractFiles(true)
    ElMessage.success('最新交易日合约数据已生成并归档')
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { fetchingContracts.value = false }
}

watch(selectedNode, async node => {
  previewSnapshots.value = []
  globalKeySearch.value = ''
  selectedDatabaseConfigTemplateId.value = null
  shellCommandEditorText.value = commandTextForNode(node || null)
  if (node?.node_type === 'database_config') await loadDatabaseConfigTemplates()
  if (node?.node_type === 'order_preparation') { await loadOrderConfigs(); await loadContractFiles() }
  if (node?.node_type === 'parser_parse') { await loadParserConfigs(); await ensureParserXmlSelections() }
  if (node?.node_type === 'data_statistics') { await loadStatisticsScripts(); ensureStatisticsScriptSelection() }
  if (node?.node_type === 'market_startup') await loadMarketScripts()
})
watch(
  [
    () => selectedNode.value?.node_type,
    () => selectedNode.value?.config.database_name,
    () => selectedResourceMap.value.database?.id,
  ],
  async () => {
    await loadDatabaseConfigItems()
  },
)
watch(() => selectedResourceMap.value.parser?.id, async () => {
  if (selectedNode.value?.node_type === 'parser_parse') {
    await loadParserConfigs()
    await ensureParserXmlSelections()
  }
  if (selectedNode.value?.node_type === 'data_statistics') {
    await loadStatisticsScripts()
    ensureStatisticsScriptSelection()
  }
})
watch(() => selectedResourceMap.value.market?.id, async () => {
  if (selectedNode.value?.node_type === 'market_startup') await loadMarketScripts()
})

onBeforeRouteLeave(async () => {
  if (!nodeDirty.value && !resourceDirty.value && !dirty.value) return true
  try { await ElMessageBox.confirm('工作流有未保存修改，确定放弃修改并离开？', '未保存修改', { type: 'warning', confirmButtonText: '放弃并离开', cancelButtonText: '继续编辑' }); return true }
  catch { return false }
})
function protectBrowserLeave(event: BeforeUnloadEvent) {
  if (!nodeDirty.value && !resourceDirty.value && !dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  window.addEventListener('beforeunload', protectBrowserLeave)
  void load()
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', protectBrowserLeave))
</script>

<template>
  <div v-loading="loading" class="workflow-page">
    <header class="workflow-header">
      <div class="header-left">
        <el-button text circle :icon="ArrowLeft" aria-label="返回方案与场景" @click="router.push({ path: '/plans', query: plansReturnQuery })" />
        <div>
          <div class="title-line"><h1>{{ scenario?.name || '工作流设置' }}</h1><span v-if="nodeDirty || resourceDirty" class="dirty-mark">有未保存修改</span></div>
          <el-popover placement="bottom-start" :width="300" trigger="click">
            <template #reference>
              <button class="version-trigger" type="button">
                <span>流程版本 v{{ selectedVersion?.version_no || 1 }}</span>
                <el-tag size="small" :type="versionTagType(selectedVersion)">{{ versionStatusText(selectedVersion) }}</el-tag>
                <el-icon><Bottom /></el-icon>
              </button>
            </template>
            <div class="version-manager" v-loading="versionsLoading || versionActionLoading">
              <div class="version-manager-title"><strong>版本管理</strong><el-switch :model-value="includeArchived" size="small" inline-prompt active-text="含归档" inactive-text="当前" @change="value => toggleArchivedVersions(Boolean(value))" /></div>
              <div class="version-list">
                <button v-for="version in versions" :key="version.id" type="button" :class="{ active: selectedVersionId === version.id }" @click="viewVersion(version.id)">
                  <span><strong>流程版本 v{{ version.version_no }}</strong><small>{{ version.updated_at?.slice(0, 16).replace('T', ' ') }}</small></span>
                  <el-tag size="small" :type="versionTagType(version)">{{ versionStatusText(version) }}</el-tag>
                </button>
              </div>
              <div class="version-manager-actions">
                <el-button :icon="Plus" :disabled="!auth.canOperate || Boolean(scenario?.draft_workflow_version_id) || !selectedVersionId" @click="createWorkflowVersion">新增版本</el-button>
                <el-button :icon="Files" :disabled="!canArchiveSelectedVersion" @click="archiveSelectedVersion">归档</el-button>
              </div>
            </div>
          </el-popover>
        </div>
      </div>
      <div class="header-actions">
        <el-button v-if="!viewingCurrentVersion && currentVersionId" @click="returnToCurrentVersion">返回当前版本</el-button>
        <template v-else-if="auth.canOperate">
          <el-button v-if="scenario?.is_enabled" type="warning" :loading="pausing" :icon="VideoPause" @click="pauseWorkflow">暂停流程</el-button>
          <el-button v-else type="primary" :loading="publishing" :icon="Check" :disabled="!scenario?.draft_workflow_version_id && !scenario?.published_workflow_version_id" @click="publishWorkflow">启用流程</el-button>
        </template>
      </div>
    </header>

    <el-tabs v-model="activeTab" class="workflow-tabs" :before-leave="beforeTabLeave">
      <el-tab-pane label="场景资源池" name="resources">
        <section class="resource-tab">
          <div class="resource-tab-heading"><div><h2>场景资源池</h2><p>为当前流程绑定运行资源，每种类型最多选择一个。</p></div><el-tag v-if="!editable" type="info" effect="plain">只读</el-tag></div>
          <div class="resource-fields">
            <label v-for="type in resourceTypes" :key="type"><span>{{ resourceText[type] || type }}</span><el-select v-model="resourceSelections[type]" clearable filterable :disabled="!editable" placeholder="未绑定" @change="markDirty"><el-option v-for="resource in resourceOptions(type)" :key="resource.id" :label="resource.name" :value="resource.id" /></el-select></label>
          </div>
          <div class="resource-note"><strong>启用校验</strong><span>节点只能引用资源池中的角色；正式运行可替换同类型资源。</span></div>
          <div v-if="editable" class="resource-actions"><el-button :disabled="!resourceDirty" @click="cancelResourceChanges">取消</el-button><el-button type="primary" :loading="saving" :disabled="!resourceDirty" @click="saveResourcePool">保存</el-button></div>
        </section>
      </el-tab-pane>
      <el-tab-pane label="流程编辑" name="workflow">
        <div class="editor-grid">
          <main class="workflow-canvas">
        <div class="canvas-intro"><strong>主流程</strong><span>拖拽节点调整顺序，点击节点编辑属性</span></div>
        <div class="flow-column">
          <button v-if="editable" class="add-point" type="button" aria-label="在流程开头添加节点" @click="openPicker(0)"><el-icon><Plus /></el-icon></button>
          <div v-if="!nodes.length" class="flow-empty"><el-icon><Tickets /></el-icon><strong>还没有流程节点</strong><span>点击加号添加第一个节点</span></div>
          <template v-for="(node, index) in nodes" :key="node.node_key">
            <article :data-node-key="node.node_key" class="flow-node" :class="[{ selected: selectedKey === node.node_key }, nodeMeta(node.node_type).tone]" :draggable="editable" @dragstart="draggingKey = node.node_key" @dragover.prevent @drop="dropNode(index)" @click="selectNode(node.node_key)">
              <div class="node-icon"><el-icon><component :is="nodeMeta(node.node_type).icon" /></el-icon></div>
              <div class="node-copy"><span>{{ nodeMeta(node.node_type).label }}</span><strong>{{ node.name }}</strong><small v-if="node.node_type === 'server_config'">{{ node.config.targets?.length || 0 }} 台服务器</small><small v-else-if="node.node_type === 'database_config'">{{ node.config.keys?.length || 0 }} 个配置项</small><small v-else-if="node.node_type === 'wiring_confirmation'">需要人工确认</small><small v-else-if="node.node_type === 'rem_startup'">{{ selectedResourceMap.rem?.name || '未绑定 REM 资源' }}</small><small v-else-if="node.node_type === 'market_startup'">{{ node.config.scripts?.length || 0 }} 个启动脚本</small><small v-else-if="node.node_type === 'order_preparation'">{{ node.config.xml_filename || '未选择 XML' }}</small><small v-else-if="node.node_type === 'parser_parse'">{{ node.config.database_name || '未选择运行数据库' }}</small><small v-else-if="node.node_type === 'data_statistics'">{{ node.config.script_filename || '未选择统计脚本' }}</small><small v-else-if="node.node_type === 'report_generation'">HTML · Excel · PDF</small><small v-else-if="slnicNodeTypes.has(node.node_type)">{{ selectedResourceMap.slnic?.name || '未绑定 SLNIC 资源' }}</small></div>
              <div v-if="editable" class="node-actions"><el-button text circle :icon="Top" :disabled="index === 0" aria-label="上移节点" @click.stop="moveNode(index, -1)" /><el-button text circle :icon="Bottom" :disabled="index === nodes.length - 1" aria-label="下移节点" @click.stop="moveNode(index, 1)" /><el-button text circle type="danger" :icon="Delete" aria-label="删除节点" @click.stop="removeNode(index)" /></div>
            </article>
            <div class="flow-link"><span></span><button v-if="editable" class="add-point" type="button" aria-label="在此处添加节点" @click="openPicker(index + 1)"><el-icon><Plus /></el-icon></button></div>
          </template>
          <div v-if="nodes.length" class="flow-end"><span></span>结束流程</div>
        </div>
          </main>

          <aside class="property-panel" :class="{ open: Boolean(selectedNode) }">
        <template v-if="selectedNode">
          <div class="property-title"><div class="node-icon" :class="nodeMeta(selectedNode.node_type).tone"><el-icon><component :is="nodeMeta(selectedNode.node_type).icon" /></el-icon></div><div><strong>节点属性</strong><small>{{ nodeMeta(selectedNode.node_type).label }}</small></div><el-button class="property-close" text circle :icon="Close" aria-label="关闭节点属性" @click="closePropertyPanel" /></div>
          <label class="field"><span>节点名称</span><el-input v-model="selectedNode.name" :disabled="!editable" maxlength="128" @input="markDirty" /></label>

          <template v-if="selectedNode.node_type === 'server_config'">
            <div class="section-label">采集服务器与字段</div>
            <div v-for="role in ['rem', 'market', 'order']" :key="role" class="target-box" :class="{ disabled: !selectedResourceMap[role] }">
              <el-checkbox :model-value="Boolean(targetFor(role))" :disabled="!editable || !selectedResourceMap[role]" @change="value => toggleServerTarget(role, Boolean(value))"><strong>{{ resourceText[role] }}</strong><small>{{ selectedResourceMap[role]?.name || '资源池未绑定' }}</small></el-checkbox>
              <el-checkbox-group v-if="targetFor(role)" v-model="targetFor(role)!.fields" :disabled="!editable" @change="markDirty"><el-checkbox v-for="field in SERVER_FIELD_OPTIONS[role]" :key="field.value" :label="field.value">{{ field.label }}</el-checkbox></el-checkbox-group>
            </div>
            <el-button :icon="Refresh" :loading="previewing" :disabled="!editable" @click="previewNode">预采集并保存</el-button>
          </template>

          <template v-else-if="selectedNode.node_type === 'database_config'">
            <label class="field"><span>配置数据库</span><el-select v-model="selectedNode.config.database_name" :disabled="!editable" filterable @change="selectDatabaseConfigDatabase"><el-option v-for="name in selectedResourceMap.database?.database_names || []" :key="name" :label="name" :value="name" /></el-select></label>
            <template v-if="editable">
              <div class="section-label">个人模板</div>
              <div class="template-toolbar">
                <el-select v-model="selectedDatabaseConfigTemplateId" :loading="databaseConfigTemplatesLoading" clearable placeholder="选择模板快速应用" :disabled="!databaseConfigCatalogLoaded" @change="applySelectedDatabaseConfigTemplate">
                  <el-option v-for="item in databaseConfigTemplates" :key="item.id" :label="`${item.name}（${item.keys.length} 项）`" :value="item.id" />
                </el-select>
                <el-button :icon="Plus" circle title="保存当前选择为模板" aria-label="保存当前选择为模板" :disabled="!(selectedNode.config.keys?.length)" @click="saveDatabaseConfigTemplate" />
                <el-button :icon="Edit" circle title="重命名模板" aria-label="重命名模板" :disabled="!selectedDatabaseConfigTemplateId" @click="renameDatabaseConfigTemplate" />
                <el-button :icon="Delete" circle type="danger" title="删除模板" aria-label="删除模板" :disabled="!selectedDatabaseConfigTemplateId" @click="deleteDatabaseConfigTemplate" />
              </div>
            </template>
            <div class="section-label">t_global_settings 配置项</div>
            <div class="key-toolbar"><el-input v-model="globalKeySearch" :prefix-icon="Search" clearable placeholder="搜索键名或描述" /><el-button size="small" :disabled="!editable || !databaseConfigItems.length" @click="toggleAllGlobalKeys">{{ allGlobalKeysSelected ? '取消全选' : '全选' }}</el-button></div>
            <el-alert v-if="databaseConfigCatalogError" class="catalog-alert" :title="databaseConfigCatalogError" type="error" :closable="false" show-icon><el-button size="small" @click="loadDatabaseConfigItems">重试</el-button></el-alert>
            <div v-if="staleGlobalKeys.length" class="stale-key-row"><span>{{ staleGlobalKeys.length }} 个已选配置项在当前数据库中不存在</span><el-button size="small" type="warning" plain @click="removeStaleGlobalKeys">清理失效项</el-button></div>
            <div class="key-grid" v-loading="databaseConfigCatalogLoading"><el-checkbox-group v-if="filteredGlobalKeys.length" v-model="selectedNode.config.keys" class="key-options" :disabled="!editable" @change="markDirty"><el-checkbox v-for="item in filteredGlobalKeys" :key="item.key" :label="item.key"><span class="key-option-copy"><strong>{{ item.key }}</strong><small>{{ item.description || '暂无描述' }}</small></span></el-checkbox></el-checkbox-group><div v-else class="key-grid-empty">{{ databaseConfigCatalogLoading ? '正在读取配置项' : '无匹配配置项' }}</div></div>
            <el-button :icon="Refresh" :loading="previewing" :disabled="!editable" @click="previewNode">预采集并保存</el-button>
          </template>

          <template v-else-if="selectedNode.node_type === 'wiring_confirmation'">
            <div class="wiring-editor-heading">
              <div><strong>动态接线图</strong><span>运行时按实际选择的 REM 与 SLNIC 固化快照</span></div>
              <el-button v-if="selectedNode.config.diagram === 'placeholder' && editable" size="small" type="primary" plain @click="selectedNode.config.diagram = 'resource'; markDirty()">升级为动态图</el-button>
              <el-tag v-else-if="selectedNode.config.diagram === 'placeholder'" type="info" effect="plain">旧版占位图</el-tag>
            </div>
            <el-alert v-if="selectedNode.config.diagram === 'placeholder'" title="该节点仍使用旧版占位图，创建新草稿后可升级" type="info" :closable="false" show-icon />
            <el-alert v-else-if="wiringValidationMessage" :title="wiringValidationMessage" type="warning" :closable="false" show-icon />
            <WiringTopologyDiagram
              :snapshot="selectedNode.config.diagram === 'resource' ? wiringPreview : null"
              :editable="editable && selectedNode.config.diagram === 'resource'"
              compact
              :empty-message="selectedNode.config.diagram === 'placeholder' ? '旧版节点未绑定资源接线图' : wiringValidationMessage"
              @interface-name-change="updateWiringInterfaceName"
            />
          </template>

          <template v-else-if="selectedNode.node_type === 'order_preparation'">
            <label class="field required"><span>XML 配置</span><el-select v-model="selectedNode.config.xml_filename" :disabled="!editable || !selectedResourceMap.order" filterable @change="selectXml"><el-option v-for="file in orderConfigs" :key="file.name" :label="file.name" :value="file.name" /></el-select></label>
            <label class="field"><span>网卡接口</span><el-input v-model="selectedNode.config.network_interface" :disabled="!editable" placeholder="例如 p4p1" maxlength="15" @input="markDirty" /><small>运行时安全生成 ZF_ATTR。</small></label>
            <el-alert :title="selectedNode.config.read_symbol_csv ? 'XML 需要合约 CSV' : 'XML 未启用 read_symbol_csv'" :type="selectedNode.config.read_symbol_csv ? 'warning' : 'info'" :closable="false" show-icon />
            <template v-if="selectedNode.config.read_symbol_csv">
              <label class="field"><span>交易数据库</span><el-select v-model="selectedNode.config.trading_database_name" :disabled="!editable" filterable @change="markDirty"><el-option v-for="name in selectedResourceMap.database?.database_names || []" :key="name" :label="name" :value="name" /></el-select><small>从数据库资源白名单中选择对应的 *_trading_data 库。</small></label>
              <div class="contract-toolbar"><strong>合约数据</strong><div><el-button size="small" :icon="Refresh" :loading="scanningContracts" :disabled="!editable || fetchingContracts" circle aria-label="刷新目录 CSV" @click="refreshContractFiles" /><el-button size="small" :loading="fetchingContracts" :disabled="!editable || scanningContracts" @click="fetchContracts(['futures'])">获取期货</el-button><el-button size="small" :loading="fetchingContracts" :disabled="!editable || scanningContracts" @click="fetchContracts(['options'])">获取期权</el-button></div></div>
              <el-checkbox-group v-if="contractFiles.length" v-model="selectedNode.config.contract_file_ids" class="contract-list" :disabled="!editable" @change="markDirty"><el-checkbox v-for="file in contractFiles" :key="file.id" :label="file.id"><span><strong>{{ file.filename }}</strong><small>{{ file.contract_type === 'futures' ? '期货' : file.contract_type === 'options' ? '期权' : '未识别类型' }} · {{ file.quote_date || '无交易日' }} · {{ file.row_count }} 条 · {{ file.database_resource_id ? '数据库生成' : '目录已有' }}</small></span></el-checkbox></el-checkbox-group>
              <div v-else v-loading="scanningContracts" class="contract-empty">发单工具目录下暂无 CSV 文件</div>
              <el-collapse v-if="selectedContractFiles.length" class="contract-previews">
                <el-collapse-item v-for="file in selectedContractFiles" :key="file.id" :name="file.id">
                  <template #title><span class="contract-preview-title"><strong>{{ file.contract_type === 'futures' ? '期货' : file.contract_type === 'options' ? '期权' : '未识别类型' }}</strong><small>{{ file.quote_date || '无交易日' }} · {{ file.row_count }} 条</small></span></template>
                  <div class="checksum"><span>SHA-256</span><code>{{ file.checksum }}</code></div>
                  <el-table :data="file.preview_rows" size="small" max-height="210" border>
                    <el-table-column v-for="column in previewColumns(file)" :key="column" :prop="column" :label="column" min-width="120" show-overflow-tooltip />
                  </el-table>
                </el-collapse-item>
              </el-collapse>
            </template>
          </template>

          <template v-else-if="selectedNode.node_type === 'rem_startup'">
            <div class="slnic-summary">
              <div><span>REM 柜台资源</span><strong>{{ selectedResourceMap.rem?.name || '资源池未绑定' }}</strong></div>
              <div><span>执行模式</span><strong>可配置交互 Shell 命令</strong></div>
              <div class="wide"><span>工作目录</span><code>{{ remWorkdir() }}</code></div>
            </div>
            <el-alert v-if="!selectedResourceMap.rem" title="请先在左侧场景资源池绑定 REM 柜台" type="warning" :closable="false" show-icon />
            <el-alert v-if="!shellCommandCount" title="至少输入一条 REM 启动命令后才能发布" type="warning" :closable="false" show-icon />
            <div class="section-label">执行命令（按顺序）</div>
            <el-input
              v-model="shellCommandEditorText"
              type="textarea"
              :rows="7"
              :readonly="!editable"
              :maxlength="32768"
              resize="vertical"
              spellcheck="false"
              class="rem-command-editor"
              placeholder="一行一条 Shell 命令"
              @input="value => updateShellCommands(String(value))"
            />
            <p class="slnic-note">空白行会被忽略。全部命令会在同一个交互 Shell 中逐行下发，前一行的 cd、export 会影响后续命令；系统不因非零退出自动截断，请在终端确认后手动完成节点。</p>
          </template>

          <template v-else-if="selectedNode.node_type === 'market_startup'">
            <div class="slnic-summary">
              <div><span>模拟市场资源</span><strong>{{ selectedResourceMap.market?.name || '资源池未绑定' }}</strong></div>
              <div><span>执行模式</span><strong>顺序执行远程脚本</strong></div>
              <div class="wide"><span>工作目录</span><code>{{ selectedResourceMap.market?.remote_path || '-' }}</code></div>
            </div>
            <el-alert v-if="!selectedResourceMap.market" title="请先在左侧场景资源池绑定模拟市场资源" type="warning" :closable="false" show-icon />
            <div class="contract-toolbar"><strong>根目录 .sh 文件</strong><el-button size="small" :icon="Refresh" :loading="loadingMarketScripts" :disabled="!selectedResourceMap.market" circle aria-label="刷新模拟市场脚本" @click="loadMarketScripts" /></div>
            <div v-loading="loadingMarketScripts" class="market-script-options">
              <el-checkbox
                v-for="script in marketScripts"
                :key="script.name"
                :model-value="marketScriptSelected(script.name)"
                :disabled="!editable || (!script.executable && !marketScriptSelected(script.name))"
                @change="value => toggleMarketScript(script, Boolean(value))"
              >
                <span><strong>{{ script.name }}</strong><small>{{ script.executable ? '可执行' : '无执行权限，不能选择' }}</small></span>
              </el-checkbox>
              <div v-if="!loadingMarketScripts && !marketScripts.length" class="contract-empty">根目录下暂无 .sh 文件</div>
            </div>
            <div class="section-label">执行顺序</div>
            <div v-if="selectedMarketScriptRows.length" class="market-script-order">
              <div v-for="(row, index) in selectedMarketScriptRows" :key="row.selection.filename" class="market-script-row" :class="{ invalid: row.status !== 'valid' }">
                <span class="market-script-index">{{ index + 1 }}</span>
                <div><strong>{{ row.selection.filename }}</strong><small v-if="row.status === 'missing'">远端文件已不存在</small><small v-else-if="row.status === 'not_executable'">远端文件已失去执行权限</small><small v-else-if="row.status === 'changed'">远端文件内容已变化，请重新确认</small><small v-else>校验和已固化</small></div>
                <div class="market-script-actions">
                  <el-button v-if="row.status === 'changed'" link type="warning" :disabled="!editable" @click="reconfirmMarketScript(index)">重新确认</el-button>
                  <el-button text circle :icon="Top" :disabled="!editable || index === 0" aria-label="上移脚本" @click="moveMarketScript(index, -1)" />
                  <el-button text circle :icon="Bottom" :disabled="!editable || index === selectedMarketScriptRows.length - 1" aria-label="下移脚本" @click="moveMarketScript(index, 1)" />
                  <el-button text circle type="danger" :icon="Delete" :disabled="!editable" aria-label="移除脚本" @click="removeMarketScript(index)" />
                </div>
              </div>
            </div>
            <div v-else class="contract-empty">至少选择一个启动脚本</div>
            <p class="slnic-note">任一脚本失败都会停止后续执行；重试节点时从第一个脚本重新开始。</p>
          </template>

          <template v-else-if="slnicNodeTypes.has(selectedNode.node_type)">
            <div class="slnic-summary">
              <div><span>SLNIC 资源</span><strong>{{ selectedResourceMap.slnic?.name || '资源池未绑定' }}</strong></div>
              <div><span>执行模式</span><strong>可配置交互 Shell 命令</strong></div>
              <div class="wide"><span>工作目录</span><code>{{ slnicWorkdir() }}</code></div>
            </div>
            <el-alert v-if="!selectedResourceMap.slnic" title="请先在左侧场景资源池绑定 SLNIC 节点" type="warning" :closable="false" show-icon />
            <el-alert v-if="!shellCommandCount" title="至少输入一条 SLNIC 命令后才能发布" type="warning" :closable="false" show-icon />
            <div class="section-label">执行命令（按顺序）</div>
            <el-input
              v-model="shellCommandEditorText"
              type="textarea"
              :rows="7"
              :readonly="!editable"
              :maxlength="32768"
              resize="vertical"
              spellcheck="false"
              class="rem-command-editor"
              placeholder="一行一条 Shell 命令"
              @input="value => updateShellCommands(String(value))"
            />
            <p class="slnic-note">空白行会被忽略。全部命令会在同一个交互 Shell 中逐行下发并共享状态；系统不因非零退出自动截断，请在终端确认后手动完成节点。</p>
          </template>
          <template v-else-if="selectedNode.node_type === 'parser_parse'">
            <label class="field required"><span>运行数据库</span><el-select v-model="selectedNode.config.database_name" :disabled="!editable || !selectedResourceMap.database" filterable @change="markDirty"><el-option v-for="name in selectedResourceMap.database?.database_names || []" :key="name" :label="name" :value="name" /></el-select><small>三张订单表从该 *_trading_data 库导出；t_account_exchange_code 从同前缀 *_config 库导出。</small></label>
            <div class="section-label">解析 XML</div>
            <label class="field required">
              <span>config.xml 配置</span>
              <el-select v-model="selectedNode.config.config_xml_filename" :loading="loadingParserConfigs" :disabled="!editable || !selectedResourceMap.parser" filterable @change="value => selectParserXml('config', String(value || ''))"><el-option v-for="file in parserXmlOptions.config" :key="file.name" :label="file.name" :value="file.name" /></el-select>
            </label>
            <label class="field required">
              <span>instance.xml 配置</span>
              <el-select v-model="selectedNode.config.instance_xml_filename" :loading="loadingParserConfigs" :disabled="!editable || !selectedResourceMap.parser" filterable @change="value => selectParserXml('instance', String(value || ''))"><el-option v-for="file in parserXmlOptions.instance" :key="file.name" :label="file.name" :value="file.name" /></el-select>
            </label>
            <label class="field required">
              <span>分析主配置</span>
              <el-select v-model="selectedNode.config.analysis_xml_filename" :loading="loadingParserConfigs" :disabled="!editable || !selectedResourceMap.parser" filterable @change="value => selectParserXml('analysis', String(value || ''))"><el-option v-for="file in parserXmlOptions.analysis" :key="file.name" :label="file.name" :value="file.name" /></el-select>
            </label>
            <div class="section-label">解析资源</div>
            <div class="slnic-summary">
              <div><span>解析工具</span><strong>{{ selectedResourceMap.parser?.capabilities?.parser_binary || '未绑定解析资源' }}</strong></div>
              <div><span>远端路径</span><strong class="mono">{{ selectedResourceMap.parser?.remote_path || '-' }}</strong></div>
              <div><span>资源默认主配置</span><strong class="mono">{{ selectedResourceMap.parser?.capabilities?.parser_config_filename || '-' }}</strong></div>
            </div>
            <el-alert v-if="!selectedResourceMap.parser || !selectedResourceMap.database" title="请先在左侧资源池绑定解析工具和数据库资源" type="warning" :closable="false" show-icon />
            <el-alert title="运行时会查找本次运行已经生成的 merge_pcap.pcapng，并上传三份订单 CSV。若输入尚未生成，节点执行时会明确失败。" type="info" :closable="false" show-icon />
          </template>
          <template v-else-if="selectedNode.node_type === 'data_statistics'">
            <label class="field required">
              <span>交易所统计脚本</span>
              <el-select v-model="selectedNode.config.script_filename" :loading="loadingStatisticsScripts" :disabled="!editable || !selectedResourceMap.parser" filterable @change="value => selectStatisticsScript(String(value || ''))">
                <el-option v-for="script in statisticsScripts" :key="script.name" :label="script.executable ? script.name : `${script.name}（不可执行）`" :value="script.name" :disabled="!script.executable" />
              </el-select>
            </label>
            <label class="field required">
              <span>异常大值上限（ns）</span>
              <el-input-number v-model="selectedNode.config.max_latency_ns" :disabled="!editable" :min="1" :precision="0" controls-position="right" style="width:100%" @change="markDirty" />
              <small>作为第三个命令行参数传给统计脚本，默认 999999999。</small>
            </label>
            <div class="slnic-summary">
              <div><span>解析资源</span><strong>{{ selectedResourceMap.parser?.name || '未绑定解析资源' }}</strong></div>
              <div><span>执行方式</span><strong>直接读取远端 CSV · JSON 输出</strong></div>
              <div class="wide"><span>脚本目录</span><code>{{ selectedResourceMap.parser?.remote_path || '-' }}</code></div>
            </div>
            <el-alert title="运行到该节点后，在运行详情页选择前一个最近成功解析节点生成的 CSV。" type="info" :closable="false" show-icon />
          </template>
          <template v-else-if="selectedNode.node_type === 'report_generation'">
            <div class="section-label">自动汇总范围</div>
            <div class="slnic-summary">
              <div><span>配置数据</span><strong>服务器、数据库、发单 XML</strong></div>
              <div><span>测速数据</span><strong>执行时已有的统计结果</strong></div>
              <div class="wide"><span>报告格式</span><strong>HTML · Excel · PDF</strong></div>
            </div>
            <el-alert title="报告会汇总执行到该节点时已经产生的数据；缺少的可选章节会标记为无数据。" type="info" :closable="false" show-icon />
          </template>

          <div v-if="previewSnapshots.length" class="preview-results"><div class="section-label">最近预采集结果</div><div v-for="snapshot in previewSnapshots" :key="snapshot.id" class="snapshot"><div><strong>{{ snapshot.source_type === 'server' ? `资源 #${snapshot.resource_id}` : snapshot.database_name }}</strong><el-tag size="small" :type="snapshot.status === 'succeeded' ? 'success' : 'danger'">{{ snapshot.status === 'succeeded' ? '成功' : '失败' }}</el-tag></div><dl><template v-for="item in snapshot.items" :key="item.id"><dt>{{ item.item_label }}</dt><dd :class="{ failed: item.status === 'failed' }">{{ item.value_text || item.error_message || '-' }}</dd></template></dl></div></div>
          <div v-if="editable" id="node-save-actions" class="node-save-actions"><el-button :disabled="!nodeDirty" @click="cancelNodeChanges">取消</el-button><el-button type="primary" :loading="saving" :disabled="!nodeDirty" @click="saveNode()">保存</el-button></div>
        </template>
        <div v-else class="property-empty"><el-icon><Tickets /></el-icon><strong>选择一个节点</strong><span>节点配置和预览结果会显示在这里</span></div>
          </aside>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-drawer v-model="pickerOpen" title="选择一个节点" size="360px" append-to-body>
      <div class="node-catalog">
        <section v-for="category in nodeCategories" :key="category.title" class="node-category">
          <h3>{{ category.title }}</h3>
          <div class="node-picker"><button v-for="type in category.types" :key="type" type="button" :disabled="type === 'report_generation' && nodes.some(item => item.node_type === type)" @click="addNode(type)"><span class="node-icon" :class="nodeMeta(type).tone"><el-icon><component :is="nodeMeta(type).icon" /></el-icon></span><span><strong>{{ nodeMeta(type).label }}</strong><small>{{ type === 'report_generation' && nodes.some(item => item.node_type === type) ? '工作流中已存在报告节点' : nodeDescription(type) }}</small></span></button></div>
        </section>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.workflow-page{min-height:calc(100vh - 58px);background:#edf2f4;color:#1d2c34}.workflow-header{height:78px;padding:0 20px;background:#fff;border-bottom:1px solid #dce4e8;display:flex;align-items:center;justify-content:space-between}.header-left,.title-line,.header-actions,.property-title,.contract-toolbar{display:flex;align-items:center}.header-left{gap:10px}.header-left h1{font-size:18px;margin:0}.title-line{gap:9px}.header-left p{margin:5px 0 0;color:#7c8991;font-size:12px}.dirty-mark{font-size:12px;color:#b7791f}.header-actions{gap:9px}.editor-grid{display:grid;grid-template-columns:240px minmax(430px,1fr) 390px;height:calc(100vh - 136px);min-height:660px}.resource-panel,.property-panel{background:#fff;overflow:auto}.resource-panel{border-right:1px solid #dce4e8;padding:18px}.property-panel{border-left:1px solid #dce4e8;padding:20px}.panel-heading strong,.panel-heading small,.property-title strong,.property-title small{display:block}.panel-heading small,.property-title small{color:#81909a;font-size:11px;margin-top:4px}.resource-fields{display:grid;gap:13px;margin-top:20px}.resource-fields label,.field{display:grid;gap:6px}.resource-fields label>span,.field>span{font-size:12px;font-weight:600;color:#52616a}.resource-note{margin-top:22px;padding:12px;border-radius:8px;background:#edf7f4;color:#37675b}.resource-note strong,.resource-note span{display:block}.resource-note span{font-size:11px;line-height:1.6;margin-top:5px}.workflow-canvas{overflow:auto;padding:18px 28px 60px;background-color:#f4f7f8;background-image:radial-gradient(#d7e0e4 1px,transparent 1px);background-size:22px 22px}.canvas-intro{display:flex;align-items:baseline;justify-content:space-between;color:#71808a;font-size:12px}.canvas-intro strong{font-size:14px;color:#34444d}.flow-column{width:360px;margin:22px auto;display:flex;flex-direction:column;align-items:center}.add-point{width:30px;height:30px;border:1px solid #cbd7dc;border-radius:7px;background:#fff;color:#268b77;display:grid;place-items:center;cursor:pointer;box-shadow:0 3px 10px rgba(34,61,72,.08)}.flow-node{width:360px;min-height:108px;padding:15px;background:#fff;border:1px solid #d9e2e6;border-left:4px solid #48a895;border-radius:10px;display:grid;grid-template-columns:42px minmax(0,1fr) auto;gap:12px;align-items:start;box-shadow:0 8px 24px rgba(35,58,68,.08);cursor:pointer}.flow-node.selected{border-color:#22a68e;box-shadow:0 0 0 3px rgba(34,166,142,.13),0 10px 28px rgba(35,58,68,.1)}.flow-node.blue{border-left-color:#4f8fbd}.flow-node.amber{border-left-color:#d29b42}.flow-node.rose{border-left-color:#bd6b78}.node-icon{width:36px;height:36px;border-radius:8px;background:#e1f3ee;color:#248b76;display:grid;place-items:center;flex:0 0 auto}.node-icon.blue{background:#e7f0f7;color:#3f7ca7}.node-icon.amber{background:#f8eedc;color:#a77525}.node-icon.rose{background:#f7e7ea;color:#a65361}.node-copy>span,.node-copy>strong,.node-copy>small{display:block}.node-copy>span{font-size:10px;color:#8a98a0}.node-copy>strong{font-size:14px;margin-top:5px}.node-copy>small{font-size:11px;color:#71808a;margin-top:8px}.node-actions{display:flex;flex-direction:column;opacity:.25}.flow-node:hover .node-actions,.flow-node.selected .node-actions{opacity:1}.node-actions :deep(.el-button){margin:0}.flow-link{position:relative;width:30px;height:54px;display:grid;place-items:center}.flow-link>span{position:absolute;top:0;bottom:8px;left:50%;border-left:2px solid #ccd7dc;transform:translateX(-1px)}.flow-link>span::after{position:absolute;bottom:-8px;left:50%;width:0;height:0;border-top:8px solid #ccd7dc;border-right:6px solid transparent;border-left:6px solid transparent;content:'';transform:translateX(-50%)}.flow-link .add-point{position:relative;z-index:1;width:28px;height:28px}.flow-empty,.property-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;color:#87949c}.flow-empty{width:360px;height:180px;margin-top:14px;border:1px dashed #bccbd1;border-radius:12px;background:rgba(255,255,255,.7)}.flow-empty :deep(svg),.property-empty :deep(svg){width:30px;height:30px}.flow-empty strong,.property-empty strong{color:#52616a;margin-top:12px}.flow-empty span,.property-empty span{font-size:12px;margin-top:5px}.flow-end{color:#87949c;font-size:11px;display:flex;flex-direction:column;align-items:center}.flow-end span{height:18px}.property-title{gap:10px;padding-bottom:16px;border-bottom:1px solid #e4eaed}.property-panel .field{margin-top:17px}.field small{color:#829099;font-size:11px;line-height:1.5}.required>span:after{content:' *';color:#d04b5d}.section-label{font-size:12px;font-weight:700;color:#46565f;margin:20px 0 10px}.target-box{padding:12px;margin-bottom:10px;border:1px solid #e0e7ea;border-radius:8px}.target-box.disabled{background:#f6f8f9}.target-box :deep(.el-checkbox__label) strong,.target-box :deep(.el-checkbox__label) small{display:block}.target-box :deep(.el-checkbox__label) small{font-size:10px;color:#8a979f}.target-box :deep(.el-checkbox-group){display:grid;grid-template-columns:1fr 1fr;margin:10px 0 0 24px}.key-toolbar{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center;margin-bottom:8px}.key-toolbar :deep(.el-button){min-width:64px;margin:0}.key-grid{max-height:310px;overflow:auto;padding:10px;border:1px solid #e0e7ea;border-radius:8px}.key-options{display:grid;grid-template-columns:1fr}.key-options :deep(.el-checkbox){margin-right:0}.key-options :deep(.el-checkbox__label){font:11px/1.4 Cascadia Code,Consolas,monospace}.key-grid-empty{min-height:72px;display:grid;place-items:center;color:#87949c;font-size:11px}.wiring-placeholder{margin-top:20px;min-height:260px;border:1px dashed #d6b56e;border-radius:10px;background:repeating-linear-gradient(45deg,#fffaf0,#fffaf0 10px,#fdf6e8 10px,#fdf6e8 20px);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;color:#9a762d;padding:24px}.wiring-placeholder :deep(svg){width:44px;height:44px}.wiring-placeholder strong{margin-top:14px}.wiring-placeholder span{font-size:12px;line-height:1.6;margin-top:7px;max-width:240px}.contract-toolbar{justify-content:space-between;margin-top:18px}.contract-list{display:grid;margin-top:10px;border:1px solid #e0e7ea;border-radius:8px;max-height:200px;overflow:auto}.contract-list :deep(.el-checkbox){height:auto;margin:0;padding:10px;border-bottom:1px solid #edf1f3}.contract-list :deep(.el-checkbox:last-child){border-bottom:0}.contract-list strong,.contract-list small{display:block}.contract-list small{font-size:10px;color:#85929a;margin-top:3px}.preview-results{margin-top:22px}.snapshot{border:1px solid #e0e7ea;border-radius:8px;margin-bottom:10px;padding:11px}.snapshot>div{display:flex;justify-content:space-between;align-items:center}.snapshot dl{display:grid;grid-template-columns:120px 1fr;margin:10px 0 0;font-size:11px}.snapshot dt,.snapshot dd{padding:5px 0;border-top:1px solid #edf1f3}.snapshot dt{color:#77858e}.snapshot dd{margin:0;word-break:break-word}.snapshot dd.failed{color:#c74d5d}.property-empty{height:100%}.node-picker{display:grid;gap:10px}.node-picker button{width:100%;display:flex;align-items:center;gap:12px;padding:14px;border:1px solid #dfe6ea;border-radius:9px;background:#fff;text-align:left;cursor:pointer}.node-picker button:hover{border-color:#55aa98;background:#f2f9f7}.node-picker strong,.node-picker small{display:block}.node-picker small{margin-top:5px;color:#7c8a93;font-size:11px}.teal{--node-tone:#48a895}@media(max-width:1250px){.editor-grid{grid-template-columns:210px minmax(410px,1fr) 340px}.flow-node,.flow-empty{width:330px}.flow-column{width:330px}}
.node-picker button:disabled{cursor:not-allowed;opacity:.55;background:#f5f7f8}
.flow-node,.flow-empty,.wiring-placeholder,.node-picker button{border-radius:8px}.wiring-editor-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin:20px 0 10px}.wiring-editor-heading strong,.wiring-editor-heading span{display:block}.wiring-editor-heading span{max-width:240px;margin-top:4px;color:var(--ui-text-tertiary);font-size:11px;line-height:1.45}.wiring-editor-heading+.el-alert{margin-bottom:10px}.contract-previews{margin-top:12px}.contract-preview-title{display:flex;align-items:center;gap:8px;min-width:0}.contract-preview-title small{color:#7d8a92}.checksum{display:grid;gap:4px;margin-bottom:10px}.checksum span{font-size:10px;color:#7d8a92}.checksum code{font-size:10px;line-height:1.5;overflow-wrap:anywhere;color:#34444d}.snapshot dl{grid-template-columns:minmax(0,1fr) minmax(96px,35%);column-gap:12px}.snapshot dt,.snapshot dd{min-width:0;line-height:1.45;overflow-wrap:anywhere}.snapshot dt{font-family:Cascadia Code,Consolas,monospace;font-size:10px}.snapshot dd{font-variant-numeric:tabular-nums}@media(max-width:1250px){.editor-grid{grid-template-columns:180px minmax(320px,1fr) 300px}.resource-panel{padding:12px}.property-panel{padding:14px}.workflow-canvas{padding:16px 12px 48px}.flow-column,.flow-node,.flow-empty{width:290px}}
.contract-empty{min-height:96px;margin-top:10px;border:1px dashed #ccd8dd;border-radius:8px;display:grid;place-items:center;color:#829099;font-size:11px}
.flow-node.violet{border-left-color:#7669b5}.node-icon.violet{background:#eeebf8;color:#6556a5}.node-catalog{display:grid;gap:22px}.node-category h3{margin:0 0 9px;padding-bottom:7px;border-bottom:1px solid #e6ebee;color:#697780;font-size:12px;font-weight:600}.slnic-summary{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:18px 0 14px}.slnic-summary>div{padding:11px;border:1px solid #e3e0ef;border-radius:8px;background:#f8f7fc}.slnic-summary .wide{grid-column:1/-1}.slnic-summary span,.slnic-summary strong,.slnic-summary code{display:block}.slnic-summary span{font-size:10px;color:#7f8991}.slnic-summary strong,.slnic-summary code{margin-top:5px;font-size:12px;overflow-wrap:anywhere}.slnic-commands{display:grid;gap:7px}.slnic-commands code{padding:10px;border-radius:7px;background:#242632;color:#d9e6df;font-size:11px;line-height:1.45;overflow-wrap:anywhere}.slnic-note{color:#7c8991;font-size:11px;line-height:1.6}
.rem-command-editor :deep(.el-textarea__inner){background:#242632;color:#d9e6df;font:12px/1.7 "Cascadia Code","JetBrains Mono",Consolas,monospace;box-shadow:inset 0 0 0 1px #39404f}.rem-command-editor :deep(.el-textarea__inner:focus){box-shadow:inset 0 0 0 1px var(--ui-primary)}.rem-command-editor :deep(.el-textarea__inner[readonly]){cursor:default}
.market-script-options{display:grid;max-height:190px;margin-top:10px;overflow:auto;border:1px solid #e0e7ea;border-radius:8px}.market-script-options :deep(.el-checkbox){height:auto;margin:0;padding:9px 11px;border-bottom:1px solid #edf1f3}.market-script-options :deep(.el-checkbox:last-of-type){border-bottom:0}.market-script-options strong,.market-script-options small{display:block}.market-script-options small{margin-top:3px;color:#85929a;font-size:10px}.market-script-order{display:grid;gap:7px}.market-script-row{display:grid;grid-template-columns:24px minmax(0,1fr) auto;gap:8px;align-items:center;padding:9px;border:1px solid #dfe6ea;border-radius:7px}.market-script-row.invalid{border-color:#e3b268;background:#fffaf1}.market-script-row strong,.market-script-row small{display:block;overflow-wrap:anywhere}.market-script-row small{margin-top:3px;color:#85929a;font-size:10px}.market-script-row.invalid small{color:#a66e20}.market-script-index{display:grid;width:22px;height:22px;place-items:center;border-radius:5px;background:#edf3f4;color:#627078;font-size:10px;font-weight:700}.market-script-actions{display:flex;align-items:center}.market-script-actions :deep(.el-button){margin:0}

.template-toolbar{display:grid;grid-template-columns:minmax(0,1fr) repeat(3,32px);gap:6px;align-items:center}.template-toolbar :deep(.el-button){width:32px;height:32px;margin:0}.catalog-alert{margin-bottom:8px}.catalog-alert :deep(.el-alert__content){min-width:0}.stale-key-row{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px;padding:8px 10px;border:1px solid #efd39a;border-radius:6px;background:#fff9ec;color:#8a641f;font-size:11px}.stale-key-row :deep(.el-button){flex:0 0 auto}.key-grid{min-height:94px}.key-options :deep(.el-checkbox){height:auto;min-height:36px;padding:5px 0;align-items:flex-start}.key-options :deep(.el-checkbox__input){margin-top:2px}.key-options :deep(.el-checkbox__label){min-width:0;white-space:normal;font-family:inherit}.key-option-copy,.key-option-copy strong,.key-option-copy small{display:block}.key-option-copy strong{font:11px/1.4 Cascadia Code,Consolas,monospace;overflow-wrap:anywhere}.key-option-copy small{margin-top:2px;color:#839099;font-size:10px;line-height:1.45}

/* Professional console theme and compact-window fallback */
.workflow-page{min-height:calc(100dvh - 52px);overflow:hidden;background:var(--ui-surface-subtle);color:var(--ui-text-primary)}
.workflow-header{height:72px;border-color:var(--ui-border)}
.header-left p{color:var(--ui-text-secondary)}
.dirty-mark{color:var(--ui-warning)}
.editor-grid{height:calc(100dvh - 124px)}
.resource-panel{border-color:var(--ui-border)}
.property-panel{border-color:var(--ui-border)}
.workflow-canvas{background-color:#f4f8f8;background-image:radial-gradient(#d1dddf 1px,transparent 1px)}
.flow-node{border-color:var(--ui-border);box-shadow:0 7px 20px rgba(35,58,68,.07);transition:border-color var(--ui-transition),box-shadow var(--ui-transition),transform var(--ui-transition)}
.flow-node:hover{transform:translateY(-1px)}
.flow-node.selected{border-color:var(--ui-primary);box-shadow:0 0 0 3px rgba(14,128,111,.12),0 10px 24px rgba(35,58,68,.09)}
.resource-note{background:var(--ui-primary-soft);color:var(--ui-primary-hover)}
@media(max-width:1023px){.workflow-page{overflow:auto}.workflow-header{position:sticky;z-index:3;top:0}.editor-grid{grid-template-columns:190px minmax(420px,1fr);height:auto;min-height:0}.resource-panel{min-height:680px}.property-panel{grid-column:1/-1;min-height:520px;border-top:1px solid var(--ui-border);border-left:0}.workflow-canvas{min-height:680px}}
@media(max-width:767px){.workflow-header{align-items:flex-start;height:auto;flex-direction:column;gap:12px;padding:14px 12px}.header-actions{width:100%;justify-content:flex-end}.editor-grid{grid-template-columns:1fr}.resource-panel{min-height:0;border-right:0;border-bottom:1px solid var(--ui-border)}.workflow-canvas{min-width:420px;min-height:600px}.property-panel{grid-column:1;min-width:420px}.flow-column,.flow-node,.flow-empty{width:300px}}

/* Full-screen workflow workspace */
.workflow-page{min-height:100dvh;height:100dvh;overflow:hidden}
.workflow-header{height:72px;flex:0 0 72px;padding-inline:18px}
.header-left>div{min-width:0}.title-line h1{max-width:42vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.header-actions :deep(.el-button){white-space:nowrap}
.version-trigger{display:flex;align-items:center;gap:7px;margin-top:6px;padding:0;border:0;color:var(--ui-text-secondary);background:transparent;font-size:12px;cursor:pointer}.version-trigger:hover{color:var(--ui-primary)}
.version-manager{display:grid;gap:12px}.version-manager-title{display:flex;align-items:center;justify-content:space-between;gap:12px}.version-list{display:grid;max-height:280px;overflow:auto;border:1px solid var(--ui-border);border-radius:8px}.version-list button{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 12px;border:0;border-bottom:1px solid var(--ui-border);color:var(--ui-text-primary);background:#fff;text-align:left;cursor:pointer}.version-list button:last-child{border-bottom:0}.version-list button:hover,.version-list button.active{background:var(--ui-primary-soft)}.version-list button.active{box-shadow:inset 3px 0 0 var(--ui-primary)}.version-list strong,.version-list small{display:block}.version-list small{margin-top:3px;color:var(--ui-text-tertiary);font-size:10px}.version-manager-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px}.version-manager-actions :deep(.el-button){margin:0}
.workflow-tabs{height:calc(100dvh - 72px);background:var(--ui-surface-subtle)}.workflow-tabs :deep(.el-tabs__header){height:48px;margin:0;padding:0 24px;border-bottom:1px solid var(--ui-border);background:#fff}.workflow-tabs :deep(.el-tabs__nav-wrap::after){display:none}.workflow-tabs :deep(.el-tabs__item){height:48px}.workflow-tabs :deep(.el-tabs__content){height:calc(100% - 48px)}.workflow-tabs :deep(.el-tab-pane){height:100%}
.editor-grid{grid-template-columns:minmax(420px,1fr) 390px;height:100%;min-height:0}.workflow-canvas{min-width:0}.property-panel{display:block;min-width:0}.property-title>div:nth-child(2){min-width:0}.property-close{display:none;margin-left:auto}.node-save-actions{position:sticky;z-index:2;bottom:-20px;display:flex;justify-content:flex-end;gap:8px;margin:24px -20px -20px;padding:14px 20px;border-top:1px solid var(--ui-border);background:rgba(255,255,255,.96);backdrop-filter:blur(8px)}
.resource-tab{height:100%;overflow:auto;padding:28px 32px 40px}.resource-tab-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;max-width:1120px;margin:0 auto 24px}.resource-tab-heading h2{margin:0;font-size:20px}.resource-tab-heading p{margin:6px 0 0;color:var(--ui-text-secondary);font-size:12px}.resource-tab .resource-fields{grid-template-columns:repeat(3,minmax(220px,1fr));max-width:1120px;margin:0 auto}.resource-tab .resource-note{max-width:1120px;margin:24px auto 0}.resource-actions{position:sticky;bottom:-40px;display:flex;justify-content:flex-end;gap:8px;max-width:1184px;margin:32px auto -40px;padding:14px 32px;border-top:1px solid var(--ui-border);background:rgba(237,242,244,.96);backdrop-filter:blur(8px)}
@media(max-width:1250px){.editor-grid{grid-template-columns:minmax(380px,1fr) 340px}.resource-tab .resource-fields{grid-template-columns:repeat(2,minmax(220px,1fr))}}
@media(max-width:1023px){.workflow-page{overflow:hidden}.workflow-header{position:relative;z-index:12}.editor-grid{grid-template-columns:1fr;height:100%;min-height:0}.workflow-canvas{min-height:0}.property-panel{position:fixed;z-index:20;top:120px;right:0;bottom:0;width:min(420px,100vw);min-width:0;min-height:0;border-top:0;border-left:1px solid var(--ui-border);box-shadow:-16px 0 40px rgba(19,43,48,.16);transform:translateX(102%);transition:transform var(--ui-transition);pointer-events:none}.property-panel.open{transform:translateX(0);pointer-events:auto}.property-close{display:inline-flex}.resource-tab{padding-inline:20px}}
@media(max-width:767px){.workflow-header{height:auto;min-height:112px}.title-line h1{max-width:70vw}.workflow-tabs{height:calc(100dvh - 112px)}.workflow-tabs :deep(.el-tabs__header){padding-inline:12px}.resource-tab{padding:20px 14px 32px}.resource-tab .resource-fields{grid-template-columns:1fr}.resource-actions{bottom:-32px;margin-bottom:-32px;padding-inline:14px}.workflow-canvas{min-width:0;min-height:0;padding-inline:10px}.property-panel{top:160px;width:100vw}.flow-column,.flow-node,.flow-empty{width:min(320px,calc(100vw - 32px))}}
</style>
