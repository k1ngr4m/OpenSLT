<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft, Check, CircleCheck, Close, Download, EditPen, Refresh, RefreshRight, VideoPlay } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import RunCaptureDetails from '@/components/run-detail/RunCaptureDetails.vue'
import RunContractFiles from '@/components/run-detail/RunContractFiles.vue'
import RunContractPreviewDialog from '@/components/run-detail/RunContractPreviewDialog.vue'
import RunLogPanel from '@/components/run-detail/RunLogPanel.vue'
import RunWorkflowStrip from '@/components/run-detail/RunWorkflowStrip.vue'
import SshTerminalPanel from '@/components/SshTerminalPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import WiringTopologyDiagram from '@/components/WiringTopologyDiagram.vue'
import { useRunActions } from '@/composables/useRunActions'
import { useRunLifecycle } from '@/composables/useRunLifecycle'
import { useOrderActions } from '@/composables/useOrderActions'
import { useParserExports } from '@/composables/useParserExports'
import { useStatisticsInputs } from '@/composables/useStatisticsInputs'
import { useRunStepPresentation } from '@/composables/useRunStepPresentation'
import { useWiringInterfaceNames } from '@/composables/useWiringInterfaceNames'
import { useWorkflowTerminal } from '@/composables/useWorkflowTerminal'
import { useAuthStore } from '@/stores/auth'
import type {
  CaptureSnapshot,
  CaptureState,
  ContractFilePreview,
  JsonMap,
  LogScope,
  RunStep,
} from '@/types/run'
import { formatBytes, formatDate, formatTime, nodeTypeText, normalizeContractFile, presentRunMetric, sortArtifactsNewestFirst } from '@/utils/runDetail'
import { businessText, resourceText } from '@/utils/status'

const route = useRoute()
const auth = useAuthStore()
const runId = Number(route.params.id)
const { load, logs, run } = useRunLifecycle(runId)
const active = ref('detail')
const selectedStepId = ref<number | null>(null)
const manualStepSelection = ref(false)
const logScope = ref<LogScope>('all')
const captureStates = reactive<Record<number, CaptureState>>({})
const contractPreviewDialog = ref(false)
const contractPreviewFile = ref<ContractFilePreview | null>(null)
const contractPreviewLoading = ref(false)
const contractPreviewError = ref('')
const contractPreviewCache = reactive<Record<number, ContractFilePreview>>({})

