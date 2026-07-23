<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshRight } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { businessText, resourceText } from '@/utils/status'

const auth = useAuthStore()
const router = useRouter()
const rows = ref<any[]>([])
const dialog = ref(false)
const editing = ref<number | null>(null)
const loading = ref(false)
const databaseStep = ref(1)
const databaseOptions = ref<{ name: string; missing: boolean }[]>([])
const discoveringDatabases = ref(false)
const discoveryError = ref('')
const discoverySimulated = ref(false)

const marketEnvironments = [
  { value: 'cffex_2_0', label: '中金所2.0标准模拟市场', frontendPorts: '5110-5141', fensPorts: '5142-5145', defaultPath: '/home/user0/rem_mkt/cffex_2.0' },
  { value: 'cffex_1_0', label: '中金所1.0标准模拟市场', frontendPorts: '5210-5241', fensPorts: '5242-5245', defaultPath: '/home/user0/rem_mkt/cffex_1.0' },
  { value: 'shfe_2_0', label: '上期2.0标准模拟市场', frontendPorts: '5310-5341', fensPorts: '5342-5345', defaultPath: '/home/user0/rem_mkt/shfe_2.0' },
  { value: 'dce_7', label: '大商所7期标准模拟市场', frontendPorts: '5410-5441', fensPorts: '5442-5445', defaultPath: '/home/user0/rem_mkt/dce_1.4.4' },
  { value: 'gfex', label: '广期所标准模拟市场', frontendPorts: '5510-5541', fensPorts: '5542-5545', defaultPath: '/home/user0/rem_mkt/gfex' },
  { value: 'zce_v2_2', label: '郑商所标准模拟市场', frontendPorts: '5610/5710/5810-5641/5741/5841', fensPorts: '', defaultPath: '/home/user0/rem_mkt/zce_v2.2' },
]

const remDefaultPaths: Record<string, string> = {
  fut_mm: '/home/user0/rem_mm',
  rem_two: '/home/user0/rem_two',
  rem_two_mm: '/home/user0/rem_two_mm',
}

const orderTools = [
  { value: 'ees_ef_vi_trader_binary_api_test', path: '/home/user0/ees_ef_vi_trader_binary_api_test' },
  { value: 'ees_zf_trader_binary_api_test', path: '/home/user0/ees_zf_trader_binary_api_test' },
]

const slnicModels = [
  { value: 'SLNIC_NF11_10g_10g', path: '/home/user0/slnic/SLNIC_NF11_10g_10g_911.hw_7881.driver_12671.sw_20240528' },
  { value: 'SLNIC_NF11_1g_10g', path: '/home/user0/slnic/SLNIC_NF11_1g_10g_911.hw_7881.driver_12671.sw_20240528' },
]

const empty = () => ({
  name: '', resource_type: 'rem', market_environment: '', order_tool: '', slnic_model: '', business_code: 'fut_mm',
  host: '', ssh_port: 22, username: '', auth_type: 'password', password: '', private_key: '',
  database_engine: 'mysql', database_connection_mode: 'direct', database_host: '',
  database_port: 3306, database_names: [] as string[], database_username: '',
  database_password: '', database_tls_enabled: false,
  remote_path: '', capabilities: {}, version_info: '', notes: '', is_enabled: true,
})
const form = reactive<any>(empty())
const selectedDatabaseSummary = computed(() => form.database_names.length
  ? `已选择 ${form.database_names.length} 个数据库`
  : '尚未选择数据库')

async function load() { rows.value = (await api.get('/resources')).data }

function setMarketDefaultPath(value: string) {
  const selected = marketEnvironments.find(item => item.value === value)
  if (selected) form.remote_path = selected.defaultPath
}

function setRemDefaultPath(value: string) {
  if (form.resource_type === 'rem' && remDefaultPaths[value]) form.remote_path = remDefaultPaths[value]
}

function setOrderToolDefaultPath(value: string) {
  const selected = orderTools.find(item => item.value === value)
  if (selected) form.remote_path = selected.path
}

function setSlnicDefaultPath(value: string) {
  const selected = slnicModels.find(item => item.value === value)
  if (selected) form.remote_path = selected.path
}

