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
    <span class="status-badge" :class="`is-${type}`" :data-status="status">
      <span class="status-mark" aria-hidden="true" />{{ text }}
    </span>
  </el-tooltip>
</template>

<style scoped>
.status-badge{display:inline-flex;min-height:22px;align-items:center;color:var(--ui-running);font-size:11px;font-weight:600;line-height:1.2;white-space:nowrap}.status-mark{display:inline-block;flex:none;width:5px;height:5px;margin-right:6px;border-radius:50%;background:currentColor}.status-badge.is-success{color:var(--ui-success)}.status-badge.is-warning{color:var(--ui-warning)}.status-badge.is-danger{padding:3px 7px;border-radius:4px;background:#fcecee;color:#c63d50}.status-badge.is-info{color:var(--ui-paused)}
</style>
