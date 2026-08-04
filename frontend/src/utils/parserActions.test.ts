import { describe, expect, it } from 'vitest'
import {
  parserActionOptions,
  parserActionsFromCapabilities,
  parserActionsPayload,
} from '@/utils/parserActions'

describe('parser action resource configuration', () => {
  it('defaults missing capabilities to every parser action', () => {
    expect(parserActionsFromCapabilities({ parser_tool: 'soft_cffex_speed_analysis_v2' }))
      .toEqual(parserActionOptions)
  })

  it('preserves an explicit empty action selection', () => {
    expect(parserActionsFromCapabilities({ parser_actions: [] })).toEqual([])
    expect(parserActionsPayload([])).toEqual({ parser_actions: [] })
  })

  it('keeps only known actions in the configured order', () => {
    expect(parserActionsFromCapabilities({
      parser_actions: [parserActionOptions[2], 'unknown', parserActionOptions[0]],
    })).toEqual([parserActionOptions[2], parserActionOptions[0]])
  })
})
