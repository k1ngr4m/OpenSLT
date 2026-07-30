import { computed, ref, type Ref } from 'vue'

export function cloneWorkflowValue<T>(value: T): T {
  return JSON.parse(JSON.stringify(value))
}

export function useStagedWorkflowNode<T extends object>() {
  const form = ref<T | null>(null) as Ref<T | null>
  const baseline = ref('')
  const dirty = computed(() => Boolean(form.value && JSON.stringify(form.value) !== baseline.value))

  function stage(value: T | null | undefined) {
    form.value = value ? cloneWorkflowValue(value) : null
    baseline.value = form.value ? JSON.stringify(form.value) : ''
  }

  function reset() {
    form.value = baseline.value ? JSON.parse(baseline.value) : null
  }

  function snapshot(): T | null {
    return form.value ? cloneWorkflowValue(form.value) : null
  }

  return { form, dirty, stage, reset, snapshot }
}
