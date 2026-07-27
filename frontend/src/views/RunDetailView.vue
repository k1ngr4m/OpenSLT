<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCheck, RefreshRight, VideoPlay } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import SshTerminalPanel from '@/components/SshTerminalPanel.vue'
import { useAuthStore } from '@/stores/auth'
import { businessText, resourceText, statusText, statusType } from '@/utils/status'

type JsonMap = Record<string, any>
type LogScope = 'all' | number
type WorkflowTerminalKind = 'slnic' | 'order'

interface RunStep {
  id: number
  code: string
  name: string
  workflow_node_id: number | null
  node_type: string
  config_snapshot: JsonMap
  result_summary: JsonMap
  position: number
  status: string
  progress: number
  retry_count: number
  max_retries: number
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  error_message: string | null
}

interface RunLog {
  id: number
  log_type: string
  level: string
  event: string
  message: string
  trace_id: string
  user_id: number | null
  run_id: number | null
  step_id: number | null
  source: string
  detail: JsonMap
  is_redacted: boolean
  created_at: string
}

interface CaptureItem {
  id: number
  item_key: string
  item_label: string
  value_text: string | null
  source_reference: string
  raw_output: string
  exit_code: number | null
  status: string
  error_message: string | null
}

interface CaptureSnapshot {
  id: number
  scope: string
  source_type: string
  resource_id: number
  database_name: string | null
  status: string
  attempt: number
  error_message: string | null
  started_at: string
  finished_at: string | null
  items: CaptureItem[]
}

interface CaptureState {
  signature: string
  loading: boolean
  error: string
  data: CaptureSnapshot[]
}

interface RunArtifact {
  id: number
  step_id: number | null
  artifact_type: string
  name: string
  content_type: string
  size: number
  checksum: string
  is_immutable: boolean
  created_at: string
}

interface RunMetric {
  id: number
  name: string
  value: number
  unit: string
  sample_count: number | null
  detail: JsonMap
}

interface RunDetail {
  id: number
  run_number: string
  plan_id: number
  scenario_id: number
  workflow_version_id: number | null
  business_code: string
  status: string
  progress: number
  resource_ids: number[]
  config_snapshot: JsonMap
  trace_id: string
  created_by: number
  started_at: string | null
  finished_at: string | null
  timeout_at: string | null
  error_code: string | null
  error_message: string | null
  queue_reason: string | null
  paused_from: string | null
  logs_complete: boolean
  created_at: string
  steps: RunStep[]
  artifacts: RunArtifact[]
  metrics: RunMetric[]
  verdict: {
    id: number
    final_result: string | null
    issue_description: string
    notes: string
    reviewed_by: number | null
    reviewed_at: string | null
  } | null
}

interface InfoRow {
  label: string
  value: string | number | null | undefined
  mono?: boolean
}

interface RunResourceSnapshot {
  id: number
  name: string
  type: string
  host?: string
  version?: string
}

interface ContractFilePreview {
  id: number
  filename: string
  contract_type?: string | null
  source_table?: string | null
  remote_path?: string | null
  quote_date?: string | null
  row_count?: number
  size?: number
  checksum?: string
  preview_rows?: JsonMap[]
}

const nodeTypeText: Record<string, string> = {
  server_config: '服务器配置',
  database_config: '数据库配置',
  wiring_confirmation: '接线确认',
  order_preparation: '发单准备',
  slnic_start_capture: '启动 SLNIC',
  slnic_stop_capture: '关闭 SLNIC',
  slnic_merge_capture: '合并 pcapng',
  parser_parse: '数据解析',
}

const route = useRoute()
const auth = useAuthStore()
const run = ref<RunDetail | null>(null)
const logs = ref<RunLog[]>([])
const active = ref('detail')
const verdictDialog = ref(false)
const verdict = reactive({ final_result: 'passed', issue_description: '', notes: '' })
const selectedStepId = ref<number | null>(null)
const manualStepSelection = ref(false)
const logScope = ref<LogScope>('all')
const captureStates = reactive<Record<number, CaptureState>>({})
const slnicWorkflowTerminalPanel = ref<InstanceType<typeof SshTerminalPanel> | null>(null)
const orderWorkflowTerminalPanel = ref<InstanceType<typeof SshTerminalPanel> | null>(null)
const terminalCommandPendingStepId = ref<number | null>(null)
const queuedTerminalCommand = ref<{ stepId: number; operation: 'start' | 'retry'; kind: WorkflowTerminalKind } | null>(null)
const contractPreviewDialog = ref(false)
const contractPreviewFile = ref<ContractFilePreview | null>(null)
const contractPreviewLoading = ref(false)
const contractPreviewError = ref('')
const contractPreviewCache = reactive<Record<number, ContractFilePreview>>({})
let socket: WebSocket | null = null
let timer: number | undefined
const runId = Number(route.params.id)
const actingStepId = ref<number | null>(null)

