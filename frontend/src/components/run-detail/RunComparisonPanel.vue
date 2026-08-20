<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Delete, Refresh, Select } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import { ElMessage, ElMessageBox } from '@/ui/elementPlusServices'
import type { RunComparison, RunComparisonCandidate, RunComparisonMetric } from '@/types/run'
import { formatBeijingDateTime } from '@/utils/time'

const props = defineProps<{
  runId: number
  canOperate: boolean
  hasMetrics: boolean
}>()

const comparison = ref<RunComparison | null>(null)
const candidates = ref<RunComparisonCandidate[]>([])
const selectedBaselineId = ref<number | null>(null)
const loading = ref(false)
const saving = ref(false)
const deleting = ref(false)
const loadError = ref('')

const recommended = computed(() => candidates.value.find(item => item.recommended) || null)
const selectedCandidate = computed(() => candidates.value.find(item => item.run_id === selectedBaselineId.value) || null)
const compatibleMetricCount = computed(() => comparison.value?.rows.filter(item => (
  item.assessment === 'improved' || item.assessment === 'stable' || item.assessment === 'regressed'
)).length || 0)

function candidateLabel(candidate: RunComparisonCandidate) {
  const conclusion = candidate.verdict === 'passed'
    ? '通过'
    : candidate.verdict === 'failed'
      ? '不通过'
      : candidate.verdict === 'conditional' ? '有条件通过' : '未复核'
  const compatibility = candidate.compatible ? '可比' : '存在差异'
  return `${candidate.run_number} · ${conclusion} · ${compatibility}`
}

function candidateNote(candidate: RunComparisonCandidate) {
  if (!candidate.compatible) return candidate.warnings.join('；')
  if (candidate.warnings.length) return `核心统计配置一致；${candidate.warnings.join('；')}`
  return '统计配置一致，可作为可比基线。'
}

function assessmentText(value: RunComparisonMetric['assessment']) {
  return {
    improved: '下降',
    stable: '持平',
    regressed: '上升',
    added: '新增',
    missing: '缺失',
    incompatible: '不可比',
  }[value]
}

function assessmentType(value: RunComparisonMetric['assessment']): 'success' | 'info' | 'danger' | 'warning' {
  if (value === 'improved') return 'success'
  if (value === 'regressed') return 'danger'
  if (value === 'stable') return 'info'
  return 'warning'
}

function metricValue(value: number | null, unit: string) {
  if (value == null) return '—'
  return `${Number(value).toFixed(3)} ${unit}`
}

function deltaValue(row: RunComparisonMetric) {
  if (row.absolute_delta == null) return '—'
  const sign = row.absolute_delta > 0 ? '+' : ''
  return `${sign}${Number(row.absolute_delta).toFixed(3)} ${row.unit}`
}

function percentageValue(value: number | null) {
  if (value == null) return '基线为 0'
  const sign = value > 0 ? '+' : ''
  return `${sign}${Number(value).toFixed(2)}%`
}

