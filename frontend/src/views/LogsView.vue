<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, errorMessage } from '@/api/client'
import { ElMessage } from 'element-plus'
import { CopyDocument, Download, RefreshRight, Search, View } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import type { ApiAuditLog, ApiLogDetail, ApiLogSearchPage, ApiLogSummary } from '@/types/api'
import { formatBeijingDateTime } from '@/utils/time'

const auth = useAuthStore()
const tab = ref('application')
const rows = ref<ApiLogSummary[]>([])
const audit = ref<ApiAuditLog[]>([])
const detail = ref<ApiLogDetail | null>(null)
const detailOpen = ref(false)
const detailLoading = ref(false)
const loading = ref(false)
const loadError = ref('')
const page = ref(1)
const pageSize = ref(50)
const total = ref(0)
const timeRange = ref<[Date, Date] | null>(null)
const filters = ref({
  level: '',
  trace_id: '',
  keyword: '',
  result: '',
  http_method: '',
  http_path: '',
  http_status: undefined as number | undefined,
  database_scope: '',
  sql_fingerprint: '',
  min_duration_ms: undefined as number | undefined,
})

const tabs = computed(() => {
  const items = [{ name: 'application', label: '应用与运行' }]
  if (auth.user?.role !== 'visitor') {
    items.push(
      { name: 'access', label: 'HTTP' },
      { name: 'sql', label: 'SQL' },
      { name: 'websocket', label: 'WebSocket' },
    )
  }
  if (auth.isAdmin) items.push({ name: 'audit', label: '审计日志' })
  return items
})
const activeFilterCount = computed(() => {
  const values = Object.values(filters.value).filter(value => value !== '' && value !== undefined)
  return values.length + (timeRange.value ? 1 : 0)
})

function levelType(level: string) {
  if (['ERROR', 'CRITICAL'].includes(level)) return 'danger'
  if (level === 'WARNING') return 'warning'
  if (level === 'INFO') return 'primary'
  return 'info'
}

function resultType(result?: string | null) {
  if (result === 'success' || result === 'accepted') return 'success'
  if (result === 'failed' || result === 'rejected' || result === 'disconnected') return 'danger'
  return 'info'
}

function queryParams() {
  return {
    group: tab.value,
    ...filters.value,
    time_from: timeRange.value?.[0]?.toISOString(),
    time_to: timeRange.value?.[1]?.toISOString(),
    page: page.value,
    page_size: pageSize.value,
  }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    if (tab.value === 'audit') {
      audit.value = (await api.get<ApiAuditLog[]>('/audit-logs')).data
      return
    }
    const response = (await api.get<ApiLogSearchPage>('/logs/search', { params: queryParams() })).data
    rows.value = response.items
    total.value = response.total
  } catch (error) {
    loadError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.value = {
    level: '', trace_id: '', keyword: '', result: '', http_method: '', http_path: '',
    http_status: undefined, database_scope: '', sql_fingerprint: '', min_duration_ms: undefined,
  }
  timeRange.value = null
  page.value = 1
  load()
}

async function copy(value?: string | null) {
  if (!value) return
  await navigator.clipboard.writeText(value)
  ElMessage.success('已复制')
}

async function showDetail(row: ApiLogSummary) {
  if (!auth.isAdmin || !row.event_id) return
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = (await api.get<ApiLogDetail>(`/logs/${row.event_id}`)).data
  } catch (error) {
    ElMessage.error(errorMessage(error))
    detailOpen.value = false
  } finally {
    detailLoading.value = false
  }
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

function pretty(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2)
}

watch(tab, () => {
  page.value = 1
  load()
})
watch([page, pageSize], load)
onMounted(load)
</script>

