import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  confirm: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  api: { get: mocks.get, put: mocks.put, delete: mocks.delete },
  errorMessage: (error: unknown) => String(error),
}))
vi.mock('@/ui/elementPlusServices', () => ({
  ElMessage: { success: mocks.success, error: mocks.error, warning: mocks.warning },
  ElMessageBox: { confirm: mocks.confirm },
}))

import RunComparisonPanel from './RunComparisonPanel.vue'

const source = readFileSync(resolve(process.cwd(), 'src/components/run-detail/RunComparisonPanel.vue'), 'utf8')

const candidate = {
  run_id: 7,
  run_number: 'RUN-BASELINE-007',
  finished_at: '2026-08-20T10:00:00+08:00',
  verdict: 'passed',
  workflow_version_id: 3,
  compatible: true,
  warnings: [],
  matched_metric_count: 1,
  metric_count: 1,
  recommended: true,
}

const comparison = {
  id: 2,
  run_id: 9,
  baseline_run_id: 7,
  target_run_number: 'RUN-TARGET-009',
  baseline_run_number: 'RUN-BASELINE-007',
  target_analysis_refs: [{ analysis_no: 2 }],
  baseline_analysis_refs: [{ analysis_no: 1 }],
  rows: [{
    key: 'statistics\u001flatency.csv\u001faverage',
    step_code: 'statistics',
    step_name: '数据统计',
    source_file: 'latency.csv',
    metric_key: 'average',
    metric_label: '平均值',
    unit: 'ns',
    baseline_value: 100,
    target_value: 125,
    absolute_delta: 25,
    percentage_delta: 25,
    assessment: 'regressed',
  }],
  warnings: [],
  compatible: true,
  target_metrics_stale: false,
  baseline_metrics_changed: false,
  created_by: 1,
  created_at: '2026-08-20T11:00:00+08:00',
  updated_at: '2026-08-20T11:00:00+08:00',
}

function render() {
  return mount(RunComparisonPanel, {
    props: { runId: 9, canOperate: true, hasMetrics: true },
    global: {
      stubs: {
        ElButton: { props: ['disabled'], template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>' },
        ElSelect: { template: '<div><slot /></div>' },
        ElOption: { template: '<div><slot /></div>' },
        ElAlert: { props: ['title', 'description'], template: '<div role="alert">{{ title }} {{ description }}</div>' },
        ElSkeleton: true,
        ElEmpty: { props: ['description'], template: '<div>{{ description }}<slot /></div>' },
        ElTable: { template: '<div><slot /></div>' },
        ElTableColumn: true,
        ElTag: { template: '<span><slot /></span>' },
      },
    },
  })
}

describe('RunComparisonPanel', () => {
  it('loads a recommended baseline and saves an immutable comparison snapshot', async () => {
    mocks.get.mockImplementation((url: string) => Promise.resolve({
      data: url.endsWith('comparison-candidates') ? [candidate] : null,
    }))
    mocks.put.mockResolvedValue({ data: comparison })

    const wrapper = render()
    await flushPromises()

    expect(mocks.get).toHaveBeenCalledWith('/runs/9/comparison')
    expect(mocks.get).toHaveBeenCalledWith('/runs/9/comparison-candidates')
    expect(wrapper.text()).toContain('匹配 1 / 1 个指标')

    const saveButton = wrapper.findAll('button').find(button => button.text().includes('保存对比快照'))
    expect(saveButton).toBeTruthy()
    await saveButton!.trigger('click')
    await flushPromises()

    expect(mocks.put).toHaveBeenCalledWith('/runs/9/comparison', { baseline_run_id: 7 })
    expect(wrapper.text()).toContain('RUN-TARGET-009')
    expect(wrapper.text()).toContain('RUN-BASELINE-007')
    expect(mocks.success).toHaveBeenCalledWith('运行对比快照已保存；重新生成报告后可纳入报告')
  })

  it('exposes compatibility and stale-state feedback accessibly', () => {
    expect(source).toContain('aria-labelledby="run-comparison-heading"')
    expect(source).toContain('<fieldset v-if="canOperate"')
    expect(source).toContain('aria-label="基线运行"')
    expect(source).toContain('aria-live="polite"')
    expect(source).toContain('comparison.target_metrics_stale')
    expect(source).toContain('comparison.baseline_metrics_changed')
    expect(source).toContain("improved: '下降'")
    expect(source).toContain("regressed: '上升'")
  })
})
