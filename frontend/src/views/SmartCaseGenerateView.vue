<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Download, MagicStick, Search, Setting, View } from '@element-plus/icons-vue'
import { ElMessage } from '@/ui/elementPlusServices'
import GenerationPromptDialog from '@/components/GenerationPromptDialog.vue'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { formatBeijingDateTime } from '@/utils/time'
import type { components } from '@/types/api.generated'

interface Requirement { source_path: string; revision: string; requirement_no: string | null; requirement_name: string }
interface Generation { id: number; knowledge_base_id?: number | null; requirement_path: string; requirement_revision: string; requirement_no: string | null; requirement_name: string; status: string; llm_model: string; case_count: number; error: string | null; download_ready: boolean; created_at: string }

const router = useRouter()
const knowledgeBases = ref<components['schemas']['KnowledgeBaseOut'][]>([])
const knowledgeBaseId = ref<number | null>(null)
const auth = useAuthStore()
const loading = ref(false)
const generating = ref(false)
const downloading = ref<number | null>(null)
const previewVisible = ref(false)
const previewLoading = ref(false)
const previewError = ref('')
const previewItem = ref<Generation | null>(null)
const previewDetail = ref<components['schemas']['SmartCaseGenerationDetailOut'] | null>(null)
const query = ref('')
const requirements = ref<Requirement[]>([])
const generations = ref<Generation[]>([])
const selectedPath = ref('')
const additionalPrompt = ref('')
type RevisionRequest = components['schemas']['SmartCaseRevisionCreate']
const revisionIndices = ref<number[]>([])
const revisionFields = ref<RevisionRequest['fields']>(['title'])
const revisionInstruction = ref('')
const revising = ref(false)
const revisionError = ref('')
const fieldOptions: { value: RevisionRequest['fields'][number]; label: string }[] = [
  { value: 'title', label: '用例名称' }, { value: 'preconditions', label: '前置条件' },
  { value: 'steps', label: '测试步骤' }, { value: 'expected_results', label: '预期结果' },
  { value: 'case_type', label: '用例类型' }, { value: 'priority', label: '优先级' },
]
const allRevisionCases = computed({
  get: () => revisionIndices.value.length > 0 && revisionIndices.value.length === previewDetail.value?.result_cases.length,
  set: (checked: boolean) => { revisionIndices.value = checked ? previewDetail.value?.result_cases.map((_, index) => index) ?? [] : [] },
})
const allRevisionFields = computed({
  get: () => revisionFields.value.length === fieldOptions.length,
  set: (checked: boolean) => { revisionFields.value = checked ? fieldOptions.map(option => option.value) : [] },
})
let timer: ReturnType<typeof setInterval> | undefined

const selected = computed(() => requirements.value.find(item => item.source_path === selectedPath.value))
const visibleRequirements = computed(() => {
  const needle = query.value.trim().toLowerCase()
  if (!needle) return requirements.value
  return requirements.value.filter(item => `${item.requirement_no || ''} ${item.requirement_name} ${item.source_path}`.toLowerCase().includes(needle))
})
const hasRunning = computed(() => generations.value.some(item => ['queued', 'running'].includes(item.status)))
const statusText: Record<string, string> = { queued: '排队中', running: '生成中', succeeded: '已完成', failed: '失败' }

async function load(showError = true) {
  loading.value = !requirements.value.length
  const selectedBaseId = knowledgeBaseId.value
  try {
    const [requirementResponse, generationResponse] = await Promise.allSettled([
      knowledgeBaseId.value ? api.get<Requirement[]>('/smart-cases/requirements', { params: { knowledge_base_id: knowledgeBaseId.value } }) : Promise.resolve({ data: [] as Requirement[] }),
      api.get<Generation[]>('/smart-cases/generations'),
    ])
    if (knowledgeBaseId.value !== selectedBaseId) return
    if (generationResponse.status === 'fulfilled') generations.value = generationResponse.value.data
    if (requirementResponse.status === 'fulfilled') requirements.value = requirementResponse.value.data
    if (generationResponse.status === 'rejected') throw generationResponse.reason
    if (requirementResponse.status === 'rejected') throw requirementResponse.reason
    if (selectedPath.value && !requirements.value.some(item => item.source_path === selectedPath.value)) selectedPath.value = ''
  } catch (error) {
    if (showError && knowledgeBaseId.value === selectedBaseId) ElMessage.error(errorMessage(error))
  } finally { if (knowledgeBaseId.value === selectedBaseId) loading.value = false }
}

