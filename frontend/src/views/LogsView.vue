<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, errorMessage } from '@/api/client'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore()
const tab = ref('logs')
const rows = ref<any[]>([])
const audit = ref<any[]>([])
const filters = ref({ log_type: '', level: '', trace_id: '', keyword: '' })
async function load() {
  try {
    rows.value = (await api.get('/logs', { params: filters.value })).data
    if (auth.isAdmin) audit.value = (await api.get('/audit-logs')).data
  } catch (e) { ElMessage.error(errorMessage(e)) }
}
async function exportAudit() {
  try {
    const response = await api.get('/audit-logs/export', { responseType: 'blob' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(response.data)
    link.download = 'audit-logs.csv'
    link.click()
    URL.revokeObjectURL(link.href)
  } catch (e) { ElMessage.error(errorMessage(e)) }
}
onMounted(load)
</script>
<template><div class="page"><div class="page-header"><div><h1 class="page-title">日志中心</h1><p class="muted">通过 trace_id 关联 API、运行步骤和远端命令</p></div></div><div class="card panel"><el-tabs v-model="tab"><el-tab-pane label="应用与运行日志" name="logs"><div class="toolbar filters"><el-select v-model="filters.log_type" clearable placeholder="日志类型"><el-option v-for="t in ['application','access','run','command']" :key="t" :value="t"/></el-select><el-select v-model="filters.level" clearable placeholder="级别"><el-option v-for="l in ['DEBUG','INFO','WARNING','ERROR','CRITICAL']" :key="l" :value="l"/></el-select><el-input v-model="filters.trace_id" clearable placeholder="trace_id"/><el-input v-model="filters.keyword" clearable placeholder="关键词"/><el-button type="primary" @click="load">查询</el-button></div><el-table :data="rows"><el-table-column label="UTC 时间" width="190"><template #default="s">{{new Date(s.row.created_at).toISOString()}}</template></el-table-column><el-table-column prop="level" label="级别" width="90"/><el-table-column prop="log_type" label="类型" width="100"/><el-table-column prop="source" label="来源" width="100"/><el-table-column prop="event" label="事件" width="180"/><el-table-column prop="message" label="消息" show-overflow-tooltip/><el-table-column prop="trace_id" label="Trace ID" width="240" show-overflow-tooltip/></el-table></el-tab-pane><el-tab-pane v-if="auth.isAdmin" label="审计日志" name="audit"><div class="audit-action"><el-button @click="exportAudit">导出 CSV（计入审计）</el-button></div><el-table :data="audit"><el-table-column label="UTC 时间" width="190"><template #default="s">{{new Date(s.row.created_at).toISOString()}}</template></el-table-column><el-table-column prop="actor_id" label="操作者" width="90"/><el-table-column prop="action" label="动作"/><el-table-column prop="object_type" label="对象类型"/><el-table-column prop="object_id" label="对象 ID"/><el-table-column prop="result" label="结果"/><el-table-column prop="trace_id" label="Trace ID" width="240" show-overflow-tooltip/></el-table></el-tab-pane></el-tabs></div></div></template>
<style scoped>.panel{padding:0 18px 18px}.filters{margin-bottom:14px}.filters .el-input{width:220px}.audit-action{text-align:right;margin-bottom:12px}</style>
