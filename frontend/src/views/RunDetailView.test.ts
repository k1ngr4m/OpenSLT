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
})
