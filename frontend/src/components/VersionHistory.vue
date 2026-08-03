<script setup lang="ts">
import { ref } from 'vue'
import { ArrowRight } from '@element-plus/icons-vue'
import { appVersion, releaseChangeLabels, releaseHistory } from '@/releaseMetadata'
import type { ReleaseChange } from '@/releaseMetadata'

const visible = ref(false)

const releaseChangeTypeOrder: readonly ReleaseChange['type'][] = [
  'added',
  'changed',
  'fixed',
  'removed',
  'security',
]

function groupChangesByType(changes: readonly ReleaseChange[]): ReleaseChange[] {
  return releaseChangeTypeOrder.flatMap(type => changes.filter(change => change.type === type))
}

const displayReleaseHistory = releaseHistory.map(release => ({
  ...release,
  changes: groupChangesByType(release.changes),
}))
</script>

<template>
  <button
    class="version-trigger mono"
    type="button"
    aria-haspopup="dialog"
    @click="visible = true"
  >
    v{{ appVersion }}
  </button>

  <el-dialog
    v-model="visible"
    title="版本更新说明"
    class="version-history-dialog"
    width="640px"
    append-to-body
  >
    <div class="release-list">
      <details
        v-for="release in displayReleaseHistory"
        :key="release.version"
        class="release-entry"
        :open="release.version === appVersion"
      >
        <summary class="release-heading">
          <div>
            <strong>v{{ release.version }}</strong>
            <el-tag v-if="release.version === appVersion" size="small" type="success" effect="plain">当前版本</el-tag>
            <span>{{ release.title }}</span>
          </div>
          <div class="release-meta">
            <time v-if="release.date" :datetime="release.date">{{ release.date }}</time>
            <span v-else>日期未记录</span>
            <el-icon class="release-chevron"><ArrowRight /></el-icon>
          </div>
        </summary>
        <div class="release-content">
          <ul>
            <li v-for="(change, changeIndex) in release.changes" :key="`${release.version}-${changeIndex}`">
              <span class="change-type">{{ releaseChangeLabels[change.type] }}</span>
              <span>{{ change.text }}</span>
            </li>
          </ul>
        </div>
      </details>
    </div>
  </el-dialog>
</template>

<style scoped>
.version-trigger{display:block;margin:0;padding:1px 0;border:0;background:transparent;color:var(--ui-text-secondary);font-size:10px;line-height:1;cursor:pointer;white-space:nowrap}
.version-trigger:hover{color:var(--ui-primary)}
.version-trigger:focus-visible{outline:2px solid var(--ui-primary);outline-offset:2px}
:global(.version-history-dialog){display:flex;height:560px;max-width:calc(100vw - 32px);max-height:calc(100dvh - 32px);margin:max(16px,calc((100dvh - 560px)/2)) auto 0;overflow:hidden;flex-direction:column}
:global(.version-history-dialog .el-dialog__header){flex:none}
:global(.version-history-dialog .el-dialog__body){min-height:0;overflow-y:auto}
.release-list{display:grid}
.release-entry{border-bottom:1px solid var(--ui-border)}
.release-entry:last-child{border-bottom:0}
.release-heading{display:flex;min-height:60px;align-items:center;justify-content:space-between;gap:16px;padding:10px 2px;cursor:pointer;list-style:none}
.release-heading::-webkit-details-marker{display:none}
.release-heading:focus-visible{outline:2px solid var(--ui-primary);outline-offset:-2px}
.release-heading>div{display:flex;min-width:0;align-items:center;gap:9px}
.release-heading strong{font-size:15px;color:var(--ui-text-primary)}
.release-heading>div>span:last-child{min-width:0;overflow:hidden;color:var(--ui-text-secondary);font-size:12px;text-overflow:ellipsis;white-space:nowrap}
.release-meta{display:flex;flex:none;align-items:center;gap:10px;color:var(--ui-text-tertiary);font-size:11px;white-space:nowrap}
.release-chevron{transition:transform .18s ease}
.release-entry[open] .release-chevron{transform:rotate(90deg)}
.release-content{padding:0 2px 18px}
.release-content ul{display:grid;gap:8px;margin:0;padding:0;list-style:none}
.release-entry li{display:grid;grid-template-columns:38px minmax(0,1fr);align-items:start;gap:8px;color:var(--ui-text-secondary);font-size:12px;line-height:1.6}
.change-type{color:var(--ui-text-tertiary);font-size:10px;font-weight:600}
@media(max-width:480px){.release-heading{min-height:72px;align-items:flex-start;flex-direction:column;gap:5px}.release-heading>div{width:100%}.release-meta{width:100%;justify-content:space-between}.release-entry li{grid-template-columns:34px minmax(0,1fr)}}
</style>
