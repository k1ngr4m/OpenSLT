import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import WiringTopologyDiagram from '@/components/WiringTopologyDiagram.vue'
import { buildWiringSnapshot } from '@/utils/wiring'

describe('WiringTopologyDiagram', () => {
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
    expect(wrapper.findAll('path.uplink')).toHaveLength(4)
    expect(wrapper.findAll('path.downlink')).toHaveLength(4)
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
