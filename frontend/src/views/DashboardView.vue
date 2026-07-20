<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
const runs = ref<any[]>([])
const resources = ref<any[]>([])
const active = computed(() => runs.value.filter(r => !['completed', 'cancelled', 'execution_failed', 'parse_failed', 'precheck_failed'].includes(r.status)).length)
const completed = computed(() => runs.value.filter(r => r.status === 'completed').length)
const healthy = computed(() => resources.value.filter(r => r.health_status === 'healthy').length)
onMounted(async () => {
  ;[runs.value, resources.value] = await Promise.all([api.get('/runs').then(r => r.data), api.get('/resources').then(r => r.data)])
})
</script>
<template><div class="page"><div class="page-header"><div><h1 class="page-title">工作台</h1><p class="muted">测速任务和基础资源的实时概览</p></div><el-button type="primary" @click="$router.push('/runs')">创建测速运行</el-button></div><div class="metrics"><div class="card metric"><span class="muted">运行总数</span><div class="metric-value">{{runs.length}}</div></div><div class="card metric"><span class="muted">执行中 / 待确认</span><div class="metric-value">{{active}}</div></div><div class="card metric"><span class="muted">已完成</span><div class="metric-value">{{completed}}</div></div><div class="card metric"><span class="muted">健康资源</span><div class="metric-value">{{healthy}} / {{resources.length}}</div></div></div><div class="card recent"><div class="section-title"><h3>最近运行</h3><el-button text @click="$router.push('/runs')">查看全部</el-button></div><el-table :data="runs.slice(0,8)"><el-table-column prop="run_number" label="运行编号"/><el-table-column prop="business_code" label="业务"/><el-table-column label="场景"><template #default="s">{{s.row.config_snapshot?.scenario?.name}}</template></el-table-column><el-table-column prop="status" label="状态"><template #default="s"><el-tag effect="plain">{{s.row.status}}</el-tag></template></el-table-column><el-table-column prop="progress" label="进度"><template #default="s"><el-progress :percentage="s.row.progress"/></template></el-table-column><el-table-column width="100"><template #default="s"><el-button link type="primary" @click="$router.push(`/runs/${s.row.id}`)">详情</el-button></template></el-table-column></el-table></div></div></template>
<style scoped>.recent{margin-top:20px;padding:8px 18px 18px}.section-title{display:flex;align-items:center;justify-content:space-between}</style>
