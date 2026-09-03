<script setup lang="ts">
import type { RunStep } from '@/types/run'
import { formatDuration, statusClass } from '@/utils/runDetail'
import { statusText } from '@/utils/status'

defineProps<{
  steps: RunStep[]
  selectedStepId: number | null
  currentStepId: number | null
  manualSelection: boolean
  logCounts: Map<number, number>
}>()

const emit = defineEmits<{
  select: [step: RunStep]
  followCurrent: []
}>()
</script>

<template>
  <section class="workflow-strip card" aria-label="流程节点">
    <div class="workflow-strip-head">
      <div>
        <strong>运行流程</strong>
        <span class="muted">{{ steps.length }} 个节点</span>
      </div>
      <el-button v-if="manualSelection" link type="primary" @click="emit('followCurrent')">回到当前节点</el-button>
    </div>
    <div v-if="steps.length" class="workflow-scroller">
      <button
        v-for="step in steps"
        :key="step.id"
        type="button"
        class="flow-step"
        :class="[statusClass(step.status), { 'is-selected': selectedStepId === step.id, 'is-current': currentStepId === step.id }]"
        @click="emit('select', step)"
      >
        <span class="flow-index mono" aria-hidden="true">
          <span v-if="step.status === 'succeeded'">✓</span>
          <span v-else-if="step.status.includes('failed') || step.status === 'cancelled'">×</span>
          <span v-else-if="step.status === 'running'" class="running-signal" />
          <span v-else>{{ String(step.position).padStart(2, '0') }}</span>
        </span>
        <span class="flow-body">
          <span class="flow-name">{{ step.name }}</span>
          <span class="flow-meta">
            {{ statusText[step.status] || step.status }}
            <template v-if="step.duration_ms != null"> · {{ formatDuration(step.duration_ms) }}</template>
            <template v-if="step.retry_count"> · 重试 {{ step.retry_count }}</template>
            <template v-if="logCounts.get(step.id)"> · 日志 {{ logCounts.get(step.id) }}</template>
          </span>
        </span>
      </button>
    </div>
    <el-empty v-else description="暂无流程节点" :image-size="72" />
  </section>
</template>

<style scoped>
.workflow-strip{padding:14px 16px 12px;margin-top:12px;box-shadow:none}.workflow-strip-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}.workflow-strip-head strong{margin-right:8px;font-size:16px}.workflow-strip-head .muted{font-size:11px}.workflow-scroller{display:flex;overflow-x:auto;padding:5px 2px 8px;scrollbar-width:thin}.flow-step{position:relative;display:grid;flex:1 0 132px;min-width:132px;max-width:190px;justify-items:center;gap:7px;padding:5px 8px 7px;border:0;border-radius:6px;background:transparent;color:var(--ui-text-primary);text-align:center;cursor:pointer;transition:background-color var(--ui-transition-fast),color var(--ui-transition-fast)}.flow-step::before{position:absolute;z-index:0;top:17px;right:50%;left:-50%;height:1.5px;background:#aab8bc;content:''}.flow-step:first-child::before{display:none}.flow-step:hover{background:#f3f8f7}.flow-step:focus-visible{outline:2px solid var(--ui-primary);outline-offset:1px}.flow-step.is-selected{background:#eaf7f4}.flow-step.is-selected::after{position:absolute;bottom:0;left:12px;width:2px;height:22px;background:var(--ui-primary);content:''}.flow-step.is-current .flow-name::after{margin-left:6px;color:var(--ui-primary);font-size:10px;font-weight:600;content:'CURRENT'}.flow-step.is-success::before{background:var(--ui-success)}.flow-step.is-running::before{background:var(--ui-running)}.flow-step.is-danger::before{background:var(--ui-danger)}.flow-index{position:relative;z-index:1;display:grid;width:25px;height:25px;place-items:center;border:1.5px solid #aab8bc;border-radius:50%;background:#fff;color:var(--ui-text-tertiary);font:600 10px/1 var(--ui-font-mono)}.flow-step.is-success .flow-index{border-color:var(--ui-success);background:var(--ui-success);color:#fff}.flow-step.is-danger .flow-index{border-color:var(--ui-danger);background:var(--ui-danger);color:#fff}.flow-step.is-running .flow-index{border-color:var(--ui-running);color:var(--ui-running)}.flow-step.is-waiting .flow-index{border-color:var(--ui-warning);color:var(--ui-warning)}.running-signal{width:7px;height:7px;border-radius:50%;background:var(--ui-running);box-shadow:0 0 0 4px rgba(51,120,183,.14);animation:signal-pulse 1.6s ease-in-out infinite}.flow-body{min-width:0}.flow-name{display:block;overflow:hidden;font-size:11px;font-weight:600;text-overflow:ellipsis;white-space:nowrap}.flow-meta{display:block;overflow:hidden;margin-top:3px;color:var(--ui-text-tertiary);font:10px/1.35 var(--ui-font-mono);text-overflow:ellipsis;white-space:nowrap}@keyframes signal-pulse{50%{box-shadow:0 0 0 7px rgba(51,120,183,0)}}@media(max-width:767px){.workflow-strip{padding-inline:12px}.flow-step{flex-basis:122px;min-width:122px}}@media(prefers-reduced-motion:reduce){.running-signal{animation:none}}
</style>
