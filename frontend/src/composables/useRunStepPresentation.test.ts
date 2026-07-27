import { computed, ref } from 'vue'
import { describe, expect, it } from 'vitest'
import { useRunStepPresentation } from '@/composables/useRunStepPresentation'
import type { ContractFilePreview, RunDetail, RunStep } from '@/types/run'

function orderStep(): RunStep {
  return {
    id: 9,
    code: 'order',
    name: '发单准备',
    workflow_node_id: 9,
    node_type: 'order_preparation',
    config_snapshot: {
      read_symbol_csv: 1,
      contract_file_ids: [2, 1],
      contract_files: [{ id: 1, filename: 'futures.csv', row_count: 100 }],
    },
    result_summary: {
      contract_files: [{ id: 2, filename: 'options.csv', checksum: 'result-checksum' }],
    },
    position: 1,
    status: 'waiting',
    progress: 100,
    retry_count: 0,
    max_retries: 2,
    started_at: null,
    finished_at: null,
    duration_ms: null,
    error_message: null,
  }
}

describe('useRunStepPresentation contract preview', () => {
  it('merges selected files from snapshots, results, and preview cache in ID order', () => {
    const step = orderStep()
    const run = ref({ artifacts: [], config_snapshot: {} } as unknown as RunDetail)
    const selectedStep = computed(() => step)
    const cache: Record<number, ContractFilePreview> = {
      1: { id: 1, filename: 'futures.csv', checksum: 'cache-checksum', preview_rows: [{ symbol: 'IF' }] },
      2: { id: 2, filename: 'options.csv', row_count: 200, preview_rows: [{ symbol: 'IO' }] },
    }
    const presentation = useRunStepPresentation(run, selectedStep, cache)

    expect(presentation.orderReadSymbolCsvEnabled.value).toBe(true)
    expect(presentation.selectedContractFileIds.value).toEqual([2, 1])
    expect(presentation.contractFiles.value.map(file => file.id)).toEqual([2, 1])
    expect(presentation.contractFiles.value[0]).toMatchObject({
      filename: 'options.csv',
      checksum: 'result-checksum',
      row_count: 200,
      preview_rows: [{ symbol: 'IO' }],
    })
    expect(presentation.contractFiles.value[1]).toMatchObject({
      filename: 'futures.csv',
      checksum: 'cache-checksum',
      row_count: 100,
      preview_rows: [{ symbol: 'IF' }],
    })
  })
})
