import { describe, expect, it } from 'vitest'
import { marketScriptSelectionStatus, moveMarketScriptSelection, toggleMarketScriptSelection } from '@/utils/marketScripts'

describe('market script workflow selection', () => {
  it('only adds executable scripts and preserves selection order', () => {
    const first = { name: 'prepare.sh', checksum: 'a', executable: true }
    const second = { name: 'start.sh', checksum: 'b', executable: true }
    const disabled = { name: 'disabled.sh', checksum: 'c', executable: false }

    let selections = toggleMarketScriptSelection([], first, true)
    selections = toggleMarketScriptSelection(selections, second, true)
    selections = toggleMarketScriptSelection(selections, disabled, true)
    expect(selections.map(item => item.filename)).toEqual(['prepare.sh', 'start.sh'])

    selections = moveMarketScriptSelection(selections, 1, -1)
    expect(selections.map(item => item.filename)).toEqual(['start.sh', 'prepare.sh'])
  })

  it('detects missing, non-executable, and changed selections', () => {
    const selection = { filename: 'start.sh', checksum: 'saved' }
    expect(marketScriptSelectionStatus(selection)).toBe('missing')
    expect(marketScriptSelectionStatus(selection, { name: 'start.sh', checksum: 'saved', executable: false })).toBe('not_executable')
    expect(marketScriptSelectionStatus(selection, { name: 'start.sh', checksum: 'new', executable: true })).toBe('changed')
    expect(marketScriptSelectionStatus(selection, { name: 'start.sh', checksum: 'saved', executable: true })).toBe('valid')
  })
})
