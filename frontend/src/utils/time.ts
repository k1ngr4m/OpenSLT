export const BEIJING_TIME_ZONE = 'Asia/Shanghai'

type TimeValue = string | number | Date

function parseSystemTime(value: TimeValue): Date {
  if (value instanceof Date || typeof value === 'number') return new Date(value)
  // OpenSLT historically serialized SQLite/MySQL UTC values without an offset.
  // Treat only those legacy system timestamps as UTC during a rolling upgrade.
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`
  return new Date(normalized)
}

function parts(value: TimeValue, milliseconds = false): Record<string, string> | null {
  const date = parseSystemTime(value)
  if (Number.isNaN(date.getTime())) return null
  const formatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone: BEIJING_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
    ...(milliseconds ? { fractionalSecondDigits: 3 as const } : {}),
  })
  return Object.fromEntries(
    formatter.formatToParts(date).map(part => [part.type, part.value]),
  )
}

export function formatBeijingDateTime(
  value?: TimeValue | null,
  options: { milliseconds?: boolean; fallback?: string } = {},
): string {
  if (value == null || value === '') return options.fallback ?? '-'
  const item = parts(value, options.milliseconds)
  if (!item) return options.fallback ?? '-'
  const fraction = options.milliseconds ? `.${item.fractionalSecond || '000'}` : ''
  return `${item.year}-${item.month}-${item.day} ${item.hour}:${item.minute}:${item.second}${fraction}`
}

export function formatBeijingTime(value?: TimeValue | null, fallback = '--:--:--'): string {
  if (value == null || value === '') return fallback
  const item = parts(value)
  return item ? `${item.hour}:${item.minute}:${item.second}` : fallback
}

export function formatBeijingFilenameStamp(value: TimeValue = new Date()): string {
  const item = parts(value)
  if (!item) throw new Error('无法生成北京时间文件戳')
  return `${item.year}${item.month}${item.day}-${item.hour}${item.minute}`
}
