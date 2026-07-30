export const DEFAULT_SLNIC_COMMANDS: Record<string, readonly string[]> = {
  slnic_start_capture: ['./start_slnic_dump.sh'],
  slnic_stop_capture: ['./stop_slnic_dump.sh'],
  slnic_merge_capture: [
    './pcap_merge_tool slnic*',
    'if [ ! -f merge_pcap.pcap ] && [ -f merge_pacp.pcap ]; then mv -- merge_pacp.pcap merge_pcap.pcap; fi; test -f merge_pcap.pcap',
    './editcap merge_pcap.pcap merge_pcap.pcapng && test -f merge_pcap.pcapng',
  ],
}

export function defaultSlnicCommands(nodeType: string): string[] {
  return [...(DEFAULT_SLNIC_COMMANDS[nodeType] || [])]
}

export function slnicCommandText(nodeType: string, commands: unknown): string {
  if (!Array.isArray(commands)) return defaultSlnicCommands(nodeType).join('\n')
  return commands.filter((command): command is string => typeof command === 'string').join('\n')
}
