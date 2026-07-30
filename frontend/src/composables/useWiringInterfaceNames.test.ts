import { computed, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { useWiringInterfaceNames } from '@/composables/useWiringInterfaceNames'
import type { RunDetail, RunStep } from '@/types/run'
import { buildWiringSnapshot } from '@/utils/wiring'

const message = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }))

vi.mock('@/api/client', () => ({
  api: { put: vi.fn() },
  errorMessage: (error: unknown) => String(error),
}))
vi.mock('element-plus', () => ({ ElMessage: message }))

function wiringStep(status: RunStep['status'] = 'pending', businessCode = 'fut_mm'): RunStep {
  return {
    id: 17,
    code: 'wiring',
    name: '接线确认',
    workflow_node_id: 17,
    node_type: 'wiring_confirmation',
    config_snapshot: {
      wiring_snapshot: buildWiringSnapshot(
        businessCode,
        { id: 1, name: 'REM-01', host: '10.1.51.8', trade_ip: '180.1.1.101' },
        { id: 2, name: 'Market-01', host: '10.1.51.101' },
        { id: 3, name: 'SLNIC-01', host: '10.1.51.210' },
      ),
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

function setup(step = wiringStep(), runStatus: RunDetail['status'] = 'awaiting_step_start') {
  const current = ref<RunStep | null>(step)
  const selected = ref<RunStep | null>(step)
  const run = ref({ id: 9, status: runStatus, steps: [step] } as unknown as RunDetail)
  const reload = vi.fn().mockResolvedValue(undefined)
  const wiring = useWiringInterfaceNames({
    canOperate: computed(() => true),
    currentStep: computed(() => current.value),
    selectedStep: computed(() => selected.value),
    run,
    runId: 9,
    reload,
  })
  return { current, reload, run, selected, wiring }
}

describe('useWiringInterfaceNames', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.put).mockResolvedValue({ data: {} })
  })

  it('edits and saves interface names before starting the current wiring step', async () => {
    const { reload, wiring } = setup()
    expect(wiring.canEditWiringNames.value).toBe(true)

    wiring.startEditingWiringNames()
    wiring.updateWiringInterfaceName('client', ' client-new ')
    wiring.updateWiringInterfaceName('market', 'market-new')
    expect(wiring.wiringNamesDirty.value).toBe(true)
    expect(wiring.wiringActionBlocked.value).toBe(true)

    await wiring.saveWiringInterfaceNames()

    expect(api.put).toHaveBeenCalledWith('/runs/9/steps/17/wiring-interface-names', {
      client_interface_name: 'client-new',
      market_interface_name: 'market-new',
      auxiliary_interface_names: [],
    })
    expect(message.success).toHaveBeenCalledWith('网卡名称已保存')
    expect(reload).toHaveBeenCalled()
    expect(wiring.editingWiringNames.value).toBe(false)
  })

  it('requires all four integrated interface names', async () => {
    const { wiring } = setup(wiringStep('waiting', 'rem_two'), 'awaiting_step_completion')
    wiring.startEditingWiringNames()
    wiring.updateWiringInterfaceName('auxiliary', ' ', 0)

    expect(wiring.wiringValidationMessage.value).toBe('请输入第 3 个接口名称')
    await wiring.saveWiringInterfaceNames()
    expect(api.put).not.toHaveBeenCalled()
  })

  it('does not allow editing a historical or completed wiring step', () => {
    const completed = setup(wiringStep('succeeded'), 'awaiting_step_start')
    completed.current.value = null
    expect(completed.wiring.canEditWiringNames.value).toBe(false)

    const finishedRun = setup(wiringStep('succeeded'), 'completed')
    expect(finishedRun.wiring.canEditWiringNames.value).toBe(false)
  })
})
