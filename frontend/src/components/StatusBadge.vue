<script setup lang="ts">
import { computed } from 'vue'
import { statusText, statusType } from '@/utils/status'

const props = defineProps<{
  status: string
  label?: string
  showRaw?: boolean
}>()

const text = computed(() => props.label || statusText[props.status] || props.status || '未知')
const type = computed(() => statusType(props.status))
</script>

<template>
  <el-tooltip :disabled="!showRaw || text === status" :content="status" placement="top">
    <el-tag :type="type" effect="plain" size="small" class="status-badge">
      <span class="status-mark" aria-hidden="true" />{{ text }}
    </el-tag>
  </el-tooltip>
</template>

<style scoped>
.status-badge{font-weight:600}.status-mark{display:inline-block;width:5px;height:5px;margin-right:6px;border-radius:50%;background:currentColor;vertical-align:1px}
</style>
