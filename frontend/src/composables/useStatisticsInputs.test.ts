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

function analysis(analysisNo: number, status: 'running' | 'succeeded' | 'failed' = 'succeeded') {
  return {
    analysis_no: analysisNo,
    status,
    config_revision: 3,
    inputs: [{ relative_path: 'latency.csv', filename: 'latency.csv', source: 'root', size: 100, modified_at: '2026-08-10T10:00:00+08:00' }],
    max_latency_ns: 900,
    script: { filename: 'statistics_cffex.py', checksum: 'a'.repeat(64) },
    reserved_at: '2026-08-10T10:00:00+08:00',
    started_at: '2026-08-10T10:00:01+08:00',
    finished_at: status === 'running' ? null : '2026-08-10T10:00:02+08:00',
    duration_ms: status === 'running' ? null : 1000,
    error_code: status === 'failed' ? 'SCRIPT_FAILED' : null,
    artifact_id: status === 'running' ? null : analysisNo + 200,
    artifact_checksum: status === 'running' ? null : 'b'.repeat(64),
    artifact_size: status === 'running' ? null : 2048,
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
    vi.mocked(api.put).mockResolvedValue({ data: {
      inputs: [
        { relative_path: 'latency.csv', filename: 'latency.csv', source: 'root', size: 100, modified_at: '2026-08-10T10:00:00+08:00' },
        { relative_path: '.openslt-runs/r9-s31-a1/result.csv', filename: 'result.csv', source: 'current_run', size: 101, modified_at: '2026-08-10T10:00:00+08:00' },
      ],
      max_latency_ns: 900,
      statistics_config_revision: 1,
      changed: true,
    } })
    const { reload, statistics } = setup()
    statistics.selectedRelativePaths.value = ['latency.csv', '.openslt-runs/r9-s31-a1/result.csv']
    statistics.statisticsMaxLatencyNsDraft.value = 900

    await statistics.saveStatisticsConfig()

    expect(api.put).toHaveBeenCalledWith('/runs/9/steps/32/statistics-config', {
      relative_paths: ['latency.csv', '.openslt-runs/r9-s31-a1/result.csv'],
      max_latency_ns: 900,
    })
    expect(message.success).toHaveBeenCalledWith('统计配置已保存')
    expect(reload).toHaveBeenCalled()
    expect(statistics.statisticsConfigDirty.value).toBe(false)
    expect(statistics.statisticsConfigSaved.value).toBe(true)
  })

  it('allows reselection while waiting for retry and disables it while running', () => {
    const retry = setup(statisticsStep('failed'), 'awaiting_step_retry')
    expect(retry.statistics.canSelectStatisticsInputs.value).toBe(true)

    const reanalysis = setup(statisticsStep('waiting'), 'awaiting_step_completion')
    expect(reanalysis.statistics.canEditStatisticsConfig.value).toBe(true)

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

  it('initializes saved threshold state and blocks completion for unsaved invalid values', () => {
    const { statistics } = setup(statisticsStep('waiting', {
      statistics_config_revision: 3,
      statistics_latest_success_revision: 3,
      statistics_latest_success_analysis_no: 12,
      statistics_selection: {
        inputs: [{ relative_path: 'latency.csv', filename: 'latency.csv', source: 'root', size: 100, modified_at: '2026-08-10T10:00:00+08:00' }],
      },
      statistics_analyses: [analysis(12)],
    }), 'awaiting_step_completion')

    expect(statistics.statisticsMaxLatencyNsDraft.value).toBe(999999999)
    expect(statistics.statisticsConfigSaved.value).toBe(true)
    expect(statistics.statisticsCompletionBlocked.value).toBe(false)

    statistics.statisticsMaxLatencyNsDraft.value = 0
    expect(statistics.statisticsThresholdValid.value).toBe(false)
    expect(statistics.statisticsConfigDirty.value).toBe(true)
    expect(statistics.statisticsCompletionBlocked.value).toBe(true)

    statistics.statisticsMaxLatencyNsDraft.value = 1.5
    expect(statistics.statisticsThresholdValid.value).toBe(false)
  })

  it('marks a changed revision without a matching successful analysis as stale', () => {
    const stale = setup(statisticsStep('waiting', {
      statistics_config_revision: 4,
      statistics_latest_success_revision: 3,
      statistics_latest_success_analysis_no: 12,
      statistics_selection: {
        inputs: [{ relative_path: 'latency.csv', filename: 'latency.csv', source: 'root', size: 100, modified_at: '2026-08-10T10:00:00+08:00' }],
      },
      statistics_analyses: [analysis(12)],
    }), 'awaiting_step_completion').statistics
    const legacy = setup(statisticsStep('waiting', {
      statistics_results: [{ source_file: 'legacy.csv', metrics: [] }],
    }), 'awaiting_step_completion').statistics

    expect(stale.statisticsCompletionStale.value).toBe(true)
    expect(stale.statisticsCompletionBlocked.value).toBe(true)
    expect(legacy.statisticsCompletionStale.value).toBe(false)
    expect(legacy.statisticsResults.value).toEqual([{ source_file: 'legacy.csv', metrics: [] }])
  })

  it('loads newest-first history metadata and lazily caches immutable analysis details', async () => {
    const { statistics } = setup(statisticsStep('waiting'), 'awaiting_step_completion')
    vi.clearAllMocks()
    vi.mocked(api.get).mockResolvedValueOnce({ data: [analysis(1), analysis(3, 'failed')] })

    await statistics.refreshStatisticsAnalyses()

    expect(statistics.statisticsAnalyses.value.map(item => item.analysis_no)).toEqual([3, 1])
    expect(statistics.statisticsAnalyses.value[0]?.error_code).toBe('SCRIPT_FAILED')
    expect(statistics.statisticsAnalysisDetails.value).toEqual({})

    vi.mocked(api.get).mockResolvedValueOnce({ data: {
      analysis: analysis(3, 'failed'),
      artifact: { analysis_no: 3, status: 'failed', inputs: [], max_latency_ns: 900, script: {}, attempts: [], error: { code: 'SCRIPT_FAILED' } },
    } })
    const detail = await statistics.loadStatisticsAnalysisDetail(3)

    expect(detail?.artifact.error).toEqual({ code: 'SCRIPT_FAILED' })
    expect(statistics.statisticsAnalysisDetails.value[3]?.analysis.status).toBe('failed')
    expect(api.get).toHaveBeenLastCalledWith('/runs/9/steps/32/statistics-analyses/3')

    await statistics.loadStatisticsAnalysisDetail(3)
    expect(api.get).toHaveBeenCalledTimes(2)
  })
})
