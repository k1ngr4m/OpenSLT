export const parserActionOptions = [
  'write_clt_new_to_rem_accept',
  'write_clt_new_to_mkt',
  'write_clt_action_to_mkt',
  'write_clt_action_quote_to_mkt',
  'write_mkt_accept_to_clt',
  'write_mkt_new_to_mkt_accept',
  'write_clt_new_to_clt_accept',
  'write_clt_mkt_trade',
  'write_clt_new_quote_to_rem_accept',
  'write_clt_new_quote_to_mkt',
  'write_mkt_quote_accept_to_clt',
  'write_mkt_quote_new_to_mkt_accept',
  'write_clt_quote_new_to_clt_accept',
] as const

export type ParserAction = typeof parserActionOptions[number]

export function parserActionsFromCapabilities(capabilities: unknown): ParserAction[] {
  if (!capabilities || typeof capabilities !== 'object' || Array.isArray(capabilities)) {
    return [...parserActionOptions]
  }
  const configured = (capabilities as Record<string, unknown>).parser_actions
  if (!Array.isArray(configured)) return [...parserActionOptions]
  return configured.filter(
    (action, index): action is ParserAction => (
      typeof action === 'string'
      && parserActionOptions.includes(action as ParserAction)
      && configured.indexOf(action) === index
    ),
  )
}

export function parserActionsPayload(actions: readonly ParserAction[]) {
  return { parser_actions: [...actions] }
}
