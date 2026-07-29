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

  it('does not expose checksums in node configuration rows', () => {
    const run = ref({ artifacts: [], config_snapshot: {} } as unknown as RunDetail)
    const cases: Array<[RunStep['node_type'], Record<string, unknown>]> = [
      ['order_preparation', { xml_filename: 'order.xml', xml_checksum: 'order-checksum' }],
      ['parser_parse', {
        config_xml_filename: 'config.xml', config_xml_checksum: 'config-checksum',
        instance_xml_filename: 'instance.xml', instance_xml_checksum: 'instance-checksum',
        analysis_xml_filename: 'analysis.xml', analysis_xml_checksum: 'analysis-checksum',
      }],
      ['data_statistics', { script_filename: 'statistics.py', script_checksum: 'script-checksum' }],
    ]

    for (const [nodeType, configSnapshot] of cases) {
      const step = { ...orderStep(), node_type: nodeType, config_snapshot: configSnapshot }
      const presentation = useRunStepPresentation(run, computed(() => step), {})
      expect(presentation.configRows.value.every(row => !row.label.includes('校验') && !row.label.includes('SHA'))).toBe(true)
      expect(presentation.configRows.value.every(row => !String(row.value ?? '').includes('checksum'))).toBe(true)
    }
  })

  it('presents the fixed REM startup sequence and execution result', () => {
    const step = {
      ...orderStep(),
      node_type: 'rem_startup',
      config_snapshot: {},
      result_summary: {
        resource_id: 3,
        resource_name: 'REM-03',
        remote_workdir: '/home/user0/rem_mm',
        exit_code: 0,
        duration_ms: 1250,
        commands: [
          { script: './stop_rem.sh', exit_code: 0 },
          { script: './makeneat.sh', exit_code: 0 },
          { script: './start_rem_all.sh', exit_code: 0 },
        ],
      },
    } as RunStep
    const run = ref({ artifacts: [], config_snapshot: {} } as unknown as RunDetail)
    const presentation = useRunStepPresentation(run, computed(() => step), {})

    expect(presentation.configRows.value).toEqual([
      { label: 'REM 动作', value: '停止服务 → 清理数据流 → 启动服务' },
      { label: '固定脚本', value: './stop_rem.sh → ./makeneat.sh → ./start_rem_all.sh', mono: true },
    ])
    expect(presentation.resultRows.value).toEqual(expect.arrayContaining([
      { label: '资源', value: 'REM-03' },
      { label: '完成命令', value: '3/3' },
      { label: '退出码', value: 0 },
    ]))
  })

  it('presents the ordered market startup scripts and completion count', () => {
    const step = {
      ...orderStep(),
      node_type: 'market_startup',
      config_snapshot: {
        scripts: [
          { filename: 'prepare.sh', checksum: 'a' },
          { filename: 'start_all.sh', checksum: 'b' },
        ],
      },
      result_summary: {
        resource_name: 'Market-01',
        remote_workdir: '/home/user0/rem_mkt/cffex_2.0',
        exit_code: 0,
        duration_ms: 2000,
        commands: [
          { script: 'prepare.sh', exit_code: 0 },
          { script: 'start_all.sh', exit_code: 0 },
        ],
      },
    } as RunStep
    const run = ref({ artifacts: [], config_snapshot: {} } as unknown as RunDetail)
    const presentation = useRunStepPresentation(run, computed(() => step), {})

    expect(presentation.configRows.value).toEqual([
      { label: '启动脚本数量', value: '2 个' },
      { label: '执行顺序', value: 'prepare.sh → start_all.sh', mono: true },
    ])
    expect(presentation.resultRows.value).toEqual(expect.arrayContaining([
      { label: '资源', value: 'Market-01' },
      { label: '完成脚本', value: '2/2' },
      { label: '执行顺序', value: 'prepare.sh → start_all.sh', mono: true },
    ]))
  })
})
