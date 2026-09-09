import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import SmartCaseGenerateView from './SmartCaseGenerateView.vue'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/api/client', () => ({ api: { get, post }, errorMessage: () => '预览加载失败' }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ isAdmin: false }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

const source = readFileSync(resolve(process.cwd(), 'src/views/SmartCaseGenerateView.vue'), 'utf8')

describe('SmartCaseGenerateView', () => {
  it.each(['', '   ', '  重点覆盖权限校验\n注意 {{references}} 边界值  '])('submits optional notes with the selected requirement: %j', async (notes) => {
    const requirement = { source_path: '需求/登录.md', revision: '51', requirement_no: '1024', requirement_name: '登录' }
    get.mockImplementation((path: string) => Promise.resolve({ data: path === '/knowledge-bases' ? [{ id: 1, name: '知识库' }] : path.endsWith('/requirements') ? [requirement] : [] }))
    post.mockResolvedValue({ data: {} })
    const wrapper = mount(SmartCaseGenerateView, { global: { plugins: [ElementPlus] } })
    try {
      await flushPromises()
      await wrapper.get('input[type="radio"]').setValue()
      const input = wrapper.get('textarea#additional-prompt')
      expect(wrapper.get('label[for="additional-prompt"]').text()).toBe('补充提示词（选填）')
      expect(input.attributes('maxlength')).toBe('4000')
      await input.setValue(notes)
      await wrapper.get('.generate-button').trigger('click')
      await flushPromises()
      expect(post).toHaveBeenCalledWith('/smart-cases/generations', { knowledge_base_id: 1, requirement_path: requirement.source_path, additional_prompt: notes.trim() })
    } finally {
      wrapper.unmount()
    }
  })

  it('selects an indexed requirement and generates a downloadable Excel draft', () => {
    expect(source).toContain('/smart-cases/requirements')
    expect(source).toContain('type="radio"')
    expect(source).toContain('requirement_path')
    expect(source).toContain('生成 Excel 用例草稿')
    expect(source).toContain('/download')
    expect(source).toContain('aria-live="polite"')
  })

  it('previews completed cases on demand and clears old content when a later request fails', async () => {
    const generation = { id: 1, requirement_name: '登录需求', requirement_no: '1024', requirement_revision: '51', status: 'succeeded', llm_model: 'qwen3', case_count: 1, download_ready: true, created_at: '2026-09-09T10:00:00+08:00' }
    get.mockImplementation((path: string) => Promise.resolve({ data: path === '/knowledge-bases' ? [{ id: 1, name: '知识库' }] : path.endsWith('/requirements') ? [] : [generation, { ...generation, id: 2, status: 'failed', download_ready: false }] }))
    const wrapper = mount(SmartCaseGenerateView, { attachTo: document.body, global: { plugins: [ElementPlus] } })
    try {
      await flushPromises()
      const buttons = wrapper.findAll('button[aria-label="预览用例"]')
      expect(buttons).toHaveLength(1)
      expect(buttons[0]!.text()).toBe('')
      const downloads = wrapper.findAll('.generation-actions button[aria-label="下载 Excel"]')
      expect(downloads).toHaveLength(2)
      expect(downloads[0]!.text()).toBe('')
      expect(downloads[1]!.attributes('disabled')).toBeDefined()
      expect(get).toHaveBeenCalledWith('/smart-cases/requirements', { params: { knowledge_base_id: 1 } })
      get.mockResolvedValueOnce({ data: { ...generation, requirement_path: '需求/登录.md', referenced_sources: [{ source_path: '参考/账号.md', revision: '50' }], result_cases: [{ title: '<script>登录成功</script>', preconditions: ['账号已启用'], steps: ['输入账号', '点击登录'], expected_results: ['账号可见', '进入首页'], case_type: '功能', priority: '高' }] } })
      await buttons[0]!.trigger('click')
      await flushPromises()
      expect(get).toHaveBeenLastCalledWith('/smart-cases/generations/1')
      const dialog = document.body.querySelector('[role="dialog"]')!
      for (const text of ['TC-0001', '<script>登录成功</script>', '账号已启用', '点击登录', '进入首页', '参考/账号.md', '下载 Excel']) expect(dialog.textContent).toContain(text)
      expect(dialog.querySelector('script')).toBeNull()
      get.mockRejectedValueOnce(new Error('offline'))
      await buttons[0]!.trigger('click')
      await flushPromises()
      expect(dialog.textContent).toContain('预览加载失败')
      expect(dialog.textContent).not.toContain('登录成功')
    } finally {
      wrapper.unmount()
    }
  })
})

