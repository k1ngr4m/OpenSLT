import type { ContractFilePreview, JsonMap, RunMetric } from '@/types/run'
import { formatBeijingDateTime, formatBeijingTime } from '@/utils/time'

export const nodeTypeText: Record<string, string> = {
  server_config: '服务器配置',
  database_config: '数据库配置',
  wiring_confirmation: '接线确认',
  rem_startup: '启动rem柜台',
  market_startup: '启动模拟市场',
  order_preparation: '发单准备',
  slnic_start_capture: '启动 SLNIC',
  slnic_stop_capture: '关闭 SLNIC',
  slnic_merge_capture: '合并 pcapng',
  parser_parse: '数据解析',
  data_statistics: '数据统计',
  report_generation: '生成报告',
}

export function statusClass(status: string) {
  if (status === 'succeeded' || status === 'completed') return 'is-success'
  if (status.includes('failed') || status === 'cancelled') return 'is-danger'
  if (status === 'running') return 'is-running'
  if (status.includes('awaiting') || status === 'waiting') return 'is-waiting'
  return 'is-pending'
}

export function formatValue(value: unknown) {
  if (value == null || value === '') return '-'
  if (Array.isArray(value)) return value.length ? value.map(item => typeof item === 'object' ? JSON.stringify(item) : String(item)).join('、') : '-'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function isJsonMap(value: unknown): value is JsonMap {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export function stringValue(value: unknown, fallback = '-') {
  return typeof value === 'string' && value ? value : fallback
}

export function optionalString(value: unknown) {
  return typeof value === 'string' ? value : undefined
}

export function optionalNumber(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

export function formatDate(value?: string | null) {
  return formatBeijingDateTime(value)
}

export function formatTime(value?: string | null) {
  return formatBeijingTime(value)
}

export function formatDuration(value?: number | null) {
  if (value == null) return '-'
  if (value < 1000) return `${value} ms`
  return `${(value / 1000).toFixed(1)} s`
}

export function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function metricDetailText(metric: RunMetric, key: string) {
  const value = metric.detail[key]
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

export function presentRunMetric(metric: RunMetric) {
  const sourceFile = metricDetailText(metric, 'source_file')
  return {
    ...metric,
    displayName: metricDetailText(metric, 'metric_label') || metric.name,
    sourceFile: sourceFile || '-',
    sourcePath: metricDetailText(metric, 'source_path'),
  }
}

export function contractTypeLabel(type?: string | null) {
  if (type === 'futures') return '期货'
  if (type === 'options') return '期权'
  return type || '合约'
}

export function shortChecksum(value?: string | null) {
  if (!value) return '-'
  return value.length > 16 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value
}

export function normalizeContractFile(source: unknown): ContractFilePreview | null {
  if (!source || typeof source !== 'object') return null
  const file = source as JsonMap
  const id = Number(file.id)
  if (!Number.isFinite(id)) return null
  return {
    id,
    filename: String(file.filename || `contract-${id}.csv`),
    contract_type: optionalString(file.contract_type),
    source_table: optionalString(file.source_table),
    remote_path: optionalString(file.remote_path),
    quote_date: optionalString(file.quote_date),
    row_count: Number.isFinite(Number(file.row_count)) ? Number(file.row_count) : undefined,
    size: Number.isFinite(Number(file.size)) ? Number(file.size) : undefined,
    checksum: stringValue(file.checksum, ''),
    preview_rows: Array.isArray(file.preview_rows) ? file.preview_rows.filter(isJsonMap) : undefined,
  }
}
