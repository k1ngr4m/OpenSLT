import { describe, expect, it } from 'vitest'
import { buildWiringSnapshot, fillWiringPreset, WIRING_PRESETS } from '@/utils/wiring'

describe('wiring presets', () => {
  it('provides the three confirmed business defaults', () => {
    expect(WIRING_PRESETS.fut_mm.client_interface).toEqual({ name: 'enp101s0d1', ip_address: '180.1.1.101' })
    expect(WIRING_PRESETS.rem_two.market_interface).toEqual({ name: '2(mac1)', ip_address: '10.1.51.116' })
    expect(WIRING_PRESETS.rem_two_mm.client_interface).toEqual({ name: '1(mac0)', ip_address: '10.1.51.146' })
  })

  it('only fills blank fields when the business changes', () => {
    const result = fillWiringPreset({
      client_switch_label: '自定义客户端交换机',
      client_interface: { name: 'custom0', ip_address: '' },
    }, 'rem_two')
    expect(result.client_switch_label).toBe('自定义客户端交换机')
    expect(result.client_interface).toEqual({ name: 'custom0', ip_address: '10.1.51.242' })
    expect(result.market_interface).toEqual({ name: '2(mac1)', ip_address: '10.1.51.116' })
  })

  it('builds a hard-core snapshot with immutable port semantics', () => {
    const snapshot = buildWiringSnapshot(
      'rem_two_mm',
      { id: 8, name: '51.129 rem硬核', host: '10.1.51.129', wiring_profile: WIRING_PRESETS.rem_two_mm },
      { id: 9, name: '51.210 slnic板卡', host: '10.1.51.210' },
    )
    expect(snapshot).toMatchObject({
      topology_kind: 'hard_core_mg11',
      model_label: 'MG11',
      auxiliary_interfaces: ['3(mac2)', '4(mac3)'],
    })
    expect(snapshot?.slnic_ports.map(item => `${item.port}:${item.label}`)).toEqual([
      '0:客户端上行', '1:市场上行', '2:市场下行', '3:客户端下行',
    ])
  })
})
