import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import WiringTopologyDiagram from '@/components/WiringTopologyDiagram.vue'
import { buildWiringSnapshot } from '@/utils/wiring'

describe('WiringTopologyDiagram', () => {
  const expectedLinks = {
    'client-uplink-main': { d: 'M520 111 H468 V214 H350', tone: 'uplink' },
    'client-uplink-slnic-0': { d: 'M468 214 V494 H350', tone: 'uplink' },
    'market-uplink-main': { d: 'M350 296 H490 V350 H642', tone: 'uplink' },
    'market-uplink-slnic-1': { d: 'M520 350 V521 H350', tone: 'uplink' },
    'client-downlink-main': { d: 'M350 241 H448 V84 H520', tone: 'downlink' },
    'client-downlink-slnic-3': { d: 'M448 241 H430 V575 H350', tone: 'downlink' },
    'market-downlink-main': { d: 'M642 380 H548 V323 H350', tone: 'downlink' },
    'market-downlink-slnic-2': { d: 'M548 323 V548 H350', tone: 'downlink' },
  } as const

  it.each([
    ['fut_mm', '软核', '180.1.1.101', 'enp23s0'],
    ['rem_two', 'NF11', '180.1.1.101', '2(mac1)'],
    ['rem_two_mm', 'MG11', '180.1.1.101', '2(mac1)'],
  ])('renders %s resource values and link directions', (businessCode, model, clientIp, marketInterface) => {
    const snapshot = buildWiringSnapshot(
      businessCode,
      { id: 1, name: 'REM-01', host: '10.1.51.8', trade_ip: '180.1.1.101' },
      { id: 3, name: 'Market-01', host: '10.1.51.101' },
      { id: 2, name: 'SLNIC-01', host: '10.1.51.210' },
    )
    const wrapper = mount(WiringTopologyDiagram, {
      props: { snapshot },
      global: { stubs: { ElIcon: true } },
    })
    const content = wrapper.text()
    expect(content).toContain(model)
    expect(content).toContain(clientIp)
    expect(content).toContain(marketInterface)
    expect(content).toContain('客户端上行')
    expect(content).toContain('市场下行')
    const hardCore = businessCode !== 'fut_mm'
    const paths = hardCore
      ? {
          ...expectedLinks,
          'market-uplink-main': { d: 'M350 272 H490 V350 H642', tone: 'uplink' },
          'market-downlink-main': { d: 'M642 380 H548 V299 H350', tone: 'downlink' },
          'market-downlink-slnic-2': { d: 'M548 299 V548 H350', tone: 'downlink' },
        }
      : expectedLinks
    for (const [name, expected] of Object.entries(paths)) {
      const link = wrapper.get(`[data-link="${name}"]`)
      expect(link.attributes('d')).toBe(expected.d)
      expect(link.classes()).toContain(expected.tone)
      expect(link.attributes('marker-end')).toContain(`wiring-${expected.tone}-`)
    }
    expect(wrapper.findAll('.hard-core-interface-row')).toHaveLength(hardCore ? 4 : 0)
    if (hardCore) {
      expect(wrapper.findAll('.hard-core-interface-row rect').map(rect => [
        rect.attributes('y'),
        rect.attributes('height'),
      ])).toEqual([
        ['194', '58'], ['252', '58'], ['310', '58'], ['368', '58'],
      ])
      expect(wrapper.get('[data-link="market-uplink-main"]').attributes('d')).toContain('M350 272')
      expect(wrapper.get('[data-link="market-downlink-main"]').attributes('d')).toContain('H350')
    }
  })

  it('edits all four integrated names in place and keeps read-only diagrams static', async () => {
    const snapshot = buildWiringSnapshot(
      'rem_two',
      { id: 1, name: 'REM-01', host: '10.1.51.8', trade_ip: '180.1.1.101' },
      { id: 3, name: 'Market-01', host: '10.1.51.101' },
      { id: 2, name: 'SLNIC-01', host: '10.1.51.210' },
    )
    const editable = mount(WiringTopologyDiagram, {
      props: { snapshot, editable: true },
      global: { stubs: { ElIcon: true } },
    })
    const inputs = editable.findAll('.interface-name input')
    expect(inputs).toHaveLength(4)
    await inputs[0].setValue('client-custom')
    await inputs[2].setValue('aux-custom')
    expect(editable.emitted('interface-name-change')).toEqual([
      ['client', 'client-custom', undefined],
      ['auxiliary', 'aux-custom', 0],
    ])

    const readonly = mount(WiringTopologyDiagram, {
      props: { snapshot },
      global: { stubs: { ElIcon: true } },
    })
    expect(readonly.findAll('.interface-name input')).toHaveLength(0)
    expect(readonly.text()).toContain('1(mac0)')
    expect(readonly.text()).toContain('4(mac3)')
  })

  it('renders an actionable empty state', () => {
    const wrapper = mount(WiringTopologyDiagram, {
      props: { snapshot: null },
      global: { stubs: { ElIcon: true } },
    })
    expect(wrapper.text()).toContain('接线图尚未就绪')
    expect(wrapper.text()).toContain('请先绑定 REM、模拟市场与 SLNIC')
  })
})
