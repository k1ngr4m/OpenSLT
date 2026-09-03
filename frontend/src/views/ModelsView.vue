<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Connection, Delete, Download, Plus, Search } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from '@/ui/elementPlusServices'
import { api, errorMessage } from '@/api/client'

type ModelKind = 'chat' | 'embedding'
interface AiModel { id: number; provider_id: number; kind: ModelKind; model_id: string; is_active: boolean }
interface Provider {
  id: number
  name: string
  base_url: string
  has_api_key: boolean
  allow_insecure_http: boolean
  models: AiModel[]
}

const loading = ref(false)
const saving = ref(false)
const discovering = ref(false)
const testingId = ref<number | null>(null)
const selectedId = ref<number | null>(null)
const kind = ref<ModelKind>('chat')
const query = ref('')
const providers = ref<Provider[]>([])
const discovered = ref<string[]>([])
const discoveryVisible = ref(false)
const form = reactive({ name: '', base_url: '', api_key: '', allow_insecure_http: false })

const selected = computed(() => providers.value.find(item => item.id === selectedId.value) || null)
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
  })
}

async function load(preferredId = selectedId.value) {
  loading.value = true
  try {
    providers.value = (await api.get<Provider[]>('/model-providers')).data
    const provider = providers.value.find(item => item.id === preferredId) || providers.value[0] || null
    editProvider(provider)
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { loading.value = false }
}

async function saveProvider() {
  saving.value = true
  try {
    const body = {
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      api_key: form.api_key || null,
      allow_insecure_http: form.allow_insecure_http,
    }
    const response = selected.value
      ? await api.put<Provider>(`/model-providers/${selected.value.id}`, body)
      : await api.post<Provider>('/model-providers', body)
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
    )).data.models
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
    const { data } = await api.post(`/model-providers/models/${model.id}/connection-test`)
    ElMessage.success(model.kind === 'embedding'
      ? `Embedding 连接成功，向量维度 ${data.dimensions}`
      : '对话模型连接成功')
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { testingId.value = null }
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
</script>

<template>
  <div v-loading="loading" class="page models-page">
    <header class="page-header">
      <div><span class="page-kicker">系统配置</span><h1 class="page-title">模型管理</h1><p class="muted">集中管理 OpenAI-compatible 提供商，并为业务指定当前模型</p></div>
    </header>

    <el-tabs v-model="kind" class="model-tabs">
      <el-tab-pane label="对话" name="chat" />
      <el-tab-pane label="Embedding" name="embedding" />
    </el-tabs>

    <div class="provider-layout">
      <aside class="card provider-list" aria-label="模型提供商">
        <div class="list-heading"><strong>提供商</strong><el-button text type="primary" :icon="Plus" @click="editProvider(null)">新增</el-button></div>
        <button v-for="provider in providers" :key="provider.id" type="button" class="provider-item" :class="{ active: provider.id === selectedId }" @click="editProvider(provider)">
          <span><strong>{{ provider.name }}</strong><small>{{ provider.base_url }}</small></span>
          <el-tag size="small" effect="plain">{{ provider.models.filter(item => item.kind === kind).length }}</el-tag>
        </button>
        <el-empty v-if="!providers.length" description="暂无提供商" :image-size="58" />
      </aside>

      <section class="card provider-detail" aria-label="提供商配置与模型列表">
        <div class="detail-heading">
          <div><span class="page-kicker">提供商配置</span><h2>{{ selected?.name || '新增提供商' }}</h2></div>
          <div class="detail-actions"><el-button v-if="selected" text type="danger" :icon="Delete" @click="removeProvider">删除</el-button><el-button type="primary" :loading="saving" @click="saveProvider">保存配置</el-button></div>
        </div>
        <el-form label-position="top" @submit.prevent>
          <div class="form-row"><el-form-item label="名称" required><el-input v-model="form.name" placeholder="内网模型服务" /></el-form-item><el-form-item label="API Base URL" required><el-input v-model="form.base_url" placeholder="https://api.example.com/v1" /></el-form-item></div>
          <el-form-item label="API Key"><el-input v-model="form.api_key" type="password" show-password autocomplete="new-password" :placeholder="selected?.has_api_key ? '留空表示不修改' : '服务无需鉴权时可留空'" /></el-form-item>
          <el-form-item v-if="isHttp"><el-checkbox v-model="form.allow_insecure_http">我已知晓 HTTP 会明文传输 API Key 和业务资料，并允许连接当前受控内网服务</el-checkbox></el-form-item>
        </el-form>

        <section class="models-section" aria-labelledby="models-title">
          <div class="models-heading"><div><span class="page-kicker">{{ kind === 'chat' ? '对话' : 'Embedding' }}</span><h2 id="models-title">已配置模型</h2></div><div class="model-actions"><el-input v-model="query" clearable placeholder="搜索模型 ID"><template #prefix><el-icon><Search /></el-icon></template></el-input><el-button :icon="Download" :loading="discovering" :disabled="!selected" @click="discoverModels">获取模型列表</el-button><el-button :icon="Plus" :disabled="!selected" @click="addModel()">手动添加</el-button></div></div>
          <div v-if="visibleModels.length" class="models-list">
            <article v-for="model in visibleModels" :key="model.id" class="model-item">
              <div><strong>{{ model.model_id }}</strong><el-tag v-if="model.is_active" size="small" type="success" effect="plain">当前模型</el-tag></div>
              <div class="model-row-actions"><el-button text :icon="Connection" :loading="testingId === model.id" @click="testModel(model)">测试连接</el-button><el-button v-if="!model.is_active" text type="primary" @click="activateModel(model)">设为当前</el-button><el-button text type="danger" :icon="Delete" :disabled="model.is_active" aria-label="删除模型" @click="removeModel(model)" /></div>
            </article>
          </div>
          <el-empty v-else :description="selected ? '当前分类还没有模型' : '请先保存提供商配置'" :image-size="64" />
        </section>
      </section>
    </div>

    <el-dialog v-model="discoveryVisible" title="远端模型列表" width="min(680px, 92vw)">
      <div class="discovery-list">
        <div v-for="modelId in discovered" :key="modelId"><code>{{ modelId }}</code><el-button text type="primary" :disabled="configuredIds.has(modelId)" @click="addModel(modelId)">{{ configuredIds.has(modelId) ? '已添加' : '添加' }}</el-button></div>
        <el-empty v-if="!discovered.length" description="服务未返回模型" :image-size="58" />
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.models-page{max-width:1500px}.model-tabs{margin-bottom:14px}.provider-layout{display:grid;grid-template-columns:minmax(240px,.34fr) minmax(560px,1fr);gap:14px}.provider-list,.provider-detail{padding:18px}.list-heading,.detail-heading,.models-heading,.detail-actions,.model-actions,.model-item,.model-row-actions{display:flex;align-items:center}.list-heading,.detail-heading,.models-heading,.model-item{justify-content:space-between}.list-heading{margin-bottom:12px}.provider-item{display:flex;width:100%;align-items:center;justify-content:space-between;gap:10px;margin:3px 0;padding:12px;border:0;border-radius:7px;background:transparent;color:var(--ui-text);text-align:left;cursor:pointer}.provider-item:hover,.provider-item.active{background:var(--ui-primary-soft)}.provider-item span,.provider-item strong,.provider-item small{display:block;min-width:0}.provider-item small{max-width:210px;margin-top:4px;overflow:hidden;color:var(--ui-text-secondary);font-size:10px;text-overflow:ellipsis;white-space:nowrap}.detail-heading{gap:16px;margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid var(--ui-border)}.detail-heading h2,.models-heading h2{margin:3px 0 0;font-size:18px}.detail-actions,.model-actions,.model-row-actions{gap:8px}.form-row{display:grid;grid-template-columns:.55fr 1fr;gap:12px}.models-section{margin-top:12px;padding-top:20px;border-top:1px solid var(--ui-border)}.models-heading{align-items:flex-end;gap:16px;margin-bottom:14px}.model-actions .el-input{width:190px}.models-list{border-top:1px solid var(--ui-border)}.model-item{gap:16px;padding:13px 4px;border-bottom:1px solid var(--ui-border)}.model-item>div:first-child{display:flex;min-width:0;align-items:center;gap:8px}.model-item strong{overflow-wrap:anywhere;font-size:12px}.discovery-list{display:grid;max-height:440px;overflow:auto}.discovery-list>div{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 4px;border-bottom:1px solid var(--ui-border)}.discovery-list code{overflow-wrap:anywhere;font-size:11px}@media(max-width:980px){.provider-layout{grid-template-columns:1fr}.provider-list{max-height:260px;overflow:auto}}@media(max-width:700px){.form-row{grid-template-columns:1fr}.detail-heading,.models-heading{align-items:stretch;flex-direction:column}.detail-actions,.model-actions{flex-wrap:wrap}.model-actions .el-input{width:100%}.model-item{align-items:flex-start;flex-direction:column}.model-row-actions{align-self:stretch;justify-content:flex-end}}
</style>
