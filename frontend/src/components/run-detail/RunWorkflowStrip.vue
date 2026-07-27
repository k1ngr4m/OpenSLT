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
        <strong>流程条</strong>
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
.workflow-strip{padding:18px 20px;margin-top:16px}.workflow-strip-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}.workflow-strip-head strong{font-size:16px;margin-right:10px}.workflow-scroller{display:flex;gap:10px;overflow-x:auto;padding:4px 2px 8px;scrollbar-width:thin}.flow-step{position:relative;display:flex;align-items:center;gap:10px;min-width:218px;max-width:260px;padding:12px 16px;border:1px solid #dfe7ef;border-radius:8px;background:#fff;color:#263445;text-align:left;cursor:pointer;transition:background .2s,border-color .2s,box-shadow .2s,transform .2s}.flow-step:hover{transform:translateY(-1px);border-color:#9fc8ff;box-shadow:0 8px 20px rgba(44,92,145,.08)}.flow-step:active{transform:translateY(0)}.flow-step:focus-visible{outline:2px solid #409eff;outline-offset:2px}.flow-step:after{content:'';position:absolute;right:-11px;top:50%;width:10px;height:1px;background:#cbd6e2}.flow-step:last-child:after{display:none}.flow-step.is-selected{border-color:#409eff;background:#f3f8ff;box-shadow:0 0 0 2px rgba(64,158,255,.12)}.flow-step.is-current .flow-name:after{content:'当前';margin-left:8px;color:#409eff;font-size:11px;font-weight:600}.flow-step.is-danger{border-color:#ffc3c3;background:#fff7f7}.flow-step.is-success .flow-index{background:#e7f8ef;color:#24935a}.flow-step.is-danger .flow-index{background:#ffe6e6;color:#cf2f2f}.flow-step.is-running .flow-index,.flow-step.is-waiting .flow-index{background:#fff4dd;color:#b36b00}.flow-index{display:grid;place-items:center;flex:none;width:32px;height:32px;border-radius:8px;background:#eef5ff;color:#347fcf;font-weight:700}.flow-body{min-width:0}.flow-name{display:block;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.flow-meta{display:block;margin-top:5px;color:#7b8794;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}@media(max-width:1250px){.flow-step{min-width:200px}}
</style>
