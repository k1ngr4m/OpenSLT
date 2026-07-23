<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { businessText, statusText, statusType } from '@/utils/status'

const route = useRoute()
const auth = useAuthStore()
const run = ref<any>(null)
const logs = ref<any[]>([])
const active = ref('timeline')
const verdictDialog = ref(false)
const verdict = reactive({ final_result: 'passed', issue_description: '', notes: '' })
let socket: WebSocket | null = null
let timer: number | undefined
const runId = Number(route.params.id)
const canStart = computed(() => ['draft', 'resource_queue'].includes(run.value?.status))
const terminal = computed(() => ['completed', 'cancelled', 'execution_failed', 'parse_failed', 'precheck_failed', 'timed_out'].includes(run.value?.status))

async function load() {
  run.value = (await api.get(`/runs/${runId}`)).data
  logs.value = (await api.get(`/runs/${runId}/logs`)).data
}

async function action(path: string, message: string) {
  try {
    await api.post(`/runs/${runId}/${path}`)
    ElMessage.success(message)
    setTimeout(load, 300)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function cancel() {
  await ElMessageBox.confirm('取消后将执行安全清理并释放资源，确定继续？', '取消运行', { type: 'warning' })
  action('cancel', '取消指令已提交')
}

async function submitVerdict() {
  try {
    await api.post(`/runs/${runId}/verdict`, verdict)
    ElMessage.success('结论和报告已生成')
    verdictDialog.value = false
    load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function download(id: number) {
  try {
    const response = await api.get(`/artifacts/${id}/download`, { responseType: 'blob' })
    const disposition = response.headers['content-disposition'] || ''
    const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] || `artifact-${id}`
    const link = document.createElement('a')
    link.href = URL.createObjectURL(response.data)
    link.download = filename
    link.click()
    URL.revokeObjectURL(link.href)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function connect() {
  const token = localStorage.getItem('access_token')
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  socket = new WebSocket(`${protocol}://${location.host}/api/v1/ws/runs/${runId}?token=${token}`)
  socket.onmessage = event => {
    const payload = JSON.parse(event.data)
    if (payload.type === 'log') logs.value.push(payload.data)
    if (payload.type === 'status' || payload.type === 'snapshot') {
      if (run.value) {
        run.value.status = payload.status
        run.value.progress = payload.progress
      }
      setTimeout(load, 200)
    }
  }
}

onMounted(() => {
  load()
  connect()
  timer = window.setInterval(load, 5000)
})
onBeforeUnmount(() => {
  socket?.close()
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div v-if="run" class="page">
    <div class="page-header">
      <div>
        <el-button link @click="$router.push('/runs')">← 返回运行列表</el-button>
        <h1 class="page-title mono">{{ run.run_number }}</h1>
        <p class="muted">{{ businessText[run.business_code] }} · {{ run.config_snapshot?.plan?.name }} / {{ run.config_snapshot?.scenario?.name }}</p>
      </div>
      <div v-if="auth.canOperate" class="toolbar">
        <el-button v-if="canStart" type="primary" @click="action('start', '运行已启动')">启动运行</el-button>
        <el-button v-if="run.status === 'awaiting_wiring'" type="warning" @click="action('confirm-wiring', '已确认接线，自动流程继续')">确认接线完成</el-button>
        <el-button v-if="run.status === 'awaiting_review'" type="success" @click="verdictDialog = true">提交人工结论</el-button>
        <el-button v-if="!terminal" type="danger" plain @click="cancel">取消</el-button>
      </div>
    </div>
    <div class="summary card">
      <div><span class="muted">当前状态</span><p><el-tag size="large" :type="statusType(run.status)">{{ statusText[run.status] || run.status }}</el-tag></p></div>
      <div><span class="muted">总体进度</span><el-progress :percentage="run.progress" :stroke-width="12" /></div>
      <div><span class="muted">Trace ID</span><p class="mono trace">{{ run.trace_id }}</p></div>
      <div><span class="muted">日志完整性</span><p>{{ run.logs_complete ? '完整' : '已降级，待补传' }}</p></div>
    </div>
    <el-alert v-if="run.status === 'awaiting_wiring'" title="人工确认节点" description="请完成机房接线，确认四方向链路无误后点击“确认接线完成”。" type="warning" show-icon :closable="false" />
    <el-alert v-if="run.error_message" :title="run.error_code || '运行异常'" :description="run.error_message" type="error" show-icon :closable="false" />
    <div class="content">
      <div class="card main-card">
        <el-tabs v-model="active">
          <el-tab-pane label="步骤时间线" name="timeline">
            <el-timeline><el-timeline-item v-for="step in run.steps" :key="step.id" :type="statusType(step.status) as any" :timestamp="step.duration_ms != null ? `${step.duration_ms} ms` : ''"><div class="step"><strong>{{ step.position }}. {{ step.name }}</strong><el-tag size="small" :type="statusType(step.status)">{{ statusText[step.status] || step.status }}</el-tag></div><p v-if="step.error_message" class="danger">{{ step.error_message }}</p></el-timeline-item></el-timeline>
          </el-tab-pane>
          <el-tab-pane label="实时日志" name="logs">
            <div class="log-toolbar"><span class="muted">{{ logs.length }} 条记录</span><el-button size="small" @click="load">刷新</el-button></div>
            <div class="log-view"><div v-for="log in logs" :key="log.id"><span class="muted">{{ new Date(log.created_at).toLocaleTimeString() }}</span> <span :class="{ danger: log.level === 'ERROR' }">[{{ log.level }}]</span> {{ log.message }}</div><div v-if="!logs.length" class="muted">暂无日志</div></div>
          </el-tab-pane>
          <el-tab-pane label="指标与结论" name="metrics">
            <el-table :data="run.metrics"><el-table-column prop="name" label="指标" /><el-table-column label="值"><template #default="scope"><strong>{{ Number(scope.row.value).toFixed(3) }}</strong> {{ scope.row.unit }}</template></el-table-column><el-table-column prop="sample_count" label="样本数" /></el-table>
            <div v-if="run.verdict" class="verdict"><h3>结论</h3><p>最终结论：{{ run.verdict.final_result || '待复核' }}</p><p>{{ run.verdict.issue_description }}</p><p class="muted">{{ run.verdict.notes }}</p></div>
          </el-tab-pane>
          <el-tab-pane label="产物与报告" name="artifacts">
            <el-table :data="run.artifacts"><el-table-column prop="name" label="文件" /><el-table-column prop="artifact_type" label="类型" /><el-table-column label="大小"><template #default="scope">{{ (scope.row.size / 1024).toFixed(1) }} KB</template></el-table-column><el-table-column prop="checksum" label="SHA-256" show-overflow-tooltip /><el-table-column width="90"><template #default="scope"><el-button link type="primary" @click="download(scope.row.id)">下载</el-button></template></el-table-column></el-table>
          </el-tab-pane>
        </el-tabs>
      </div>
      <aside class="card side"><h3>配置快照</h3><dl><dt>方案版本</dt><dd>{{ run.config_snapshot?.plan?.config_version }}</dd><dt>场景类型</dt><dd>{{ run.config_snapshot?.scenario?.scenario_type }}</dd><dt>场景版本</dt><dd>{{ run.config_snapshot?.scenario?.config_version }}</dd><dt>资源数</dt><dd>{{ run.resource_ids.length }}</dd><dt>创建人 ID</dt><dd>{{ run.created_by }}</dd><dt>开始时间</dt><dd>{{ run.started_at ? new Date(run.started_at).toLocaleString() : '-' }}</dd><dt>结束时间</dt><dd>{{ run.finished_at ? new Date(run.finished_at).toLocaleString() : '-' }}</dd></dl></aside>
    </div>
    <el-dialog v-model="verdictDialog" title="提交人工复核结论" width="600px">
      <el-form label-width="100px"><el-form-item label="最终结论"><el-radio-group v-model="verdict.final_result"><el-radio-button value="passed">通过</el-radio-button><el-radio-button value="conditional">有条件通过</el-radio-button><el-radio-button value="failed">不通过</el-radio-button></el-radio-group></el-form-item><el-form-item label="问题说明"><el-input v-model="verdict.issue_description" type="textarea" :rows="3" /></el-form-item><el-form-item label="备注"><el-input v-model="verdict.notes" type="textarea" :rows="3" /></el-form-item></el-form>
      <template #footer><el-button @click="verdictDialog = false">取消</el-button><el-button type="primary" @click="submitVerdict">提交并生成报告</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.summary{display:grid;grid-template-columns:180px 1fr 1.5fr 180px;gap:28px;padding:18px 22px;margin-bottom:16px}.summary p{margin:10px 0 0}.trace{font-size:12px;overflow:hidden;text-overflow:ellipsis}.content{display:grid;grid-template-columns:1fr 280px;gap:16px;margin-top:16px}.main-card{padding:0 20px 20px;min-height:530px}.side{padding:18px}.side h3{margin-top:0}.side dl{font-size:13px}.side dt{color:#7b8794;margin-top:16px}.side dd{margin:4px 0}.step{display:flex;align-items:center;justify-content:space-between}.log-toolbar{display:flex;justify-content:space-between;margin-bottom:8px}.verdict{padding:16px;background:#f7faf9;border-radius:8px;margin-top:14px}
</style>