const canStart = computed(() => ['draft', 'resource_queue'].includes(run.value?.status || ''))
const isWorkflowRun = computed(() => Boolean(run.value?.config_snapshot?.workflow))
const canEditVerdict = computed(() => Boolean(
  auth.canOperate && run.value
  && (run.value.status === 'awaiting_review' || (run.value.status === 'completed' && isWorkflowRun.value)),
))
const canRegenerateReports = computed(() => Boolean(
  auth.canOperate && run.value?.status === 'completed' && isWorkflowRun.value,
))
const metricRows = computed(() => (run.value?.metrics || []).map(presentRunMetric))
const artifactsNewestFirst = computed(() => sortArtifactsNewestFirst(run.value?.artifacts || []))
const isTerminalRunStatus = computed(() => ['completed', 'cancelled', 'execution_failed', 'parse_failed', 'precheck_failed', 'timed_out'].includes(run.value?.status || ''))
const currentStep = computed(() => findCurrentStep(run.value?.steps || []))
const selectedStep = computed(() => {
  const steps = run.value?.steps || []
  return steps.find(step => step.id === selectedStepId.value) || currentStep.value || steps[0] || null
})
const {
  canEditWiringNames,
  cancelEditingWiringNames,
  editingWiringNames,
  saveWiringInterfaceNames,
  savingWiringNames,
  startEditingWiringNames,
  updateWiringInterfaceName,
  wiringActionBlocked,
  wiringNamesDirty,
  wiringSnapshot,
  wiringValidationMessage,
} = useWiringInterfaceNames({
  canOperate: computed(() => auth.canOperate),
  currentStep,
  selectedStep,
  run,
  runId,
  reload: load,
})
const {
  handleWorkflowTerminalCommand,
  handleWorkflowTerminalError,
  handleWorkflowTerminalStatus,
  marketResource,
  marketTerminalSubtitle,
  marketWorkflowTerminalPanel,
  orderResource,
  orderTerminalSubtitle,
  orderWorkflowTerminalPanel,
  remResource,
  remTerminalSubtitle,
  remWorkflowTerminalPanel,
  runWorkflowStepInTerminal,
  showWorkflowTerminal,
  slnicResource,
  slnicTerminalSubtitle,
  slnicWorkflowTerminalPanel,
  terminalCommandPendingStepId,
  workflowTerminalKind,
  workflowTerminalResource,
  workflowTerminalResourceText,
  workflowTerminalTitle,
  workflowTerminalDescription,
} = useWorkflowTerminal({
  active,
  manualStepSelection,
  reload: load,
  run,
  runId,
  selectedStep,
  selectedStepId,
})
const {
  actingStepId,
  action,
  cancel,
  download,
  openVerdict,
  regenerateReports,
  regeneratingReports,
  stepAction,
  submitVerdict,
  verdict,
  verdictDialog,
} = useRunActions({ runId, reload: load, runTerminalStep: runWorkflowStepInTerminal })
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
  summaryRows,
} = useRunStepPresentation(run, selectedStep, contractPreviewCache)
const {
  availableOrderActions,
  canSendOrderActions,
  confirmCurrentOrderAction,
  defaultOrderAction,
  isDangerousOrderAction,
  orderActionUnresolved,
  recentOrderActionHistory,
  retryUnknownOrderAction,
  sendOrderAction,
  sendingOrderAction,
} = useOrderActions({ currentStep, reload: load, retryStep: stepAction, run, runId })
const {
  canExportParserTables,
  exportingTable,
  exportParserTable,
  parserExportRows,
} = useParserExports({
  currentStep,
  selectedStep,
  run,
  runId,
  reload: load,
  downloadArtifact: download,
})
const {
  canSelectStatisticsInputs,
  displayStatisticsValue,
  displayedStatisticsCsvFiles,
  loadingStatisticsCsvFiles,
  refreshStatisticsCsvFiles,
  saveStatisticsInputs,
  savingStatisticsInputs,
  selectedRelativePaths,
  statisticsCsvDirectory,
  statisticsResults,
  statisticsSelectionDirty,
  statisticsSelectionReady,
  statisticsUnit,
} = useStatisticsInputs({ currentStep, selectedStep, run, runId, reload: load })
const statisticsActionBlocked = computed(() => Boolean(
  currentStep.value?.node_type === 'data_statistics'
  && (!statisticsSelectionReady.value || savingStatisticsInputs.value),
))
const canCompleteCurrent = computed(() => Boolean(
  currentStep.value?.status === 'waiting'
  && run.value?.status === 'awaiting_step_completion'
  && (currentStep.value.node_type !== 'order_preparation' || !orderActionUnresolved.value),
))
const orderTerminalSocketPath = computed(() => selectedStep.value?.node_type === 'order_preparation'
  ? `/ws/runs/${runId}/steps/${selectedStep.value.id}/order-terminal`
  : '')
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
const sensitiveArtifactTypes = new Set(['web_report', 'excel_report', 'pdf_report', 'order_config_xml'])

function canDownloadArtifact(artifactType: string) {
  return auth.canOperate || !sensitiveArtifactTypes.has(artifactType)
}

