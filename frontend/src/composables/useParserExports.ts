import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import type { JsonMap, RunDetail, RunStep } from '@/types/run'

export const PARSER_TABLES = ['t_fut_orders', 't_fut_quotes', 't_fut_arbi_orders'] as const
export type ParserTable = typeof PARSER_TABLES[number]

interface ParserExportDetail extends JsonMap {
  artifact_id?: number
  filename?: string
  row_count?: number
  size?: number
  checksum?: string
  source?: string
  exported_at?: string
}

interface ParserExportsOptions {
  currentStep: ComputedRef<RunStep | null>
  selectedStep: ComputedRef<RunStep | null>
  run: Ref<RunDetail | null>
  runId: number
  reload: () => Promise<void>
  downloadArtifact: (artifactId: number) => Promise<void>
}

function exportMap(step: RunStep | null): Record<string, ParserExportDetail> {
  const raw = step?.result_summary?.parser_input_exports
  return raw && typeof raw === 'object' && !Array.isArray(raw)
    ? raw as Record<string, ParserExportDetail>
    : {}
}

export function useParserExports(options: ParserExportsOptions) {
  const { currentStep, selectedStep, run, runId, reload, downloadArtifact } = options
  const exportingTable = ref<ParserTable | null>(null)
  const selectedParserExports = computed(() => exportMap(selectedStep.value))
  const isCurrentParserStep = computed(() => Boolean(
    selectedStep.value?.node_type === 'parser_parse'
    && selectedStep.value.id === currentStep.value?.id,
  ))
  const canExportParserTables = computed(() => Boolean(
    isCurrentParserStep.value
    && (
      (run.value?.status === 'awaiting_step_start' && currentStep.value?.status === 'pending')
      || (run.value?.status === 'awaiting_step_retry' && currentStep.value?.status === 'failed')
    )
    && !exportingTable.value,
  ))
  const parserExportRows = computed(() => PARSER_TABLES.map(table => {
    const detail = selectedParserExports.value[table] || {}
    const artifactId = Number(detail.artifact_id)
    const artifact = Number.isFinite(artifactId)
      ? run.value?.artifacts.find(item => item.id === artifactId)
      : undefined
    return {
      table,
      detail,
      artifactId: Number.isFinite(artifactId) ? artifactId : null,
      artifact,
      ready: Number.isFinite(artifactId) && Boolean(detail.checksum),
    }
  }))

  async function exportParserTable(table: ParserTable) {
    const step = currentStep.value
    if (!step || !canExportParserTables.value) return
    exportingTable.value = table
    try {
      const response = await api.post(`/runs/${runId}/steps/${step.id}/parser-exports`, { table })
      ElMessage.success(`${table}.csv 已生成并归档`)
      await reload()
      const artifactId = Number(response.data?.artifact_id)
      if (Number.isFinite(artifactId)) await downloadArtifact(artifactId)
    } catch (error) {
      ElMessage.error(errorMessage(error))
    } finally {
      exportingTable.value = null
    }
  }

  return {
    canExportParserTables,
    exportingTable,
    exportParserTable,
    isCurrentParserStep,
    parserExportRows,
  }
}
