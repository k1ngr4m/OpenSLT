import { flushPromises, mount, shallowMount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ModelsView from './ModelsView.vue'

const { get, post, put, auth } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), auth: { isAdmin: false } }))
vi.mock('@/api/client', () => ({ api: { get, post, put }, errorMessage: () => '加载失败' }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => auth }))

beforeEach(() => {
  vi.clearAllMocks()
  get.mockResolvedValue({ data: [] })
  auth.isAdmin = false
})

describe('account model management', () => {
  it('shows only chat to ordinary accounts and creates a chat provider', async () => {
    const wrapper = mount(ModelsView, { global: { plugins: [ElementPlus] } })
    try {
      await flushPromises()
      expect(wrapper.findAll('[role="tab"]').map(tab => tab.text())).toEqual(['对话'])
      expect(get).toHaveBeenCalledWith('/model-providers', { params: { kind: 'chat' } })
      expect(wrapper.find('input[aria-label="嵌入维度"]').exists()).toBe(false)
      await wrapper.get('input[placeholder="内网模型服务"]').setValue('我的服务')
      await wrapper.get('input[placeholder="https://api.example.com/v1"]').setValue('https://example.com/v1')
      post.mockResolvedValue({ data: { id: 1 } })
      await wrapper.findAll('button').find(button => button.text() === '保存配置')!.trigger('click')
      await flushPromises()
      expect(post).toHaveBeenCalledWith('/model-providers', {
        name: '我的服务', base_url: 'https://example.com/v1', api_key: null, allow_insecure_http: false, kind: 'chat',
      })
    } finally { wrapper.unmount() }
  })

  it('lets admins switch to shared Embedding and clears the chat form', async () => {
    auth.isAdmin = true
    get.mockResolvedValueOnce({ data: [{ id: 1, name: '私有对话', base_url: 'https://private.example/v1', models: [] }] })
    const wrapper = mount(ModelsView, { global: { plugins: [ElementPlus] } })
    try {
      await flushPromises()
      expect(wrapper.findAll('[role="tab"]').map(tab => tab.text())).toEqual(['对话', 'Embedding'])
      await wrapper.get('#tab-embedding').trigger('click')
      await flushPromises()
      expect(get).toHaveBeenLastCalledWith('/model-providers', { params: { kind: 'embedding' } })
      expect(wrapper.text()).not.toContain('私有对话')
      expect((wrapper.get('input[placeholder="https://api.example.com/v1"]').element as HTMLInputElement).value).toBe('')
      expect((wrapper.get('input[aria-label="嵌入维度"]').element as HTMLInputElement).value).toBe('1024')
      expect(wrapper.findAll('button').find(button => button.text() === '自动检测')!.attributes('disabled')).toBeDefined()
      await wrapper.get('input[placeholder="内网模型服务"]').setValue('嵌入服务')
      await wrapper.get('input[placeholder="https://api.example.com/v1"]').setValue('https://embedding.example/v1')
      post.mockResolvedValueOnce({ data: { id: 2 } })
      await wrapper.findAll('button').find(button => button.text() === '保存配置')!.trigger('click')
      await flushPromises()
      expect(post).toHaveBeenCalledWith('/model-providers', {
        name: '嵌入服务', base_url: 'https://embedding.example/v1', api_key: null,
        allow_insecure_http: false, kind: 'embedding', embedding_dimensions: 1024,
      })
    } finally { wrapper.unmount() }
  })
})

