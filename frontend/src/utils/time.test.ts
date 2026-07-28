import { describe, expect, it } from 'vitest'
import {
  formatBeijingDateTime,
  formatBeijingFilenameStamp,
  formatBeijingTime,
} from './time'

describe('Beijing time formatting', () => {
  it('formats an absolute instant in Asia/Shanghai', () => {
    expect(formatBeijingDateTime('2026-07-28T08:30:45Z')).toBe('2026-07-28 16:30:45')
    expect(formatBeijingTime('2026-07-28T08:30:45-04:00')).toBe('20:30:45')
  })

  it('supports millisecond precision for logs', () => {
    expect(formatBeijingDateTime('2026-07-28T08:30:45.123Z', { milliseconds: true }))
      .toBe('2026-07-28 16:30:45.123')
  })

  it('treats legacy offset-free system timestamps as UTC', () => {
    expect(formatBeijingDateTime('2026-07-28T08:30:45')).toBe('2026-07-28 16:30:45')
  })

  it('uses Beijing calendar fields in generated filenames', () => {
    expect(formatBeijingFilenameStamp(new Date('2026-07-28T16:01:00Z')))
      .toBe('20260729-0001')
  })
})
