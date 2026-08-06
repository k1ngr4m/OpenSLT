export const DEFAULT_SLNIC_COMMANDS: Record<string, readonly string[]> = {
  slnic_start_capture: ['./start_slnic_dump.sh'],
  slnic_stop_capture: ['./stop_slnic_dump.sh'],
  slnic_merge_capture: ['./pcap_merge_tool slnic*'],
}

export const DEFAULT_EDITCAP_PATH = 'D:\\Program Files\\Wireshark\\editcap.exe'

interface UncResource {
  host?: unknown
  remote_path?: unknown
}

function linuxHomePathToUnc(resource: UncResource): string {
  const host = String(resource.host || '').trim().replace(/^[\\/]+|[\\/]+$/g, '')
  const remotePath = String(resource.remote_path || '').trim().replace(/\/+$/g, '')
  if (!host || !remotePath.startsWith('/home/')) return ''
  const relative = remotePath.slice('/home/'.length)
  if (!relative || relative.split('/').some(part => !part || part === '.' || part === '..')) return ''
  return `\\\\${host}\\${relative.replace(/\//g, '\\')}`
}

export function buildWindowsEditcapCommand(
  editcapPath: string,
  slnicResource: UncResource,
  parserResource: UncResource,
): string {
  const slnicRoot = linuxHomePathToUnc(slnicResource)
  const parserRoot = linuxHomePathToUnc(parserResource)
  if (!editcapPath || !slnicRoot || !parserRoot) return ''
  return `"${editcapPath}" -F pcapng "${slnicRoot}\\tcpdump\\merge_pcap.pcap" "${parserRoot}\\merge_pcap.pcapng"`
}

export function defaultSlnicCommands(nodeType: string): string[] {
  return [...(DEFAULT_SLNIC_COMMANDS[nodeType] || [])]
}

export function slnicCommandText(nodeType: string, commands: unknown): string {
  if (!Array.isArray(commands)) return defaultSlnicCommands(nodeType).join('\n')
  return commands.filter((command): command is string => typeof command === 'string').join('\n')
}
