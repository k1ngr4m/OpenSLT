import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

let currentHarness: ReturnType<typeof createHarness>

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: '9' } }) }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ canOperate: true }) }))
vi.mock('@/composables/useRunLifecycle', () => ({ useRunLifecycle: () => currentHarness.lifecycle }))
vi.mock('@/composables/useRunActions', () => ({ useRunActions: () => currentHarness.runActions }))
vi.mock('@/composables/useRunStepPresentation', () => ({ useRunStepPresentation: () => currentHarness.presentation }))
vi.mock('@/composables/useStatisticsInputs', () => ({ useStatisticsInputs: () => currentHarness.statistics }))
vi.mock('@/composables/useWiringInterfaceNames', () => ({ useWiringInterfaceNames: () => currentHarness.wiring }))
vi.mock('@/composables/useWorkflowTerminal', () => ({ useWorkflowTerminal: () => currentHarness.terminal }))
vi.mock('@/composables/useOrderRuntimeConfig', () => ({ useOrderRuntimeConfig: () => currentHarness.orderConfig }))
vi.mock('@/composables/useOrderActions', () => ({ useOrderActions: () => currentHarness.orderActions }))
vi.mock('@/composables/useParserExports', () => ({ useParserExports: () => currentHarness.parserExports }))
vi.mock('@/components/SshTerminalPanel.vue', () => ({ default: { template: '<div />' } }))

import RunDetailView from './RunDetailView.vue'

const source = readFileSync(resolve(process.cwd(), 'src/views/RunDetailView.vue'), 'utf8')

describe('RunDetailView node details', () => {
  it('shows structured configuration and results without raw JSON sections', () => {
    expect(source).toContain('<h3>节点配置</h3>')
    expect(source).toContain('<h3>执行结果</h3>')
    expect(source).not.toContain('原始配置')
    expect(source).not.toContain('原始结果')
    expect(source).not.toContain('class="json-fold"')
  })

  it('separates metric labels from their source files', () => {
    expect(source).toContain(':data="metricRows"')
    expect(source).toContain('label="数据来源"')
    expect(source).toContain('scope.row.sourceFile')
    expect(source).toContain(':content="scope.row.sourcePath"')
  })

  it('does not show the run configuration snapshot summary', () => {
    expect(source).not.toContain('<h3>运行配置快照</h3>')
    expect(source).not.toContain('class="detail-section compact-snapshot"')
  })

  it('keeps the order SSH terminal interactive', () => {
    const orderTerminal = source.match(/<SshTerminalPanel[\s\S]*?ref="orderWorkflowTerminalPanel"[\s\S]*?\/>/)
    expect(orderTerminal?.[0]).toBeTruthy()
    expect(orderTerminal?.[0]).not.toContain('read-only')
  })

  it('uses one saved analysis configuration for CSV inputs and the latency limit', () => {
    expect(source).toContain('<h3>分析配置</h3>')
    expect(source).toContain('v-model="statisticsMaxLatencyNsDraft"')
    expect(source).toContain('最大延迟上限（ns）')
    expect(source).toContain('@click="saveStatisticsConfig"')
    expect(source).not.toContain('保存输入选择')
  })

  it('guides statistics operators from start through reanalysis before completion', () => {
    expect(source).toContain("currentStep.node_type === 'data_statistics' ? '开始分析' : '开始'")
    expect(source).toContain('@click="currentStep && reanalyzeStatistics(currentStep)"')
    expect(source).toContain("statisticsCompletionStale ? '开始分析' : '再次分析'")
    expect(source).toContain('statisticsCompletionBlockedReason')
    expect(source).toContain('role="status"')
  })

  it('loads and exposes statistics analysis history on demand', () => {
    expect(source).toContain('refreshStatisticsAnalyses')
    expect(source).toContain('loadStatisticsAnalysisDetail')
    expect(source).toContain('expandedStatisticsAnalysisNo')
    expect(source).toContain('id="statistics-history-heading">分析历史</h3>')
    expect(source).toContain('@change="handleStatisticsHistoryChange"')
    expect(source).toContain('analysis.analysis_no')
  })
})

