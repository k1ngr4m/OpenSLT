import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

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
