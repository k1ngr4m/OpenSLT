import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { sendChatMessage, type ChatMessage } from '@/api/chat'
import ChatView from './ChatView.vue'

vi.mock('@/api/client', () => ({ api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() }, errorMessage: (error: unknown) => String(error) }))
vi.mock('@/api/chat', () => ({ sendChatMessage: vi.fn() }))
vi.mock('@/ui/elementPlusServices', () => ({ ElMessageBox: { confirm: vi.fn().mockResolvedValue(true) } }))

const stubs = {
  'el-button': { props: ['disabled', 'nativeType'], template: '<button :disabled="disabled" :type="nativeType || \'button\'"><slot /></button>' },
  'el-icon': { template: '<span><slot /></span>' },
  'el-alert': { props: ['title'], template: '<div role="alert">{{ title }}</div>' },
}
const conversation = { id: 1, title: '已有问题', mode: 'knowledge', knowledge_base_id: 1, created_at: '', updated_at: '' }
const user: ChatMessage = { id: 1, role: 'user', content: '重连后怎么办？', status: 'completed', model: '', sources: [], error: null, created_at: '' }
const answer: ChatMessage = { id: 2, role: 'assistant', content: '', status: 'running', model: 'test', sources: [], error: null, created_at: '' }
const savedProviders = () => [
  { id: 1, name: '我的服务', models: [{ id: 11, model_id: 'test', is_active: true }] },
  { id: 2, name: '备用服务', models: [{ id: 22, model_id: 'second-model', is_active: false }] },
]
afterEach(() => { vi.clearAllMocks() })

