import { describe, expect, it } from 'vitest'
import type { RunMetric } from '@/types/run'
import { presentRunMetric } from '@/utils/runDetail'

function metric(overrides: Partial<RunMetric> = {}): RunMetric {
  return {
    id: 1,
    name: '数据统计/.openslt-runs/run-1/latency.csv/平均值',
    value: 7805.67,
    unit: 'ns',
    sample_count: 100,
    detail: {},
    ...overrides,
  }
}

describe('presentRunMetric', () => {
  it('uses structured metric and source fields for display', () => {
    const result = presentRunMetric(metric({
      detail: {
        metric_label: '平均值',
        source_file: 'latency.csv',
        source_path: '.openslt-runs/run-1/latency.csv',
      },
    }))

    expect(result.displayName).toBe('平均值')
    expect(result.sourceFile).toBe('latency.csv')
    expect(result.sourcePath).toBe('.openslt-runs/run-1/latency.csv')
  })

  it('falls back to the original name when legacy metric detail is absent', () => {
    const original = metric({ name: 'average' })

    expect(presentRunMetric(original)).toMatchObject({
      displayName: 'average',
      sourceFile: '-',
      sourcePath: '',
    })
  })
})
