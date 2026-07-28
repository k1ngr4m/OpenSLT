export interface WiringInterface {
  name: string
  ip_address: string
}

export interface RemWiringProfile {
  client_switch_label: string
  market_switch_label: string
  client_interface: WiringInterface
  market_interface: WiringInterface
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
  slnic: WiringResource
  slnic_ports: Array<{ port: number; side: 'client' | 'market'; direction: 'uplink' | 'downlink'; label: string }>
}

const PORTS: WiringSnapshot['slnic_ports'] = [
  { port: 0, side: 'client', direction: 'uplink', label: '客户端上行' },
  { port: 1, side: 'market', direction: 'uplink', label: '市场上行' },
  { port: 2, side: 'market', direction: 'downlink', label: '市场下行' },
  { port: 3, side: 'client', direction: 'downlink', label: '客户端下行' },
]

export const WIRING_PRESETS: Record<string, RemWiringProfile> = {
  fut_mm: {
    client_switch_label: '180段交换机（客户端）',
    market_switch_label: '51段交换机（市场端）',
    client_interface: { name: 'enp101s0d1', ip_address: '180.1.1.101' },
    market_interface: { name: 'enp23s0', ip_address: '10.1.51.101' },
  },
  rem_two: {
    client_switch_label: '51段交换机（客户端）',
    market_switch_label: '51段交换机（市场端）',
    client_interface: { name: '1(mac0)', ip_address: '10.1.51.242' },
    market_interface: { name: '2(mac1)', ip_address: '10.1.51.116' },
  },
  rem_two_mm: {
    client_switch_label: '51段交换机（客户端）',
    market_switch_label: '51段交换机（市场端）',
    client_interface: { name: '1(mac0)', ip_address: '10.1.51.146' },
    market_interface: { name: '2(mac1)', ip_address: '10.1.51.198' },
  },
}

const text = (value: unknown) => typeof value === 'string' ? value.trim() : ''

export function fillWiringPreset(value: Partial<RemWiringProfile> | null | undefined, businessCode: string): RemWiringProfile {
  const preset = WIRING_PRESETS[businessCode] || WIRING_PRESETS.fut_mm
  return {
    client_switch_label: text(value?.client_switch_label) || preset.client_switch_label,
    market_switch_label: text(value?.market_switch_label) || preset.market_switch_label,
    client_interface: {
      name: text(value?.client_interface?.name) || preset.client_interface.name,
      ip_address: text(value?.client_interface?.ip_address) || preset.client_interface.ip_address,
    },
    market_interface: {
      name: text(value?.market_interface?.name) || preset.market_interface.name,
      ip_address: text(value?.market_interface?.ip_address) || preset.market_interface.ip_address,
    },
  }
}

export function wiringProfileComplete(value: unknown): value is RemWiringProfile {
  const profile = value as RemWiringProfile | null
  return Boolean(
    text(profile?.client_switch_label)
    && text(profile?.market_switch_label)
    && text(profile?.client_interface?.name)
    && text(profile?.client_interface?.ip_address)
    && text(profile?.market_interface?.name)
    && text(profile?.market_interface?.ip_address),
  )
}

export function buildWiringSnapshot(businessCode: string, rem: any, slnic: any): WiringSnapshot | null {
  if (!rem || !slnic || !wiringProfileComplete(rem.wiring_profile)) return null
  const topology = businessCode === 'rem_two'
    ? ['hard_core_nf11', 'NF11'] as const
    : businessCode === 'rem_two_mm'
      ? ['hard_core_mg11', 'MG11'] as const
      : ['soft_core', '软核'] as const
  return {
    schema_version: 1,
    business_code: businessCode,
    topology_kind: topology[0],
    model_label: topology[1],
    client_switch_label: rem.wiring_profile.client_switch_label,
    market_switch_label: rem.wiring_profile.market_switch_label,
    client_interface: { ...rem.wiring_profile.client_interface },
    market_interface: { ...rem.wiring_profile.market_interface },
    auxiliary_interfaces: businessCode === 'fut_mm' ? [] : ['3(mac2)', '4(mac3)'],
    rem: { id: rem.id, name: rem.name, host: rem.host },
    slnic: { id: slnic.id, name: slnic.name, host: slnic.host },
    slnic_ports: PORTS.map(item => ({ ...item })),
  }
}