describe('ChatView', () => {
  it('shows live text, stops generation, and restores the stored partial answer', async () => {
    let stored: ChatMessage[] = []
    vi.mocked(api.get).mockImplementation(async url => ({ data: url === '/chat/status'
      ? { model: 'test', general_ready: true, knowledge_ready: true, general_error: null, knowledge_error: null }
      : url === '/knowledge-bases' ? [{ id: 1, name: '知识库' }] : url === '/model-providers' ? savedProviders() : url === '/chat/conversations' ? [] : stored }))
    vi.mocked(api.post).mockResolvedValue({ data: conversation })
    let finish: () => void = () => {}
    vi.mocked(sendChatMessage).mockImplementation(async (_id, _content, _signal, receive) => {
      receive({ type: 'meta', user: { ...user }, assistant: { ...answer } })
      receive({ type: 'delta', content: '正在解释' })
      await new Promise<void>(resolve => { finish = resolve })
      stored = [user, { ...answer, content: '正在解释', status: 'cancelled' }]
      receive({ type: 'done', message: stored[1]! })
    })
    const wrapper = mount(ChatView, { global: { stubs } })
    await flushPromises()
    await wrapper.get('textarea').setValue(user.content)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('正在解释')
    expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
    expect(wrapper.get('select[aria-label="对话模型"]').attributes('disabled')).toBeDefined()
    const stop = wrapper.findAll('button').find(button => button.text().includes('停止生成'))!
    await stop.trigger('click')
    expect(api.post).toHaveBeenCalledWith('/chat/conversations/1/cancel')
    finish()
    await flushPromises()
    expect(wrapper.text()).toContain('已停止')
    expect(wrapper.get('textarea').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('loads private history and renders source markup as inert text', async () => {
    const stored = { ...answer, status: 'completed', content: '<img src=x onerror=alert(1)> [1]', sources: [
      { id: 1, source_path: '需求/重连.md', revision: '51', chunk_no: 3, content: '<script>bad()</script>必须重新登录' },
    ] }
    vi.mocked(api.get).mockImplementation(async url => ({ data: url === '/chat/status'
      ? { model: 'test', general_error: null, knowledge_error: null }
      : url === '/knowledge-bases' ? [{ id: 1, name: '知识库' }] : url === '/model-providers' ? savedProviders() : url === '/chat/conversations' ? [conversation] : [user, stored] }))
    const wrapper = mount(ChatView, { global: { stubs } })
    await flushPromises()
    await wrapper.get('.conversation-link').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('需求/重连.md')
    expect(wrapper.text()).toContain('r51 · 片段 4')
    expect(wrapper.text()).toContain('必须重新登录')
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.get('.message.assistant .message-meta').text()).toContain('test · 已完成')
    wrapper.unmount()
  })

  it.each([false, true])('switches the account model and restores selection on failure: %s', async fails => {
    let currentModel = 'test'
    vi.mocked(api.get).mockImplementation(async url => ({ data: url === '/chat/status'
      ? { model: currentModel, general_error: null, knowledge_error: null }
      : url === '/knowledge-bases' ? [{ id: 1, name: '知识库' }] : url === '/model-providers' ? savedProviders() : [] }))
    let complete: () => void = () => {}
    vi.mocked(api.post).mockImplementation(async () => {
      await new Promise<void>(resolve => { complete = resolve })
      if (fails) throw new Error('切换失败')
      currentModel = 'second-model'
      return { data: {} }
    })
    const wrapper = mount(ChatView, { global: { stubs } })
    try {
      await flushPromises()
      expect(api.get).toHaveBeenCalledWith('/model-providers', { params: { kind: 'chat' } })
      const selector = wrapper.get<HTMLSelectElement>('select[aria-label="对话模型"]')
      expect(selector.element.value).toBe('11')
      expect(selector.findAll('optgroup').map(group => group.attributes('label'))).toEqual(['我的服务', '备用服务'])
      await wrapper.get('textarea').setValue('保留草稿')
      await selector.setValue('22')
      expect(api.post).toHaveBeenCalledWith('/model-providers/models/22/activate')
      expect(selector.attributes('disabled')).toBeDefined()
      await wrapper.get('form').trigger('submit')
      expect(sendChatMessage).not.toHaveBeenCalled()
      complete()
      await flushPromises()
      expect(selector.element.value).toBe(fails ? '11' : '22')
      expect(wrapper.get<HTMLTextAreaElement>('textarea').element.value).toBe('保留草稿')
      if (fails) expect(wrapper.text()).toContain('切换失败')
      else expect(api.get).toHaveBeenLastCalledWith('/chat/status', { params: { knowledge_base_id: undefined } })
    } finally { wrapper.unmount() }
  })
})

it('selects and changes the optional library within the same conversation', async () => {
  vi.mocked(api.get).mockImplementation(async url => ({ data: url === '/knowledge-bases' ? [{ id: 1, name: '账户库' }, { id: 2, name: '交易库' }] : url === '/chat/status' ? { model: 'test', general_error: null, knowledge_error: null } : url === '/model-providers' ? savedProviders() : [] }))
  vi.mocked(api.post).mockResolvedValue({ data: { ...conversation, knowledge_base_id: 2 } })
  vi.mocked(sendChatMessage).mockResolvedValue()
  const wrapper = mount(ChatView, { global: { stubs } })
  try {
    await flushPromises()
    expect(wrapper.text()).toContain('不使用知识库')
    await wrapper.get('select[aria-label="选择知识库"]').setValue('2')
    await flushPromises()
    expect(api.get).toHaveBeenLastCalledWith('/chat/status', { params: { knowledge_base_id: 2 } })
    await wrapper.get('textarea').setValue('交易规则？')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/chat/conversations', { knowledge_base_id: 2 })
    expect(wrapper.get<HTMLSelectElement>('select[aria-label="选择知识库"]').element.value).toBe('2')
    expect(wrapper.get('select[aria-label="选择知识库"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('select[aria-label="选择知识库"]').setValue('1'); await flushPromises()
    expect(api.get).toHaveBeenLastCalledWith('/chat/status', { params: { knowledge_base_id: 1 } })
  } finally { wrapper.unmount() }
})

it('uses one conversation composer with attachments and optional knowledge next to the send controls', async () => {
  vi.mocked(api.get).mockImplementation(async url => ({ data: url === '/chat/status'
    ? { model: 'test', general_ready: true, knowledge_ready: true, general_error: null, knowledge_error: null }
    : url === '/knowledge-bases' ? [{ id: 1, name: '业务资料' }] : url === '/model-providers' ? savedProviders() : [] }))
  const file = { name: '资料.txt', size: 6, content: '业务规则', total_chars: 4 }
  vi.mocked(api.post).mockImplementation(async url => ({ data: url === '/chat/attachments/parse' ? file : { ...conversation, knowledge_base_id: 1 } }))
  vi.mocked(sendChatMessage).mockResolvedValue(undefined)
  const wrapper = mount(ChatView, { global: { stubs } })
  try {
    await flushPromises()
    expect(wrapper.find('select[aria-label="对话模式"]').exists()).toBe(false)
    expect(wrapper.find('.dialogue-heading .model-picker').exists()).toBe(false)
    expect(wrapper.find('.composer-footer .model-picker').exists()).toBe(true)
    expect(wrapper.get('.composer-footer').element.firstElementChild?.getAttribute('class')).toBe('add-menu')
    await wrapper.get('select[aria-label="选择知识库"]').setValue(1); await flushPromises()
    expect(wrapper.text()).toContain('知识库：业务资料')
    const input = wrapper.get<HTMLInputElement>('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['业务规则'], '资料.txt')], configurable: true })
    await input.trigger('change'); await flushPromises()
    expect(wrapper.get('.pending-attachments').text()).toContain('资料.txt')
    await wrapper.get('button[aria-label="移除附件：资料.txt"]').trigger('click')
    expect(wrapper.find('.pending-attachments').exists()).toBe(false)
    await input.trigger('change'); await flushPromises()
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(sendChatMessage).toHaveBeenCalledWith(1, '请分析所附文件。', expect.any(AbortSignal), expect.any(Function), [file], 1)
  } finally { wrapper.unmount() }
})
