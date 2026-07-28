<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { CircleCheck, RefreshRight, VideoPlay } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import RunCaptureDetails from '@/components/run-detail/RunCaptureDetails.vue'
import RunContractFiles from '@/components/run-detail/RunContractFiles.vue'
import RunContractPreviewDialog from '@/components/run-detail/RunContractPreviewDialog.vue'
import RunLogPanel from '@/components/run-detail/RunLogPanel.vue'
import RunWorkflowStrip from '@/components/run-detail/RunWorkflowStrip.vue'
import SshTerminalPanel from '@/components/SshTerminalPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import WiringTopologyDiagram from '@/components/WiringTopologyDiagram.vue'
import { useRunActions } from '@/composables/useRunActions'
import { useRunLifecycle } from '@/composables/useRunLifecycle'
import { useRunStepPresentation } from '@/composables/useRunStepPresentation'
import { useWorkflowTerminal } from '@/composables/useWorkflowTerminal'
import { useAuthStore } from '@/stores/auth'
import type {
  CaptureSnapshot,
  CaptureState,
  ContractFilePreview,
  JsonMap,
  LogScope,
  RunStep,
} from '@/types/run'
import { formatBytes, formatDate, nodeTypeText, normalizeContractFile, prettyJson } from '@/utils/runDetail'
import { businessText, resourceText } from '@/utils/status'
import type { WiringSnapshot } from '@/utils/wiring'

const route = useRoute()
const auth = useAuthStore()
const runId = Number(route.params.id)
const { load, logs, run } = useRunLifecycle(runId)
const active = ref('detail')
const selectedStepId = ref<number | null>(null)
const manualStepSelection = ref(false)
const logScope = ref<LogScope>('all')
const captureStates = reactive<Record<number, CaptureState>>({})
const contractPreviewDialog = ref(false)
const contractPreviewFile = ref<ContractFilePreview | null>(null)
const contractPreviewLoading = ref(false)
const contractPreviewError = ref('')
const contractPreviewCache = reactive<Record<number, ContractFilePreview>>({})

const canStart = computed(() => ['draft', 'resource_queue'].includes(run.value?.status || ''))
const isTerminalRunStatus = computed(() => ['completed', 'cancelled', 'execution_failed', 'parse_failed', 'precheck_failed', 'timed_out'].includes(run.value?.status || ''))
const currentStep = computed(() => findCurrentStep(run.value?.steps || []))
const selectedStep = computed(() => {
  const steps = run.value?.steps || []
  return steps.find(step => step.id === selectedStepId.value) || currentStep.value || steps[0] || null
})
const {
  handleWorkflowTerminalCommand,
  handleWorkflowTerminalError,
  handleWorkflowTerminalStatus,
  orderResource,
  orderTerminalSubtitle,
  orderWorkflowTerminalPanel,
  runWorkflowStepInTerminal,
  showWorkflowTerminal,
  slnicResource,
  slnicTerminalSubtitle,
  slnicWorkflowTerminalPanel,
  terminalCommandPendingStepId,
  workflowTerminalKind,
  workflowTerminalResource,
  workflowTerminalResourceText,
  workflowTerminalTitle,
  workflowTerminalDescription,
} = useWorkflowTerminal({
  active,
  manualStepSelection,
  reload: load,
  run,
  runId,
  selectedStep,
  selectedStepId,
})
const {
  actingStepId,
  action,
  cancel,
  download,
  stepAction,
  submitVerdict,
  verdict,
  verdictDialog,
} = useRunActions({ runId, reload: load, runTerminalStep: runWorkflowStepInTerminal })
const {
  configRows,
  contractFiles,
  inputChecksums,
  parserOutputFiles,
  parserTableRows,
  resultRows,
  selectedArtifacts,
  selectedConfig,
  selectedContractFileIds,
  selectedResult,
  showCaptureDetails,
  showRawConfig,
  showRawResult,
  summaryRows,
} = useRunStepPresentation(run, selectedStep, contractPreviewCache)
const wiringSnapshot = computed(() => {
  const value = selectedConfig.value.wiring_snapshot
  return value && typeof value === 'object' ? value as unknown as WiringSnapshot : null
})
const filteredLogs = computed(() => {
  if (logScope.value === 'all') return logs.value
  return logs.value.filter(log => log.step_id === logScope.value)
})
const logScopeLabel = computed(() => {
  if (logScope.value === 'all') return '全部日志'
  return run.value?.steps.find(step => step.id === logScope.value)?.name || '节点日志'
})
const stepLogsCount = computed(() => {
  const counts = new Map<number, number>()
  for (const log of logs.value) {
    if (log.step_id == null) continue
    counts.set(log.step_id, (counts.get(log.step_id) || 0) + 1)
  }
  return counts
})
const selectedCaptureSignature = computed(() => selectedStep.value ? snapshotSignature(selectedStep.value) : '')
const selectedCaptureState = computed(() => selectedStep.value ? captureStates[selectedStep.value.id] : undefined)
const captureSnapshots = computed(() => selectedCaptureState.value?.data || [])

