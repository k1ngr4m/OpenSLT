import { computed, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { useOrderRuntimeConfig } from '@/composables/useOrderRuntimeConfig'
import type { RunDetail, RunStep } from '@/types/run'

const message = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }))

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), put: vi.fn() },
  errorMessage: (error: unknown) => String(error),
}))
vi.mock('@/ui/elementPlusServices', () => ({ ElMessage: message }))

function orderStep(status: RunStep['status'] = 'pending'): RunStep {
  return {
    id: 23,
    code: 'order',
    name: '发单执行',
    workflow_node_id: 23,
    node_type: 'order_preparation',
    config_snapshot: {
      xml_filename: 'ees_ef_vi_trader_api_test_conf.xml',
      network_interface: 'p4p1',
    },
    result_summary: {},
    position: 1,
    status,
    progress: 0,
    retry_count: 0,
    max_retries: 2,
    started_at: null,
    finished_at: null,
    duration_ms: null,
    error_message: null,
  }
}

function setup(step = orderStep(), runStatus: RunDetail['status'] = 'awaiting_step_start') {
  const current = ref<RunStep | null>(step)
  const selected = ref<RunStep | null>(step)
  const run = ref({ id: 7, status: runStatus, steps: [step] } as unknown as RunDetail)
  const reload = vi.fn().mockResolvedValue(undefined)
  const runtimeConfig = useOrderRuntimeConfig({
    canOperate: computed(() => true),
    currentStep: computed(() => current.value),
    selectedStep: computed(() => selected.value),
    run,
    runId: 7,
    orderResourceId: computed(() => 41),
    reload,
  })
  return { current, reload, run, runtimeConfig, selected }
}

describe('useOrderRuntimeConfig', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockResolvedValue({ data: {
      files: [
        { name: 'ees_ef_vi_trader_api_test_conf.xml', size: 100, modified_at: '2026-08-06T10:00:00+08:00' },
        { name: 'ees_ef_vi_trader_api_test_conf-runtime.xml', size: 120, modified_at: '2026-08-06T11:00:00+08:00' },
      ],
    } })
    vi.mocked(api.put).mockResolvedValue({ data: {} })
  })

  it('switches XML and interface for the current order step before start', async () => {
    const { reload, runtimeConfig } = setup()
    expect(runtimeConfig.canEditOrderConfig.value).toBe(true)

    await runtimeConfig.startEditingOrderConfig()
    runtimeConfig.orderConfigDraft.xml_filename = 'ees_ef_vi_trader_api_test_conf-runtime.xml'
    runtimeConfig.orderConfigDraft.network_interface = ' enp1s0 '
    expect(runtimeConfig.orderConfigActionBlocked.value).toBe(true)

    await runtimeConfig.saveOrderRuntimeConfig()

    expect(api.put).toHaveBeenCalledWith('/runs/7/steps/23/order-config', {
      xml_filename: 'ees_ef_vi_trader_api_test_conf-runtime.xml',
      network_interface: 'enp1s0',
    })
    expect(message.success).toHaveBeenCalledWith('发单配置已保存')
    expect(reload).toHaveBeenCalled()
    expect(runtimeConfig.editingOrderConfig.value).toBe(false)
  })

  it('allows editing before retry but not after the order session has started', () => {
    expect(setup(orderStep('failed'), 'awaiting_step_retry').runtimeConfig.canEditOrderConfig.value).toBe(true)
    expect(setup(orderStep('waiting'), 'awaiting_step_completion').runtimeConfig.canEditOrderConfig.value).toBe(false)
    expect(setup(orderStep('succeeded'), 'completed').runtimeConfig.canEditOrderConfig.value).toBe(false)
  })
})
