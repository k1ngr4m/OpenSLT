<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from '@/ui/elementPlusServices'
import { RefreshRight, Search } from '@element-plus/icons-vue'
import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { businessText, resourceText } from '@/utils/status'
import {
  parserActionOptions,
  parserActionsFromCapabilities,
  parserActionsPayload,
} from '@/utils/parserActions'

const auth = useAuthStore()
const router = useRouter()
const rows = ref<any[]>([])
const dialog = ref(false)
const editing = ref<number | null>(null)
const loading = ref(false)
const listLoading = ref(false)
const listError = ref('')
const databaseStep = ref(1)
const databaseOptions = ref<{ name: string; missing: boolean }[]>([])
const discoveringDatabases = ref(false)
const discoveryError = ref('')
const testingConnection = ref(false)
const connectionTestResult = ref<{ ok: boolean; message: string } | null>(null)
const filters = reactive({ keyword: '', type: '', business: '', health: '' })
const filteredRows = computed(() => rows.value.filter(row => {
  const query = filters.keyword.trim().toLowerCase()
  const text = `${row.name} ${connectionText(row)}`.toLowerCase()
  return (!query || text.includes(query))
    && (!filters.type || row.resource_type === filters.type)
    && (!filters.business || row.business_code === filters.business)
    && (!filters.health || row.health_status === filters.health)
}))

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
const orderActionOptions = [
  'new_order', 'new_order_simple', 'new_quote', 'new_quote_simple',
  'new_arbi_order', 'new_arbi_order_simple', 'cxl_order', 'cxl_quote', 'stop_order',
]

const slnicModels = [
  { value: 'SLNIC_NF11_10g_10g', path: '/home/user0/slnic/SLNIC_NF11_10g_10g_911.hw_7881.driver_12671.sw_20240528' },
  { value: 'SLNIC_NF11_1g_10g', path: '/home/user0/slnic/SLNIC_NF11_1g_10g_911.hw_7881.driver_12671.sw_20240528' },
]

const parserTools = [
  'soft_cffex_speed_analysis',
  'soft_cffex_speed_analysis_v2',
  'soft_shfe_speed_analysis_v2',
  'soft_czce_speed_analysis',
  'soft_dce_speed_analysis_v7',
  'soft_gfex_speed_analysis',
  'hwcffex_1414_2.0',
  'hwshfe_1414_2.0',
  'mg11',
]

const empty = () => ({
  name: '', resource_type: 'rem', market_environment: '', order_tool: '', order_actions: [...orderActionOptions], slnic_model: '', parser_tool: '', parser_actions: [...parserActionOptions], business_code: 'fut_mm',
  host: '', ssh_port: 22, username: '', auth_type: 'password', password: '', private_key: '',
  database_engine: 'mysql', database_connection_mode: 'direct', database_host: '',
  database_port: 3306, database_names: [] as string[], database_username: '',
  database_password: '', database_tls_enabled: false,
  trade_ip: '', trade_tcp_port: null, trade_udp_port: null,
  query_ip: '', query_port: null,
  remote_path: '', capabilities: {}, version_info: '', notes: '', is_enabled: true,
})
const form = reactive<any>(empty())
const selectedDatabaseSummary = computed(() => form.database_names.length
  ? `已选择 ${form.database_names.length} 个数据库`
  : '尚未选择数据库')

async function load() {
  listLoading.value = true
  listError.value = ''
  try {
    rows.value = (await api.get('/resources')).data
  } catch (error) {
    listError.value = errorMessage(error)
  } finally {
    listLoading.value = false
  }
}

function setMarketDefaultPath(value: string) {
  const selected = marketEnvironments.find(item => item.value === value)
  if (selected) form.remote_path = selected.defaultPath
}

function setRemDefaultPath(value: string) {
  if (form.resource_type === 'rem' && remDefaultPaths[value]) form.remote_path = remDefaultPaths[value]
}

function handleBusinessChange(value: string) {
  setRemDefaultPath(value)
}

function setOrderToolDefaultPath(value: string) {
  const selected = orderTools.find(item => item.value === value)
  if (selected) form.remote_path = selected.path
}

function setSlnicDefaultPath(value: string) {
  const selected = slnicModels.find(item => item.value === value)
  if (selected) form.remote_path = selected.path
}

