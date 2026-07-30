import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import { ElMessage } from '@/ui/elementPlusServices'
import { api, errorMessage } from '@/api/client'
import type { JsonMap, RunDetail, RunStep } from '@/types/run'

interface StatisticsOptions {
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

export function useStatisticsInputs(options: StatisticsOptions) {
  const { currentStep, selectedStep, run, runId, reload } = options
  const selectedRelativePaths = ref<string[]>([])
  const savingStatisticsInputs = ref(false)
  const loadingStatisticsCsvFiles = ref(false)
  const statisticsCsvFiles = ref<StatisticsCsvFile[]>([])
  const statisticsCsvDirectory = ref('')
  const statisticsUnit = ref<'ns' | 'us'>('ns')
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
  const canSelectStatisticsInputs = computed(() => Boolean(
    isCurrentStatisticsStep.value
    && (
      (run.value?.status === 'awaiting_step_start' && currentStep.value?.status === 'pending')
      || (run.value?.status === 'awaiting_step_retry' && currentStep.value?.status === 'failed')
    )
    && !savingStatisticsInputs.value,
  ))
  const statisticsSelectionDirty = computed(
    () => sortedPaths(selectedRelativePaths.value) !== sortedPaths(savedRelativePaths.value),
  )
  const statisticsSelectionReady = computed(() => Boolean(
    (savedRelativePaths.value.length || savedArtifactIds.value.length)
      && !statisticsSelectionDirty.value
      && !savingStatisticsInputs.value,
  ))
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

  async function saveStatisticsInputs() {
    const step = currentStep.value
    if (!step || !canSelectStatisticsInputs.value || !selectedRelativePaths.value.length) return
    savingStatisticsInputs.value = true
    try {
      await api.put(`/runs/${runId}/steps/${step.id}/statistics-inputs`, {
        relative_paths: selectedRelativePaths.value,
      })
      ElMessage.success(`已选择 ${selectedRelativePaths.value.length} 个统计输入`)
      await reload()
    } catch (error) {
      ElMessage.error(errorMessage(error))
    } finally {
      savingStatisticsInputs.value = false
    }
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

  watch(
    () => `${selectedStep.value?.id || ''}:${savedRelativePaths.value.join(',')}`,
    () => { selectedRelativePaths.value = [...savedRelativePaths.value] },
    { immediate: true },
  )
  watch(
    () => `${selectedStep.value?.id || ''}:${currentStep.value?.id || ''}:${run.value?.status || ''}:${currentStep.value?.status || ''}`,
    () => { void refreshStatisticsCsvFiles() },
    { immediate: true },
  )

  return {
    canSelectStatisticsInputs,
    displayStatisticsValue,
    displayedStatisticsCsvFiles,
    isCurrentStatisticsStep,
    loadingStatisticsCsvFiles,
    refreshStatisticsCsvFiles,
    saveStatisticsInputs,
    savingStatisticsInputs,
    selectedRelativePaths,
    statisticsCsvDirectory,
    statisticsCsvFiles,
    statisticsResults,
    statisticsSelectionDirty,
    statisticsSelectionReady,
    statisticsUnit,
  }
}
