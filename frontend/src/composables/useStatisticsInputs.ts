import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import { ElMessage } from '@/ui/elementPlusServices'
import { api, errorMessage } from '@/api/client'
import type { JsonMap, RunDetail, RunStep } from '@/types/run'

interface StatisticsOptions {
  canOperate: ComputedRef<boolean>
  currentStep: ComputedRef<RunStep | null>
  selectedStep: ComputedRef<RunStep | null>
  run: Ref<RunDetail | null>
  runId: number
  reload: () => Promise<void>
}

function selectionIds(step: RunStep | null) {
  const selection = step?.result_summary?.statistics_selection
  if (!selection || typeof selection !== 'object' || Array.isArray(selection)) return []
  const inputs = (selection as JsonMap).inputs
  return Array.isArray(inputs)
    ? inputs.map(item => Number((item as JsonMap)?.artifact_id)).filter(Number.isFinite)
    : []
}

function selectionPaths(step: RunStep | null) {
  const selection = step?.result_summary?.statistics_selection
  if (!selection || typeof selection !== 'object' || Array.isArray(selection)) return []
  const inputs = (selection as JsonMap).inputs
  return Array.isArray(inputs)
    ? inputs.map(item => String((item as JsonMap)?.relative_path || '')).filter(Boolean)
    : []
}

function sortedPaths(paths: string[]) {
  return [...paths].sort().join('\n')
}

export interface StatisticsCsvFile {
  relative_path: string
  filename: string
  source: 'root' | 'current_run'
  size: number
  modified_at: string
}

export interface StatisticsAnalysisMetadata {
  analysis_no: number
  status: 'running' | 'succeeded' | 'failed'
  config_revision: number
  inputs: JsonMap[]
  max_latency_ns: number | null
  script: JsonMap
  reserved_at: string
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  error_code: string | null
  artifact_id: number | null
  artifact_checksum: string | null
  artifact_size: number | null
}

export interface StatisticsAnalysisDetail {
  analysis: StatisticsAnalysisMetadata
  artifact: JsonMap
}

