<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import type { ApiUser, ApiUserCreate } from '@/types/api'
import { formatBeijingDateTime } from '@/utils/time'

const rows = ref<ApiUser[]>([])
const loading = ref(false)
const dialog = ref(false)
const saving = ref(false)
const editing = ref<number | null>(null)
const keyword = ref('')
const form = reactive<ApiUserCreate & { is_active: boolean }>({ username: '', display_name: '', password: '', role: 'visitor', is_active: true })
const roleText: Record<string, string> = { admin: '管理员', tester: '测试人员', visitor: '访客' }
const roleDescription: Record<string, string> = { admin: '管理用户、资源、系统配置和全部运行', tester: '创建并执行测速任务，处理人工确认', visitor: '只读查看任务、指标和报告' }
const filteredRows = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  return query ? rows.value.filter(row => `${row.username} ${row.display_name || ''}`.toLowerCase().includes(query)) : rows.value
})
const activeCount = computed(() => rows.value.filter(row => row.is_active).length)

async function load() {
  loading.value = true
  try { rows.value = (await api.get<ApiUser[]>('/users')).data }
  catch (error) { ElMessage.error(errorMessage(error)) }
  finally { loading.value = false }
}

function open(row?: ApiUser) {
  Object.assign(form, { username: '', display_name: '', password: '', role: 'visitor', is_active: true }, row || {})
  form.password = ''
  editing.value = row?.id || null
  dialog.value = true
}

async function save() {
  if (!form.username.trim() || !form.display_name.trim()) { ElMessage.warning('请填写用户名和显示名称'); return }
  if (!editing.value && form.password.length < 8) { ElMessage.warning('新用户密码至少需要 8 个字符'); return }
  saving.value = true
  try {
    if (editing.value) {
      const data = { display_name: form.display_name, role: form.role, is_active: form.is_active, ...(form.password ? { password: form.password } : {}) }
      await api.patch(`/users/${editing.value}`, data)
    } else await api.post('/users', form)
    ElMessage.success(`用户 ${form.username} 已保存`)
    dialog.value = false
    await load()
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { saving.value = false }
}

onMounted(load)
</script>

<template>
  <div class="page users-page">
    <header class="page-header"><div><span class="page-kicker">权限管理</span><h1 class="page-title">用户管理</h1><p class="muted">管理员创建账号并分配最小必要权限</p></div><el-button type="primary" :icon="Plus" @click="open()">新增用户</el-button></header>
    <section class="user-summary" aria-label="用户概览"><div><span>用户总数</span><strong>{{ rows.length }}</strong></div><div><span>已启用</span><strong class="success">{{ activeCount }}</strong></div><div><span>已停用</span><strong>{{ rows.length-activeCount }}</strong></div></section>
    <div class="filter-bar"><el-input v-model="keyword" clearable :prefix-icon="Search" placeholder="搜索用户名或显示名称" class="keyword-filter" /><span class="filter-count">{{ filteredRows.length }} 条</span></div>
    <section class="card table-panel"><el-table v-loading="loading" :data="filteredRows" empty-text="没有符合条件的用户"><el-table-column label="用户" min-width="190"><template #default="scope"><div class="user-cell"><span class="avatar">{{ (scope.row.display_name||scope.row.username).slice(0,1).toUpperCase() }}</span><span><strong>{{ scope.row.display_name||'-' }}</strong><small class="mono">{{ scope.row.username }}</small></span></div></template></el-table-column><el-table-column label="角色" width="130"><template #default="scope"><el-tag effect="plain">{{ roleText[scope.row.role] }}</el-tag></template></el-table-column><el-table-column label="权限说明" min-width="260"><template #default="scope"><span class="role-description">{{ roleDescription[scope.row.role] }}</span></template></el-table-column><el-table-column label="状态" width="100"><template #default="scope"><el-tag :type="scope.row.is_active?'success':'info'" effect="plain">{{ scope.row.is_active?'已启用':'已停用' }}</el-tag></template></el-table-column><el-table-column label="最后登录" width="195"><template #default="scope"><span class="table-time">{{ scope.row.last_login_at?formatBeijingDateTime(scope.row.last_login_at):'从未登录' }}</span></template></el-table-column><el-table-column label="操作" width="90" fixed="right"><template #default="scope"><el-button link type="primary" @click="open(scope.row)">编辑</el-button></template></el-table-column></el-table></section>

    <el-dialog v-model="dialog" :title="editing?'编辑用户':'新增用户'" width="520px"><el-form label-position="top"><el-form-item label="用户名" required><el-input v-model="form.username" :disabled="!!editing" placeholder="用于登录，不可重复" /></el-form-item><el-form-item label="显示名称" required><el-input v-model="form.display_name" placeholder="界面中显示的姓名" /></el-form-item><el-form-item :label="editing?'重置密码':'初始密码'" :required="!editing"><el-input v-model="form.password" type="password" show-password :placeholder="editing?'留空表示不修改':'至少 8 个字符'" /><p class="field-help">{{ editing?'仅在需要重置时填写新密码':'用户首次登录后应立即修改初始密码' }}</p></el-form-item><el-form-item label="角色" required><el-select v-model="form.role" style="width:100%"><el-option v-for="(label,value) in roleText" :key="value" :label="label" :value="value"><div class="role-option"><strong>{{ label }}</strong><span>{{ roleDescription[value] }}</span></div></el-option></el-select><p class="field-help">{{ roleDescription[form.role] }}</p></el-form-item><el-form-item v-if="editing" label="账号状态"><el-switch v-model="form.is_active" active-text="启用" inactive-text="停用" /></el-form-item></el-form><template #footer><el-button @click="dialog=false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存用户</el-button></template></el-dialog>
  </div>
</template>

<style scoped>
.users-page{max-width:1600px}.user-summary{display:flex;overflow:hidden;width:max-content;margin-bottom:14px;border:1px solid var(--ui-border);border-radius:8px;background:var(--ui-surface)}.user-summary>div{min-width:130px;padding:12px 16px}.user-summary>div+div{border-left:1px solid var(--ui-border)}.user-summary span,.user-summary strong{display:block}.user-summary span{color:var(--ui-text-secondary);font-size:10px}.user-summary strong{margin-top:4px;font:650 20px/1.2 "Cascadia Code",Consolas,monospace}.keyword-filter{width:320px}.filter-count{margin-left:auto;color:var(--ui-text-secondary);font-size:11px}.table-panel{overflow:hidden}.user-cell{display:flex;align-items:center;gap:10px}.avatar{display:grid;flex:0 0 auto;width:32px;height:32px;place-items:center;border-radius:7px;color:var(--ui-primary-hover);background:var(--ui-primary-soft);font-size:12px;font-weight:700}.user-cell strong,.user-cell small{display:block}.user-cell strong{font-size:13px}.user-cell small{margin-top:2px;color:var(--ui-text-secondary);font-size:10px}.role-description,.table-time{color:var(--ui-text-secondary);font-size:11px}.field-help{margin:5px 0 0;color:var(--ui-text-secondary);font-size:11px;line-height:1.5}.role-option{display:grid;padding:4px 0}.role-option strong{font-size:12px}.role-option span{color:var(--ui-text-secondary);font-size:10px}@media(max-width:767px){.user-summary{width:100%}.user-summary>div{min-width:0;flex:1}.keyword-filter{width:100%}}
</style>