const canStart = computed(() => ['draft', 'resource_queue'].includes(run.value?.status || ''))
const isTerminalRunStatus = computed(() => ['completed', 'cancelled', 'execution_failed', 'parse_failed', 'precheck_failed', 'timed_out'].includes(run.value?.status || ''))
const currentStep = computed(() => findCurrentStep(run.value?.steps || []))
const selectedStep = computed(() => {
  const steps = run.value?.steps || []
  return steps.find(step => step.id === selectedStepId.value) || currentStep.value || steps[0] || null
})
const slnicResource = computed<RunResourceSnapshot | null>(() => {
  const resources = run.value?.config_snapshot?.resources
  if (!Array.isArray(resources)) return null
  return (resources.find((resource: JsonMap) => resource.type === 'slnic') as RunResourceSnapshot) || null
})
const orderResource = computed<RunResourceSnapshot | null>(() => {
  const resources = run.value?.config_snapshot?.resources
  if (!Array.isArray(resources)) return null
  return (resources.find((resource: JsonMap) => resource.type === 'order') as RunResourceSnapshot) || null
})
const workflowTerminalKind = computed<WorkflowTerminalKind | null>(() => terminalKindForStep(selectedStep.value))
const showWorkflowTerminal = computed(() => Boolean(workflowTerminalKind.value))
const workflowTerminalResource = computed(() => workflowTerminalKind.value ? resourceForTerminalKind(workflowTerminalKind.value) : null)
const workflowTerminalResourceText = computed(() => workflowTerminalKind.value === 'order' ? resourceText.order : resourceText.slnic)
const workflowTerminalTitle = computed(() => workflowTerminalKind.value ? titleForTerminalKind(workflowTerminalKind.value) : 'SSH 终端')
const workflowTerminalDescription = computed(() => {
  if (selectedStep.value?.node_type === 'order_preparation') {
    return '点击顶部“开始”后，会先完成 XML/合约准备，再把发单命令下发到这个终端。'
  }
  if (selectedStep.value?.node_type === 'slnic_stop_capture') {
    return '点击顶部“开始”后，关闭抓包脚本会在这个终端中下发。'
  }
  if (selectedStep.value?.node_type === 'slnic_merge_capture') {
    return '点击顶部“开始”后，合并与转换 pcapng 的命令会在这个终端中下发；确认完成后再点击顶部“完成”。'
  }
  return '点击顶部“开始”后，启动脚本会在这个终端中下发。'
})
const slnicTerminalSubtitle = computed(() => terminalSubtitle('slnic'))
const orderTerminalSubtitle = computed(() => terminalSubtitle('order'))
const selectedConfig = computed(() => selectedStep.value?.config_snapshot || {})
const selectedResult = computed(() => selectedStep.value?.result_summary || {})
const selectedArtifacts = computed(() => {
  if (!run.value || !selectedStep.value) return []
  return run.value.artifacts.filter(item => item.step_id === selectedStep.value?.id)
})
const filteredLogs = computed(() => {
  if (logScope.value === 'all') return logs.value
  return logs.value.filter(log => log.step_id === logScope.value)
})
const logScopeLabel = computed(() => {
  if (logScope.value === 'all') return '全部日志'
  return run.value?.steps.find(step => step.id === logScope.value)?.name || '节点日志'
})
const stepLogsCount = computed(() => {
  const counts = new Map<number, number>()
  for (const log of logs.value) {
    if (log.step_id == null) continue
    counts.set(log.step_id, (counts.get(log.step_id) || 0) + 1)
  }
  return counts
})
const summaryRows = computed<InfoRow[]>(() => {
  if (!selectedStep.value) return []
  return [
    { label: '节点类型', value: nodeTypeText[selectedStep.value.node_type] || selectedStep.value.node_type },
    { label: '节点状态', value: statusText[selectedStep.value.status] || selectedStep.value.status },
    { label: '进度', value: `${selectedStep.value.progress}%` },
    { label: '执行耗时', value: formatDuration(selectedStep.value.duration_ms) },
    { label: '重试次数', value: `${selectedStep.value.retry_count}/${selectedStep.value.max_retries}` },
    { label: '开始时间', value: formatDate(selectedStep.value.started_at) },
    { label: '结束时间', value: formatDate(selectedStep.value.finished_at) },
  ]
})
const configRows = computed<InfoRow[]>(() => {
  const step = selectedStep.value
  const config = selectedConfig.value
  if (!step) return []
  if (step.node_type === 'server_config') {
    const targets = Array.isArray(config.targets) ? config.targets : []
    return [
      { label: '采集目标', value: targets.length ? `${targets.length} 个` : '-' },
      { label: '资源类型', value: targets.map((item: JsonMap) => item.resource_type).filter(Boolean).join('、') || '-' },
      { label: '采集字段', value: targets.flatMap((item: JsonMap) => item.fields || []).join('、') || '-' },
    ]
  }
  if (step.node_type === 'database_config') {
    return [
      { label: '数据库', value: config.database_name || '-' },
      { label: '配置键数量', value: Array.isArray(config.keys) ? `${config.keys.length} 个` : '-' },
      { label: '配置键', value: Array.isArray(config.keys) ? config.keys.join('、') : '-' },
    ]
  }
  if (step.node_type === 'wiring_confirmation') {
    return [
      { label: '接线图', value: config.diagram || selectedResult.value.diagram || 'placeholder' },
      { label: '确认要求', value: config.instructions || '等待现场确认链路连接' },
    ]
  }
  if (step.node_type === 'order_preparation') {
    const rows: InfoRow[] = [
      { label: 'XML 文件', value: config.xml_filename || '-' },
      { label: 'XML 校验', value: config.xml_checksum || '-', mono: true },
      { label: '读取合约 CSV', value: orderReadSymbolCsvEnabled.value ? '是' : '否' },
      { label: '交易库', value: config.trading_database_name || '-' },
      { label: '网卡接口', value: config.network_interface || '-' },
    ]
    if (orderReadSymbolCsvEnabled.value) {
      rows.push({ label: '合约文件', value: Array.isArray(config.contract_file_ids) ? `${config.contract_file_ids.length} 个` : '-' })
    }
    return rows
  }
  if (step.node_type === 'parser_parse') {
    return [
      { label: '数据库', value: config.database_name || '-' },
      { label: '解析配置', value: config.config_filename || '-' },
    ]
  }
  if (step.node_type.startsWith('slnic_')) {
    return [
      { label: 'SLNIC 动作', value: nodeTypeText[step.node_type] || step.node_type },
      { label: '节点配置', value: Object.keys(config).length ? '见原始配置' : '-' },
    ]
  }
  return objectRows(config)
})
const resultRows = computed<InfoRow[]>(() => {
  const step = selectedStep.value
  const result = selectedResult.value
  if (!step || !Object.keys(result).length) return []
  if (step.node_type === 'server_config' || step.node_type === 'database_config') {
    return [
      { label: '采集来源', value: result.sources != null ? `${result.sources} 个` : '-' },
      { label: '失败数量', value: result.failed != null ? `${result.failed} 个` : '-' },
      { label: '快照 ID', value: Array.isArray(result.snapshot_ids) ? result.snapshot_ids.join('、') : '-' },
    ]
  }
  if (step.node_type === 'wiring_confirmation') {
    return [
      { label: '已确认', value: result.confirmed ? '是' : '否' },
      { label: '确认人 ID', value: result.confirmed_by || '-' },
      { label: '确认时间', value: formatDate(result.confirmed_at) },
    ]
  }
  if (step.node_type === 'order_preparation') {
    return [
      { label: '准备状态', value: result.prepared ? '已完成' : '-' },
      { label: 'XML 文件', value: result.xml_filename || '-' },
      { label: '读取合约 CSV', value: result.read_symbol_csv ? '是' : '否' },
      { label: '网卡接口', value: result.network_interface || '-' },
      { label: '执行模式', value: result.mode === 'terminal' ? 'SSH 终端' : '后端准备' },
      { label: '资源', value: result.resource_name || (result.resource_id ? resourceDisplayName(Number(result.resource_id)) : '-') },
      { label: '发单命令', value: result.command || result.generated_command || '-', mono: true },
      { label: '下发时间', value: formatDate(result.dispatched_at) },
      { label: '进程状态', value: result.process_started ? '已启动' : '未启动' },
    ]
  }
  if (step.node_type.startsWith('slnic_')) {
    return [
      { label: '资源', value: result.resource_name || (result.resource_id ? resourceDisplayName(Number(result.resource_id)) : '-') },
      { label: '执行模式', value: result.mode === 'terminal' ? 'SSH 终端' : '后端自动执行' },
      { label: 'SLNIC 指令', value: result.command || '-', mono: true },
      { label: '退出码', value: result.exit_code ?? '-' },
      { label: '下发时间', value: formatDate(result.dispatched_at) },
      { label: '产物文件', value: result.filename || '-' },
      { label: '文件大小', value: result.size != null ? formatBytes(result.size) : '-' },
      { label: 'SHA-256', value: result.checksum || '-', mono: true },
    ]
  }
  if (step.node_type === 'parser_parse') {
    return [
      { label: '数据库', value: result.database_name || '-' },
      { label: '退出码', value: result.exit_code ?? '-' },
      { label: '执行耗时', value: formatDuration(result.duration_ms) },
      { label: 'PCAP 产物 ID', value: result.pcap_artifact_id || '-' },
      { label: '输出文件', value: Array.isArray(result.output_files) ? `${result.output_files.length} 个` : '-' },
    ]
  }
  return objectRows(result)
})
const orderReadSymbolCsvEnabled = computed(() => {
  const step = selectedStep.value
  if (step?.node_type !== 'order_preparation') return false
  const configValue = selectedConfig.value.read_symbol_csv
  if (configValue != null) return configValue === true || configValue === 1 || configValue === '1'
  const resultValue = selectedResult.value.read_symbol_csv
  return resultValue === true || resultValue === 1 || resultValue === '1'
})
const selectedContractFileIds = computed(() => {
  const ids: number[] = []
  const add = (value: unknown) => {
    const id = Number(value)
    if (Number.isFinite(id) && !ids.includes(id)) ids.push(id)
  }
  ;(Array.isArray(selectedConfig.value.contract_file_ids) ? selectedConfig.value.contract_file_ids : []).forEach(add)
  ;(Array.isArray(selectedConfig.value.contract_files) ? selectedConfig.value.contract_files : []).forEach((file: JsonMap) => add(file.id))
  ;(Array.isArray(selectedResult.value.contract_files) ? selectedResult.value.contract_files : []).forEach((file: JsonMap) => add(file.id))
  return ids
})
const contractFiles = computed<ContractFilePreview[]>(() => {
  if (!orderReadSymbolCsvEnabled.value) return []
  const files = new Map<number, ContractFilePreview>()
  const merge = (source: unknown) => {
    const file = normalizeContractFile(source)
    if (!file) return
    files.set(file.id, { ...(files.get(file.id) || {}), ...file })
  }
  ;(Array.isArray(selectedConfig.value.contract_files) ? selectedConfig.value.contract_files : []).forEach(merge)
  ;(Array.isArray(selectedResult.value.contract_files) ? selectedResult.value.contract_files : []).forEach(merge)
  Object.values(contractPreviewCache).forEach(merge)
  const ids = selectedContractFileIds.value
  if (ids.length) return ids.map(id => files.get(id)).filter((file): file is ContractFilePreview => Boolean(file))
  return Array.from(files.values())
})
const contractPreviewRows = computed<JsonMap[]>(() => Array.isArray(contractPreviewFile.value?.preview_rows) ? contractPreviewFile.value.preview_rows : [])
const contractPreviewColumns = computed(() => Object.keys(contractPreviewRows.value[0] || {}))
const parserOutputFiles = computed(() => Array.isArray(selectedResult.value.output_files) ? selectedResult.value.output_files : [])
const parserTableRows = computed(() => Object.entries(selectedResult.value.table_rows || {}).map(([name, count]) => ({ name, count })))
const inputChecksums = computed(() => Object.entries(selectedResult.value.input_checksums || {}).map(([name, checksum]) => ({ name, checksum })))
const showRawConfig = computed(() => selectedStep.value ? Object.keys(selectedConfig.value).length > 0 : false)
const showRawResult = computed(() => selectedStep.value ? Object.keys(selectedResult.value).length > 0 : false)
const selectedCaptureSignature = computed(() => selectedStep.value ? snapshotSignature(selectedStep.value) : '')
const selectedCaptureState = computed(() => selectedStep.value ? captureStates[selectedStep.value.id] : undefined)
const captureSnapshots = computed(() => selectedCaptureState.value?.data || [])
const showCaptureDetails = computed(() => ['server_config', 'database_config'].includes(selectedStep.value?.node_type || ''))

