import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
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

function sortedIds(ids: number[]) {
  return [...ids].sort((a, b) => a - b).join(',')
}

export function useStatisticsInputs(options: StatisticsOptions) {
  const { currentStep, selectedStep, run, runId, reload } = options
  const selectedArtifactIds = ref<number[]>([])
  const savingStatisticsInputs = ref(false)
  const statisticsUnit = ref<'ns' | 'us'>('ns')
  const savedArtifactIds = computed(() => selectionIds(selectedStep.value))
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
  const statisticsInputArtifacts = computed(() => {
    const parserNodeKey = String(selectedStep.value?.config_snapshot?.parser_node_key || '')
    const parserStep = run.value?.steps.find(step => step.code === parserNodeKey && step.node_type === 'parser_parse')
    if (!parserStep) return []
    return (run.value?.artifacts || []).filter(
      artifact => artifact.step_id === parserStep.id && artifact.artifact_type === 'parsed_csv',
    )
  })
  const statisticsSelectionDirty = computed(
    () => sortedIds(selectedArtifactIds.value) !== sortedIds(savedArtifactIds.value),
  )
  const statisticsSelectionReady = computed(() => Boolean(
    savedArtifactIds.value.length
    && !statisticsSelectionDirty.value
    && !savingStatisticsInputs.value,
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
    if (!step || !canSelectStatisticsInputs.value || !selectedArtifactIds.value.length) return
    savingStatisticsInputs.value = true
    try {
      await api.put(`/runs/${runId}/steps/${step.id}/statistics-inputs`, {
        artifact_ids: selectedArtifactIds.value,
      })
      ElMessage.success(`已选择 ${selectedArtifactIds.value.length} 个统计输入`)
      await reload()
    } catch (error) {
      ElMessage.error(errorMessage(error))
    } finally {
      savingStatisticsInputs.value = false
    }
  }

  watch(
    () => `${selectedStep.value?.id || ''}:${savedArtifactIds.value.join(',')}`,
    () => { selectedArtifactIds.value = [...savedArtifactIds.value] },
    { immediate: true },
  )

  return {
    canSelectStatisticsInputs,
    displayStatisticsValue,
    isCurrentStatisticsStep,
    saveStatisticsInputs,
    savingStatisticsInputs,
    selectedArtifactIds,
    statisticsInputArtifacts,
    statisticsResults,
    statisticsSelectionDirty,
    statisticsSelectionReady,
    statisticsUnit,
  }
}
