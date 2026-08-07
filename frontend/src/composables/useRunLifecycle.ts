import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import type { RunDetail, RunLog } from '@/types/run'

interface RunSocketPayload {
  type?: string
  status?: RunDetail['status']
  progress?: number
  data?: RunLog
}

export function useRunLifecycle(runId: number) {
  const run = ref<RunDetail | null>(null)
  const logs = ref<RunLog[]>([])
  let socket: WebSocket | null = null
  let refreshTimer: number | undefined

  function mergeLogs(items: RunLog[]) {
    const merged = new Map(logs.value.map(item => [item.id, item]))
    for (const item of items) merged.set(item.id, item)
    logs.value = [...merged.values()].sort((left, right) => left.id - right.id)
  }

  async function load() {
    const response = await api.get<RunDetail>(`/runs/${runId}`)
    run.value = response.data
  }

  async function loadInitialLogs() {
    const response = await api.get<RunLog[]>(`/runs/${runId}/logs`)
    mergeLogs(response.data)
  }

  function scheduleRunRefresh() {
    if (refreshTimer !== undefined) window.clearTimeout(refreshTimer)
    refreshTimer = window.setTimeout(() => {
      refreshTimer = undefined
      void load()
    }, 200)
  }

  function connect() {
    const token = localStorage.getItem('access_token')
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    socket = new WebSocket(`${protocol}://${location.host}/api/v1/ws/runs/${runId}?token=${token}`)
    socket.onmessage = event => {
      const payload = JSON.parse(event.data) as RunSocketPayload
      if (payload.type === 'log' && payload.data) mergeLogs([payload.data])
      if ((payload.type === 'status' || payload.type === 'snapshot') && run.value) {
        if (payload.status) run.value.status = payload.status
        if (typeof payload.progress === 'number') run.value.progress = payload.progress
      }
      if (payload.type === 'status') scheduleRunRefresh()
    }
  }

  onMounted(() => {
    void load()
    void loadInitialLogs()
    connect()
  })

  onBeforeUnmount(() => {
    socket?.close()
    if (refreshTimer !== undefined) window.clearTimeout(refreshTimer)
  })

  return { load, logs, run }
}
