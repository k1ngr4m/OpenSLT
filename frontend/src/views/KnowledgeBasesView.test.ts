import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import ElementPlus, { ElPagination, ElSelect, ElInputNumber, ElTree } from 'element-plus'
import KnowledgeBasesView from './KnowledgeBasesView.vue'

const { get, post, put } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }))
vi.mock('@/api/client', () => ({ api: { get, post, put }, errorMessage: () => '请求失败' }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ isAdmin: true }) }))
const base = { id: 1, name: '需求知识库', description: '业务规则', embedding_model_id: 20, embedding_model: 'embed', embedding_provider: '服务商', embedding_dimensions: 2, chunk_size: 1200, chunk_overlap: 150, top_k: 10, document_count: 1, chunk_count: 3, failed_count: 0, index_status: 'succeeded', last_success_at: '2026-09-09T10:00:00+08:00', created_at: '2026-09-09T10:00:00+08:00', updated_at: '2026-09-09T10:00:00+08:00' }
async function setup(path: string, models = true, docs?: Record<string, unknown>[]) {
  get.mockImplementation(async (url: string) => ({ data: url === '/knowledge-bases' ? [base] : url === '/model-providers' ? (models ? [{ id: 2, name: '服务商', models: [{ id: 20, kind: 'embedding', model_id: 'embed', is_active: true }] }] : []) : url.endsWith('/documents') ? docs ?? [{ source_path: 'upload/1/需求.md', name: '需求.md', origin: 'upload', upload_id: 1, size: 100, chunk_count: 3, revision: 'abc', status: 'indexed' }] : base }))
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
      expect(wrapper.get('#pane-documents').find('svn-source-panel-stub').exists()).toBe(false)
      expect(wrapper.get('#pane-documents').text()).not.toContain('SVN 配置')
      await wrapper.get('#tab-settings').trigger('click'); await flushPromises()
      expect(router.currentRoute.value.query.tab).toBe('settings')
      expect(wrapper.get('#pane-settings').get('svn-source-panel-stub').attributes('knowledgebaseid')).toBe('1')
      await wrapper.get('#tab-documents').trigger('click'); await flushPromises()
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


it('paginates, combines filters, browses recursive folders and previews indexed text', async () => {
  const docs = Array.from({ length: 25 }, (_, i) => ({ source_path: `repo/${i < 23 ? '业务/子目录' : '其他'}/文件${i}.md`, name: `文件${i}.md`, origin: 'svn', size: 2 * 1024 * 1024, chunk_count: i, revision: i < 22 ? 'r1' : 'r2', status: i === 21 ? 'failed' : 'indexed' }))
  const { wrapper } = await setup('/knowledge-bases/1?tab=documents', true, docs)
  try {
    const rows = () => wrapper.findAll('.el-table__body-wrapper tbody tr')
    expect(rows()).toHaveLength(20)
    expect(wrapper.text()).toContain('2.0 MB')
    const pagination = wrapper.findComponent(ElPagination)
    pagination.vm.$emit('update:current-page', 2); await flushPromises()
    expect(rows()).toHaveLength(5)
    const selects = wrapper.findAllComponents(ElSelect)
    const choose = async (label: string, value: string) => { selects.find(select => select.props('ariaLabel') === label)!.vm.$emit('update:modelValue', value); await flushPromises() }
    await choose('文档版本', 'r1')
    expect(pagination.props('currentPage')).toBe(1)
    expect(pagination.props('total')).toBe(22)
    await choose('文档状态', 'failed')
    expect(rows()).toHaveLength(1)
    expect(rows()[0]!.text()).toContain('文件21.md')
    await choose('文档状态', '')
    const inputs = wrapper.findAllComponents(ElInputNumber)
    inputs.find(input => input.props('ariaLabel') === '最少分块')!.vm.$emit('update:modelValue', 20)
    inputs.find(input => input.props('ariaLabel') === '最多分块')!.vm.$emit('update:modelValue', 20)
    await flushPromises()
    expect(rows()).toHaveLength(1)
    expect(rows()[0]!.text()).toContain('文件20.md')
    await choose('文档版本', '')
    for (const label of ['最少分块', '最多分块']) inputs.find(input => input.props('ariaLabel') === label)!.vm.$emit('update:modelValue', undefined)
    const tree = wrapper.findComponent(ElTree)
    expect(tree.props('data')![0].children[0].children[0].children[0].label).toBe('子目录')
    tree.vm.$emit('node-click', { path: 'repo/业务' }, {}, {}, new MouseEvent('click')); await flushPromises()
    expect(pagination.props('total')).toBe(23)
    get.mockResolvedValue({ data: { revision: 'r1', total: 7, page_size: 5, chunks: [{ chunk_no: 0, content: '<script>正文</script>' }] } })
    await rows()[0]!.findAll('button').find(button => button.text() === '预览')!.trigger('click'); await flushPromises()
    expect(get).toHaveBeenLastCalledWith('/knowledge-bases/1/documents/preview', { params: { source_path: docs[0]!.source_path, page: 1 } })
    expect(document.body.textContent).toContain('<script>正文</script>')
    expect(document.querySelector('.document-preview script')).toBeNull()
    const previewPagination = wrapper.findAllComponents(ElPagination)[1]!
    previewPagination.vm.$emit('update:current-page', 2)
    previewPagination.vm.$emit('current-change', 2); await flushPromises()
    expect(get).toHaveBeenLastCalledWith('/knowledge-bases/1/documents/preview', { params: { source_path: docs[0]!.source_path, page: 2 } })
  } finally { wrapper.unmount() }
})
