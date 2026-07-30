import { computed, nextTick, ref, type ComputedRef, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import SshTerminalPanel from '@/components/SshTerminalPanel.vue'
import type {
  JsonMap,
  RunDetail,
  RunResourceSnapshot,
  RunStep,
  WorkflowTerminalKind,
} from '@/types/run'
import { resourceText } from '@/utils/status'

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
  const terminalCommandPendingStepId = ref<number | null>(null)
  const queuedTerminalCommand = ref<{
    stepId: number
    operation: 'start' | 'retry'
    kind: WorkflowTerminalKind
  } | null>(null)

  const remResource = computed(() => resourceByType('rem'))
  const marketResource = computed(() => resourceByType('market'))
  const slnicResource = computed(() => resourceByType('slnic'))
  const orderResource = computed(() => resourceByType('order'))
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
      return '点击顶部“开始”后，系统会在远端 tmux 中启动发单程序；确认程序就绪后使用下方动作按钮。终端支持刷新和重连。'
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
      return '点击顶部“开始”后，合并与转换 pcapng 的命令会在这个终端中下发；确认完成后再点击顶部“完成”。'
    }
    return '点击顶部“开始”后，启动脚本会在这个终端中下发。'
  })
  const remTerminalSubtitle = computed(() => terminalSubtitle('rem'))
  const marketTerminalSubtitle = computed(() => terminalSubtitle('market'))
  const slnicTerminalSubtitle = computed(() => terminalSubtitle('slnic'))
  const orderTerminalSubtitle = computed(() => terminalSubtitle('order'))

  function resourceByType(resourceType: string): RunResourceSnapshot | null {
    const resources = run.value?.config_snapshot?.resources
    if (!Array.isArray(resources)) return null
    return resources.find(resource => resource.type === resourceType) || null
  }

  function terminalKindForStep(step: RunStep | null): WorkflowTerminalKind | null {
    if (!step) return null
    if (step.node_type === 'order_preparation') return 'order'
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
    return kind === 'market' ? marketWorkflowTerminalPanel.value : slnicWorkflowTerminalPanel.value
  }

  function resourceForTerminalKind(kind: WorkflowTerminalKind) {
    if (kind === 'order') return orderResource.value
    if (kind === 'rem') return remResource.value
    return kind === 'market' ? marketResource.value : slnicResource.value
  }

  function titleForTerminalKind(kind: WorkflowTerminalKind) {
    if (kind === 'order') return '发单 SSH 终端'
    if (kind === 'rem') return 'REM SSH 终端'
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

  return {
    handleWorkflowTerminalCommand,
    handleWorkflowTerminalError,
    handleWorkflowTerminalStatus,
    marketResource,
    marketTerminalSubtitle,
    marketWorkflowTerminalPanel,
    orderResource,
    orderTerminalSubtitle,
    orderWorkflowTerminalPanel,
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