async function generate() {
  if (!selected.value) return
  generating.value = true
  try {
    const payload: components['schemas']['SmartCaseGenerationCreate'] = {
      knowledge_base_id: knowledgeBaseId.value,
      requirement_path: selected.value.source_path,
      additional_prompt: additionalPrompt.value.trim(),
    }
    await api.post('/smart-cases/generations', payload)
    ElMessage.success('生成任务已提交，完成后可预览或下载 Excel 草稿')
    await load(false)
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { generating.value = false }
}

async function preview(item: Generation) {
  revisionIndices.value = []
  revisionFields.value = ['title']
  revisionInstruction.value = ''
  revisionError.value = ''
  previewItem.value = item
  previewDetail.value = null
  previewError.value = ''
  previewLoading.value = true
  previewVisible.value = true
  try {
    const response = await api.get<components['schemas']['SmartCaseGenerationDetailOut']>(`/smart-cases/generations/${item.id}`)
    if (previewItem.value?.id === item.id) previewDetail.value = response.data
  } catch (error) {
    if (previewItem.value?.id === item.id) previewError.value = errorMessage(error)
  } finally {
    if (previewItem.value?.id === item.id) previewLoading.value = false
  }
}

async function revise() {
  if (!previewDetail.value || revising.value || !revisionIndices.value.length || !revisionFields.value.length || !revisionInstruction.value.trim()) return
  revising.value = true
  revisionError.value = ''
  try {
    const payload: RevisionRequest = {
      case_indices: [...revisionIndices.value].sort((a, b) => a - b),
      fields: [...revisionFields.value], instruction: revisionInstruction.value.trim(),
    }
    const { data } = await api.post<Generation>(`/smart-cases/generations/${previewDetail.value.id}/revise`, payload)
    generations.value = [data, ...generations.value]
    previewVisible.value = false
    ElMessage.success(`修改任务 #${data.id} 已提交，完成后可在最近任务中预览、继续修改或下载；原稿已保留`)
  } catch (error) { revisionError.value = errorMessage(error) }
  finally { revising.value = false }
}

async function download(item: Generation) {
  downloading.value = item.id
  try {
    const response = await api.get(`/smart-cases/generations/${item.id}/download`, { responseType: 'blob' })
    const url = URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = url
    link.download = `智能测试用例-${item.requirement_no || item.id}.xlsx`
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { downloading.value = null }
}

watch(knowledgeBaseId, () => { requirements.value = []; selectedPath.value = ''; query.value = ''; void load() })
onMounted(async () => {
  try {
    knowledgeBases.value = (await api.get<components['schemas']['KnowledgeBaseOut'][]>('/knowledge-bases')).data
    if (knowledgeBases.value.length === 1) knowledgeBaseId.value = knowledgeBases.value[0]!.id
  } catch (error) { ElMessage.error(errorMessage(error)) }
  await load()
  timer = setInterval(() => { if (hasRunning.value) load(false) }, 5000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div v-loading="loading" class="page smart-generate-page">
    <header class="page-header">
      <div><span class="page-kicker">知识驱动测试设计</span><h1 class="page-title">智能用例</h1><p class="muted">从所选知识库的成功索引中选择需求，生成可追溯的人工执行 Excel 用例草稿</p></div>
      <div><el-button :icon="Setting" @click="router.push('/models')">模型管理</el-button><el-button v-if="auth.isAdmin" :icon="Setting" @click="router.push('/knowledge-bases')">知识库管理</el-button><GenerationPromptDialog v-if="auth.isAdmin" /></div>
    </header>

    <div class="generate-grid">
      <section class="card requirement-card" aria-labelledby="requirement-title">
        <div class="section-heading"><div><span class="page-kicker">第一步</span><h2 id="requirement-title">选择需求</h2></div><el-tag effect="plain">{{ visibleRequirements.length }} 项</el-tag></div>
        <el-select v-model="knowledgeBaseId" class="knowledge-select" aria-label="选择知识库" placeholder="请选择知识库" :disabled="generating"><el-option v-for="base in knowledgeBases" :key="base.id" :label="base.name" :value="base.id" /></el-select>
        <el-input v-model="query" clearable aria-label="按需求编号或名称检索" placeholder="输入需求编号、名称或文档路径"><template #prefix><el-icon><Search /></el-icon></template></el-input>
        <div v-if="visibleRequirements.length" class="requirement-list" role="radiogroup" aria-label="可生成的需求">
          <label v-for="item in visibleRequirements" :key="item.source_path" class="requirement-item" :class="{ selected: selectedPath === item.source_path }">
            <input v-model="selectedPath" type="radio" name="requirement" :value="item.source_path" />
            <span><strong>{{ item.requirement_no || '未识别编号' }} · {{ item.requirement_name }}</strong><small>{{ item.source_path }} · r{{ item.revision }}</small></span>
          </label>
        </div>
        <el-empty v-else description="没有匹配的已索引需求；请检查关键词或联系管理员同步知识库" :image-size="72" />
      </section>

      <section class="card action-card" aria-labelledby="generate-title" aria-live="polite">
        <div class="section-heading"><div><span class="page-kicker">第二步</span><h2 id="generate-title">生成用例</h2></div></div>
        <template v-if="selected">
          <dl><div><dt>需求编号</dt><dd>{{ selected.requirement_no || '未从文件名识别' }}</dd></div><div><dt>需求名称</dt><dd>{{ selected.requirement_name }}</dd></div><div><dt>知识版本</dt><dd>r{{ selected.revision }}</dd></div><div><dt>文档来源</dt><dd>{{ selected.source_path }}</dd></div></dl>
          <el-alert type="info" :closable="false" show-icon title="系统会检索相关知识作为参考；输出为草稿，执行前必须人工复核。" />
          <div class="additional-prompt">
            <label for="additional-prompt">补充提示词（选填）</label>
            <el-input id="additional-prompt" v-model="additionalPrompt" type="textarea" :rows="4" :maxlength="4000" show-word-limit :disabled="generating" aria-describedby="additional-prompt-help" placeholder="例如：重点覆盖权限校验、异常处理和边界条件；注明需要关注的业务规则。" />
            <p id="additional-prompt-help" class="muted">填写本次生成需要注意的点或事项，将随需求和参考资料一起发送给 LLM。</p>
          </div>
          <el-button class="generate-button" type="primary" size="large" :icon="MagicStick" :loading="generating" @click="generate">生成 Excel 用例草稿</el-button>
        </template>
        <el-empty v-else description="请先从左侧选择一个需求" :image-size="72" />
      </section>
    </div>

    <section class="card history-card" aria-labelledby="history-title">
      <div class="section-heading"><div><span class="page-kicker">生成记录</span><h2 id="history-title">我的最近任务</h2></div></div>
      <el-table v-if="generations.length" :data="generations">
        <el-table-column label="任务" width="90"><template #default="{ row }">#{{ row.id }}</template></el-table-column>
        <el-table-column label="知识库" min-width="150"><template #default="{ row }">{{ knowledgeBases.find(base => base.id === row.knowledge_base_id)?.name || '—' }}</template></el-table-column>
        <el-table-column prop="requirement_no" label="需求编号" width="130"><template #default="{ row }">{{ row.requirement_no || '—' }}</template></el-table-column>
        <el-table-column prop="requirement_name" label="需求名称" min-width="220" show-overflow-tooltip />
        <el-table-column prop="requirement_revision" label="版本" width="90"><template #default="{ row }">r{{ row.requirement_revision }}</template></el-table-column>
        <el-table-column prop="llm_model" label="模型" min-width="150" show-overflow-tooltip />
        <el-table-column label="状态" width="110"><template #default="{ row }"><el-tooltip :content="row.error || ''" :disabled="!row.error"><el-tag :type="row.status === 'succeeded' ? 'success' : row.status === 'failed' ? 'danger' : 'warning'" effect="plain">{{ statusText[row.status] || row.status }}<template v-if="row.case_count"> · {{ row.case_count }}</template></el-tag></el-tooltip></template></el-table-column>
        <el-table-column label="提交时间" width="180"><template #default="{ row }">{{ formatBeijingDateTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="104" fixed="right">
          <template #default="{ row }">
            <div class="generation-actions">
              <el-tooltip v-if="row.status === 'succeeded'" content="预览用例" placement="top"><el-button text circle type="primary" :icon="View" aria-label="预览用例" @click="preview(row)" /></el-tooltip>
              <el-tooltip :content="row.download_ready ? '下载 Excel' : '用例文件尚未生成'" placement="top"><span><el-button text circle type="primary" :icon="Download" aria-label="下载 Excel" :loading="downloading === row.id" :disabled="!row.download_ready" @click="download(row)" /></span></el-tooltip>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="还没有生成记录" :image-size="72" />
    </section>

    <el-dialog v-model="previewVisible" :title="`用例预览 · ${previewItem?.requirement_name || ''}`" width="min(1400px, 94vw)" top="5vh" destroy-on-close :close-on-click-modal="!revising" :close-on-press-escape="!revising" :show-close="!revising" @closed="previewItem = null">
      <div v-loading="previewLoading" class="case-preview-body" :aria-busy="previewLoading">
        <el-alert v-if="previewError" :title="previewError" type="error" show-icon :closable="false" />
        <template v-else-if="previewDetail">
          <p class="muted">{{ previewDetail.requirement_no || '未识别编号' }} · r{{ previewDetail.requirement_revision }} · {{ previewDetail.llm_model }} · {{ previewDetail.result_cases.length }} 条用例</p>
          <el-alert title="以下内容为 AI 生成草稿，执行前必须由测试人员复核。" type="info" show-icon :closable="false" />
          <el-table v-if="previewDetail.result_cases.length" :data="previewDetail.result_cases" border max-height="55vh" class="case-preview-table">
            <el-table-column label="选择" width="85" fixed="left">
              <template #header><label class="case-select-all"><input v-model="allRevisionCases" type="checkbox" aria-label="全选用例" :indeterminate.prop="revisionIndices.length > 0 && !allRevisionCases" :disabled="revising" />全选</label></template>
              <template #default="{ $index }"><input v-model="revisionIndices" type="checkbox" :value="$index" :aria-label="`选择用例 TC-${String($index + 1).padStart(4, '0')}`" :disabled="revising" /></template>
            </el-table-column>
            <el-table-column label="用例编号" width="110"><template #default="{ $index }">TC-{{ String($index + 1).padStart(4, '0') }}</template></el-table-column>
            <el-table-column prop="title" label="用例名称" min-width="180" />
            <el-table-column label="前置条件" min-width="180"><template #default="{ row }"><ul v-if="row.preconditions.length"><li v-for="(text, index) in row.preconditions" :key="index">{{ text }}</li></ul><span v-else>无</span></template></el-table-column>
            <el-table-column label="测试步骤" min-width="240"><template #default="{ row }"><ol><li v-for="(text, index) in row.steps" :key="index">{{ text }}</li></ol></template></el-table-column>
            <el-table-column label="预期结果" min-width="240"><template #default="{ row }"><ol><li v-for="(text, index) in row.expected_results" :key="index">{{ text }}</li></ol></template></el-table-column>
            <el-table-column prop="case_type" label="用例类型" width="100" />
            <el-table-column prop="priority" label="优先级" width="80" />
          </el-table>
          <el-empty v-else description="暂无用例预览数据" :image-size="72" />
          <form v-if="previewDetail.result_cases.length" class="case-revision" @submit.prevent="revise">
            <h3>局部修改 / 润色</h3>
            <p class="muted">在表格中勾选用例，再选择允许修改的字段。基于现有用例和你的方向生成新记录，未选内容保持原样；步骤与预期结果需一一对应。</p>
            <fieldset :disabled="revising"><legend>允许修改的字段</legend><label><input v-model="allRevisionFields" type="checkbox" aria-label="全选字段" :indeterminate.prop="revisionFields.length > 0 && !allRevisionFields" />全选</label><label v-for="option in fieldOptions" :key="option.value"><input v-model="revisionFields" type="checkbox" :value="option.value" />{{ option.label }}</label></fieldset>
            <label for="revision-instruction">修改方向</label>
            <el-input id="revision-instruction" v-model="revisionInstruction" type="textarea" :rows="3" :maxlength="4000" show-word-limit :disabled="revising" placeholder="例如：把名称润色得更简洁；将第 2 步拆成明确操作并补全对应预期（请同时选择步骤和预期结果）。" />
            <el-alert v-if="revisionError" :title="revisionError" type="error" :closable="false" show-icon />
            <el-button class="revise-button" native-type="submit" type="primary" :icon="MagicStick" :loading="revising" :disabled="!revisionIndices.length || !revisionFields.length || !revisionInstruction.trim()">提交修改（已选 {{ revisionIndices.length }} 条）</el-button>
          </form>
          <details class="case-preview-sources"><summary>来源与参考资料</summary><p>需求来源：{{ previewDetail.requirement_path }}</p><ul><li v-for="(item, index) in previewDetail.referenced_sources" :key="index">{{ item.source_path }}（r{{ item.revision }}）</li></ul></details>
        </template>
      </div>
      <template #footer><el-button :disabled="revising" @click="previewVisible = false">关闭</el-button><el-button v-if="previewItem" type="primary" :icon="Download" :loading="downloading === previewItem.id" :disabled="!previewItem.download_ready" @click="download(previewItem)">下载 Excel</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.knowledge-select{width:100%;margin-bottom:12px}
.case-select-all{display:inline-flex;align-items:center;gap:4px;white-space:nowrap}
.case-revision{display:grid;gap:12px;margin-top:20px;padding-top:16px;border-top:1px solid var(--ui-border)}.case-revision h3,.case-revision p{margin:0}.case-revision fieldset{display:flex;flex-wrap:wrap;gap:12px;border:1px solid var(--ui-border);padding:12px}.case-revision fieldset label{display:inline-flex;align-items:center;gap:6px}.revise-button{justify-self:start}
.additional-prompt{margin-top:18px}.additional-prompt label{display:block;margin-bottom:8px;font-size:13px}.additional-prompt p{margin:8px 0 0;font-size:12px}
.generation-actions{display:flex;align-items:center;gap:4px;white-space:nowrap}.generation-actions>span{display:inline-flex}
.case-preview-body{min-height:180px}.case-preview-table{margin-top:14px}.case-preview-table :deep(.cell){white-space:pre-wrap;overflow-wrap:anywhere}.case-preview-table :deep(td){vertical-align:top}.case-preview-table ul,.case-preview-table ol{margin:0;padding-left:20px}.case-preview-table li+li{margin-top:6px}.case-preview-sources{margin-top:14px;overflow-wrap:anywhere}.case-preview-sources summary{cursor:pointer}
.smart-generate-page{max-width:1500px}.generate-grid{display:grid;grid-template-columns:minmax(480px,1.25fr) minmax(320px,.75fr);gap:14px}.requirement-card,.action-card,.history-card{padding:20px}.history-card{margin-top:14px}.section-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:18px}.section-heading h2{margin:3px 0 0;font-size:18px}.requirement-list{display:grid;max-height:440px;margin-top:12px;overflow:auto;border-top:1px solid var(--ui-border)}.requirement-item{display:flex;align-items:flex-start;gap:10px;padding:13px 8px;border-bottom:1px solid var(--ui-border);cursor:pointer}.requirement-item:hover,.requirement-item.selected{background:var(--ui-primary-soft)}.requirement-item input{margin-top:3px;accent-color:var(--ui-primary)}.requirement-item span,.requirement-item strong,.requirement-item small{display:block;min-width:0}.requirement-item strong{font-size:13px}.requirement-item small{margin-top:5px;color:var(--ui-text-secondary);font-size:12px;overflow-wrap:anywhere}.action-card dl{display:grid;margin:0 0 16px}.action-card dl div{display:grid;grid-template-columns:90px minmax(0,1fr);gap:10px;padding:10px 0;border-bottom:1px solid var(--ui-border)}.action-card dt{color:var(--ui-text-secondary);font-size:12px}.action-card dd{margin:0;overflow-wrap:anywhere;font-size:12px}.generate-button{width:100%;margin-top:18px}@media(max-width:900px){.generate-grid{grid-template-columns:1fr}}@media(max-width:640px){.requirement-card,.action-card,.history-card{padding:15px}.action-card dl div{grid-template-columns:1fr;gap:3px}}
</style>
