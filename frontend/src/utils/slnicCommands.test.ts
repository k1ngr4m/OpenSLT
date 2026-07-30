import { describe, expect, it } from 'vitest'
import { defaultSlnicCommands, slnicCommandText } from '@/utils/slnicCommands'

describe('slnicCommands', () => {
  it('provides independent defaults for every SLNIC node', () => {
    expect(defaultSlnicCommands('slnic_start_capture')).toEqual(['./start_slnic_dump.sh'])
    expect(defaultSlnicCommands('slnic_stop_capture')).toEqual(['./stop_slnic_dump.sh'])
    expect(defaultSlnicCommands('slnic_merge_capture')).toEqual([
      './pcap_merge_tool slnic*',
      'if [ ! -f merge_pcap.pcap ] && [ -f merge_pacp.pcap ]; then mv -- merge_pacp.pcap merge_pcap.pcap; fi; test -f merge_pcap.pcap',
      './editcap merge_pcap.pcap merge_pcap.pcapng && test -f merge_pcap.pcapng',
    ])
  })

  it('uses defaults only for legacy missing configuration', () => {
    expect(slnicCommandText('slnic_start_capture', undefined)).toBe('./start_slnic_dump.sh')
    expect(slnicCommandText('slnic_start_capture', [])).toBe('')
    expect(slnicCommandText('slnic_stop_capture', [' export MODE=test ', 7, './run.sh'])).toBe(
      ' export MODE=test \n./run.sh',
    )
  })
})