function analysis(analysisNo: number, status: 'running' | 'succeeded' | 'failed' = 'succeeded') {
  return {
    analysis_no: analysisNo,
    status,
    config_revision: analysisNo,
    inputs: [{ relative_path: `latency-${analysisNo}.csv`, filename: `latency-${analysisNo}.csv` }],
    max_latency_ns: 2000,
    script: { filename: 'statistics.py' },
    reserved_at: `2026-08-10T10:0${analysisNo}:00+08:00`,
    started_at: `2026-08-10T10:0${analysisNo}:01+08:00`,
    finished_at: status === 'running' ? null : `2026-08-10T10:0${analysisNo}:02+08:00`,
    duration_ms: status === 'running' ? null : 1000,
    error_code: status === 'failed' ? 'STATISTICS_SCRIPT_FAILED' : null,
    artifact_id: status === 'running' ? null : 200 + analysisNo,
    artifact_checksum: status === 'running' ? null : 'b'.repeat(64),
    artifact_size: status === 'running' ? null : 1024,
  }
}

interface HarnessOptions {
  analyses?: ReturnType<typeof analysis>[]
  analysisDetails?: Record<number, unknown>
  canEditStatisticsConfig?: boolean
  configDirty?: boolean
  configReady?: boolean
  configSaved?: boolean
  completionBlocked?: boolean
  completionStale?: boolean
  historyStructure?: boolean
  reanalyzing?: boolean
  statisticsResults?: Record<string, unknown>[]
}

