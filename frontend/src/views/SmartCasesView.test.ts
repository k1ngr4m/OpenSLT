import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'src/views/SmartCasesView.vue'), 'utf8')

describe('SmartCasesView SVN knowledge source', () => {
  it('keeps credentials write-only and exposes the required safety controls', () => {
    expect(source).toContain('留空表示不修改')
    expect(source).toContain('HTTP 明文传输风险')
    expect(source).toContain('允许索引的相对路径')
    expect(source).toContain('添加仓库 URL')
    expect(source).toContain('删除第 ${index + 1} 条仓库 URL')
    expect(source).toContain('默认仓库 URL（可多条）')
    expect(source).toContain('每 30 分钟')
    expect(source).toContain('aria-live="polite"')
    expect(source).not.toContain('{{ form.password }}')
  })
})
