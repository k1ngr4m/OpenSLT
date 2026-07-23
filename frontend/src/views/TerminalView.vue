<script setup lang="ts">
import '@xterm/xterm/css/xterm.css'
import { FitAddon } from '@xterm/addon-fit'
import { Terminal } from '@xterm/xterm'
import { ArrowLeft, Connection, RefreshRight, VideoPause } from '@element-plus/icons-vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import { resourceText } from '@/utils/status'

const route = useRoute()
const router = useRouter()
const resource = ref<any>(null)
const terminalHost = ref<HTMLElement | null>(null)
const terminal = ref<Terminal | null>(null)
const fitAddon = ref<FitAddon | null>(null)
const socket = ref<WebSocket | null>(null)
const state = ref<'loading' | 'connecting' | 'connected' | 'closed' | 'error'>('loading')
const mode = ref<'remote' | 'simulated'>('simulated')
const statusMessage = ref('准备连接')
const lastError = ref('')
const manualClose = ref(false)
const connecting = computed(() => state.value === 'loading' || state.value === 'connecting')
const connected = computed(() => state.value === 'connected')
const resourceId = computed(() => Number(route.params.id))

async function loadResource() {
  const { data } = await api.get('/resources')
  resource.value = data.find((item: any) => item.id === resourceId.value)
  if (!resource.value || !['rem', 'market', 'order', 'slnic'].includes(resource.value.resource_type)) {
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

function writeOutput(data: string) {
  terminal.value?.write(data)
}

function send(payload: Record<string, unknown>) {
  if (socket.value?.readyState === WebSocket.OPEN) socket.value.send(JSON.stringify(payload))
}

function syncSize() {
  if (!fitAddon.value || !terminal.value) return
  fitAddon.value.fit()
  send({ type: 'resize', cols: terminal.value.cols, rows: terminal.value.rows })
}

function setupTerminal() {
  if (!terminalHost.value || terminal.value) return
  const instance = new Terminal({
    cursorBlink: true,
    convertEol: true,
    fontFamily: 'Cascadia Code, Consolas, monospace',
    fontSize: 13,
    lineHeight: 1.35,
    scrollback: 5000,
    theme: { background: '#111827', foreground: '#d8e1e8', cursor: '#76e3c4', selectionBackground: '#28445c', black: '#0d1117', brightBlack: '#667085', green: '#75d69a', brightGreen: '#8bf0b0', cyan: '#74d3e8', brightCyan: '#9ce7f5', yellow: '#efc66d', brightYellow: '#f9dc94' },
  })
  const addon = new FitAddon()
  instance.loadAddon(addon)
  instance.open(terminalHost.value)
  terminal.value = instance
  fitAddon.value = addon
  instance.onData(data => send({ type: 'input', data }))
  nextTick(() => { syncSize(); instance.focus() })
}

function websocketUrl() {
  const token = localStorage.getItem('access_token') || ''
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${location.host}/api/v1/ws/resources/${resourceId.value}/terminal?token=${encodeURIComponent(token)}`
}

function connect() {
  if (!resource.value) return
  socket.value?.close(1000, 'reconnecting')
  manualClose.value = false
  lastError.value = ''
  state.value = 'connecting'
  statusMessage.value = '正在建立终端会话'
  terminal.value?.clear()
  writeOutput('\x1b[90mConnecting to OpenSLT terminal...\x1b[0m\r\n')
  const current = new WebSocket(websocketUrl())
  socket.value = current
  current.onopen = () => {
    if (socket.value !== current) return
    statusMessage.value = '连接已建立'
    syncSize()
  }
  current.onmessage = event => {
    if (socket.value !== current) return
    const message = JSON.parse(event.data)
    if (message.mode) mode.value = message.mode
    if (message.type === 'status') {
      statusMessage.value = message.message || message.status
      if (message.status === 'connected') { state.value = 'connected'; syncSize(); terminal.value?.focus() }
      if (message.status === 'closed') state.value = 'closed'
    } else if (message.type === 'output') writeOutput(message.data || '')
    else if (message.type === 'error') { lastError.value = message.message || '终端连接失败'; state.value = 'error'; writeOutput(`\r\n\x1b[31m${lastError.value}\x1b[0m\r\n`) }
    else if (message.type === 'exit') { state.value = 'closed'; statusMessage.value = '远端 Shell 已退出' }
  }
  current.onerror = () => {
    if (socket.value !== current) return
    state.value = 'error'
    lastError.value = 'WebSocket 连接失败，请检查服务状态'
    statusMessage.value = lastError.value
  }
  current.onclose = event => {
    if (socket.value !== current) return
    socket.value = null
    if (!manualClose.value && state.value !== 'error' && event.code !== 1000) {
      state.value = 'error'
      lastError.value = '终端连接已断开，请重试'
      statusMessage.value = lastError.value
    } else if (state.value !== 'error') {
      state.value = 'closed'
      statusMessage.value = '终端已断开'
    }
  }
}

function disconnect() {
  manualClose.value = true
  socket.value?.close(1000, 'user_closed')
  socket.value = null
  state.value = 'closed'
  statusMessage.value = '终端已断开'
}

function handleResize() { syncSize() }

onMounted(async () => {
  try {
    if (await loadResource()) {
      await nextTick()
      setupTerminal()
      connect()
      window.addEventListener('resize', handleResize)
    }
  } catch (error) {
    state.value = 'error'
    lastError.value = errorMessage(error)
    statusMessage.value = lastError.value
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  disconnect()
  terminal.value?.dispose()
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
      <div class="terminal-actions">
        <el-tag :type="mode === 'remote' ? 'success' : 'warning'" effect="plain">{{ mode === 'remote' ? '真实 SSH' : '模拟会话' }}</el-tag>
        <el-button v-if="connected" :icon="VideoPause" plain @click="disconnect">断开</el-button>
        <el-button v-else :icon="RefreshRight" type="primary" :loading="connecting" @click="connect">重新连接</el-button>
      </div>
    </div>
    <section class="terminal-shell">
      <div class="terminal-shell-bar">
        <div class="terminal-shell-info"><span class="terminal-dot" :class="{ live: connected }" /> <span>{{ statusMessage }}</span></div>
        <span class="terminal-meta">xterm-256color · {{ terminal?.cols || 0 }}×{{ terminal?.rows || 0 }}</span>
      </div>
      <div ref="terminalHost" class="terminal-host" tabindex="0" aria-label="SSH 终端" />
      <div v-if="lastError" class="terminal-error">{{ lastError }}</div>
    </section>
    <div class="terminal-footnote"><Connection /> <span>{{ mode === 'remote' ? '输入会直接发送到远端 Shell，请确认目标资源和权限。' : '模拟模式只提供内置命令，不会连接远程服务器或执行本机命令。' }}</span></div>
  </div>
</template>

<style scoped>
.terminal-header{align-items:flex-start}.terminal-title{display:flex;align-items:center;gap:14px}.terminal-actions{display:flex;align-items:center;gap:10px}.terminal-shell{overflow:hidden;background:#111827;border:1px solid #263548;border-radius:10px;box-shadow:0 12px 30px rgba(15,32,48,.16)}.terminal-shell-bar{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid #263548;background:#172234;color:#aebdcb;font-size:12px}.terminal-shell-info{display:flex;align-items:center;gap:8px}.terminal-dot{width:8px;height:8px;border-radius:50%;background:#e0a34c}.terminal-dot.live{background:#58c894;box-shadow:0 0 0 3px rgba(88,200,148,.14)}.terminal-meta{color:#71859a;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.terminal-host{height:min(68vh,720px);min-height:420px;padding:18px 20px}.terminal-host :deep(.xterm){height:100%}.terminal-host :deep(.xterm-viewport){background:#111827!important}.terminal-error{padding:10px 14px;background:#3a1e26;color:#f2a7b5;border-top:1px solid #713845;font-size:13px}.terminal-footnote{display:flex;align-items:center;gap:7px;margin-top:12px;color:#7b8794;font-size:12px}.terminal-footnote :deep(svg){width:14px}.mono{font-family:Cascadia Code,Consolas,monospace}
</style>