function findCurrentStep(steps: RunStep[]) {
  return steps.find(step => step.status !== 'succeeded') || steps[steps.length - 1] || null
}

function snapshotSignature(step: RunStep) {
  const ids = step.result_summary?.snapshot_ids
  return Array.isArray(ids) ? ids.join(',') : ''
}

function shouldLoadCaptureDetails(step: RunStep | null) {
  return Boolean(step && ['server_config', 'database_config'].includes(step.node_type) && snapshotSignature(step))
}

function syncSelectedStep() {
  const steps = run.value?.steps || []
  const current = findCurrentStep(steps)
  const selectedStillExists = steps.some(step => step.id === selectedStepId.value)
  if (!manualStepSelection.value || !selectedStillExists) {
    selectedStepId.value = current?.id || steps[0]?.id || null
    if (!selectedStillExists) manualStepSelection.value = false
  }
  if (logScope.value !== 'all' && !steps.some(step => step.id === logScope.value)) {
    logScope.value = 'all'
  }
}

async function ensureCaptureDetails(step: RunStep | null) {
  if (!step || !shouldLoadCaptureDetails(step)) return
  const signature = snapshotSignature(step)
  const cached = captureStates[step.id]
  if (cached?.signature === signature && (cached.loading || cached.data.length || cached.error)) return
  captureStates[step.id] = { signature, loading: true, error: '', data: cached?.data || [] }
  try {
    const { data } = await api.get<CaptureSnapshot[]>(`/runs/${runId}/steps/${step.id}/capture-snapshots`)
    captureStates[step.id] = { signature, loading: false, error: '', data }
  } catch (error) {
    captureStates[step.id] = { signature, loading: false, error: errorMessage(error), data: [] }
  }
}

async function ensureContractPreviewFile(file: ContractFilePreview) {
  if (Array.isArray(file.preview_rows)) return file
  const cached = contractPreviewCache[file.id]
  if (cached && Array.isArray(cached.preview_rows)) return { ...file, ...cached }
  if (!run.value || !selectedStep.value) return file
  contractPreviewLoading.value = true
  contractPreviewError.value = ''
  try {
    const { data } = await api.get<ContractFilePreview[]>(`/scenarios/${run.value.scenario_id}/workflow/nodes/${selectedStep.value.code}/contract-files`)
    const allowedIds = new Set(selectedContractFileIds.value)
    for (const item of data) {
      const normalized = normalizeContractFile(item)
      if (!normalized || (allowedIds.size && !allowedIds.has(normalized.id))) continue
      contractPreviewCache[normalized.id] = { ...(contractPreviewCache[normalized.id] || {}), ...normalized }
    }
    return { ...file, ...(contractPreviewCache[file.id] || {}) }
  } catch (error) {
    contractPreviewError.value = errorMessage(error)
    return file
  } finally {
    contractPreviewLoading.value = false
  }
}

async function openContractPreview(file: ContractFilePreview) {
  contractPreviewDialog.value = true
  contractPreviewError.value = ''
  contractPreviewFile.value = { ...file, ...(contractPreviewCache[file.id] || {}) }
  contractPreviewFile.value = await ensureContractPreviewFile(contractPreviewFile.value)
}

function selectStep(step: RunStep) {
  selectedStepId.value = step.id
  manualStepSelection.value = true
  logScope.value = step.id
  active.value = 'detail'
}

function followCurrentStep() {
  manualStepSelection.value = false
  selectedStepId.value = currentStep.value?.id || null
  active.value = 'detail'
}

function showAllLogs() {
  logScope.value = 'all'
}

