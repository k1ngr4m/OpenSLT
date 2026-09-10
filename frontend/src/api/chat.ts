import { refreshAccessToken } from './client'
import type { components } from '@/types/api.generated'

export type ChatConversation = components['schemas']['ChatConversationOut']
export type ChatMessage = components['schemas']['ChatMessageOut']
export type ChatSource = components['schemas']['ChatSource']
export type ChatAttachment = components['schemas']['ChatAttachment']
export type ChatStatus = components['schemas']['ChatStatusOut']
export type ChatEvent =
  | { type: 'meta'; user: ChatMessage; assistant: ChatMessage }
  | { type: 'delta'; content: string }
  | { type: 'sources'; sources: ChatSource[] }
  | { type: 'done'; message: ChatMessage }
  | { type: 'error'; message: string }

export async function readChatStream(response: Response, receive: (event: ChatEvent) => void) {
  if (!response.body) throw new Error('浏览器不支持流式回答')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let data: string[] = []
  try {
    while (true) {
      const result = await reader.read()
      buffer += decoder.decode(result.value, { stream: !result.done })
      if (buffer.length > 1024 * 1024) throw new Error('回答事件超过大小限制')
      let newline: number
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline).replace(/\r$/, '')
        buffer = buffer.slice(newline + 1)
        if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
        else if (line === '' && data.length) {
          const event = JSON.parse(data.join('\n')) as ChatEvent
          data = []
          receive(event)
          if (event.type === 'error') throw new Error(event.message)
          if (event.type === 'done') return
        }
      }
      if (result.done) throw new Error('连接已中断，请刷新历史记录确认回答状态')
    }
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

export async function sendChatMessage(id: number, content: string, signal: AbortSignal, receive: (event: ChatEvent) => void, attachments: ChatAttachment[] = [], knowledgeBaseId?: number | null) {
  const request = () => fetch(`/api/v1/chat/conversations/${id}/messages`, {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('access_token') || ''}` },
    body: JSON.stringify({ content, ...(attachments.length ? { attachments } : {}), ...(knowledgeBaseId !== undefined ? { knowledge_base_id: knowledgeBaseId } : {}) }),
  })
  let response = await request()
  if (response.status === 401 && localStorage.getItem('refresh_token')) {
    await response.body?.cancel()
    await refreshAccessToken()
    signal.throwIfAborted()
    response = await request()
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error.message || `发送失败（HTTP ${response.status}）`)
  }
  if (!response.headers.get('content-type')?.includes('text/event-stream')) throw new Error('服务器未返回流式回答')
  await readChatStream(response, receive)
}
