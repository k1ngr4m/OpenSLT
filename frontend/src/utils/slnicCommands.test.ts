import { describe, expect, it } from 'vitest'
import {
  DEFAULT_EDITCAP_PATH,
  buildWindowsEditcapCommand,
  defaultSlnicCommands,
  slnicCommandText,
} from '@/utils/slnicCommands'

describe('slnicCommands', () => {
  it('provides independent defaults for every SLNIC node', () => {
    expect(defaultSlnicCommands('slnic_start_capture')).toEqual(['./start_slnic_dump.sh'])
    expect(defaultSlnicCommands('slnic_stop_capture')).toEqual(['./stop_slnic_dump.sh'])
    expect(defaultSlnicCommands('slnic_merge_capture')).toEqual([
      './pcap_merge_tool slnic*',
    ])
  })

  it('uses defaults only for legacy missing configuration', () => {
    expect(slnicCommandText('slnic_start_capture', undefined)).toBe('./start_slnic_dump.sh')
    expect(slnicCommandText('slnic_start_capture', [])).toBe('')
    expect(slnicCommandText('slnic_stop_capture', [' export MODE=test ', 7, './run.sh'])).toBe(
      ' export MODE=test \n./run.sh',
    )
  })

  it('builds the quoted Windows editcap preview from bound resources', () => {
    expect(buildWindowsEditcapCommand(
      DEFAULT_EDITCAP_PATH,
      { host: '10.1.51.210', remote_path: '/home/user0/slnic/SLNIC NF11' },
      { host: '10.1.51.210', remote_path: '/home/user0/ckd/speed analysis' },
    )).toBe(
      '"D:\\Program Files\\Wireshark\\editcap.exe" -F pcapng '
      + '"\\\\10.1.51.210\\user0\\slnic\\SLNIC NF11\\tcpdump\\merge_pcap.pcap" '
      + '"\\\\10.1.51.210\\user0\\ckd\\speed analysis\\merge_pcap.pcapng"',
    )
  })

  it('does not preview UNC paths for resources outside /home', () => {
    expect(buildWindowsEditcapCommand(
      DEFAULT_EDITCAP_PATH,
      { host: '10.1.51.210', remote_path: '/tmp/openslt' },
      { host: '10.1.51.210', remote_path: '/home/user0/parser' },
    )).toBe('')
  })
})
