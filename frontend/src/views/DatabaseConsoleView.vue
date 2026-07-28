<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Download, Search, WarningFilled } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'

const route = useRoute()
const router = useRouter()
const resource = ref<any>(null)
const databaseName = ref('')
const sql = ref('SELECT 1 AS result')
const loading = ref(false)
const result = ref<any>(null)

const resourceId = computed(() => Number(route.params.id))
const isUpdate = computed(() => /^\s*update\b/i.test(sql.value))

async function load() {
  try {
    const { data } = await api.get('/resources')
    resource.value = data.find((item: any) => item.id === resourceId.value && item.resource_type === 'database')
    if (!resource.value) {
      ElMessage.error('数据库资源不存在')
      router.replace('/resources')
      return
    }
    databaseName.value = resource.value.database_names?.[0] || ''
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function runSelect() {
  if (!/^\s*select\b/i.test(sql.value)) {
    ElMessage.warning('查询操作台只接受 SELECT；UPDATE 请使用预览更新')
    return
  }
  loading.value = true
  try {
    const { data } = await api.post(`/resources/${resourceId.value}/database/select`, {
      database_name: databaseName.value,
      sql: sql.value,
    })
    result.value = data
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function previewUpdate() {
  if (!isUpdate.value) {
    ElMessage.warning('请输入单条 UPDATE 语句')
    return
  }
  try {
    await ElMessageBox.confirm(
      '即将分析 UPDATE 的目标表和影响行数。该步骤不会修改数据。',
      '第一次确认',
      { type: 'warning', confirmButtonText: '生成更新预览' },
    )
  } catch { return }

  loading.value = true
  try {
    const { data: preview } = await api.post(`/resources/${resourceId.value}/database/update-preview`, {
      database_name: databaseName.value,
      sql: sql.value,
    })
    const { value } = await ElMessageBox.prompt(
      `目标：${preview.database_name}.${preview.table_name}，预计影响 ${preview.estimated_rows} 行。真实执行将提交数据库事务。\n请输入资源名称“${resource.value.name}”完成第二次确认。`,
      '第二次确认',
      {
        type: 'warning',
        confirmButtonText: '确认执行 UPDATE',
        inputPlaceholder: resource.value.name,
        inputValidator: input => input === resource.value.name || '资源名称不匹配',
      },
    )
    const { data } = await api.post(`/resources/${resourceId.value}/database/update-execute`, {
      database_name: databaseName.value,
      sql: sql.value,
      confirmation_id: preview.confirmation_id,
      confirmation_text: value,
    })
    result.value = null
    ElMessage.success(`UPDATE 已完成，影响 ${data.affected_rows} 行`)
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function exportData(format: 'csv' | 'xlsx') {
  if (!/^\s*select\b/i.test(sql.value)) {
    ElMessage.warning('只能导出 SELECT 查询结果')
    return
  }
  loading.value = true
  try {
    const response = await api.post(
      `/resources/${resourceId.value}/database/export`,
      { database_name: databaseName.value, sql: sql.value, format },
      { responseType: 'blob' },
    )
    const url = URL.createObjectURL(response.data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `database-${resourceId.value}-${databaseName.value}.${format}`
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-if="resource" class="page database-console">
    <div class="page-header">
      <div class="console-title">
        <el-button :icon="ArrowLeft" circle plain aria-label="返回资源管理" @click="router.push('/resources')" />
        <div>
          <span class="page-kicker">数据库操作台</span>
          <h1 class="page-title">{{ resource.name }}</h1>
          <p class="muted mono">{{ resource.database_username }}@{{ resource.database_host }}:{{ resource.database_port }}</p>
        </div>
      </div>
    </div>

    <div class="console-toolbar card">
      <span class="toolbar-label">目标数据库</span>
      <el-select v-model="databaseName" aria-label="选择数据库" style="width:220px">
        <el-option v-for="name in resource.database_names" :key="name" :label="name" :value="name" />
      </el-select>
      <el-button type="primary" :icon="Search" :loading="loading" @click="runSelect">执行查询</el-button>
      <el-button type="danger" plain :icon="WarningFilled" :loading="loading" @click="previewUpdate">预览更新</el-button>
      <span class="toolbar-spacer" />
      <el-button :icon="Download" :disabled="loading" @click="exportData('csv')">导出 CSV</el-button>
      <el-button :icon="Download" :disabled="loading" @click="exportData('xlsx')">导出 XLSX</el-button>
    </div>

    <section class="editor-panel card" :class="{ 'is-update': isUpdate }">
      <div class="editor-heading"><div><strong>SQL 编辑器</strong><span>{{ isUpdate ? '更新语句必须先生成影响预览并完成两次确认' : '查询操作台仅执行 SELECT 语句' }}</span></div><el-tag :type="isUpdate ? 'warning' : 'success'" effect="plain">{{ isUpdate ? 'UPDATE 风险模式' : '只读查询模式' }}</el-tag></div>
      <el-input v-model="sql" type="textarea" :rows="9" resize="vertical" class="sql-editor" spellcheck="false" />
    </section>

    <div v-if="result" class="card result-panel">
      <div class="result-meta">
        <strong>查询结果</strong>
        <span class="muted">{{ result.row_count }} 行 · {{ result.elapsed_ms }} ms<span v-if="result.truncated"> · 仅显示前 500 行</span></span>
      </div>
      <el-table :data="result.rows" height="430">
        <el-table-column v-for="column in result.columns" :key="column" :prop="column" :label="column" min-width="150" show-overflow-tooltip />
      </el-table>
    </div>
    <div v-else class="card result-empty"><div><strong>等待执行查询</strong><span>查询结果、行数和耗时会显示在这里。</span></div></div>
  </div>
</template>

<style scoped>
.database-console{max-width:1600px}.console-title{display:flex;align-items:center;gap:14px}.console-title .page-kicker{margin-bottom:0}.console-toolbar{display:flex;align-items:center;gap:10px;margin-bottom:14px;padding:11px 12px}.toolbar-label{color:var(--ui-text-secondary);font-size:11px;font-weight:600}.toolbar-spacer{flex:1}.editor-panel{overflow:hidden}.editor-panel.is-update{border-color:#dfc293}.editor-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:13px 15px;border-bottom:1px solid var(--ui-border)}.editor-heading strong,.editor-heading span{display:block}.editor-heading strong{font-size:13px}.editor-heading span{margin-top:3px;color:var(--ui-text-secondary);font-size:10px}.sql-editor{font-family:"Cascadia Code","JetBrains Mono",Consolas,monospace}.sql-editor :deep(.el-textarea__inner){border:0;border-radius:0;background:var(--ui-terminal);color:#d8e4e6;font:12px/1.7 "Cascadia Code","JetBrains Mono",Consolas,monospace;box-shadow:none}.sql-editor :deep(.el-textarea__inner:focus){box-shadow:inset 0 0 0 1px var(--ui-primary)!important}.result-panel{overflow:hidden;margin-top:14px}.result-meta{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid var(--ui-border)}.result-empty{display:grid;min-height:190px;margin-top:14px;place-items:center;color:var(--ui-text-secondary);text-align:center}.result-empty strong,.result-empty span{display:block}.result-empty strong{color:var(--ui-text-primary);font-size:14px}.result-empty span{margin-top:5px;font-size:11px}@media(max-width:900px){.console-toolbar{flex-wrap:wrap}.toolbar-spacer{display:none}.console-toolbar .el-button{flex:1}.console-toolbar>.el-select{width:100%!important}}@media(max-width:767px){.editor-heading,.result-meta{align-items:flex-start;flex-direction:column}}
</style>