function runResource(resourceId: number) {
  const resources = run.value?.config_snapshot?.resources
  if (!Array.isArray(resources)) return null
  return resources.find((resource: JsonMap) => Number(resource.id) === resourceId) || null
}

function resourceDisplayName(resourceId: number) {
  return runResource(resourceId)?.name || `资源 ${resourceId}`
}

function resourceDisplayMeta(snapshot: CaptureSnapshot) {
  const resource = runResource(snapshot.resource_id)
  const parts = [
    `资源 ID ${snapshot.resource_id}`,
    resource?.type || snapshot.source_type,
    `第 ${snapshot.attempt} 次`,
    formatDate(snapshot.started_at),
  ]
  return parts.filter(Boolean).join(' · ')
}

watch(run, syncSelectedStep)
watch(
  [() => selectedStep.value?.id, selectedCaptureSignature],
  () => ensureCaptureDetails(selectedStep.value),
)
</script>

<template>
  <main v-if="run" class="page run-detail-page">
    <div class="page-header run-header">
      <div>
        <el-button link @click="$router.push('/runs')">← 返回运行列表</el-button>
        <div class="run-title-line"><h1 class="page-title mono">{{ run.run_number }}</h1><StatusBadge :status="run.status" show-raw /></div>
        <p class="muted">{{ businessText[run.business_code] }} · {{ run.config_snapshot?.plan?.name }} / {{ run.config_snapshot?.scenario?.name }}</p>
      </div>
      <div v-if="auth.canOperate" class="toolbar">
        <el-button v-if="canStart" type="primary" @click="action('start', '运行已就绪')">启动运行</el-button>
        <el-button
          v-if="currentStep?.status === 'pending' && run.status === 'awaiting_step_start'"
          type="primary"
          :icon="VideoPlay"
          :loading="actingStepId === currentStep.id || terminalCommandPendingStepId === currentStep.id"
          @click="currentStep && stepAction(currentStep, 'start')"
        >开始</el-button>
        <el-button
          v-if="currentStep?.status === 'waiting' && run.status === 'awaiting_step_completion'"
          type="success"
          :icon="CircleCheck"
          :loading="actingStepId === currentStep.id"
          @click="currentStep && stepAction(currentStep, 'complete')"
        >完成</el-button>
        <el-button
          v-if="currentStep?.status === 'failed' && run.status === 'awaiting_step_retry'"
          type="warning"
          :icon="RefreshRight"
          :loading="actingStepId === currentStep.id || terminalCommandPendingStepId === currentStep.id"
          @click="currentStep && stepAction(currentStep, 'retry')"
        >重试</el-button>
        <el-button v-if="run.status === 'awaiting_review'" type="success" @click="verdictDialog = true">提交人工结论</el-button>
        <el-button v-if="!isTerminalRunStatus" type="danger" plain @click="cancel">取消</el-button>
      </div>
    </div>

    <section class="summary card" aria-label="运行摘要">
      <div><span class="muted">当前状态</span><p><StatusBadge :status="run.status" show-raw /></p></div>
      <div><span class="muted">总体进度</span><el-progress :percentage="run.progress" :stroke-width="12" /></div>
      <div><span class="muted">Trace ID</span><p class="mono trace">{{ run.trace_id }}</p></div>
      <div><span class="muted">日志完整性</span><p>{{ run.logs_complete ? '完整' : '已降级，待补传' }}</p></div>
    </section>

    <el-alert v-if="run.error_message" :title="run.error_code || '运行异常'" :description="run.error_message" type="error" show-icon :closable="false" />

    <RunWorkflowStrip
      :steps="run.steps"
      :selected-step-id="selectedStep?.id || null"
      :current-step-id="currentStep?.id || null"
      :manual-selection="manualStepSelection"
      :log-counts="stepLogsCount"
      @select="selectStep"
      @follow-current="followCurrentStep"
    />

    <div class="workbench">
      <section class="card main-card">
        <el-tabs v-model="active">
          <el-tab-pane label="节点详情" name="detail">
            <div v-if="selectedStep" class="node-detail">
              <div class="node-title">
                <div>
                  <p class="eyebrow">当前查看节点</p>
                  <h2>{{ selectedStep.position }}. {{ selectedStep.name }}</h2>
                  <p class="muted">{{ nodeTypeText[selectedStep.node_type] || selectedStep.node_type }}</p>
                </div>
                <StatusBadge :status="selectedStep.status" show-raw />
              </div>

              <div class="detail-grid">
                <div v-for="item in summaryRows" :key="item.label" class="info-tile">
                  <span class="muted">{{ item.label }}</span>
                  <strong :class="{ mono: item.mono }">{{ item.value || '-' }}</strong>
                </div>
              </div>

              <section v-show="showWorkflowTerminal" class="detail-section workflow-terminal-section">
                <div class="section-heading">
                  <div>
                    <h3>{{ workflowTerminalTitle }}</h3>
                    <p class="muted">{{ workflowTerminalDescription }}</p>
                  </div>
                  <el-tag v-if="workflowTerminalResource" type="success" effect="plain">{{ workflowTerminalResource.name }}</el-tag>
                </div>
                <SshTerminalPanel
                  v-if="slnicResource"
                  v-show="workflowTerminalKind === 'slnic'"
                  ref="slnicWorkflowTerminalPanel"
                  :resource-id="slnicResource.id"
                  :title="slnicResource.name"
                  :subtitle="slnicTerminalSubtitle"
                  :active="workflowTerminalKind === 'slnic'"
                  :min-height="320"
                  @status="message => handleWorkflowTerminalStatus('slnic', message)"
                  @error="message => handleWorkflowTerminalError('slnic', message)"
                  @workflow-command="message => handleWorkflowTerminalCommand('slnic', message)"
                />
                <SshTerminalPanel
                  v-if="orderResource"
                  v-show="workflowTerminalKind === 'order'"
                  ref="orderWorkflowTerminalPanel"
                  :resource-id="orderResource.id"
                  :title="orderResource.name"
                  :subtitle="orderTerminalSubtitle"
                  :active="workflowTerminalKind === 'order'"
                  :min-height="320"
                  @status="message => handleWorkflowTerminalStatus('order', message)"
                  @error="message => handleWorkflowTerminalError('order', message)"
                  @workflow-command="message => handleWorkflowTerminalCommand('order', message)"
                />
                <div v-if="showWorkflowTerminal && !workflowTerminalResource" class="empty-line">当前运行没有{{ workflowTerminalResourceText }}，无法加载 SSH 终端</div>
              </section>

              <WiringTopologyDiagram
                v-if="selectedStep.node_type === 'wiring_confirmation'"
                :snapshot="wiringSnapshot"
                empty-message="该历史节点使用旧版占位图，确认流程仍可正常执行"
              />

              <section class="detail-section">
                <h3>节点配置</h3>
                <dl class="info-list">
                  <template v-for="row in configRows" :key="row.label">
                    <dt>{{ row.label }}</dt>
                    <dd :class="{ mono: row.mono }">{{ row.value || '-' }}</dd>
                  </template>
                </dl>
                <details v-if="showRawConfig" class="json-fold">
                  <summary>原始配置</summary>
                  <pre>{{ prettyJson(selectedConfig) }}</pre>
                </details>
              </section>

              <section class="detail-section">
                <h3>执行结果</h3>
                <el-alert v-if="selectedStep.error_message" :title="selectedStep.error_message" type="error" show-icon :closable="false" />
                <div v-else-if="!resultRows.length" class="empty-line">暂无执行结果</div>
                <dl v-else class="info-list">
                  <template v-for="row in resultRows" :key="row.label">
                    <dt>{{ row.label }}</dt>
                    <dd :class="{ mono: row.mono }">{{ row.value || '-' }}</dd>
                  </template>
                </dl>

                <RunCaptureDetails
                  v-if="showCaptureDetails"
                  :state="selectedCaptureState"
                  :signature="selectedCaptureSignature"
                  :snapshots="captureSnapshots"
                  :resource-name="resourceDisplayName"
                  :resource-meta="resourceDisplayMeta"
                />

                <RunContractFiles
                  :files="contractFiles"
                  :loading-file-id="contractPreviewLoading ? contractPreviewFile?.id || null : null"
                  @preview="openContractPreview"
                />

                <div v-if="parserTableRows.length" class="mini-table two-col">
                  <div v-for="row in parserTableRows" :key="row.name" class="mini-row">
                    <span>{{ row.name }}</span>
                    <span>{{ row.count }} 行</span>
                  </div>
                </div>

                <div v-if="inputChecksums.length" class="mini-table">
                  <div v-for="row in inputChecksums" :key="row.name" class="mini-row">
                    <span>{{ row.name }}</span>
                    <span class="mono">{{ row.checksum }}</span>
                  </div>
                </div>

                <div v-if="parserOutputFiles.length" class="file-chips">
                  <span v-for="file in parserOutputFiles" :key="file">{{ file }}</span>
                </div>

                <div v-if="selectedArtifacts.length" class="artifact-links">
                  <el-button v-for="artifact in selectedArtifacts" :key="artifact.id" link type="primary" @click="download(artifact.id)">
                    下载 {{ artifact.name }}
                  </el-button>
                </div>

                <details v-if="showRawResult" class="json-fold">
                  <summary>原始结果</summary>
                  <pre>{{ prettyJson(selectedResult) }}</pre>
                </details>
              </section>

              <section class="detail-section compact-snapshot">
                <h3>运行配置快照</h3>
                <dl class="info-list">
                  <dt>方案版本</dt><dd>{{ run.config_snapshot?.plan?.config_version || '-' }}</dd>
                  <dt>场景类型</dt><dd>{{ run.config_snapshot?.scenario?.scenario_type || '-' }}</dd>
                  <dt>场景版本</dt><dd>{{ run.config_snapshot?.scenario?.config_version || '-' }}</dd>
                  <dt>资源数</dt><dd>{{ run.resource_ids.length }}</dd>
                  <dt>创建人 ID</dt><dd>{{ run.created_by }}</dd>
                  <dt>开始时间</dt><dd>{{ formatDate(run.started_at) }}</dd>
                  <dt>结束时间</dt><dd>{{ formatDate(run.finished_at) }}</dd>
                </dl>
              </section>
            </div>
            <el-empty v-else description="暂无节点详情" :image-size="80" />
          </el-tab-pane>

          <el-tab-pane label="指标与结论" name="metrics">
            <el-table :data="run.metrics" empty-text="暂无指标">
              <el-table-column prop="name" label="指标" />
              <el-table-column label="值">
                <template #default="scope"><strong>{{ Number(scope.row.value).toFixed(3) }}</strong> {{ scope.row.unit }}</template>
              </el-table-column>
              <el-table-column prop="sample_count" label="样本数" />
            </el-table>
            <div v-if="run.verdict" class="verdict"><h3>结论</h3><p>最终结论：{{ run.verdict.final_result || '待复核' }}</p><p>{{ run.verdict.issue_description }}</p><p class="muted">{{ run.verdict.notes }}</p></div>
          </el-tab-pane>

          <el-tab-pane label="产物与报告" name="artifacts">
            <el-table :data="run.artifacts" empty-text="暂无产物">
              <el-table-column prop="name" label="文件" />
              <el-table-column prop="artifact_type" label="类型" width="140" />
              <el-table-column label="大小" width="110"><template #default="scope">{{ formatBytes(scope.row.size) }}</template></el-table-column>
              <el-table-column prop="checksum" label="SHA-256" show-overflow-tooltip />
              <el-table-column width="90"><template #default="scope"><el-button link type="primary" @click="download(scope.row.id)">下载</el-button></template></el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </section>

      <RunLogPanel
        :logs="filteredLogs"
        :total="logs.length"
        :scope-label="logScopeLabel"
        :scoped="logScope !== 'all'"
        @refresh="load"
        @show-all="showAllLogs"
      />
    </div>

    <el-dialog v-model="verdictDialog" title="提交人工复核结论" width="600px">
      <el-form label-width="100px">
        <el-form-item label="最终结论">
          <el-radio-group v-model="verdict.final_result">
            <el-radio-button value="passed">通过</el-radio-button>
            <el-radio-button value="conditional">有条件通过</el-radio-button>
            <el-radio-button value="failed">不通过</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="问题说明"><el-input v-model="verdict.issue_description" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="verdict.notes" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="verdictDialog = false">取消</el-button><el-button type="primary" @click="submitVerdict">提交并生成报告</el-button></template>
    </el-dialog>

    <RunContractPreviewDialog
      v-model="contractPreviewDialog"
      :file="contractPreviewFile"
      :loading="contractPreviewLoading"
      :error="contractPreviewError"
    />
  </main>
  <main v-else class="page run-detail-page">
    <el-skeleton :rows="10" animated />
  </main>
</template>

<style scoped src="@/styles/run-detail.css"></style>
