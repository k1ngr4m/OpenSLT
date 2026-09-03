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
.log-panel{position:sticky;top:64px;min-height:620px;padding:0;overflow:hidden;box-shadow:none}.log-panel-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:14px 14px 10px;margin:0}.log-panel h2{margin:0 0 4px;font-size:16px}.log-panel p{margin:0;font-size:11px}.log-filters{display:flex;align-items:center;gap:8px;padding:8px 12px;margin:0;background:var(--ui-terminal-toolbar)}.run-log-view{height:548px;overflow:auto;padding:10px 12px;border:0;border-radius:0;background:var(--ui-terminal);color:var(--ui-terminal-text);font:12px/1.65 var(--ui-font-mono)}.log-line{padding:7px 0;border-bottom:1px solid rgba(215,226,229,.07)}.log-line:last-child{border-bottom:0}.log-line p{margin:3px 0 0;word-break:break-word}.log-line.is-error p{color:#e56a76}.log-meta{display:flex;flex-wrap:wrap;gap:8px;color:var(--ui-terminal-muted);font-size:10px}.log-meta span:nth-child(2){color:#62a6d8}.log-empty{padding:28px 0;color:var(--ui-terminal-muted);text-align:center}@media(max-width:1250px){.run-log-view{height:520px}}@media(max-width:1023px){.log-panel{position:static;min-height:0}.run-log-view{height:400px}}
</style>