<template>
  <div class="page logs-page">
    <header class="page-header">
      <div><span class="page-kicker">可观测性</span><h1 class="page-title">日志中心</h1></div>
      <el-button :icon="RefreshRight" :loading="loading" @click="load">刷新</el-button>
    </header>

    <el-alert v-if="loadError" class="load-error" title="日志加载失败" :description="loadError" type="error" show-icon :closable="false" />

    <section class="card log-workspace">
      <el-tabs v-model="tab">
        <el-tab-pane v-for="item in tabs" :key="item.name" :label="item.label" :name="item.name" />
      </el-tabs>

      <template v-if="tab !== 'audit'">
        <div class="filter-bar log-filters">
          <el-select v-model="filters.level" clearable placeholder="级别">
            <el-option v-for="level in ['DEBUG','INFO','WARNING','ERROR','CRITICAL']" :key="level" :label="level" :value="level" />
          </el-select>
          <el-select v-model="filters.result" clearable placeholder="结果">
            <el-option v-for="value in ['success','failed','accepted','rejected','disconnected']" :key="value" :label="value" :value="value" />
          </el-select>
          <el-input v-model="filters.trace_id" clearable placeholder="Trace ID" class="trace-filter mono" />
          <el-input v-model="filters.keyword" clearable placeholder="事件或消息" :prefix-icon="Search" class="keyword-filter" @keyup.enter="load" />
          <el-date-picker v-model="timeRange" type="datetimerange" range-separator="至" start-placeholder="开始时间" end-placeholder="结束时间" />
          <el-input-number v-model="filters.min_duration_ms" :min="0" :controls="false" placeholder="最小耗时 ms" />
        </div>
        <div v-if="tab === 'access'" class="filter-bar secondary-filters">
          <el-select v-model="filters.http_method" clearable placeholder="方法"><el-option v-for="method in ['GET','POST','PUT','PATCH','DELETE']" :key="method" :label="method" :value="method" /></el-select>
          <el-input v-model="filters.http_path" clearable placeholder="路径" />
          <el-input-number v-model="filters.http_status" :min="100" :max="599" :controls="false" placeholder="状态码" />
        </div>
        <div v-if="tab === 'sql'" class="filter-bar secondary-filters">
          <el-select v-model="filters.database_scope" clearable placeholder="数据库范围"><el-option label="平台数据库" value="platform" /><el-option label="资源数据库" value="resource" /></el-select>
          <el-input v-model="filters.sql_fingerprint" clearable placeholder="SQL 指纹" class="fingerprint-filter mono" />
        </div>
        <div class="filter-actions">
          <span>{{ total }} 条<span v-if="activeFilterCount">，{{ activeFilterCount }} 项筛选</span></span>
          <el-button text @click="resetFilters">重置</el-button>
          <el-button type="primary" :loading="loading" @click="page = 1; load()">查询</el-button>
        </div>

        <el-table :data="rows" v-loading="loading" height="calc(100dvh - 390px)" empty-text="没有符合条件的日志" class="log-table">
          <el-table-column label="北京时间" width="195"><template #default="scope"><time class="mono table-time">{{ formatBeijingDateTime(scope.row.created_at, { milliseconds: true }) }}</time></template></el-table-column>
          <el-table-column label="级别" width="95"><template #default="scope"><el-tag :type="levelType(scope.row.level)" effect="plain" size="small">{{ scope.row.level }}</el-tag></template></el-table-column>
          <el-table-column v-if="tab === 'access'" prop="http_method" label="方法" width="82" />
          <el-table-column v-if="tab === 'access'" label="状态" width="82"><template #default="scope">{{ scope.row.http_status || '-' }}</template></el-table-column>
          <el-table-column v-if="tab === 'sql'" prop="database_scope" label="范围" width="95" />
          <el-table-column prop="event" label="事件" min-width="145" show-overflow-tooltip />
          <el-table-column prop="message" label="消息" min-width="300" show-overflow-tooltip />
          <el-table-column label="耗时" width="90"><template #default="scope"><span class="mono">{{ scope.row.duration_ms == null ? '-' : `${scope.row.duration_ms} ms` }}</span></template></el-table-column>
          <el-table-column label="结果" width="105"><template #default="scope"><el-tag v-if="scope.row.result" :type="resultType(scope.row.result)" effect="plain" size="small">{{ scope.row.result }}</el-tag><span v-else>-</span></template></el-table-column>
          <el-table-column label="Trace ID" width="220"><template #default="scope"><div class="trace-cell"><span class="mono">{{ scope.row.trace_id || '-' }}</span><el-button v-if="scope.row.trace_id" text circle size="small" aria-label="复制 Trace ID" @click="copy(scope.row.trace_id)"><el-icon><CopyDocument /></el-icon></el-button></div></template></el-table-column>
          <el-table-column v-if="auth.isAdmin" label="详情" width="72" fixed="right"><template #default="scope"><el-button text circle :icon="View" aria-label="查看日志详情" :disabled="!scope.row.event_id" @click="showDetail(scope.row)" /></template></el-table-column>
        </el-table>
        <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[20,50,100,200]" layout="total, sizes, prev, pager, next" />
      </template>

      <template v-else>
        <div class="audit-actions"><span>不可变审计记录</span><el-button :icon="Download" @click="exportAudit">导出 CSV</el-button></div>
        <el-table :data="audit" v-loading="loading" height="calc(100dvh - 292px)" empty-text="暂无审计日志">
          <el-table-column label="北京时间" width="195"><template #default="scope"><time class="mono table-time">{{ formatBeijingDateTime(scope.row.created_at, { milliseconds: true }) }}</time></template></el-table-column>
          <el-table-column prop="actor_id" label="操作者" width="90" /><el-table-column prop="action" label="动作" min-width="150" /><el-table-column prop="object_type" label="对象类型" min-width="120" /><el-table-column prop="object_id" label="对象 ID" min-width="100" /><el-table-column prop="result" label="结果" width="100" /><el-table-column prop="trace_id" label="Trace ID" width="230" show-overflow-tooltip />
        </el-table>
      </template>
    </section>

    <el-drawer v-model="detailOpen" title="日志详情" size="min(760px, 92vw)">
      <div v-loading="detailLoading" class="detail-content">
        <template v-if="detail">
          <dl class="detail-meta"><dt>事件</dt><dd>{{ detail.summary.event }}</dd><dt>Trace ID</dt><dd class="mono">{{ detail.summary.trace_id }}</dd><dt>时间</dt><dd>{{ formatBeijingDateTime(detail.summary.created_at, { milliseconds: true }) }}</dd><dt>耗时</dt><dd>{{ detail.summary.duration_ms ?? '-' }} ms</dd></dl>
          <template v-if="detail.payload.category === 'http'">
            <h3>Request</h3><pre>{{ pretty(detail.payload.request) }}</pre><h3>Response</h3><pre>{{ pretty(detail.payload.response) }}</pre>
          </template>
          <template v-else-if="detail.payload.category === 'sql'">
            <h3>SQL</h3><pre>{{ detail.payload.statement_template }}</pre><h3>参数</h3><pre>{{ pretty(detail.payload.parameters) }}</pre><h3 v-if="detail.payload.error_type">异常</h3><pre v-if="detail.payload.error_type">{{ pretty({ type: detail.payload.error_type, message: detail.payload.error_message }) }}</pre>
          </template>
          <template v-else><pre>{{ pretty(detail.payload) }}</pre></template>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.logs-page{max-width:1680px}.load-error{margin-bottom:14px}.log-workspace{overflow:hidden;padding:0 18px 18px}.log-workspace :deep(.el-tabs__header){margin-bottom:14px}.log-filters,.secondary-filters{margin-bottom:10px}.log-filters>.el-select,.secondary-filters>.el-select{width:145px}.trace-filter{width:220px}.keyword-filter{width:min(280px,22vw)}.secondary-filters>.el-input{width:260px}.fingerprint-filter{width:360px!important}.filter-actions,.audit-actions{display:flex;align-items:center;justify-content:flex-end;gap:10px;margin-bottom:12px;color:var(--ui-text-secondary);font-size:11px}.audit-actions{justify-content:space-between}.table-time{color:var(--ui-text-secondary);font-size:10px}.trace-cell{display:flex;align-items:center;gap:5px}.trace-cell>span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px}.trace-cell .el-button{min-height:28px;opacity:0}.trace-cell:hover .el-button,.trace-cell:focus-within .el-button{opacity:1}.el-pagination{justify-content:flex-end;margin-top:12px}.detail-content{min-height:180px}.detail-meta{display:grid;grid-template-columns:90px 1fr;gap:8px 12px;margin:0 0 24px}.detail-meta dt{color:var(--ui-text-tertiary)}.detail-meta dd{min-width:0;margin:0;overflow-wrap:anywhere}.detail-content h3{margin:22px 0 8px;font-size:13px}.detail-content pre{max-height:360px;margin:0;overflow:auto;padding:14px;border:1px solid var(--ui-border);border-radius:6px;background:var(--ui-terminal);color:#d9e8e6;font:11px/1.6 var(--ui-font-mono);white-space:pre-wrap;overflow-wrap:anywhere}
@media(max-width:767px){.log-workspace{padding-inline:12px}.log-filters>*,.secondary-filters>*{width:100%!important}.filter-actions{flex-wrap:wrap}.trace-cell .el-button{opacity:1}.detail-meta{grid-template-columns:1fr}.detail-meta dt{margin-top:6px}}
</style>
