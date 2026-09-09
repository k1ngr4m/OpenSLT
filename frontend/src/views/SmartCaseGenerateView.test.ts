import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import SmartCaseGenerateView from './SmartCaseGenerateView.vue'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/api/client', () => ({ api: { get }, errorMessage: () => '预览加载失败' }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ isAdmin: false }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

const source = readFileSync(resolve(process.cwd(), 'src/views/SmartCaseGenerateView.vue'), 'utf8')

describe('SmartCaseGenerateView', () => {
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
    get.mockImplementation((path: string) => Promise.resolve({ data: path.endsWith('/requirements') ? [] : [generation, { ...generation, id: 2, status: 'failed', download_ready: false }] }))
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
      expect(get).toHaveBeenCalledTimes(2)
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