it('clears the selected requirement when switching libraries', async () => {
  get.mockImplementation(async (path: string, config?: { params: { knowledge_base_id: number } }) => ({ data: path === '/knowledge-bases' ? [{ id: 1, name: '账户库' }, { id: 2, name: '交易库' }] : path.endsWith('/requirements') ? [{ source_path: `REQ-${config!.params.knowledge_base_id}.md`, revision: '1', requirement_no: '123', requirement_name: config!.params.knowledge_base_id === 1 ? '账户需求' : '交易需求' }] : [] }))
  const wrapper = mount(SmartCaseGenerateView, { global: { plugins: [ElementPlus] } })
  try {
    await flushPromises()
    const selector = wrapper.findComponent({ name: 'ElSelect' })
    selector.vm.$emit('update:modelValue', 1)
    await flushPromises()
    await wrapper.get('input[type="radio"]').setValue()
    expect(wrapper.text()).toContain('生成 Excel 用例草稿')
    selector.vm.$emit('update:modelValue', 2)
    await flushPromises()
    expect(get).toHaveBeenCalledWith('/smart-cases/requirements', { params: { knowledge_base_id: 2 } })
    expect(wrapper.get<HTMLInputElement>('input[type="radio"]').element.checked).toBe(false)
    expect(wrapper.text()).toContain('交易需求')
    expect(wrapper.text()).not.toContain('账户需求')
    expect(wrapper.text()).toContain('请先从左侧选择一个需求')
  } finally { wrapper.unmount() }
})

it('submits only selected cases and fields, retains input on failure and resets on another preview', async () => {
  const generation = { id: 1, requirement_name: '登录需求', status: 'succeeded', llm_model: 'qwen3', case_count: 2, download_ready: true }
  const cases = ['登录成功', '登录失败'].map(title => ({ title, preconditions: [], steps: ['登录'], expected_results: ['显示结果'], case_type: '功能', priority: '中' }))
  get.mockImplementation((path: string) => {
    if (path.endsWith('/requirements')) return Promise.reject(new Error('索引未就绪'))
    return Promise.resolve({ data: path === '/knowledge-bases' ? [{ id: 1, name: '知识库' }] : path === '/smart-cases/generations/1' ? { ...generation, result_cases: cases, referenced_sources: [] } : [generation] })
  })
  const wrapper = mount(SmartCaseGenerateView, { attachTo: document.body, global: { plugins: [ElementPlus] } })
  try {
    await flushPromises()
    await wrapper.get('button[aria-label="预览用例"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('.revise-button').attributes('disabled')).toBeDefined()
    await wrapper.get('input[aria-label="选择用例 TC-0002"]').setValue(true)
    await wrapper.get('#revision-instruction').setValue('  名称更清晰  ')
    expect(wrapper.get('.revise-button').attributes('disabled')).toBeUndefined()
    post.mockRejectedValueOnce(new Error('offline'))
    await wrapper.get('.case-revision').trigger('submit')
    await flushPromises()
    expect(wrapper.get('.case-revision').text()).toContain('预览加载失败')
    expect(wrapper.get<HTMLTextAreaElement>('#revision-instruction').element.value).toBe('  名称更清晰  ')
    post.mockResolvedValueOnce({ data: { ...generation, id: 2, status: 'queued', download_ready: false } })
    await wrapper.get('.case-revision').trigger('submit')
    await flushPromises()
    expect(post).toHaveBeenLastCalledWith('/smart-cases/generations/1/revise', { case_indices: [1], fields: ['title'], instruction: '名称更清晰' })
    expect(wrapper.text()).toContain('#2')
    expect(wrapper.text()).toContain('排队中')
    await wrapper.get('button[aria-label="预览用例"]').trigger('click')
    await flushPromises()
    expect(wrapper.get<HTMLTextAreaElement>('#revision-instruction').element.value).toBe('')
    expect(wrapper.get<HTMLInputElement>('input[aria-label="选择用例 TC-0002"]').element.checked).toBe(false)
  } finally { wrapper.unmount() }
})
