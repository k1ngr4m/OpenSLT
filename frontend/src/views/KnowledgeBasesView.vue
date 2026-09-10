<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Collection, Plus, Search, Upload, Refresh, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from '@/ui/elementPlusServices'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { formatBeijingDateTime } from '@/utils/time'
import { formatBytes } from '@/utils/runDetail'
import SvnSourcePanel from '@/components/SvnSourcePanel.vue'
import type { components } from '@/types/api.generated'

type Base = components['schemas']['KnowledgeBaseOut']
type Document = components['schemas']['KnowledgeDocumentOut']
type Provider = components['schemas']['ModelProviderOut']
const route = useRoute(), router = useRouter(), auth = useAuthStore()
const bases = ref<Base[]>([]), current = ref<Base | null>(null), documents = ref<Document[]>([]), providers = ref<Provider[]>([])
const loading = ref(false), saving = ref(false), error = ref(''), createVisible = ref(false)
const query = ref(''), origin = ref(''), searchQuery = ref(''), searched = ref(false), searching = ref(false), searchError = ref('')
const searchResults = ref<components['schemas']['KnowledgeSearchResult'][]>([])
const uploadInput = ref<HTMLInputElement>()
const uploading = ref(false)
const id = computed(() => route.params.id ? Number(route.params.id) : null)
const tabs = ['overview', 'documents', 'search', 'settings']
const tab = computed({ get: () => tabs.includes(String(route.query.tab)) ? String(route.query.tab) : 'overview', set: value => router.replace({ query: { tab: value } }) })
const form = reactive({ name: '', description: '', embedding_model_id: null as number | null, chunk_size: 1200, chunk_overlap: 150, top_k: 10 })
const createForm = reactive({ name: '', description: '', embedding_model_id: null as number | null })
const modelOptions = computed(() => providers.value.flatMap(provider => (provider.models || []).filter(model => model.kind === 'embedding').map(model => ({ id: model.id, label: `${provider.name} / ${model.model_id}`, active: model.is_active }))))
const visibleBases = computed(() => bases.value.filter(base => `${base.name} ${base.description}`.toLowerCase().includes(query.value.toLowerCase())))
const revision = ref(''), documentStatus = ref(''), folder = ref('')
const minChunks = ref<number>(), maxChunks = ref<number>()
const page = ref(1), pageSize = ref(20)
const revisions = computed(() => [...new Set(documents.value.map(doc => doc.revision))].sort())
const documentStatuses = computed(() => [...new Set(documents.value.map(doc => doc.status))].sort())
const visibleDocuments = computed(() => documents.value.filter(doc =>
  (!origin.value || doc.origin === origin.value) && (!revision.value || doc.revision === revision.value) &&
  (!documentStatus.value || doc.status === documentStatus.value) &&
  (minChunks.value == null || doc.chunk_count >= minChunks.value) &&
  (maxChunks.value == null || doc.chunk_count <= maxChunks.value) &&
  (!folder.value || doc.source_path.startsWith(folder.value + '/')) &&
  `${doc.name} ${doc.source_path}`.toLowerCase().includes(query.value.toLowerCase())))
