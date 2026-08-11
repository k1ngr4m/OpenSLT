import { computed, nextTick, ref, type ComputedRef, type Ref } from 'vue'
import { ElMessage } from '@/ui/elementPlusServices'
import SshTerminalPanel from '@/components/SshTerminalPanel.vue'
import type {
  JsonMap,
  RunDetail,
  RunResourceSnapshot,
  RunStep,
  WorkflowTerminalKind,
} from '@/types/run'
import { resourceText } from '@/utils/status'
import { parserActionOptions, type ParserAction } from '@/utils/parserActions'

interface WorkflowTerminalOptions {
  active: Ref<string>
  manualStepSelection: Ref<boolean>
  reload: () => Promise<void>
  run: Ref<RunDetail | null>
  runId: number
  selectedStep: ComputedRef<RunStep | null>
  selectedStepId: Ref<number | null>
}

export function useWorkflowTerminal(options: WorkflowTerminalOptions) {
  const { active, manualStepSelection, reload, run, runId, selectedStep, selectedStepId } = options
  const remWorkflowTerminalPanel = ref<InstanceType<typeof SshTerminalPanel> | null>(null)
  const marketWorkflowTerminalPanel = ref<InstanceType<typeof SshTerminalPanel> | null>(null)
  const slnicWorkflowTerminalPanel = ref<InstanceType<typeof SshTerminalPanel> | null>(null)
  const orderWorkflowTerminalPanel = ref<InstanceType<typeof SshTerminalPanel> | null>(null)
  const parserWorkflowTerminalPanel = ref<InstanceType<typeof SshTerminalPanel> | null>(null)
  const terminalCommandPendingStepId = ref<number | null>(null)
  const parserActionPending = ref<string | null>(null)
  const queuedTerminalCommand = ref<{
    stepId: number
    operation: 'start' | 'retry'
    kind: WorkflowTerminalKind
  } | null>(null)

  const remResource = computed(() => resourceByType('rem'))
  const marketResource = computed(() => resourceByType('market'))
  const slnicResource = computed(() => resourceByType('slnic'))
  const orderResource = computed(() => resourceByType('order'))
  const parserResource = computed(() => resourceByType('parser'))
  const workflowTerminalKind = computed<WorkflowTerminalKind | null>(() =>
    terminalKindForStep(selectedStep.value),
  )
  const showWorkflowTerminal = computed(() => Boolean(workflowTerminalKind.value))
  const workflowTerminalResource = computed(() =>
    workflowTerminalKind.value ? resourceForTerminalKind(workflowTerminalKind.value) : null,
  )
  const workflowTerminalResourceText = computed(() => {
    const kind = workflowTerminalKind.value
    return kind ? resourceText[kind] : ''
  })
  const workflowTerminalTitle = computed(() =>
    workflowTerminalKind.value ? titleForTerminalKind(workflowTerminalKind.value) : 'SSH 终端',
  )
  const workflowTerminalDescription = computed(() => {
    if (selectedStep.value?.node_type === 'order_preparation') {
      return '点击顶部“开始”后，系统会在远端 tmux 中启动发单程序；确认程序就绪后可直接输入命令或使用下方动作按钮。终端支持刷新和重连。'
    }
    if (selectedStep.value?.node_type === 'rem_startup') {
      return '点击顶部“开始”后，配置的 REM 命令会在这个终端中逐行下发；查看输出并确认完成后再点击顶部“完成”。'
    }
    if (selectedStep.value?.node_type === 'market_startup') {
      return '点击顶部“开始”后，已选择的模拟市场脚本会按顺序在这个终端中下发；查看输出并确认完成后再点击顶部“完成”。'
    }
    if (selectedStep.value?.node_type === 'slnic_stop_capture') {
      return '点击顶部“开始”后，关闭抓包脚本会在这个终端中下发。'
    }
    if (selectedStep.value?.node_type === 'slnic_merge_capture') {
      return '点击顶部“开始”后，Linux 合并命令会在这个终端中下发；随后复制页面生成的 Windows editcap 命令到本机执行，确认完成后再点击顶部“完成”。'
    }
    if (selectedStep.value?.node_type === 'parser_parse') {
      return '点击顶部“开始”后，解析工具会在这个 SSH Shell 中启动；可直接输入或点击下方快捷指令，生成 CSV 后再点击顶部“完成”。'
    }
    return '点击顶部“开始”后，启动脚本会在这个终端中下发。'
  })
  const remTerminalSubtitle = computed(() => terminalSubtitle('rem'))
  const marketTerminalSubtitle = computed(() => terminalSubtitle('market'))
  const slnicTerminalSubtitle = computed(() => terminalSubtitle('slnic'))
  const orderTerminalSubtitle = computed(() => terminalSubtitle('order'))
  const parserTerminalSubtitle = computed(() => terminalSubtitle('parser'))
  const availableParserActions = computed(() => {
    const configured = selectedStep.value?.result_summary?.supported_parser_actions
    if (!Array.isArray(configured)) return [...parserActionOptions]
    return configured.filter((item): item is ParserAction => typeof item === 'string' && parserActionOptions.includes(item as ParserAction))
  })

  function resourceByType(resourceType: string): RunResourceSnapshot | null {
    const resources = run.value?.config_snapshot?.resources
    if (!Array.isArray(resources)) return null
    return resources.find(resource => resource.type === resourceType) || null
  }

  function terminalKindForStep(step: RunStep | null): WorkflowTerminalKind | null {
    if (!step) return null
    if (step.node_type === 'order_preparation') return 'order'
    if (step.node_type === 'parser_parse') return 'parser'
    if (step.node_type === 'rem_startup') return 'rem'
    if (step.node_type === 'market_startup') return 'market'
    if (['slnic_start_capture', 'slnic_stop_capture', 'slnic_merge_capture'].includes(step.node_type)) {
      return 'slnic'
    }
    return null
  }

  function panelForTerminalKind(kind: WorkflowTerminalKind) {
    if (kind === 'order') return orderWorkflowTerminalPanel.value
    if (kind === 'rem') return remWorkflowTerminalPanel.value
    if (kind === 'parser') return parserWorkflowTerminalPanel.value
    return kind === 'market' ? marketWorkflowTerminalPanel.value : slnicWorkflowTerminalPanel.value
  }

  function resourceForTerminalKind(kind: WorkflowTerminalKind) {
    if (kind === 'order') return orderResource.value
    if (kind === 'rem') return remResource.value
    if (kind === 'parser') return parserResource.value
    return kind === 'market' ? marketResource.value : slnicResource.value
  }

  function titleForTerminalKind(kind: WorkflowTerminalKind) {
    if (kind === 'order') return '发单 SSH 终端'
    if (kind === 'rem') return 'REM SSH 终端'
    if (kind === 'parser') return '解析工具 SSH 终端'
    return kind === 'market' ? '模拟市场 SSH 终端' : 'SLNIC SSH 终端'
  }

  function terminalSubtitle(kind: WorkflowTerminalKind) {
    const resource = resourceForTerminalKind(kind)
    if (!resource) return ''
    const label = resourceText[kind]
    return [label, resource.host, resource.version].filter(Boolean).join(' · ')
  }

  async function runWorkflowStepInTerminal(step: RunStep, operation: 'start' | 'retry') {
    const kind = terminalKindForStep(step)
    if (!kind || kind === 'order') throw new Error('当前节点不支持通过交互终端启动')
    manualStepSelection.value = false
    selectedStepId.value = step.id
    active.value = 'detail'
    await nextTick()
    const panel = panelForTerminalKind(kind)
    const title = titleForTerminalKind(kind)
    if (!resourceForTerminalKind(kind) || !panel) {
      throw new Error(`未找到${title}，请检查运行资源配置`)
    }
    if (!panel.connected) {
      queuedTerminalCommand.value = { stepId: step.id, operation, kind }
      terminalCommandPendingStepId.value = step.id
      if (!panel.connecting) panel.connect()
      ElMessage.info(`${title}连接中，连接成功后会自动下发指令`)
      return
    }
    terminalCommandPendingStepId.value = step.id
    const sent = panel.sendWorkflowStepCommand({ run_id: runId, step_id: step.id, operation })
    if (!sent) {
      terminalCommandPendingStepId.value = null
      throw new Error(`${title}未连接，无法下发指令`)
    }
  }

  function dispatchQueuedTerminalCommand(kind: WorkflowTerminalKind) {
    const queued = queuedTerminalCommand.value
    const panel = panelForTerminalKind(kind)
    if (!queued || queued.kind !== kind || !panel?.connected) return
    const sent = panel.sendWorkflowStepCommand({
      run_id: runId,
      step_id: queued.stepId,
      operation: queued.operation,
    })
    if (!sent) {
      ElMessage.error(`${titleForTerminalKind(kind)}未连接，无法下发指令`)
      terminalCommandPendingStepId.value = null
    }
    queuedTerminalCommand.value = null
  }

  function handleWorkflowTerminalStatus(kind: WorkflowTerminalKind, message: JsonMap) {
    if (message.status === 'connected') dispatchQueuedTerminalCommand(kind)
  }

  function handleWorkflowTerminalError(kind: WorkflowTerminalKind, message: string) {
    if (queuedTerminalCommand.value?.kind === kind) {
      ElMessage.error(message)
      queuedTerminalCommand.value = null
      terminalCommandPendingStepId.value = null
    }
  }

  function handleWorkflowTerminalCommand(kind: WorkflowTerminalKind, message: JsonMap) {
    const title = titleForTerminalKind(kind)
    if (message.status === 'dispatched') {
      ElMessage.success(`${title}指令已在终端中下发`)
      window.setTimeout(reload, 300)
    } else if (message.status === 'failed') {
      ElMessage.error(String(message.message || `${title}指令下发失败`))
    }
    queuedTerminalCommand.value = null
    terminalCommandPendingStepId.value = null
  }

  function sendParserAction(action: ParserAction) {
    const step = selectedStep.value
    const panel = parserWorkflowTerminalPanel.value
    if (!step || step.node_type !== 'parser_parse' || !availableParserActions.value.includes(action) || !panel?.connected) return false
    parserActionPending.value = action
    const sent = panel.sendParserAction({ run_id: runId, step_id: step.id, action })
    if (!sent) parserActionPending.value = null
    return sent
  }

  function handleParserAction(message: JsonMap) {
    if (message.status === 'dispatched') {
      ElMessage.success(`解析指令 ${String(message.action || '')} 已发送`)
      window.setTimeout(reload, 300)
    } else if (message.status === 'failed') {
      ElMessage.error(String(message.message || '解析指令下发失败'))
    }
    parserActionPending.value = null
  }

  function stopWorkflowTerminal(step: RunStep) {
    if (step.node_type !== 'parser_parse') return false
    return Boolean(parserWorkflowTerminalPanel.value?.sendControl('\u0003'))
  }

  return {
    handleWorkflowTerminalCommand,
    handleParserAction,
    handleWorkflowTerminalError,
    handleWorkflowTerminalStatus,
    marketResource,
    marketTerminalSubtitle,
    marketWorkflowTerminalPanel,
    orderResource,
    orderTerminalSubtitle,
    orderWorkflowTerminalPanel,
    parserResource,
    parserTerminalSubtitle,
    parserWorkflowTerminalPanel,
    parserActionPending,
    availableParserActions,
    sendParserAction,
    stopWorkflowTerminal,
    remResource,
    remTerminalSubtitle,
    remWorkflowTerminalPanel,
    runWorkflowStepInTerminal,
    showWorkflowTerminal,
    slnicResource,
    slnicTerminalSubtitle,
    slnicWorkflowTerminalPanel,
    terminalCommandPendingStepId,
    workflowTerminalKind,
    workflowTerminalResource,
    workflowTerminalResourceText,
    workflowTerminalTitle,
    workflowTerminalDescription,
  }
}
