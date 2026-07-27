<script setup lang="ts">
import { ArrowLeft } from '@element-plus/icons-vue'
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import OrderConfigPanel from '@/components/OrderConfigPanel.vue'
import SshTerminalPanel from '@/components/SshTerminalPanel.vue'
import { resourceText } from '@/utils/status'

const route = useRoute()
const router = useRouter()
const resource = ref<any>(null)
const terminalPanel = ref<InstanceType<typeof SshTerminalPanel> | null>(null)
const activeWorkspace = ref<'terminal' | 'configs'>('terminal')
const resourceId = computed(() => Number(route.params.id))
const terminalSubtitle = computed(() => resource.value ? `${resourceText[resource.value.resource_type]} · ${resource.value.username}@${resource.value.host}:${resource.value.ssh_port}` : '')

async function loadResource() {
  const { data } = await api.get('/resources')
  resource.value = data.find((item: any) => item.id === resourceId.value)
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
          <h1 class="page-title">{{ resource?.name || '资源操作台' }}</h1>
          <p v-if="resource" class="muted mono">{{ resourceText[resource.resource_type] }} · {{ resource.username }}@{{ resource.host }}:{{ resource.ssh_port }}</p>
        </div>
      </div>
    </div>
    <div v-if="['order', 'parser'].includes(resource?.resource_type)" class="workspace-switch">
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
    <OrderConfigPanel v-if="['order', 'parser'].includes(resource?.resource_type)" v-show="activeWorkspace === 'configs'" :resource-id="resourceId" :active="activeWorkspace === 'configs'" :resource-type="resource.resource_type" />
  </div>
</template>

<style scoped>
.terminal-header{align-items:flex-start}.terminal-title{display:flex;align-items:center;gap:14px}.workspace-switch{display:flex;align-items:center;gap:12px;margin:-4px 0 14px}.workspace-note{color:#7f8c97;font-size:12px}.mono{font-family:Cascadia Code,Consolas,monospace}
</style>