const pagedDocuments = computed(() => visibleDocuments.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
type Folder = { path: string; label: string; children: Folder[] }
const folders = computed(() => {
  const root: Folder = { path: '', label: '全部文件夹', children: [] }
  const nodes = new Map<string, Folder>([['', root]])
  for (const doc of documents.value) {
    const parts = doc.source_path.split('/'); parts.pop()
    let parent = root
    for (const part of parts) {
      const path = parent.path ? `${parent.path}/${part}` : part
      let node = nodes.get(path)
      if (!node) { node = { path, label: part || '/', children: [] }; nodes.set(path, node); parent.children.push(node) }
      parent = node
    }
  }
  return [root]
})
watch([query, origin, revision, documentStatus, minChunks, maxChunks, folder, pageSize], () => { page.value = 1 })
watch(() => visibleDocuments.value.length, total => { page.value = Math.min(page.value, Math.max(1, Math.ceil(total / pageSize.value))) })
type Preview = { revision: string; total: number; page_size: number; chunks: { chunk_no: number; content: string }[] }
const previewVisible = ref(false), previewLoading = ref(false), previewError = ref(''), previewPage = ref(1)
const previewDoc = ref<Document>(), preview = ref<Preview>()
let previewSequence = 0
async function loadPreview() {
  if (!previewDoc.value) return
  const sequence = ++previewSequence
  previewLoading.value = true; previewError.value = ''; preview.value = undefined
  try {
    const response = await api.get<Preview>(`/knowledge-bases/${id.value}/documents/preview`, { params: { source_path: previewDoc.value.source_path, page: previewPage.value } })
    if (sequence === previewSequence) preview.value = response.data
  } catch (cause) { if (sequence === previewSequence) previewError.value = errorMessage(cause) }
  finally { if (sequence === previewSequence) previewLoading.value = false }
}
function openPreview(doc: Document) {
  previewDoc.value = doc; previewPage.value = 1; previewVisible.value = true
  void loadPreview()
}
const busy = computed(() => !!current.value?.task_id)
const statusNames: Record<string, string> = { never: '尚未索引', stale: '待重建', queued: '排队中', running: '索引中', cancelling: '正在取消', cancelled: '已取消', succeeded: '已就绪', failed: '失败', pending: '待处理', indexed: '已索引', deleting: '待删除' }
const label = (status: string) => statusNames[status] || status
let timer: ReturnType<typeof setTimeout> | undefined
let loadSequence = 0

async function load(fillForm = false) {
  const sequence = ++loadSequence, selectedId = id.value
  loading.value = fillForm
  try {
    if (selectedId) {
      const [baseResponse, documentResponse] = await Promise.all([api.get<Base>(`/knowledge-bases/${selectedId}`), api.get<Document[]>(`/knowledge-bases/${selectedId}/documents`)])
      if (sequence !== loadSequence) return
      current.value = baseResponse.data
      documents.value = documentResponse.data
      if (fillForm) Object.assign(form, { name: current.value.name, description: current.value.description, embedding_model_id: current.value.embedding_model_id, chunk_size: current.value.chunk_size, chunk_overlap: current.value.chunk_overlap, top_k: current.value.top_k })
    } else {
      const response = await api.get<Base[]>('/knowledge-bases')
      if (sequence !== loadSequence) return
      bases.value = response.data
    }
    error.value = ''
  } catch (cause) { if (sequence === loadSequence) error.value = errorMessage(cause) }
  finally {
    if (sequence === loadSequence) {
      loading.value = false
      clearTimeout(timer)
      timer = setTimeout(() => void load(), 5000)
    }
  }
}
async function loadModels() {
  if (auth.isAdmin) providers.value = (await api.get<Provider[]>('/model-providers', { params: { kind: 'embedding' } })).data
}
async function openCreate() {
  Object.assign(createForm, { name: '', description: '', embedding_model_id: null })
  createVisible.value = true
  try { await loadModels(); createForm.embedding_model_id = modelOptions.value.find(model => model.active)?.id ?? null }
  catch (cause) { ElMessage.error(errorMessage(cause)) }
}
async function create() {
  if (!createForm.name.trim() || !createForm.embedding_model_id) return
  saving.value = true
  try {
    const response = await api.post<Base>('/knowledge-bases', createForm)
    createVisible.value = false
    await router.push(`/knowledge-bases/${response.data.id}`)
  } catch (cause) { ElMessage.error(errorMessage(cause)) }
  finally { saving.value = false }
}
async function save() {
  if (!form.name.trim() || form.chunk_overlap >= form.chunk_size) { ElMessage.error('请填写名称，且分块重叠须小于分块大小'); return }
  saving.value = true
  try { current.value = (await api.put<Base>(`/knowledge-bases/${id.value}`, form)).data; ElMessage.success(current.value.index_status === 'stale' ? '设置已保存，请重建索引' : '设置已保存') }
  catch (cause) { ElMessage.error(errorMessage(cause)) }
  finally { saving.value = false }
}
async function index(cancel = false) {
  saving.value = true
  try { await api.post(`/knowledge-bases/${id.value}/index${cancel ? '/cancel' : ''}`); await load() }
  catch (cause) { ElMessage.error(errorMessage(cause)) }
  finally { saving.value = false }
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files?.length) return
  const data = new FormData()
  for (const file of Array.from(input.files)) data.append('files', file)
  uploading.value = true
  try { await api.post(`/knowledge-bases/${id.value}/documents`, data, { timeout: 0 }); ElMessage.success('文件已上传，正在建立索引'); await load() }
  catch (cause) { ElMessage.error(errorMessage(cause)) }
  finally { input.value = ''; uploading.value = false }
}
async function remove(doc: Document) {
  try { await ElMessageBox.confirm(`删除“${doc.name}”？索引更新成功后完成删除。`, '删除上传文档', { type: 'warning' }) }
  catch { return }
  try { await api.delete(`/knowledge-bases/${id.value}/documents/${doc.upload_id}`); await load() }
  catch (cause) { ElMessage.error(errorMessage(cause)) }
}
async function search() {
  if (!searchQuery.value.trim()) return
  const selectedId = id.value
  searching.value = true; searchError.value = ''; searched.value = false; searchResults.value = []
  try {
    const response = await api.post(`/knowledge-bases/${selectedId}/search`, { query: searchQuery.value.trim() })
    if (selectedId === id.value) { searchResults.value = response.data.results; searched.value = true }
  } catch (cause) { if (selectedId === id.value) searchError.value = errorMessage(cause) }
  finally { if (selectedId === id.value) searching.value = false }
}
watch(id, () => {
  ++previewSequence; previewVisible.value = false; revision.value = ''; documentStatus.value = ''; minChunks.value = undefined; maxChunks.value = undefined; folder.value = ''; page.value = 1;
  current.value = null; documents.value = []; query.value = ''; origin.value = ''; searchQuery.value = ''; searchResults.value = []; searched.value = false; searchError.value = ''; searching.value = false
  void load(true)
  void loadModels().catch(cause => { error.value = errorMessage(cause) })
}, { immediate: true })
onBeforeUnmount(() => { ++previewSequence; ++loadSequence; clearTimeout(timer) })
</script>

