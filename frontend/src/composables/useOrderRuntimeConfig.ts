import { computed, reactive, ref, watch, type ComputedRef, type Ref } from 'vue'
import { ElMessage } from '@/ui/elementPlusServices'
import { api, errorMessage } from '@/api/client'
import type { OrderConfigFile } from '@/types/orderConfig'
import type { RunDetail, RunStep } from '@/types/run'

interface OrderRuntimeConfigOptions {
  canOperate: ComputedRef<boolean>
  currentStep: ComputedRef<RunStep | null>
  selectedStep: ComputedRef<RunStep | null>
  run: Ref<RunDetail | null>
  runId: number
  orderResourceId: ComputedRef<number | null>
  reload: () => Promise<void>
}

export function useOrderRuntimeConfig(options: OrderRuntimeConfigOptions) {
  const { canOperate, currentStep, selectedStep, run, runId, orderResourceId, reload } = options
  const editingOrderConfig = ref(false)
  const loadingOrderConfigs = ref(false)
  const savingOrderConfig = ref(false)
  const orderConfigFiles = ref<OrderConfigFile[]>([])
  const orderConfigDraft = reactive({ xml_filename: '', network_interface: '' })
  const orderConfigEditAllowed = computed(() => Boolean(
    canOperate.value
    && orderResourceId.value
    && selectedStep.value?.node_type === 'order_preparation'
    && selectedStep.value.id === currentStep.value?.id
    && (
      (run.value?.status === 'awaiting_step_start' && selectedStep.value.status === 'pending')
      || (run.value?.status === 'awaiting_step_retry' && selectedStep.value.status === 'failed')
    )
  ))
  const canEditOrderConfig = computed(() => orderConfigEditAllowed.value && !savingOrderConfig.value)
  const orderConfigValidationMessage = computed(() => {
    if (!orderConfigDraft.xml_filename.trim()) return '请选择 XML 配置'
    const interfaceName = orderConfigDraft.network_interface.trim()
    if (interfaceName && !/^[A-Za-z0-9_.-]{1,15}$/.test(interfaceName)) return '网卡接口名称不合法'
    return ''
  })
  const orderConfigDirty = computed(() => {
    const config = selectedStep.value?.config_snapshot || {}
    return orderConfigDraft.xml_filename.trim() !== String(config.xml_filename || '')
      || orderConfigDraft.network_interface.trim() !== String(config.network_interface || '')
  })
  const orderConfigActionBlocked = computed(() => Boolean(
    editingOrderConfig.value && selectedStep.value?.id === currentStep.value?.id,
  ))

  async function refreshOrderConfigs() {
    const resourceId = orderResourceId.value
    if (!resourceId) {
      orderConfigFiles.value = []
      return
    }
    loadingOrderConfigs.value = true
    try {
      const { data } = await api.get(`/resources/${resourceId}/order-configs`)
      orderConfigFiles.value = Array.isArray(data.files) ? data.files : []
    } catch (error) {
      orderConfigFiles.value = []
      ElMessage.error(errorMessage(error))
    } finally {
      loadingOrderConfigs.value = false
    }
  }

  async function startEditingOrderConfig() {
    if (!canEditOrderConfig.value || !selectedStep.value) return
    orderConfigDraft.xml_filename = String(selectedStep.value.config_snapshot.xml_filename || '')
    orderConfigDraft.network_interface = String(selectedStep.value.config_snapshot.network_interface || '')
    editingOrderConfig.value = true
    await refreshOrderConfigs()
  }

  function cancelEditingOrderConfig() {
    editingOrderConfig.value = false
    orderConfigDraft.xml_filename = ''
    orderConfigDraft.network_interface = ''
  }

  async function saveOrderRuntimeConfig() {
    const step = currentStep.value
    if (!step || !canEditOrderConfig.value || orderConfigValidationMessage.value) return
    savingOrderConfig.value = true
    try {
      await api.put(`/runs/${runId}/steps/${step.id}/order-config`, {
        xml_filename: orderConfigDraft.xml_filename.trim(),
        network_interface: orderConfigDraft.network_interface.trim(),
      })
      cancelEditingOrderConfig()
      ElMessage.success('发单配置已保存')
      await reload()
    } catch (error) {
      ElMessage.error(errorMessage(error))
    } finally {
      savingOrderConfig.value = false
    }
  }

  watch(
    () => `${selectedStep.value?.id || ''}:${currentStep.value?.id || ''}:${run.value?.status || ''}:${selectedStep.value?.status || ''}`,
    () => {
      if (editingOrderConfig.value && !orderConfigEditAllowed.value) cancelEditingOrderConfig()
    },
  )

  return {
    canEditOrderConfig,
    cancelEditingOrderConfig,
    editingOrderConfig,
    loadingOrderConfigs,
    orderConfigActionBlocked,
    orderConfigDirty,
    orderConfigDraft,
    orderConfigFiles,
    orderConfigValidationMessage,
    refreshOrderConfigs,
    saveOrderRuntimeConfig,
    savingOrderConfig,
    startEditingOrderConfig,
  }
}
