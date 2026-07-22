param(
    [string]$Python = "python",
    [switch]$ReuseEnvironment,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root

if (-not $SkipFrontend) {
    $pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
    if (-not $pnpm) {
        throw "pnpm is missing. Install Node.js/pnpm or use -SkipFrontend when frontend/dist already exists."
    }
    & $pnpm.Source --dir frontend install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed" }
    & $pnpm.Source --dir frontend build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
}

if (-not (Test-Path "frontend\dist\index.html")) {
    throw "frontend/dist/index.html is missing; cannot build portable package."
}

if ($ReuseEnvironment) {
    $pythonExe = (Get-Command $Python -ErrorAction Stop).Source
} else {
    $venv = Join-Path $root ".venv-portable"
    if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
        & $Python -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw "Failed to create portable build environment" }
    }
    $pythonExe = Join-Path $venv "Scripts\python.exe"
    & $pythonExe -m pip install --upgrade pip
    & $pythonExe -m pip install -r "deploy\portable\requirements.txt"
    if ($LASTEXITCODE -ne 0) { throw "Failed to install portable build dependencies" }
}

& $pythonExe -m PyInstaller --noconfirm --clean `
    --distpath "release" `
    --workpath "build\portable" `
    "deploy\portable\OpenSLT.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

$portableDir = Join-Path $root "release\OpenSLT-Portable"
Copy-Item "deploy\portable\README-PORTABLE.txt" $portableDir -Force
Copy-Item ".env.example" (Join-Path $portableDir "OpenSLT.env.example") -Force

$zipPath = Join-Path $root "release\OpenSLT-Portable-windows-x64.zip"
if (Test-Path $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Start-Sleep -Seconds 2
& tar.exe -a -c -f $zipPath -C $portableDir `
    --exclude=./data `
    --exclude=./logs `
    --exclude=./.env `
    --exclude=./*.log `
    .
if ($LASTEXITCODE -ne 0) { throw "Portable ZIP creation failed" }

$hash = (Get-FileHash -Algorithm SHA256 $zipPath).Hash
Write-Host ""
Write-Host "Portable package created: $zipPath" -ForegroundColor Green
Write-Host "SHA-256: $hash"