function handleResourceTypeChange(value: string) {
  databaseStep.value = 1
  databaseOptions.value = []
  discoveryError.value = ''
  discoverySimulated.value = false
  if (value === 'rem') setRemDefaultPath(form.business_code)
  else if (value === 'market' && form.market_environment) setMarketDefaultPath(form.market_environment)
  else if (value === 'order') {
    form.order_tool = form.order_tool || orderTools[0].value
    setOrderToolDefaultPath(form.order_tool)
  }
  else if (value === 'slnic') {
    form.slnic_model = form.slnic_model || slnicModels[0].value
    setSlnicDefaultPath(form.slnic_model)
  }
}

function open(row?: any) {
  Object.assign(form, empty(), row || {})
  form.market_environment = row?.capabilities?.market_environment || ''
  form.order_tool = row?.capabilities?.order_tool || orderTools.find(item => item.path === row?.remote_path)?.value || ''
  form.slnic_model = row?.capabilities?.slnic_model || slnicModels.find(item => item.path === row?.remote_path)?.value || ''
  form.database_names = [...(row?.database_names || [])]
  if (!form.remote_path) {
    if (form.resource_type === 'market' && form.market_environment) setMarketDefaultPath(form.market_environment)
    else if (form.resource_type === 'order' && form.order_tool) setOrderToolDefaultPath(form.order_tool)
    else if (form.resource_type === 'slnic' && form.slnic_model) setSlnicDefaultPath(form.slnic_model)
    else setRemDefaultPath(form.business_code)
  }
  form.password = ''
  form.private_key = ''
  form.database_password = ''
  editing.value = row?.id || null
  databaseStep.value = 1
  databaseOptions.value = []
  discoveryError.value = ''
  discoverySimulated.value = false
  dialog.value = true
}

function validateDatabaseConnection() {
  if (!form.name.trim()) return '请填写资源名称'
  if (!form.business_code) return '请选择所属业务'
  if (!form.database_host.trim()) return '请填写数据库地址'
  if (!form.database_username.trim()) return '请填写数据库用户'
  if (form.database_connection_mode === 'ssh_tunnel') {
    if (!form.host.trim()) return '请填写 SSH 跳板机地址'
    if (!form.username.trim()) return '请填写 SSH 用户名'
  }
  return ''
}

function databaseDiscoveryPayload() {
  return {
    resource_id: editing.value,
    database_connection_mode: form.database_connection_mode,
    database_host: form.database_host,
    database_port: form.database_port,
    database_username: form.database_username,
    database_password: form.database_password,
    database_tls_enabled: form.database_tls_enabled,
    host: form.host,
    ssh_port: form.ssh_port,
    username: form.username,
    auth_type: form.auth_type,
    password: form.password,
    private_key: form.private_key,
  }
}

async function discoverDatabases() {
  const validationError = validateDatabaseConnection()
  if (validationError) {
    ElMessage.warning(validationError)
    return
  }
  discoveringDatabases.value = true
  discoveryError.value = ''
  try {
    const { data } = await api.post('/resources/database/discover', databaseDiscoveryPayload())
    const systemDatabases = new Set(['information_schema', 'mysql', 'performance_schema', 'sys'])
    form.database_names = form.database_names.filter((name: string) => !systemDatabases.has(name.toLowerCase()))
    const discovered = new Set<string>(data.databases)
    databaseOptions.value = [
      ...data.databases.map((name: string) => ({ name, missing: false })),
      ...form.database_names
        .filter((name: string) => !discovered.has(name))
        .map((name: string) => ({ name, missing: true })),
    ]
    discoverySimulated.value = data.simulated
    databaseStep.value = 2
    if (!databaseOptions.value.length) discoveryError.value = '当前账号没有可选择的业务数据库'
  } catch (error) {
    discoveryError.value = errorMessage(error)
    ElMessage.error(discoveryError.value)
  } finally {
    discoveringDatabases.value = false
  }
}

function backToDatabaseConnection() {
  databaseStep.value = 1
  discoveryError.value = ''
}

