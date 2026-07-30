import { computed, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { useParserExports } from '@/composables/useParserExports'
import type { RunDetail, RunStep } from '@/types/run'

const message = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }))

vi.mock('@/api/client', () => ({
  api: { post: vi.fn() },
  errorMessage: (error: unknown) => String(error),
}))
vi.mock('@/ui/elementPlusServices', () => ({ ElMessage: message }))

function parserStep(status: RunStep['status'] = 'pending', resultSummary = {}): RunStep {
  return {
    id: 13,
    code: 'parser',
    name: '数据解析',
    workflow_node_id: 13,
    node_type: 'parser_parse',
    config_snapshot: { database_name: 'fut_mm_trading_data' },
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

function setup(step = parserStep(), runStatus: RunDetail['status'] = 'awaiting_step_start') {
  const current = ref<RunStep | null>(step)
  const selected = ref<RunStep | null>(step)
  const run = ref({ id: 2, status: runStatus, artifacts: [] } as unknown as RunDetail)
  const reload = vi.fn().mockResolvedValue(undefined)
  const downloadArtifact = vi.fn().mockResolvedValue(undefined)
  const exports = useParserExports({
    currentStep: computed(() => current.value),
    selectedStep: computed(() => selected.value),
    run,
    runId: 2,
    reload,
    downloadArtifact,
  })
  return { current, downloadArtifact, exports, reload, run, selected }
}

describe('useParserExports', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.post).mockResolvedValue({ data: { artifact_id: 41 } })
  })

  it('lists all parser tables and exports one as an archived download', async () => {
    const { downloadArtifact, exports, reload } = setup()
    expect(exports.parserExportRows.value.map(row => row.table)).toEqual([
      't_fut_orders', 't_fut_quotes', 't_fut_arbi_orders', 't_account_exchange_code',
    ])
    expect(exports.canExportParserTables.value).toBe(true)

    await exports.exportParserTable('t_fut_orders')

    expect(api.post).toHaveBeenCalledWith('/runs/2/steps/13/parser-exports', { table: 't_fut_orders' })
    expect(reload).toHaveBeenCalled()
    expect(downloadArtifact).toHaveBeenCalledWith(41)
  })

  it('exports the account exchange code snapshot through the same endpoint', async () => {
    const { exports } = setup()

    await exports.exportParserTable('t_account_exchange_code')

    expect(api.post).toHaveBeenCalledWith('/runs/2/steps/13/parser-exports', { table: 't_account_exchange_code' })
  })

  it('exposes an existing snapshot as refreshable', () => {
    const { exports } = setup(parserStep('pending', {
      parser_input_exports: {
        t_fut_quotes: { artifact_id: 9, row_count: 12, checksum: 'abc', exported_at: '2026-07-28T10:00:00+08:00' },
      },
    }))
    const quote = exports.parserExportRows.value.find(row => row.table === 't_fut_quotes')!
    expect(quote.ready).toBe(true)
    expect(quote.artifactId).toBe(9)
  })

  it('allows refresh before retry and disables it while running', () => {
    const retry = setup(parserStep('failed'), 'awaiting_step_retry')
    expect(retry.exports.canExportParserTables.value).toBe(true)

    retry.run.value.status = 'running'
    retry.current.value!.status = 'running'
    expect(retry.exports.canExportParserTables.value).toBe(false)
  })
})
