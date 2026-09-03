<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Download, MagicStick, Search, Setting } from '@element-plus/icons-vue'
import { ElMessage } from '@/ui/elementPlusServices'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { formatBeijingDateTime } from '@/utils/time'

interface Requirement { source_path: string; revision: string; requirement_no: string | null; requirement_name: string }
interface Generation { id: number; requirement_path: string; requirement_revision: string; requirement_no: string | null; requirement_name: string; status: string; llm_model: string; case_count: number; error: string | null; download_ready: boolean; created_at: string }

const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const generating = ref(false)
const downloading = ref<number | null>(null)
const query = ref('')
const requirements = ref<Requirement[]>([])
const generations = ref<Generation[]>([])
const selectedPath = ref('')
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
  try {
    const [requirementResponse, generationResponse] = await Promise.all([
      api.get<Requirement[]>('/smart-cases/requirements'),
      api.get<Generation[]>('/smart-cases/generations'),
    ])
    requirements.value = requirementResponse.data
    generations.value = generationResponse.data
    if (selectedPath.value && !requirements.value.some(item => item.source_path === selectedPath.value)) selectedPath.value = ''
  } catch (error) {
    if (showError) ElMessage.error(errorMessage(error))
  } finally { loading.value = false }
}

async function generate() {
  if (!selected.value) return
  generating.value = true
  try {
    await api.post('/smart-cases/generations', { requirement_path: selected.value.source_path })
    ElMessage.success('生成任务已提交，完成后可下载 Excel 草稿')
    await load(false)
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { generating.value = false }
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

onMounted(async () => {
  await load()
  timer = setInterval(() => { if (hasRunning.value) load(false) }, 5000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div v-loading="loading" class="page smart-generate-page">
    <header class="page-header">
      <div><span class="page-kicker">知识驱动测试设计</span><h1 class="page-title">智能用例</h1><p class="muted">从最近一次 SVN 成功索引中选择需求，生成可追溯的人工执行 Excel 用例草稿</p></div>
      <el-button v-if="auth.isAdmin" :icon="Setting" @click="router.push('/smart-cases/settings')">配置</el-button>
    </header>

    <div class="generate-grid">
      <section class="card requirement-card" aria-labelledby="requirement-title">
        <div class="section-heading"><div><span class="page-kicker">第一步</span><h2 id="requirement-title">选择需求</h2></div><el-tag effect="plain">{{ visibleRequirements.length }} 项</el-tag></div>
        <el-input v-model="query" clearable aria-label="按需求编号或名称检索" placeholder="输入需求编号、名称或 SVN 路径"><template #prefix><el-icon><Search /></el-icon></template></el-input>
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
          <dl><div><dt>需求编号</dt><dd>{{ selected.requirement_no || '未从文件名识别' }}</dd></div><div><dt>需求名称</dt><dd>{{ selected.requirement_name }}</dd></div><div><dt>知识版本</dt><dd>r{{ selected.revision }}</dd></div><div><dt>SVN 来源</dt><dd>{{ selected.source_path }}</dd></div></dl>
          <el-alert type="info" :closable="false" show-icon title="系统会检索相关知识作为参考；输出为草稿，执行前必须人工复核。" />
          <el-button class="generate-button" type="primary" size="large" :icon="MagicStick" :loading="generating" @click="generate">生成 Excel 用例草稿</el-button>
        </template>
        <el-empty v-else description="请先从左侧选择一个需求" :image-size="72" />
      </section>
    </div>

    <section class="card history-card" aria-labelledby="history-title">
      <div class="section-heading"><div><span class="page-kicker">生成记录</span><h2 id="history-title">最近任务</h2></div></div>
      <el-table v-if="generations.length" :data="generations">
        <el-table-column prop="requirement_no" label="需求编号" width="130"><template #default="{ row }">{{ row.requirement_no || '—' }}</template></el-table-column>
        <el-table-column prop="requirement_name" label="需求名称" min-width="220" show-overflow-tooltip />
        <el-table-column prop="requirement_revision" label="版本" width="90"><template #default="{ row }">r{{ row.requirement_revision }}</template></el-table-column>
        <el-table-column prop="llm_model" label="模型" min-width="150" show-overflow-tooltip />
        <el-table-column label="状态" width="110"><template #default="{ row }"><el-tooltip :content="row.error || ''" :disabled="!row.error"><el-tag :type="row.status === 'succeeded' ? 'success' : row.status === 'failed' ? 'danger' : 'warning'" effect="plain">{{ statusText[row.status] || row.status }}<template v-if="row.case_count"> · {{ row.case_count }}</template></el-tag></el-tooltip></template></el-table-column>
        <el-table-column label="提交时间" width="180"><template #default="{ row }">{{ formatBeijingDateTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="110" fixed="right"><template #default="{ row }"><el-button text type="primary" :icon="Download" :loading="downloading === row.id" :disabled="!row.download_ready" @click="download(row)">下载</el-button></template></el-table-column>
      </el-table>
      <el-empty v-else description="还没有生成记录" :image-size="72" />
    </section>
  </div>
</template>

<style scoped>
.smart-generate-page{max-width:1500px}.generate-grid{display:grid;grid-template-columns:minmax(480px,1.25fr) minmax(320px,.75fr);gap:14px}.requirement-card,.action-card,.history-card{padding:20px}.history-card{margin-top:14px}.section-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:18px}.section-heading h2{margin:3px 0 0;font-size:18px}.requirement-list{display:grid;max-height:440px;margin-top:12px;overflow:auto;border-top:1px solid var(--ui-border)}.requirement-item{display:flex;align-items:flex-start;gap:10px;padding:13px 8px;border-bottom:1px solid var(--ui-border);cursor:pointer}.requirement-item:hover,.requirement-item.selected{background:var(--ui-primary-soft)}.requirement-item input{margin-top:3px;accent-color:var(--ui-primary)}.requirement-item span,.requirement-item strong,.requirement-item small{display:block;min-width:0}.requirement-item strong{font-size:13px}.requirement-item small{margin-top:5px;color:var(--ui-text-secondary);font-size:11px;overflow-wrap:anywhere}.action-card dl{display:grid;margin:0 0 16px}.action-card dl div{display:grid;grid-template-columns:90px minmax(0,1fr);gap:10px;padding:10px 0;border-bottom:1px solid var(--ui-border)}.action-card dt{color:var(--ui-text-secondary);font-size:11px}.action-card dd{margin:0;overflow-wrap:anywhere;font-size:12px}.generate-button{width:100%;margin-top:18px}@media(max-width:900px){.generate-grid{grid-template-columns:1fr}}@media(max-width:640px){.requirement-card,.action-card,.history-card{padding:15px}.action-card dl div{grid-template-columns:1fr;gap:3px}}
</style>
