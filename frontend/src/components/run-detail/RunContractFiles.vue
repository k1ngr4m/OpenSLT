<script setup lang="ts">
import type { ContractFilePreview } from '@/types/run'
import { contractTypeLabel, shortChecksum } from '@/utils/runDetail'

defineProps<{
  files: ContractFilePreview[]
  loadingFileId: number | null
}>()

const emit = defineEmits<{
  preview: [file: ContractFilePreview]
}>()
</script>

<template>
  <div v-if="files.length" class="contract-file-list">
    <div class="contract-file-title">
      <h4>合约 CSV 文件</h4>
      <span class="muted">{{ files.length }} 个文件</span>
    </div>
    <div v-for="file in files" :key="file.id || file.filename" class="contract-file-row">
      <div class="contract-file-main">
        <strong>{{ file.filename }}</strong>
        <span class="muted">
          {{ contractTypeLabel(file.contract_type) }}
          <template v-if="file.quote_date"> · {{ file.quote_date }}</template>
          <template v-if="file.row_count != null"> · {{ file.row_count }} 行</template>
        </span>
        <span class="mono muted">SHA-256 {{ shortChecksum(file.checksum) }}</span>
      </div>
      <el-button size="small" type="primary" plain :loading="loadingFileId === file.id" @click="emit('preview', file)">预览</el-button>
    </div>
  </div>
</template>

<style scoped>
.contract-file-list{display:grid;gap:10px;margin-top:16px}.contract-file-title{display:flex;align-items:baseline;gap:10px}.contract-file-title h4{margin:0;font-size:15px}.contract-file-row{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:12px 14px;border:1px solid #e6edf4;border-radius:8px;background:#fbfdff}.contract-file-main{min-width:0;display:grid;gap:4px}.contract-file-main strong{color:#2f83e6;word-break:break-all}.contract-file-main span{font-size:12px}
</style>
