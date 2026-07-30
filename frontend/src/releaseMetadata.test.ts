import { describe, expect, it } from 'vitest'
import { appVersion, releaseHistory } from './releaseMetadata'

describe('release metadata', () => {
  it('exposes the canonical version and newest-first release history', () => {
    expect(appVersion).toBe('0.2.1')
    expect(releaseHistory.map(release => release.version)).toEqual(['0.2.1', '0.2.0', '0.1.0'])
    expect(releaseHistory[2].date).toBeNull()
  })
})