function findCurrentStep(steps: RunStep[]) {
  return steps.find(step => step.status !== 'succeeded') || steps[steps.length - 1] || null
}

function terminalKindForStep(step: RunStep | null): WorkflowTerminalKind | null {
  if (!step) return null
  if (step.node_type === 'order_preparation') return 'order'
  if (['slnic_start_capture', 'slnic_stop_capture', 'slnic_merge_capture'].includes(step.node_type)) return 'slnic'
  return null
}

function panelForTerminalKind(kind: WorkflowTerminalKind) {
  return kind === 'order' ? orderWorkflowTerminalPanel.value : slnicWorkflowTerminalPanel.value
}

function resourceForTerminalKind(kind: WorkflowTerminalKind) {
  return kind === 'order' ? orderResource.value : slnicResource.value
}

function titleForTerminalKind(kind: WorkflowTerminalKind) {
  return kind === 'order' ? '发单 SSH 终端' : 'SLNIC SSH 终端'
}

function terminalSubtitle(kind: WorkflowTerminalKind) {
  const resource = resourceForTerminalKind(kind)
  if (!resource) return ''
  const label = kind === 'order' ? resourceText.order : resourceText.slnic
  return [label, resource.host, resource.version].filter(Boolean).join(' · ')
}

function snapshotSignature(step: RunStep) {
  const ids = step.result_summary?.snapshot_ids
  return Array.isArray(ids) ? ids.join(',') : ''
}

function shouldLoadCaptureDetails(step: RunStep | null) {
  return Boolean(step && ['server_config', 'database_config'].includes(step.node_type) && snapshotSignature(step))
}

function syncSelectedStep() {
  const steps = run.value?.steps || []
  const current = findCurrentStep(steps)
  const selectedStillExists = steps.some(step => step.id === selectedStepId.value)
  if (!manualStepSelection.value || !selectedStillExists) {
    selectedStepId.value = current?.id || steps[0]?.id || null
    if (!selectedStillExists) manualStepSelection.value = false
  }
  if (logScope.value !== 'all' && !steps.some(step => step.id === logScope.value)) {
    logScope.value = 'all'
  }
}

async function load() {
  run.value = (await api.get<RunDetail>(`/runs/${runId}`)).data
  logs.value = (await api.get<RunLog[]>(`/runs/${runId}/logs`)).data
  syncSelectedStep()
  ensureCaptureDetails(selectedStep.value)
}

async function ensureCaptureDetails(step: RunStep | null) {
  if (!step || !shouldLoadCaptureDetails(step)) return
  const signature = snapshotSignature(step)
  const cached = captureStates[step.id]
  if (cached?.signature === signature && (cached.loading || cached.data.length || cached.error)) return
  captureStates[step.id] = { signature, loading: true, error: '', data: cached?.data || [] }
  try {
    const { data } = await api.get<CaptureSnapshot[]>(`/runs/${runId}/steps/${step.id}/capture-snapshots`)
    captureStates[step.id] = { signature, loading: false, error: '', data }
  } catch (error) {
    captureStates[step.id] = { signature, loading: false, error: errorMessage(error), data: [] }
  }
}

function normalizeContractFile(source: unknown): ContractFilePreview | null {
  if (!source || typeof source !== 'object') return null
  const file = source as JsonMap
  const id = Number(file.id)
  if (!Number.isFinite(id)) return null
  return {
    id,
    filename: String(file.filename || `contract-${id}.csv`),
    contract_type: file.contract_type ?? null,
    source_table: file.source_table ?? null,
    remote_path: file.remote_path ?? null,
    quote_date: file.quote_date ?? null,
    row_count: Number.isFinite(Number(file.row_count)) ? Number(file.row_count) : undefined,
    size: Number.isFinite(Number(file.size)) ? Number(file.size) : undefined,
    checksum: file.checksum || '',
    preview_rows: Array.isArray(file.preview_rows) ? file.preview_rows : undefined,
  }
}

function contractTypeLabel(type?: string | null) {
  if (type === 'futures') return '期货'
  if (type === 'options') return '期权'
  return type || '合约'
}

function shortChecksum(value?: string | null) {
  if (!value) return '-'
  return value.length > 16 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value
}

