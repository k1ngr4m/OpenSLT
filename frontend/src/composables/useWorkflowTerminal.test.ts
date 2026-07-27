import { computed, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { useWorkflowTerminal } from '@/composables/useWorkflowTerminal'
import type { RunDetail, RunStep } from '@/types/run'

const message = vi.hoisted(() => ({ error: vi.fn(), info: vi.fn(), success: vi.fn() }))
vi.mock('element-plus', () => ({ ElMessage: message }))
vi.mock('@/components/SshTerminalPanel.vue', () => ({ default: {} }))

const terminalStep = {
  id: 7,
  code: 'slnic-start',
  name: '启动抓包',
  node_type: 'slnic_start_capture',
} as RunStep

function runDetail(): RunDetail {
  return {
    id: 11,
    status: 'awaiting_step_start',
    steps: [terminalStep],
    config_snapshot: {
      resources: [{ id: 3, name: 'SLNIC', type: 'slnic', host: '10.0.0.3' }],
    },
  } as RunDetail
}

describe('useWorkflowTerminal', () => {
  it('queues a command while disconnected and dispatches it after connection', async () => {
    const selectedStepId = ref<number | null>(null)
    const terminal = useWorkflowTerminal({
      active: ref('logs'),
      manualStepSelection: ref(true),
      reload: vi.fn().mockResolvedValue(undefined),
      run: ref(runDetail()),
      runId: 11,
      selectedStep: computed(() => terminalStep),
      selectedStepId,
    })
    const sendWorkflowStepCommand = vi.fn().mockReturnValue(true)
    const panel = {
      connected: false,
      connecting: false,
      connect: vi.fn(),
      sendWorkflowStepCommand,
    }
    terminal.slnicWorkflowTerminalPanel.value = panel as never

    await terminal.runWorkflowStepInTerminal(terminalStep, 'start')
    await nextTick()
    expect(panel.connect).toHaveBeenCalled()
    expect(selectedStepId.value).toBe(7)
    expect(terminal.terminalCommandPendingStepId.value).toBe(7)

    panel.connected = true
    terminal.handleWorkflowTerminalStatus('slnic', { status: 'connected' })
    expect(sendWorkflowStepCommand).toHaveBeenCalledWith({
      run_id: 11,
      step_id: 7,
      operation: 'start',
    })
  })
})
