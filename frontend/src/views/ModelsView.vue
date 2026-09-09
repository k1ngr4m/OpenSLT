<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Connection, Delete, Download, Plus, Search } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from '@/ui/elementPlusServices'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

type ModelKind = 'chat' | 'embedding'
interface AiModel { id: number; provider_id: number; kind: ModelKind; model_id: string; is_active: boolean }
interface Provider {
  id: number
  name: string
  base_url: string
  has_api_key: boolean
  allow_insecure_http: boolean
  embedding_dimensions: number | null
  models: AiModel[]
}

const loading = ref(false)
const auth = useAuthStore()
const saving = ref(false)
const discovering = ref(false)
const testingId = ref<number | null>(null)
const detectingDimensions = ref(false)
const detectionModelId = ref<number | null>(null)
const selectedId = ref<number | null>(null)
const kind = ref<ModelKind>('chat')
const query = ref('')
const providers = ref<Provider[]>([])
const discovered = ref<string[]>([])
const discoveryVisible = ref(false)
const discoveryQuery = ref('')
const filteredDiscovered = computed(() => {
  const needle = discoveryQuery.value.trim().toLowerCase()
  return discovered.value.filter(modelId => modelId.toLowerCase().includes(needle))
})
const form = reactive({ name: '', base_url: '', api_key: '', allow_insecure_http: false, embedding_dimensions: 1024 })

const selected = computed(() => providers.value.find(item => item.id === selectedId.value) || null)
const embeddingModels = computed(() => (selected.value?.models || []).filter(model => model.kind === 'embedding'))
const isHttp = computed(() => form.base_url.trim().toLowerCase().startsWith('http://'))
const visibleModels = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return (selected.value?.models || []).filter(item =>
    item.kind === kind.value && (!needle || item.model_id.toLowerCase().includes(needle)),
  )
})
const configuredIds = computed(() => new Set(
  (selected.value?.models || []).filter(item => item.kind === kind.value).map(item => item.model_id),
))

function editProvider(provider: Provider | null) {
  selectedId.value = provider?.id || null
  Object.assign(form, {
    name: provider?.name || '',
    base_url: provider?.base_url || '',
    api_key: '',
    allow_insecure_http: provider?.allow_insecure_http || false,
    embedding_dimensions: provider?.embedding_dimensions ?? 1024,
  })
  const models = provider?.models.filter(model => model.kind === 'embedding') || []
  detectionModelId.value = models.find(model => model.is_active)?.id ?? models[0]?.id ?? null
}

