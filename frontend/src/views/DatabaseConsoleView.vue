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
    if (data.simulated) ElMessage.warning('当前为模拟模式，显示的是模拟数据')
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
    const mode = preview.simulated ? '模拟执行，不会修改真实数据。' : '真实执行将提交数据库事务。'
    const { value } = await ElMessageBox.prompt(
      `目标：${preview.database_name}.${preview.table_name}，预计影响 ${preview.estimated_rows} 行。${mode}\n请输入资源名称“${resource.value.name}”完成第二次确认。`,
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
    ElMessage.success(`${data.simulated ? '模拟' : '真实'} UPDATE 已完成，影响 ${data.affected_rows} 行`)
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
    if (response.headers['x-openslt-simulated'] === 'true') ElMessage.warning('已导出模拟数据')
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
          <h1 class="page-title">{{ resource.name }}</h1>
          <p class="muted mono">{{ resource.database_username }}@{{ resource.database_host }}:{{ resource.database_port }}</p>
        </div>
      </div>
      <el-tag v-if="result?.simulated" type="warning" effect="dark">模拟数据</el-tag>
    </div>

    <div class="console-toolbar">
      <el-select v-model="databaseName" aria-label="选择数据库" style="width:220px">
        <el-option v-for="name in resource.database_names" :key="name" :label="name" :value="name" />
      </el-select>
      <el-button type="primary" :icon="Search" :loading="loading" @click="runSelect">执行查询</el-button>
      <el-button type="danger" plain :icon="WarningFilled" :loading="loading" @click="previewUpdate">预览更新</el-button>
      <span class="toolbar-spacer" />
      <el-button :icon="Download" :disabled="loading" @click="exportData('csv')">导出 CSV</el-button>
      <el-button :icon="Download" :disabled="loading" @click="exportData('xlsx')">导出 XLSX</el-button>
    </div>

    <el-input v-model="sql" type="textarea" :rows="8" resize="vertical" class="sql-editor" spellcheck="false" />

    <div v-if="result" class="card result-panel">
      <div class="result-meta">
        <strong>查询结果</strong>
        <span class="muted">{{ result.row_count }} 行 · {{ result.elapsed_ms }} ms<span v-if="result.truncated"> · 仅显示前 500 行</span></span>
      </div>
      <el-table :data="result.rows" height="430" border>
        <el-table-column v-for="column in result.columns" :key="column" :prop="column" :label="column" min-width="150" show-overflow-tooltip />
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.console-title{display:flex;align-items:center;gap:14px}.console-toolbar{display:flex;align-items:center;gap:10px;margin-bottom:14px}.toolbar-spacer{flex:1}.sql-editor{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.result-panel{margin-top:18px}.result-meta{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
</style>
