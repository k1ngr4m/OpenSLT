<script setup lang="ts">
import type { RunLog } from '@/types/run'
import { formatTime } from '@/utils/runDetail'

defineProps<{
  logs: RunLog[]
  total: number
  scopeLabel: string
  scoped: boolean
}>()

const emit = defineEmits<{
  refresh: []
  showAll: []
}>()
</script>

<template>
  <aside class="card log-panel" aria-label="运行日志">
    <div class="log-panel-head">
      <div>
        <h2>运行日志</h2>
        <p class="muted">{{ scopeLabel }} · {{ logs.length }} / {{ total }} 条</p>
      </div>
      <el-button size="small" @click="emit('refresh')">刷新</el-button>
    </div>
    <div class="log-filters">
      <el-button size="small" :type="scoped ? 'default' : 'primary'" plain @click="emit('showAll')">全部日志</el-button>
      <el-tag v-if="scoped" type="info" effect="plain">{{ scopeLabel }}</el-tag>
    </div>
    <div class="run-log-view">
      <div v-for="log in logs" :key="log.id" class="log-line" :class="{ 'is-error': log.level === 'ERROR' }">
        <div class="log-meta">
          <span class="mono">{{ formatTime(log.created_at) }}</span>
          <span>{{ log.source }}</span>
          <span>{{ log.event }}</span>
        </div>
        <p><span class="mono">[{{ log.level }}]</span> {{ log.message }}</p>
      </div>
      <div v-if="!logs.length" class="log-empty">暂无日志</div>
    </div>
  </aside>
</template>

<style scoped>
.log-panel{position:sticky;top:16px;padding:18px;min-height:620px}.log-panel-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}.log-panel h2{margin:0 0 6px;font-size:18px}.log-panel p{margin:0}.log-filters{display:flex;align-items:center;gap:8px;margin-bottom:12px}.run-log-view{height:540px;overflow:auto;padding:12px;border-radius:8px;background:#111827;color:#d1d5db;font:12px/1.65 "Cascadia Code",Consolas,monospace}.log-line{padding:8px 0;border-bottom:1px solid rgba(255,255,255,.08)}.log-line:last-child{border-bottom:0}.log-line p{margin:4px 0 0;word-break:break-word}.log-line.is-error p{color:#fecaca}.log-meta{display:flex;gap:8px;flex-wrap:wrap;color:#8fa3b8;font-size:11px}.log-empty{padding:24px 0;text-align:center;color:#8fa3b8}@media(max-width:1250px){.log-panel{padding:16px}.run-log-view{height:520px}}
</style>
