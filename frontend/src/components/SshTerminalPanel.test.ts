import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import SshTerminalPanel from './SshTerminalPanel.vue'

const mocks = vi.hoisted(() => ({ fit: vi.fn(), dispose: vi.fn(), focus: vi.fn() }))
vi.mock('@xterm/xterm', () => ({ Terminal: class {
  cols = 80; rows = 24
  loadAddon() {} open() {} onData() {} clear() {} write() {}
  focus = mocks.focus; dispose = mocks.dispose
} }))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit = mocks.fit } }))
class Socket {
  static OPEN = 1
  static latest: Socket
  readyState = 1
  onopen?: () => void
  send = vi.fn()
  close = vi.fn()
  constructor() { Socket.latest = this }
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('WebSocket', Socket)
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.stubGlobal('requestAnimationFrame', vi.fn())
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
  vi.spyOn(HTMLElement.prototype, 'getClientRects').mockReturnValue([{ width: 800, height: 400 }] as unknown as DOMRectList)
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
it('keeps a hidden session alive, skips fitting it and deduplicates resize messages', async () => {
  const wrapper = mount(SshTerminalPanel, { props: { resourceId: 1 }, global: { stubs: { ElButton: true, ElTag: true } } })
  await flushPromises()
  const socket = Socket.latest
  socket.onopen?.()
  socket.onopen?.()
  expect(socket.send).toHaveBeenCalledTimes(1)
  await wrapper.setProps({ active: false })
  const fits = mocks.fit.mock.calls.length
  socket.onopen?.()
  expect(mocks.fit).toHaveBeenCalledTimes(fits)
  expect(socket.close).not.toHaveBeenCalled()
  await wrapper.setProps({ active: true })
  await flushPromises()
  expect(socket.send).toHaveBeenCalledTimes(1)
  wrapper.unmount()
  expect(socket.close).toHaveBeenCalledOnce()
  expect(mocks.dispose).toHaveBeenCalledOnce()
})
