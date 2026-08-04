import { computed, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { useWorkflowTerminal } from '@/composables/useWorkflowTerminal'
import type { RunDetail, RunStep } from '@/types/run'

const message = vi.hoisted(() => ({ error: vi.fn(), info: vi.fn(), success: vi.fn() }))
vi.mock('@/ui/elementPlusServices', () => ({ ElMessage: message }))
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

const remStep = {
  ...terminalStep,
  id: 8,
  code: 'rem-start',
  name: '启动 REM',
  node_type: 'rem_startup',
} as RunStep

function remRunDetail(): RunDetail {
  return {
    ...runDetail(),
    steps: [remStep],
    config_snapshot: {
      resources: [{ id: 4, name: 'REM', type: 'rem', host: '10.0.0.4' }],
    },
  } as RunDetail
}

const marketStep = {
  ...terminalStep,
  id: 9,
  code: 'market-start',
  name: '启动模拟市场',
  node_type: 'market_startup',
} as RunStep

const parserStep = {
  ...terminalStep,
  id: 10,
  code: 'parser-parse',
  name: '数据解析',
  node_type: 'parser_parse',
  status: 'waiting',
  result_summary: {
    supported_parser_actions: ['write_clt_new_to_rem_accept'],
  },
} as RunStep

function marketRunDetail(): RunDetail {
  return {
    ...runDetail(),
    steps: [marketStep],
    config_snapshot: {
      resources: [{ id: 5, name: 'Market', type: 'market', host: '10.0.0.5' }],
    },
  } as RunDetail
}

function parserRunDetail(): RunDetail {
  return {
    ...runDetail(),
    status: 'awaiting_step_completion',
    steps: [parserStep],
    config_snapshot: {
      resources: [{ id: 6, name: 'Parser', type: 'parser', host: '10.0.0.6' }],
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

  it('selects the REM panel and dispatches the queued command after connection', async () => {
    const selectedStepId = ref<number | null>(null)
    const terminal = useWorkflowTerminal({
      active: ref('logs'),
      manualStepSelection: ref(true),
      reload: vi.fn().mockResolvedValue(undefined),
      run: ref(remRunDetail()),
      runId: 11,
      selectedStep: computed(() => remStep),
      selectedStepId,
    })
    const sendWorkflowStepCommand = vi.fn().mockReturnValue(true)
    const panel = {
      connected: false,
      connecting: false,
      connect: vi.fn(),
      sendWorkflowStepCommand,
    }
    terminal.remWorkflowTerminalPanel.value = panel as never

    expect(terminal.workflowTerminalKind.value).toBe('rem')
    expect(terminal.workflowTerminalResource.value?.id).toBe(4)
    expect(terminal.workflowTerminalTitle.value).toBe('REM SSH 终端')

    await terminal.runWorkflowStepInTerminal(remStep, 'start')
    await nextTick()
    expect(panel.connect).toHaveBeenCalled()
    expect(selectedStepId.value).toBe(8)

    panel.connected = true
    terminal.handleWorkflowTerminalStatus('rem', { status: 'connected' })
    expect(sendWorkflowStepCommand).toHaveBeenCalledWith({
      run_id: 11,
      step_id: 8,
      operation: 'start',
    })
  })

  it('selects the market panel and dispatches the queued command after connection', async () => {
    const selectedStepId = ref<number | null>(null)
    const terminal = useWorkflowTerminal({
      active: ref('logs'),
      manualStepSelection: ref(true),
      reload: vi.fn().mockResolvedValue(undefined),
      run: ref(marketRunDetail()),
      runId: 11,
      selectedStep: computed(() => marketStep),
      selectedStepId,
    })
    const sendWorkflowStepCommand = vi.fn().mockReturnValue(true)
    const panel = {
      connected: false,
      connecting: false,
      connect: vi.fn(),
      sendWorkflowStepCommand,
    }
    terminal.marketWorkflowTerminalPanel.value = panel as never

    expect(terminal.workflowTerminalKind.value).toBe('market')
    expect(terminal.workflowTerminalResource.value?.id).toBe(5)
    expect(terminal.workflowTerminalTitle.value).toBe('模拟市场 SSH 终端')
    expect(terminal.workflowTerminalDescription.value).toContain('按顺序')

    await terminal.runWorkflowStepInTerminal(marketStep, 'start')
    await nextTick()
    expect(panel.connect).toHaveBeenCalled()
    expect(selectedStepId.value).toBe(9)

    panel.connected = true
    terminal.handleWorkflowTerminalStatus('market', { status: 'connected' })
    expect(sendWorkflowStepCommand).toHaveBeenCalledWith({
      run_id: 11,
      step_id: 9,
      operation: 'start',
    })
  })

  it('selects the parser panel, sends configured actions, and stops it with control input', () => {
    const terminal = useWorkflowTerminal({
      active: ref('detail'),
      manualStepSelection: ref(false),
      reload: vi.fn().mockResolvedValue(undefined),
      run: ref(parserRunDetail()),
      runId: 11,
      selectedStep: computed(() => parserStep),
      selectedStepId: ref(10),
    })
    const sendParserAction = vi.fn().mockReturnValue(true)
    const sendControl = vi.fn().mockReturnValue(true)
    terminal.parserWorkflowTerminalPanel.value = {
      connected: true,
      connecting: false,
      connect: vi.fn(),
      sendWorkflowStepCommand: vi.fn(),
      sendParserAction,
      sendControl,
    } as never

    expect(terminal.workflowTerminalKind.value).toBe('parser')
    expect(terminal.workflowTerminalResource.value?.id).toBe(6)
    expect(terminal.availableParserActions.value).toEqual(['write_clt_new_to_rem_accept'])
    expect(terminal.sendParserAction('write_clt_new_to_rem_accept')).toBe(true)
    expect(sendParserAction).toHaveBeenCalledWith({
      run_id: 11,
      step_id: 10,
      action: 'write_clt_new_to_rem_accept',
    })
    expect(terminal.stopWorkflowTerminal(parserStep)).toBe(true)
    expect(sendControl).toHaveBeenCalledWith('\u0003')
  })
})
