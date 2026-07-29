export type DatabaseConfigItem = {
  key: string
  description: string | null
}

export type DatabaseConfigTemplate = {
  id: number
  name: string
  keys: string[]
  created_at: string
  updated_at: string
}

export function filterDatabaseConfigItems(items: DatabaseConfigItem[], query: string) {
  const folded = query.trim().toLocaleLowerCase()
  if (!folded) return items
  return items.filter(item =>
    item.key.toLocaleLowerCase().includes(folded)
    || (item.description || '').toLocaleLowerCase().includes(folded),
  )
}

export function staleDatabaseConfigKeys(selectedKeys: string[], items: DatabaseConfigItem[]) {
  const available = new Set(items.map(item => item.key))
  return selectedKeys.filter(key => !available.has(key))
}

export function applyDatabaseConfigTemplate(templateKeys: string[], items: DatabaseConfigItem[]) {
  const available = new Set(items.map(item => item.key))
  return {
    selected: templateKeys.filter(key => available.has(key)),
    missing: templateKeys.filter(key => !available.has(key)),
  }
}
