import { computed, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { useOrderActions } from '@/composables/useOrderActions'
import type { RunDetail, RunStep } from '@/types/run'

const message = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }))
const confirm = vi.hoisted(() => vi.fn())

vi.mock('@/api/client', () => ({
  api: { post: vi.fn() },
  errorMessage: (error: unknown) => String(error),
}))
vi.mock('element-plus', () => ({
  ElMessage: message,
  ElMessageBox: { confirm },
}))

function orderStep(resultSummary: Record<string, unknown> = {}): RunStep {
  return {
    id: 7,
    code: 'order',
    name: '发单执行',
    workflow_node_id: 7,
    node_type: 'order_preparation',
    config_snapshot: { order_action: 'new_quote' },
    result_summary: resultSummary,
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

function setup(step: RunStep) {
  const currentStep = ref<RunStep | null>(step)
  const reload = vi.fn().mockResolvedValue(undefined)
  const retryStep = vi.fn().mockResolvedValue(undefined)
  const run = ref({ id: 11, status: 'awaiting_step_completion' } as RunDetail)
  const actions = useOrderActions({
    currentStep: computed(() => currentStep.value),
    reload,
    retryStep,
    run,
    runId: 11,
  })
  return { actions, currentStep, reload, retryStep, run }
}

describe('useOrderActions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.post).mockResolvedValue({ data: {} })
    confirm.mockResolvedValue(undefined)
  })

  it('lists supported actions with the configured default first', () => {
    const { actions } = setup(orderStep({
      supported_order_actions: ['new_order', 'stop_order', 'new_quote'],
    }))
    expect(actions.availableOrderActions.value).toEqual(['new_quote', 'new_order', 'stop_order'])
  })

  it('falls back to only the configured action for historical runs', () => {
    const { actions } = setup(orderStep())
    expect(actions.availableOrderActions.value).toEqual(['new_quote'])
  })

  it('allows the same action to be sent repeatedly', async () => {
    const { actions } = setup(orderStep({ supported_order_actions: ['new_quote'] }))
    await actions.sendOrderAction('new_quote')
    await actions.sendOrderAction('new_quote')
    expect(api.post).toHaveBeenNthCalledWith(1, '/runs/11/steps/7/order-action', { action: 'new_quote' })
    expect(api.post).toHaveBeenNthCalledWith(2, '/runs/11/steps/7/order-action', { action: 'new_quote' })
  })

  it('confirms dangerous actions before sending', async () => {
    const { actions } = setup(orderStep({ supported_order_actions: ['new_quote', 'stop_order'] }))
    await actions.sendOrderAction('stop_order')
    expect(confirm).toHaveBeenCalledWith(
      expect.stringContaining('stop_order'),
      '确认高风险指令',
      expect.objectContaining({ confirmButtonText: '确认发送' }),
    )
    expect(api.post).toHaveBeenCalledWith('/runs/11/steps/7/order-action', { action: 'stop_order' })
  })

  it('locks sending and exposes the latest ten history entries while unresolved', () => {
    const history = Array.from({ length: 12 }, (_, index) => ({
      request_id: `request-${index}`,
      action: 'new_order',
      status: index === 11 ? 'unknown' : 'dispatched',
    }))
    const { actions } = setup(orderStep({
      order_action_status: 'unknown',
      supported_order_actions: ['new_order'],
      order_action_history: history,
    }))
    expect(actions.canSendOrderActions.value).toBe(false)
    expect(actions.orderActionUnresolved.value).toBe(true)
    expect(actions.recentOrderActionHistory.value).toHaveLength(10)
    expect(actions.recentOrderActionHistory.value[0].request_id).toBe('request-11')
  })
})
