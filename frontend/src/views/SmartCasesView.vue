<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from '@/ui/elementPlusServices'
import { Refresh, Connection, Delete, Lock, Plus, Search } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { formatBeijingDateTime } from '@/utils/time'

interface KnowledgeSource {
  configured: boolean
  repository_urls: string[]
  repository_url: string
  username: string
  has_password: boolean
  include_paths: string[]
  sync_interval_minutes: number
  enabled: boolean
  allow_insecure_http: boolean
  updated_at: string | null
}

interface SyncStatus {
  configured: boolean
  client_ready: boolean
  svn_version: string | null
  embedding_model: string | null
  embedding_dimensions: number | null
  status: string
  task_id: number | null
  last_attempt_at: string | null
  last_success_at: string | null
  revisions: Record<string, string>
  file_count: number
  failed_file_count: number
  changes: Record<string, number>
  error: string | null
}

const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const syncing = ref(false)
const searching = ref(false)
const searchQuery = ref('')
const searchResults = ref<Array<{ source_path: string; revision: string; snippet: string; score: number }>>([])
const source = ref<KnowledgeSource | null>(null)
const status = ref<SyncStatus | null>(null)
const form = reactive({
  repository_urls: [''],
  username: '',
  password: '',
  include_paths_text: '',
  sync_interval_minutes: 30,
  enabled: true,
  allow_insecure_http: false,
})
let timer: ReturnType<typeof setInterval> | undefined

const repositoryUrls = () => form.repository_urls.map(item => item.trim()).filter(Boolean)
const svnPaths = () => form.include_paths_text.split(/\r?\n/).map(item => item.trim()).filter(Boolean)
const isHttp = computed(() => repositoryUrls().some(item => item.toLowerCase().startsWith('http://')))
const isBusy = computed(() => ['queued', 'running'].includes(status.value?.status || ''))
const statusText: Record<string, string> = {
  unconfigured: '未配置', never: '尚未同步', stale: '配置已变更', queued: '排队中', running: '同步中', succeeded: '同步成功', failed: '同步失败',
}

function addRepositoryUrl() {
  form.repository_urls.push('')
}

function removeRepositoryUrl(index: number) {
  if (form.repository_urls.length > 1) form.repository_urls.splice(index, 1)
}

function payload() {
  return {
    repository_urls: repositoryUrls(),
    username: form.username.trim(),
    password: form.password || null,
    include_paths: svnPaths(),
    sync_interval_minutes: 30,
    enabled: form.enabled,
    allow_insecure_http: form.allow_insecure_http,
  }
}

async function load(showError = true) {
  loading.value = !source.value
  try {
    const [sourceResponse, statusResponse] = await Promise.all([
      api.get<KnowledgeSource>('/smart-cases/knowledge-source'),
      api.get<SyncStatus>('/smart-cases/knowledge-source/sync-status'),
    ])
    source.value = sourceResponse.data
    status.value = statusResponse.data
    if (!form.repository_urls.some(Boolean) && source.value.configured) {
      Object.assign(form, {
        repository_urls: source.value.repository_urls?.length ? [...source.value.repository_urls] : [source.value.repository_url],
        username: source.value.username,
        password: '',
        include_paths_text: source.value.include_paths.join('\n'),
        sync_interval_minutes: source.value.sync_interval_minutes,
        enabled: source.value.enabled,
        allow_insecure_http: source.value.allow_insecure_http,
      })
    }
  } catch (error) {
    if (showError) ElMessage.error(errorMessage(error))
  } finally { loading.value = false }
}

