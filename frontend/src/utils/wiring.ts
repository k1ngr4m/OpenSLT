export interface WiringInterface {
  name: string
  ip_address: string
}

export interface WiringResource {
  id?: number
  name: string
  host: string
}

export interface WiringSnapshot {
  schema_version: number
  business_code: string
  topology_kind: 'soft_core' | 'hard_core_nf11' | 'hard_core_mg11'
  model_label: string
  client_switch_label: string
  market_switch_label: string
  client_interface: WiringInterface
  market_interface: WiringInterface
  auxiliary_interfaces: string[]
  rem: WiringResource
  market?: WiringResource
  slnic: WiringResource
  slnic_ports: Array<{ port: number; side: 'client' | 'market'; direction: 'uplink' | 'downlink'; label: string }>
}

export interface WiringInterfaceNameOverrides {
  client_interface_name?: string | null
  market_interface_name?: string | null
  auxiliary_interface_names?: string[] | null
}

const PORTS: WiringSnapshot['slnic_ports'] = [
  { port: 0, side: 'client', direction: 'uplink', label: '客户端上行' },
  { port: 1, side: 'market', direction: 'uplink', label: '市场上行' },
  { port: 2, side: 'market', direction: 'downlink', label: '市场下行' },
  { port: 3, side: 'client', direction: 'downlink', label: '客户端下行' },
]

const WIRING_PRESETS: Record<string, {
  client_switch_label: string
  market_switch_label: string
  client_interface: string
  market_interface: string
  auxiliary_interfaces: string[]
}> = {
  fut_mm: {
    client_switch_label: '180段交换机（客户端）',
    market_switch_label: '51段交换机（市场端）',
    client_interface: 'enp101s0d1',
    market_interface: 'enp23s0',
    auxiliary_interfaces: [],
  },
  rem_two: {
    client_switch_label: '51段交换机（客户端）',
    market_switch_label: '51段交换机（市场端）',
    client_interface: '1(mac0)',
    market_interface: '2(mac1)',
    auxiliary_interfaces: ['3(mac2)', '4(mac3)'],
  },
  rem_two_mm: {
    client_switch_label: '51段交换机（客户端）',
    market_switch_label: '51段交换机（市场端）',
    client_interface: '1(mac0)',
    market_interface: '2(mac1)',
    auxiliary_interfaces: ['3(mac2)', '4(mac3)'],
  },
}

const ipv4 = (value: unknown) => {
  if (typeof value !== 'string') return ''
  const parts = value.trim().split('.')
  if (parts.length !== 4 || parts.some(part => !/^\d{1,3}$/.test(part) || Number(part) > 255)) return ''
  return parts.join('.')
}

export function wiringInterfaceNameDefaults(businessCode: string): {
  client_interface_name: string
  market_interface_name: string
  auxiliary_interface_names: string[]
} {
  const preset = WIRING_PRESETS[businessCode] || WIRING_PRESETS.fut_mm
  return {
    client_interface_name: preset.client_interface,
    market_interface_name: preset.market_interface,
    auxiliary_interface_names: [...preset.auxiliary_interfaces],
  }
}

export function buildWiringSnapshot(
  businessCode: string,
  rem: any,
  market: any,
  slnic: any,
  interfaceNames: WiringInterfaceNameOverrides = {},
): WiringSnapshot | null {
  const clientIp = ipv4(rem?.trade_ip)
  const marketIp = ipv4(market?.host)
  const slnicIp = ipv4(slnic?.host)
  if (!rem || !market || !slnic || !clientIp || !marketIp || !slnicIp) return null
  const topology = businessCode === 'rem_two'
    ? ['hard_core_nf11', 'NF11'] as const
    : businessCode === 'rem_two_mm'
      ? ['hard_core_mg11', 'MG11'] as const
      : ['soft_core', '软核'] as const
  const preset = WIRING_PRESETS[businessCode] || WIRING_PRESETS.fut_mm
  const defaults = wiringInterfaceNameDefaults(businessCode)
  return {
    schema_version: 2,
    business_code: businessCode,
    topology_kind: topology[0],
    model_label: topology[1],
    client_switch_label: preset.client_switch_label,
    market_switch_label: preset.market_switch_label,
    client_interface: {
      name: interfaceNames.client_interface_name ?? defaults.client_interface_name,
      ip_address: clientIp,
    },
    market_interface: {
      name: interfaceNames.market_interface_name ?? defaults.market_interface_name,
      ip_address: marketIp,
    },
    auxiliary_interfaces: interfaceNames.auxiliary_interface_names == null
      ? [...defaults.auxiliary_interface_names]
      : [...interfaceNames.auxiliary_interface_names],
    rem: { id: rem.id, name: rem.name, host: rem.host },
    market: { id: market.id, name: market.name, host: marketIp },
    slnic: { id: slnic.id, name: slnic.name, host: slnicIp },
    slnic_ports: PORTS.map(item => ({ ...item })),
  }
}
