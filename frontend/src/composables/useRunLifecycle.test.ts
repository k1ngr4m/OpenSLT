import { defineComponent, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { useRunLifecycle } from '@/composables/useRunLifecycle'
import type { RunDetail, RunLog } from '@/types/run'

vi.mock('@/api/client', () => ({
  api: { get: vi.fn() },
}))

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static latest: FakeWebSocket | null = null
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  close = vi.fn(() => this.onclose?.({ code: 1000 } as CloseEvent))

  constructor(public url: string) {
    FakeWebSocket.instances.push(this)
    FakeWebSocket.latest = this
  }

  open() {
    this.onopen?.(new Event('open'))
  }

  message(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>)
  }

  disconnect(code = 1006) {
    this.onclose?.({ code } as CloseEvent)
  }
}

let initialLogs: RunLog[] = []
let runStatus: RunDetail['status'] = 'draft'
let runProgress = 0

function runLog(id: number): RunLog {
  return {
    id,
    event_id: null,
    log_type: 'run',
    level: 'INFO',
    event: 'run.test',
    message: `log-${id}`,
    trace_id: 'trace-9',
    user_id: null,
    run_id: 9,
    step_id: null,
    source: 'test',
    duration_ms: null,
    result: null,
    http_method: null,
    http_status: null,
    database_scope: null,
    sql_fingerprint: null,
    detail: {},
    is_redacted: true,
    created_at: '2026-08-07T12:00:00+08:00',
  }
}

function requestsFor(url: string) {
  return vi.mocked(api.get).mock.calls.filter(([value]) => value === url)
}

function mountLifecycle() {
  let lifecycle: ReturnType<typeof useRunLifecycle>
  const wrapper = mount(defineComponent({
    setup() {
      lifecycle = useRunLifecycle(9)
      return () => null
    },
  }))
  return { lifecycle: lifecycle!, wrapper }
}

describe('useRunLifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeWebSocket.instances = []
    FakeWebSocket.latest = null
    initialLogs = []
    runStatus = 'draft'
    runProgress = 0
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.mocked(api.get).mockImplementation(async url => {
      if (String(url) === '/runs/9/logs') return { data: initialLogs }
      return {
        data: { id: 9, status: runStatus, progress: runProgress, steps: [] } as unknown as RunDetail,
      }
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('loads data and merges websocket status before refreshing', async () => {
    const { lifecycle, wrapper } = mountLifecycle()
    await flushPromises()
    expect(lifecycle.run.value?.status).toBe('draft')
    expect(FakeWebSocket.latest?.url).toContain('/api/v1/ws/runs/9')

    FakeWebSocket.latest?.onmessage?.({
      data: JSON.stringify({ type: 'status', status: 'running', progress: 30 }),
    } as MessageEvent<string>)
    await nextTick()
    expect(lifecycle.run.value?.status).toBe('running')
    expect(lifecycle.run.value?.progress).toBe(30)
    await vi.advanceTimersByTimeAsync(200)
    expect(api.get).toHaveBeenCalledWith('/runs/9')

    wrapper.unmount()
    expect(FakeWebSocket.latest?.close).toHaveBeenCalled()
  })

  it('does not poll or reload for an initial snapshot while connected', async () => {
    const { wrapper } = mountLifecycle()
    await flushPromises()
    const socket = FakeWebSocket.instances[0]
    socket.open()
    socket.message({ type: 'snapshot', status: 'draft', progress: 0 })
    await vi.advanceTimersByTimeAsync(15_000)
    expect(requestsFor('/runs/9')).toHaveLength(1)
    expect(requestsFor('/runs/9/logs')).toHaveLength(1)
    wrapper.unmount()
  })

  it('coalesces status messages into one detail-only refresh', async () => {
    const { lifecycle, wrapper } = mountLifecycle()
    await flushPromises()
    const socket = FakeWebSocket.instances[0]
    socket.open()
    runStatus = 'running'
    runProgress = 20
    socket.message({ type: 'status', status: 'running', progress: 10 })
    socket.message({ type: 'status', status: 'running', progress: 20 })
    expect(lifecycle.run.value?.progress).toBe(20)
    await vi.advanceTimersByTimeAsync(200)
    await flushPromises()
    expect(requestsFor('/runs/9')).toHaveLength(2)
    expect(requestsFor('/runs/9/logs')).toHaveLength(1)
    wrapper.unmount()
  })

  it('merges websocket logs by id without an HTTP reload', async () => {
    const { lifecycle, wrapper } = mountLifecycle()
    await flushPromises()
    const socket = FakeWebSocket.instances[0]
    socket.open()
    const item = runLog(41)
    socket.message({ type: 'log', data: item })
    socket.message({ type: 'log', data: item })
    expect(lifecycle.logs.value.map(log => log.id)).toEqual([41])
    expect(requestsFor('/runs/9/logs')).toHaveLength(1)
    wrapper.unmount()
  })
})