async function save() {
  saving.value = true
  try {
    source.value = (await api.put<KnowledgeSource>('/smart-cases/knowledge-source', payload())).data
    form.password = ''
    ElMessage.success('智能用例配置已保存')
    await load(false)
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { saving.value = false }
}

async function testConnection() {
  testing.value = true
  try {
    const { data } = await api.post('/smart-cases/knowledge-source/connection-test', payload())
    ElMessage.success(`SVN 连接成功，已检查 ${data.checked_paths.length} 个路径`)
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { testing.value = false }
}

async function syncNow() {
  syncing.value = true
  try {
    const { data } = await api.post('/smart-cases/knowledge-source/sync')
    ElMessage.success(data.reused ? '已有同步任务，已显示当前进度' : '同步任务已提交')
    await load(false)
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { syncing.value = false }
}

async function searchKnowledge() {
  if (!searchQuery.value.trim()) return
  searching.value = true
  try {
    searchResults.value = (await api.post('/smart-cases/knowledge-search', { query: searchQuery.value.trim(), top_k: 10 })).data.results
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { searching.value = false }
}

onMounted(async () => {
  await load()
  timer = setInterval(() => load(false), 10_000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div v-loading="loading" class="page smart-cases-page">
    <header class="page-header">
      <div><span class="page-kicker">管理员配置</span><h1 class="page-title">知识源管理</h1><p class="muted">配置 SVN 知识源、同步范围与索引状态</p></div>
      <el-button v-if="auth.isAdmin" type="primary" :icon="Refresh" :loading="syncing" :disabled="!source?.configured || isBusy || !status?.client_ready" @click="syncNow">立即同步</el-button>
    </header>

    <el-alert v-if="isHttp || source?.repository_urls?.some(item => item.startsWith('http://')) || source?.repository_url?.startsWith('http://')" class="http-warning" type="warning" :closable="false" show-icon title="当前 SVN 使用 HTTP，用户名、密码和资料可能以明文经过网络。仅应在受限内网中使用专用只读账号，并尽快迁移到 HTTPS。" />

    <div class="content-grid">
      <section class="card status-card" aria-labelledby="sync-status-title" aria-live="polite">
        <div class="section-heading"><div><span class="page-kicker">运行状态</span><h2 id="sync-status-title">知识同步</h2></div><el-tag :type="status?.status === 'succeeded' ? 'success' : status?.status === 'failed' ? 'danger' : isBusy ? 'warning' : 'info'" effect="plain">{{ statusText[status?.status || 'unconfigured'] || status?.status }}</el-tag></div>
        <dl class="status-list">
          <div><dt>SVN 客户端</dt><dd>{{ status?.client_ready ? `已就绪 · ${status.svn_version}` : '不可用' }}</dd></div>
          <div><dt>Embedding</dt><dd>{{ status?.embedding_model || '未配置' }}<template v-if="status?.embedding_dimensions"> · {{ status.embedding_dimensions }} 维</template></dd></div>
          <div><dt>最近成功</dt><dd>{{ status?.last_success_at ? formatBeijingDateTime(status.last_success_at) : '尚无成功同步' }}</dd></div>
          <div><dt>已发布文件</dt><dd>{{ status?.file_count || 0 }} 个</dd></div>
          <div><dt>解析失败</dt><dd>{{ status?.failed_file_count || 0 }} 个</dd></div>
          <div><dt>最近变化</dt><dd>新增 {{ status?.changes.added || 0 }} · 修改 {{ status?.changes.changed || 0 }} · 删除 {{ status?.changes.deleted || 0 }}</dd></div>
        </dl>
        <div v-if="Object.keys(status?.revisions || {}).length" class="revision-list">
          <strong>最近成功 revision</strong>
          <div v-for="(revision, path) in status?.revisions" :key="path"><code>{{ path }}</code><span>r{{ revision }}</span></div>
        </div>
        <el-alert v-if="status?.error" type="error" :closable="false" show-icon :title="status.error" />
      </section>

      <section class="card config-card" aria-labelledby="source-config-title">
        <div class="section-heading"><div><span class="page-kicker">管理员配置</span><h2 id="source-config-title">SVN 知识源</h2></div><span class="credential-state"><el-icon><Lock /></el-icon>{{ source?.has_password ? '凭据已配置' : '未配置凭据' }}</span></div>
        <el-form v-if="auth.isAdmin" label-position="top" @submit.prevent>
          <el-form-item label="默认仓库 URL（可多条）" required>
            <div class="svn-path-list">
              <div v-for="(_, index) in form.repository_urls" :key="index" class="svn-path-row">
                <span class="svn-path-index">{{ index + 1 }}</span>
                <el-input v-model="form.repository_urls[index]" placeholder="http://svn.intranet.example/svn/knowledge" />
                <el-button :icon="Delete" text circle type="danger" :disabled="form.repository_urls.length === 1" :aria-label="`删除第 ${index + 1} 条仓库 URL`" @click="removeRepositoryUrl(index)" />
              </div>
              <el-button :icon="Plus" text @click="addRepositoryUrl">添加仓库 URL</el-button>
            </div>
            <p class="field-help">相对白名单路径会应用到每个默认仓库，所有仓库共用下方只读账号。</p>
          </el-form-item>
          <div class="form-row"><el-form-item label="只读用户名" required><el-input v-model="form.username" autocomplete="username" /></el-form-item><el-form-item label="密码" :required="!source?.has_password"><el-input v-model="form.password" type="password" show-password autocomplete="new-password" :placeholder="source?.has_password ? '留空表示不修改' : '请输入密码'" /></el-form-item></div>
          <el-form-item label="允许索引的相对路径（每行一个）" required><el-input v-model="form.include_paths_text" type="textarea" :rows="5" placeholder="docs/测试文档&#10;docs/需求文档/2026年需求" /><p class="field-help">相对路径应用到每个默认仓库；禁止空路径、绝对路径、.. 和 .svn。</p></el-form-item>
          <el-form-item v-if="isHttp"><el-checkbox v-model="form.allow_insecure_http">我已知晓 HTTP 明文传输风险，并允许在当前受限内网中使用</el-checkbox></el-form-item>
          <el-form-item label="自动同步"><el-switch v-model="form.enabled" active-text="启用，每 30 分钟" inactive-text="停用" /></el-form-item>
          <div class="form-actions"><el-button :icon="Connection" :loading="testing" :disabled="isBusy" @click="testConnection">测试 SVN 连接</el-button><el-button type="primary" :loading="saving" :disabled="isBusy" @click="save">保存配置</el-button></div>
        </el-form>
        <dl v-else class="status-list readonly-config">
          <div><dt>默认仓库</dt><dd>{{ source?.repository_urls?.join('、') || source?.repository_url || '未配置' }}</dd></div>
          <div><dt>SVN 路径</dt><dd>{{ source?.include_paths.join('、') || '未配置' }}</dd></div>
          <div><dt>自动同步</dt><dd>{{ source?.enabled ? '每 30 分钟' : '已停用' }}</dd></div>
        </dl>
      </section>
    </div>

    <section class="card search-card" aria-labelledby="knowledge-search-title">
      <div class="section-heading"><div><span class="page-kicker">混合检索验证</span><h2 id="knowledge-search-title">检索已发布知识</h2></div></div>
      <div class="search-row"><el-input v-model="searchQuery" clearable placeholder="输入需求主题、业务规则或历史用例关键词" @keyup.enter="searchKnowledge"><template #prefix><el-icon><Search /></el-icon></template></el-input><el-button type="primary" :loading="searching" :disabled="!status?.last_success_at || status?.status === 'stale'" @click="searchKnowledge">检索</el-button></div>
      <div v-if="searchResults.length" class="search-results" aria-live="polite"><article v-for="item in searchResults" :key="`${item.source_path}:${item.snippet}`"><div><code>{{ item.source_path }}</code><el-tag size="small" effect="plain">r{{ item.revision }}</el-tag><span>{{ item.score.toFixed(3) }}</span></div><p>{{ item.snippet }}</p></article></div>
      <el-empty v-else description="同步成功后，可用真实查询验证 embedding 与关键词混合检索结果" :image-size="72" />
    </section>
  </div>
</template>

<style scoped>
.smart-cases-page{max-width:1500px}.http-warning{margin-bottom:14px}.content-grid{display:grid;grid-template-columns:minmax(320px,.8fr) minmax(520px,1.2fr);gap:14px}.status-card,.config-card,.search-card{padding:20px}.search-card{margin-top:14px}.section-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:18px}.section-heading h2{margin:3px 0 0;font-size:18px}.status-list{display:grid;margin:0}.status-list>div{display:grid;grid-template-columns:130px minmax(0,1fr);gap:12px;padding:11px 0;border-bottom:1px solid var(--ui-border)}.status-list dt{color:var(--ui-text-secondary);font-size:11px}.status-list dd{min-width:0;margin:0;overflow-wrap:anywhere;font-size:12px}.revision-list{display:grid;gap:7px;margin:18px 0}.revision-list>strong{font-size:11px}.revision-list>div{display:flex;justify-content:space-between;gap:12px;padding:8px 10px;border-radius:6px;background:var(--ui-canvas);font-size:11px}.credential-state{display:inline-flex;align-items:center;gap:5px;color:var(--ui-text-secondary);font-size:11px}.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}.form-row :deep(.el-form-item){min-width:0}.svn-path-list{display:grid;width:100%;gap:8px}.svn-path-list>.el-button{justify-self:start}.svn-path-row{display:grid;grid-template-columns:24px minmax(0,1fr) 32px;align-items:center;gap:8px}.svn-path-index{display:grid;width:24px;height:24px;place-items:center;border-radius:50%;background:var(--ui-canvas);color:var(--ui-text-secondary);font-size:11px}.field-help{margin:5px 0 0;color:var(--ui-text-secondary);font-size:11px;line-height:1.5}.config-divider{display:flex;align-items:center;gap:10px;margin:20px 0 16px;color:var(--ui-text-secondary);font-size:11px;font-weight:650}.config-divider::after{height:1px;flex:1;background:var(--ui-border);content:""}.form-actions{display:flex;justify-content:flex-end;gap:8px}.readonly-config{margin-top:8px}.search-row{display:flex;gap:8px}.search-results{display:grid;gap:8px;margin-top:14px}.search-results article{padding:12px 14px;border:1px solid var(--ui-border);border-radius:7px}.search-results article>div{display:flex;align-items:center;gap:8px}.search-results article>div span:last-child{margin-left:auto;color:var(--ui-text-secondary);font:11px/1 monospace}.search-results p{margin:8px 0 0;color:var(--ui-text-secondary);font-size:12px;line-height:1.6;white-space:pre-wrap}@media(max-width:980px){.content-grid{grid-template-columns:1fr}}@media(max-width:640px){.form-row{grid-template-columns:1fr}.status-list>div{grid-template-columns:1fr;gap:4px}.search-row{align-items:stretch;flex-direction:column}}
</style>
