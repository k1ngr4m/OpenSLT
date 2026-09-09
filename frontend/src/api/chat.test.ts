import { afterEach, describe, expect, it, vi } from 'vitest'
import { readChatStream, sendChatMessage } from './chat'
import { refreshAccessToken } from './client'

vi.mock('./client', () => ({ refreshAccessToken: vi.fn() }))
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); localStorage.clear() })

function response(text: string, fragmented = false) {
  const bytes = new TextEncoder().encode(text)
  return new Response(new ReadableStream({ start(controller) {
    if (fragmented) for (const byte of bytes) controller.enqueue(new Uint8Array([byte]))
    else controller.enqueue(bytes)
    controller.close()
  } }), { headers: { 'Content-Type': 'text/event-stream' } })
}

describe('chat streaming transport', () => {
  it('decodes fragmented UTF-8 and CRLF while ignoring heartbeats', async () => {
    const receive = vi.fn()
    await readChatStream(response(': heartbeat\r\n\r\ndata: {"type":"delta","content":"你好"}\r\n\r\ndata: {"type":"done","message":{}}\r\n\r\n', true), receive)
    expect(receive.mock.calls.map(([event]) => event.type)).toEqual(['delta', 'done'])
    expect(receive.mock.calls[0][0].content).toBe('你好')
  })

  it('does not mistake EOF or a server error for a completed answer', async () => {
    await expect(readChatStream(response('data: {"type":"delta","content":"部分"}\n\n'), vi.fn())).rejects.toThrow('连接已中断')
    await expect(readChatStream(response('data: {"type":"error","message":"保存失败"}\n\n'), vi.fn())).rejects.toThrow('保存失败')
  })

  it('refreshes authentication once and sends only the user content', async () => {
    localStorage.setItem('access_token', 'old')
    localStorage.setItem('refresh_token', 'refresh')
    vi.mocked(refreshAccessToken).mockImplementation(async () => { localStorage.setItem('access_token', 'new'); return 'new' })
    const fetch = vi.fn().mockResolvedValueOnce(new Response('{}', { status: 401 }))
      .mockResolvedValueOnce(response('data: {"type":"done","message":{}}\n\n'))
    vi.stubGlobal('fetch', fetch)
    await sendChatMessage(4, '测试', new AbortController().signal, vi.fn())
    expect(refreshAccessToken).toHaveBeenCalledTimes(1)
    expect(fetch).toHaveBeenCalledTimes(2)
    const [url, options] = fetch.mock.calls[1]
    expect(url).toBe('/api/v1/chat/conversations/4/messages')
    expect(options.headers.Authorization).toBe('Bearer new')
    expect(JSON.parse(options.body)).toEqual({ content: '测试' })
  })

  it('never retries a rejected business request or turns it into a blank answer', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"message":"智能助手繁忙"}', { status: 429 }))
    vi.stubGlobal('fetch', fetch)
    await expect(sendChatMessage(1, '问题', new AbortController().signal, vi.fn())).rejects.toThrow('智能助手繁忙')
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
