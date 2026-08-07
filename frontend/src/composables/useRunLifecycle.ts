import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import type { RunDetail, RunLog } from '@/types/run'

interface RunSocketPayload {
  type?: string
  status?: RunDetail['status']
  progress?: number
  data?: RunLog
}

const LOG_PAGE_SIZE = 5000
const POLL_INTERVAL = 5000
const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 30000] as const
const TERMINAL_STATUSES = new Set<RunDetail['status']>([
  'completed',
  'cancelled',
  'execution_failed',
  'parse_failed',
  'precheck_failed',
  'timed_out',
])

export function useRunLifecycle(runId: number) {
  const run = ref<RunDetail | null>(null)
  const logs = ref<RunLog[]>([])
  let socket: WebSocket | null = null
  let refreshTimer: number | undefined
  let pollTimer: number | undefined
  let reconnectTimer: number | undefined
  let reconnectAttempt = 0
  let reconnectSyncRequired = false
  let mounted = false
  let terminal = false
  let initialRunPending = true
  let lastHttpLogId = 0
  let runLoadPromise: Promise<void> | null = null
  let runReloadQueued = false
  let initialLogLoadPromise: Promise<void> | null = null
  let logSyncPromise: Promise<void> | null = null
  let socketStatusRevision = 0
  let socketProgressRevision = 0
  let latestSocketStatus: RunDetail['status'] | undefined
  let latestSocketProgress: number | undefined

  function mergeLogs(items: RunLog[]) {
    const merged = new Map(logs.value.map(item => [item.id, item]))
    for (const item of items) merged.set(item.id, item)
    logs.value = [...merged.values()].sort((left, right) => left.id - right.id)
  }

  function stopFallback() {
    if (pollTimer !== undefined) window.clearInterval(pollTimer)
    if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
    pollTimer = undefined
    reconnectTimer = undefined
  }

  function observeStatus(status: RunDetail['status'], initialHttp = false) {
    const wasTerminal = terminal
    terminal = TERMINAL_STATUSES.has(status)
    if (terminal) {
      stopFallback()
      if (initialHttp) {
        const current = socket
        socket = null
        current?.close()
      }
      return
    }
    if (wasTerminal && mounted && !socket) connect()
  }

  async function requestRun() {
    const statusRevisionAtStart = socketStatusRevision
    const progressRevisionAtStart = socketProgressRevision
    const response = await api.get<RunDetail>(`/runs/${runId}`)
    const isInitial = initialRunPending
    initialRunPending = false
    const nextRun = { ...response.data }
    if (socketStatusRevision !== statusRevisionAtStart && latestSocketStatus) {
      nextRun.status = latestSocketStatus
    }
    if (socketProgressRevision !== progressRevisionAtStart && latestSocketProgress !== undefined) {
      nextRun.progress = latestSocketProgress
    }
    run.value = nextRun
    observeStatus(nextRun.status, isInitial)
  }

  async function load() {
    if (runLoadPromise) {
      runReloadQueued = true
      return runLoadPromise
    }
    runLoadPromise = (async () => {
      do {
        runReloadQueued = false
        await requestRun()
      } while (runReloadQueued)
    })().finally(() => {
      runLoadPromise = null
    })
    return runLoadPromise
  }

  async function loadInitialLogs() {
    if (initialLogLoadPromise) return initialLogLoadPromise
    initialLogLoadPromise = (async () => {
      const response = await api.get<RunLog[]>(`/runs/${runId}/logs`)
      mergeLogs(response.data)
      for (const item of response.data) lastHttpLogId = Math.max(lastHttpLogId, item.id)
    })().finally(() => {
      initialLogLoadPromise = null
    })
    return initialLogLoadPromise
  }

  async function syncIncrementalLogs() {
    if (logSyncPromise) return logSyncPromise
    logSyncPromise = (async () => {
      if (lastHttpLogId === 0) {
        await loadInitialLogs()
        return
      }
      let afterId = lastHttpLogId
      while (true) {
        const response = await api.get<RunLog[]>(`/runs/${runId}/logs`, {
          params: { after_id: afterId },
        })
        mergeLogs(response.data)
        for (const item of response.data) lastHttpLogId = Math.max(lastHttpLogId, item.id)
        if (response.data.length < LOG_PAGE_SIZE || lastHttpLogId <= afterId) return
        afterId = lastHttpLogId
      }
    })().finally(() => {
      logSyncPromise = null
    })
    return logSyncPromise
  }

  async function reconcile() {
    await Promise.allSettled([load(), syncIncrementalLogs()])
  }

  function startFallback() {
    if (!mounted || terminal || pollTimer !== undefined) return
    void reconcile()
    pollTimer = window.setInterval(() => void reconcile(), POLL_INTERVAL)
  }

  function scheduleReconnect() {
    if (!mounted || terminal || reconnectTimer !== undefined) return
    const delay = RECONNECT_DELAYS[Math.min(reconnectAttempt, RECONNECT_DELAYS.length - 1)]
    reconnectAttempt += 1
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = undefined
      connect()
    }, delay)
  }

  function scheduleRunRefresh() {
    if (refreshTimer !== undefined) window.clearTimeout(refreshTimer)
    refreshTimer = window.setTimeout(() => {
      refreshTimer = undefined
      void load().catch(() => undefined)
    }, 200)
  }

  function connect() {
    if (!mounted || terminal || socket) return
    const token = localStorage.getItem('access_token')
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    const candidate = new WebSocket(`${protocol}://${location.host}/api/v1/ws/runs/${runId}?token=${token}`)
    socket = candidate
    candidate.onopen = () => {
      if (socket !== candidate) return
      stopFallback()
      reconnectAttempt = 0
      if (reconnectSyncRequired) void reconcile()
      reconnectSyncRequired = false
    }
    candidate.onmessage = event => {
      const payload = JSON.parse(event.data) as RunSocketPayload
      if (payload.type === 'log' && payload.data) mergeLogs([payload.data])
      if (payload.type === 'status' || payload.type === 'snapshot') {
        if (payload.status) {
          latestSocketStatus = payload.status
          socketStatusRevision += 1
          if (run.value) {
            run.value.status = payload.status
            observeStatus(payload.status)
          }
        }
        if (typeof payload.progress === 'number') {
          latestSocketProgress = payload.progress
          socketProgressRevision += 1
          if (run.value) run.value.progress = payload.progress
        }
      }
      if (payload.type === 'status') scheduleRunRefresh()
    }
    candidate.onerror = () => candidate.close()
    candidate.onclose = () => {
      if (socket !== candidate) return
      socket = null
      if (!mounted || terminal) return
      reconnectSyncRequired = true
      startFallback()
      scheduleReconnect()
    }
  }

  onMounted(() => {
    mounted = true
    void load().catch(() => undefined)
    void loadInitialLogs().catch(() => undefined)
    connect()
  })

  onBeforeUnmount(() => {
    mounted = false
    stopFallback()
    if (refreshTimer !== undefined) window.clearTimeout(refreshTimer)
    refreshTimer = undefined
    const current = socket
    socket = null
    current?.close()
  })

  return { load, logs, run }
}
