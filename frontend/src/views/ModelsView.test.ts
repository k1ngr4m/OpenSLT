import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'src/views/ModelsView.vue'), 'utf8')

describe('ModelsView', () => {
  it('manages providers and tests chat and embedding models independently', () => {
    expect(source).toContain('label="对话" name="chat"')
    expect(source).toContain('label="Embedding" name="embedding"')
    expect(source).toContain('获取模型列表')
    expect(source).toContain('手动添加')
    expect(source).toContain('设为当前')
    expect(source).toContain('/connection-test')
    expect(source.match(/\{ timeout: 0 \}/g)).toHaveLength(2)
    expect(source).toContain('留空表示不修改')
    expect(source).not.toContain('{{ form.api_key }}')
  })
})