function createHarness(options: HarnessOptions = {}) {
  const analyses = options.analyses ?? [analysis(1)]
  const resultSummary: Record<string, unknown> = {
    statistics_config_revision: 1,
    statistics_latest_success_revision: 1,
    statistics_latest_success_analysis_no: analyses.find(item => item.status === 'succeeded')?.analysis_no ?? null,
  }
  if (options.historyStructure !== false) resultSummary.statistics_analyses = analyses
  const step = {
    id: 32,
    code: 'statistics',
    name: '数据统计',
    workflow_node_id: 32,
    node_type: 'data_statistics',
    config_snapshot: { max_latency_ns: 2000, script_filename: 'statistics.py' },
    result_summary: resultSummary,
    position: 8,
    status: 'waiting',
    progress: 100,
    retry_count: 0,
    max_retries: 2,
    started_at: '2026-08-10T10:00:00+08:00',
    finished_at: null,
    duration_ms: 1000,
    error_message: null,
  }
  const run = ref({
    id: 9,
    run_number: 'RUN-20260810-001',
    business_code: 'fut_mm',
    status: 'awaiting_step_completion',
    progress: 80,
    trace_id: 'trace-9',
    logs_complete: true,
    config_snapshot: { plan: { name: '测试方案' }, scenario: { name: '测试场景' }, resources: [] },
    steps: [step],
    metrics: [],
    artifacts: [],
    verdict: null,
    error_code: null,
    error_message: null,
  })
  const refreshStatisticsAnalyses = vi.fn().mockResolvedValue(undefined)
  const loadStatisticsAnalysisDetail = vi.fn().mockResolvedValue(null)
  const statistics = {
    canEditStatisticsConfig: ref(options.canEditStatisticsConfig ?? true),
    displayStatisticsValue: (value: unknown) => String(value),
    displayedStatisticsCsvFiles: ref([{
      relative_path: 'latency.csv', filename: 'latency.csv', source: 'root', size: 1024,
      modified_at: '2026-08-10T10:00:00+08:00',
    }]),
    loadStatisticsAnalysisDetail,
    loadingStatisticsCsvFiles: ref(false),
    loadingStatisticsAnalyses: ref(false),
    loadingStatisticsAnalysisNo: ref<number | null>(null),
    refreshStatisticsCsvFiles: vi.fn(),
    refreshStatisticsAnalyses,
    saveStatisticsConfig: vi.fn(),
    savingStatisticsInputs: ref(false),
    selectedRelativePaths: ref(['latency.csv']),
    statisticsAnalyses: ref(analyses),
    statisticsAnalysisDetails: ref(options.analysisDetails ?? {}),
    statisticsCsvDirectory: ref('/srv/statistics'),
    statisticsCompletionBlocked: ref(options.completionBlocked ?? false),
    statisticsCompletionStale: ref(options.completionStale ?? false),
    statisticsConfigDirty: ref(options.configDirty ?? false),
    statisticsConfigReady: ref(options.configReady ?? true),
    statisticsConfigSaved: ref(options.configSaved ?? true),
    statisticsMaxLatencyNsDraft: ref(2000),
    statisticsResults: ref(options.statisticsResults ?? []),
    statisticsThresholdValid: ref(true),
    statisticsUnit: ref<'ns' | 'us'>('ns'),
  }
  const runActions = {
    actingStepId: ref<number | null>(null),
    action: vi.fn(), cancel: vi.fn(), download: vi.fn(), openVerdict: vi.fn(),
    reanalyzeStatistics: vi.fn(),
    reanalyzingStatisticsStepId: ref<number | null>(options.reanalyzing ? 32 : null),
    regenerateReports: vi.fn(), regeneratingReports: ref(false), stepAction: vi.fn(), submitVerdict: vi.fn(),
    verdict: ref({ final_result: 'passed', issue_description: '', notes: '' }), verdictDialog: ref(false),
  }
  const inert = vi.fn()
  return {
    step,
    lifecycle: { load: vi.fn().mockResolvedValue(undefined), logs: ref([]), run },
    statistics,
    runActions,
    presentation: {
      configRows: ref([]), contractFiles: ref([]), inputChecksums: ref([]), parserOutputFiles: ref([]),
      parserTableRows: ref([]), resultRows: ref([]), selectedArtifacts: ref([]), selectedConfig: ref({}),
      selectedContractFileIds: ref([]), selectedResult: ref({}), showCaptureDetails: ref(false), summaryRows: ref([]),
    },
    wiring: {
      canEditWiringNames: ref(false), cancelEditingWiringNames: inert, editingWiringNames: ref(false),
      saveWiringInterfaceNames: inert, savingWiringNames: ref(false), startEditingWiringNames: inert,
      updateWiringInterfaceIpAddress: inert, updateWiringInterfaceName: inert, wiringActionBlocked: ref(false),
      wiringNamesDirty: ref(false), wiringSnapshot: ref(null), wiringValidationMessage: ref(''),
    },
    terminal: {
      availableParserActions: ref([]), handleParserAction: inert, handleWorkflowTerminalCommand: inert,
      handleWorkflowTerminalError: inert, handleWorkflowTerminalStatus: inert, marketResource: ref(null),
      marketTerminalSubtitle: ref(''), marketWorkflowTerminalPanel: ref(null), orderResource: ref(null),
      orderTerminalSubtitle: ref(''), orderWorkflowTerminalPanel: ref(null), parserActionPending: ref(null),
      parserResource: ref(null), parserTerminalSubtitle: ref(''), parserWorkflowTerminalPanel: ref(null),
      remResource: ref(null), remTerminalSubtitle: ref(''), remWorkflowTerminalPanel: ref(null),
      runWorkflowStepInTerminal: inert, sendParserAction: inert, showWorkflowTerminal: ref(false), slnicResource: ref(null),
      slnicTerminalSubtitle: ref(''), slnicWorkflowTerminalPanel: ref(null), terminalCommandPendingStepId: ref(null),
      stopWorkflowTerminal: () => true, workflowTerminalKind: ref(''), workflowTerminalResource: ref(null),
      workflowTerminalResourceText: ref('资源'), workflowTerminalTitle: ref('终端'), workflowTerminalDescription: ref(''),
    },
    orderConfig: {
      canEditOrderConfig: ref(false), cancelEditingOrderConfig: inert, editingOrderConfig: ref(false),
      loadingOrderConfigs: ref(false), orderConfigActionBlocked: ref(false), orderConfigDirty: ref(false),
      orderConfigDraft: ref({}), orderConfigFiles: ref([]), orderConfigValidationMessage: ref(''),
      refreshOrderConfigs: inert, saveOrderRuntimeConfig: inert, savingOrderConfig: ref(false), startEditingOrderConfig: inert,
    },
    orderActions: {
      availableOrderActions: ref([]), canSendOrderActions: ref(false), confirmCurrentOrderAction: inert,
      defaultOrderAction: ref(''), isDangerousOrderAction: () => false, orderActionUnresolved: ref(false),
      recentOrderActionHistory: ref([]), retryUnknownOrderAction: inert, sendOrderAction: inert, sendingOrderAction: ref(null),
    },
    parserExports: {
      canExportParserTables: ref(false), exportingTable: ref(null), exportParserTable: inert, parserExportRows: ref([]),
    },
  }
}

