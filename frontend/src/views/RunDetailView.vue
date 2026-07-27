<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCheck, RefreshRight, VideoPlay } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import RunCaptureDetails from '@/components/run-detail/RunCaptureDetails.vue'
import RunContractFiles from '@/components/run-detail/RunContractFiles.vue'
import RunContractPreviewDialog from '@/components/run-detail/RunContractPreviewDialog.vue'
import RunLogPanel from '@/components/run-detail/RunLogPanel.vue'
import RunWorkflowStrip from '@/components/run-detail/RunWorkflowStrip.vue'
import SshTerminalPanel from '@/components/SshTerminalPanel.vue'
import { useRunStepPresentation } from '@/composables/useRunStepPresentation'
import { useAuthStore } from '@/stores/auth'
import type {
  CaptureSnapshot,
  CaptureState,
  ContractFilePreview,
  JsonMap,
  LogScope,
  RunDetail,
  RunLog,
  RunResourceSnapshot,
  RunStep,
  WorkflowTerminalKind,
} from '@/types/run'
import { formatBytes, formatDate, nodeTypeText, normalizeContractFile, prettyJson } from '@/utils/runDetail'
import { businessText, resourceText, statusText, statusType } from '@/utils/status'

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
const {
  configRows,
  contractFiles,
  inputChecksums,
  parserOutputFiles,
  parserTableRows,
  resultRows,
  selectedArtifacts,
  selectedConfig,
  selectedContractFileIds,
  selectedResult,
  showCaptureDetails,
  showRawConfig,
  showRawResult,
  summaryRows,
} = useRunStepPresentation(run, selectedStep, contractPreviewCache)
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
const selectedCaptureSignature = computed(() => selectedStep.value ? snapshotSignature(selectedStep.value) : '')
const selectedCaptureState = computed(() => selectedStep.value ? captureStates[selectedStep.value.id] : undefined)
const captureSnapshots = computed(() => selectedCaptureState.value?.data || [])

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

    <RunWorkflowStrip
      :steps="run.steps"
      :selected-step-id="selectedStep?.id || null"
      :current-step-id="currentStep?.id || null"
      :manual-selection="manualStepSelection"
      :log-counts="stepLogsCount"
      @select="selectStep"
      @follow-current="followCurrentStep"
    />

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

                <RunCaptureDetails
                  v-if="showCaptureDetails"
                  :state="selectedCaptureState"
                  :signature="selectedCaptureSignature"
                  :snapshots="captureSnapshots"
                  :resource-name="resourceDisplayName"
                  :resource-meta="resourceDisplayMeta"
                />

                <RunContractFiles
                  :files="contractFiles"
                  :loading-file-id="contractPreviewLoading ? contractPreviewFile?.id || null : null"
                  @preview="openContractPreview"
                />

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

      <RunLogPanel
        :logs="filteredLogs"
        :total="logs.length"
        :scope-label="logScopeLabel"
        :scoped="logScope !== 'all'"
        @refresh="load"
        @show-all="showAllLogs"
      />
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

    <RunContractPreviewDialog
      v-model="contractPreviewDialog"
      :file="contractPreviewFile"
      :loading="contractPreviewLoading"
      :error="contractPreviewError"
    />
  </main>
  <main v-else class="page run-detail-page">
    <el-skeleton :rows="10" animated />
  </main>
</template>

<style scoped>
.run-detail-page {
  min-height: 100vh;
  background: #f3f6f8;
}

.run-header {
  align-items: flex-start;
}

.summary {
  display: grid;
  grid-template-columns: 180px minmax(260px, 1fr) minmax(260px, 1.2fr) 180px;
  gap: 28px;
  padding: 18px 22px;
  margin-bottom: 16px;
}

.summary p {
  margin: 10px 0 0;
}

.trace {
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workbench {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
  align-items: start;
  gap: 16px;
  margin-top: 16px;
}

.main-card {
  min-height: 620px;
  padding: 0 20px 20px;
}

.node-title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 0 18px;
  border-bottom: 1px solid #edf1f5;
}

.node-title h2 {
  margin: 4px 0 6px;
  font-size: 22px;
}

.eyebrow {
  margin: 0;
  color: #409eff;
  font-size: 12px;
  font-weight: 700;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 16px 0;
}

.info-tile {
  min-height: 76px;
  padding: 12px;
  border: 1px solid #edf1f5;
  border-radius: 8px;
  background: #f7fafc;
}

.info-tile span {
  display: block;
  font-size: 12px;
}

.info-tile strong {
  display: block;
  margin-top: 8px;
  color: #223041;
  font-size: 14px;
  word-break: break-word;
}

