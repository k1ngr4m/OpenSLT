import { readFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'

interface ReleaseChange {
  type: 'added' | 'changed' | 'fixed' | 'removed' | 'security'
  text: string
}

interface ReleaseRecord {
  version: string
  date: string | null
  title: string
  changes: ReleaseChange[]
}

interface ReleaseFile {
  unreleased: ReleaseChange[]
  releases: ReleaseRecord[]
}

export interface ReleaseMetadata extends ReleaseFile {
  version: string
}

const VERSION_PATTERN = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/
const CHANGE_TYPES = new Set(['added', 'changed', 'fixed', 'removed', 'security'])
const REPOSITORY_ROOT = fileURLToPath(new URL('..', import.meta.url))

function requireString(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${field} must be a non-empty string`)
  return value.trim()
}

function parseVersion(value: string): number[] {
  const match = VERSION_PATTERN.exec(value)
  if (!match) throw new Error(`Invalid version ${JSON.stringify(value)}; expected MAJOR.MINOR.PATCH`)
  return match.slice(1).map(Number)
}

function validateChanges(value: unknown, field: string, allowEmpty: boolean): ReleaseChange[] {
  if (!Array.isArray(value) || (!allowEmpty && value.length === 0)) {
    throw new Error(`${field} must be ${allowEmpty ? 'an array' : 'a non-empty array'}`)
  }
  return value.map((raw, index) => {
    if (!raw || typeof raw !== 'object') throw new Error(`${field}[${index}] must be an object`)
    const item = raw as Record<string, unknown>
    const type = requireString(item.type, `${field}[${index}].type`)
    if (!CHANGE_TYPES.has(type)) throw new Error(`${field}[${index}].type is not supported`)
    return {
      type: type as ReleaseChange['type'],
      text: requireString(item.text, `${field}[${index}].text`),
    }
  })
}

export function loadReleaseMetadata(repositoryRoot = REPOSITORY_ROOT): ReleaseMetadata {
  const version = readFileSync(`${repositoryRoot}/VERSION`, 'utf8').trim()
  parseVersion(version)
  const raw = JSON.parse(readFileSync(`${repositoryRoot}/RELEASES.json`, 'utf8')) as Record<string, unknown>
  const unreleased = validateChanges(raw.unreleased, 'unreleased', true)
  if (!Array.isArray(raw.releases) || raw.releases.length === 0) {
    throw new Error('releases must be a non-empty array')
  }

  const seen = new Set<string>()
  const releases = raw.releases.map((rawRelease, index): ReleaseRecord => {
    if (!rawRelease || typeof rawRelease !== 'object') throw new Error(`releases[${index}] must be an object`)
    const item = rawRelease as Record<string, unknown>
    const releaseVersion = requireString(item.version, `releases[${index}].version`)
    parseVersion(releaseVersion)
    if (seen.has(releaseVersion)) throw new Error(`Duplicate release version: ${releaseVersion}`)
    seen.add(releaseVersion)
    let releaseDate: string | null = null
    if (item.date !== null) {
      if (typeof item.date !== 'string' || !DATE_PATTERN.test(item.date)) {
        throw new Error(`releases[${index}].date must use YYYY-MM-DD or null`)
      }
      const parsedDate = new Date(`${item.date}T00:00:00Z`)
      if (Number.isNaN(parsedDate.valueOf()) || parsedDate.toISOString().slice(0, 10) !== item.date) {
        throw new Error(`releases[${index}].date is not a valid calendar date`)
      }
      releaseDate = item.date
    }
    return {
      version: releaseVersion,
      date: releaseDate,
      title: requireString(item.title, `releases[${index}].title`),
      changes: validateChanges(item.changes, `releases[${index}].changes`, false),
    }
  })

  for (let index = 1; index < releases.length; index += 1) {
    const previous = parseVersion(releases[index - 1].version)
    const current = parseVersion(releases[index].version)
    const isDescending = previous[0] > current[0]
      || (previous[0] === current[0] && previous[1] > current[1])
      || (previous[0] === current[0] && previous[1] === current[1] && previous[2] > current[2])
    if (!isDescending) throw new Error('releases must be ordered from newest to oldest')
  }
  if (releases[0].version !== version) {
    throw new Error(`VERSION is ${version}, but the newest release is ${releases[0].version}`)
  }
  return { version, unreleased, releases }
}

export function releaseDefines(): Record<string, string> {
  const metadata = loadReleaseMetadata()
  return {
    __OPENSLT_VERSION__: JSON.stringify(metadata.version),
    __OPENSLT_RELEASES__: JSON.stringify(metadata.releases),
  }
}
