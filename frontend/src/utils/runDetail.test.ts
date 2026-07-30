import { describe, expect, it } from 'vitest'
import type { RunArtifact, RunMetric } from '@/types/run'
import { presentRunMetric, sortArtifactsNewestFirst } from '@/utils/runDetail'

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

describe('sortArtifactsNewestFirst', () => {
  it('sorts by creation time descending without mutating the source array', () => {
    const artifacts = [
      { id: 1, created_at: '2026-07-28T10:00:00+08:00' },
      { id: 3, created_at: '2026-07-29T10:00:00+08:00' },
      { id: 2, created_at: '2026-07-29T10:00:00+08:00' },
    ] as RunArtifact[]

    expect(sortArtifactsNewestFirst(artifacts).map(item => item.id)).toEqual([3, 2, 1])
    expect(artifacts.map(item => item.id)).toEqual([1, 3, 2])
  })
})