async function ensureContractPreviewFile(file: ContractFilePreview) {
  if (Array.isArray(file.preview_rows)) return file
  const cached = contractPreviewCache[file.id]
  if (cached && Array.isArray(cached.preview_rows)) return { ...file, ...cached }
  if (!run.value || !selectedStep.value) return file
  contractPreviewLoading.value = true
  contractPreviewError.value = ''
  try {
    const { data } = await api.get<ContractFilePreview[]>(`/scenarios/${run.value.scenario_id}/workflow/nodes/${selectedStep.value.code}/contract-files`)
    const allowedIds = new Set(selectedContractFileIds.value)
    for (const item of data) {
      const normalized = normalizeContractFile(item)
      if (!normalized || (allowedIds.size && !allowedIds.has(normalized.id))) continue
      contractPreviewCache[normalized.id] = { ...(contractPreviewCache[normalized.id] || {}), ...normalized }
    }
    return { ...file, ...(contractPreviewCache[file.id] || {}) }
  } catch (error) {
    contractPreviewError.value = errorMessage(error)
    return file
  } finally {
    contractPreviewLoading.value = false
  }
}

async function openContractPreview(file: ContractFilePreview) {
  contractPreviewDialog.value = true
  contractPreviewError.value = ''
  contractPreviewFile.value = { ...file, ...(contractPreviewCache[file.id] || {}) }
  contractPreviewFile.value = await ensureContractPreviewFile(contractPreviewFile.value)
}

