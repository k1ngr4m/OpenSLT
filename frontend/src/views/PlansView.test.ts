import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'src/views/PlansView.vue'), 'utf8')

describe('PlansView scenario form', () => {
  it('does not expose scenario type, config version, or enable controls', () => {
    const scenarioDialog = source.match(/<el-dialog v-model="scenarioDialog"[\s\S]*?<\/el-dialog>/)?.[0]
    expect(scenarioDialog).toBeTruthy()
    expect(scenarioDialog).not.toContain('label="场景类型"')
    expect(scenarioDialog).not.toContain('label="配置版本"')
    expect(scenarioDialog).not.toContain('label="启用"')
  })

  it('submits only system-owned defaults for hidden scenario fields', () => {
    const saveScenario = source.match(/async function saveScenario\(\)[\s\S]*?\n}\n\nasync function copyPlan/)?.[0]
    expect(saveScenario).toContain("scenario_type: scenario.scenario_type || 'order'")
    expect(saveScenario).toContain("config_version: scenario.config_version || '1.0'")
    expect(saveScenario).not.toContain('...scenario')
    expect(saveScenario).not.toContain('is_enabled:')
  })
})
