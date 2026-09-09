import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { expect, it, vi } from 'vitest'
import PersonalLlmDialog from './PersonalLlmDialog.vue'

const { get, put, post, remove, confirm } = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn(), remove: vi.fn(), confirm: vi.fn() }))
vi.mock('@/api/client', () => ({ api: { get, put, post, delete: remove }, errorMessage: () => '请求失败' }))
vi.mock('@/ui/elementPlusServices', () => ({ ElMessage: { success: vi.fn(), error: vi.fn() }, ElMessageBox: { confirm } }))

it('loads only personal settings, preserves a blank key, saves before testing, and can restore the default', async () => {
  const config = { configured: true, base_url: 'https://personal.example/v1', model_id: 'original', has_api_key: true, allow_insecure_http: false, default_model: 'system-model' }
  get.mockResolvedValue({ data: config })
  put.mockResolvedValue({ data: { ...config, model_id: 'new-model' } })
  post.mockResolvedValue({ data: { ok: true } })
  remove.mockResolvedValue({})
  confirm.mockResolvedValue('confirm')
  const wrapper = mount(PersonalLlmDialog, { global: { plugins: [ElementPlus], stubs: { teleport: true } } })
  const button = (label: string) => wrapper.findAll('button').find(item => item.text() === label)!
  try {
    await flushPromises()
    expect(get).toHaveBeenCalledWith('/model-providers/personal-llm')
    expect(wrapper.get<HTMLInputElement>('input[aria-label="LLM API Key"]').element.value).toBe('')
    expect(wrapper.text()).toContain('system-model')
    await wrapper.get('input[aria-label="LLM 模型 ID"]').setValue('new-model')
    expect(button('测试连接').attributes('disabled')).toBeDefined()
    await button('保存配置').trigger('click')
    await flushPromises()
    expect(put).toHaveBeenCalledWith('/model-providers/personal-llm', { base_url: config.base_url, model_id: 'new-model', api_key: null, allow_insecure_http: false })
    expect(wrapper.emitted('saved')).toHaveLength(1)
    expect(button('测试连接').attributes('disabled')).toBeUndefined()
    await button('测试连接').trigger('click')
    await flushPromises()
    expect(post).toHaveBeenCalledWith('/model-providers/personal-llm/connection-test', undefined, { timeout: 0 })
    get.mockResolvedValue({ data: { ...config, configured: false, base_url: '', model_id: '', has_api_key: false } })
    await button('恢复系统默认').trigger('click')
    await flushPromises()
    expect(remove).toHaveBeenCalledWith('/model-providers/personal-llm')
    expect(wrapper.get<HTMLInputElement>('input[aria-label="LLM 模型 ID"]').element.value).toBe('')
    expect(button('保存配置').attributes('disabled')).toBeDefined()
    await button('关闭').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
  } finally { wrapper.unmount() }
})