.detail-section {
  padding: 18px 0;
  border-top: 1px solid #edf1f5;
}

.detail-section:first-of-type {
  border-top: 0;
}

.detail-section h3 {
  margin: 0 0 14px;
  font-size: 16px;
}

.section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.section-heading h3 {
  margin: 0 0 6px;
}

.section-heading p {
  margin: 0;
}

.workflow-terminal-section {
  padding: 18px;
  margin: 4px 0 18px;
  border: 1px solid #dce8f4;
  border-radius: 10px;
  background: linear-gradient(180deg, #fbfdff, #f7fafc);
}

.info-list {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr);
  gap: 12px 18px;
  margin: 0;
  font-size: 13px;
}

.info-list dt {
  color: #7b8794;
}

.info-list dd {
  margin: 0;
  color: #263445;
  word-break: break-word;
}

.empty-line {
  padding: 14px;
  border-radius: 8px;
  background: #f7fafc;
  color: #7b8794;
}

.json-fold {
  margin-top: 14px;
}

.json-fold summary {
  color: #409eff;
  font-size: 13px;
  cursor: pointer;
}

.json-fold pre {
  max-height: 260px;
  overflow: auto;
  padding: 12px;
  margin: 10px 0 0;
  border-radius: 8px;
  background: #111827;
  color: #d1d5db;
  font: 12px/1.6 "Cascadia Code", Consolas, monospace;
}

.wiring-run {
  display: grid;
  grid-template-columns: 1fr minmax(90px, .7fr) 1fr minmax(90px, .7fr) 1fr;
  align-items: center;
  gap: 12px;
  padding: 16px;
  margin: 10px 0 4px;
  border: 1px solid #e3efeb;
  border-radius: 8px;
  background: #f8fbfa;
}

.wiring-device {
  display: flex;
  min-height: 76px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border: 1px solid #9bc8bd;
  border-left: 4px solid #269a82;
  border-radius: 8px;
  background: #f3faf8;
}

.wiring-device.market {
  border-color: #b7c9dd;
  border-left-color: #4f83b2;
  background: #f5f8fc;
}

.wiring-device.order {
  border-color: #d9bd84;
  border-left-color: #bd842f;
  background: #fffaf1;
}

.wiring-device span,
.wiring-cable span {
  margin-top: 4px;
  color: #75848c;
  font-size: 11px;
}

.wiring-cable {
  display: flex;
  align-items: center;
  gap: 5px;
  text-align: center;
}

.wiring-cable i {
  position: relative;
  height: 2px;
  flex: 1;
  background: #94aaa5;
}

.wiring-cable i:first-child::before,
.wiring-cable i:last-child::after {
  position: absolute;
  top: -3px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #269a82;
  content: '';
}

.wiring-cable i:first-child::before {
  left: 0;
}

.wiring-cable i:last-child::after {
  right: 0;
}

.mini-table {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.mini-row {
  display: grid;
  grid-template-columns: minmax(140px, 1fr) 90px minmax(160px, 1.4fr);
  gap: 12px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f7fafc;
  color: #263445;
  font-size: 12px;
}

.mini-table.two-col .mini-row {
  grid-template-columns: 1fr 120px;
}

.file-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.artifact-links {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 12px;
}

.file-chips span {
  padding: 6px 9px;
  border-radius: 6px;
  background: #eef5ff;
  color: #347fcf;
  font-size: 12px;
}

.compact-snapshot {
  padding: 18px;
  margin: 6px -4px 0;
  border-radius: 8px;
  background: #fbfcfd;
}

.verdict {
  padding: 16px;
  margin-top: 14px;
  border-radius: 8px;
  background: #f7faf9;
}

.mono {
  font-variant-numeric: tabular-nums;
}

@media (max-width: 1250px) {
  .summary {
    grid-template-columns: 160px minmax(240px, 1fr) minmax(220px, 1fr) 150px;
    gap: 18px;
  }

  .workbench {
    grid-template-columns: minmax(0, 1fr) 340px;
  }

  .detail-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .wiring-run {
    grid-template-columns: 1fr 70px 1fr 70px 1fr;
  }

  .wiring-cable span {
    display: none;
  }
}

@media (max-width: 1120px) {
  .workbench {
    grid-template-columns: minmax(0, 1fr) 320px;
  }

  .detail-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .summary {
    grid-template-columns: 150px minmax(220px, 1fr) minmax(200px, 1fr) 130px;
  }

  .mini-row {
    grid-template-columns: minmax(120px, 1fr) 80px minmax(120px, 1.1fr);
  }
}
</style>