function findCurrentStep(steps: RunStep[]) {
  return steps.find(step => step.status !== 'succeeded') || steps[steps.length - 1] || null
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

watch(run, syncSelectedStep)
watch(
  [() => selectedStep.value?.id, selectedCaptureSignature],
  () => ensureCaptureDetails(selectedStep.value),
)
</script>

<template>
  <main v-if="run" class="page run-detail-page">
    <div class="page-header run-header">
      <div>
        <el-tooltip content="返回运行列表" placement="right">
          <el-button
            class="run-back-button"
            :icon="ArrowLeft"
            circle
            plain
            aria-label="返回运行列表"
            @click="$router.push('/runs')"
          />
        </el-tooltip>
        <div class="run-title-line"><h1 class="page-title mono">{{ run.run_number }}</h1><StatusBadge :status="run.status" show-raw /></div>
        <p class="muted">{{ businessText[run.business_code] }} · {{ run.config_snapshot?.plan?.name }} / {{ run.config_snapshot?.scenario?.name }}</p>
      </div>
      <div v-if="auth.canOperate" class="toolbar">
        <el-button v-if="canStart" type="primary" @click="action('start', '运行已就绪')">启动运行</el-button>
        <el-button
          v-if="currentStep?.status === 'pending' && run.status === 'awaiting_step_start'"
          type="primary"
          :icon="VideoPlay"
          :loading="actingStepId === currentStep.id || terminalCommandPendingStepId === currentStep.id"
          :disabled="Boolean(exportingTable) || statisticsActionBlocked || wiringActionBlocked"
          @click="currentStep && stepAction(currentStep, 'start')"
        >开始</el-button>
        <el-button
          v-if="canCompleteCurrent"
          type="success"
          :icon="CircleCheck"
          :loading="actingStepId === currentStep.id"
          :disabled="wiringActionBlocked"
          @click="currentStep && stepAction(currentStep, currentStep.node_type === 'wiring_confirmation' ? 'confirm' : 'complete')"
        >{{ currentStep.node_type === 'wiring_confirmation' ? '确认接线' : '完成' }}</el-button>
        <el-button
          v-if="currentStep?.status === 'failed' && run.status === 'awaiting_step_retry'"
          type="warning"
          :icon="RefreshRight"
          :loading="actingStepId === currentStep.id || terminalCommandPendingStepId === currentStep.id"
          :disabled="Boolean(exportingTable) || statisticsActionBlocked"
          @click="currentStep && stepAction(currentStep, 'retry')"
        >重试</el-button>
        <el-button v-if="canEditVerdict" type="success" @click="openVerdict(run.verdict)">{{ run.verdict ? '更新人工结论' : '提交人工结论' }}</el-button>
        <el-button v-if="!isTerminalRunStatus" type="danger" plain @click="cancel">取消</el-button>
      </div>
    </div>

    <section class="summary card" aria-label="运行摘要">
      <div><span class="muted">当前状态</span><p><StatusBadge :status="run.status" show-raw /></p></div>
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
                <div class="node-title-main">
                  <p class="eyebrow">当前查看节点</p>
                  <h2>{{ selectedStep.position }}. {{ selectedStep.name }}</h2>
                  <p class="node-type-label">{{ nodeTypeText[selectedStep.node_type] || selectedStep.node_type }}</p>
                </div>
                <StatusBadge :status="selectedStep.status" show-raw />
              </div>

              <div class="detail-grid">
                <div v-for="item in summaryRows" :key="item.label" class="info-tile">
                  <span class="muted">{{ item.label }}</span>
                  <strong :class="{ mono: item.mono }">{{ item.value || '-' }}</strong>
                </div>
              </div>

              <section v-if="selectedStep.node_type === 'parser_parse'" class="detail-section parser-export-panel">
                <div class="section-heading">
                  <div>
                    <h3>解析输入 CSV</h3>
                    <p class="muted">手动获取的快照会被解析复用，开始时自动补齐缺失表。</p>
                  </div>
                  <el-tag v-if="canExportParserTables" type="success" effect="plain">可获取</el-tag>
                </div>
                <div class="parser-export-list">
                  <div v-for="row in parserExportRows" :key="row.table" class="parser-export-row">
                    <div class="parser-export-name">
                      <code>{{ row.table }}</code>
                      <span v-if="row.ready" class="muted">{{ row.detail.row_count ?? 0 }} 行 · {{ formatDate(String(row.detail.exported_at || '')) }}</span>
                      <span v-else class="muted">尚未获取，开始解析时将自动生成</span>
                    </div>
                    <div class="parser-export-actions">
                      <el-button
                        v-if="auth.canOperate && canExportParserTables"
                        :icon="row.ready ? Refresh : Download"
                        :loading="exportingTable === row.table"
                        :disabled="Boolean(exportingTable && exportingTable !== row.table)"
                        @click="exportParserTable(row.table)"
                      >{{ row.ready ? '刷新 CSV' : '获取 CSV' }}</el-button>
                      <el-tooltip v-if="row.artifactId" content="下载当前快照" placement="top">
                        <el-button :icon="Download" circle plain aria-label="下载当前 CSV 快照" @click="download(row.artifactId)" />
                      </el-tooltip>
                    </div>
                  </div>
                </div>
              </section>

              <section v-if="selectedStep.node_type === 'data_statistics'" class="detail-section statistics-panel">
                <div class="section-heading">
                  <div>
                    <h3>统计输入 CSV</h3>
                    <p class="muted">仅可选择当前节点前最近一次成功解析生成的 CSV；统计脚本将直接读取远端文件。</p>
                  </div>
                  <div class="parser-export-actions">
                    <el-tag v-if="canSelectStatisticsInputs" type="success" effect="plain">可选择</el-tag>
                    <el-button v-if="canSelectStatisticsInputs" :icon="Refresh" :loading="loadingStatisticsCsvFiles" circle plain aria-label="刷新统计 CSV" @click="refreshStatisticsCsvFiles" />
                  </div>
                </div>
                <p v-if="statisticsCsvDirectory" class="muted mono">{{ statisticsCsvDirectory }}</p>
                <el-checkbox-group v-model="selectedRelativePaths" class="statistics-input-list" :disabled="!canSelectStatisticsInputs" v-loading="loadingStatisticsCsvFiles">
                  <el-checkbox v-for="file in displayedStatisticsCsvFiles" :key="file.relative_path" :value="file.relative_path">
                    <span class="statistics-input-copy">
                      <strong>{{ file.relative_path }}</strong>
                      <small>最近解析结果 · {{ formatBytes(file.size) }} · {{ formatDate(file.modified_at) }}</small>
                    </span>
                  </el-checkbox>
                </el-checkbox-group>
                <div v-if="!loadingStatisticsCsvFiles && !displayedStatisticsCsvFiles.length" class="empty-line">最近一次解析结果中暂无可统计的 CSV</div>
                <div v-if="auth.canOperate && canSelectStatisticsInputs" class="statistics-selection-actions">
                  <span class="muted">已勾选 {{ selectedRelativePaths.length }} 个<span v-if="statisticsSelectionDirty"> · 尚未保存</span></span>
                  <el-button type="primary" :loading="savingStatisticsInputs" :disabled="!selectedRelativePaths.length || !statisticsSelectionDirty" @click="saveStatisticsInputs">保存输入选择</el-button>
                </div>
              </section>

              <section v-show="showWorkflowTerminal" class="detail-section workflow-terminal-section">
                <div class="section-heading">
                  <div>
                    <h3>{{ workflowTerminalTitle }}</h3>
                    <p class="muted">{{ workflowTerminalDescription }}</p>
                  </div>
                  <el-tag v-if="workflowTerminalResource" type="success" effect="plain">{{ workflowTerminalResource.name }}</el-tag>
                </div>
                <SshTerminalPanel
                  v-if="remResource"
                  v-show="workflowTerminalKind === 'rem'"
                  ref="remWorkflowTerminalPanel"
                  :resource-id="remResource.id"
                  :title="remResource.name"
                  :subtitle="remTerminalSubtitle"
                  :active="workflowTerminalKind === 'rem'"
                  :min-height="320"
                  @status="message => handleWorkflowTerminalStatus('rem', message)"
                  @error="message => handleWorkflowTerminalError('rem', message)"
                  @workflow-command="message => handleWorkflowTerminalCommand('rem', message)"
                />
                <SshTerminalPanel
                  v-if="marketResource"
                  v-show="workflowTerminalKind === 'market'"
                  ref="marketWorkflowTerminalPanel"
                  :resource-id="marketResource.id"
                  :title="marketResource.name"
                  :subtitle="marketTerminalSubtitle"
                  :active="workflowTerminalKind === 'market'"
                  :min-height="320"
                  @status="message => handleWorkflowTerminalStatus('market', message)"
                  @error="message => handleWorkflowTerminalError('market', message)"
                  @workflow-command="message => handleWorkflowTerminalCommand('market', message)"
                />
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
                  :auto-connect="Boolean(selectedStep?.result_summary?.process_started && selectedStep?.result_summary?.session_status === 'running')"
                  :socket-path="orderTerminalSocketPath"
                  read-only
                  :min-height="320"
                  @status="message => handleWorkflowTerminalStatus('order', message)"
                  @error="message => handleWorkflowTerminalError('order', message)"
                />
                <div v-if="workflowTerminalKind === 'order' && selectedStep?.id === currentStep?.id && selectedStep?.status === 'waiting'" class="order-action-panel">
                  <div class="order-action-heading">
                    <strong>发单指令</strong>
                    <span class="muted">{{ availableOrderActions.length }} 项可用</span>
                  </div>
                  <div class="order-action-buttons">
                    <el-button
                      v-for="actionName in availableOrderActions"
                      :key="actionName"
                      :type="isDangerousOrderAction(actionName) ? 'danger' : actionName === defaultOrderAction ? 'primary' : 'default'"
                      :plain="actionName !== defaultOrderAction"
                      :icon="VideoPlay"
                      :disabled="!canSendOrderActions"
                      :loading="sendingOrderAction === actionName"
                      @click="sendOrderAction(actionName)"
                    >{{ actionName }}</el-button>
                  </div>
                  <div v-if="orderActionUnresolved" class="order-action-resolution">
                    <el-tag type="warning">最近指令结果待确认</el-tag>
                    <el-button type="warning" @click="confirmCurrentOrderAction">确认已发送</el-button>
                    <el-button type="danger" plain @click="retryUnknownOrderAction">重试节点</el-button>
                  </div>
                  <div v-if="recentOrderActionHistory.length" class="order-action-history">
                    <div class="order-action-history-heading">
                      <strong>最近发送</strong>
                      <span class="muted">{{ recentOrderActionHistory.length }} 条</span>
                    </div>
                    <div v-for="item in recentOrderActionHistory" :key="item.request_id" class="order-action-history-row">
                      <time class="mono">{{ formatTime(item.finished_at || item.started_at) }}</time>
                      <code>{{ item.action }}</code>
                      <el-tag :type="item.status === 'dispatched' ? 'success' : item.status === 'unknown' ? 'warning' : 'info'" size="small">
                        {{ item.status === 'dispatched' ? '已发送' : item.status === 'unknown' ? '待确认' : '发送中' }}
                      </el-tag>
                    </div>
                  </div>
                </div>
                <div v-if="showWorkflowTerminal && !workflowTerminalResource" class="empty-line">当前运行没有{{ workflowTerminalResourceText }}，无法加载 SSH 终端</div>
              </section>

              <section v-if="selectedStep.node_type === 'wiring_confirmation'" class="detail-section wiring-detail-section">
                <div class="section-heading">
                  <h3>接线拓扑</h3>
                  <div v-if="editingWiringNames" class="wiring-name-actions">
                    <el-button :icon="Close" :disabled="savingWiringNames" @click="cancelEditingWiringNames">取消</el-button>
                    <el-button
                      type="primary"
                      :icon="Check"
                      :loading="savingWiringNames"
                      :disabled="!wiringNamesDirty || Boolean(wiringValidationMessage)"
                      @click="saveWiringInterfaceNames"
                    >保存</el-button>
                  </div>
                  <el-button
                    v-else-if="canEditWiringNames"
                    :icon="EditPen"
                    @click="startEditingWiringNames"
                  >编辑网卡名称</el-button>
                </div>
                <el-alert
                  v-if="editingWiringNames && wiringValidationMessage"
                  :title="wiringValidationMessage"
                  type="warning"
                  :closable="false"
                  show-icon
                />
                <WiringTopologyDiagram
                  :snapshot="wiringSnapshot"
                  :editable="editingWiringNames"
                  empty-message="该历史节点使用旧版占位图，确认流程仍可正常执行"
                  @interface-name-change="updateWiringInterfaceName"
                />
              </section>

              <section class="detail-section">
                <h3>节点配置</h3>
                <dl class="info-list">
                  <template v-for="row in configRows" :key="row.label">
                    <dt>{{ row.label }}</dt>
                    <dd :class="{ mono: row.mono }">{{ row.value || '-' }}</dd>
                  </template>
                </dl>
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

                <div v-if="statisticsResults.length" class="statistics-results">
                  <div class="statistics-result-toolbar">
                    <strong>统计结果</strong>
                    <el-radio-group v-model="statisticsUnit" size="small">
                      <el-radio-button value="ns">ns</el-radio-button>
                      <el-radio-button value="us">us</el-radio-button>
                    </el-radio-group>
                  </div>
                  <section v-for="result in statisticsResults" :key="String(result.source_path || result.source_file)" class="statistics-result-card">
                    <div class="statistics-result-title">
                      <div><strong>{{ result.source_path || result.source_file }}</strong><span class="muted">{{ result.sample_count }} 个有效样本</span></div>
                      <el-tag effect="plain">{{ statisticsUnit }}</el-tag>
                    </div>
                    <div class="statistics-excluded">
                      <span>超上限 {{ (result.excluded_counts as any)?.above_limit || 0 }}</span>
                      <span>负数 {{ (result.excluded_counts as any)?.negative || 0 }}</span>
                      <span>无效 {{ (result.excluded_counts as any)?.invalid || 0 }}</span>
                    </div>
                    <el-table :data="Array.isArray(result.metrics) ? result.metrics : []" size="small" border>
                      <el-table-column prop="label" label="指标" />
                      <el-table-column label="值">
                        <template #default="scope"><strong>{{ displayStatisticsValue(scope.row.value) }}</strong> {{ statisticsUnit }}</template>
                      </el-table-column>
                    </el-table>
                  </section>
                </div>

                <div v-if="selectedArtifacts.length" class="artifact-links">
                  <template v-for="artifact in selectedArtifacts" :key="artifact.id">
                    <el-button
                      v-if="canDownloadArtifact(artifact.artifact_type)"
                      class="artifact-download-button"
                      :icon="Download"
                      plain
                      type="primary"
                      :title="artifact.name"
                      @click="download(artifact.id)"
                    >
                      <span class="artifact-name">{{ artifact.name }}</span>
                    </el-button>
                    <el-tag v-else class="artifact-restricted" type="info" effect="plain">{{ artifact.name }} · 受限</el-tag>
                  </template>
                </div>

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
            <div class="metrics-table-scroll">
              <el-table :data="metricRows" empty-text="暂无指标" class="metrics-table">
                <el-table-column label="指标" min-width="140">
                  <template #default="scope"><span class="metric-label">{{ scope.row.displayName }}</span></template>
                </el-table-column>
                <el-table-column label="值" width="170">
                  <template #default="scope">
                    <span class="metric-value"><strong>{{ Number(scope.row.value).toFixed(3) }}</strong><span>{{ scope.row.unit }}</span></span>
                  </template>
                </el-table-column>
                <el-table-column prop="sample_count" label="样本数" width="100" />
                <el-table-column label="数据来源" min-width="280">
                  <template #default="scope">
                    <el-tooltip :disabled="!scope.row.sourcePath" :content="scope.row.sourcePath" placement="top-start">
                      <span class="metric-source">{{ scope.row.sourceFile }}</span>
                    </el-tooltip>
                  </template>
                </el-table-column>
              </el-table>
            </div>
            <div v-if="run.verdict" class="verdict"><h3>结论</h3><p>最终结论：{{ run.verdict.final_result || '待复核' }}</p><p>{{ run.verdict.issue_description }}</p><p class="muted">{{ run.verdict.notes }}</p></div>
          </el-tab-pane>

          <el-tab-pane label="产物与报告" name="artifacts">
            <div v-if="canRegenerateReports" class="section-heading">
              <div><h3>报告版本</h3><p class="muted">重新生成会创建下一版本，历史文件保持不变。</p></div>
              <el-button :icon="Refresh" :loading="regeneratingReports" @click="regenerateReports">重新生成报告</el-button>
            </div>
            <el-table :data="artifactsNewestFirst" empty-text="暂无产物">
              <el-table-column prop="name" label="文件" />
              <el-table-column prop="artifact_type" label="类型" width="140" />
              <el-table-column label="大小" width="110"><template #default="scope">{{ formatBytes(scope.row.size) }}</template></el-table-column>
              <el-table-column prop="checksum" label="SHA-256" show-overflow-tooltip />
              <el-table-column width="90"><template #default="scope">
                <el-button v-if="canDownloadArtifact(scope.row.artifact_type)" link type="primary" @click="download(scope.row.id)">下载</el-button>
                <el-tag v-else type="info" effect="plain" size="small">受限</el-tag>
              </template></el-table-column>
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

    <el-dialog v-model="verdictDialog" :title="run.verdict ? '更新人工复核结论' : '提交人工复核结论'" width="600px">
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

<style scoped src="@/styles/run-detail.css"></style>
