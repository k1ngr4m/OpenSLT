import { computed, type ComputedRef, type Ref } from 'vue'
import type { ContractFilePreview, InfoRow, JsonMap, RunDetail, RunStep } from '@/types/run'
import { formatBytes, formatDate, formatDuration, formatValue, isJsonMap, nodeTypeText, normalizeContractFile, optionalNumber, optionalString, stringValue } from '@/utils/runDetail'
import { statusText } from '@/utils/status'

export function useRunStepPresentation(
  run: Ref<RunDetail | null>,
  selectedStep: ComputedRef<RunStep | null>,
  contractPreviewCache: Record<number, ContractFilePreview>,
) {
  const selectedConfig = computed(() => selectedStep.value?.config_snapshot || {})
  const selectedResult = computed(() => selectedStep.value?.result_summary || {})
  const selectedArtifacts = computed(() => {
    if (!run.value || !selectedStep.value) return []
    return run.value.artifacts.filter(item => item.step_id === selectedStep.value?.id)
  })

  function resourceDisplayName(resourceId: number) {
    const resources = run.value?.config_snapshot?.resources
    if (!Array.isArray(resources)) return `资源 ${resourceId}`
    return resources.find(resource => resource.id === resourceId)?.name || `资源 ${resourceId}`
  }

  const summaryRows = computed<InfoRow[]>(() => {
    if (!selectedStep.value) return []
    return [
      { label: '节点类型', value: nodeTypeText[selectedStep.value.node_type] || selectedStep.value.node_type },
      { label: '节点状态', value: statusText[selectedStep.value.status] || selectedStep.value.status },
      { label: '进度', value: `${selectedStep.value.progress}%` },
      { label: '执行耗时', value: formatDuration(selectedStep.value.duration_ms) },
      { label: '重试次数', value: `${selectedStep.value.retry_count}/${selectedStep.value.max_retries}` },
      { label: '开始时间', value: formatDate(selectedStep.value.started_at) },
      { label: '结束时间', value: formatDate(selectedStep.value.finished_at) },
    ]
  })

  const orderReadSymbolCsvEnabled = computed(() => {
    if (selectedStep.value?.node_type !== 'order_preparation') return false
    const configValue = selectedConfig.value.read_symbol_csv
    if (configValue != null) return configValue === true || configValue === 1 || configValue === '1'
    const resultValue = selectedResult.value.read_symbol_csv
    return resultValue === true || resultValue === 1 || resultValue === '1'
  })

  const configRows = computed<InfoRow[]>(() => {
    const step = selectedStep.value
    const config = selectedConfig.value
    if (!step) return []
    if (step.node_type === 'server_config') {
      const targets = Array.isArray(config.targets) ? config.targets.filter(isJsonMap) : []
      return [
        { label: '采集目标', value: targets.length ? `${targets.length} 个` : '-' },
        { label: '资源类型', value: targets.map(item => stringValue(item.resource_type, '')).filter(Boolean).join('、') || '-' },
        { label: '采集字段', value: targets.flatMap(item => Array.isArray(item.fields) ? item.fields.map(String) : []).join('、') || '-' },
      ]
    }
    if (step.node_type === 'database_config') {
      return [
        { label: '数据库', value: stringValue(config.database_name) },
        { label: '配置键数量', value: Array.isArray(config.keys) ? `${config.keys.length} 个` : '-' },
        { label: '配置键', value: Array.isArray(config.keys) ? config.keys.join('、') : '-' },
      ]
    }
    if (step.node_type === 'wiring_confirmation') {
      const snapshot = isJsonMap(config.wiring_snapshot) ? config.wiring_snapshot : null
      const rem = snapshot && isJsonMap(snapshot.rem) ? snapshot.rem : null
      const market = snapshot && isJsonMap(snapshot.market) ? snapshot.market : null
      const slnic = snapshot && isJsonMap(snapshot.slnic) ? snapshot.slnic : null
      const clientInterface = snapshot && isJsonMap(snapshot.client_interface) ? snapshot.client_interface : null
      const marketInterface = snapshot && isJsonMap(snapshot.market_interface) ? snapshot.market_interface : null
      if (snapshot) {
        return [
          { label: '拓扑类型', value: stringValue(snapshot.model_label) },
          { label: 'REM 柜台', value: `${stringValue(rem?.name)} (${stringValue(rem?.host)})` },
          { label: '客户端接口', value: `${stringValue(clientInterface?.name)} / ${stringValue(clientInterface?.ip_address)}`, mono: true },
          { label: '市场端接口', value: `${stringValue(marketInterface?.name)} / ${stringValue(marketInterface?.ip_address)}`, mono: true },
          ...(market ? [{ label: '模拟市场', value: `${stringValue(market.name)} (${stringValue(market.host)})` }] : []),
          { label: 'SLNIC 节点', value: `${stringValue(slnic?.name)} (${stringValue(slnic?.host)})` },
          { label: '确认要求', value: '查看动态接线图后人工确认' },
        ]
      }
      return [
        { label: '接线图', value: stringValue(config.diagram, stringValue(selectedResult.value.diagram, 'placeholder')) },
        { label: '确认要求', value: stringValue(config.instructions, '等待现场确认链路连接') },
      ]
    }
    if (step.node_type === 'order_preparation') {
      const rows: InfoRow[] = [
        { label: '发单动作', value: stringValue(config.order_action, 'new_order'), mono: true },
        { label: 'XML 文件', value: stringValue(config.xml_filename) },
        { label: '读取合约 CSV', value: orderReadSymbolCsvEnabled.value ? '是' : '否' },
        { label: '交易库', value: stringValue(config.trading_database_name) },
        { label: '网卡接口', value: stringValue(config.network_interface) },
      ]
      if (orderReadSymbolCsvEnabled.value) {
        rows.push({ label: '合约文件', value: Array.isArray(config.contract_file_ids) ? `${config.contract_file_ids.length} 个` : '-' })
      }
      return rows
    }
    if (step.node_type === 'rem_startup') {
      return [
        { label: 'REM 动作', value: '停止服务 → 清理数据流 → 启动服务' },
        { label: '固定脚本', value: './stop_rem.sh → ./makeneat.sh → ./start_rem_all.sh', mono: true },
      ]
    }
    if (step.node_type === 'market_startup') {
      const scripts = Array.isArray(config.scripts) ? config.scripts.filter(isJsonMap) : []
      return [
        { label: '启动脚本数量', value: `${scripts.length} 个` },
        { label: '执行顺序', value: scripts.map(item => stringValue(item.filename, '')).filter(Boolean).join(' → ') || '-', mono: true },
      ]
    }
    if (step.node_type === 'parser_parse') {
      return [
        { label: '数据库', value: stringValue(config.database_name) },
        { label: 'config.xml', value: stringValue(config.config_xml_filename, 'config.xml') },
        { label: 'instance.xml', value: stringValue(config.instance_xml_filename, 'instance.xml') },
        { label: '分析主配置', value: stringValue(config.analysis_xml_filename) },
      ]
    }
    if (step.node_type === 'data_statistics') {
      return [
        { label: '前置解析节点', value: stringValue(config.parser_node_key), mono: true },
        { label: '统计脚本', value: stringValue(config.script_filename), mono: true },
        { label: '异常大值上限', value: `${optionalNumber(config.max_latency_ns) ?? 999999999} ns` },
      ]
    }
    if (step.node_type.startsWith('slnic_')) {
      return [
        { label: 'SLNIC 动作', value: nodeTypeText[step.node_type] || step.node_type },
        { label: '节点配置', value: Object.keys(config).length ? '见原始配置' : '-' },
      ]
    }
    return objectRows(config)
  })

  const resultRows = computed<InfoRow[]>(() => {
    const step = selectedStep.value
    const result = selectedResult.value
    if (!step || !Object.keys(result).length) return []
    if (step.node_type === 'server_config' || step.node_type === 'database_config') {
      return [
        { label: '采集来源', value: result.sources != null ? `${result.sources} 个` : '-' },
        { label: '失败数量', value: result.failed != null ? `${result.failed} 个` : '-' },
        { label: '快照 ID', value: Array.isArray(result.snapshot_ids) ? result.snapshot_ids.join('、') : '-' },
      ]
    }
    if (step.node_type === 'wiring_confirmation') {
      return [
        { label: '已确认', value: result.confirmed ? '是' : '否' },
        { label: '确认人 ID', value: optionalNumber(result.confirmed_by) ?? '-' },
        { label: '确认时间', value: formatDate(optionalString(result.confirmed_at)) },
      ]
    }
    if (step.node_type === 'order_preparation') {
      return [
        { label: '准备状态', value: result.prepared ? '已完成' : '-' },
        { label: 'XML 文件', value: stringValue(result.xml_filename) },
        { label: '读取合约 CSV', value: result.read_symbol_csv ? '是' : '否' },
        { label: '网卡接口', value: stringValue(result.network_interface) },
        { label: '执行模式', value: result.mode === 'terminal' ? 'SSH 终端' : '后端准备' },
        { label: '资源', value: stringValue(result.resource_name, result.resource_id ? resourceDisplayName(Number(result.resource_id)) : '-') },
        { label: '发单命令', value: stringValue(result.command, stringValue(result.generated_command)), mono: true },
        { label: '下发时间', value: formatDate(optionalString(result.dispatched_at)) },
        { label: '进程状态', value: result.process_started ? '已启动' : '未启动' },
        { label: 'tmux 会话', value: stringValue(result.tmux_session), mono: true },
        { label: '会话状态', value: stringValue(result.session_status) },
        { label: '动作状态', value: stringValue(result.order_action_status) },
        { label: '发单动作', value: stringValue(result.order_action, stringValue(selectedConfig.value.order_action, 'new_order')), mono: true },
      ]
    }
    if (step.node_type === 'rem_startup') {
      const commands = Array.isArray(result.commands) ? result.commands.filter(isJsonMap) : []
      return [
        { label: '资源', value: stringValue(result.resource_name, result.resource_id ? resourceDisplayName(Number(result.resource_id)) : '-') },
        { label: '远端工作目录', value: stringValue(result.remote_workdir), mono: true },
        { label: '完成命令', value: `${commands.filter(command => command.exit_code === 0).length}/${commands.length || 3}` },
        { label: '退出码', value: optionalNumber(result.exit_code) ?? '-' },
        { label: '执行耗时', value: formatDuration(optionalNumber(result.duration_ms)) },
      ]
    }
    if (step.node_type === 'market_startup') {
      const commands = Array.isArray(result.commands) ? result.commands.filter(isJsonMap) : []
      const expected = Array.isArray(selectedConfig.value.scripts) ? selectedConfig.value.scripts.length : commands.length
      return [
        { label: '资源', value: stringValue(result.resource_name, result.resource_id ? resourceDisplayName(Number(result.resource_id)) : '-') },
        { label: '远端工作目录', value: stringValue(result.remote_workdir), mono: true },
        { label: '完成脚本', value: `${commands.filter(command => command.exit_code === 0).length}/${expected}` },
        { label: '执行顺序', value: commands.map(command => stringValue(command.script, '')).filter(Boolean).join(' → ') || '-', mono: true },
        { label: '退出码', value: optionalNumber(result.exit_code) ?? '-' },
        { label: '执行耗时', value: formatDuration(optionalNumber(result.duration_ms)) },
      ]
    }
    if (step.node_type.startsWith('slnic_')) {
      return [
        { label: '资源', value: stringValue(result.resource_name, result.resource_id ? resourceDisplayName(Number(result.resource_id)) : '-') },
        { label: '执行模式', value: result.mode === 'terminal' ? 'SSH 终端' : '后端自动执行' },
        { label: 'SLNIC 指令', value: stringValue(result.command), mono: true },
        { label: '退出码', value: optionalNumber(result.exit_code) ?? '-' },
        { label: '下发时间', value: formatDate(optionalString(result.dispatched_at)) },
        { label: '产物文件', value: stringValue(result.filename) },
        { label: '文件大小', value: optionalNumber(result.size) != null ? formatBytes(optionalNumber(result.size)!) : '-' },
        { label: 'SHA-256', value: stringValue(result.checksum), mono: true },
      ]
    }
    if (step.node_type === 'parser_parse') {
      return [
        { label: '数据库', value: stringValue(result.database_name) },
        { label: '远端工作目录', value: stringValue(result.remote_workdir), mono: true },
        { label: '退出码', value: optionalNumber(result.exit_code) ?? '-' },
        { label: '执行耗时', value: formatDuration(optionalNumber(result.duration_ms)) },
        { label: 'PCAP 产物 ID', value: optionalNumber(result.pcap_artifact_id) ?? '-' },
        { label: '输出文件', value: Array.isArray(result.output_files) ? `${result.output_files.length} 个` : '-' },
      ]
    }
    if (step.node_type === 'data_statistics') {
      const selection = isJsonMap(result.statistics_selection) ? result.statistics_selection : null
      const script = isJsonMap(result.statistics_script) ? result.statistics_script : null
      return [
        { label: '统计脚本', value: stringValue(script?.filename, stringValue(selectedConfig.value.script_filename)), mono: true },
        { label: '脚本校验', value: stringValue(script?.checksum, stringValue(selectedConfig.value.script_checksum)), mono: true },
        { label: '输入文件', value: Array.isArray(selection?.inputs) ? `${selection.inputs.length} 个` : '-' },
        { label: '远端工作目录', value: stringValue(result.remote_workdir), mono: true },
        { label: '执行耗时', value: formatDuration(optionalNumber(result.duration_ms)) },
        { label: '结果产物 ID', value: optionalNumber(result.statistics_artifact_id) ?? '-' },
      ]
    }
    return objectRows(result)
  })

  const selectedContractFileIds = computed(() => {
    const ids: number[] = []
    const add = (value: unknown) => {
      const id = Number(value)
      if (Number.isFinite(id) && !ids.includes(id)) ids.push(id)
    }
    ;(Array.isArray(selectedConfig.value.contract_file_ids) ? selectedConfig.value.contract_file_ids : []).forEach(add)
    ;(Array.isArray(selectedConfig.value.contract_files) ? selectedConfig.value.contract_files.filter(isJsonMap) : []).forEach(file => add(file.id))
    ;(Array.isArray(selectedResult.value.contract_files) ? selectedResult.value.contract_files.filter(isJsonMap) : []).forEach(file => add(file.id))
    return ids
  })

  const contractFiles = computed<ContractFilePreview[]>(() => {
    if (!orderReadSymbolCsvEnabled.value) return []
    const files = new Map<number, ContractFilePreview>()
    const merge = (source: unknown) => {
      const file = normalizeContractFile(source)
      if (!file) return
      const existing = files.get(file.id)
      const sourceMap = isJsonMap(source) ? source : {}
      const meaningful = Object.fromEntries(
        Object.entries(file).filter(([key, value]) =>
          value !== undefined && value !== '' && !(key === 'filename' && existing && !sourceMap.filename),
        ),
      )
      files.set(file.id, { ...(existing || {}), ...meaningful } as ContractFilePreview)
    }
    ;(Array.isArray(selectedConfig.value.contract_files) ? selectedConfig.value.contract_files : []).forEach(merge)
    ;(Array.isArray(selectedResult.value.contract_files) ? selectedResult.value.contract_files : []).forEach(merge)
    Object.values(contractPreviewCache).forEach(merge)
    const ids = selectedContractFileIds.value
    if (ids.length) return ids.map(id => files.get(id)).filter((file): file is ContractFilePreview => Boolean(file))
    return Array.from(files.values())
  })

  const parserOutputFiles = computed(() => Array.isArray(selectedResult.value.output_files) ? selectedResult.value.output_files : [])
  const parserTableRows = computed(() => Object.entries(selectedResult.value.table_rows || {}).map(([name, count]) => ({ name, count })))
  const inputChecksums = computed(() => Object.entries(selectedResult.value.input_checksums || {}).map(([name, checksum]) => ({ name, checksum })))
  const showRawConfig = computed(() => selectedStep.value ? Object.keys(selectedConfig.value).length > 0 : false)
  const showRawResult = computed(() => selectedStep.value ? Object.keys(selectedResult.value).length > 0 : false)
  const showCaptureDetails = computed(() => ['server_config', 'database_config'].includes(selectedStep.value?.node_type || ''))

  return {
    configRows,
    contractFiles,
    inputChecksums,
    orderReadSymbolCsvEnabled,
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
  }
}

function objectRows(value: JsonMap): InfoRow[] {
  return Object.entries(value).map(([label, item]) => ({ label, value: formatValue(item), mono: typeof item !== 'string' }))
}