async function loadComparison() {
  if (!props.hasMetrics) return
  loading.value = true
  loadError.value = ''
  try {
    const [comparisonResponse, candidateResponse] = await Promise.all([
      api.get<RunComparison | null>(`/runs/${props.runId}/comparison`),
      api.get<RunComparisonCandidate[]>(`/runs/${props.runId}/comparison-candidates`),
    ])
    comparison.value = comparisonResponse.data
    candidates.value = candidateResponse.data
    selectedBaselineId.value = comparison.value?.baseline_run_id
      || recommended.value?.run_id
      || candidates.value[0]?.run_id
      || null
  } catch (error) {
    loadError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

function selectRecommended() {
  if (recommended.value) selectedBaselineId.value = recommended.value.run_id
}

async function save() {
  const candidate = selectedCandidate.value
  if (!candidate) {
    ElMessage.warning('请选择一个基线运行')
    return
  }
  if (!candidate.compatible) {
    try {
      await ElMessageBox.confirm(
        `所选运行存在可比性差异：${candidate.warnings.join('；') || '配置不完全一致'}。仍要保存该快照吗？`,
        '确认保存非完全可比的基线',
        { type: 'warning', confirmButtonText: '仍然保存', cancelButtonText: '返回检查' },
      )
    } catch (error) {
      if (error === 'cancel' || error === 'close') return
      throw error
    }
  }
  saving.value = true
  try {
    const response = await api.put<RunComparison>(`/runs/${props.runId}/comparison`, {
      baseline_run_id: candidate.run_id,
    })
    comparison.value = response.data
    ElMessage.success('运行对比快照已保存；重新生成报告后可纳入报告')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function remove() {
  try {
    await ElMessageBox.confirm(
      '确定删除已保存的运行对比快照？已有历史报告不会改变。',
      '删除运行对比',
      { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' },
    )
    deleting.value = true
    await api.delete(`/runs/${props.runId}/comparison`)
    comparison.value = null
    selectedBaselineId.value = recommended.value?.run_id || candidates.value[0]?.run_id || null
    ElMessage.success('运行对比快照已删除')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  } finally {
    deleting.value = false
  }
}

onMounted(loadComparison)
</script>

<template>
  <section class="comparison-panel" aria-labelledby="run-comparison-heading">
    <header class="comparison-heading">
      <div>
        <h3 id="run-comparison-heading">同场景运行对比</h3>
        <p>固定当前与基线的统计分析快照。延迟指标下降表示改善，上升表示退化。</p>
      </div>
      <el-button :icon="Refresh" :loading="loading" plain aria-label="刷新运行对比" @click="loadComparison">刷新</el-button>
    </header>

    <el-alert
      v-if="!hasMetrics"
      title="当前运行尚无统计指标"
      description="数据统计节点成功生成指标后，才能选择同场景基线。"
      type="info"
      show-icon
      :closable="false"
    />
    <el-alert
      v-else-if="loadError"
      title="运行对比加载失败"
      :description="loadError"
      type="error"
      show-icon
      :closable="false"
    />
    <el-skeleton v-else-if="loading" :rows="6" animated />

    <template v-else-if="hasMetrics">
      <fieldset v-if="canOperate" class="baseline-picker">
        <legend>选择基线</legend>
        <div class="baseline-control">
          <el-select
            v-model="selectedBaselineId"
            filterable
            placeholder="选择同场景已完成运行"
            aria-label="基线运行"
            no-data-text="暂无同场景已完成运行"
          >
            <el-option
              v-for="candidate in candidates"
              :key="candidate.run_id"
              :label="candidateLabel(candidate)"
              :value="candidate.run_id"
            >
              <div class="candidate-option">
                <span>{{ candidate.run_number }}</span>
                <small>{{ candidateLabel(candidate).split(' · ').slice(1).join(' · ') }}</small>
              </div>
            </el-option>
          </el-select>
          <el-button v-if="recommended" :icon="Select" plain @click="selectRecommended">使用推荐基线</el-button>
          <el-button type="primary" :loading="saving" :disabled="!selectedBaselineId" @click="save">
            {{ comparison ? '更新对比快照' : '保存对比快照' }}
          </el-button>
          <el-button v-if="comparison" :icon="Delete" type="danger" plain :loading="deleting" @click="remove">删除</el-button>
        </div>
        <p v-if="selectedCandidate" class="candidate-note" aria-live="polite">
          匹配 {{ selectedCandidate.matched_metric_count }} / {{ selectedCandidate.metric_count }} 个指标；
          {{ candidateNote(selectedCandidate) }}
        </p>
      </fieldset>

      <div v-if="comparison" class="comparison-content" aria-live="polite">
        <div class="snapshot-strip">
          <div><span>当前运行</span><strong class="mono">{{ comparison.target_run_number }}</strong></div>
          <div><span>基线运行</span><strong class="mono">{{ comparison.baseline_run_number }}</strong></div>
          <div><span>可比指标</span><strong>{{ compatibleMetricCount }} / {{ comparison.rows.length }}</strong></div>
          <div><span>快照状态</span><el-tag :type="comparison.compatible ? 'success' : 'warning'" effect="plain">{{ comparison.compatible ? '统计配置一致' : '存在差异' }}</el-tag></div>
          <div><span>保存时间</span><strong>{{ formatBeijingDateTime(comparison.updated_at) }}</strong></div>
        </div>

        <el-alert
          v-if="comparison.target_metrics_stale"
          title="当前运行在快照保存后又执行了统计分析"
          description="表格仍展示已固定的旧快照；请更新对比快照以使用最新分析结果。"
          type="warning"
          show-icon
          :closable="false"
        />
        <el-alert
          v-if="comparison.baseline_metrics_changed"
          title="基线运行在快照保存后又执行了统计分析"
          description="当前比较仍固定使用保存时的基线批次，不会被后续分析静默改写。"
          type="info"
          show-icon
          :closable="false"
        />
        <el-alert
          v-if="comparison.warnings.length"
          title="可比性说明"
          :description="comparison.warnings.join('；')"
          type="warning"
          show-icon
          :closable="false"
        />

        <div class="comparison-table-scroll">
          <el-table :data="comparison.rows" empty-text="暂无可比较指标" class="comparison-table">
            <el-table-column label="指标" min-width="230">
              <template #default="scope">
                <div class="metric-identity">
                  <strong>{{ scope.row.metric_label }}</strong>
                  <span>{{ scope.row.step_name }} · {{ scope.row.source_file }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="基线值" min-width="135">
              <template #default="scope"><span class="mono metric-number">{{ metricValue(scope.row.baseline_value, scope.row.unit) }}</span></template>
            </el-table-column>
            <el-table-column label="当前值" min-width="135">
              <template #default="scope"><span class="mono metric-number">{{ metricValue(scope.row.target_value, scope.row.unit) }}</span></template>
            </el-table-column>
            <el-table-column label="绝对变化" min-width="135">
              <template #default="scope"><span class="mono metric-number">{{ deltaValue(scope.row) }}</span></template>
            </el-table-column>
            <el-table-column label="变化率" min-width="110">
              <template #default="scope"><span class="mono metric-number">{{ percentageValue(scope.row.percentage_delta) }}</span></template>
            </el-table-column>
            <el-table-column label="判断" width="96" align="center">
              <template #default="scope"><el-tag :type="assessmentType(scope.row.assessment)" effect="plain">{{ assessmentText(scope.row.assessment) }}</el-tag></template>
            </el-table-column>
          </el-table>
        </div>
      </div>

      <el-empty v-else description="尚未保存运行对比快照" :image-size="76">
        <p class="empty-guidance">选择一个同场景已完成运行作为基线；系统会固定双方当前分析批次。</p>
      </el-empty>
    </template>
  </section>
</template>

<style scoped>
.comparison-panel{display:grid;gap:16px}.comparison-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.comparison-heading h3{margin:0;font-size:16px}.comparison-heading p{margin:5px 0 0;color:var(--ui-text-secondary);font-size:12px}.baseline-picker{display:grid;gap:9px;margin:0;padding:14px 16px;border:1px solid var(--ui-border);border-radius:var(--ui-radius-panel);background:var(--ui-surface-subtle)}.baseline-picker legend{padding:0 6px;color:var(--ui-text-primary);font-size:12px;font-weight:700}.baseline-control{display:flex;align-items:center;gap:9px}.baseline-control :deep(.el-select){min-width:300px;flex:1}.candidate-option{display:flex;align-items:center;justify-content:space-between;gap:16px}.candidate-option span{font-family:"Cascadia Code",Consolas,monospace;font-size:11px}.candidate-option small{color:var(--ui-text-tertiary)}.candidate-note{margin:0;color:var(--ui-text-secondary);font-size:11px}.comparison-content{display:grid;gap:12px}.snapshot-strip{display:grid;grid-template-columns:1.2fr 1.2fr .7fr .8fr 1fr;overflow:hidden;border:1px solid var(--ui-border);border-radius:var(--ui-radius-panel);background:var(--ui-border)}.snapshot-strip>div{display:grid;gap:5px;padding:12px 14px;background:var(--ui-surface)}.snapshot-strip>div+div{border-left:1px solid var(--ui-border)}.snapshot-strip span{color:var(--ui-text-tertiary);font-size:10px}.snapshot-strip strong{overflow:hidden;font-size:12px;text-overflow:ellipsis;white-space:nowrap}.comparison-table-scroll{overflow-x:auto;border:1px solid var(--ui-border);border-radius:var(--ui-radius-panel)}.comparison-table{min-width:900px}.metric-identity strong,.metric-identity span{display:block}.metric-identity strong{font-size:12px}.metric-identity span{margin-top:4px;color:var(--ui-text-tertiary);font-size:10px}.metric-number{font-size:11px}.empty-guidance{max-width:480px;margin:0;color:var(--ui-text-secondary);font-size:12px;text-align:center}
@media(max-width:1000px){.snapshot-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.snapshot-strip>div{border-bottom:1px solid var(--ui-border)}.snapshot-strip>div:nth-child(odd){border-left:0}.snapshot-strip>div:last-child{grid-column:1/-1;border-bottom:0;border-left:0}.baseline-control{align-items:stretch;flex-direction:column}.baseline-control :deep(.el-select){width:100%;min-width:0}}
@media(max-width:600px){.comparison-heading{align-items:stretch;flex-direction:column}.snapshot-strip{grid-template-columns:1fr}.snapshot-strip>div+div{border-left:0}.snapshot-strip>div:last-child{grid-column:auto}.baseline-picker{padding:12px}}
</style>
