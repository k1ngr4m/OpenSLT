import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'src/views/SmartCaseGenerateView.vue'), 'utf8')

describe('SmartCaseGenerateView', () => {
  it('selects an indexed requirement and generates a downloadable Excel draft', () => {
    expect(source).toContain('/smart-cases/requirements')
    expect(source).toContain('type="radio"')
    expect(source).toContain('requirement_path')
    expect(source).toContain('生成 Excel 用例草稿')
    expect(source).toContain('/download')
    expect(source).toContain('aria-live="polite"')
  })
})
