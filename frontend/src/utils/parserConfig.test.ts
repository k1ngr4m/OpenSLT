import { describe, expect, it } from 'vitest'
import { parserXmlRole } from '@/utils/parserConfig'

describe('parserXmlRole', () => {
  it('classifies standard files and same-role copies', () => {
    expect(parserXmlRole('config.xml')).toBe('config')
    expect(parserXmlRole('config-scenario-a.xml')).toBe('config')
    expect(parserXmlRole('instance_backup.xml')).toBe('instance')
    expect(parserXmlRole('instance.v2.xml')).toBe('instance')
    expect(parserXmlRole('soft_cffex_speed_analysis.xml')).toBe('analysis')
    expect(parserXmlRole('scenario.xml')).toBe('analysis')
  })

  it('rejects unsafe or non-XML names', () => {
    expect(parserXmlRole('../config.xml')).toBe('invalid')
    expect(parserXmlRole('config.json')).toBe('invalid')
  })
})
