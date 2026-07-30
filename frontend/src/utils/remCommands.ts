export const DEFAULT_REM_STARTUP_COMMANDS = [
  './stop_rem.sh',
  './makeneat.sh',
  './start_rem_all.sh',
] as const

export function normalizeRemStartupCommands(value: string | string[]): string[] {
  const sources = Array.isArray(value) ? value : [value]
  return sources.flatMap(source => source.split(/\r?\n/).map(line => line.trim()).filter(Boolean))
}

export function remStartupCommandText(commands: unknown): string {
  if (!Array.isArray(commands)) return DEFAULT_REM_STARTUP_COMMANDS.join('\n')
  return commands.filter((command): command is string => typeof command === 'string').join('\n')
}
