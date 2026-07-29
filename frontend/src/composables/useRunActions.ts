import { reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import type { RunStep, RunVerdict, RunVerdictWrite } from '@/types/run'

type StepOperation = 'start' | 'complete' | 'confirm' | 'retry'

interface RunActionsOptions {
  runId: number
  reload: () => Promise<void>
  runTerminalStep: (step: RunStep, operation: 'start' | 'retry') => Promise<void>
}

export function useRunActions(options: RunActionsOptions) {
  const { reload, runId, runTerminalStep } = options
  const actingStepId = ref<number | null>(null)
  const regeneratingReports = ref(false)
  const verdictDialog = ref(false)
  const verdict = reactive<RunVerdictWrite>({
    final_result: 'passed',
    issue_description: '',
    notes: '',
  })

  async function action(path: string, message: string) {
    try {
      await api.post(`/runs/${runId}/${path}`)
      ElMessage.success(message)
      window.setTimeout(reload, 300)
    } catch (error) {
      ElMessage.error(errorMessage(error))
    }
  }

  async function cancel() {
    try {
      await ElMessageBox.confirm(
        '取消后将执行安全清理并释放资源，确定继续？',
        '取消运行',
        { type: 'warning' },
      )
    } catch {
      return
    }
    await action('cancel', '取消指令已提交')
  }

  async function stepAction(step: RunStep, operation: StepOperation) {
    actingStepId.value = step.id
    try {
      const terminalNodeTypes = [
        'slnic_start_capture',
        'slnic_stop_capture',
        'slnic_merge_capture',
      ]
      if (
        terminalNodeTypes.includes(step.node_type)
        && (operation === 'start' || operation === 'retry')
      ) {
        await runTerminalStep(step, operation)
        return
      }
      await api.post(`/runs/${runId}/steps/${step.id}/${operation}`)
      const messages = { start: '节点已开始', complete: '节点已完成', confirm: '接线已确认', retry: '节点已重新执行' }
      ElMessage.success(messages[operation])
      window.setTimeout(reload, 300)
    } catch (error) {
      ElMessage.error(errorMessage(error))
    } finally {
      actingStepId.value = null
    }
  }

  async function submitVerdict() {
    try {
      await api.post(`/runs/${runId}/verdict`, verdict)
      ElMessage.success('结论和报告已生成')
      verdictDialog.value = false
      await reload()
    } catch (error) {
      ElMessage.error(errorMessage(error))
    }
  }

  function openVerdict(existing: RunVerdict | null) {
    const finalResult = existing?.final_result
    verdict.final_result = finalResult === 'conditional' || finalResult === 'failed' ? finalResult : 'passed'
    verdict.issue_description = existing?.issue_description || ''
    verdict.notes = existing?.notes || ''
    verdictDialog.value = true
  }

  async function regenerateReports() {
    regeneratingReports.value = true
    try {
      await api.post(`/runs/${runId}/reports`)
      ElMessage.success('新报告版本已生成')
      await reload()
    } catch (error) {
      ElMessage.error(errorMessage(error))
    } finally {
      regeneratingReports.value = false
    }
  }

  async function download(id: number) {
    try {
      const response = await api.get(`/artifacts/${id}/download`, { responseType: 'blob' })
      const disposition = response.headers['content-disposition'] || ''
      const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] || `artifact-${id}`
      const link = document.createElement('a')
      link.href = URL.createObjectURL(response.data)
      link.download = filename
      link.click()
      URL.revokeObjectURL(link.href)
    } catch (error) {
      ElMessage.error(errorMessage(error))
    }
  }

  return {
    actingStepId,
    action,
    cancel,
    download,
    openVerdict,
    regenerateReports,
    regeneratingReports,
    stepAction,
    submitVerdict,
    verdict,
    verdictDialog,
  }
}
