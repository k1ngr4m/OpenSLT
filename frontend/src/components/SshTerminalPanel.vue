<script setup lang="ts">
import '@xterm/xterm/css/xterm.css'
import { FitAddon } from '@xterm/addon-fit'
import { Terminal } from '@xterm/xterm'
import { Connection, RefreshRight, VideoPause } from '@element-plus/icons-vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { JsonMap } from '@/types/run'

type TerminalState = 'idle' | 'connecting' | 'connected' | 'closed' | 'error'

interface WorkflowCommandPayload {
  run_id: number
  step_id: number
  operation: 'start' | 'retry'
}

const props = withDefaults(defineProps<{
  resourceId: number | null
  title?: string
  subtitle?: string
  active?: boolean
  autoConnect?: boolean
  minHeight?: number
}>(), {
  title: 'SSH 终端',
  subtitle: '',
  active: true,
  autoConnect: true,
  minHeight: 360,
})

const emit = defineEmits<{
  status: [message: JsonMap]
  error: [message: string]
  workflowCommand: [message: JsonMap]
}>()

const terminalHost = ref<HTMLElement | null>(null)
const terminalInstance = ref<Terminal | null>(null)
const fitAddon = ref<FitAddon | null>(null)
const socket = ref<WebSocket | null>(null)
const state = ref<TerminalState>('idle')
const statusMessage = ref('准备连接')
const lastError = ref('')
const manualClose = ref(false)
let terminalResizeObserver: ResizeObserver | null = null
let resizeFrame = 0

const connecting = computed(() => state.value === 'connecting')
const connected = computed(() => state.value === 'connected')
const dimensions = computed(() => `${terminalInstance.value?.cols || 0}×${terminalInstance.value?.rows || 0}`)

function writeOutput(data: string) {
  terminalInstance.value?.write(data)
}

function send(payload: Record<string, unknown>) {
  if (socket.value?.readyState !== WebSocket.OPEN) return false
  socket.value.send(JSON.stringify(payload))
  return true
}

function syncSize() {
  if (!fitAddon.value || !terminalInstance.value) return
  fitAddon.value.fit()
  send({ type: 'resize', cols: terminalInstance.value.cols, rows: terminalInstance.value.rows })
}

function scheduleSyncSize() {
  cancelAnimationFrame(resizeFrame)
  resizeFrame = requestAnimationFrame(syncSize)
}

function setupTerminal() {
  if (!terminalHost.value || terminalInstance.value) return
  const instance = new Terminal({
    cursorBlink: true,
    convertEol: true,
    fontFamily: 'Cascadia Code, Consolas, monospace',
    fontSize: 13,
    lineHeight: 1.35,
    scrollback: 5000,
    theme: {
      background: '#111827',
      foreground: '#d8e1e8',
      cursor: '#76e3c4',
      selectionBackground: '#28445c',
      black: '#0d1117',
      brightBlack: '#667085',
      green: '#75d69a',
      brightGreen: '#8bf0b0',
      cyan: '#74d3e8',
      brightCyan: '#9ce7f5',
      yellow: '#efc66d',
      brightYellow: '#f9dc94',
    },
  })
  const addon = new FitAddon()
  instance.loadAddon(addon)
  instance.open(terminalHost.value)
  terminalInstance.value = instance
  fitAddon.value = addon
  instance.onData(data => send({ type: 'input', data }))
  terminalResizeObserver = new ResizeObserver(scheduleSyncSize)
  terminalResizeObserver.observe(terminalHost.value)
  nextTick(() => { syncSize(); instance.focus() })
}

