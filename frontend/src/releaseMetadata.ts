export type ReleaseChangeType = 'added' | 'changed' | 'fixed' | 'removed' | 'security'

export interface ReleaseChange {
  type: ReleaseChangeType
  text: string
}

export interface ReleaseRecord {
  version: string
  date: string | null
  title: string
  changes: ReleaseChange[]
}

declare const __OPENSLT_VERSION__: string
declare const __OPENSLT_RELEASES__: ReleaseRecord[]

export const appVersion = __OPENSLT_VERSION__
export const releaseHistory = Object.freeze(__OPENSLT_RELEASES__)

export const releaseChangeLabels: Record<ReleaseChangeType, string> = {
  added: '新增',
  changed: '变更',
  fixed: '修复',
  removed: '移除',
  security: '安全',
}
