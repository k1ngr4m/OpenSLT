import { defineComponent, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { useRunLifecycle } from '@/composables/useRunLifecycle'
import type { RunDetail } from '@/types/run'

vi.mock('@/api/client', () => ({
  api: { get: vi.fn() },
}))

class FakeWebSocket {
  static latest: FakeWebSocket | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  close = vi.fn()

  constructor(public url: string) {
    FakeWebSocket.latest = this
  }
}

describe('useRunLifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.mocked(api.get).mockImplementation(async url => {
      if (String(url).endsWith('/logs')) return { data: [] }
      return {
        data: { id: 9, status: 'draft', progress: 0, steps: [] } as unknown as RunDetail,
      }
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('loads data and merges websocket status before refreshing', async () => {
    let lifecycle: ReturnType<typeof useRunLifecycle>
    const wrapper = mount(defineComponent({
      setup() {
        lifecycle = useRunLifecycle(9)
        return () => null
      },
    }))
    await flushPromises()
    expect(lifecycle!.run.value?.status).toBe('draft')
    expect(FakeWebSocket.latest?.url).toContain('/api/v1/ws/runs/9')

    FakeWebSocket.latest?.onmessage?.({
      data: JSON.stringify({ type: 'status', status: 'running', progress: 30 }),
    } as MessageEvent<string>)
    await nextTick()
    expect(lifecycle!.run.value?.status).toBe('running')
    expect(lifecycle!.run.value?.progress).toBe(30)
    await vi.advanceTimersByTimeAsync(200)
    expect(api.get).toHaveBeenCalledWith('/runs/9')

    wrapper.unmount()
    expect(FakeWebSocket.latest?.close).toHaveBeenCalled()
  })
})