<template>
  <div v-loading="loading" class="page knowledge-page">
    <header class="page-header">
      <div><span class="page-kicker">知识管理</span>
        <h1 v-if="!id" class="page-title">知识库</h1>
        <template v-else><el-breadcrumb class="knowledge-breadcrumb"><el-breadcrumb-item :to="'/knowledge-bases'">知识库</el-breadcrumb-item><el-breadcrumb-item>{{ current?.name || '加载中' }}</el-breadcrumb-item></el-breadcrumb></template>
        <p class="muted">{{ id ? current?.description || '管理当前知识库的文档、检索与设置' : '集中管理团队知识，让问答与用例生成有据可依' }}</p>
      </div>
      <el-button v-if="!id && auth.isAdmin" type="primary" :icon="Plus" @click="openCreate">创建知识库</el-button>
      <el-tag v-if="current" effect="plain" :type="current.index_status === 'failed' ? 'danger' : current.index_status === 'succeeded' ? 'success' : 'info'">{{ label(current.index_status) }}</el-tag>
    </header>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <template v-if="!id">
      <el-input v-model="query" class="list-search" clearable placeholder="搜索知识库名称或描述" aria-label="搜索知识库" :prefix-icon="Search" />
      <div v-if="visibleBases.length" class="base-grid">
        <router-link v-for="base in visibleBases" :key="base.id" :to="`/knowledge-bases/${base.id}`" class="card base-card">
          <div class="base-heading"><span class="base-icon"><el-icon :size="24"><Collection /></el-icon></span><el-tag size="small" effect="plain">{{ label(base.index_status) }}</el-tag></div>
          <h2>{{ base.name }}</h2><p class="base-description">{{ base.description || '暂无描述' }}</p>
          <p class="model-name">{{ base.embedding_provider ? `${base.embedding_provider} / ` : '' }}{{ base.embedding_model || '待配置 Embedding' }}</p>
          <footer><span>{{ base.document_count }} 份文档</span><span>{{ formatBeijingDateTime(base.updated_at) }}</span></footer>
        </router-link>
      </div>
      <el-empty v-else :description="query ? '没有匹配的知识库' : '还没有知识库，创建后即可导入文档'" />
    </template>
    <el-tabs v-else-if="current" v-model="tab" class="detail-tabs">
      <el-tab-pane label="概览" name="overview">
        <div class="overview-grid">
          <section class="card section"><h2>基本信息</h2><dl class="info-list"><div><dt>名称</dt><dd>{{ current.name }}</dd></div><div><dt>描述</dt><dd>{{ current.description || '暂无描述' }}</dd></div><div><dt>创建时间</dt><dd>{{ formatBeijingDateTime(current.created_at) }}</dd></div><div><dt>更新时间</dt><dd>{{ formatBeijingDateTime(current.updated_at) }}</dd></div><div><dt>最近成功索引</dt><dd>{{ current.last_success_at ? formatBeijingDateTime(current.last_success_at) : '尚未索引' }}</dd></div></dl></section>
          <div class="overview-right"><section class="card section"><h2>统计信息</h2><div class="stats"><div><strong>{{ current.document_count }}</strong><span>文档数量</span></div><div><strong>{{ current.chunk_count }}</strong><span>分块数量</span></div><div><strong>{{ current.failed_count }}</strong><span>处理失败</span></div></div></section><section class="card section"><h2>Embedding 模型</h2><p>{{ current.embedding_provider || '未配置' }} / {{ current.embedding_model || '未选择模型' }}</p><p class="muted">{{ current.embedding_dimensions ? `${current.embedding_dimensions} 维` : '请在设置中补选模型' }}</p></section></div>
        </div>
        <el-alert v-if="current.last_error" :title="current.last_error" type="error" show-icon :closable="false" />
      </el-tab-pane>
      <el-tab-pane :label="`文档管理 (${current.document_count})`" name="documents">
        <section class="card section">
          <div class="document-toolbar"><div class="filters"><el-input v-model="query" clearable placeholder="搜索文件名称或路径" aria-label="搜索文档" :prefix-icon="Search" /><el-select v-model="origin" aria-label="文档来源" placeholder="全部来源"><el-option label="全部来源" value="" /><el-option label="SVN" value="svn" /><el-option label="手动上传" value="upload" /></el-select>
              <el-select v-model="revision" clearable filterable aria-label="文档版本" placeholder="全部版本"><el-option label="全部版本" value="" /><el-option v-for="value in revisions" :key="value" :label="value" :value="value" /></el-select>
              <el-select v-model="documentStatus" clearable aria-label="文档状态" placeholder="全部状态"><el-option label="全部状态" value="" /><el-option v-for="value in documentStatuses" :key="value" :label="label(value)" :value="value" /></el-select>
              <el-input-number v-model="minChunks" :min="0" :precision="0" :controls="false" aria-label="最少分块" placeholder="最少分块" />
              <el-input-number v-model="maxChunks" :min="0" :precision="0" :controls="false" aria-label="最多分块" placeholder="最多分块" />
            </div>
            <div class="actions"><template v-if="auth.isAdmin"><input ref="uploadInput" class="file-input" type="file" multiple accept=".txt,.md,.csv,.json,.yaml,.yml,.htm,.html,.doc,.docx,.xls,.xlsx,.pdf" aria-label="上传文档" @change="upload" /><el-button :icon="Upload" :disabled="busy" :loading="uploading" @click="uploadInput?.click()">上传文件</el-button><el-button v-if="busy" :loading="saving" @click="index(true)">取消索引</el-button><el-button v-else type="primary" :icon="Refresh" :loading="saving" @click="index()">同步 / 重试索引</el-button></template></div>
          </div>
          <p class="muted">支持 PDF、Word、Excel、文本及结构化文件，DOCX、XLSX 单文件最大 500 MiB，其他格式最大 50 MiB，提取文本最多 1000 万字符。SVN 文档通过同步范围管理，上传文件可单独删除。</p>
          <div class="document-browser"><aside class="folder-panel" aria-label="文档文件夹"><h3>文件夹</h3><el-tree :data="folders" node-key="path" :current-node-key="folder" :default-expanded-keys="['']" highlight-current :expand-on-click-node="false" @node-click="(node: Folder) => folder = node.path" /></aside><div class="document-list">
          <p v-if="folder" class="document-path">当前目录：{{ folder }}（包含所有子目录）<el-button text @click="folder = ''">全部文件夹</el-button></p>
          <el-table v-if="visibleDocuments.length" :data="pagedDocuments" row-key="source_path"><el-table-column label="文档" min-width="240"><template #default="{ row }"><strong>{{ row.name }}</strong><small class="document-path">{{ row.source_path }}</small></template></el-table-column><el-table-column label="来源" width="110"><template #default="{ row }">{{ row.origin === 'svn' ? 'SVN' : '手动上传' }}</template></el-table-column><el-table-column prop="revision" label="版本" width="130" /><el-table-column label="大小" width="100"><template #default="{ row }">{{ formatBytes(row.size) }}</template></el-table-column><el-table-column prop="chunk_count" label="分块" width="80" /><el-table-column label="状态" min-width="130"><template #default="{ row }"><el-tag :type="row.status === 'failed' ? 'danger' : 'info'">{{ label(row.status) }}</el-tag><small v-if="row.error" class="document-path">{{ row.error }}</small></template></el-table-column><el-table-column label="操作" width="150"><template #default="{ row }"><el-button text type="primary" @click="openPreview(row)">预览</el-button><el-button v-if="auth.isAdmin && row.upload_id" :icon="Delete" text type="danger" :disabled="busy || uploading" :aria-label="`删除 ${row.name}`" @click="remove(row)" /></template></el-table-column></el-table>
          <el-empty v-else :description="documents.length ? '没有匹配的文档' : '上传文件或配置 SVN 同步，开始构建知识库'" :image-size="80" />
          <el-pagination v-if="visibleDocuments.length" v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[20, 50, 100]" :total="visibleDocuments.length" layout="total, sizes, prev, pager, next, jumper" />
          </div></div>
        </section>
      </el-tab-pane>
      <el-tab-pane label="知识库检索" name="search"><section class="card section"><h2>检索当前知识库</h2><p class="muted">使用 {{ current.embedding_model || '所选模型' }} 与关键词混合检索，最多返回 {{ current.top_k }} 个片段。</p><el-alert v-if="!current.last_success_at || current.index_status === 'stale'" title="知识库尚未完成索引或设置已变更，请先建立索引。" type="info" :closable="false" /><form class="search-row" @submit.prevent="search"><el-input v-model="searchQuery" placeholder="输入需求主题、业务规则或关键词" aria-label="检索问题" clearable /><el-button native-type="submit" type="primary" :icon="Search" :loading="searching" :disabled="!searchQuery.trim() || !current.last_success_at || current.index_status === 'stale'">检索</el-button></form><el-alert v-if="searchError" :title="searchError" type="error" :closable="false" /><div v-if="searchResults.length" class="search-results" aria-live="polite"><article v-for="(hit, index) in searchResults" :key="index"><div><strong>{{ hit.source_path }}</strong><el-tag size="small">{{ hit.revision }}</el-tag><span>{{ hit.score.toFixed(3) }}</span></div><p>{{ hit.snippet }}</p></article></div><el-empty v-else-if="searched" description="未找到匹配的文档片段" :image-size="80" /></section></el-tab-pane>
      <el-tab-pane label="设置" name="settings" lazy><section class="card section settings-section"><h2>知识库设置</h2><el-form label-position="top" :disabled="!auth.isAdmin || busy" @submit.prevent="save"><el-form-item label="名称" required><el-input v-model="form.name" :maxlength="128" /></el-form-item><el-form-item label="描述"><el-input v-model="form.description" type="textarea" :rows="3" :maxlength="2000" /></el-form-item><div class="form-grid"><el-form-item label="分块大小（字符）"><el-input-number v-model="form.chunk_size" :min="1" :max="100000" /></el-form-item><el-form-item label="分块重叠（字符）"><el-input-number v-model="form.chunk_overlap" :min="0" :max="form.chunk_size - 1" /></el-form-item><el-form-item label="检索数量"><el-input-number v-model="form.top_k" :min="1" :max="50" /></el-form-item><el-form-item label="Embedding 模型"><el-select v-if="!current.embedding_model_id" v-model="form.embedding_model_id" placeholder="请选择已保存的模型"><el-option v-for="model in modelOptions" :key="model.id" :label="model.label" :value="model.id" /></el-select><el-input v-else :model-value="`${current.embedding_provider} / ${current.embedding_model}`" disabled /></el-form-item></div><p class="muted">Embedding 模型绑定后固定。修改分块参数后须重建索引；检索数量保存后立即生效。</p><el-button v-if="auth.isAdmin" type="primary" native-type="submit" :loading="saving">保存设置</el-button><el-button v-if="auth.isAdmin && current.index_status === 'stale'" :loading="saving" @click="index()">重建索引</el-button></el-form></section>
        <SvnSourcePanel :key="current.id" :knowledge-base-id="current.id" />
      </el-tab-pane>
    </el-tabs>
    <el-dialog v-model="previewVisible" :title="`文档预览 · ${previewDoc?.name || ''}`" width="min(960px, 94vw)">
      <div v-loading="previewLoading" class="document-preview">
        <p class="document-path">{{ previewDoc?.source_path }}</p><p class="muted">已索引的文本正文，按分块分页展示；相邻分块可能包含重叠内容。原文件的图片和排版不在此预览中。</p>
        <el-alert v-if="previewError" :title="previewError" type="error" :closable="false" />
        <template v-if="preview"><p>版本：{{ preview.revision }}</p><article v-for="chunk in preview.chunks" :key="chunk.chunk_no"><small class="muted">分块 {{ chunk.chunk_no + 1 }}</small><pre>{{ chunk.content }}</pre></article><el-empty v-if="!preview.total" description="文档没有可预览的文本" /><el-pagination v-if="preview.total" v-model:current-page="previewPage" :page-size="preview.page_size" :total="preview.total" layout="total, prev, pager, next" @current-change="loadPreview" /></template>
      </div>
    </el-dialog>
    <el-dialog v-model="createVisible" title="创建知识库" width="min(600px, 94vw)" :close-on-click-modal="false"><el-form label-position="top" @submit.prevent="create"><el-form-item label="知识库名称" required><el-input v-model="createForm.name" autofocus :maxlength="128" placeholder="例如：产品需求知识库" /></el-form-item><el-form-item label="描述"><el-input v-model="createForm.description" type="textarea" :rows="4" :maxlength="2000" placeholder="说明知识库的内容与用途" /></el-form-item><el-form-item label="Embedding 模型" required><el-select v-model="createForm.embedding_model_id" placeholder="选择已保存的 Embedding 模型" class="full-width"><el-option v-for="model in modelOptions" :key="model.id" :label="model.label" :value="model.id" /></el-select></el-form-item><p class="muted">模型创建后无法修改，更换模型请创建新的知识库。</p><el-alert v-if="!modelOptions.length" title="暂无可用的 Embedding 模型" type="info" :closable="false"><router-link to="/models">前往模型管理添加模型</router-link></el-alert></el-form><template #footer><el-button @click="createVisible = false">取消</el-button><el-button type="primary" :disabled="!createForm.name.trim() || !createForm.embedding_model_id" :loading="saving" @click="create">创建</el-button></template></el-dialog>
  </div>