const ElButtonStub = defineComponent({
  name: 'ElButton',
  inheritAttrs: false,
  props: { disabled: Boolean, loading: Boolean },
  emits: ['click'],
  template: '<button v-bind="$attrs" :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>',
})
const ElInputNumberStub = defineComponent({
  name: 'ElInputNumber',
  props: { id: String, modelValue: Number, disabled: Boolean },
  emits: ['update:modelValue'],
  setup(_props, { emit }) {
    return { updateValue: (event: Event) => emit('update:modelValue', Number((event.target as HTMLInputElement).value)) }
  },
  template: '<input class="input-number-stub" type="number" :id="id" :value="modelValue" :disabled="disabled" @input="updateValue">',
})
const ElCheckboxGroupStub = defineComponent({
  name: 'ElCheckboxGroup',
  props: { modelValue: Array, disabled: Boolean },
  emits: ['update:modelValue'],
  template: '<div class="checkbox-group-stub" :data-disabled="disabled" v-bind="$attrs"><slot /></div>',
})
const ElCheckboxStub = defineComponent({
  name: 'ElCheckbox',
  props: { disabled: Boolean },
  template: '<label class="checkbox-stub"><input type="checkbox" :disabled="disabled"><slot /></label>',
})
const ElCollapseStub = defineComponent({
  name: 'ElCollapse',
  props: { modelValue: [String, Number], accordion: Boolean },
  emits: ['update:modelValue', 'change'],
  template: '<div class="collapse-stub" :data-active="modelValue" :data-accordion="accordion"><slot /></div>',
})
const ElCollapseItemStub = defineComponent({
  name: 'ElCollapseItem',
  props: { name: [String, Number] },
  template: '<section class="collapse-item-stub" :data-name="name"><header><slot name="title" /></header><slot /></section>',
})
const ElAlertStub = defineComponent({
  name: 'ElAlert',
  props: { title: String, description: String },
  template: '<div class="alert-stub"><strong>{{ title }}</strong><span>{{ description }}</span></div>',
})
const slotStub = { template: '<div><slot /></div>' }

async function mountRunDetail(options: HarnessOptions = {}) {
  currentHarness = createHarness(options)
  const wrapper = mount(RunDetailView, {
    global: {
      directives: { loading: () => {} },
      stubs: {
        ElAlert: ElAlertStub,
        ElButton: ElButtonStub,
        ElCheckbox: ElCheckboxStub,
        ElCheckboxGroup: ElCheckboxGroupStub,
        ElCollapse: ElCollapseStub,
        ElCollapseItem: ElCollapseItemStub,
        ElDialog: true,
        ElEmpty: true,
        ElForm: slotStub,
        ElFormItem: slotStub,
        ElInput: true,
        ElInputNumber: ElInputNumberStub,
        ElProgress: true,
        ElRadioButton: slotStub,
        ElRadioGroup: slotStub,
        ElSelect: slotStub,
        ElOption: true,
        ElSkeleton: true,
        ElTabPane: slotStub,
        ElTabs: slotStub,
        ElTable: slotStub,
        ElTableColumn: true,
        ElTag: slotStub,
        ElTooltip: slotStub,
        OrderConfigPanel: true,
        RunCaptureDetails: true,
        RunContractFiles: true,
        RunContractPreviewDialog: true,
        RunLogPanel: true,
        RunWorkflowStrip: true,
        SshTerminalPanel: true,
        StatusBadge: true,
        WindowsEditcapCommand: true,
        WiringTopologyDiagram: true,
      },
    },
  })
  await flushPromises()
  return { wrapper, harness: currentHarness }
}

function button(wrapper: VueWrapper, text: string) {
  const target = wrapper.findAll('button').find(item => item.text() === text)
  expect(target, `button ${text}`).toBeDefined()
  return target!
}

