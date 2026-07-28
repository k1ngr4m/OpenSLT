<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, errorMessage } from '@/api/client'
import { ElMessage } from 'element-plus'
import { Download, RefreshRight, Search, CopyDocument } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import type { ApiAuditLog, ApiLog } from '@/types/api'
import { formatBeijingDateTime } from '@/utils/time'

const auth = useAuthStore()
const tab = ref('logs')
const rows = ref<ApiLog[]>([])
const audit = ref<ApiAuditLog[]>([])
const loading = ref(false)
const loadError = ref('')
const filters = ref({ log_type: '', level: '', trace_id: '', keyword: '' })
const activeFilterCount = computed(() => Object.values(filters.value).filter(Boolean).length)

const logTypeText: Record<string, string> = {
  application: '应用日志',
  access: '访问日志',
  run: '运行日志',
  command: '远端命令',
  remote_command: '远端命令',
}

function levelType(level: string) {
  if (['ERROR', 'CRITICAL'].includes(level)) return 'danger'
  if (level === 'WARNING') return 'warning'
  if (level === 'INFO') return 'primary'
  return 'info'
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    rows.value = (await api.get<ApiLog[]>('/logs', { params: filters.value })).data
    if (auth.isAdmin) audit.value = (await api.get<ApiAuditLog[]>('/audit-logs')).data
  } catch (error) {
    loadError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.value = { log_type: '', level: '', trace_id: '', keyword: '' }
  load()
}

async function copy(value?: string | null) {
  if (!value) return
  await navigator.clipboard.writeText(value)
  ElMessage.success('追踪 ID 已复制')
}

async function exportAudit() {
  try {
    const response = await api.get('/audit-logs/export', { responseType: 'blob' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(response.data)
    link.download = 'audit-logs.csv'
    link.click()
    URL.revokeObjectURL(link.href)
    ElMessage.success('审计日志已导出')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

onMounted(load)
</script>

<template>
  <div class="page logs-page">
    <header class="page-header">
      <div><span class="page-kicker">可观测性</span><h1 class="page-title">日志中心</h1><p class="muted">通过 trace_id 关联 API、运行步骤和远端命令</p></div>
      <el-button :icon="RefreshRight" :loading="loading" @click="load">刷新日志</el-button>
    </header>

    <el-alert v-if="loadError" class="load-error" title="日志加载失败" :description="loadError" type="error" show-icon :closable="false" />

    <section class="card log-workspace">
      <el-tabs v-model="tab">
        <el-tab-pane label="应用与运行日志" name="logs">
          <div class="filter-bar log-filters">
            <el-select v-model="filters.log_type" clearable placeholder="全部日志类型"><el-option v-for="(label, value) in logTypeText" :key="value" :label="label" :value="value" /></el-select>
            <el-select v-model="filters.level" clearable placeholder="全部级别"><el-option v-for="level in ['DEBUG','INFO','WARNING','ERROR','CRITICAL']" :key="level" :label="level" :value="level" /></el-select>
            <el-input v-model="filters.trace_id" clearable placeholder="trace_id" class="trace-filter mono" />
            <el-input v-model="filters.keyword" clearable placeholder="搜索事件或消息" :prefix-icon="Search" class="keyword-filter" @keyup.enter="load" />
            <span class="filter-summary">{{ rows.length }} 条<span v-if="activeFilterCount">，{{ activeFilterCount }} 项筛选</span></span>
            <el-button text @click="resetFilters">重置</el-button>
            <el-button type="primary" :loading="loading" @click="load">查询</el-button>
          </div>
          <el-table :data="rows" v-loading="loading" height="calc(100dvh - 322px)" empty-text="没有符合条件的日志" class="log-table">
            <el-table-column label="北京时间" width="195"><template #default="scope"><time class="mono table-time">{{ formatBeijingDateTime(scope.row.created_at, { milliseconds: true }) }}</time></template></el-table-column>
            <el-table-column label="级别" width="95"><template #default="scope"><el-tag :type="levelType(scope.row.level)" effect="plain" size="small">{{ scope.row.level }}</el-tag></template></el-table-column>
            <el-table-column label="类型" width="105"><template #default="scope">{{ logTypeText[scope.row.log_type] || scope.row.log_type }}</template></el-table-column>
            <el-table-column prop="source" label="来源" width="110" show-overflow-tooltip />
            <el-table-column prop="event" label="事件" min-width="160" show-overflow-tooltip />
            <el-table-column prop="message" label="消息" min-width="300" show-overflow-tooltip />
            <el-table-column label="Trace ID" width="230"><template #default="scope"><div class="trace-cell"><span class="mono">{{ scope.row.trace_id || '-' }}</span><el-button v-if="scope.row.trace_id" text circle size="small" aria-label="复制 Trace ID" @click="copy(scope.row.trace_id)"><el-icon><CopyDocument /></el-icon></el-button></div></template></el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane v-if="auth.isAdmin" label="审计日志" name="audit">
          <div class="audit-note"><div><strong>不可变审计记录</strong><span>导出行为本身也会写入审计日志</span></div><el-button :icon="Download" @click="exportAudit">导出 CSV</el-button></div>
          <el-table :data="audit" v-loading="loading" height="calc(100dvh - 292px)" empty-text="暂无审计日志">
            <el-table-column label="北京时间" width="195"><template #default="scope"><time class="mono table-time">{{ formatBeijingDateTime(scope.row.created_at, { milliseconds: true }) }}</time></template></el-table-column>
            <el-table-column prop="actor_id" label="操作者" width="90" />
            <el-table-column prop="action" label="动作" min-width="150" />
            <el-table-column prop="object_type" label="对象类型" min-width="120" />
            <el-table-column prop="object_id" label="对象 ID" min-width="100" />
            <el-table-column prop="result" label="结果" width="100" />
            <el-table-column prop="trace_id" label="Trace ID" width="230" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </section>
  </div>
</template>

<style scoped>
.logs-page{max-width:1600px}.load-error{margin-bottom:14px}.log-workspace{overflow:hidden;padding:0 18px 18px}.log-workspace :deep(.el-tabs__header){margin-bottom:14px}.log-filters{margin-bottom:14px}.log-filters>.el-select{width:150px}.trace-filter{width:220px}.keyword-filter{width:min(300px,24vw)}.filter-summary{margin-left:auto;color:var(--ui-text-secondary);font-size:11px}.table-time{color:var(--ui-text-secondary);font-size:10px}.trace-cell{display:flex;align-items:center;gap:5px}.trace-cell>span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px}.trace-cell .el-button{min-height:28px;opacity:0}.trace-cell:hover .el-button,.trace-cell:focus-within .el-button{opacity:1}.audit-note{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:14px;padding:12px 14px;border:1px solid #ead9bd;border-radius:8px;background:#fcf7ed}.audit-note strong,.audit-note span{display:block}.audit-note strong{color:#6f511c;font-size:12px}.audit-note span{margin-top:3px;color:#8b744d;font-size:10px}
@media(max-width:767px){.log-workspace{padding-inline:12px}.log-filters>*{width:100%!important}.filter-summary{margin-left:0}.trace-cell .el-button{opacity:1}}
</style>
