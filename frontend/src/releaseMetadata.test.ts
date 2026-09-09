import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { appVersion, releaseHistory } from './releaseMetadata'

describe('release metadata', () => {
  it('exposes the canonical version and newest-first release history', () => {
    const version = readFileSync(resolve(process.cwd(), '../VERSION'), 'utf8').trim()
    const releases = JSON.parse(readFileSync(resolve(process.cwd(), '../RELEASES.json'), 'utf8')).releases
    expect(appVersion).toBe(version)
    expect(releaseHistory[0].version).toBe(version)
    expect(releaseHistory).toEqual(releases)
  })
})