async function action(path: string, message: string) {
  try {
    await api.post(`/runs/${runId}/${path}`)
    ElMessage.success(message)
    setTimeout(load, 300)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function cancel() {
  await ElMessageBox.confirm('取消后将执行安全清理并释放资源，确定继续？', '取消运行', { type: 'warning' })
  action('cancel', '取消指令已提交')
}

async function stepAction(step: RunStep, operation: 'start' | 'complete' | 'retry') {
  actingStepId.value = step.id
  try {
    if (['slnic_start_capture', 'slnic_stop_capture', 'slnic_merge_capture', 'order_preparation'].includes(step.node_type) && ['start', 'retry'].includes(operation)) {
      await runWorkflowStepInTerminal(step, operation as 'start' | 'retry')
      return
    }
    await api.post(`/runs/${runId}/steps/${step.id}/${operation}`)
    const messages = { start: '节点已开始', complete: '节点已完成', retry: '节点已重新执行' }
    ElMessage.success(messages[operation])
    setTimeout(load, 300)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    actingStepId.value = null
  }
}

async function runWorkflowStepInTerminal(step: RunStep, operation: 'start' | 'retry') {
  const kind = terminalKindForStep(step)
  if (!kind) {
    throw new Error('当前节点不支持 SSH 终端执行')
  }
  manualStepSelection.value = false
  selectedStepId.value = step.id
  active.value = 'detail'
  await nextTick()
  const panel = panelForTerminalKind(kind)
  const title = titleForTerminalKind(kind)
  if (!resourceForTerminalKind(kind) || !panel) {
    throw new Error(`未找到${title}，请检查运行资源配置`)
  }
  if (!panel.connected) {
    queuedTerminalCommand.value = { stepId: step.id, operation, kind }
    terminalCommandPendingStepId.value = step.id
    if (!panel.connecting) panel.connect()
    ElMessage.info(`${title}连接中，连接成功后会自动下发指令`)
    return
  }
  terminalCommandPendingStepId.value = step.id
  const sent = panel.sendWorkflowStepCommand({ run_id: runId, step_id: step.id, operation })
  if (!sent) {
    terminalCommandPendingStepId.value = null
    throw new Error(`${title}未连接，无法下发指令`)
  }
}

function dispatchQueuedTerminalCommand(kind: WorkflowTerminalKind) {
  const queued = queuedTerminalCommand.value
  const panel = panelForTerminalKind(kind)
  if (!queued || queued.kind !== kind || !panel?.connected) return
  const sent = panel.sendWorkflowStepCommand({ run_id: runId, step_id: queued.stepId, operation: queued.operation })
  if (!sent) {
    ElMessage.error(`${titleForTerminalKind(kind)}未连接，无法下发指令`)
    terminalCommandPendingStepId.value = null
  }
  queuedTerminalCommand.value = null
}

function handleWorkflowTerminalStatus(kind: WorkflowTerminalKind, message: JsonMap) {
  if (message.status === 'connected') dispatchQueuedTerminalCommand(kind)
}

function handleWorkflowTerminalError(kind: WorkflowTerminalKind, message: string) {
  if (queuedTerminalCommand.value?.kind === kind) {
    ElMessage.error(message)
    queuedTerminalCommand.value = null
    terminalCommandPendingStepId.value = null
  }
}

function handleWorkflowTerminalCommand(kind: WorkflowTerminalKind, message: JsonMap) {
  const title = titleForTerminalKind(kind)
  if (message.status === 'dispatched') {
    ElMessage.success(`${title}指令已在终端中下发`)
    setTimeout(load, 300)
  } else if (message.status === 'failed') {
    ElMessage.error(message.message || `${title}指令下发失败`)
  }
  queuedTerminalCommand.value = null
  terminalCommandPendingStepId.value = null
  actingStepId.value = null
}

async function submitVerdict() {
  try {
    await api.post(`/runs/${runId}/verdict`, verdict)
    ElMessage.success('结论和报告已生成')
    verdictDialog.value = false
    load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function download(id: number) {
  try {
    const response = await api.get(`/artifacts/${id}/download`, { responseType: 'blob' })
    const disposition = response.headers['content-disposition'] || ''
    const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] || `artifact-${id}`
    const link = document.createElement('a')
    link.href = URL.createObjectURL(response.data)
    link.download = filename
    link.click()
    URL.revokeObjectURL(link.href)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function selectStep(step: RunStep) {
  selectedStepId.value = step.id
  manualStepSelection.value = true
  logScope.value = step.id
  active.value = 'detail'
}

function followCurrentStep() {
  manualStepSelection.value = false
  selectedStepId.value = currentStep.value?.id || null
  active.value = 'detail'
}

function showAllLogs() {
  logScope.value = 'all'
}

function statusClass(status: string) {
  if (status === 'succeeded' || status === 'completed') return 'is-success'
  if (status.includes('failed') || status === 'cancelled') return 'is-danger'
  if (status === 'running') return 'is-running'
  if (status.includes('awaiting') || status === 'waiting') return 'is-waiting'
  return 'is-pending'
}

function objectRows(value: JsonMap): InfoRow[] {
  return Object.entries(value).map(([label, item]) => ({ label, value: formatValue(item), mono: typeof item !== 'string' }))
}

function formatValue(value: any) {
  if (value == null || value === '') return '-'
  if (Array.isArray(value)) return value.length ? value.map(item => typeof item === 'object' ? JSON.stringify(item) : String(item)).join('、') : '-'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleString() : '-'
}

function formatTime(value?: string | null) {
  return value ? new Date(value).toLocaleTimeString() : '--:--:--'
}

function formatDuration(value?: number | null) {
  if (value == null) return '-'
  if (value < 1000) return `${value} ms`
  return `${(value / 1000).toFixed(1)} s`
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function prettyJson(value: JsonMap) {
  return JSON.stringify(value, null, 2)
}

function runResource(resourceId: number) {
  const resources = run.value?.config_snapshot?.resources
  if (!Array.isArray(resources)) return null
  return resources.find((resource: JsonMap) => Number(resource.id) === resourceId) || null
}

function resourceDisplayName(resourceId: number) {
  return runResource(resourceId)?.name || `资源 ${resourceId}`
}

function resourceDisplayMeta(snapshot: CaptureSnapshot) {
  const resource = runResource(snapshot.resource_id)
  const parts = [
    `资源 ID ${snapshot.resource_id}`,
    resource?.type || snapshot.source_type,
    `第 ${snapshot.attempt} 次`,
    formatDate(snapshot.started_at),
  ]
  return parts.filter(Boolean).join(' · ')
}

function connect() {
  const token = localStorage.getItem('access_token')
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  socket = new WebSocket(`${protocol}://${location.host}/api/v1/ws/runs/${runId}?token=${token}`)
  socket.onmessage = event => {
    const payload = JSON.parse(event.data)
    if (payload.type === 'log') logs.value.push(payload.data)
    if (payload.type === 'status' || payload.type === 'snapshot') {
      if (run.value) {
        run.value.status = payload.status
        run.value.progress = payload.progress
      }
      setTimeout(load, 200)
    }
  }
}

watch(
  [() => selectedStep.value?.id, selectedCaptureSignature],
  () => ensureCaptureDetails(selectedStep.value),
)

onMounted(() => {
  load()
  connect()
  timer = window.setInterval(load, 5000)
})
onBeforeUnmount(() => {
  socket?.close()
  if (timer) clearInterval(timer)
})
</script>

<template>
  <main v-if="run" class="page run-detail-page">
    <div class="page-header run-header">
      <div>
        <el-button link @click="$router.push('/runs')">← 返回运行列表</el-button>
        <h1 class="page-title mono">{{ run.run_number }}</h1>
        <p class="muted">{{ businessText[run.business_code] }} · {{ run.config_snapshot?.plan?.name }} / {{ run.config_snapshot?.scenario?.name }}</p>
      </div>
      <div v-if="auth.canOperate" class="toolbar">
        <el-button v-if="canStart" type="primary" @click="action('start', '运行已就绪')">启动运行</el-button>
        <el-button
          v-if="currentStep?.status === 'pending' && run.status === 'awaiting_step_start'"
          type="primary"
          :icon="VideoPlay"
          :loading="actingStepId === currentStep.id || terminalCommandPendingStepId === currentStep.id"
          @click="currentStep && stepAction(currentStep, 'start')"
        >开始</el-button>
        <el-button
          v-if="currentStep?.status === 'waiting' && run.status === 'awaiting_step_completion'"
          type="success"
          :icon="CircleCheck"
          :loading="actingStepId === currentStep.id"
          @click="currentStep && stepAction(currentStep, 'complete')"
        >完成</el-button>
        <el-button
          v-if="currentStep?.status === 'failed' && run.status === 'awaiting_step_retry'"
          type="warning"
          :icon="RefreshRight"
          :loading="actingStepId === currentStep.id || terminalCommandPendingStepId === currentStep.id"
          @click="currentStep && stepAction(currentStep, 'retry')"
        >重试</el-button>
        <el-button v-if="run.status === 'awaiting_review'" type="success" @click="verdictDialog = true">提交人工结论</el-button>
        <el-button v-if="!isTerminalRunStatus" type="danger" plain @click="cancel">取消</el-button>
      </div>
    </div>

    <section class="summary card" aria-label="运行摘要">
      <div><span class="muted">当前状态</span><p><el-tag size="large" :type="statusType(run.status)">{{ statusText[run.status] || run.status }}</el-tag></p></div>
      <div><span class="muted">总体进度</span><el-progress :percentage="run.progress" :stroke-width="12" /></div>
      <div><span class="muted">Trace ID</span><p class="mono trace">{{ run.trace_id }}</p></div>
      <div><span class="muted">日志完整性</span><p>{{ run.logs_complete ? '完整' : '已降级，待补传' }}</p></div>
    </section>

    <el-alert v-if="run.error_message" :title="run.error_code || '运行异常'" :description="run.error_message" type="error" show-icon :closable="false" />

    <section class="workflow-strip card" aria-label="流程节点">
      <div class="workflow-strip-head">
        <div>
          <strong>流程条</strong>
          <span class="muted">{{ run.steps.length }} 个节点</span>
        </div>
        <el-button v-if="manualStepSelection" link type="primary" @click="followCurrentStep">回到当前节点</el-button>
      </div>
      <div v-if="run.steps.length" class="workflow-scroller">
        <button
          v-for="step in run.steps"
          :key="step.id"
          type="button"
          class="flow-step"
          :class="[statusClass(step.status), { 'is-selected': selectedStep?.id === step.id, 'is-current': currentStep?.id === step.id }]"
          @click="selectStep(step)"
        >
          <span class="flow-index mono">{{ String(step.position).padStart(2, '0') }}</span>
          <span class="flow-body">
            <span class="flow-name">{{ step.name }}</span>
            <span class="flow-meta">
              {{ statusText[step.status] || step.status }}
              <template v-if="step.duration_ms != null"> · {{ formatDuration(step.duration_ms) }}</template>
              <template v-if="step.retry_count"> · 重试 {{ step.retry_count }}</template>
              <template v-if="stepLogsCount.get(step.id)"> · 日志 {{ stepLogsCount.get(step.id) }}</template>
            </span>
          </span>
        </button>
      </div>
      <el-empty v-else description="暂无流程节点" :image-size="72" />
    </section>

    <div class="workbench">
      <section class="card main-card">
        <el-tabs v-model="active">
          <el-tab-pane label="节点详情" name="detail">
            <div v-if="selectedStep" class="node-detail">
              <div class="node-title">
                <div>
                  <p class="eyebrow">当前查看节点</p>
                  <h2>{{ selectedStep.position }}. {{ selectedStep.name }}</h2>
                  <p class="muted">{{ nodeTypeText[selectedStep.node_type] || selectedStep.node_type }}</p>
                </div>
                <el-tag size="large" :type="statusType(selectedStep.status)">{{ statusText[selectedStep.status] || selectedStep.status }}</el-tag>
              </div>

              <div class="detail-grid">
                <div v-for="item in summaryRows" :key="item.label" class="info-tile">
                  <span class="muted">{{ item.label }}</span>
                  <strong :class="{ mono: item.mono }">{{ item.value || '-' }}</strong>
                </div>
              </div>

              <section v-show="showWorkflowTerminal" class="detail-section workflow-terminal-section">
                <div class="section-heading">
                  <div>
                    <h3>{{ workflowTerminalTitle }}</h3>
                    <p class="muted">{{ workflowTerminalDescription }}</p>
                  </div>
                  <el-tag v-if="workflowTerminalResource" type="success" effect="plain">{{ workflowTerminalResource.name }}</el-tag>
                </div>
                <SshTerminalPanel
                  v-if="slnicResource"
                  v-show="workflowTerminalKind === 'slnic'"
                  ref="slnicWorkflowTerminalPanel"
                  :resource-id="slnicResource.id"
                  :title="slnicResource.name"
                  :subtitle="slnicTerminalSubtitle"
                  :active="workflowTerminalKind === 'slnic'"
                  :min-height="320"
                  @status="message => handleWorkflowTerminalStatus('slnic', message)"
                  @error="message => handleWorkflowTerminalError('slnic', message)"
                  @workflow-command="message => handleWorkflowTerminalCommand('slnic', message)"
                />
                <SshTerminalPanel
                  v-if="orderResource"
                  v-show="workflowTerminalKind === 'order'"
                  ref="orderWorkflowTerminalPanel"
                  :resource-id="orderResource.id"
                  :title="orderResource.name"
                  :subtitle="orderTerminalSubtitle"
                  :active="workflowTerminalKind === 'order'"
                  :min-height="320"
                  @status="message => handleWorkflowTerminalStatus('order', message)"
                  @error="message => handleWorkflowTerminalError('order', message)"
                  @workflow-command="message => handleWorkflowTerminalCommand('order', message)"
                />
                <div v-if="showWorkflowTerminal && !workflowTerminalResource" class="empty-line">当前运行没有{{ workflowTerminalResourceText }}，无法加载 SSH 终端</div>
              </section>

              <div v-if="selectedStep.node_type === 'wiring_confirmation'" class="wiring-run">
                <div class="wiring-device"><strong>REM 系统</strong><span>测试服务器</span></div>
                <div class="wiring-cable"><i></i><span>链路连接</span><i></i></div>
                <div class="wiring-device market"><strong>模拟市场</strong><span>行情服务器</span></div>
                <div class="wiring-cable"><i></i><span>链路连接</span><i></i></div>
                <div class="wiring-device order"><strong>发单工具</strong><span>订单服务器</span></div>
              </div>

              <section class="detail-section">
                <h3>节点配置</h3>
                <dl class="info-list">
                  <template v-for="row in configRows" :key="row.label">
                    <dt>{{ row.label }}</dt>
                    <dd :class="{ mono: row.mono }">{{ row.value || '-' }}</dd>
                  </template>
                </dl>
                <details v-if="showRawConfig" class="json-fold">
                  <summary>原始配置</summary>
                  <pre>{{ prettyJson(selectedConfig) }}</pre>
                </details>
              </section>

              <section class="detail-section">
                <h3>执行结果</h3>
                <el-alert v-if="selectedStep.error_message" :title="selectedStep.error_message" type="error" show-icon :closable="false" />
                <div v-else-if="!resultRows.length" class="empty-line">暂无执行结果</div>
                <dl v-else class="info-list">
                  <template v-for="row in resultRows" :key="row.label">
                    <dt>{{ row.label }}</dt>
                    <dd :class="{ mono: row.mono }">{{ row.value || '-' }}</dd>
                  </template>
                </dl>

                <div v-if="showCaptureDetails" class="capture-detail-block">
                  <div class="capture-title">
                    <h4>配置详情</h4>
                    <span class="muted">按采集快照展示实际配置项</span>
                  </div>
                  <el-skeleton v-if="selectedCaptureState?.loading" :rows="4" animated />
                  <el-alert v-else-if="selectedCaptureState?.error" :title="selectedCaptureState.error" type="error" show-icon :closable="false" />
                  <div v-else-if="!selectedCaptureSignature" class="empty-line">暂无采集详情</div>
                  <div v-else-if="!captureSnapshots.length" class="empty-line">未获取到配置详情</div>
                  <div v-else class="capture-snapshot-list">
                    <article v-for="snapshot in captureSnapshots" :key="snapshot.id" class="capture-snapshot">
                      <div class="capture-snapshot-head">
                        <div>
                          <strong>{{ resourceDisplayName(snapshot.resource_id) }}</strong>
                          <span class="muted">{{ resourceDisplayMeta(snapshot) }}</span>
                        </div>
                        <el-tag size="small" :type="statusType(snapshot.status)">{{ statusText[snapshot.status] || snapshot.status }}</el-tag>
                      </div>
                      <el-alert v-if="snapshot.error_message" :title="snapshot.error_message" type="error" show-icon :closable="false" />
                      <el-table :data="snapshot.items" size="small" empty-text="暂无采集项">
                        <el-table-column label="配置项" min-width="150">
                          <template #default="scope">
                            <strong>{{ scope.row.item_label }}</strong>
                            <p class="muted mono capture-key">{{ scope.row.item_key }}</p>
                          </template>
                        </el-table-column>
                        <el-table-column label="采集值" min-width="220">
                          <template #default="scope">
                            <div class="capture-value" :class="{ danger: scope.row.status === 'failed' }">
                              {{ scope.row.value_text || scope.row.error_message || '-' }}
                            </div>
                            <details v-if="scope.row.raw_output && scope.row.raw_output !== scope.row.value_text" class="raw-output-fold">
                              <summary>原始输出</summary>
                              <pre>{{ scope.row.raw_output }}</pre>
                            </details>
                          </template>
                        </el-table-column>
                        <el-table-column label="来源" min-width="180" show-overflow-tooltip>
                          <template #default="scope"><span class="mono">{{ scope.row.source_reference || '-' }}</span></template>
                        </el-table-column>
                        <el-table-column label="状态" width="100">
                          <template #default="scope"><el-tag size="small" :type="statusType(scope.row.status)">{{ statusText[scope.row.status] || scope.row.status }}</el-tag></template>
                        </el-table-column>
                      </el-table>
                    </article>
                  </div>
                </div>

                <div v-if="contractFiles.length" class="contract-file-list">
                  <div class="contract-file-title">
                    <h4>合约 CSV 文件</h4>
                    <span class="muted">{{ contractFiles.length }} 个文件</span>
                  </div>
                  <div v-for="file in contractFiles" :key="file.id || file.filename" class="contract-file-row">
                    <div class="contract-file-main">
                      <strong>{{ file.filename }}</strong>
                      <span class="muted">
                        {{ contractTypeLabel(file.contract_type) }}
                        <template v-if="file.quote_date"> · {{ file.quote_date }}</template>
                        <template v-if="file.row_count != null"> · {{ file.row_count }} 行</template>
                      </span>
                      <span class="mono muted">SHA-256 {{ shortChecksum(file.checksum) }}</span>
                    </div>
                    <el-button
                      size="small"
                      type="primary"
                      plain
                      :loading="contractPreviewLoading && contractPreviewFile?.id === file.id"
                      @click="openContractPreview(file)"
                    >预览</el-button>
                  </div>
                </div>

                <div v-if="parserTableRows.length" class="mini-table two-col">
                  <div v-for="row in parserTableRows" :key="row.name" class="mini-row">
                    <span>{{ row.name }}</span>
                    <span>{{ row.count }} 行</span>
                  </div>
                </div>

                <div v-if="inputChecksums.length" class="mini-table">
                  <div v-for="row in inputChecksums" :key="row.name" class="mini-row">
                    <span>{{ row.name }}</span>
                    <span class="mono">{{ row.checksum }}</span>
                  </div>
                </div>

                <div v-if="parserOutputFiles.length" class="file-chips">
                  <span v-for="file in parserOutputFiles" :key="file">{{ file }}</span>
                </div>

                <div v-if="selectedArtifacts.length" class="artifact-links">
                  <el-button v-for="artifact in selectedArtifacts" :key="artifact.id" link type="primary" @click="download(artifact.id)">
                    下载 {{ artifact.name }}
                  </el-button>
                </div>

                <details v-if="showRawResult" class="json-fold">
                  <summary>原始结果</summary>
                  <pre>{{ prettyJson(selectedResult) }}</pre>
                </details>
              </section>

              <section class="detail-section compact-snapshot">
                <h3>运行配置快照</h3>
                <dl class="info-list">
                  <dt>方案版本</dt><dd>{{ run.config_snapshot?.plan?.config_version || '-' }}</dd>
                  <dt>场景类型</dt><dd>{{ run.config_snapshot?.scenario?.scenario_type || '-' }}</dd>
                  <dt>场景版本</dt><dd>{{ run.config_snapshot?.scenario?.config_version || '-' }}</dd>
                  <dt>资源数</dt><dd>{{ run.resource_ids.length }}</dd>
                  <dt>创建人 ID</dt><dd>{{ run.created_by }}</dd>
                  <dt>开始时间</dt><dd>{{ formatDate(run.started_at) }}</dd>
                  <dt>结束时间</dt><dd>{{ formatDate(run.finished_at) }}</dd>
                </dl>
              </section>
            </div>
            <el-empty v-else description="暂无节点详情" :image-size="80" />
          </el-tab-pane>

          <el-tab-pane label="指标与结论" name="metrics">
            <el-table :data="run.metrics" empty-text="暂无指标">
              <el-table-column prop="name" label="指标" />
              <el-table-column label="值">
                <template #default="scope"><strong>{{ Number(scope.row.value).toFixed(3) }}</strong> {{ scope.row.unit }}</template>
              </el-table-column>
              <el-table-column prop="sample_count" label="样本数" />
            </el-table>
            <div v-if="run.verdict" class="verdict"><h3>结论</h3><p>最终结论：{{ run.verdict.final_result || '待复核' }}</p><p>{{ run.verdict.issue_description }}</p><p class="muted">{{ run.verdict.notes }}</p></div>
          </el-tab-pane>

          <el-tab-pane label="产物与报告" name="artifacts">
            <el-table :data="run.artifacts" empty-text="暂无产物">
              <el-table-column prop="name" label="文件" />
              <el-table-column prop="artifact_type" label="类型" width="140" />
              <el-table-column label="大小" width="110"><template #default="scope">{{ formatBytes(scope.row.size) }}</template></el-table-column>
              <el-table-column prop="checksum" label="SHA-256" show-overflow-tooltip />
              <el-table-column width="90"><template #default="scope"><el-button link type="primary" @click="download(scope.row.id)">下载</el-button></template></el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </section>

      <aside class="card log-panel" aria-label="运行日志">
        <div class="log-panel-head">
          <div>
            <h2>运行日志</h2>
            <p class="muted">{{ logScopeLabel }} · {{ filteredLogs.length }} / {{ logs.length }} 条</p>
          </div>
          <el-button size="small" @click="load">刷新</el-button>
        </div>
        <div class="log-filters">
          <el-button size="small" :type="logScope === 'all' ? 'primary' : 'default'" plain @click="showAllLogs">全部日志</el-button>
          <el-tag v-if="logScope !== 'all'" type="info" effect="plain">{{ logScopeLabel }}</el-tag>
        </div>
        <div class="run-log-view">
          <div v-for="log in filteredLogs" :key="log.id" class="log-line" :class="{ 'is-error': log.level === 'ERROR' }">
            <div class="log-meta">
              <span class="mono">{{ formatTime(log.created_at) }}</span>
              <span>{{ log.source }}</span>
              <span>{{ log.event }}</span>
            </div>
            <p><span class="mono">[{{ log.level }}]</span> {{ log.message }}</p>
          </div>
          <div v-if="!filteredLogs.length" class="log-empty">暂无日志</div>
        </div>
      </aside>
    </div>

    <el-dialog v-model="verdictDialog" title="提交人工复核结论" width="600px">
      <el-form label-width="100px">
        <el-form-item label="最终结论">
          <el-radio-group v-model="verdict.final_result">
            <el-radio-button value="passed">通过</el-radio-button>
            <el-radio-button value="conditional">有条件通过</el-radio-button>
            <el-radio-button value="failed">不通过</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="问题说明"><el-input v-model="verdict.issue_description" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="verdict.notes" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="verdictDialog = false">取消</el-button><el-button type="primary" @click="submitVerdict">提交并生成报告</el-button></template>
    </el-dialog>

    <el-dialog v-model="contractPreviewDialog" :title="contractPreviewFile ? `预览 ${contractPreviewFile.filename}` : '预览合约 CSV'" width="900px">
      <div v-if="contractPreviewFile" class="contract-preview-meta">
        <el-tag effect="plain">{{ contractTypeLabel(contractPreviewFile.contract_type) }}</el-tag>
        <span v-if="contractPreviewFile.quote_date">交易日 {{ contractPreviewFile.quote_date }}</span>
        <span v-if="contractPreviewFile.row_count != null">{{ contractPreviewFile.row_count }} 行</span>
        <span class="mono">SHA-256 {{ shortChecksum(contractPreviewFile.checksum) }}</span>
      </div>
      <div v-loading="contractPreviewLoading" class="contract-preview-body">
        <el-alert v-if="contractPreviewError" :title="contractPreviewError" type="error" show-icon :closable="false" />
        <el-empty v-else-if="!contractPreviewRows.length" description="暂无预览数据" :image-size="80" />
        <el-table v-else :data="contractPreviewRows" border size="small" height="420">
          <el-table-column
            v-for="column in contractPreviewColumns"
            :key="column"
            :prop="column"
            :label="column"
            min-width="140"
            show-overflow-tooltip
          >
            <template #default="scope"><span class="mono">{{ formatValue(scope.row[column]) }}</span></template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer><el-button @click="contractPreviewDialog = false">关闭</el-button></template>
    </el-dialog>
  </main>
  <main v-else class="page run-detail-page">
    <el-skeleton :rows="10" animated />
  </main>
</template>

<style scoped>
.run-detail-page{background:#f3f6f8;min-height:100vh}.run-header{align-items:flex-start}.summary{display:grid;grid-template-columns:180px minmax(260px,1fr) minmax(260px,1.2fr) 180px;gap:28px;padding:18px 22px;margin-bottom:16px}.summary p{margin:10px 0 0}.trace{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.workflow-strip{padding:18px 20px;margin-top:16px}.workflow-strip-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}.workflow-strip-head strong{font-size:16px;margin-right:10px}.workflow-scroller{display:flex;gap:10px;overflow-x:auto;padding:4px 2px 8px;scrollbar-width:thin}.flow-step{position:relative;display:flex;align-items:center;gap:10px;min-width:218px;max-width:260px;padding:12px 16px;border:1px solid #dfe7ef;border-radius:8px;background:#fff;color:#263445;text-align:left;cursor:pointer;transition:background .2s,border-color .2s,box-shadow .2s,transform .2s}.flow-step:hover{transform:translateY(-1px);border-color:#9fc8ff;box-shadow:0 8px 20px rgba(44,92,145,.08)}.flow-step:active{transform:translateY(0)}.flow-step:focus-visible{outline:2px solid #409eff;outline-offset:2px}.flow-step:after{content:'';position:absolute;right:-11px;top:50%;width:10px;height:1px;background:#cbd6e2}.flow-step:last-child:after{display:none}.flow-step.is-selected{border-color:#409eff;background:#f3f8ff;box-shadow:0 0 0 2px rgba(64,158,255,.12)}.flow-step.is-current .flow-name:after{content:'当前';margin-left:8px;color:#409eff;font-size:11px;font-weight:600}.flow-step.is-danger{border-color:#ffc3c3;background:#fff7f7}.flow-step.is-success .flow-index{background:#e7f8ef;color:#24935a}.flow-step.is-danger .flow-index{background:#ffe6e6;color:#cf2f2f}.flow-step.is-running .flow-index,.flow-step.is-waiting .flow-index{background:#fff4dd;color:#b36b00}.flow-index{display:grid;place-items:center;flex:none;width:32px;height:32px;border-radius:8px;background:#eef5ff;color:#347fcf;font-weight:700}.flow-body{min-width:0}.flow-name{display:block;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.flow-meta{display:block;margin-top:5px;color:#7b8794;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.workbench{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:16px;margin-top:16px;align-items:start}.main-card{padding:0 20px 20px;min-height:620px}.node-title{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:10px 0 18px;border-bottom:1px solid #edf1f5}.node-title h2{font-size:22px;margin:4px 0 6px}.eyebrow{margin:0;color:#409eff;font-size:12px;font-weight:700}.detail-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:16px 0}.info-tile{min-height:76px;padding:12px;border-radius:8px;background:#f7fafc;border:1px solid #edf1f5}.info-tile span{display:block;font-size:12px}.info-tile strong{display:block;margin-top:8px;color:#223041;font-size:14px;word-break:break-word}.detail-section{padding:18px 0;border-top:1px solid #edf1f5}.detail-section:first-of-type{border-top:0}.section-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:14px}.section-heading h3{margin:0 0 6px}.section-heading p{margin:0}.workflow-terminal-section{padding:18px;margin:4px 0 18px;border:1px solid #dce8f4;border-radius:10px;background:linear-gradient(180deg,#fbfdff,#f7fafc)}.detail-section h3{margin:0 0 14px;font-size:16px}.info-list{display:grid;grid-template-columns:140px minmax(0,1fr);gap:12px 18px;margin:0;font-size:13px}.info-list dt{color:#7b8794}.info-list dd{margin:0;color:#263445;word-break:break-word}.empty-line{padding:14px;border-radius:8px;background:#f7fafc;color:#7b8794}.json-fold{margin-top:14px}.json-fold summary{cursor:pointer;color:#409eff;font-size:13px}.json-fold pre{max-height:260px;overflow:auto;margin:10px 0 0;padding:12px;border-radius:8px;background:#111827;color:#d1d5db;font:12px/1.6 "Cascadia Code",Consolas,monospace}.wiring-run{display:grid;grid-template-columns:1fr minmax(90px,.7fr) 1fr minmax(90px,.7fr) 1fr;align-items:center;gap:12px;padding:16px;margin:10px 0 4px;border-radius:8px;background:#f8fbfa;border:1px solid #e3efeb}.wiring-device{min-height:76px;border:1px solid #9bc8bd;border-left:4px solid #269a82;border-radius:8px;background:#f3faf8;display:flex;flex-direction:column;align-items:center;justify-content:center}.wiring-device.market{border-color:#b7c9dd;border-left-color:#4f83b2;background:#f5f8fc}.wiring-device.order{border-color:#d9bd84;border-left-color:#bd842f;background:#fffaf1}.wiring-device span,.wiring-cable span{font-size:11px;color:#75848c;margin-top:4px}.wiring-cable{display:flex;align-items:center;gap:5px;text-align:center}.wiring-cable i{height:2px;flex:1;background:#94aaa5;position:relative}.wiring-cable i:first-child:before,.wiring-cable i:last-child:after{content:'';position:absolute;top:-3px;width:8px;height:8px;border-radius:50%;background:#269a82}.wiring-cable i:first-child:before{left:0}.wiring-cable i:last-child:after{right:0}.mini-table{display:grid;gap:8px;margin-top:12px}.mini-row{display:grid;grid-template-columns:minmax(140px,1fr) 90px minmax(160px,1.4fr);gap:12px;padding:10px 12px;border-radius:8px;background:#f7fafc;color:#263445;font-size:12px}.mini-table.two-col .mini-row{grid-template-columns:1fr 120px}.file-chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.file-chips span{padding:6px 9px;border-radius:6px;background:#eef5ff;color:#347fcf;font-size:12px}.artifact-links{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px}.action-section .step-actions{display:flex;align-items:center;gap:10px;min-height:32px}.compact-snapshot{background:#fbfcfd;margin:6px -4px 0;padding:18px;border-radius:8px}.log-panel{position:sticky;top:16px;padding:18px;min-height:620px}.log-panel-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}.log-panel h2{margin:0 0 6px;font-size:18px}.log-panel p{margin:0}.log-filters{display:flex;align-items:center;gap:8px;margin-bottom:12px}.run-log-view{height:540px;overflow:auto;padding:12px;border-radius:8px;background:#111827;color:#d1d5db;font:12px/1.65 "Cascadia Code",Consolas,monospace}.log-line{padding:8px 0;border-bottom:1px solid rgba(255,255,255,.08)}.log-line:last-child{border-bottom:0}.log-line p{margin:4px 0 0;word-break:break-word}.log-line.is-error p{color:#fecaca}.log-meta{display:flex;gap:8px;flex-wrap:wrap;color:#8fa3b8;font-size:11px}.log-empty{padding:24px 0;text-align:center;color:#8fa3b8}.verdict{padding:16px;background:#f7faf9;border-radius:8px;margin-top:14px}.mono{font-variant-numeric:tabular-nums}@media(max-width:1250px){.summary{grid-template-columns:160px minmax(240px,1fr) minmax(220px,1fr) 150px;gap:18px}.workbench{grid-template-columns:minmax(0,1fr) 340px}.detail-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.wiring-run{grid-template-columns:1fr 70px 1fr 70px 1fr}.wiring-cable span{display:none}.flow-step{min-width:200px}.log-panel{padding:16px}.run-log-view{height:520px}}@media(max-width:1120px){.workbench{grid-template-columns:minmax(0,1fr) 320px}.detail-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.summary{grid-template-columns:150px minmax(220px,1fr) minmax(200px,1fr) 130px}.mini-row{grid-template-columns:minmax(120px,1fr) 80px minmax(120px,1.1fr)}}
.capture-detail-block{margin-top:18px;padding:16px;border-radius:8px;background:#fbfcfd;border:1px solid #edf1f5}.capture-title{display:flex;align-items:baseline;gap:10px;margin-bottom:12px}.capture-title h4{margin:0;font-size:15px}.capture-snapshot-list{display:grid;gap:14px}.capture-snapshot{padding:14px;border-radius:8px;background:#fff;border:1px solid #e6edf4}.capture-snapshot-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:12px}.capture-snapshot-head strong,.capture-snapshot-head span{display:block}.capture-snapshot-head .muted{font-size:12px;margin-top:4px}.capture-key{margin:4px 0 0;font-size:11px}.capture-value{white-space:pre-wrap;word-break:break-word;line-height:1.55}.raw-output-fold{margin-top:8px}.raw-output-fold summary{cursor:pointer;color:#409eff;font-size:12px}.raw-output-fold pre{max-height:180px;overflow:auto;margin:8px 0 0;padding:10px;border-radius:6px;background:#111827;color:#d1d5db;font:12px/1.6 "Cascadia Code",Consolas,monospace}
.contract-file-list{display:grid;gap:10px;margin-top:16px}.contract-file-title{display:flex;align-items:baseline;gap:10px}.contract-file-title h4{margin:0;font-size:15px}.contract-file-row{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:12px 14px;border:1px solid #e6edf4;border-radius:8px;background:#fbfdff}.contract-file-main{min-width:0;display:grid;gap:4px}.contract-file-main strong{color:#2f83e6;word-break:break-all}.contract-file-main span{font-size:12px}.contract-preview-meta{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:-4px 0 14px;color:#7b8794;font-size:12px}.contract-preview-body{min-height:180px}
</style>
