import { computed, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { useStatisticsInputs } from '@/composables/useStatisticsInputs'
import type { RunArtifact, RunDetail, RunStep } from '@/types/run'

const message = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }))

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), put: vi.fn() },
  errorMessage: (error: unknown) => String(error),
}))
vi.mock('@/ui/elementPlusServices', () => ({ ElMessage: message }))

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
    vi.mocked(api.get).mockResolvedValue({ data: { directory: '/tmp/parser', files: [] } })
  })

  it('loads remote CSV files for the current statistics step', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: {
      directory: '/tmp/parser',
      files: [{
        relative_path: 'latency.csv', filename: 'latency.csv', source: 'root',
        size: 100, modified_at: '2026-07-28T10:00:00+08:00',
      }],
    } })
    const { statistics } = setup()
    await statistics.refreshStatisticsCsvFiles()
    expect(api.get).toHaveBeenCalledWith('/runs/9/steps/32/statistics-csv-files')
    expect(statistics.displayedStatisticsCsvFiles.value.map(item => item.relative_path)).toEqual(['latency.csv'])
  })

  it('tracks saved and dirty selection state', () => {
    const { statistics } = setup(statisticsStep('pending', {
      statistics_selection: {
        inputs: [
          { relative_path: 'b.csv', filename: 'b.csv', source: 'root', size: 1, modified_at: '2026-07-28T10:00:00+08:00' },
          { relative_path: 'a.csv', filename: 'a.csv', source: 'root', size: 1, modified_at: '2026-07-28T10:00:00+08:00' },
        ],
      },
    }))
    expect(statistics.selectedRelativePaths.value).toEqual(['b.csv', 'a.csv'])
    expect(statistics.statisticsSelectionReady.value).toBe(true)

    statistics.selectedRelativePaths.value = ['a.csv']
    expect(statistics.statisticsSelectionDirty.value).toBe(true)
    expect(statistics.statisticsSelectionReady.value).toBe(false)
  })

  it('saves the current selection before start', async () => {
    const { reload, statistics } = setup()
    statistics.selectedRelativePaths.value = ['latency.csv', '.openslt-runs/r9-s31-a1/result.csv']

    await statistics.saveStatisticsInputs()

    expect(api.put).toHaveBeenCalledWith('/runs/9/steps/32/statistics-inputs', {
      relative_paths: ['latency.csv', '.openslt-runs/r9-s31-a1/result.csv'],
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
