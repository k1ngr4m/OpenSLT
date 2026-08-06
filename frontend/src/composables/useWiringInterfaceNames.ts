import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import { ElMessage } from '@/ui/elementPlusServices'
import { api, errorMessage } from '@/api/client'
import type { RunDetail, RunStep } from '@/types/run'
import type { WiringSnapshot } from '@/utils/wiring'

type InterfaceSlot = 'client' | 'market' | 'auxiliary'
type AddressableInterfaceSlot = 'client' | 'market'

interface WiringInterfaceNamesOptions {
  canOperate: ComputedRef<boolean>
  currentStep: ComputedRef<RunStep | null>
  selectedStep: ComputedRef<RunStep | null>
  run: Ref<RunDetail | null>
  runId: number
  reload: () => Promise<void>
}

function stepWiringSnapshot(step: RunStep | null): WiringSnapshot | null {
  const value = step?.config_snapshot?.wiring_snapshot
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as unknown as WiringSnapshot
    : null
}

function cloneWiringSnapshot(snapshot: WiringSnapshot): WiringSnapshot {
  return {
    ...snapshot,
    client_interface: { ...snapshot.client_interface },
    market_interface: { ...snapshot.market_interface },
    auxiliary_interfaces: [...snapshot.auxiliary_interfaces],
  }
}

function interfaceSignature(snapshot: WiringSnapshot | null) {
  if (!snapshot) return ''
  return [
    snapshot.client_interface.name,
    snapshot.client_interface.ip_address,
    snapshot.market_interface.name,
    snapshot.market_interface.ip_address,
    ...snapshot.auxiliary_interfaces,
  ].join('\n')
}

function isIpv4Address(value: string) {
  const parts = value.trim().split('.')
  return parts.length === 4
    && parts.every(part => /^(0|[1-9]\d{0,2})$/.test(part) && Number(part) <= 255)
}

function wiringValidation(snapshot: WiringSnapshot | null) {
  if (!snapshot) return '接线图尚未就绪'
  if (!snapshot.client_interface.name.trim()) return '请输入第 1 个接口名称'
  if (!isIpv4Address(snapshot.client_interface.ip_address)) return '请输入合法的第 1 个接口 IPv4 地址'
  if (!snapshot.market_interface.name.trim()) return '请输入第 2 个接口名称'
  if (!isIpv4Address(snapshot.market_interface.ip_address)) return '请输入合法的第 2 个接口 IPv4 地址'
  if (snapshot.topology_kind !== 'soft_core') {
    if (snapshot.auxiliary_interfaces.length !== 2) return '整合版接线图需要配置四个接口名称'
    if (!snapshot.auxiliary_interfaces[0]?.trim()) return '请输入第 3 个接口名称'
    if (!snapshot.auxiliary_interfaces[1]?.trim()) return '请输入第 4 个接口名称'
  }
  return ''
}

export function useWiringInterfaceNames(options: WiringInterfaceNamesOptions) {
  const { canOperate, currentStep, selectedStep, run, runId, reload } = options
  const editingWiringNames = ref(false)
  const savingWiringNames = ref(false)
  const wiringDraft = ref<WiringSnapshot | null>(null)
  const savedWiringSnapshot = computed(() => stepWiringSnapshot(selectedStep.value))
  const wiringSnapshot = computed(() => (
    editingWiringNames.value ? wiringDraft.value : savedWiringSnapshot.value
  ))
  const wiringEditAllowed = computed(() => Boolean(
    canOperate.value
    && selectedStep.value?.node_type === 'wiring_confirmation'
    && selectedStep.value.id === currentStep.value?.id
    && savedWiringSnapshot.value
    && (
      (run.value?.status === 'awaiting_step_start' && selectedStep.value.status === 'pending')
      || (run.value?.status === 'awaiting_step_completion' && selectedStep.value.status === 'waiting')
    )
  ))
  const canEditWiringNames = computed(() => wiringEditAllowed.value && !savingWiringNames.value)
  const wiringNamesDirty = computed(() => (
    interfaceSignature(wiringDraft.value) !== interfaceSignature(savedWiringSnapshot.value)
  ))
  const wiringValidationMessage = computed(() => wiringValidation(wiringDraft.value))
  const wiringActionBlocked = computed(() => {
    const step = currentStep.value
    if (!step || step.node_type !== 'wiring_confirmation') return false
    if (editingWiringNames.value && selectedStep.value?.id === step.id) return true
    if (step.config_snapshot?.diagram !== 'resource') return false
    return Boolean(wiringValidation(stepWiringSnapshot(step)))
  })

  function startEditingWiringNames() {
    if (!canEditWiringNames.value || !savedWiringSnapshot.value) return
    wiringDraft.value = cloneWiringSnapshot(savedWiringSnapshot.value)
    editingWiringNames.value = true
  }

  function cancelEditingWiringNames() {
    editingWiringNames.value = false
    wiringDraft.value = null
  }

  function updateWiringInterfaceName(slot: InterfaceSlot, value: string, index?: number) {
    const snapshot = wiringDraft.value
    if (!editingWiringNames.value || !snapshot) return
    if (slot === 'client') snapshot.client_interface.name = value
    else if (slot === 'market') snapshot.market_interface.name = value
    else snapshot.auxiliary_interfaces[index ?? 0] = value
  }

  function updateWiringInterfaceIpAddress(slot: AddressableInterfaceSlot, value: string) {
    const snapshot = wiringDraft.value
    if (!editingWiringNames.value || !snapshot) return
    snapshot[`${slot}_interface`].ip_address = value
  }

  async function saveWiringInterfaceNames() {
    const step = currentStep.value
    const snapshot = wiringDraft.value
    if (!step || !snapshot || !canEditWiringNames.value || wiringValidationMessage.value) return
    savingWiringNames.value = true
    try {
      await api.put(`/runs/${runId}/steps/${step.id}/wiring-interface-names`, {
        client_interface_name: snapshot.client_interface.name.trim(),
        client_interface_ip_address: snapshot.client_interface.ip_address.trim(),
        market_interface_name: snapshot.market_interface.name.trim(),
        market_interface_ip_address: snapshot.market_interface.ip_address.trim(),
        auxiliary_interface_names: snapshot.auxiliary_interfaces.map(name => name.trim()),
      })
      cancelEditingWiringNames()
      ElMessage.success('网卡信息已保存')
      await reload()
    } catch (error) {
      ElMessage.error(errorMessage(error))
    } finally {
      savingWiringNames.value = false
    }
  }

  watch(
    () => `${selectedStep.value?.id || ''}:${currentStep.value?.id || ''}:${run.value?.status || ''}:${selectedStep.value?.status || ''}`,
    () => {
      if (editingWiringNames.value && !wiringEditAllowed.value) cancelEditingWiringNames()
    },
  )

  return {
    canEditWiringNames,
    cancelEditingWiringNames,
    editingWiringNames,
    saveWiringInterfaceNames,
    savingWiringNames,
    startEditingWiringNames,
    updateWiringInterfaceIpAddress,
    updateWiringInterfaceName,
    wiringActionBlocked,
    wiringNamesDirty,
    wiringSnapshot,
    wiringValidationMessage,
  }
}
