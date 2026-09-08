import { flushPromises, shallowMount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
import ModelsView from './ModelsView.vue'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/api/client', () => ({ api: { get, post }, errorMessage: () => '请求失败' }))
vi.mock('@/ui/elementPlusServices', () => ({ ElMessage: { success: vi.fn(), error: vi.fn() }, ElMessageBox: {} }))

it('filters remote model IDs, clears the filter, and adds the matching model', async () => {
  get.mockResolvedValue({ data: [{ id: 1, name: '测试提供商', base_url: 'https://example.com', models: [] }] })
  post.mockResolvedValue({ data: { models: ['MiniMax/M3', 'Qwen/Qwen3'] } })
  const wrapper = shallowMount(ModelsView, { global: {
    renderStubDefaultSlot: true,
    directives: { loading: () => {} },
    stubs: {
      ElTabs: true, ElTabPane: true, ElTag: true, ElForm: true, ElFormItem: true,
      ElCheckbox: true, ElIcon: true, ElDialog: true,
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
