<script setup lang="ts">
import type { CaptureSnapshot, CaptureState } from '@/types/run'
import { statusText, statusType } from '@/utils/status'

defineProps<{
  state?: CaptureState
  signature: string
  snapshots: CaptureSnapshot[]
  resourceName: (resourceId: number) => string
  resourceMeta: (snapshot: CaptureSnapshot) => string
}>()
</script>

<template>
  <div class="capture-detail-block">
    <div class="capture-title">
      <h4>配置详情</h4>
      <span class="muted">按采集快照展示实际配置项</span>
    </div>
    <el-skeleton v-if="state?.loading" :rows="4" animated />
    <el-alert v-else-if="state?.error" :title="state.error" type="error" show-icon :closable="false" />
    <div v-else-if="!signature" class="empty-line">暂无采集详情</div>
    <div v-else-if="!snapshots.length" class="empty-line">未获取到配置详情</div>
    <div v-else class="capture-snapshot-list">
      <article v-for="snapshot in snapshots" :key="snapshot.id" class="capture-snapshot">
        <div class="capture-snapshot-head">
          <div>
            <strong>{{ resourceName(snapshot.resource_id) }}</strong>
            <span class="muted">{{ resourceMeta(snapshot) }}</span>
          </div>
          <el-tag size="small" :type="statusType(snapshot.status)">{{ statusText[snapshot.status] || snapshot.status }}</el-tag>
        </div>
        <el-alert v-if="snapshot.error_message" :title="snapshot.error_message" type="error" show-icon :closable="false" />
        <el-table :data="snapshot.items" size="small" empty-text="暂无采集项">
          <el-table-column label="配置项" min-width="150">
            <template #default="scope">
              <template v-if="snapshot.source_type === 'database'">
                <strong class="mono">{{ scope.row.item_key }}</strong>
                <p class="muted capture-description">{{ scope.row.item_description || '暂无描述' }}</p>
              </template>
              <template v-else>
                <strong>{{ scope.row.item_label }}</strong>
                <p class="muted mono capture-key">{{ scope.row.item_key }}</p>
              </template>
            </template>
          </el-table-column>
          <el-table-column label="采集值" min-width="220">
            <template #default="scope">
              <div class="capture-value" :class="{ danger: scope.row.status === 'failed' }">
                {{ scope.row.value_text || scope.row.error_message || '-' }}
              </div>
              <details v-if="scope.row.raw_output && scope.row.raw_output !== scope.row.value_text" class="raw-output-fold">
                <summary>原始输出</summary>
                <pre>{{ scope.row.raw_output }}</pre>
              </details>
            </template>
          </el-table-column>
          <el-table-column label="来源" min-width="180" show-overflow-tooltip>
            <template #default="scope"><span class="mono">{{ scope.row.source_reference || '-' }}</span></template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="scope"><el-tag size="small" :type="statusType(scope.row.status)">{{ statusText[scope.row.status] || scope.row.status }}</el-tag></template>
          </el-table-column>
        </el-table>
      </article>
    </div>
  </div>
</template>

<style scoped>
.capture-detail-block{margin-top:18px;padding:16px;border:1px solid var(--ui-border);border-radius:8px;background:var(--ui-surface-subtle)}
.capture-title{display:flex;align-items:baseline;gap:10px;margin-bottom:12px}.capture-title h4{margin:0;font-size:15px}
.capture-snapshot-list{display:grid;gap:14px}.capture-snapshot{padding:14px;border:1px solid var(--ui-border);border-radius:8px;background:var(--ui-surface)}
.capture-snapshot-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}.capture-snapshot-head strong,.capture-snapshot-head span{display:block}.capture-snapshot-head .muted{margin-top:4px;font-size:12px}
.capture-key,.capture-description{margin:4px 0 0;font-size:11px;line-height:1.45;word-break:break-word}.capture-value{line-height:1.55;white-space:pre-wrap;word-break:break-word}
.raw-output-fold{margin-top:8px}.raw-output-fold summary{color:var(--ui-primary);font-size:12px;cursor:pointer}.raw-output-fold pre{max-height:180px;overflow:auto;margin:8px 0 0;padding:10px;border-radius:6px;background:var(--ui-terminal);color:#d1dde0;font:12px/1.6 "Cascadia Code",Consolas,monospace}
.empty-line{padding:14px;border-radius:8px;color:var(--ui-text-tertiary);background:var(--ui-surface)}
</style>
