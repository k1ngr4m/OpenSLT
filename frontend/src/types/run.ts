import type { components } from '@/types/api.generated'

type ApiSchemas = components['schemas']

export type JsonMap = Record<string, unknown>
export type LogScope = 'all' | number
export type WorkflowTerminalKind = 'rem' | 'market' | 'slnic' | 'order' | 'parser'

export type RunStep = Omit<ApiSchemas['StepOut'], 'config_snapshot' | 'result_summary'> & {
  config_snapshot: JsonMap
  result_summary: JsonMap
}

export type RunLog = Omit<ApiSchemas['LogOut'], 'detail'> & {
  detail: JsonMap
}

export type CaptureItem = ApiSchemas['CaptureItemOut']

export type CaptureSnapshot = Omit<ApiSchemas['CaptureSnapshotOut'], 'items'> & {
  items: CaptureItem[]
}

export interface CaptureState {
  signature: string
  loading: boolean
  error: string
  data: CaptureSnapshot[]
}

export type RunArtifact = ApiSchemas['ArtifactOut']

export type RunMetric = Omit<ApiSchemas['MetricOut'], 'detail'> & {
  detail: JsonMap
}

export type RunVerdict = ApiSchemas['VerdictOut']
export type RunVerdictWrite = ApiSchemas['VerdictWrite']

export interface RunConfigSnapshot {
  plan?: {
    id: number
    name: string
    business_code: string
    config_version: string
  }
  scenario?: {
    id: number
    name: string
    scenario_type: string
    config_version: string
  }
  workflow?: JsonMap
  resources?: RunResourceSnapshot[]
  [key: string]: unknown
}

export type RunDetail = Omit<
  ApiSchemas['RunOut'],
  'artifacts' | 'config_snapshot' | 'metrics' | 'steps' | 'verdict'
> & {
  artifacts: RunArtifact[]
  config_snapshot: RunConfigSnapshot
  metrics: RunMetric[]
  steps: RunStep[]
  verdict: RunVerdict | null
}

export interface InfoRow {
  label: string
  value: string | number | null | undefined
  mono?: boolean
}

export interface RunResourceSnapshot {
  id: number
  name: string
  type: string
  host?: string
  version?: string
}

type ContractDataFile = ApiSchemas['ContractDataFileOut']

export type ContractFilePreview = Partial<Omit<ContractDataFile, 'preview_rows'>> &
  Pick<ContractDataFile, 'id' | 'filename'> & {
    preview_rows?: JsonMap[]
  }