function setParserToolDefaults(value: string) {
  if (!parserTools.includes(value)) return
  form.remote_path = `/home/user0/${value}`
}

function handleResourceTypeChange(value: string) {
  databaseStep.value = 1
  databaseOptions.value = []
  discoveryError.value = ''
  connectionTestResult.value = null
  if (value === 'rem') {
    setRemDefaultPath(form.business_code)
  }
  else if (value === 'market' && form.market_environment) setMarketDefaultPath(form.market_environment)
  else if (value === 'order') {
    form.order_tool = form.order_tool || orderTools[0].value
    form.order_actions = form.order_actions?.length ? form.order_actions : [...orderActionOptions]
    setOrderToolDefaultPath(form.order_tool)
  }
  else if (value === 'slnic') {
    form.slnic_model = form.slnic_model || slnicModels[0].value
    setSlnicDefaultPath(form.slnic_model)
  }
  else if (value === 'parser') {
    form.parser_tool = form.parser_tool || parserTools[0]
    if (!Array.isArray(form.parser_actions)) form.parser_actions = [...parserActionOptions]
    setParserToolDefaults(form.parser_tool)
  }
}

function open(row?: any) {
  Object.assign(form, empty(), row || {})
  form.market_environment = row?.capabilities?.market_environment || ''
  form.order_tool = row?.capabilities?.order_tool || orderTools.find(item => item.path === row?.remote_path)?.value || ''
  form.order_actions = row?.capabilities?.order_actions?.length ? [...row.capabilities.order_actions] : [...orderActionOptions]
  form.slnic_model = row?.capabilities?.slnic_model || slnicModels.find(item => item.path === row?.remote_path)?.value || ''
  form.parser_tool = row?.capabilities?.parser_tool || parserTools.find(item => `/home/user0/${item}` === row?.remote_path) || ''
  form.parser_actions = parserActionsFromCapabilities(row?.capabilities)
  form.database_names = [...(row?.database_names || [])]
  if (!form.remote_path) {
    if (form.resource_type === 'market' && form.market_environment) setMarketDefaultPath(form.market_environment)
    else if (form.resource_type === 'order' && form.order_tool) setOrderToolDefaultPath(form.order_tool)
    else if (form.resource_type === 'slnic' && form.slnic_model) setSlnicDefaultPath(form.slnic_model)
    else if (form.resource_type === 'parser' && form.parser_tool) setParserToolDefaults(form.parser_tool)
    else setRemDefaultPath(form.business_code)
  }
  form.password = ''
  form.private_key = ''
  form.database_password = ''
  editing.value = row?.id || null
  databaseStep.value = 1
  databaseOptions.value = []
  discoveryError.value = ''
  connectionTestResult.value = null
  dialog.value = true
}

function validateSshConnection() {
  if (!form.host.trim()) return '请填写 Linux 地址'
  if (!form.username.trim()) return '请填写 SSH 用户名'
  if (form.resource_type === 'order' && !orderTools.some(item => item.value === form.order_tool)) return '请选择发单工具'
  if (form.resource_type === 'order' && !form.remote_path.trim()) return '请填写远端路径'
  return ''
}

