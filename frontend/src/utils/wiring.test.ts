import { describe, expect, it } from 'vitest'
import { buildWiringSnapshot } from '@/utils/wiring'

describe('wiring snapshots', () => {
  it('builds a hard-core snapshot from the three selected resources', () => {
    const snapshot = buildWiringSnapshot(
      'rem_two_mm',
      { id: 8, name: '51.129 rem硬核', host: '10.1.51.129', trade_ip: '10.1.51.146' },
      { id: 10, name: '模拟市场', host: '10.1.51.198' },
      { id: 9, name: '51.210 slnic板卡', host: '10.1.51.210' },
    )
    expect(snapshot).toMatchObject({
      schema_version: 2,
      topology_kind: 'hard_core_mg11',
      model_label: 'MG11',
      client_switch_label: '51段交换机（客户端）',
      market_switch_label: '51段交换机（市场端）',
      client_interface: { name: '1(mac0)', ip_address: '10.1.51.146' },
      market_interface: { name: '2(mac1)', ip_address: '10.1.51.198' },
      market: { id: 10, name: '模拟市场', host: '10.1.51.198' },
      slnic: { id: 9, name: '51.210 slnic板卡', host: '10.1.51.210' },
      auxiliary_interfaces: ['3(mac2)', '4(mac3)'],
    })
    expect(snapshot?.slnic_ports.map(item => `${item.port}:${item.label}`)).toEqual([
      '0:客户端上行', '1:市场上行', '2:市场下行', '3:客户端下行',
    ])
  })

  it('does not build a preview until every resource IP is valid', () => {
    expect(buildWiringSnapshot(
      'fut_mm',
      { id: 1, name: 'REM', trade_ip: '10.1.1.999' },
      { id: 2, name: '市场', host: '10.1.1.2' },
      { id: 3, name: 'SLNIC', host: '10.1.1.3' },
    )).toBeNull()
  })
})
