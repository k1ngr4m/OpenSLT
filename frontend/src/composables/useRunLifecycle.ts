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
  let timer: number | undefined

  async function load() {
    const [runResponse, logsResponse] = await Promise.all([
      api.get<RunDetail>(`/runs/${runId}`),
      api.get<RunLog[]>(`/runs/${runId}/logs`),
    ])
    run.value = runResponse.data
    logs.value = logsResponse.data
  }

  function connect() {
    const token = localStorage.getItem('access_token')
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    socket = new WebSocket(`${protocol}://${location.host}/api/v1/ws/runs/${runId}?token=${token}`)
    socket.onmessage = event => {
      const payload = JSON.parse(event.data) as RunSocketPayload
      if (payload.type === 'log' && payload.data) logs.value.push(payload.data)
      if (payload.type === 'status' || payload.type === 'snapshot') {
        if (run.value) {
          if (payload.status) run.value.status = payload.status
          if (typeof payload.progress === 'number') run.value.progress = payload.progress
        }
        window.setTimeout(load, 200)
      }
    }
  }

  onMounted(() => {
    load()
    connect()
    timer = window.setInterval(load, 5000)
  })

  onBeforeUnmount(() => {
    socket?.close()
    if (timer) window.clearInterval(timer)
  })

  return { load, logs, run }
}
