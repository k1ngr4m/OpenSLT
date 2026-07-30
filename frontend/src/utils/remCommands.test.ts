import { describe, expect, it } from 'vitest'
import { DEFAULT_REM_STARTUP_COMMANDS, normalizeRemStartupCommands, remStartupCommandText } from '@/utils/remCommands'

describe('REM startup command helpers', () => {
  it('normalizes one command per line and ignores blank lines', () => {
    expect(normalizeRemStartupCommands('  export MODE=test  \n\n cd state\r\nprintf "$MODE" ')).toEqual([
      'export MODE=test',
      'cd state',
      'printf "$MODE"',
    ])
  })

  it('uses defaults only for legacy missing configuration', () => {
    expect(remStartupCommandText(undefined)).toBe(DEFAULT_REM_STARTUP_COMMANDS.join('\n'))
    expect(remStartupCommandText([])).toBe('')
  })
})
