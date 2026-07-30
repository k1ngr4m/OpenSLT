<script setup lang="ts">
import { ref } from 'vue'
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
    width="min(640px, calc(100vw - 32px))"
    append-to-body
  >
    <div class="release-list">
      <article v-for="(release, index) in displayReleaseHistory" :key="release.version" class="release-entry">
        <header class="release-heading">
          <div>
            <strong>v{{ release.version }}</strong>
            <el-tag v-if="index === 0" size="small" type="success" effect="plain">当前版本</el-tag>
          </div>
          <time v-if="release.date" :datetime="release.date">{{ release.date }}</time>
          <span v-else>日期未记录</span>
        </header>
        <h3>{{ release.title }}</h3>
        <ul>
          <li v-for="(change, changeIndex) in release.changes" :key="`${release.version}-${changeIndex}`">
            <span class="change-type">{{ releaseChangeLabels[change.type] }}</span>
            <span>{{ change.text }}</span>
          </li>
        </ul>
      </article>
    </div>
  </el-dialog>
</template>

<style scoped>
.version-trigger{display:block;margin:0;padding:1px 0;border:0;background:transparent;color:var(--ui-text-secondary);font-size:10px;line-height:1;cursor:pointer;white-space:nowrap}
.version-trigger:hover{color:var(--ui-primary)}
.version-trigger:focus-visible{outline:2px solid var(--ui-primary);outline-offset:2px}
.release-list{display:grid}
.release-entry{padding:18px 0;border-bottom:1px solid var(--ui-border)}
.release-entry:first-child{padding-top:2px}
.release-entry:last-child{padding-bottom:2px;border-bottom:0}
.release-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}
.release-heading>div{display:flex;align-items:center;gap:9px}
.release-heading strong{font-size:15px;color:var(--ui-text-primary)}
.release-heading time,.release-heading>span{color:var(--ui-text-tertiary);font-size:11px;white-space:nowrap}
.release-entry h3{margin:8px 0 10px;font-size:13px;font-weight:600}
.release-entry ul{display:grid;gap:8px;margin:0;padding:0;list-style:none}
.release-entry li{display:grid;grid-template-columns:38px minmax(0,1fr);align-items:start;gap:8px;color:var(--ui-text-secondary);font-size:12px;line-height:1.6}
.change-type{color:var(--ui-text-tertiary);font-size:10px;font-weight:600}
@media(max-width:480px){.release-heading{align-items:flex-start;flex-direction:column;gap:5px}.release-entry li{grid-template-columns:34px minmax(0,1fr)}}
</style>
