import { computed, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { useStatisticsInputs } from '@/composables/useStatisticsInputs'
import type { RunArtifact, RunDetail, RunStep } from '@/types/run'

const message = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }))

vi.mock('@/api/client', () => ({
  api: { put: vi.fn() },
  errorMessage: (error: unknown) => String(error),
}))
vi.mock('element-plus', () => ({ ElMessage: message }))

function parserStep(): RunStep {
  return {
    id: 31,
    code: 'parse',
    name: '数据解析',
    workflow_node_id: 31,
    node_type: 'parser_parse',
    config_snapshot: {},
    result_summary: {},
    position: 3,
    status: 'succeeded',
    progress: 100,
    retry_count: 0,
    max_retries: 2,
    started_at: null,
    finished_at: null,
    duration_ms: null,
    error_message: null,
  }
}

function statisticsStep(status: RunStep['status'] = 'pending', resultSummary = {}): RunStep {
  return {
    id: 32,
    code: 'statistics',
    name: '数据统计',
    workflow_node_id: 32,
    node_type: 'data_statistics',
    config_snapshot: {
      parser_node_key: 'parse',
      script_filename: 'statistics_cffex.py',
      script_checksum: 'a'.repeat(64),
      max_latency_ns: 999999999,
    },
    result_summary: resultSummary,
    position: 4,
    status,
    progress: 0,
    retry_count: 0,
    max_retries: 2,
    started_at: null,
    finished_at: null,
    duration_ms: null,
    error_message: null,
  }
}

function artifact(id: number, stepId: number, artifactType = 'parsed_csv'): RunArtifact {
  return {
    id,
    step_id: stepId,
    artifact_type: artifactType,
    name: `file-${id}.csv`,
    content_type: 'text/csv',
    size: 100,
    checksum: String(id).repeat(64).slice(0, 64),
    is_immutable: true,
    created_at: '2026-07-28T10:00:00+08:00',
  }
}

function setup(
  step = statisticsStep(),
  runStatus: RunDetail['status'] = 'awaiting_step_start',
  artifacts: RunArtifact[] = [artifact(101, 31), artifact(102, 31), artifact(201, 32)],
) {
  const current = ref<RunStep | null>(step)
  const selected = ref<RunStep | null>(step)
  const run = ref({
    id: 9,
    status: runStatus,
    steps: [parserStep(), step],
    artifacts,
  } as unknown as RunDetail)
  const reload = vi.fn().mockResolvedValue(undefined)
  const statistics = useStatisticsInputs({
    currentStep: computed(() => current.value),
    selectedStep: computed(() => selected.value),
    run,
    runId: 9,
    reload,
  })
  return { current, reload, run, selected, statistics }
}

describe('useStatisticsInputs', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.put).mockResolvedValue({ data: {} })
  })

  it('lists only parsed CSV artifacts from the configured parser step', () => {
    const { statistics } = setup(undefined, 'awaiting_step_start', [
      artifact(101, 31),
      artifact(102, 31, 'statistics_result_json'),
      artifact(201, 32),
      artifact(301, 99),
    ])
    expect(statistics.statisticsInputArtifacts.value.map(item => item.id)).toEqual([101])
  })

  it('tracks saved and dirty selection state', () => {
    const { statistics } = setup(statisticsStep('pending', {
      statistics_selection: {
        inputs: [{ artifact_id: 102 }, { artifact_id: 101 }],
      },
    }))
    expect(statistics.selectedArtifactIds.value).toEqual([102, 101])
    expect(statistics.statisticsSelectionReady.value).toBe(true)

    statistics.selectedArtifactIds.value = [101]
    expect(statistics.statisticsSelectionDirty.value).toBe(true)
    expect(statistics.statisticsSelectionReady.value).toBe(false)
  })

  it('saves the current selection before start', async () => {
    const { reload, statistics } = setup()
    statistics.selectedArtifactIds.value = [101, 102]

    await statistics.saveStatisticsInputs()

    expect(api.put).toHaveBeenCalledWith('/runs/9/steps/32/statistics-inputs', {
      artifact_ids: [101, 102],
    })
    expect(message.success).toHaveBeenCalledWith('已选择 2 个统计输入')
    expect(reload).toHaveBeenCalled()
  })

  it('allows reselection while waiting for retry and disables it while running', () => {
    const retry = setup(statisticsStep('failed'), 'awaiting_step_retry')
    expect(retry.statistics.canSelectStatisticsInputs.value).toBe(true)

    retry.run.value.status = 'running'
    retry.current.value!.status = 'running'
    expect(retry.statistics.canSelectStatisticsInputs.value).toBe(false)
  })

  it('converts metric values between ns and us for display', () => {
    const { statistics } = setup(statisticsStep('waiting', {
      statistics_results: [{
        source_file: 'latency.csv',
        metrics: [{ key: 'average', label: '平均值', value: 1523.4 }],
      }],
    }), 'awaiting_step_completion')

    expect(statistics.statisticsResults.value).toHaveLength(1)
    expect(statistics.displayStatisticsValue(1523.4)).toBe('1523.400')
    statistics.statisticsUnit.value = 'us'
    expect(statistics.displayStatisticsValue(1523.4)).toBe('1.523')
  })
})