function isJsonMap(value: unknown): value is JsonMap {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function positiveInteger(value: unknown): number | null {
  const numeric = Number(value)
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null
}

function savedMaxLatencyNs(step: RunStep | null) {
  return positiveInteger(step?.config_snapshot?.max_latency_ns) ?? 999999999
}

function analysisMetadata(value: unknown): StatisticsAnalysisMetadata | null {
  if (!isJsonMap(value)) return null
  const analysisNo = positiveInteger(value.analysis_no)
  const configRevision = Number(value.config_revision)
  if (
    !analysisNo
    || !Number.isInteger(configRevision)
    || configRevision < 0
    || !['running', 'succeeded', 'failed'].includes(String(value.status))
    || typeof value.reserved_at !== 'string'
  ) return null
  return {
    analysis_no: analysisNo,
    status: value.status as StatisticsAnalysisMetadata['status'],
    config_revision: configRevision,
    inputs: Array.isArray(value.inputs) ? value.inputs.filter(isJsonMap) : [],
    max_latency_ns: positiveInteger(value.max_latency_ns),
    script: isJsonMap(value.script) ? value.script : {},
    reserved_at: value.reserved_at,
    started_at: typeof value.started_at === 'string' ? value.started_at : null,
    finished_at: typeof value.finished_at === 'string' ? value.finished_at : null,
    duration_ms: Number.isInteger(value.duration_ms) && Number(value.duration_ms) >= 0 ? Number(value.duration_ms) : null,
    error_code: typeof value.error_code === 'string' ? value.error_code : null,
    artifact_id: positiveInteger(value.artifact_id),
    artifact_checksum: typeof value.artifact_checksum === 'string' ? value.artifact_checksum : null,
    artifact_size: Number.isInteger(value.artifact_size) && Number(value.artifact_size) >= 0 ? Number(value.artifact_size) : null,
  }
}

export function useStatisticsInputs(options: StatisticsOptions) {
  const { canOperate, currentStep, selectedStep, run, runId, reload } = options
  const selectedRelativePaths = ref<string[]>([])
  const savingStatisticsInputs = ref(false)
  const loadingStatisticsCsvFiles = ref(false)
  const statisticsCsvFiles = ref<StatisticsCsvFile[]>([])
  const statisticsCsvDirectory = ref('')
  const statisticsUnit = ref<'ns' | 'us'>('ns')
  const statisticsMaxLatencyNsDraft = ref(999999999)
  const loadingStatisticsAnalyses = ref(false)
  const loadingStatisticsAnalysisNo = ref<number | null>(null)
  const statisticsAnalyses = ref<StatisticsAnalysisMetadata[]>([])
  const statisticsAnalysisDetails = ref<Record<number, StatisticsAnalysisDetail>>({})
  let latestStatisticsAnalysesRequest = 0
  let latestStatisticsAnalysisDetailRequest = 0
  const savedArtifactIds = computed(() => selectionIds(selectedStep.value))
  const savedRelativePaths = computed(() => selectionPaths(selectedStep.value))
  const savedRemoteInputs = computed<StatisticsCsvFile[]>(() => {
    const selection = selectedStep.value?.result_summary?.statistics_selection
    if (!selection || typeof selection !== 'object' || Array.isArray(selection)) return []
    const inputs = (selection as JsonMap).inputs
    if (!Array.isArray(inputs)) return []
    return inputs.filter(item => {
      const value = item as JsonMap
      return typeof value.relative_path === 'string' && typeof value.filename === 'string'
    }) as unknown as StatisticsCsvFile[]
  })
  const isCurrentStatisticsStep = computed(() => Boolean(
    selectedStep.value?.node_type === 'data_statistics'
    && selectedStep.value.id === currentStep.value?.id,
  ))
  const statisticsConfigPhaseEditable = computed(() => Boolean(
    isCurrentStatisticsStep.value
    && (
      (run.value?.status === 'awaiting_step_start' && currentStep.value?.status === 'pending')
      || (run.value?.status === 'awaiting_step_retry' && currentStep.value?.status === 'failed')
      || (run.value?.status === 'awaiting_step_completion' && currentStep.value?.status === 'waiting')
    ),
  ))
  const canEditStatisticsConfig = computed(() => Boolean(
    canOperate.value
    && statisticsConfigPhaseEditable.value
    && !savingStatisticsInputs.value,
  ))
  const statisticsConfigReadonlyReason = computed<
    'unauthorized' | 'temporarily_unavailable' | 'frozen' | null
  >(() => {
    if (selectedStep.value?.node_type !== 'data_statistics') return null
    if (!canOperate.value) return 'unauthorized'
    if (selectedStep.value.status === 'succeeded') return 'frozen'
    if (!statisticsConfigPhaseEditable.value) return 'temporarily_unavailable'
    return null
  })
  const canSelectStatisticsInputs = canEditStatisticsConfig
  const statisticsSelectionDirty = computed(
    () => sortedPaths(selectedRelativePaths.value) !== sortedPaths(savedRelativePaths.value),
  )
  const statisticsThresholdValid = computed(() => positiveInteger(statisticsMaxLatencyNsDraft.value) !== null)
  const statisticsConfigDirty = computed(() => (
    statisticsSelectionDirty.value || statisticsMaxLatencyNsDraft.value !== savedMaxLatencyNs(selectedStep.value)
  ))
  const statisticsConfigSaved = computed(() => Boolean(
    (savedRelativePaths.value.length || savedArtifactIds.value.length)
      && statisticsThresholdValid.value
      && !statisticsConfigDirty.value
      && !savingStatisticsInputs.value,
  ))
  const statisticsConfigReady = computed(() => Boolean(
    selectedRelativePaths.value.length
      && statisticsThresholdValid.value
      && !savingStatisticsInputs.value,
  ))
  const statisticsSelectionReady = statisticsConfigSaved
  const statisticsCompletionStale = computed(() => {
    const summary = selectedStep.value?.result_summary
    if (!summary) return false
    const hasRevision = Object.prototype.hasOwnProperty.call(summary, 'statistics_config_revision')
    const hasHistory = Object.prototype.hasOwnProperty.call(summary, 'statistics_analyses')
    if (!hasRevision && !hasHistory) return false
    const revision = Number(summary.statistics_config_revision ?? 0)
    const latestRevision = Number(summary.statistics_latest_success_revision)
    const latestAnalysisNo = positiveInteger(summary.statistics_latest_success_analysis_no)
    if (!Number.isInteger(revision) || revision < 0 || latestRevision !== revision || !latestAnalysisNo) return true
    const history = summary.statistics_analyses
    if (!Array.isArray(history)) return true
    const latest = history.find(item => isJsonMap(item) && item.analysis_no === latestAnalysisNo)
    return !isJsonMap(latest) || latest.status !== 'succeeded' || latest.config_revision !== revision
  })
  const statisticsCompletionBlocked = computed(() => {
    const summary = selectedStep.value?.result_summary
    const hasRevision = Boolean(
      summary && Object.prototype.hasOwnProperty.call(summary, 'statistics_config_revision'),
    )
    const hasHistory = Boolean(
      summary && Object.prototype.hasOwnProperty.call(summary, 'statistics_analyses'),
    )
    const hasLegacyResults = Array.isArray(summary?.statistics_results)
      && summary.statistics_results.length > 0
    if (!hasRevision && !hasHistory && hasLegacyResults) return false
    return !statisticsConfigSaved.value || statisticsCompletionStale.value
  })
  const displayedStatisticsCsvFiles = computed(() => (
    canSelectStatisticsInputs.value ? statisticsCsvFiles.value : savedRemoteInputs.value
  ))
  const statisticsResults = computed(() => {
    const direct = selectedStep.value?.result_summary?.statistics_results
    if (Array.isArray(direct)) return direct.filter(item => item && typeof item === 'object') as JsonMap[]
    const attempts = selectedStep.value?.result_summary?.statistics_attempts
    if (!Array.isArray(attempts)) return []
    return attempts
      .map(item => item && typeof item === 'object' ? (item as JsonMap).result : null)
      .filter(item => item && typeof item === 'object') as JsonMap[]
  })

  function displayStatisticsValue(value: unknown) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) return '-'
    return statisticsUnit.value === 'us'
      ? (numeric / 1000).toFixed(3)
      : numeric.toFixed(3)
  }

  function applySavedStatisticsConfig(step: RunStep, responseData: unknown) {
    if (!isJsonMap(responseData)) return
    const inputs = Array.isArray(responseData.inputs) ? responseData.inputs.filter(isJsonMap) : []
    const maxLatencyNs = positiveInteger(responseData.max_latency_ns)
    const revision = Number(responseData.statistics_config_revision)
    if (inputs.length) {
      step.result_summary = {
        ...step.result_summary,
        statistics_selection: { inputs },
      }
      selectedRelativePaths.value = inputs
        .map(item => typeof item.relative_path === 'string' ? item.relative_path : '')
        .filter(Boolean)
    }
    if (maxLatencyNs) {
      step.config_snapshot = { ...step.config_snapshot, max_latency_ns: maxLatencyNs }
      statisticsMaxLatencyNsDraft.value = maxLatencyNs
    }
    if (Number.isInteger(revision) && revision >= 0) {
      step.result_summary = {
        ...step.result_summary,
        statistics_config_revision: revision,
      }
    }
  }

  async function saveStatisticsConfig() {
    const step = currentStep.value
    const maxLatencyNs = positiveInteger(statisticsMaxLatencyNsDraft.value)
    if (!step || !canEditStatisticsConfig.value || !selectedRelativePaths.value.length || !maxLatencyNs) return
    savingStatisticsInputs.value = true
    try {
      const response = await api.put(`/runs/${runId}/steps/${step.id}/statistics-config`, {
        relative_paths: selectedRelativePaths.value,
        max_latency_ns: maxLatencyNs,
      })
      applySavedStatisticsConfig(step, response.data)
      ElMessage.success('统计配置已保存')
      await reload()
    } catch (error) {
      ElMessage.error(errorMessage(error))
    } finally {
      savingStatisticsInputs.value = false
    }
  }

  async function saveStatisticsInputs() {
    await saveStatisticsConfig()
  }

  async function refreshStatisticsCsvFiles() {
    const step = currentStep.value
    if (!step || !canSelectStatisticsInputs.value) {
      statisticsCsvFiles.value = []
      statisticsCsvDirectory.value = ''
      return
    }
    loadingStatisticsCsvFiles.value = true
    try {
      const response = await api.get(`/runs/${runId}/steps/${step.id}/statistics-csv-files`)
      statisticsCsvDirectory.value = String(response.data?.directory || '')
      statisticsCsvFiles.value = Array.isArray(response.data?.files) ? response.data.files : []
    } catch (error) {
      statisticsCsvFiles.value = []
      ElMessage.error(errorMessage(error))
    } finally {
      loadingStatisticsCsvFiles.value = false
    }
  }

  async function refreshStatisticsAnalyses() {
    const step = selectedStep.value
    if (!step || step.node_type !== 'data_statistics') {
      statisticsAnalyses.value = []
      return
    }
    const requestedStepId = step.id
    const requestId = ++latestStatisticsAnalysesRequest
    loadingStatisticsAnalyses.value = true
    try {
      const response = await api.get(`/runs/${runId}/steps/${requestedStepId}/statistics-analyses`)
      if (requestId !== latestStatisticsAnalysesRequest || selectedStep.value?.id !== requestedStepId) return
      statisticsAnalyses.value = (Array.isArray(response.data) ? response.data : [])
        .map(analysisMetadata)
        .filter((item): item is StatisticsAnalysisMetadata => item !== null)
        .sort((left, right) => right.analysis_no - left.analysis_no)
    } catch (error) {
      if (requestId !== latestStatisticsAnalysesRequest || selectedStep.value?.id !== requestedStepId) return
      statisticsAnalyses.value = []
      ElMessage.error(errorMessage(error))
    } finally {
      if (requestId === latestStatisticsAnalysesRequest && selectedStep.value?.id === requestedStepId) {
        loadingStatisticsAnalyses.value = false
      }
    }
  }

  async function loadStatisticsAnalysisDetail(analysisNo: number) {
    const cached = statisticsAnalysisDetails.value[analysisNo]
    if (cached) return cached
    const step = selectedStep.value
    if (!step || step.node_type !== 'data_statistics' || !positiveInteger(analysisNo)) return null
    const requestedStepId = step.id
    const requestId = ++latestStatisticsAnalysisDetailRequest
    loadingStatisticsAnalysisNo.value = analysisNo
    try {
      const response = await api.get(`/runs/${runId}/steps/${requestedStepId}/statistics-analyses/${analysisNo}`)
      if (requestId !== latestStatisticsAnalysisDetailRequest || selectedStep.value?.id !== requestedStepId) return null
      if (!isJsonMap(response.data) || !isJsonMap(response.data.artifact)) return null
      const analysis = analysisMetadata(response.data.analysis)
      if (!analysis || analysis.analysis_no !== analysisNo) return null
      const detail = { analysis, artifact: response.data.artifact }
      statisticsAnalysisDetails.value = { ...statisticsAnalysisDetails.value, [analysisNo]: detail }
      return detail
    } catch (error) {
      if (requestId !== latestStatisticsAnalysisDetailRequest || selectedStep.value?.id !== requestedStepId) return null
      ElMessage.error(errorMessage(error))
      return null
    } finally {
      if (requestId === latestStatisticsAnalysisDetailRequest && selectedStep.value?.id === requestedStepId) {
        loadingStatisticsAnalysisNo.value = null
      }
    }
  }

  watch(
    () => `${selectedStep.value?.id || ''}:${savedRelativePaths.value.join(',')}:${savedMaxLatencyNs(selectedStep.value)}`,
    () => {
      selectedRelativePaths.value = [...savedRelativePaths.value]
      statisticsMaxLatencyNsDraft.value = savedMaxLatencyNs(selectedStep.value)
    },
    { immediate: true },
  )
  watch(
    () => selectedStep.value?.id,
    () => {
      latestStatisticsAnalysesRequest += 1
      latestStatisticsAnalysisDetailRequest += 1
      statisticsAnalyses.value = []
      statisticsAnalysisDetails.value = {}
      loadingStatisticsAnalyses.value = false
      loadingStatisticsAnalysisNo.value = null
    },
    { immediate: true },
  )
  watch(
    () => `${selectedStep.value?.id || ''}:${currentStep.value?.id || ''}:${run.value?.status || ''}:${currentStep.value?.status || ''}:${canOperate.value}`,
    () => { void refreshStatisticsCsvFiles() },
    { immediate: true },
  )

  return {
    canSelectStatisticsInputs,
    canEditStatisticsConfig,
    displayStatisticsValue,
    displayedStatisticsCsvFiles,
    isCurrentStatisticsStep,
    loadingStatisticsCsvFiles,
    refreshStatisticsCsvFiles,
    refreshStatisticsAnalyses,
    loadStatisticsAnalysisDetail,
    loadingStatisticsAnalyses,
    loadingStatisticsAnalysisNo,
    saveStatisticsInputs,
    saveStatisticsConfig,
    savingStatisticsInputs,
    selectedRelativePaths,
    statisticsCsvDirectory,
    statisticsCsvFiles,
    statisticsAnalyses,
    statisticsAnalysisDetails,
    statisticsCompletionBlocked,
    statisticsCompletionStale,
    statisticsConfigDirty,
    statisticsConfigReady,
    statisticsConfigReadonlyReason,
    statisticsConfigSaved,
    statisticsMaxLatencyNsDraft,
    statisticsResults,
    statisticsSelectionDirty,
    statisticsSelectionReady,
    statisticsThresholdValid,
    statisticsUnit,
  }
}
