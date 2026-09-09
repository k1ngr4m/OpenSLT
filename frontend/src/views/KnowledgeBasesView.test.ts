import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import ElementPlus from 'element-plus'
import KnowledgeBasesView from './KnowledgeBasesView.vue'

const { get, post, put } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }))
vi.mock('@/api/client', () => ({ api: { get, post, put }, errorMessage: () => '请求失败' }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ isAdmin: true }) }))
const base = { id: 1, name: '需求知识库', description: '业务规则', embedding_model_id: 20, embedding_model: 'embed', embedding_provider: '服务商', embedding_dimensions: 2, chunk_size: 1200, chunk_overlap: 150, top_k: 10, document_count: 1, chunk_count: 3, failed_count: 0, index_status: 'succeeded', last_success_at: '2026-09-09T10:00:00+08:00', created_at: '2026-09-09T10:00:00+08:00', updated_at: '2026-09-09T10:00:00+08:00' }
async function setup(path: string, models = true) {
  get.mockImplementation(async (url: string) => ({ data: url === '/knowledge-bases' ? [base] : url === '/model-providers' ? (models ? [{ id: 2, name: '服务商', models: [{ id: 20, kind: 'embedding', model_id: 'embed', is_active: true }] }] : []) : url.endsWith('/documents') ? [{ source_path: 'upload/1/需求.md', name: '需求.md', origin: 'upload', upload_id: 1, size: 100, chunk_count: 3, revision: 'abc', status: 'indexed' }] : base }))
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/knowledge-bases/:id?', component: KnowledgeBasesView }, { path: '/models', component: { template: '<div />' } }] })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(KnowledgeBasesView, { attachTo: document.body, global: { plugins: [ElementPlus, router], stubs: { SvnSourcePanel: true } } })
  await flushPromises()
  return { wrapper, router }
}
afterEach(() => { vi.clearAllMocks(); document.body.innerHTML = '' })

describe('knowledge bases', () => {
  it.each([true, false])('creates with saved models and blocks creation without them: %s', async models => {
    const { wrapper, router } = await setup('/knowledge-bases', models)
    try {
      expect(wrapper.get('.base-card').text()).toContain('需求知识库')
      await wrapper.findAll('button').find(button => button.text() === '创建知识库')!.trigger('click')
      await flushPromises()
      const name = document.querySelector<HTMLInputElement>('input[placeholder="例如：产品需求知识库"]')!
      name.value = '新的知识库'; name.dispatchEvent(new Event('input', { bubbles: true }))
      await flushPromises()
      const create = Array.from(document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')).find(button => button.textContent?.trim() === '创建')!
      expect(create.disabled).toBe(!models)
      if (models) {
        post.mockResolvedValue({ data: { ...base, id: 2 } })
        create.click(); await flushPromises()
        expect(post).toHaveBeenCalledWith('/knowledge-bases', { name: '新的知识库', description: '', embedding_model_id: 20 })
        expect(router.currentRoute.value.path).toBe('/knowledge-bases/2')
      } else expect(document.body.textContent).toContain('前往模型管理添加模型')
    } finally { wrapper.unmount() }
  })

  it('routes tabs, uploads documents, and scopes retrieval to the selected library', async () => {
    const { wrapper, router } = await setup('/knowledge-bases/1?tab=documents')
    try {
      expect(wrapper.get('#tab-documents').attributes('aria-selected')).toBe('true')
      expect(wrapper.text()).toContain('需求.md')
      post.mockResolvedValue({ data: { task_id: 1, status: 'queued' } })
      const upload = wrapper.get<HTMLInputElement>('input[type=file]')
      Object.defineProperty(upload.element, 'files', { configurable: true, value: [new File(['内容'], '需求.md', { type: 'text/plain' })] })
      await upload.trigger('change'); await flushPromises()
      expect(post).toHaveBeenCalledWith('/knowledge-bases/1/documents', expect.any(FormData), { timeout: 0 })
      await wrapper.get('#tab-search').trigger('click'); await flushPromises()
      expect(router.currentRoute.value.query.tab).toBe('search')
      await wrapper.get('input[aria-label="检索问题"]').setValue('业务规则')
      post.mockResolvedValue({ data: { results: [{ source_path: '需求.md', revision: 'abc', snippet: '权限校验', score: 0.9 }] } })
      await wrapper.get('form.search-row').trigger('submit'); await flushPromises()
      expect(post).toHaveBeenLastCalledWith('/knowledge-bases/1/search', { query: '业务规则' })
      expect(wrapper.text()).toContain('权限校验')
      await router.push('/knowledge-bases/2?tab=search'); await flushPromises()
      expect(wrapper.get<HTMLInputElement>('input[aria-label="检索问题"]').element.value).toBe('')
      expect(wrapper.text()).not.toContain('权限校验')
    } finally { wrapper.unmount() }
  })
})
