import { existsSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = path.resolve(frontendRoot, '..')
const exporter = path.join(repositoryRoot, 'backend', 'scripts', 'export_openapi.py')
const schema = path.join(frontendRoot, 'openapi.json')
const output = path.join(frontendRoot, 'src', 'types', 'api.generated.ts')
const cli = path.join(frontendRoot, 'node_modules', 'openapi-typescript', 'bin', 'cli.js')

const localPythonCandidates = process.platform === 'win32'
  ? [path.join(repositoryRoot, '.venv', 'Scripts', 'python.exe')]
  : [path.join(repositoryRoot, '.venv', 'bin', 'python')]
const pythonCandidates = [process.env.PYTHON, ...localPythonCandidates, 'python3', 'python'].filter(Boolean)

let exported = false
for (const python of pythonCandidates) {
  if (path.isAbsolute(python) && !existsSync(python)) continue
  const result = spawnSync(python, [exporter, '--output', schema], {
    cwd: repositoryRoot,
    stdio: 'inherit',
  })
  if (result.error?.code === 'ENOENT') continue
  if (result.status !== 0) process.exit(result.status ?? 1)
  exported = true
  break
}

if (!exported) {
  console.error('Unable to find a Python interpreter with the OpenSLT backend dependencies installed.')
  process.exit(1)
}

const generated = spawnSync(process.execPath, [cli, schema, '--empty-objects-unknown', '--output', output], {
  cwd: frontendRoot,
  stdio: 'inherit',
})
process.exit(generated.status ?? 1)
