<script setup lang="ts">
import { computed } from 'vue'
import type { ContractFilePreview, JsonMap } from '@/types/run'
import { contractTypeLabel, formatValue, shortChecksum } from '@/utils/runDetail'

const props = defineProps<{
  modelValue: boolean
  file: ContractFilePreview | null
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const rows = computed<JsonMap[]>(() => Array.isArray(props.file?.preview_rows) ? props.file.preview_rows : [])
const columns = computed(() => Object.keys(rows.value[0] || {}))
</script>

<template>
  <el-dialog :model-value="modelValue" :title="file ? `预览 ${file.filename}` : '预览合约 CSV'" width="900px" @update:model-value="emit('update:modelValue', $event)">
    <div v-if="file" class="contract-preview-meta">
      <el-tag effect="plain">{{ contractTypeLabel(file.contract_type) }}</el-tag>
      <span v-if="file.quote_date">交易日 {{ file.quote_date }}</span>
      <span v-if="file.row_count != null">{{ file.row_count }} 行</span>
      <span class="mono">SHA-256 {{ shortChecksum(file.checksum) }}</span>
    </div>
    <div v-loading="loading" class="contract-preview-body">
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
      <el-empty v-else-if="!rows.length" description="暂无预览数据" :image-size="80" />
      <el-table v-else :data="rows" border size="small" height="420">
        <el-table-column v-for="column in columns" :key="column" :prop="column" :label="column" min-width="140" show-overflow-tooltip>
          <template #default="scope"><span class="mono">{{ formatValue(scope.row[column]) }}</span></template>
        </el-table-column>
      </el-table>
    </div>
    <template #footer><el-button @click="emit('update:modelValue', false)">关闭</el-button></template>
  </el-dialog>
</template>

<style scoped>
.contract-preview-meta{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:-4px 0 14px;color:#7b8794;font-size:12px}.contract-preview-body{min-height:180px}
</style>