async function save() {
  if (form.resource_type === 'market' && !form.market_environment) {
    ElMessage.warning('请选择市场环境')
    return
  }
  if (form.resource_type === 'database' && !form.database_names.length) {
    ElMessage.warning('请至少填写一个数据库名称')
    return
  }
  if (form.resource_type === 'order' && !orderTools.some(item => item.value === form.order_tool)) {
    ElMessage.warning('请选择发单工具')
    return
  }
  if (form.resource_type === 'slnic' && !slnicModels.some(item => item.value === form.slnic_model)) {
    ElMessage.warning('请选择 SLNIC 型号')
    return
  }
  loading.value = true
  try {
    const { market_environment, order_tool, slnic_model, ...payload } = form
    const capabilities = { ...(form.capabilities || {}) }
    if (form.resource_type === 'market') {
      const selected = marketEnvironments.find(item => item.value === market_environment)!
      Object.assign(capabilities, {
        market_environment,
        market_environment_name: selected.label,
        frontend_ports: selected.frontendPorts,
        fens_ports: selected.fensPorts,
      })
    } else {
      for (const key of ['market_environment', 'market_environment_name', 'frontend_ports', 'fens_ports']) delete capabilities[key]
    }
    if (form.resource_type === 'order') {
      const selected = orderTools.find(item => item.value === order_tool)!
      Object.assign(capabilities, {
        order_tool,
        order_tool_name: selected.value,
        order_tool_default_path: selected.path,
      })
    } else {
      for (const key of ['order_tool', 'order_tool_name', 'order_tool_default_path']) delete capabilities[key]
    }
    if (form.resource_type === 'slnic') {
      const selected = slnicModels.find(item => item.value === slnic_model)!
      Object.assign(capabilities, {
        slnic_model,
        slnic_model_name: selected.value,
        slnic_default_path: selected.path,
      })
    } else {
      for (const key of ['slnic_model', 'slnic_model_name', 'slnic_default_path']) delete capabilities[key]
    }
    payload.capabilities = capabilities
    if (form.resource_type !== 'database') {
      Object.assign(payload, {
        database_engine: null,
        database_connection_mode: null,
        database_host: null,
        database_port: null,
        database_names: null,
        database_username: null,
        database_password: null,
        database_tls_enabled: false,
      })
    } else if (form.database_connection_mode === 'direct') {
      Object.assign(payload, { host: '', username: '', password: null, private_key: null })
    }
    if (editing.value) await api.put(`/resources/${editing.value}`, payload)
    else await api.post('/resources', payload)
    ElMessage.success('已保存')
    dialog.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function health(row: any) {
  try {
    const { data } = await api.post(`/resources/${row.id}/health`)
    data.ok ? ElMessage.success(data.message) : ElMessage.error(data.message)
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function remove(row: any) {
  await ElMessageBox.confirm(`确定删除资源“${row.name}”？`, '删除确认', { type: 'warning' })
  try {
    await api.delete(`/resources/${row.id}`)
    ElMessage.success('已删除')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function connectionText(row: any) {
  if (row.resource_type !== 'database') return `${row.username}@${row.host}:${row.ssh_port}`
  const target = `${row.database_username}@${row.database_host}:${row.database_port}`
  return row.database_connection_mode === 'ssh_tunnel'
    ? `${target} · 经 ${row.username}@${row.host}:${row.ssh_port}`
    : target
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">资源管理</h1>
        <p class="muted"></p>
      </div>
      <el-button v-if="auth.isAdmin" type="primary" @click="open()">新增资源</el-button>
    </div>

    <div class="card">
      <el-table :data="rows">
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column label="类型" width="120">
          <template #default="scope">{{ resourceText[scope.row.resource_type] || scope.row.resource_type }}</template>
        </el-table-column>
        <el-table-column label="业务" min-width="130">
          <template #default="scope">{{ businessText[scope.row.business_code] }}</template>
        </el-table-column>
        <el-table-column label="连接地址" min-width="280">
          <template #default="scope"><span class="mono">{{ connectionText(scope.row) }}</span></template>
        </el-table-column>
        <el-table-column label="健康" width="110">
          <template #default="scope">
            <el-tag :type="scope.row.health_status === 'healthy' ? 'success' : scope.row.health_status === 'unhealthy' ? 'danger' : 'info'" effect="plain">
              {{ scope.row.health_status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="90">
          <template #default="scope"><el-tag :type="scope.row.is_enabled ? 'success' : 'info'">{{ scope.row.is_enabled ? '是' : '否' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="320" fixed="right">
          <template #default="scope">
            <el-button link type="primary" @click="health(scope.row)">连通测试</el-button>
            <el-button v-if="scope.row.resource_type === 'database' && auth.canOperate" link type="primary" @click="router.push(`/resources/${scope.row.id}/database`)">操作台</el-button>
            <el-button v-if="['rem', 'market', 'order', 'slnic'].includes(scope.row.resource_type) && auth.canOperate" link type="primary" :disabled="!scope.row.is_enabled" @click="router.push(`/resources/${scope.row.id}/terminal`)">操作台</el-button>
            <template v-if="auth.isAdmin">
              <el-button link @click="open(scope.row)">编辑</el-button>
              <el-button link type="danger" @click="remove(scope.row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialog" :title="editing ? '编辑资源' : '新增资源'" width="820px">
      <el-steps v-if="form.resource_type === 'database'" :active="databaseStep - 1" align-center finish-status="success" class="database-steps">
        <el-step title="连接配置" description="填写 MySQL 与可选跳板机信息" />
        <el-step title="选择数据库" description="读取当前账号可见的业务数据库" />
      </el-steps>
      <el-form :model="form" label-width="110px">
        <el-row v-show="form.resource_type !== 'database' || databaseStep === 1" :gutter="16">
          <el-col :span="12"><el-form-item label="名称" required><el-input v-model="form.name" /></el-form-item></el-col>
          <el-col :span="12">
            <el-form-item label="类型" required>
              <el-select v-model="form.resource_type" style="width:100%" @change="handleResourceTypeChange">
                <el-option v-for="(value, key) in resourceText" :key="key" :label="value" :value="key" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col v-if="form.resource_type === 'market'" :span="24">
            <el-form-item label="市场环境" required>
              <el-select v-model="form.market_environment" placeholder="请选择模拟市场环境" style="width:100%" @change="setMarketDefaultPath">
                <el-option v-for="item in marketEnvironments" :key="item.value" :label="`${item.label} - 前置端口${item.frontendPorts}${item.fensPorts ? `，FENS端口${item.fensPorts}` : '，无FENS'}`" :value="item.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col v-if="form.resource_type === 'order'" :span="24">
            <el-form-item label="发单工具" required>
              <el-select v-model="form.order_tool" placeholder="请选择发单工具" style="width:100%" @change="setOrderToolDefaultPath">
                <el-option v-for="item in orderTools" :key="item.value" :label="item.value" :value="item.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col v-if="form.resource_type === 'slnic'" :span="24">
            <el-form-item label="SLNIC 型号" required>
              <el-select v-model="form.slnic_model" placeholder="请选择 SLNIC 型号" style="width:100%" @change="setSlnicDefaultPath">
                <el-option v-for="item in slnicModels" :key="item.value" :label="item.value" :value="item.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="所属业务" required>
              <el-select v-model="form.business_code" style="width:100%" @change="setRemDefaultPath">
                <el-option v-for="(value, key) in businessText" :key="key" :label="value" :value="key" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12"><el-form-item label="启用"><el-switch v-model="form.is_enabled" /></el-form-item></el-col>

          <template v-if="form.resource_type === 'database'">
            <el-col :span="24"><el-divider content-position="left">MySQL 连接</el-divider></el-col>
            <el-col :span="12">
              <el-form-item label="连接方式" required>
                <el-radio-group v-model="form.database_connection_mode">
                  <el-radio-button value="direct">直接连接</el-radio-button>
                  <el-radio-button value="ssh_tunnel">SSH 隧道</el-radio-button>
                </el-radio-group>
              </el-form-item>
            </el-col>
            <el-col :span="12"><el-form-item label="启用 TLS"><el-switch v-model="form.database_tls_enabled" /></el-form-item></el-col>
            <el-col :span="16"><el-form-item label="数据库地址" required><el-input v-model="form.database_host" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="端口" required><el-input-number v-model="form.database_port" :min="1" :max="65535" style="width:100%" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="数据库用户" required><el-input v-model="form.database_username" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="数据库密码"><el-input v-model="form.database_password" type="password" show-password :placeholder="editing && form.has_database_password ? '留空保持原密码' : ''" /></el-form-item></el-col>
          </template>

          <template v-if="form.resource_type !== 'database' || form.database_connection_mode === 'ssh_tunnel'">
            <el-col v-if="form.resource_type === 'database'" :span="24"><el-divider content-position="left">SSH 跳板机</el-divider></el-col>
            <el-col :span="16"><el-form-item :label="form.resource_type === 'database' ? '跳板机地址' : 'Linux 地址'" required><el-input v-model="form.host" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="SSH 端口" required><el-input-number v-model="form.ssh_port" :min="1" :max="65535" style="width:100%" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="SSH 用户名" required><el-input v-model="form.username" /></el-form-item></el-col>
            <el-col :span="12">
              <el-form-item label="认证方式">
                <el-radio-group v-model="form.auth_type">
                  <el-radio-button value="password">密码</el-radio-button>
                  <el-radio-button value="private_key">私钥</el-radio-button>
                </el-radio-group>
              </el-form-item>
            </el-col>
            <el-col :span="24">
              <el-form-item :label="form.auth_type === 'password' ? 'SSH 密码' : 'SSH 私钥'">
                <el-input v-if="form.auth_type === 'password'" v-model="form.password" type="password" show-password :placeholder="editing ? '留空保持原值' : ''" />
                <el-input v-else v-model="form.private_key" type="textarea" :rows="3" :placeholder="editing ? '留空保持原值' : ''" />
              </el-form-item>
            </el-col>
          </template>

          <el-col v-if="form.resource_type !== 'database'" :span="24"><el-form-item label="远端路径"><el-input v-model="form.remote_path" /></el-form-item></el-col>
          <el-col :span="24"><el-form-item label="备注"><el-input v-model="form.notes" type="textarea" /></el-form-item></el-col>
        </el-row>

        <section v-if="form.resource_type === 'database' && databaseStep === 2" class="database-selection-step">
          <div class="database-connection-summary">
            <div><small>连接方式</small><strong>{{ form.database_connection_mode === 'ssh_tunnel' ? 'SSH 隧道' : '直接连接' }}</strong></div>
            <div><small>MySQL 地址</small><strong class="mono">{{ form.database_username }}@{{ form.database_host }}:{{ form.database_port }}</strong></div>
            <div v-if="form.database_connection_mode === 'ssh_tunnel'"><small>跳板机</small><strong class="mono">{{ form.username }}@{{ form.host }}:{{ form.ssh_port }}</strong></div>
            <el-tag :type="discoverySimulated ? 'warning' : 'success'" effect="plain">{{ discoverySimulated ? '模拟发现' : '连接成功' }}</el-tag>
          </div>
          <div v-if="discoveryError" class="database-discovery-error">{{ discoveryError }}</div>
          <el-form-item label="数据库名称" required class="database-picker">
            <el-select v-model="form.database_names" multiple filterable collapse-tags :max-collapse-tags="4" placeholder="请选择需要管理的数据库" style="width:100%">
              <el-option v-for="option in databaseOptions" :key="option.name" :label="option.missing ? `${option.name}（当前未发现）` : option.name" :value="option.name">
                <div class="database-option"><span class="mono">{{ option.name }}</span><el-tag v-if="option.missing" type="warning" effect="plain" size="small">当前未发现</el-tag></div>
              </el-option>
            </el-select>
          </el-form-item>
          <div class="database-selection-meta">
            <span>{{ selectedDatabaseSummary }}</span>
            <el-button :icon="RefreshRight" text :loading="discoveringDatabases" @click="discoverDatabases">重新读取</el-button>
          </div>
        </section>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <template v-if="form.resource_type === 'database'">
          <el-button v-if="databaseStep === 2" @click="backToDatabaseConnection">上一步</el-button>
          <el-button v-if="databaseStep === 1" type="primary" :loading="discoveringDatabases" @click="discoverDatabases">下一步</el-button>
          <el-button v-else type="primary" :loading="loading" :disabled="!form.database_names.length" @click="save">保存</el-button>
        </template>
        <el-button v-else type="primary" :loading="loading" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.database-steps{margin:-4px 0 22px;padding:0 80px}.database-selection-step{min-height:330px;padding:6px 18px 0}.database-connection-summary{display:flex;align-items:center;gap:22px;padding:14px 16px;margin-bottom:22px;background:#f5f8fa;border:1px solid #e3e9ef;border-radius:6px}.database-connection-summary>div{display:flex;min-width:0;flex-direction:column;gap:4px}.database-connection-summary small{color:#83909b;font-size:11px}.database-connection-summary strong{overflow:hidden;max-width:250px;color:#334250;font-size:12px;font-weight:600;text-overflow:ellipsis;white-space:nowrap}.database-connection-summary .el-tag{margin-left:auto}.database-picker{margin:0}.database-picker :deep(.el-select__wrapper){min-height:42px}.database-option{display:flex;align-items:center;justify-content:space-between;width:100%;gap:12px}.database-selection-meta{display:flex;align-items:center;justify-content:space-between;margin:8px 0 0 110px;color:#7f8b96;font-size:12px}.database-discovery-error{margin:0 0 14px 110px;padding:9px 12px;border-radius:5px;background:#fff1f2;color:#a33a4c;font-size:12px}.mono{font-family:Cascadia Code,Consolas,monospace}
</style>
