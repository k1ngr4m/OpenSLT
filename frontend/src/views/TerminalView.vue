<script setup lang="ts">
import { ArrowLeft } from '@element-plus/icons-vue'
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from '@/ui/elementPlusServices'
import { api, errorMessage } from '@/api/client'
import OrderConfigPanel from '@/components/OrderConfigPanel.vue'
import SshTerminalPanel from '@/components/SshTerminalPanel.vue'
import { resourceText } from '@/utils/status'
import type { ApiResource } from '@/types/api'

const route = useRoute()
const router = useRouter()
const resource = ref<ApiResource | null>(null)
const terminalPanel = ref<InstanceType<typeof SshTerminalPanel> | null>(null)
const activeWorkspace = ref<'terminal' | 'configs'>('terminal')
const resourceId = computed(() => Number(route.params.id))
const terminalSubtitle = computed(() => resource.value ? `${resourceText[resource.value.resource_type]} · ${resource.value.username}@${resource.value.host}:${resource.value.ssh_port}` : '')
const configResourceType = computed<'order' | 'parser' | null>(() => {
  const type = resource.value?.resource_type
  return type === 'order' || type === 'parser' ? type : null
})

async function loadResource() {
  const { data } = await api.get<ApiResource[]>('/resources')
  resource.value = data.find(item => item.id === resourceId.value) || null
  if (!resource.value || !['rem', 'market', 'order', 'slnic', 'parser'].includes(resource.value.resource_type)) {
    ElMessage.error('资源不存在或不支持操作台')
    await router.replace('/resources')
    return false
  }
  if (!resource.value.is_enabled) {
    ElMessage.error('资源已停用，无法打开操作台')
    await router.replace('/resources')
    return false
  }
  return true
}

function switchWorkspace(value: 'terminal' | 'configs') {
  activeWorkspace.value = value
  if (value === 'terminal') nextTick(() => terminalPanel.value?.focus())
}

onMounted(async () => {
  try {
    await loadResource()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
})
</script>

<template>
  <div class="page terminal-page">
    <div class="page-header terminal-header">
      <div class="terminal-title">
        <el-button :icon="ArrowLeft" circle plain aria-label="返回资源管理" @click="router.push('/resources')" />
        <div>
          <span class="page-kicker">远端资源操作台</span>
          <h1 class="page-title">{{ resource?.name || '资源操作台' }}</h1>
          <p v-if="resource" class="muted mono">{{ resourceText[resource.resource_type] }} · {{ resource.username }}@{{ resource.host }}:{{ resource.ssh_port }}</p>
        </div>
      </div>
    </div>
    <div v-if="configResourceType" class="workspace-switch">
      <el-radio-group :model-value="activeWorkspace" @change="value => switchWorkspace(value as 'terminal' | 'configs')">
        <el-radio-button value="terminal">SSH 终端</el-radio-button>
        <el-radio-button value="configs">配置文件</el-radio-button>
      </el-radio-group>
      <span v-if="activeWorkspace === 'configs'" class="workspace-note">管理远端根目录中的 {{ resource?.resource_type === 'parser' ? '解析工具' : '发单场景' }} XML</span>
    </div>
    <div v-show="activeWorkspace === 'terminal'" class="terminal-workspace">
      <SshTerminalPanel
        v-if="resource"
        ref="terminalPanel"
        :resource-id="resourceId"
        :title="resource.name"
        :subtitle="terminalSubtitle"
        :active="activeWorkspace === 'terminal'"
        :min-height="420"
      />
    </div>
    <OrderConfigPanel v-if="configResourceType" v-show="activeWorkspace === 'configs'" :resource-id="resourceId" :active="activeWorkspace === 'configs'" :resource-type="configResourceType" />
  </div>
</template>

<style scoped>
.terminal-page{max-width:1600px}.terminal-header{align-items:flex-start}.terminal-title{display:flex;align-items:center;gap:14px}.terminal-title .page-kicker{margin-bottom:0}.workspace-switch{display:flex;align-items:center;gap:12px;margin:-4px 0 14px;padding:8px;border:1px solid var(--ui-border);border-radius:8px;background:var(--ui-surface-subtle)}.workspace-note{color:var(--ui-text-secondary);font-size:11px}.terminal-workspace{padding:14px;border:1px solid var(--ui-border);border-radius:var(--ui-radius-panel);background:var(--ui-surface)}@media(max-width:767px){.workspace-switch{align-items:flex-start;flex-direction:column}.terminal-workspace{padding:8px}}
</style>