describe('RunDetailView statistics behavior', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('blocks completion for an unsaved draft and for a saved stale analysis with visible reasons', async () => {
    const { wrapper, harness } = await mountRunDetail({ configDirty: true, configSaved: false, completionBlocked: true })
    expect(button(wrapper, '完成').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('请先选择 CSV、填写正整数的最大延迟上限并保存分析配置')

    harness.statistics.statisticsConfigDirty.value = false
    harness.statistics.statisticsConfigSaved.value = true
    harness.statistics.statisticsCompletionStale.value = true
    await nextTick()
    expect(button(wrapper, '完成').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('当前分析配置尚无成功结果')
    wrapper.unmount()
  })

  it('saves the draft and starts reanalysis from the rendered controls', async () => {
    const { wrapper, harness } = await mountRunDetail({ configDirty: true, configSaved: false })
    await button(wrapper, '保存分析配置').trigger('click')
    expect(harness.statistics.saveStatisticsConfig).toHaveBeenCalledTimes(1)

    harness.statistics.statisticsConfigDirty.value = false
    harness.statistics.statisticsConfigSaved.value = true
    await nextTick()
    await button(wrapper, '再次分析').trigger('click')
    expect(harness.runActions.reanalyzeStatistics).toHaveBeenCalledWith(harness.step)
    wrapper.unmount()
  })

  it('disables completion and conflicting statistics controls while reanalysis is pending', async () => {
    const { wrapper } = await mountRunDetail({ reanalyzing: true, configDirty: true })
    expect(button(wrapper, '完成').attributes('disabled')).toBeDefined()
    expect(button(wrapper, '再次分析').attributes('disabled')).toBeDefined()
    expect(button(wrapper, '保存分析配置').attributes('disabled')).toBeDefined()
    expect(wrapper.get('button[aria-label="刷新统计 CSV"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('.checkbox-group-stub').attributes('data-disabled')).toBe('true')
    expect(wrapper.get('.input-number-stub').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('统计分析正在执行')
    wrapper.unmount()
  })

  it('refreshes the same step and expands a newly successful analysis when its history signature changes', async () => {
    const { wrapper, harness } = await mountRunDetail({ analyses: [analysis(1)] })
    harness.statistics.refreshStatisticsAnalyses.mockClear()
    harness.statistics.loadStatisticsAnalysisDetail.mockClear()

    const second = analysis(2)
    harness.statistics.statisticsAnalyses.value = [second, analysis(1)]
    harness.lifecycle.run.value.steps[0].result_summary = {
      ...harness.lifecycle.run.value.steps[0].result_summary,
      statistics_analyses: [analysis(1), second],
      statistics_latest_success_analysis_no: 2,
      statistics_latest_success_revision: 2,
    }
    await nextTick()
    await flushPromises()

    expect(harness.statistics.refreshStatisticsAnalyses).toHaveBeenCalledTimes(1)
    expect(harness.statistics.loadStatisticsAnalysisDetail).toHaveBeenCalledWith(2)
    expect(wrapper.getComponent(ElCollapseStub).props('modelValue')).toBe('2')
    wrapper.unmount()
  })

  it('loads an older analysis detail only when its accordion item is expanded', async () => {
    const { wrapper, harness } = await mountRunDetail({ analyses: [analysis(2), analysis(1, 'failed')] })
    expect(wrapper.getComponent(ElCollapseStub).props('accordion')).toBe(true)
    expect(harness.statistics.loadStatisticsAnalysisDetail).toHaveBeenCalledWith(2)
    expect(harness.statistics.loadStatisticsAnalysisDetail).not.toHaveBeenCalledWith(1)

    wrapper.getComponent(ElCollapseStub).vm.$emit('change', '1')
    await flushPromises()
    expect(harness.statistics.loadStatisticsAnalysisDetail).toHaveBeenCalledWith(1)
    wrapper.unmount()
  })

  it('shows compatibility statistics results only for runs without the history structure', async () => {
    const result = { source_file: 'legacy.csv', sample_count: 1, metrics: [] }
    const modern = await mountRunDetail({ analyses: [], historyStructure: true, statisticsResults: [result] })
    expect(modern.wrapper.find('.statistics-legacy-results').exists()).toBe(false)
    expect(modern.wrapper.text()).not.toContain('legacy.csv')
    modern.wrapper.unmount()

    const legacy = await mountRunDetail({ analyses: [], historyStructure: false, statisticsResults: [result] })
    expect(legacy.wrapper.find('.statistics-legacy-results').exists()).toBe(true)
    expect(legacy.wrapper.text()).toContain('legacy.csv')
    legacy.wrapper.unmount()
  })

  it('shows the archived failure message without suggesting edits for a frozen statistics node', async () => {
    const failed = analysis(1, 'failed')
    const { wrapper } = await mountRunDetail({
      analyses: [failed],
      canEditStatisticsConfig: false,
      analysisDetails: {
        1: { analysis: failed, artifact: { error: { code: failed.error_code, message: '远端脚本退出状态 2' } } },
      },
    })
    expect(wrapper.text()).toContain('远端脚本退出状态 2')
    expect(wrapper.text()).toContain('节点已冻结')
    expect(wrapper.text()).not.toContain('可修改配置后重新分析')
    wrapper.unmount()
  })

  it('associates the CSV group and maximum-latency input without nested labels', async () => {
    const { wrapper } = await mountRunDetail()
    expect(wrapper.find('.statistics-config-form label label').exists()).toBe(false)
    expect(wrapper.get('.checkbox-group-stub').attributes('aria-labelledby')).toBe('statistics-csv-input-label')
    expect(wrapper.get('label[for="statistics-max-latency-ns"]').text()).toContain('最大延迟上限')
    expect(wrapper.find('#statistics-max-latency-ns').exists()).toBe(true)
    wrapper.unmount()
  })
})
