import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { useRunActions } from '@/composables/useRunActions'
import type { RunStep } from '@/types/run'

const message = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }))
const confirm = vi.hoisted(() => vi.fn())

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn() },
  errorMessage: (error: unknown) => String(error),
}))
vi.mock('element-plus', () => ({
  ElMessage: message,
  ElMessageBox: { confirm },
}))

function step(nodeType = 'server_config'): RunStep {
  return {
    id: 7,
    code: 'node-7',
    name: '节点',
    workflow_node_id: 7,
    node_type: nodeType,
    config_snapshot: {},
    result_summary: {},
    position: 1,
    status: 'failed',
    progress: 0,
    retry_count: 0,
    max_retries: 2,
    started_at: null,
    finished_at: null,
    duration_ms: null,
    error_message: null,
  }
}

describe('useRunActions', () => {
  beforeEach(() => {
    vi.mocked(api.post).mockResolvedValue({ data: {} })
    vi.mocked(api.get).mockResolvedValue({ data: new Blob(), headers: {} })
    confirm.mockResolvedValue(undefined)
  })

  it('posts ordinary step retries and reloads', async () => {
    vi.useFakeTimers()
    const reload = vi.fn().mockResolvedValue(undefined)
    const runTerminalStep = vi.fn().mockResolvedValue(undefined)
    const actions = useRunActions({ runId: 11, reload, runTerminalStep })

    await actions.stepAction(step(), 'retry')
    expect(api.post).toHaveBeenCalledWith('/runs/11/steps/7/retry')
    expect(runTerminalStep).not.toHaveBeenCalled()
    await vi.runAllTimersAsync()
    expect(reload).toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('routes terminal-capable nodes through the terminal executor', async () => {
    const runTerminalStep = vi.fn().mockResolvedValue(undefined)
    const actions = useRunActions({
      runId: 11,
      reload: vi.fn().mockResolvedValue(undefined),
      runTerminalStep,
    })
    const terminalStep = step('slnic_start_capture')

    await actions.stepAction(terminalStep, 'retry')
    expect(runTerminalStep).toHaveBeenCalledWith(terminalStep, 'retry')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('submits a verdict and closes the dialog', async () => {
    const reload = vi.fn().mockResolvedValue(undefined)
    const actions = useRunActions({
      runId: 11,
      reload,
      runTerminalStep: vi.fn().mockResolvedValue(undefined),
    })
    actions.verdictDialog.value = true

    await actions.submitVerdict()
    expect(api.post).toHaveBeenCalledWith('/runs/11/verdict', actions.verdict)
    expect(actions.verdictDialog.value).toBe(false)
    expect(reload).toHaveBeenCalled()
  })
})
