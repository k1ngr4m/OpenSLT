import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { ElMessage, ElMessageBox } from '@/ui/elementPlusServices'
import { api, errorMessage } from '@/api/client'
import type { JsonMap, RunDetail, RunStep } from '@/types/run'

export interface OrderActionHistoryEntry {
  request_id: string
  action: string
  status: string
  requested_by?: number
  started_at?: string
  finished_at?: string | null
  error?: string | null
  confirmed_by?: number
  confirmed_at?: string
}

interface OrderActionOptions {
  currentStep: ComputedRef<RunStep | null>
  reload: () => Promise<void>
  retryStep: (step: RunStep, operation: 'retry') => Promise<void>
  run: Ref<RunDetail | null>
  runId: number
}

const DANGEROUS_ACTIONS = new Set(['cxl_order', 'stop_order'])
const UNRESOLVED_STATUSES = new Set(['dispatching', 'unknown'])

function historyEntry(value: unknown): OrderActionHistoryEntry | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const item = value as JsonMap
  if (typeof item.request_id !== 'string' || typeof item.action !== 'string' || typeof item.status !== 'string') return null
  return item as unknown as OrderActionHistoryEntry
}

export function useOrderActions(options: OrderActionOptions) {
  const { currentStep, reload, retryStep, run, runId } = options
  const sendingOrderAction = ref<string | null>(null)

  const defaultOrderAction = computed(() => currentStep.value?.node_type === 'order_preparation'
    ? String(currentStep.value.config_snapshot?.order_action || 'new_order')
    : '')
  const orderActionStatus = computed(() => currentStep.value?.node_type === 'order_preparation'
    ? String(currentStep.value.result_summary?.order_action_status || 'pending')
    : '')
  const orderActionUnresolved = computed(() => UNRESOLVED_STATUSES.has(orderActionStatus.value))
  const availableOrderActions = computed(() => {
    const configured = currentStep.value?.result_summary?.supported_order_actions
    const supported = Array.isArray(configured)
      ? configured.filter((item): item is string => typeof item === 'string' && Boolean(item))
      : []
    if (!supported.length) return defaultOrderAction.value ? [defaultOrderAction.value] : []
    const unique = [...new Set(supported)]
    const preferred = defaultOrderAction.value
    if (!preferred || !unique.includes(preferred)) return unique
    return [preferred, ...unique.filter(action => action !== preferred)]
  })
  const recentOrderActionHistory = computed(() => {
    const raw = currentStep.value?.result_summary?.order_action_history
    if (!Array.isArray(raw)) return []
    return raw.map(historyEntry).filter((item): item is OrderActionHistoryEntry => Boolean(item)).slice(-10).reverse()
  })
  const canSendOrderActions = computed(() => Boolean(
    currentStep.value?.node_type === 'order_preparation'
    && currentStep.value.status === 'waiting'
    && run.value?.status === 'awaiting_step_completion'
    && !orderActionUnresolved.value
    && !sendingOrderAction.value,
  ))

  function isDangerousOrderAction(action: string) {
    return DANGEROUS_ACTIONS.has(action)
  }

  async function confirmDangerousAction(action: string) {
    if (!isDangerousOrderAction(action)) return true
    try {
      await ElMessageBox.confirm(
        `确定发送 ${action}？该指令可能撤销订单或停止发单程序。`,
        '确认高风险指令',
        { type: 'warning', confirmButtonText: '确认发送' },
      )
      return true
    } catch {
      return false
    }
  }

  async function sendOrderAction(action: string) {
    const step = currentStep.value
    if (!step || !canSendOrderActions.value || !availableOrderActions.value.includes(action)) return
    if (!await confirmDangerousAction(action)) return
    sendingOrderAction.value = action
    try {
      await api.post(`/runs/${runId}/steps/${step.id}/order-action`, { action })
      ElMessage.success(`已发送 ${action}`)
      await reload()
    } catch (error) {
      ElMessage.error(errorMessage(error))
      await reload()
    } finally {
      sendingOrderAction.value = null
    }
  }

  async function confirmCurrentOrderAction() {
    const step = currentStep.value
    if (!step || !orderActionUnresolved.value) return
    try {
      await ElMessageBox.confirm('请确认终端输出表明最近一条动作已经发送。确认后可以继续发送其他指令。', '确认已发单', { type: 'warning' })
      await api.post(`/runs/${runId}/steps/${step.id}/order-action/confirm`)
      ElMessage.success('已确认最近一条发单动作')
      await reload()
    } catch (error) {
      if (error !== 'cancel' && error !== 'close') ElMessage.error(errorMessage(error))
    }
  }

  async function retryUnknownOrderAction() {
    const step = currentStep.value
    if (!step || !orderActionUnresolved.value) return
    try {
      await ElMessageBox.confirm('该动作可能已经到达发单程序。重试会关闭当前会话并重新启动节点，可能造成重复发单。', '重试发单节点', { type: 'warning', confirmButtonText: '仍要重试' })
      await retryStep(step, 'retry')
    } catch (error) {
      if (error !== 'cancel' && error !== 'close') ElMessage.error(errorMessage(error))
    }
  }

  return {
    availableOrderActions,
    canSendOrderActions,
    confirmCurrentOrderAction,
    defaultOrderAction,
    isDangerousOrderAction,
    orderActionStatus,
    orderActionUnresolved,
    recentOrderActionHistory,
    retryUnknownOrderAction,
    sendOrderAction,
    sendingOrderAction,
  }
}
