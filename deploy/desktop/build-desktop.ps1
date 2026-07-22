param(
    [string]$Python = "python",
    [switch]$ReuseEnvironment
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root

if ($ReuseEnvironment) {
    $pythonExe = (Get-Command $Python -ErrorAction Stop).Source
} else {
    $venv = Join-Path $root ".venv-desktop"
    if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
        & $Python -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw "Failed to create desktop build environment" }
    }
    $pythonExe = Join-Path $venv "Scripts\python.exe"
    & $pythonExe -m pip install --upgrade pip
    & $pythonExe -m pip install -e ".[desktop]"
    if ($LASTEXITCODE -ne 0) { throw "Failed to install desktop build dependencies" }
}

# PyInstaller resolves transitive DLLs through PATH. Conda distributions often
# expose incompatible ICU/OpenSSL DLLs there, so keep them out of this build.
$env:PATH = (($env:PATH -split ';') | Where-Object {
    $_ -and $_ -notmatch '(?i)(anaconda|miniconda|conda)'
}) -join ';'

& $pythonExe -m PyInstaller --noconfirm --clean --distpath "release" --workpath "build\desktop" "deploy\desktop\OpenSLT-Desktop.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller desktop build failed" }

$desktopDir = Join-Path $root "release\OpenSLT-Desktop-windows-x64"
$internalDir = Join-Path $desktopDir "_internal"
foreach ($icuName in @("icuuc.dll", "icudt73.dll")) {
    if (Test-Path (Join-Path $internalDir $icuName)) {
        throw "Contaminated ICU runtime was bundled: $icuName"
    }
}
$pythonDllDir = & $pythonExe -c "import pathlib,sys; print(pathlib.Path(sys.base_prefix) / 'DLLs')"
foreach ($sslName in @("libcrypto-3-x64.dll", "libssl-3-x64.dll")) {
    $sourceHash = (Get-FileHash (Join-Path $pythonDllDir $sslName) -Algorithm SHA256).Hash
    $packageHash = (Get-FileHash (Join-Path $internalDir $sslName) -Algorithm SHA256).Hash
    if ($sourceHash -ne $packageHash) {
        throw "Incorrect Python runtime DLL was bundled: $sslName"
    }
}
Copy-Item "deploy\desktop\README-DESKTOP.txt" (Join-Path $desktopDir "README-DESKTOP.txt") -Force
Copy-Item ".env.example" (Join-Path $desktopDir "OpenSLT.env.example") -Force
$zipPath = Join-Path $root "release\OpenSLT-Desktop-windows-x64.zip"
if (Test-Path $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
& tar.exe -a -c -f $zipPath -C $desktopDir --exclude=./data --exclude=./logs --exclude=./.env --exclude=./*.log .
if ($LASTEXITCODE -ne 0) { throw "Desktop ZIP creation failed" }

$hash = (Get-FileHash -Algorithm SHA256 $zipPath).Hash
Write-Host "Desktop package created: $zipPath" -ForegroundColor Green
Write-Host "SHA-256: $hash"