let loadId = 0
async function load(preferredId = selectedId.value) {
  const requestId = ++loadId
  const requestedKind = kind.value
  loading.value = true
  try {
    const { data } = await api.get<Provider[]>('/model-providers', { params: { kind: requestedKind } })
    if (requestId !== loadId) return
    providers.value = data
    const provider = providers.value.find(item => item.id === preferredId) || providers.value[0] || null
    editProvider(provider)
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { if (requestId === loadId) loading.value = false }
}

async function saveProvider() {
  if (kind.value === 'embedding' && (!Number.isInteger(form.embedding_dimensions) || form.embedding_dimensions < 1 || form.embedding_dimensions > 2147483647)) {
    ElMessage.error('嵌入维度必须是 1 到 2147483647 之间的整数')
    return
  }
  saving.value = true
  try {
    const body = {
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      api_key: form.api_key || null,
      allow_insecure_http: form.allow_insecure_http,
      ...(kind.value === 'embedding' ? { embedding_dimensions: form.embedding_dimensions } : {}),
    }
    const response = selected.value
      ? await api.put<Provider>(`/model-providers/${selected.value.id}`, body)
      : await api.post<Provider>('/model-providers', { ...body, kind: kind.value })
    form.api_key = ''
    ElMessage.success('提供商配置已保存')
    await load(response.data.id)
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { saving.value = false }
}

async function removeProvider() {
  if (!selected.value) return
  try {
    await ElMessageBox.confirm(`确定删除提供商“${selected.value.name}”及其模型？`, '删除提供商', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
    await api.delete(`/model-providers/${selected.value.id}`)
    ElMessage.success('提供商已删除')
    await load(null)
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(errorMessage(error))
  }
}

async function discoverModels() {
  if (!selected.value) return
  discovering.value = true
  try {
    discovered.value = (await api.post<{ models: string[] }>(
      `/model-providers/${selected.value.id}/models/discover`, { kind: kind.value },
      { timeout: 0 },
    )).data.models
    discoveryQuery.value = ''
    discoveryVisible.value = true
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { discovering.value = false }
}

async function addModel(modelId?: string) {
  if (!selected.value) return
  try {
    let value = modelId
    if (!value) {
      const result = await ElMessageBox.prompt('输入 OpenAI-compatible 模型 ID', '添加模型', {
        inputPlaceholder: kind.value === 'chat' ? 'Qwen3-32B' : 'bge-m3',
        confirmButtonText: '添加', cancelButtonText: '取消',
      })
      value = result.value
    }
    await api.post(`/model-providers/${selected.value.id}/models`, {
      kind: kind.value, model_id: value?.trim(),
    })
    ElMessage.success('模型已添加')
    await load(selected.value.id)
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(errorMessage(error))
  }
}

async function activateModel(model: AiModel) {
  try {
    await api.post(`/model-providers/models/${model.id}/activate`)
    ElMessage.success(`已设为当前${model.kind === 'chat' ? '对话' : ' Embedding'}模型`)
    await load(selectedId.value)
  } catch (error) { ElMessage.error(errorMessage(error)) }
}

async function testModel(model: AiModel) {
  testingId.value = model.id
  try {
    const { data } = await api.post(
      `/model-providers/models/${model.id}/connection-test`, undefined, { timeout: 0 },
    )
    ElMessage.success(model.kind === 'embedding'
      ? `Embedding 连接成功，向量维度 ${data.dimensions}`
      : '对话模型连接成功')
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { testingId.value = null }
}

async function detectDimensions() {
  const provider = selected.value
  if (!provider || detectionModelId.value === null) return
  if (form.base_url.trim() !== provider.base_url || form.api_key || form.allow_insecure_http !== provider.allow_insecure_http) {
    ElMessage.warning('请先保存服务地址和 API Key，再自动检测嵌入维度')
    return
  }
  detectingDimensions.value = true
  testingId.value = detectionModelId.value
  try {
    const { data } = await api.post<{ dimensions: number }>(
      `/model-providers/models/${detectionModelId.value}/connection-test`, undefined, { timeout: 0 },
    )
    if (!Number.isInteger(data.dimensions) || data.dimensions < 1 || data.dimensions > 2147483647) {
      throw new Error('模型未返回有效的嵌入维度')
    }
    form.embedding_dimensions = data.dimensions
    ElMessage.success(`检测到 ${data.dimensions} 维，请保存配置`)
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { detectingDimensions.value = false; testingId.value = null }
}

async function removeModel(model: AiModel) {
  try {
    await ElMessageBox.confirm(`确定删除模型“${model.model_id}”？`, '删除模型', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
    await api.delete(`/model-providers/models/${model.id}`)
    ElMessage.success('模型已删除')
    await load(selectedId.value)
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(errorMessage(error))
  }
}

onMounted(() => load())
watch(kind, () => {
  providers.value = []
  editProvider(null)
  discovered.value = []
  discoveryVisible.value = false
  query.value = ''
  void load(null)
})
</script>

<template>
  <div v-loading="loading" class="page models-page">
    <header class="page-header">
      <div><span class="page-kicker">模型配置</span><h1 class="page-title">模型管理</h1><p class="muted">{{ kind === 'chat' ? '对话模型仅对当前账户生效，供智能助手和智能用例使用' : 'Embedding 为知识库共享配置，仅系统管理员可管理' }}</p></div>
    </header>

    <el-tabs v-model="kind" class="model-tabs">
      <el-tab-pane label="对话" name="chat" :disabled="saving || discovering || testingId !== null" />
      <el-tab-pane v-if="auth.isAdmin" label="Embedding" name="embedding" :disabled="saving || discovering || testingId !== null" />
    </el-tabs>

    <div class="provider-layout">
      <aside class="card provider-list" aria-label="模型提供商">
        <div class="list-heading"><strong>{{ kind === 'chat' ? '对话模型提供商' : 'Embedding 提供商' }}</strong><el-button text type="primary" :icon="Plus" :disabled="saving || testingId !== null" @click="editProvider(null)">新增</el-button></div>
        <button v-for="provider in providers" :key="provider.id" type="button" class="provider-item" :disabled="saving || testingId !== null" :class="{ active: provider.id === selectedId }" @click="editProvider(provider)">
          <span><strong>{{ provider.name }}</strong><small>{{ provider.base_url }}</small></span>
          <el-tag class="provider-count" size="small" effect="plain" title="已配置模型数量">{{ provider.models.filter(item => item.kind === kind).length }}</el-tag>
        </button>
        <el-empty v-if="!providers.length" description="暂无提供商" :image-size="58" />
      </aside>

      <section class="card provider-detail" aria-label="提供商配置与模型列表">
        <div class="detail-heading">
          <div><span class="page-kicker">提供商配置</span><h2>{{ selected?.name || '新增提供商' }}</h2></div>
          <div class="detail-actions"><el-button v-if="selected" text type="danger" :icon="Delete" :disabled="saving || testingId !== null" @click="removeProvider">删除</el-button><el-button type="primary" :loading="saving" :disabled="testingId !== null" @click="saveProvider">保存配置</el-button></div>
        </div>
        <el-form @submit.prevent :disabled="saving || testingId !== null" label-position="left" label-width="var(--ui-field-label-width)">
          <div class="form-row"><el-form-item label="名称" required><el-input v-model="form.name" placeholder="内网模型服务" /></el-form-item><el-form-item label="API Base URL" required><el-input v-model="form.base_url" placeholder="https://api.example.com/v1" /></el-form-item></div>
          <el-form-item label="API Key"><el-input v-model="form.api_key" type="password" show-password autocomplete="new-password" :placeholder="selected?.has_api_key ? '********' : '服务无需鉴权时可留空'" /></el-form-item>
          <el-form-item v-if="isHttp"><el-checkbox v-model="form.allow_insecure_http">我已知晓 HTTP 会明文传输 API Key 和业务资料，并允许连接当前受控内网服务</el-checkbox></el-form-item>
          <el-form-item v-if="kind === 'embedding'" label="嵌入维度" required>
            <div class="embedding-dimensions">
              <div class="dimension-controls">
                <el-input-number v-model="form.embedding_dimensions" :min="1" :max="2147483647" :precision="0" :controls="false" aria-label="嵌入维度" />
                <el-select v-if="embeddingModels.length > 1" v-model="detectionModelId" aria-label="检测维度使用的模型" placeholder="选择检测模型">
                  <el-option v-for="model in embeddingModels" :key="model.id" :label="model.model_id" :value="model.id" />
                </el-select>
                <el-button type="primary" plain :loading="detectingDimensions" :disabled="!detectionModelId || testingId !== null" @click="detectDimensions">自动检测</el-button>
              </div>
              <p class="muted dimension-hint">嵌入向量维度，默认 1024，需与模型实际输出一致。修改后需重新同步知识索引。</p>
              <p class="muted dimension-hint">{{ detectionModelId ? `检测模型：${embeddingModels.find(model => model.id === detectionModelId)?.model_id}；检测结果需保存后生效。` : '请先保存提供商并添加 Embedding 模型，再自动检测。' }}</p>
            </div>
          </el-form-item>
        </el-form>

        <section class="models-section" aria-labelledby="models-title">
          <div class="models-heading"><div><span class="page-kicker">{{ kind === 'chat' ? '对话' : 'Embedding' }}</span><h2 id="models-title">已配置模型</h2></div><div class="model-actions"><el-input v-model="query" clearable placeholder="搜索模型 ID"><template #prefix><el-icon><Search /></el-icon></template></el-input><el-button :icon="Download" :loading="discovering" :disabled="!selected || saving || testingId !== null" @click="discoverModels">获取模型列表</el-button><el-button :icon="Plus" :disabled="!selected || saving || testingId !== null" @click="addModel()">手动添加</el-button></div></div>
          <div v-if="visibleModels.length" class="models-list">
            <article v-for="model in visibleModels" :key="model.id" class="model-item">
              <div><strong>{{ model.model_id }}</strong><el-tag v-if="model.is_active" size="small" type="success" effect="plain">当前模型</el-tag></div>
              <div class="model-row-actions"><el-button text :icon="Connection" :loading="testingId === model.id && !detectingDimensions" :disabled="saving || testingId !== null" @click="testModel(model)">测试连接</el-button><el-button v-if="!model.is_active" text type="primary" :disabled="saving || testingId !== null" @click="activateModel(model)">设为当前</el-button><el-button text type="danger" :icon="Delete" :disabled="model.is_active || saving || testingId !== null" aria-label="删除模型" @click="removeModel(model)" /></div>
            </article>
          </div>
          <el-empty v-else :description="selected ? '当前分类还没有模型' : '请先保存提供商配置'" :image-size="64" />
        </section>
      </section>
    </div>

    <el-dialog v-model="discoveryVisible" title="远端模型列表" width="min(680px, 92vw)">
      <el-input v-model="discoveryQuery" class="discovery-search" clearable :prefix-icon="Search" placeholder="输入模型 ID 筛选" aria-label="筛选远端模型" />
      <div class="discovery-list">
        <div v-for="modelId in filteredDiscovered" :key="modelId"><code>{{ modelId }}</code><el-button text type="primary" :disabled="configuredIds.has(modelId)" @click="addModel(modelId)">{{ configuredIds.has(modelId) ? '已添加' : '添加' }}</el-button></div>
        <el-empty v-if="!filteredDiscovered.length" :description="discovered.length ? '没有匹配的模型' : '服务未返回模型'" :image-size="58" />
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.provider-item .provider-count {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  height: 24px;
  padding: 0 8px;
  border: 0;
  border-radius: 999px;
  background: var(--ui-primary-soft);
  color: var(--ui-primary);
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.provider-item.active .provider-count { background: var(--ui-primary); color: #fff; }
.discovery-search { margin-bottom: 12px; }
.embedding-dimensions { width: 100%; }
.dimension-controls { display: flex; flex-wrap: wrap; gap: 10px; }
.dimension-controls .el-input-number { flex: 1; min-width: 140px; }
.dimension-controls .el-select { flex: 1; min-width: 180px; }
.dimension-hint { margin: 6px 0 0; font-size: 12px; line-height: 1.6; }
.models-page{max-width:1500px}.model-tabs{margin-bottom:14px}.provider-layout{display:grid;grid-template-columns:minmax(240px,.34fr) minmax(560px,1fr);gap:14px}.provider-list,.provider-detail{padding:18px}.list-heading,.detail-heading,.models-heading,.detail-actions,.model-actions,.model-item,.model-row-actions{display:flex;align-items:center}.list-heading,.detail-heading,.models-heading,.model-item{justify-content:space-between}.list-heading{margin-bottom:12px}.provider-item{display:flex;width:100%;align-items:center;justify-content:space-between;gap:10px;margin:3px 0;padding:12px;border:0;border-radius:7px;background:transparent;color:var(--ui-text);text-align:left;cursor:pointer}.provider-item:hover,.provider-item.active{background:var(--ui-primary-soft)}.provider-item span,.provider-item strong,.provider-item small{display:block;min-width:0}.provider-item small{max-width:210px;margin-top:4px;overflow:hidden;color:var(--ui-text-secondary);font-size:12px;text-overflow:ellipsis;white-space:nowrap}.detail-heading{gap:16px;margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid var(--ui-border)}.detail-heading h2,.models-heading h2{margin:3px 0 0;font-size:18px}.detail-actions,.model-actions,.model-row-actions{gap:8px}.form-row{display:grid;grid-template-columns:.55fr 1fr;gap:12px}.models-section{margin-top:12px;padding-top:20px;border-top:1px solid var(--ui-border)}.models-heading{align-items:flex-end;gap:16px;margin-bottom:14px}.model-actions .el-input{width:190px}.models-list{border-top:1px solid var(--ui-border)}.model-item{gap:16px;padding:13px 4px;border-bottom:1px solid var(--ui-border)}.model-item>div:first-child{display:flex;min-width:0;align-items:center;gap:8px}.model-item strong{overflow-wrap:anywhere;font-size:12px}.discovery-list{display:grid;max-height:440px;overflow:auto}.discovery-list>div{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 4px;border-bottom:1px solid var(--ui-border)}.discovery-list code{overflow-wrap:anywhere;font-size:12px}@media(max-width:980px){.provider-layout{grid-template-columns:1fr}.provider-list{max-height:260px;overflow:auto}}@media(max-width:700px){.form-row{grid-template-columns:1fr}.detail-heading,.models-heading{align-items:stretch;flex-direction:column}.detail-actions,.model-actions{flex-wrap:wrap}.model-actions .el-input{width:100%}.model-item{align-items:flex-start;flex-direction:column}.model-row-actions{align-self:stretch;justify-content:flex-end}}

.form-row { grid-template-columns: minmax(0, 1fr); }
</style>
