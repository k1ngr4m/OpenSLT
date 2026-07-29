export interface MarketScriptSelection {
  filename: string
  checksum: string
}

export interface MarketScriptFile {
  name: string
  checksum: string
  executable: boolean
}

export type MarketScriptSelectionStatus = 'valid' | 'missing' | 'not_executable' | 'changed'

export function marketScriptSelectionStatus(
  selection: MarketScriptSelection,
  file?: MarketScriptFile,
): MarketScriptSelectionStatus {
  if (!file) return 'missing'
  if (!file.executable) return 'not_executable'
  if (file.checksum !== selection.checksum) return 'changed'
  return 'valid'
}

export function toggleMarketScriptSelection(
  selections: MarketScriptSelection[],
  file: MarketScriptFile,
  checked: boolean,
) {
  const next = [...selections]
  const index = next.findIndex(item => item.filename === file.name)
  if (checked && index < 0 && file.executable) next.push({ filename: file.name, checksum: file.checksum })
  if (!checked && index >= 0) next.splice(index, 1)
  return next
}

export function moveMarketScriptSelection(
  selections: MarketScriptSelection[],
  index: number,
  offset: number,
) {
  const target = index + offset
  if (target < 0 || target >= selections.length) return [...selections]
  const next = [...selections]
  const [item] = next.splice(index, 1)
  next.splice(target, 0, item)
  return next
}