async function testConnection() {
  const validationError = validateSshConnection()
  if (validationError) {
    ElMessage.warning(validationError)
    return
  }
  testingConnection.value = true
  connectionTestResult.value = null
  const capabilities = { ...(form.capabilities || {}) }
  if (form.resource_type === 'order') capabilities.order_tool = form.order_tool
  try {
    const { data } = await api.post('/resources/connection-test', {
      resource_id: editing.value,
      resource_type: form.resource_type,
      host: form.host,
      ssh_port: form.ssh_port,
      username: form.username,
      auth_type: form.auth_type,
      password: form.password,
      private_key: form.private_key,
      remote_path: form.remote_path,
      capabilities,
    })
    connectionTestResult.value = data
    data.ok ? ElMessage.success(data.message) : ElMessage.error(data.message)
  } catch (error) {
    const message = errorMessage(error)
    connectionTestResult.value = { ok: false, message }
    ElMessage.error(message)
  } finally {
    testingConnection.value = false
  }
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
  if (form.resource_type === 'rem' && ![
    form.trade_ip,
    form.trade_tcp_port,
    form.trade_udp_port,
    form.query_ip,
    form.query_port,
  ].every(value => value !== '' && value !== null && value !== undefined)) {
    ElMessage.warning('请补全 REM 更多配置')
    return
  }
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
  if (form.resource_type === 'order' && !form.order_actions?.length) {
    ElMessage.warning('请至少选择一个发单动作')
    return
  }
  if (form.resource_type === 'slnic' && !slnicModels.some(item => item.value === form.slnic_model)) {
    ElMessage.warning('请选择 SLNIC 型号')
    return
  }
  if (form.resource_type === 'parser' && !parserTools.includes(form.parser_tool)) {
    ElMessage.warning('请选择解析工具')
    return
  }
  loading.value = true
  try {
    const { market_environment, order_tool, order_actions, slnic_model, parser_tool, parser_actions, ...payload } = form
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
        order_actions,
      })
    } else {
      for (const key of ['order_tool', 'order_tool_name', 'order_tool_default_path', 'order_actions']) delete capabilities[key]
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
    if (form.resource_type === 'parser') {
      delete capabilities.parser_config_filename
      Object.assign(capabilities, {
        parser_tool,
        parser_binary: parser_tool,
        ...parserActionsPayload(parser_actions),
      })
    } else {
      for (const key of ['parser_tool', 'parser_binary', 'parser_config_filename', 'parser_actions']) delete capabilities[key]
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
    if (form.resource_type !== 'rem') {
      Object.assign(payload, {
        trade_ip: null,
        trade_tcp_port: null,
        trade_udp_port: null,
        query_ip: null,
        query_port: null,
      })
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

async function copyResource(row: any) {
  try {
    await api.post(`/resources/${row.id}/copy`)
    ElMessage.success('资源已复制')
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

watch(
  () => [
    form.resource_type, form.host, form.ssh_port, form.username, form.auth_type,
    form.password, form.private_key, form.remote_path, form.order_tool,
  ],
  () => { connectionTestResult.value = null },
)

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <span class="page-kicker">基础设施</span>
        <h1 class="page-title">资源管理</h1>
        <p class="muted">集中管理测试服务器、数据库、发单、抓包与解析资源</p>
      </div>
      <el-button v-if="auth.isAdmin" type="primary" @click="open()">新增资源</el-button>
    </div>

    <el-alert v-if="listError" type="error" :closable="false" show-icon class="load-alert">
      <template #title><span>资源数据加载失败：{{ listError }}</span><el-button link type="danger" @click="load">重试</el-button></template>
    </el-alert>

    <div class="filter-bar resource-filters">
      <el-input v-model="filters.keyword" clearable :prefix-icon="Search" placeholder="搜索名称或连接地址" class="keyword-filter" />
      <el-select v-model="filters.type" clearable placeholder="全部类型"><el-option v-for="(label,value) in resourceText" :key="value" :label="label" :value="value" /></el-select>
      <el-select v-model="filters.business" clearable placeholder="全部业务"><el-option v-for="(label,value) in businessText" :key="value" :label="label" :value="value" /></el-select>
      <el-select v-model="filters.health" clearable placeholder="全部健康状态"><el-option label="健康" value="healthy" /><el-option label="异常" value="unhealthy" /><el-option label="未知" value="unknown" /></el-select>
      <span class="filter-count">{{ filteredRows.length }} / {{ rows.length }} 条</span>
      <el-button text @click="Object.assign(filters,{keyword:'',type:'',business:'',health:''})">重置</el-button>
    </div>

    <div class="card resource-table">
      <el-table v-loading="listLoading" :data="filteredRows" empty-text="没有符合条件的资源">
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
              {{ scope.row.health_status === 'healthy' ? '健康' : scope.row.health_status === 'unhealthy' ? '异常' : '未知' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="90">
          <template #default="scope"><el-tag :type="scope.row.is_enabled ? 'success' : 'info'" effect="plain">{{ scope.row.is_enabled ? '已启用' : '已停用' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="380" fixed="right">
          <template #default="scope">
            <el-button v-if="auth.canOperate" link type="primary" @click="health(scope.row)">连通测试</el-button>
            <el-button link type="primary" @click="copyResource(scope.row)">复制</el-button>
            <el-button v-if="scope.row.resource_type === 'database' && auth.canOperate" link type="primary" @click="router.push(`/resources/${scope.row.id}/database`)">操作台</el-button>
            <el-button v-if="['rem', 'market', 'order', 'slnic', 'parser'].includes(scope.row.resource_type) && auth.canOperate" link type="primary" :disabled="!scope.row.is_enabled" @click="router.push(`/resources/${scope.row.id}/terminal`)">操作台</el-button>
            <template v-if="auth.isAdmin">
              <el-button link @click="open(scope.row)">编辑</el-button>
              <el-button link type="danger" @click="remove(scope.row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-drawer v-model="dialog" :title="editing ? '编辑资源' : '新增资源'" size="720px" destroy-on-close class="resource-config-drawer">
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
                <el-option v-for="item in marketEnvironments" :key="item.value" :label="item.label" :value="item.value" />
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
          <el-col v-if="form.resource_type === 'order'" :span="24">
            <el-form-item label="支持动作" required>
              <el-checkbox-group v-model="form.order_actions">
                <el-checkbox v-for="action in orderActionOptions" :key="action" :label="action">{{ action }}</el-checkbox>
              </el-checkbox-group>
            </el-form-item>
          </el-col>
          <el-col v-if="form.resource_type === 'slnic'" :span="24">
            <el-form-item label="SLNIC 型号" required>
              <el-select v-model="form.slnic_model" placeholder="请选择 SLNIC 型号" style="width:100%" @change="setSlnicDefaultPath">
                <el-option v-for="item in slnicModels" :key="item.value" :label="item.value" :value="item.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col v-if="form.resource_type === 'parser'" :span="24">
            <el-form-item label="解析工具" required>
              <el-select v-model="form.parser_tool" placeholder="请选择解析工具" style="width:100%" @change="setParserToolDefaults">
                <el-option v-for="item in parserTools" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col v-if="form.resource_type === 'parser'" :span="24">
            <el-form-item label="快捷指令">
              <el-checkbox-group v-model="form.parser_actions" class="parser-action-options">
                <el-checkbox v-for="action in parserActionOptions" :key="action" :value="action">{{ action }}</el-checkbox>
              </el-checkbox-group>
              <div class="muted">可不选择快捷指令，运行时仍可直接在 SSH 终端输入。</div>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="所属业务" required>
              <el-select v-model="form.business_code" style="width:100%" @change="handleBusinessChange">
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
            <el-col :span="8"><el-form-item label="端口" label-width="90px" required><el-input-number v-model="form.database_port" class="port-input" :min="1" :max="65535" style="width:100%" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="数据库用户" required><el-input v-model="form.database_username" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="数据库密码"><el-input v-model="form.database_password" type="password" show-password :placeholder="editing && form.has_database_password ? '留空保持原密码' : ''" /></el-form-item></el-col>
          </template>

          <template v-if="form.resource_type !== 'database' || form.database_connection_mode === 'ssh_tunnel'">
            <el-col v-if="form.resource_type === 'database'" :span="24"><el-divider content-position="left">SSH 跳板机</el-divider></el-col>
            <el-col :span="16"><el-form-item :label="form.resource_type === 'database' ? '跳板机地址' : 'Linux 地址'" required><el-input v-model="form.host" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="SSH 端口" label-width="90px" required><el-input-number v-model="form.ssh_port" class="port-input" :min="1" :max="65535" style="width:100%" /></el-form-item></el-col>
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

          <template v-if="form.resource_type === 'rem'">
            <el-col :span="24">
              <div class="more-config-heading">
                <div><strong>更多配置</strong><span>REM 交易、查询与运行参数</span></div>
              </div>
            </el-col>
            <el-col :span="24">
              <div class="more-config-grid">
                <el-form-item class="trade-ip-field" label="交易 IP" label-width="128px" required><el-input v-model="form.trade_ip" class="mono" /></el-form-item>
                <el-form-item class="trade-tcp-field" label="交易 TCP 端口" label-width="128px" required><el-input-number v-model="form.trade_tcp_port" class="port-input" :min="1" :max="65535" style="width:100%" /></el-form-item>
                <el-form-item class="trade-udp-field" label="交易 UDP 端口" label-width="128px" required><el-input-number v-model="form.trade_udp_port" class="port-input" :min="1" :max="65535" style="width:100%" /></el-form-item>
                <el-form-item class="query-ip-field" label="查询 IP" label-width="128px" required><el-input v-model="form.query_ip" class="mono" /></el-form-item>
                <el-form-item class="query-port-field" label="查询端口" label-width="128px" required><el-input-number v-model="form.query_port" class="port-input" :min="1" :max="65535" style="width:100%" /></el-form-item>
              </div>
            </el-col>
            <el-col :span="24"><el-form-item label="远端路径"><el-input v-model="form.remote_path" /></el-form-item></el-col>
            <el-col :span="24"><el-form-item label="备注"><el-input v-model="form.notes" type="textarea" /></el-form-item></el-col>
          </template>

          <el-col v-if="!['database', 'rem'].includes(form.resource_type)" :span="24"><el-form-item label="远端路径"><el-input v-model="form.remote_path" /></el-form-item></el-col>
          <el-col v-if="form.resource_type !== 'rem'" :span="24"><el-form-item label="备注"><el-input v-model="form.notes" type="textarea" /></el-form-item></el-col>
        </el-row>

        <el-alert
          v-if="form.resource_type !== 'database' && connectionTestResult"
          :type="connectionTestResult.ok ? 'success' : 'error'"
          :title="connectionTestResult.message"
          :closable="false"
          show-icon
          class="connection-test-result"
        />

        <section v-if="form.resource_type === 'database' && databaseStep === 2" class="database-selection-step">
          <div class="database-connection-summary">
            <div><small>连接方式</small><strong>{{ form.database_connection_mode === 'ssh_tunnel' ? 'SSH 隧道' : '直接连接' }}</strong></div>
            <div><small>MySQL 地址</small><strong class="mono">{{ form.database_username }}@{{ form.database_host }}:{{ form.database_port }}</strong></div>
            <div v-if="form.database_connection_mode === 'ssh_tunnel'"><small>跳板机</small><strong class="mono">{{ form.username }}@{{ form.host }}:{{ form.ssh_port }}</strong></div>
            <el-tag type="success" effect="plain">连接成功</el-tag>
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
          <el-button v-if="databaseStep === 1" type="primary" :loading="discoveringDatabases" @click="discoverDatabases">测试连接并下一步</el-button>
          <el-button v-else type="primary" :loading="loading" :disabled="!form.database_names.length" @click="save">保存</el-button>
        </template>
        <template v-else>
          <el-button :loading="testingConnection" :disabled="loading" @click="testConnection">连通测试</el-button>
          <el-button type="primary" :loading="loading" :disabled="testingConnection" @click="save">保存</el-button>
        </template>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.resource-filters > .el-select {
  width: 160px;
}

.keyword-filter {
  width: 300px;
}

.filter-count {
  margin-left: auto;
  color: var(--ui-text-secondary);
  font-size: 11px;
}

.resource-table {
  overflow: hidden;
}

.resource-table :deep(.mono) {
  font-size: 11px;
}

:global(.resource-config-drawer) {
  width: min(720px, 100vw) !important;
}

:global(.resource-config-drawer .el-drawer__header) {
  height: 58px;
  margin: 0;
  padding: 0 20px;
  border-bottom: 1px solid var(--ui-border);
}

:global(.resource-config-drawer .el-drawer__title) {
  color: var(--ui-text-primary);
  font-size: 16px;
  font-weight: 700;
  line-height: 58px;
}

:global(.resource-config-drawer .el-drawer__close-btn) {
  width: 32px;
  height: 32px;
  margin: 13px 0;
  color: var(--ui-text-primary);
  font-size: 18px;
}

:global(.resource-config-drawer .el-drawer__body) {
  padding: 20px 20px 24px;
}

:global(.resource-config-drawer .el-drawer__footer) {
  padding: 14px 20px 18px;
  border-top: 1px solid var(--ui-border);
  background: var(--ui-surface);
}

:global(.resource-config-drawer .el-form-item) {
  display: flex;
  align-items: center;
  margin-bottom: 18px;
}

:global(.resource-config-drawer .el-form-item__label) {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: flex-end;
  height: 32px;
  margin: 0;
  padding: 0 12px 0 0;
  color: var(--el-text-color-regular);
  font-size: 14px;
  font-weight: 400;
  line-height: 32px;
}

:global(.resource-config-drawer .el-form-item__content) {
  display: flex;
  flex: 1;
  min-width: 0;
  align-items: center;
  line-height: 32px;
}

:global(.resource-config-drawer .el-input__wrapper),
:global(.resource-config-drawer .el-select__wrapper) {
  min-height: 32px;
  border-radius: 5px;
}

:global(.resource-config-drawer .el-input__inner),
:global(.resource-config-drawer .el-select__placeholder),
:global(.resource-config-drawer .el-select__selected-item) {
  height: 30px;
  font-size: 14px;
  line-height: 30px;
}

:global(.resource-config-drawer .el-textarea__inner) {
  min-height: 52px;
  border-radius: 5px;
  font-size: 14px;
  line-height: 1.45;
}

:global(.resource-config-drawer .el-radio-group),
:global(.resource-config-drawer .el-radio),
:global(.resource-config-drawer .el-switch) {
  height: 32px;
}

:global(.resource-config-drawer .el-radio-button__inner) {
  height: 32px;
  padding: 7px 14px;
  font-size: 14px;
  line-height: 16px;
}

:global(.resource-config-drawer .el-radio) {
  margin-right: 12px;
}

:global(.resource-config-drawer .el-radio__label) {
  padding-left: 6px;
  font-size: 14px;
}

:global(.resource-config-drawer .el-button) {
  min-height: 36px;
}

:global(.resource-config-drawer .el-button + .el-button) {
  margin-left: 8px;
}

.port-input {
  height: 32px;
}

.port-input :deep(.el-input__wrapper) {
  padding-right: 32px;
  padding-left: 32px;
}

.port-input :deep(.el-input-number__decrease),
.port-input :deep(.el-input-number__increase) {
  width: 30px;
  height: 30px;
}

.connection-test-result {
  margin-top: 4px;
}

.more-config-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 2px 0 18px;
  padding: 10px 0 9px;
  border-bottom: 1px solid var(--ui-border);
}

.more-config-heading strong,
.more-config-heading span {
  display: block;
}

.more-config-heading strong {
  font-size: 13px;
}

.more-config-heading span {
  margin-top: 3px;
  color: var(--ui-text-tertiary);
  font-size: 11px;
}

.more-config-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 16px;
}

.more-config-grid .trade-ip-field {
  grid-column: 1;
  grid-row: 1;
}

.more-config-grid .trade-tcp-field {
  grid-column: 2;
  grid-row: 1;
}

.more-config-grid .trade-udp-field {
  grid-column: 2;
  grid-row: 2;
}

.more-config-grid .query-ip-field {
  grid-column: 1;
  grid-row: 3;
}

.more-config-grid .query-port-field {
  grid-column: 2;
  grid-row: 3;
}

.database-steps {
  margin: -4px 0 22px;
  padding: 0 40px;
}

.database-selection-step {
  min-height: 330px;
  padding: 6px 0 0;
}

.database-connection-summary {
  display: flex;
  align-items: center;
  gap: 22px;
  margin-bottom: 22px;
  padding: 14px 16px;
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  background: var(--ui-surface-subtle);
}

.database-connection-summary > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.database-connection-summary small {
  color: var(--ui-text-tertiary);
  font-size: 11px;
}

.database-connection-summary strong {
  overflow: hidden;
  max-width: 250px;
  color: var(--ui-text-primary);
  font-size: 12px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.database-connection-summary .el-tag {
  margin-left: auto;
}

.database-picker {
  margin: 0;
}

.database-picker :deep(.el-select__wrapper) {
  min-height: 42px;
}

.database-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 12px;
}

.database-selection-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 8px 0 0 110px;
  color: var(--ui-text-secondary);
  font-size: 12px;
}

.database-discovery-error {
  margin: 0 0 14px 110px;
  padding: 9px 12px;
  border-radius: 5px;
  background: #fff1f2;
  color: var(--ui-danger);
  font-size: 12px;
}

@media (max-width: 767px) {
  .resource-filters > * {
    width: 100% !important;
  }

  .filter-count {
    margin-left: 0;
  }
}

@media (max-width: 599px) {
  :global(.resource-config-drawer) {
    width: 100vw !important;
  }

  :global(.resource-config-drawer .el-drawer__body) {
    padding: 16px;
  }

  :global(.resource-config-drawer .el-form-item) {
    display: block;
  }

  :global(.resource-config-drawer .el-form-item__label) {
    justify-content: flex-start;
    width: auto !important;
  }

  .database-steps {
    padding: 0;
  }

  .database-selection-meta,
  .database-discovery-error {
    margin-left: 0;
  }

  .more-config-heading {
    align-items: flex-start;
  }

  .more-config-grid {
    display: block;
  }
}
</style>
