<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft, Check, CircleCheck, Close, Download, EditPen, Refresh, RefreshRight, VideoPlay } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import RunCaptureDetails from '@/components/run-detail/RunCaptureDetails.vue'
import RunContractFiles from '@/components/run-detail/RunContractFiles.vue'
import RunContractPreviewDialog from '@/components/run-detail/RunContractPreviewDialog.vue'
import RunLogPanel from '@/components/run-detail/RunLogPanel.vue'
import RunComparisonPanel from '@/components/run-detail/RunComparisonPanel.vue'
import RunWorkflowStrip from '@/components/run-detail/RunWorkflowStrip.vue'
import WindowsEditcapCommand from '@/components/run-detail/WindowsEditcapCommand.vue'
import OrderConfigPanel from '@/components/OrderConfigPanel.vue'
import SshTerminalPanel from '@/components/SshTerminalPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import WiringTopologyDiagram from '@/components/WiringTopologyDiagram.vue'
import { useRunActions } from '@/composables/useRunActions'
import { useRunLifecycle } from '@/composables/useRunLifecycle'
import { useOrderActions } from '@/composables/useOrderActions'
import { useOrderRuntimeConfig } from '@/composables/useOrderRuntimeConfig'
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
const orderConfigEditorVisible = ref(false)

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
  updateWiringInterfaceIpAddress,
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
  availableParserActions,
  handleParserAction,
  handleWorkflowTerminalCommand,
  handleWorkflowTerminalError,
  handleWorkflowTerminalStatus,
  marketResource,
  marketTerminalSubtitle,
  marketWorkflowTerminalPanel,
  orderResource,
  orderTerminalSubtitle,
  orderWorkflowTerminalPanel,
  parserActionPending,
  parserResource,
  parserTerminalSubtitle,
  parserWorkflowTerminalPanel,
  remResource,
  remTerminalSubtitle,
  remWorkflowTerminalPanel,
  runWorkflowStepInTerminal,
  sendParserAction,
  showWorkflowTerminal,
  slnicResource,
  slnicTerminalSubtitle,
  slnicWorkflowTerminalPanel,
  terminalCommandPendingStepId,
  stopWorkflowTerminal,
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
  canEditOrderConfig,
  cancelEditingOrderConfig,
  editingOrderConfig,
  loadingOrderConfigs,
  orderConfigActionBlocked,
  orderConfigDirty,
  orderConfigDraft,
  orderConfigFiles,
  orderConfigValidationMessage,
  refreshOrderConfigs,
  saveOrderRuntimeConfig,
  savingOrderConfig,
  startEditingOrderConfig,
} = useOrderRuntimeConfig({
  canOperate: computed(() => auth.canOperate),
  currentStep,
  selectedStep,
  run,
  runId,
  orderResourceId: computed(() => orderResource.value?.id || null),
  reload: load,
})
const {
  actingStepId,
  action,
  cancel,
  download,
  openVerdict,
  reanalyzeStatistics,
  reanalyzingStatisticsStepId,
  regenerateReports,
  regeneratingReports,
  stepAction,
  submitVerdict,
  verdict,
  verdictDialog,
} = useRunActions({
  runId,
  reload: load,
  runTerminalStep: runWorkflowStepInTerminal,
  stopWorkflowTerminal,
})
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
  canEditStatisticsConfig,
  displayStatisticsValue,
  displayedStatisticsCsvFiles,
  loadStatisticsAnalysisDetail,
  loadingStatisticsCsvFiles,
  loadingStatisticsAnalyses,
  loadingStatisticsAnalysisNo,
  refreshStatisticsCsvFiles,
  refreshStatisticsAnalyses,
  saveStatisticsConfig,
  savingStatisticsInputs,
  selectedRelativePaths,
  statisticsAnalyses,
  statisticsAnalysisDetails,
  statisticsCsvDirectory,
  statisticsCompletionBlocked,
  statisticsCompletionStale,
  statisticsConfigDirty,
  statisticsConfigReady,
  statisticsConfigReadonlyReason,
  statisticsConfigSaved,
  statisticsMaxLatencyNsDraft,
  statisticsResults,
  statisticsThresholdValid,
  statisticsUnit,
} = useStatisticsInputs({
  canOperate: computed(() => auth.canOperate),
  currentStep,
  selectedStep,
  run,
  runId,
  reload: load,
})
const statisticsReanalysisPending = computed(() => Boolean(
  currentStep.value?.node_type === 'data_statistics'
  && reanalyzingStatisticsStepId.value === currentStep.value.id,
))
const statisticsActionBlocked = computed(() => Boolean(
  currentStep.value?.node_type === 'data_statistics'
  && (!statisticsConfigSaved.value || savingStatisticsInputs.value || statisticsReanalysisPending.value),
))
const canCompleteCurrent = computed(() => Boolean(
  currentStep.value?.status === 'waiting'
  && run.value?.status === 'awaiting_step_completion'
  && (currentStep.value.node_type !== 'order_preparation' || !orderActionUnresolved.value),
))
const expandedStatisticsAnalysisNo = ref<string | number>('')
const initializedStatisticsHistorySuccessKey = ref('')
const selectedStatisticsHasHistoryStructure = computed(() => {
  const summary = selectedStep.value?.result_summary
  return Boolean(
    selectedStep.value?.node_type === 'data_statistics'
    && summary
    && Object.prototype.hasOwnProperty.call(summary, 'statistics_analyses'),
  )
})
const selectedStatisticsHistorySignature = computed(() => {
  const step = selectedStep.value
  if (!step || step.node_type !== 'data_statistics') return ''
  const summary = step.result_summary || {}
  const history = Array.isArray(summary.statistics_analyses) ? summary.statistics_analyses : []
  let latestAnalysisNo = 0
  let latestAnalysisStatus = ''
  for (const item of history) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) continue
    const analysisNo = Number((item as JsonMap).analysis_no)
    if (!Number.isInteger(analysisNo) || analysisNo <= latestAnalysisNo) continue
    latestAnalysisNo = analysisNo
    latestAnalysisStatus = String((item as JsonMap).status || '')
  }
  return [
    step.id,
    selectedStatisticsHasHistoryStructure.value ? 'history' : 'legacy',
    latestAnalysisNo,
    latestAnalysisStatus,
    Number(summary.statistics_latest_success_analysis_no) || 0,
  ].join(':')
})
const statisticsCompletionBlockedReason = computed(() => {
  if (currentStep.value?.node_type !== 'data_statistics' || !canCompleteCurrent.value) return ''
  if (statisticsReanalysisPending.value) {
    return '完成已禁用：统计分析正在执行，请等待本次分析结束。'
  }
  if (!statisticsCompletionBlocked.value) return ''
  if (!statisticsConfigSaved.value) {
    return '完成已禁用：请先选择 CSV、填写正整数的最大延迟上限并保存分析配置。'
  }
  if (statisticsCompletionStale.value) {
    return '完成已禁用：当前分析配置尚无成功结果，请先开始或再次执行分析。'
  }
  return ''
})
const statisticsConfigReadonlyMessage = computed(() => {
  if (statisticsConfigReadonlyReason.value === 'unauthorized') {
    return '无操作权限；仅可查看已保存的 CSV、最大延迟上限和分析历史。'
  }
  if (statisticsConfigReadonlyReason.value === 'temporarily_unavailable') {
    return '当前阶段暂不可编辑；仅可查看已保存的分析配置。'
  }
  if (statisticsConfigReadonlyReason.value === 'frozen') {
    return '节点已完成冻结；只读展示执行时保存的 CSV 与最大延迟上限。'
  }
  return '选择 CSV 并设置最大延迟上限后统一保存；修改已保存配置后需要重新分析。'
})
const canSendParserActions = computed(() => Boolean(
  currentStep.value?.node_type === 'parser_parse'
  && currentStep.value.status === 'waiting'
  && run.value?.status === 'awaiting_step_completion'
  && !parserActionPending.value,
))
const recentParserActionHistory = computed(() => {
  if (currentStep.value?.node_type !== 'parser_parse') return []
  const history = currentStep.value.result_summary?.parser_action_history
  if (!Array.isArray(history)) return []
  return history
    .filter((item): item is JsonMap => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    .slice(-10)
    .reverse()
})
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

function statisticsAnalysisStatusText(status: string) {
  return status === 'succeeded' ? '成功' : status === 'failed' ? '失败' : '分析中'
}

function statisticsAnalysisStatusType(status: string): 'success' | 'danger' | 'warning' {
  return status === 'succeeded' ? 'success' : status === 'failed' ? 'danger' : 'warning'
}

function statisticsAnalysisInputs(analysisNo: number) {
  const analysis = statisticsAnalyses.value.find(item => item.analysis_no === analysisNo)
  if (!analysis) return []
  return analysis.inputs
    .map(input => String(input.relative_path || input.filename || ''))
    .filter(Boolean)
}

function statisticsAnalysisDetailResults(analysisNo: number) {
  const results = statisticsAnalysisDetails.value[analysisNo]?.artifact.results
  return Array.isArray(results)
    ? results.filter((item): item is JsonMap => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    : []
}

function statisticsAnalysisFailureDescription(analysisNo: number) {
  const error = statisticsAnalysisDetails.value[analysisNo]?.artifact.error
  const message = error && typeof error === 'object' && !Array.isArray(error)
    ? String((error as JsonMap).message || '')
    : ''
  let guidance = '该次失败记录已保留；可修改配置后重新分析。'
  if (statisticsConfigReadonlyReason.value === 'unauthorized') {
    guidance = '无操作权限，仅可查看失败记录。'
  } else if (statisticsConfigReadonlyReason.value === 'temporarily_unavailable') {
    guidance = '当前阶段暂不可编辑；失败记录保留供审计。'
  } else if (statisticsConfigReadonlyReason.value === 'frozen') {
    guidance = '节点已完成冻结；失败记录保留供审计。'
  }
  return message ? `${message}；${guidance}` : guidance
}

async function loadStatisticsHistory() {
  const step = selectedStep.value
  if (!step || step.node_type !== 'data_statistics') {
    expandedStatisticsAnalysisNo.value = ''
    initializedStatisticsHistorySuccessKey.value = ''
    return
  }
  const stepId = step.id
  await refreshStatisticsAnalyses()
  if (selectedStep.value?.id !== stepId) return
  const latestSuccess = statisticsAnalyses.value.find(analysis => analysis.status === 'succeeded')
  const successKey = `${stepId}:${latestSuccess?.analysis_no || 0}`
  if (initializedStatisticsHistorySuccessKey.value === successKey) return
  initializedStatisticsHistorySuccessKey.value = successKey
  if (!latestSuccess) return
  expandedStatisticsAnalysisNo.value = String(latestSuccess.analysis_no)
  void loadStatisticsAnalysisDetail(latestSuccess.analysis_no)
}

function handleStatisticsHistoryChange(activeName: string | number) {
  const analysisNo = Number(activeName)
  if (Number.isInteger(analysisNo) && analysisNo > 0) void loadStatisticsAnalysisDetail(analysisNo)
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
watch(
  selectedStatisticsHistorySignature,
  () => { void loadStatisticsHistory() },
  { immediate: true },
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
        <div class="run-title-line"><h1 class="page-title mono">{{ run.run_number }}</h1><StatusBadge :status="run.status" show-raw /><span class="run-progress mono">{{ run.progress }}%</span></div>
        <p class="muted">{{ businessText[run.business_code] }} · {{ run.config_snapshot?.plan?.name }} / {{ run.config_snapshot?.scenario?.name }}</p>
      </div>
      <div v-if="auth.canOperate" class="toolbar">
        <el-button v-if="canStart" type="primary" @click="action('start', '运行已就绪')">启动运行</el-button>
        <el-button
          v-if="currentStep?.status === 'pending' && run.status === 'awaiting_step_start'"
          type="primary"
          :icon="VideoPlay"
          :loading="actingStepId === currentStep.id || terminalCommandPendingStepId === currentStep.id"
          :disabled="Boolean(exportingTable) || statisticsActionBlocked || wiringActionBlocked || orderConfigActionBlocked || orderConfigEditorVisible"
          @click="currentStep && stepAction(currentStep, 'start')"
        >{{ currentStep.node_type === 'data_statistics' ? '开始分析' : '开始' }}</el-button>
        <el-button
          v-if="currentStep?.node_type === 'data_statistics' && currentStep.status === 'waiting' && run.status === 'awaiting_step_completion' && canEditStatisticsConfig"
          type="warning"
          :icon="Refresh"
          :loading="reanalyzingStatisticsStepId === currentStep.id"
          :disabled="!statisticsConfigSaved || savingStatisticsInputs || statisticsReanalysisPending"
          @click="currentStep && reanalyzeStatistics(currentStep)"
        >{{ statisticsCompletionStale ? '开始分析' : '再次分析' }}</el-button>
        <el-button
          v-if="canCompleteCurrent"
          type="success"
          :icon="CircleCheck"
          :loading="actingStepId === currentStep.id"
          :disabled="wiringActionBlocked || (currentStep?.node_type === 'data_statistics' && (statisticsCompletionBlocked || statisticsReanalysisPending))"
          :aria-describedby="currentStep?.node_type === 'data_statistics' && statisticsCompletionBlockedReason ? 'statistics-completion-blocked-reason' : undefined"
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
        <p
          v-if="statisticsCompletionBlockedReason"
          id="statistics-completion-blocked-reason"
          class="statistics-completion-blocked-reason"
          role="status"
        >{{ statisticsCompletionBlockedReason }}</p>
      </div>
    </div>

    <section class="summary card" aria-label="运行摘要">
      <div><span class="muted">当前状态</span><p><StatusBadge :status="run.status" show-raw /></p></div>
      <div><span class="muted">总体进度</span><el-progress :percentage="run.progress" :stroke-width="8" /></div>
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
                    <h3>分析配置</h3>
                    <p class="muted">{{ statisticsConfigReadonlyMessage }}</p>
                  </div>
                  <div class="parser-export-actions">
                    <el-tag v-if="statisticsConfigSaved" type="success" effect="plain">已保存</el-tag>
                    <el-tag v-else-if="statisticsConfigDirty" type="warning" effect="plain">未保存</el-tag>
                    <el-button v-if="canEditStatisticsConfig" :icon="Refresh" :loading="loadingStatisticsCsvFiles" :disabled="statisticsReanalysisPending" circle plain aria-label="刷新统计 CSV" @click="refreshStatisticsCsvFiles" />
                  </div>
                </div>
                <p v-if="statisticsCsvDirectory" class="muted mono">{{ statisticsCsvDirectory }}</p>
                <fieldset class="statistics-config-form">
                  <legend>分析配置</legend>
                  <fieldset class="statistics-config-field statistics-csv-field">
                    <legend id="statistics-csv-input-label">CSV 输入</legend>
                    <small id="statistics-csv-input-description">仅可选择当前节点前最近一次成功解析生成的 CSV；统计脚本将直接读取远端文件。</small>
                    <el-checkbox-group
                      v-model="selectedRelativePaths"
                      class="statistics-input-list"
                      :disabled="!canEditStatisticsConfig || statisticsReanalysisPending"
                      aria-labelledby="statistics-csv-input-label"
                      aria-describedby="statistics-csv-input-description"
                      v-loading="loadingStatisticsCsvFiles"
                    >
                      <el-checkbox v-for="file in displayedStatisticsCsvFiles" :key="file.relative_path" :value="file.relative_path">
                        <span class="statistics-input-copy">
                          <strong>{{ file.relative_path }}</strong>
                          <small>最近解析结果 · {{ formatBytes(file.size) }} · {{ formatDate(file.modified_at) }}</small>
                        </span>
                      </el-checkbox>
                    </el-checkbox-group>
                    <span v-if="!loadingStatisticsCsvFiles && !displayedStatisticsCsvFiles.length" class="empty-line">最近一次解析结果中暂无可统计的 CSV</span>
                  </fieldset>
                  <div class="statistics-config-field statistics-threshold-field">
                    <label for="statistics-max-latency-ns">最大延迟上限（ns）</label>
                    <small id="statistics-max-latency-description">仅纳入不超过该正整数上限的有效延迟样本。</small>
                    <el-input-number
                      id="statistics-max-latency-ns"
                      v-model="statisticsMaxLatencyNsDraft"
                      :min="1"
                      :precision="0"
                      :step="1"
                      :disabled="!canEditStatisticsConfig || statisticsReanalysisPending"
                      aria-describedby="statistics-max-latency-description"
                      :aria-invalid="!statisticsThresholdValid"
                      aria-label="最大延迟上限（纳秒）"
                    />
                    <span v-if="!statisticsThresholdValid" class="statistics-field-error" role="alert">最大延迟上限必须是正整数。</span>
                  </div>
                </fieldset>
                <div v-if="auth.canOperate && canEditStatisticsConfig" class="statistics-selection-actions">
                  <span class="muted">已勾选 {{ selectedRelativePaths.length }} 个 CSV<span v-if="statisticsConfigDirty"> · 尚未保存</span></span>
                  <el-button type="primary" :loading="savingStatisticsInputs" :disabled="!statisticsConfigDirty || !statisticsConfigReady || statisticsReanalysisPending" @click="saveStatisticsConfig">保存分析配置</el-button>
                </div>
              </section>

              <section v-if="selectedStep.node_type === 'data_statistics'" class="detail-section statistics-history-section" aria-labelledby="statistics-history-heading">
                <div class="section-heading">
                  <div>
                    <h3 id="statistics-history-heading">分析历史</h3>
                    <p class="muted">按分析序号从新到旧排列；最新成功分析会默认展开，其他记录在展开时加载详情。</p>
                  </div>
                  <el-button :icon="Refresh" :loading="loadingStatisticsAnalyses" plain @click="loadStatisticsHistory">刷新历史</el-button>
                </div>
                <p class="statistics-history-status muted" role="status">{{ loadingStatisticsAnalyses ? '正在加载分析历史…' : `已加载 ${statisticsAnalyses.length} 条分析记录` }}</p>
                <div v-if="!loadingStatisticsAnalyses && !statisticsAnalyses.length" class="empty-line">{{ selectedStatisticsHasHistoryStructure ? '暂无分析历史。' : '该旧运行没有分析历史索引，仍会在下方展示现有统计结果。' }}</div>
                <el-collapse v-else v-model="expandedStatisticsAnalysisNo" accordion class="statistics-history-list" @change="handleStatisticsHistoryChange">
                  <el-collapse-item
                    v-for="analysis in statisticsAnalyses"
                    :key="analysis.analysis_no"
                    :name="String(analysis.analysis_no)"
                  >
                    <template #title>
                      <div class="statistics-history-title">
                        <strong>第 {{ analysis.analysis_no }} 次分析</strong>
                        <span class="muted">配置修订 {{ analysis.config_revision }} · {{ formatTime(analysis.finished_at || analysis.started_at || analysis.reserved_at) }}</span>
                        <el-tag :type="statisticsAnalysisStatusType(analysis.status)" size="small" effect="plain">{{ statisticsAnalysisStatusText(analysis.status) }}</el-tag>
                      </div>
                    </template>
                    <el-alert
                      v-if="analysis.status === 'failed'"
                      :title="`分析失败${analysis.error_code ? `：${analysis.error_code}` : ''}`"
                      :description="statisticsAnalysisFailureDescription(analysis.analysis_no)"
                      type="error"
                      show-icon
                      :closable="false"
                    />
                    <dl class="statistics-analysis-meta">
                      <dt>CSV 输入</dt>
                      <dd class="mono">{{ statisticsAnalysisInputs(analysis.analysis_no).join('、') || '-' }}</dd>
                      <dt>最大延迟上限</dt>
                      <dd>{{ analysis.max_latency_ns || '-' }} ns</dd>
                      <dt>完成时间</dt>
                      <dd>{{ formatDate(analysis.finished_at || analysis.started_at || analysis.reserved_at) }}</dd>
                    </dl>
                    <div v-if="loadingStatisticsAnalysisNo === analysis.analysis_no" class="statistics-history-detail-loading" role="status">正在加载本次分析详情…</div>
                    <template v-else-if="statisticsAnalysisDetails[analysis.analysis_no]">
                      <div v-if="statisticsAnalysisDetailResults(analysis.analysis_no).length" class="statistics-results statistics-history-results">
                        <section v-for="result in statisticsAnalysisDetailResults(analysis.analysis_no)" :key="String(result.source_path || result.source_file)" class="statistics-result-card">
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
                            <el-table-column label="值"><template #default="scope"><strong>{{ displayStatisticsValue(scope.row.value) }}</strong> {{ statisticsUnit }}</template></el-table-column>
                          </el-table>
                        </section>
                      </div>
                      <p v-else class="muted statistics-history-detail-empty">该次分析未产生可展示的统计指标。</p>
                    </template>
                  </el-collapse-item>
                </el-collapse>
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
                <WindowsEditcapCommand
                  v-if="workflowTerminalKind === 'slnic' && selectedStep?.node_type === 'slnic_merge_capture'"
                  :command="String(selectedStep?.result_summary?.windows_editcap_command || '')"
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
                  :min-height="320"
                  @status="message => handleWorkflowTerminalStatus('order', message)"
                  @error="message => handleWorkflowTerminalError('order', message)"
                />
                <SshTerminalPanel
                  v-if="parserResource"
                  v-show="workflowTerminalKind === 'parser'"
                  ref="parserWorkflowTerminalPanel"
                  :resource-id="parserResource.id"
                  :title="parserResource.name"
                  :subtitle="parserTerminalSubtitle"
                  :active="workflowTerminalKind === 'parser'"
                  :min-height="320"
                  @status="message => handleWorkflowTerminalStatus('parser', message)"
                  @error="message => handleWorkflowTerminalError('parser', message)"
                  @workflow-command="message => handleWorkflowTerminalCommand('parser', message)"
                  @parser-action="handleParserAction"
                />
                <div v-if="workflowTerminalKind === 'parser' && selectedStep?.id === currentStep?.id && selectedStep?.status === 'waiting'" class="order-action-panel parser-action-panel">
                  <div class="order-action-heading">
                    <strong>解析指令</strong>
                    <span class="muted">{{ availableParserActions.length }} 项可用 · 也可直接在终端输入</span>
                  </div>
                  <div class="order-action-buttons">
                    <el-button
                      v-for="actionName in availableParserActions"
                      :key="actionName"
                      :icon="VideoPlay"
                      :disabled="!canSendParserActions"
                      :loading="parserActionPending === actionName"
                      @click="sendParserAction(actionName)"
                    >{{ actionName }}</el-button>
                  </div>
                  <div v-if="recentParserActionHistory.length" class="order-action-history">
                    <div class="order-action-history-heading">
                      <strong>最近发送</strong>
                      <span class="muted">{{ recentParserActionHistory.length }} 条</span>
                    </div>
                    <div v-for="(item, index) in recentParserActionHistory" :key="`${String(item.started_at || '')}-${String(item.action || '')}-${index}`" class="order-action-history-row">
                      <time class="mono">{{ formatTime(String(item.finished_at || item.started_at || '')) }}</time>
                      <code>{{ String(item.action || '-') }}</code>
                      <el-tag type="success" size="small">已发送</el-tag>
                    </div>
                  </div>
                </div>
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
                  >编辑网卡信息</el-button>
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
                  :editable-ip="editingWiringNames"
                  empty-message="该历史节点使用旧版占位图，确认流程仍可正常执行"
                  @interface-ip-change="updateWiringInterfaceIpAddress"
                  @interface-name-change="updateWiringInterfaceName"
                />
              </section>

              <section v-if="selectedStep.node_type === 'order_preparation'" class="detail-section order-runtime-config-section">
                <div class="section-heading">
                  <div>
                    <h3>本次发单配置</h3>
                    <p class="muted">节点开始时读取所选 XML 的最新内容，不校验是否与发布版本一致。</p>
                  </div>
                  <div v-if="editingOrderConfig" class="order-runtime-config-actions">
                    <el-button :icon="Close" :disabled="savingOrderConfig" @click="cancelEditingOrderConfig">取消</el-button>
                    <el-button
                      type="primary"
                      :icon="Check"
                      :loading="savingOrderConfig"
                      :disabled="!orderConfigDirty || Boolean(orderConfigValidationMessage)"
                      @click="saveOrderRuntimeConfig"
                    >保存</el-button>
                  </div>
                  <el-button
                    v-else-if="canEditOrderConfig"
                    :icon="EditPen"
                    @click="startEditingOrderConfig"
                  >更改发单配置</el-button>
                </div>
                <template v-if="editingOrderConfig">
                  <div class="order-runtime-config-form">
                    <label class="order-runtime-config-field">
                      <span>XML 配置</span>
                      <el-select
                        v-model="orderConfigDraft.xml_filename"
                        :loading="loadingOrderConfigs"
                        filterable
                        placeholder="请选择 XML 配置"
                      >
                        <el-option v-for="file in orderConfigFiles" :key="file.name" :label="file.name" :value="file.name" />
                      </el-select>
                    </label>
                    <label class="order-runtime-config-field">
                      <span>网卡接口</span>
                      <el-input v-model="orderConfigDraft.network_interface" maxlength="15" placeholder="例如 p4p1" />
                    </label>
                  </div>
                  <div class="order-runtime-config-tools">
                    <span class="muted">需要修改 XML 节点值时，可打开完整的结构化/原文编辑器。</span>
                    <el-button :icon="EditPen" :disabled="!orderResource" @click="orderConfigEditorVisible = true">编辑 XML 内容</el-button>
                  </div>
                  <el-alert
                    v-if="orderConfigValidationMessage"
                    :title="orderConfigValidationMessage"
                    type="warning"
                    :closable="false"
                    show-icon
                  />
                </template>
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

                <div v-if="!selectedStatisticsHasHistoryStructure && statisticsResults.length" class="statistics-results statistics-legacy-results">
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

          <el-tab-pane label="运行对比" name="comparison">
            <RunComparisonPanel
              :run-id="runId"
              :can-operate="auth.canOperate"
              :has-metrics="Boolean(run.metrics.length)"
            />
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

    <el-dialog
      v-model="orderConfigEditorVisible"
      title="编辑发单 XML"
      width="94vw"
      top="3vh"
      destroy-on-close
      @closed="refreshOrderConfigs"
    >
      <OrderConfigPanel
        v-if="orderResource"
        :resource-id="orderResource.id"
        :active="orderConfigEditorVisible"
        resource-type="order"
      />
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
