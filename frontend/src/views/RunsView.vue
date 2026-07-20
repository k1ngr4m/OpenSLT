<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { businessText, statusText, statusType, resourceText } from '@/utils/status'
const auth = useAuthStore()
const runs = ref<any[]>([])
const plans = ref<any[]>([])
const scenarios = ref<any[]>([])
const resources = ref<any[]>([])
const dialog = ref(false)
const form = reactive({ plan_id: 0, scenario_id: 0, resource_ids: [] as number[], timeout_minutes: 120 })
const selectedPlan = computed(() => plans.value.find(p => p.id === form.plan_id))
const availableScenarios = computed(() => scenarios.value.filter(s => s.plan_id === form.plan_id))
const availableResources = computed(() => resources.value.filter(r => r.business_code === selectedPlan.value?.business_code && r.is_enabled))
async function load() { ;[runs.value, plans.value, scenarios.value, resources.value] = await Promise.all([api.get('/runs').then(r => r.data), api.get('/plans').then(r => r.data), api.get('/scenarios').then(r => r.data), api.get('/resources').then(r => r.data)]) }
function open() { form.plan_id = plans.value[0]?.id || 0; form.scenario_id = 0; form.resource_ids = []; dialog.value = true }
async function create() { try { const { data } = await api.post('/runs', form); dialog.value = false; ElMessage.success('运行已创建'); location.href = `/runs/${data.id}` } catch (e) { ElMessage.error(errorMessage(e)) } }
onMounted(load)
</script>
<template><div class="page"><div class="page-header"><div><h1 class="page-title">测速运行</h1><p class="muted">创建、排队并跟踪每一次独立测速执行</p></div><el-button v-if="auth.canOperate" type="primary" @click="open">创建运行</el-button></div><div class="card"><el-table :data="runs" @row-click="row=>$router.push(`/runs/${row.id}`)" class="clickable"><el-table-column prop="run_number" label="运行编号" min-width="190"/><el-table-column label="业务" min-width="130"><template #default="s">{{businessText[s.row.business_code]}}</template></el-table-column><el-table-column label="方案 / 场景" min-width="200"><template #default="s"><strong>{{s.row.config_snapshot?.plan?.name}}</strong><br/><small class="muted">{{s.row.config_snapshot?.scenario?.name}}</small></template></el-table-column><el-table-column label="状态" width="140"><template #default="s"><el-tag :type="statusType(s.row.status)" effect="plain">{{statusText[s.row.status]||s.row.status}}</el-tag></template></el-table-column><el-table-column label="进度" width="170"><template #default="s"><el-progress :percentage="s.row.progress"/></template></el-table-column><el-table-column label="创建时间" width="180"><template #default="s">{{new Date(s.row.created_at).toLocaleString()}}</template></el-table-column></el-table></div><el-dialog v-model="dialog" title="创建测速运行" width="620px"><el-form label-width="100px"><el-form-item label="测速方案"><el-select v-model="form.plan_id" style="width:100%" @change="form.scenario_id=0;form.resource_ids=[]"><el-option v-for="p in plans.filter(p=>p.is_enabled)" :key="p.id" :label="`${p.name} · ${businessText[p.business_code]}`" :value="p.id"/></el-select></el-form-item><el-form-item label="测速场景"><el-select v-model="form.scenario_id" style="width:100%"><el-option v-for="s in availableScenarios.filter(s=>s.is_enabled)" :key="s.id" :label="`${s.name} · v${s.config_version}`" :value="s.id"/></el-select></el-form-item><el-form-item label="执行资源"><el-select v-model="form.resource_ids" multiple style="width:100%"><el-option v-for="r in availableResources" :key="r.id" :label="`${r.name} · ${resourceText[r.resource_type]}`" :value="r.id"/></el-select></el-form-item><el-form-item label="超时时间"><el-input-number v-model="form.timeout_minutes" :min="5" :max="1440"/> 分钟</el-form-item></el-form><template #footer><el-button @click="dialog=false">取消</el-button><el-button type="primary" :disabled="!form.scenario_id||!form.resource_ids.length" @click="create">创建</el-button></template></el-dialog></div></template>
<style scoped>.clickable :deep(.el-table__row){cursor:pointer}</style>
