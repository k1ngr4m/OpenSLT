import { describe, expect, it } from 'vitest'
import {
  applyDatabaseConfigTemplate,
  filterDatabaseConfigItems,
  staleDatabaseConfigKeys,
  type DatabaseConfigItem,
} from './databaseConfig'

const items: DatabaseConfigItem[] = [
  { key: 'ACCOUNT_QUANTITY', description: null },
  { key: 'ASYNC_MKT_MSG_PROC', description: '市场回报的默认同步模式' },
]

describe('database config helpers', () => {
  it('searches both key and description', () => {
    expect(filterDatabaseConfigItems(items, 'async')).toEqual([items[1]])
    expect(filterDatabaseConfigItems(items, '市场回报')).toEqual([items[1]])
  })

  it('applies only keys available in the current database', () => {
    expect(applyDatabaseConfigTemplate(['ASYNC_MKT_MSG_PROC', 'REMOVED'], items)).toEqual({
      selected: ['ASYNC_MKT_MSG_PROC'],
      missing: ['REMOVED'],
    })
    expect(staleDatabaseConfigKeys(['ACCOUNT_QUANTITY', 'REMOVED'], items)).toEqual(['REMOVED'])
  })
})
