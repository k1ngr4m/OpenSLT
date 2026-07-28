export type ParserXmlRole = 'config' | 'instance' | 'analysis'

export function parserXmlRole(filename: string): ParserXmlRole | 'invalid' {
  if (/^config(?:[._-][A-Za-z0-9._-]+)?\.xml$/i.test(filename)) return 'config'
  if (/^instance(?:[._-][A-Za-z0-9._-]+)?\.xml$/i.test(filename)) return 'instance'
  return /^[A-Za-z0-9._-]+\.xml$/i.test(filename) ? 'analysis' : 'invalid'
}
