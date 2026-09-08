<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { RunLog } from '@/types/run'
import { formatTime } from '@/utils/runDetail'

const props = defineProps<{
  logs: RunLog[]
  total: number
  scopeLabel: string
  scoped: boolean
}>()

const pageSize = 200
const page = ref(1)
const following = ref(true)
const readCount = ref(0)
const pageCount = computed(() => Math.max(1, Math.ceil(props.logs.length / pageSize)))
const currentPage = computed(() => following.value ? pageCount.value : Math.min(page.value, pageCount.value))
const visibleLogs = computed(() => props.logs.slice((currentPage.value - 1) * pageSize, currentPage.value * pageSize))
const newCount = computed(() => following.value ? 0 : Math.max(0, props.logs.length - readCount.value))
function changePage(next: number) {
  if (following.value) readCount.value = props.logs.length
  page.value = next
  following.value = next === pageCount.value
}
function showLatest() { following.value = true }
watch(() => props.scopeLabel, showLatest)

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
        <p class="muted">{{ scopeLabel }} · {{ logs.length }} / {{ total }} 条 · 每页 {{ pageSize }} 条</p>
      </div>
      <el-button size="small" @click="emit('refresh')">刷新</el-button>
    </div>
    <div class="log-filters">
      <el-button size="small" :type="scoped ? 'default' : 'primary'" plain @click="emit('showAll')">全部日志</el-button>
      <el-tag v-if="scoped" type="info" effect="plain">{{ scopeLabel }}</el-tag>
    </div>
    <div class="log-pagination">
      <el-button :disabled="currentPage === 1" size="small" @click="changePage(currentPage - 1)">上一页</el-button>
      <span aria-live="polite">{{ currentPage }} / {{ pageCount }} 页</span>
      <el-button :disabled="currentPage === pageCount" size="small" @click="changePage(currentPage + 1)">下一页</el-button>
      <el-button v-if="!following" size="small" @click="showLatest">{{ newCount ? `${newCount} 条新日志 · 查看最新` : '查看最新' }}</el-button>
    </div>
    <div class="run-log-view">
      <div v-for="log in visibleLogs" :key="log.id" class="log-line" :class="{ 'is-error': log.level === 'ERROR' }">
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
.log-pagination{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:12px;font-size:12px;color:var(--ui-text-secondary)}

.log-panel{position:sticky;top:68px;min-height:620px;padding:16px;box-shadow:none}.log-panel-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}.log-panel h2{margin:0 0 4px;font-size:15px}.log-panel p{margin:0;font-size:12px}.log-filters{display:flex;align-items:center;gap:8px;margin-bottom:10px}.run-log-view{height:548px;overflow:auto;padding:12px;border:1px solid #243640;border-radius:7px;background:var(--ui-terminal);color:#d1dde0;font:12px/1.7 "Cascadia Code","JetBrains Mono",Consolas,monospace}.log-line{padding:7px 0;border-bottom:1px solid rgba(255,255,255,.07)}.log-line:last-child{border-bottom:0}.log-line p{margin:3px 0 0;word-break:break-word}.log-line.is-error p{color:#f4b9be}.log-meta{display:flex;flex-wrap:wrap;gap:8px;color:#849da5;font-size:12px}.log-empty{padding:28px 0;color:#849da5;text-align:center}@media(max-width:1250px){.log-panel{padding:14px}.run-log-view{height:520px}}@media(max-width:1023px){.log-panel{position:static;min-height:0}.run-log-view{height:400px}}
</style>