function websocketUrl() {
  const token = localStorage.getItem('access_token') || ''
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${location.host}/api/v1/ws/resources/${props.resourceId}/terminal?token=${encodeURIComponent(token)}`
}

function connect() {
  if (!props.resourceId) {
    lastError.value = '缺少 SSH 资源，无法建立终端连接'
    state.value = 'error'
    emit('error', lastError.value)
    return false
  }
  setupTerminal()
  socket.value?.close(1000, 'reconnecting')
  manualClose.value = false
  lastError.value = ''
  state.value = 'connecting'
  statusMessage.value = '正在建立终端会话'
  terminalInstance.value?.clear()
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
    if (message.type === 'status') {
      statusMessage.value = message.message || message.status
      emit('status', message)
      if (message.status === 'connected') {
        state.value = 'connected'
        syncSize()
        terminalInstance.value?.focus()
      }
      if (message.status === 'closed') state.value = 'closed'
    } else if (message.type === 'output') {
      writeOutput(message.data || '')
    } else if (message.type === 'error') {
      lastError.value = message.message || '终端连接失败'
      state.value = 'error'
      writeOutput(`\r\n\x1b[31m${lastError.value}\x1b[0m\r\n`)
      emit('error', lastError.value)
    } else if (message.type === 'exit') {
      state.value = 'closed'
      statusMessage.value = '远端 Shell 已退出'
    } else if (message.type === 'workflow_command') {
      if (message.status === 'failed') {
        lastError.value = message.message || '终端命令下发失败'
        writeOutput(`\r\n\x1b[31m${lastError.value}\x1b[0m\r\n`)
        emit('error', lastError.value)
      }
      emit('workflowCommand', message)
    }
  }
  current.onerror = () => {
    if (socket.value !== current) return
    state.value = 'error'
    lastError.value = 'WebSocket 连接失败，请检查服务状态'
    statusMessage.value = lastError.value
    emit('error', lastError.value)
  }
  current.onclose = event => {
    if (socket.value !== current) return
    socket.value = null
    if (!manualClose.value && state.value !== 'error' && event.code !== 1000) {
      state.value = 'error'
      lastError.value = '终端连接已断开，请重试'
      statusMessage.value = lastError.value
      emit('error', lastError.value)
    } else if (state.value !== 'error') {
      state.value = 'closed'
      statusMessage.value = '终端已断开'
    }
  }
  return true
}

function disconnect() {
  manualClose.value = true
  socket.value?.close(1000, 'user_closed')
  socket.value = null
  state.value = 'closed'
  statusMessage.value = '终端已断开'
}

function focus() {
  nextTick(() => { syncSize(); terminalInstance.value?.focus() })
}

function sendWorkflowStepCommand(payload: WorkflowCommandPayload) {
  if (!connected.value) return false
  return send({ type: 'workflow_step_command', ...payload })
}

function handleResize() { scheduleSyncSize() }

watch(
  () => [props.active, props.resourceId, props.autoConnect] as const,
  ([active, resourceId, autoConnect], [_oldActive, oldResourceId]) => {
    if (resourceId !== oldResourceId && socket.value) disconnect()
    if (active && autoConnect && resourceId && !socket.value && state.value !== 'connecting') {
      nextTick(connect)
    }
    if (active) focus()
  },
)

onMounted(() => {
  setupTerminal()
  window.addEventListener('resize', handleResize)
  if (props.active && props.autoConnect && props.resourceId) connect()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  terminalResizeObserver?.disconnect()
  cancelAnimationFrame(resizeFrame)
  disconnect()
  terminalInstance.value?.dispose()
})

defineExpose({
  connect,
  disconnect,
  focus,
  sendWorkflowStepCommand,
  connected,
  connecting,
  state,
})
</script>

<template>
  <section class="ssh-terminal-panel">
    <div class="terminal-shell">
      <div class="terminal-shell-bar">
        <div class="terminal-shell-info">
          <span class="terminal-dot" :class="{ live: connected }" />
          <span>{{ statusMessage }}</span>
        </div>
        <span class="terminal-meta">xterm-256color · {{ dimensions }}</span>
      </div>
      <div ref="terminalHost" class="terminal-host" :style="{ minHeight: `${minHeight}px` }" tabindex="0" aria-label="SSH 终端" />
      <div v-if="lastError" class="terminal-error">{{ lastError }}</div>
    </div>
    <div class="terminal-footnote">
      <div>
        <strong>{{ title }}</strong>
        <span v-if="subtitle"> · {{ subtitle }}</span>
      </div>
      <div class="terminal-actions">
        <el-tag type="success" effect="plain">真实 SSH</el-tag>
        <el-button v-if="connected" :icon="VideoPause" plain size="small" @click="disconnect">断开</el-button>
        <el-button v-else :icon="RefreshRight" type="primary" size="small" :loading="connecting" @click="connect">重新连接</el-button>
      </div>
    </div>
    <p class="terminal-warning"><Connection /> <span>输入会直接发送到远端 Shell，请确认目标资源和权限。</span></p>
  </section>
</template>

<style scoped>
.ssh-terminal-panel{display:grid;gap:10px}.terminal-shell{overflow:hidden;background:#111827;border:1px solid #263548;border-radius:10px;box-shadow:0 12px 30px rgba(15,32,48,.16)}.terminal-shell-bar{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid #263548;background:#172234;color:#aebdcb;font-size:12px}.terminal-shell-info{display:flex;align-items:center;gap:8px}.terminal-dot{width:8px;height:8px;border-radius:50%;background:#e0a34c}.terminal-dot.live{background:#58c894;box-shadow:0 0 0 3px rgba(88,200,148,.14)}.terminal-meta{color:#71859a;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.terminal-host{height:min(48vh,520px)}.terminal-host :deep(.xterm){height:100%;padding:18px 20px}.terminal-host :deep(.xterm-viewport){background:#111827!important}.terminal-error{padding:10px 14px;background:#3a1e26;color:#f2a7b5;border-top:1px solid #713845;font-size:13px}.terminal-footnote{display:flex;align-items:center;justify-content:space-between;gap:12px;color:#6f7f8e;font-size:12px}.terminal-actions{display:flex;align-items:center;gap:8px;flex:none}.terminal-warning{display:flex;align-items:center;gap:7px;margin:0;color:#7b8794;font-size:12px}.terminal-warning :deep(svg){width:14px}.terminal-footnote strong{color:#263445}
</style>
