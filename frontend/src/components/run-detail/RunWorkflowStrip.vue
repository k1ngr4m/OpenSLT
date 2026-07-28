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
        <span class="flow-index mono">{{ String(step.position).padStart(2, '0') }}</span>
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
.workflow-strip{padding:16px 18px;margin-top:16px;box-shadow:none}.workflow-strip-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}.workflow-strip-head strong{margin-right:10px;font-size:15px}.workflow-strip-head .muted{font-size:11px}.workflow-scroller{display:flex;gap:9px;overflow-x:auto;padding:3px 2px 7px;scrollbar-width:thin}.flow-step{position:relative;display:flex;align-items:center;gap:10px;min-width:210px;max-width:250px;padding:11px 13px;border:1px solid var(--ui-border);border-radius:7px;background:#fff;color:var(--ui-text-primary);text-align:left;cursor:pointer;transition:background-color var(--ui-transition),border-color var(--ui-transition),transform var(--ui-transition),box-shadow var(--ui-transition)}.flow-step:hover{border-color:var(--ui-border-strong);background:#f8fbfb;transform:translateY(-1px)}.flow-step:active{transform:translateY(0)}.flow-step:focus-visible{outline:2px solid var(--ui-primary);outline-offset:2px}.flow-step:after{position:absolute;top:50%;right:-10px;width:9px;height:1px;background:var(--ui-border-strong);content:''}.flow-step:last-child:after{display:none}.flow-step.is-selected{border-color:var(--ui-primary);background:#f1f8f6;box-shadow:0 0 0 2px rgba(14,128,111,.1)}.flow-step.is-current .flow-name:after{margin-left:7px;color:var(--ui-primary);font-size:10px;font-weight:600;content:'当前'}.flow-step.is-danger{border-color:#e7b7bc;background:#fff8f8}.flow-step.is-success .flow-index{background:#dff1eb;color:var(--ui-success)}.flow-step.is-danger .flow-index{background:#f8e2e4;color:var(--ui-danger)}.flow-step.is-running .flow-index{background:#e3edf6;color:var(--ui-info)}.flow-step.is-waiting .flow-index{background:#f8ead5;color:var(--ui-warning)}.flow-index{display:grid;flex:none;width:30px;height:30px;place-items:center;border-radius:6px;background:var(--ui-surface-subtle);color:var(--ui-text-secondary);font-size:11px;font-weight:700}.flow-body{min-width:0}.flow-name{display:block;overflow:hidden;font-size:12px;font-weight:650;text-overflow:ellipsis;white-space:nowrap}.flow-meta{display:block;overflow:hidden;margin-top:4px;color:var(--ui-text-tertiary);font-size:10px;text-overflow:ellipsis;white-space:nowrap}@media(max-width:1250px){.flow-step{min-width:194px}}@media(max-width:767px){.workflow-strip{padding-inline:14px}.flow-step{min-width:180px}}
</style>