</template>

<style scoped>
.document-browser{display:grid;grid-template-columns:220px minmax(0,1fr);gap:18px}.document-list{min-width:0}.folder-panel{overflow:auto;border-right:1px solid var(--ui-border);padding-right:12px;max-height:650px}.folder-panel h3{font-size:14px}.el-pagination{margin-top:18px;overflow:auto}.document-preview{min-height:100px;max-height:65vh;overflow:auto}.document-preview pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.8}.document-toolbar .filters{flex-wrap:wrap;flex:1}.document-toolbar .filters>*{width:150px}.document-toolbar .filters>.el-input{width:240px}
@media(max-width:767px){.document-browser{grid-template-columns:1fr}.folder-panel{max-height:220px;border-right:0;border-bottom:1px solid var(--ui-border)}}

.knowledge-page{max-width:1500px}.knowledge-breadcrumb{font-size:24px;line-height:1.6}.list-search{max-width:360px;margin:8px 0 24px}.base-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:18px}.base-card{display:block;padding:24px;text-decoration:none;color:var(--ui-text-primary);transition:border-color .15s,box-shadow .15s}.base-card:hover,.base-card:focus-visible{border-color:var(--ui-primary);box-shadow:0 4px 16px #00000008;outline:2px solid var(--ui-primary)}.base-heading,.base-card footer{display:flex;justify-content:space-between;align-items:center;gap:12px}.base-icon{display:grid;place-items:center;width:48px;height:48px;border-radius:12px;background:var(--ui-primary-soft);color:var(--ui-primary)}.base-card h2{font-size:19px;margin:20px 0 10px;overflow-wrap:anywhere}.base-description{height:42px;overflow:hidden;color:var(--ui-text-secondary);font-size:13px;line-height:1.6}.model-name{font-size:12px;overflow-wrap:anywhere}.base-card footer{border-top:1px solid var(--ui-border);margin-top:20px;padding-top:16px;font-size:11px;color:var(--ui-text-secondary)}.detail-tabs{margin-top:22px}.section{padding:24px;margin-top:16px}.section h2{font-size:18px;margin:0 0 24px}.overview-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.overview-right{display:grid;gap:4px}.info-list{margin:0}.info-list>div{display:grid;grid-template-columns:120px 1fr;gap:18px;padding:16px 0}.info-list dt,.muted{color:var(--ui-text-secondary)}.info-list dd{margin:0;overflow-wrap:anywhere}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.stats>div{display:grid;text-align:center;gap:16px;padding:22px 10px;background:var(--ui-canvas);border-radius:8px}.stats strong{font-size:34px;font-weight:600}.stats span{font-size:13px;color:var(--ui-text-secondary)}.document-toolbar,.filters,.actions,.search-row{display:flex;align-items:center;gap:10px}.document-toolbar{justify-content:space-between;flex-wrap:wrap}.filters{flex:1;min-width:250px}.filters>.el-select{width:130px;flex-shrink:0}.actions{flex-wrap:wrap}.actions .el-button{margin:0}.file-input{display:none}.document-path{display:block;margin-top:6px;color:var(--ui-text-secondary);overflow-wrap:anywhere}.search-row{margin:20px 0}.search-results article{padding:20px 0;border-bottom:1px solid var(--ui-border)}.search-results article>div{display:flex;align-items:center;gap:12px;overflow-wrap:anywhere}.search-results article strong{flex:1}.search-results article p{white-space:pre-wrap;line-height:1.7}.settings-section{max-width:1000px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px 24px}.form-grid :deep(.el-input-number){width:100%}.full-width{width:100%}.muted{font-size:13px;line-height:1.6}.knowledge-page>.el-alert{margin:16px 0}@media(max-width:800px){.overview-grid,.form-grid{grid-template-columns:1fr}.section{padding:18px}.info-list>div{grid-template-columns:90px 1fr}.search-results article>div{flex-wrap:wrap}.stats strong{font-size:28px}.knowledge-breadcrumb{font-size:20px}.filters{width:100%}.actions{margin-top:8px}}
</style>