it('detects dimensions for the selected Embedding model and saves them independently', async () => {
  auth.isAdmin = true
  const provider = {
    id: 2, name: 'Embedding 服务', base_url: 'https://embedding.example/v1',
    has_api_key: true, allow_insecure_http: false, embedding_dimensions: 1024,
    models: [
      { id: 20, provider_id: 2, kind: 'embedding', model_id: 'bge-m3', is_active: false },
      { id: 21, provider_id: 2, kind: 'embedding', model_id: 'embed-large', is_active: true },
    ],
  }
  get.mockImplementation((_url, config) => Promise.resolve({ data: config.params.kind === 'embedding' ? [provider] : [] }))
  const wrapper = mount(ModelsView, { global: { plugins: [ElementPlus] } })
  try {
    await flushPromises()
    await wrapper.get('#tab-embedding').trigger('click')
    await flushPromises()
    const dimensionInput = () => wrapper.get('input[aria-label="嵌入维度"]')
    const detectButton = () => wrapper.findAll('button').find(button => button.text() === '自动检测')!
    expect(wrapper.text()).toContain('检测模型：embed-large')
    await wrapper.get('input[placeholder="https://api.example.com/v1"]').setValue('https://unsaved.example/v1')
    await detectButton().trigger('click')
    expect(post).not.toHaveBeenCalled()
    await wrapper.get('input[placeholder="https://api.example.com/v1"]').setValue(provider.base_url)
    post.mockRejectedValueOnce(new Error('连接失败'))
    await detectButton().trigger('click')
    await flushPromises()
    expect((dimensionInput().element as HTMLInputElement).value).toBe('1024')
    wrapper.findComponent({ name: 'ElSelect' }).vm.$emit('update:modelValue', 20)
    await flushPromises()
    post.mockResolvedValueOnce({ data: { dimensions: 768 } })
    await detectButton().trigger('click')
    await flushPromises()
    expect(post).toHaveBeenLastCalledWith('/model-providers/models/20/connection-test', undefined, { timeout: 0 })
    expect((dimensionInput().element as HTMLInputElement).value).toBe('768')
    expect(put).not.toHaveBeenCalled()
    put.mockResolvedValue({ data: { ...provider, embedding_dimensions: 768 } })
    provider.embedding_dimensions = 768
    await wrapper.findAll('button').find(button => button.text() === '保存配置')!.trigger('click')
    await flushPromises()
    expect(put).toHaveBeenCalledWith('/model-providers/2', {
      name: provider.name, base_url: provider.base_url, api_key: null, allow_insecure_http: false, embedding_dimensions: 768,
    })
    expect((dimensionInput().element as HTMLInputElement).value).toBe('768')
    await wrapper.get('#tab-chat').trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('Embedding 服务')
    expect(wrapper.find('input[aria-label="嵌入维度"]').exists()).toBe(false)
  } finally { wrapper.unmount() }
})

it('filters remote model IDs, clears the filter, and adds the matching model', async () => {
  get.mockResolvedValue({ data: [{ id: 1, name: '测试提供商', base_url: 'https://example.com', models: [] }] })
  post.mockResolvedValue({ data: { models: ['MiniMax/M3', 'Qwen/Qwen3'] } })
  const wrapper = shallowMount(ModelsView, { global: {
    renderStubDefaultSlot: true,
    directives: { loading: () => {} },
    stubs: {
      ElTabs: true, ElTabPane: true, ElTag: true, ElForm: true, ElFormItem: true,
      ElCheckbox: true, ElIcon: true, ElDialog: true, ElInputNumber: true, ElSelect: true, ElOption: true,
      ElButton: { template: '<button><slot /></button>' },
      ElInput: { props: ['modelValue'], emits: ['update:modelValue'], template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />' },
      ElEmpty: { props: ['description'], template: '<p>{{ description }}</p>' },
    },
  } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '获取模型列表')!.trigger('click')
  await flushPromises()
  const input = wrapper.get('input[aria-label="筛选远端模型"]')
  await input.setValue('  MINIMAX  ')
  expect(wrapper.get('.discovery-list').text()).toContain('MiniMax/M3')
  expect(wrapper.get('.discovery-list').text()).not.toContain('Qwen/Qwen3')
  await wrapper.get('.discovery-list button').trigger('click')
  await flushPromises()
  expect(post).toHaveBeenCalledWith('/model-providers/1/models', { kind: 'chat', model_id: 'MiniMax/M3' })
  await input.setValue('no-match')
  expect(wrapper.get('.discovery-list').text()).toContain('没有匹配的模型')
  await input.setValue('')
  expect(wrapper.get('.discovery-list').text()).toContain('Qwen/Qwen3')
  wrapper.unmount()
})
