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
let incrementalLogs: RunLog[] = []
let incrementalLogPages = new Map<number, RunLog[]>()
let runStatus: RunDetail['status'] = 'draft'
let runProgress = 0
let deferredRunLoad: Promise<{ data: RunDetail }> | null = null
let deferredInitialLogs: Promise<{ data: RunLog[] }> | null = null

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
    incrementalLogs = []
    incrementalLogPages = new Map()
    runStatus = 'draft'
    runProgress = 0
    deferredRunLoad = null
    deferredInitialLogs = null
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.mocked(api.get).mockImplementation(async (url, config) => {
      if (String(url) === '/runs/9/logs') {
        const afterId = Number((config as { params?: { after_id?: number } } | undefined)?.params?.after_id || 0)
        if (afterId > 0) return { data: incrementalLogPages.get(afterId) || incrementalLogs.filter(log => log.id > afterId) }
        if (deferredInitialLogs) return deferredInitialLogs
        return { data: initialLogs }
      }
      if (deferredRunLoad) return deferredRunLoad
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

  it('serializes concurrent detail refreshes and performs one follow-up refresh', async () => {
    const { lifecycle, wrapper } = mountLifecycle()
    await flushPromises()
    let resolveDeferredRunLoad: ((value: { data: RunDetail }) => void) | undefined
    deferredRunLoad = new Promise(resolve => {
      resolveDeferredRunLoad = resolve
    })

    const first = lifecycle.load()
    const second = lifecycle.load()
    expect(requestsFor('/runs/9')).toHaveLength(2)

    resolveDeferredRunLoad?.({
      data: { id: 9, status: 'running', progress: 30, steps: [] } as unknown as RunDetail,
    })
    await flushPromises()
    expect(requestsFor('/runs/9')).toHaveLength(3)
    await Promise.all([first, second])
    wrapper.unmount()
  })

  it('does not let an older HTTP response overwrite a websocket status received in flight', async () => {
    const { lifecycle, wrapper } = mountLifecycle()
    await flushPromises()
    let resolveDeferredRunLoad: ((value: { data: RunDetail }) => void) | undefined
    deferredRunLoad = new Promise(resolve => {
      resolveDeferredRunLoad = resolve
    })

    const refresh = lifecycle.load()
    FakeWebSocket.instances[0].message({ type: 'status', status: 'running', progress: 40 })
    resolveDeferredRunLoad?.({
      data: { id: 9, status: 'draft', progress: 0, steps: [] } as unknown as RunDetail,
    })
    await refresh
    expect(lifecycle.run.value?.status).toBe('running')
    expect(lifecycle.run.value?.progress).toBe(40)
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

  it('polls incrementally while disconnected and stops after reconnecting', async () => {
    initialLogs = [runLog(10)]
    incrementalLogs = [runLog(11)]
    const { lifecycle, wrapper } = mountLifecycle()
    await flushPromises()
    const first = FakeWebSocket.instances[0]
    first.open()
    first.message({ type: 'log', data: runLog(99) })
    first.disconnect()
    await flushPromises()

    expect(api.get).toHaveBeenCalledWith('/runs/9/logs', { params: { after_id: 10 } })
    expect(lifecycle.logs.value.map(log => log.id)).toEqual([10, 11, 99])

    await vi.advanceTimersByTimeAsync(1_000)
    const second = FakeWebSocket.instances[1]
    expect(second).toBeTruthy()
    second.open()
    await flushPromises()

    const callsAfterReconnect = vi.mocked(api.get).mock.calls.length
    await vi.advanceTimersByTimeAsync(5_000)
    expect(vi.mocked(api.get).mock.calls).toHaveLength(callsAfterReconnect)
    wrapper.unmount()
  })

  it('shares the initial log request when disconnect reconciliation overlaps it', async () => {
    let resolveDeferredInitialLogs: ((value: { data: RunLog[] }) => void) | undefined
    deferredInitialLogs = new Promise(resolve => {
      resolveDeferredInitialLogs = resolve
    })
    const { wrapper } = mountLifecycle()
    await flushPromises()
    FakeWebSocket.instances[0].disconnect()
    expect(requestsFor('/runs/9/logs')).toHaveLength(1)
    resolveDeferredInitialLogs?.({ data: [runLog(10)] })
    await flushPromises()
    expect(requestsFor('/runs/9/logs')).toHaveLength(1)
    wrapper.unmount()
  })

  it('pages incremental logs after a full response', async () => {
    initialLogs = [runLog(10)]
    incrementalLogPages.set(10, Array.from({ length: 5_000 }, (_, index) => runLog(index + 11)))
    incrementalLogPages.set(5_010, [runLog(5_011)])
    const { lifecycle, wrapper } = mountLifecycle()
    await flushPromises()
    const socket = FakeWebSocket.instances[0]
    socket.open()
    socket.disconnect()
    await flushPromises()

    expect(api.get).toHaveBeenCalledWith('/runs/9/logs', { params: { after_id: 10 } })
    expect(api.get).toHaveBeenCalledWith('/runs/9/logs', { params: { after_id: 5010 } })
    expect(lifecycle.logs.value.map(log => log.id)).toEqual([10, ...Array.from({ length: 5_001 }, (_, index) => index + 11)])
    wrapper.unmount()
  })

  it('retries one websocket at a time with capped backoff', async () => {
    const { wrapper } = mountLifecycle()
    await flushPromises()
    const delays = [1_000, 2_000, 5_000, 10_000, 30_000, 30_000]
    for (const [index, delay] of delays.entries()) {
      FakeWebSocket.instances[index].disconnect()
      await vi.advanceTimersByTimeAsync(delay - 1)
      expect(FakeWebSocket.instances).toHaveLength(index + 1)
      await vi.advanceTimersByTimeAsync(1)
      expect(FakeWebSocket.instances).toHaveLength(index + 2)
    }
    wrapper.unmount()
  })

  it('closes the concurrently opened socket when the initial run is terminal', async () => {
    runStatus = 'completed'
    runProgress = 100
    const { wrapper } = mountLifecycle()
    await flushPromises()
    expect(FakeWebSocket.instances[0].close).toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(FakeWebSocket.instances).toHaveLength(1)
    wrapper.unmount()
  })

  it('does not reconnect after a terminal status but keeps the open socket for final logs', async () => {
    const { lifecycle, wrapper } = mountLifecycle()
    await flushPromises()
    const socket = FakeWebSocket.instances[0]
    socket.open()
    runStatus = 'completed'
    runProgress = 100
    socket.message({ type: 'status', status: 'completed', progress: 100 })
    socket.message({ type: 'log', data: runLog(99) })
    expect(socket.close).not.toHaveBeenCalled()
    socket.disconnect()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(lifecycle.logs.value.some(log => log.id === 99)).toBe(true)
    expect(FakeWebSocket.instances).toHaveLength(1)
    wrapper.unmount()
  })

  it('clears reconnect, polling, and refresh timers on unmount', async () => {
    const { wrapper } = mountLifecycle()
    await flushPromises()
    FakeWebSocket.instances[0].disconnect()
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('absorbs initial background synchronization failures without clearing state', async () => {
    vi.mocked(api.get).mockImplementation(async () => {
      throw new Error('temporarily unavailable')
    })
    const { lifecycle, wrapper } = mountLifecycle()
    await flushPromises()
    expect(lifecycle.run.value).toBeNull()
    expect(lifecycle.logs.value).toEqual([])
    wrapper.unmount()
  })

  it('keeps websocket state when its background detail refresh fails', async () => {
    const { lifecycle, wrapper } = mountLifecycle()
    await flushPromises()
    const socket = FakeWebSocket.instances[0]
    socket.open()
    vi.mocked(api.get).mockRejectedValueOnce(new Error('temporarily unavailable'))
    socket.message({ type: 'status', status: 'running', progress: 25 })
    await vi.advanceTimersByTimeAsync(200)
    await flushPromises()
    expect(lifecycle.run.value?.status).toBe('running')
    expect(lifecycle.run.value?.progress).toBe(25)
    wrapper.unmount()
  })
})
