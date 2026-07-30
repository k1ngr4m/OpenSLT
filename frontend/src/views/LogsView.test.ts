import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'src/views/LogsView.vue'), 'utf8')

describe('LogsView observability workspace', () => {
  it('uses paginated search for each structured log category', () => {
    expect(source).toContain("api.get<ApiLogSearchPage>('/logs/search'")
    expect(source).toContain("{ name: 'access', label: 'HTTP' }")
    expect(source).toContain("{ name: 'sql', label: 'SQL' }")
    expect(source).toContain("{ name: 'websocket', label: 'WebSocket' }")
    expect(source).toContain('<el-pagination')
    expect(source).toContain('min_duration_ms')
    expect(source).toContain('sql_fingerprint')
  })

  it('restricts payload details to administrators', () => {
    expect(source).toContain('if (!auth.isAdmin || !row.event_id) return')
    expect(source).toContain('api.get<ApiLogDetail>(`/logs/${row.event_id}`)')
    expect(source).toContain('v-if="auth.isAdmin"')
    expect(source).toContain('detail.payload.statement_template')
    expect(source).toContain('detail.payload.request')
    expect(source).toContain('detail.payload.response')
  })
})
