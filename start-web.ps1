[CmdletBinding()]
param(
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$VenvRoot = Join-Path $ProjectRoot ".venv"
$Python = Join-Path $VenvRoot "Scripts\python.exe"
$ApiUrl = "http://127.0.0.1:8000"
$WebUrl = "http://127.0.0.1:5173"
$ApiProcess = $null
$WebProcess = $null
$ExitCode = 0

function Write-Step {
    param([string]$Message)
    Write-Host "[OpenSLT] $Message" -ForegroundColor Cyan
}

function Invoke-External {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$FailureMessage
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Get-SupportedPython {
    $Candidates = @()
    $PyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($PyLauncher) {
        $Candidates += [pscustomobject]@{ File = $PyLauncher.Source; Prefix = @("-3.12") }
        $Candidates += [pscustomobject]@{ File = $PyLauncher.Source; Prefix = @("-3") }
    }

    $PythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($PythonCommand) {
        $Candidates += [pscustomobject]@{ File = $PythonCommand.Source; Prefix = @() }
    }

    foreach ($Candidate in $Candidates) {
        $Prefix = $Candidate.Prefix
        $VersionText = & $Candidate.File @Prefix -c "import platform; print(platform.python_version())" 2>$null
        if ($LASTEXITCODE -eq 0 -and $VersionText) {
            $Version = [version]($VersionText | Select-Object -Last 1)
            if ($Version -ge [version]"3.12") {
                return $Candidate
            }
        }
    }

    throw "Python 3.12 or newer was not found. Install Python and run start-web.cmd again."
}

function Test-PortOpen {
    param([string]$Address, [int]$Port)

    $Client = New-Object System.Net.Sockets.TcpClient
    try {
        $Connect = $Client.ConnectAsync($Address, $Port)
        if (-not $Connect.Wait(300)) {
            return $false
        }
        return $Client.Connected
    }
    catch {
        return $false
    }
    finally {
        $Client.Dispose()
    }
}

function Test-ApiReady {
    try {
        $Response = Invoke-RestMethod -Uri "$ApiUrl/health" -TimeoutSec 2
        return $Response.status -eq "ok" -and $Response.service -eq "openslt-api"
    }
    catch {
        return $false
    }
}

function Test-WebReady {
    try {
        $Response = Invoke-WebRequest -Uri $WebUrl -UseBasicParsing -TimeoutSec 2
        return $Response.StatusCode -eq 200 -and $Response.Content -match "OpenSLT"
    }
    catch {
        return $false
    }
}

function Wait-UntilReady {
    param(
        [scriptblock]$Probe,
        [System.Diagnostics.Process]$Process,
        [string]$ServiceName,
        [int]$TimeoutSeconds = 45
    )

    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if (& $Probe) {
            return
        }
        if ($Process -and $Process.HasExited) {
            throw "$ServiceName exited during startup (exit code $($Process.ExitCode))."
        }
        Start-Sleep -Milliseconds 250
    }
    throw "$ServiceName did not become ready within $TimeoutSeconds seconds."
}

function Stop-ProcessTree {
    param([System.Diagnostics.Process]$Process)

    if (-not $Process -or $Process.HasExited) {
        return
    }
    & taskkill.exe /PID $Process.Id /T /F 2>$null | Out-Null
}

try {
    Set-Location $ProjectRoot

    if (-not (Test-Path (Join-Path $ProjectRoot ".env"))) {
        Write-Step "Creating .env from .env.example..."
        Copy-Item (Join-Path $ProjectRoot ".env.example") (Join-Path $ProjectRoot ".env")
    }

    if (-not (Test-Path $Python)) {
        Write-Step "Creating the Python virtual environment..."
        $BootstrapPython = Get-SupportedPython
        $BootstrapArgs = @($BootstrapPython.Prefix) + @("-m", "venv", $VenvRoot)
        Invoke-External $BootstrapPython.File $BootstrapArgs "Failed to create the Python virtual environment."
    }

    $VenvVersion = [version](& $Python -c "import platform; print(platform.python_version())")
    if ($VenvVersion -lt [version]"3.12") {
        throw "The existing .venv uses Python $VenvVersion. OpenSLT requires Python 3.12 or newer."
    }

    $PyprojectHash = (Get-FileHash (Join-Path $ProjectRoot "pyproject.toml") -Algorithm SHA256).Hash
    $PythonStamp = Join-Path $VenvRoot ".openslt-pyproject.sha256"
    $InstalledPythonHash = if (Test-Path $PythonStamp) { (Get-Content $PythonStamp -Raw).Trim() } else { "" }
    $PythonImportsWork = $false
    if ($InstalledPythonHash -eq $PyprojectHash) {
        & $Python -c "import alembic, fastapi, uvicorn, app" 2>$null
        $PythonImportsWork = $LASTEXITCODE -eq 0
    }
    if (-not $PythonImportsWork) {
        Write-Step "Installing backend dependencies..."
        Invoke-External $Python @("-m", "pip", "install", "--editable", $ProjectRoot) "Failed to install backend dependencies."
        Set-Content -Path $PythonStamp -Value $PyprojectHash -Encoding Ascii
    }

    $Node = Get-Command "node.exe" -ErrorAction SilentlyContinue
    $Npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if (-not $Node -or -not $Npm) {
        throw "Node.js 20+ and npm 10+ are required. Install Node.js and run start-web.cmd again."
    }
    $NodeVersion = [version]((& $Node.Source --version).TrimStart("v"))
    $NpmVersion = [version](& $Npm.Source --version)
    if ($NodeVersion -lt [version]"20.0" -or $NpmVersion -lt [version]"10.0") {
        throw "Found Node.js $NodeVersion and npm $NpmVersion; OpenSLT requires Node.js 20+ and npm 10+."
    }

    $PackageLock = Join-Path $FrontendRoot "package-lock.json"
    $PackageHash = (Get-FileHash $PackageLock -Algorithm SHA256).Hash
    $NodeModules = Join-Path $FrontendRoot "node_modules"
    $NodeStamp = Join-Path $NodeModules ".openslt-package-lock.sha256"
    $InstalledNodeHash = if (Test-Path $NodeStamp) { (Get-Content $NodeStamp -Raw).Trim() } else { "" }
    $ViteEntry = Join-Path $NodeModules "vite\bin\vite.js"
    $NodeModulesAreValid = $false
    if (-not $InstalledNodeHash -and (Test-Path $ViteEntry)) {
        & $Npm.Source "ls" "--prefix" $FrontendRoot "--depth=0" "--silent" 2>$null | Out-Null
        $NodeModulesAreValid = $LASTEXITCODE -eq 0
        if ($NodeModulesAreValid) {
            Set-Content -Path $NodeStamp -Value $PackageHash -Encoding Ascii
            $InstalledNodeHash = $PackageHash
        }
    }
    if ($InstalledNodeHash -ne $PackageHash -or -not (Test-Path $ViteEntry)) {
        Write-Step "Installing frontend dependencies..."
        Invoke-External $Npm.Source @(
            "ci", "--prefix", $FrontendRoot, "--no-audit", "--no-fund"
        ) "Failed to install frontend dependencies."
        Set-Content -Path $NodeStamp -Value $PackageHash -Encoding Ascii
    }

    Write-Step "Applying database migrations..."
    Invoke-External $Python @("-m", "alembic", "upgrade", "head") "Database migration failed."

    $ReuseApi = Test-PortOpen "127.0.0.1" 8000
    if ($ReuseApi -and -not (Test-ApiReady)) {
        throw "Port 8000 is already used by another application."
    }
    $ReuseWeb = Test-PortOpen "127.0.0.1" 5173
    if ($ReuseWeb -and -not (Test-WebReady)) {
        throw "Port 5173 is already used by another application."
    }

    if (-not $ReuseApi) {
        Write-Step "Starting the API on $ApiUrl..."
        $ApiProcess = Start-Process -FilePath $Python -ArgumentList @(
            "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
            "--host", "127.0.0.1", "--port", "8000"
        ) -WorkingDirectory $ProjectRoot -NoNewWindow -PassThru
        Wait-UntilReady ${function:Test-ApiReady} $ApiProcess "OpenSLT API"
    }
    else {
        Write-Step "Reusing the OpenSLT API already running on port 8000."
    }

    if (-not $ReuseWeb) {
        Write-Step "Starting the web client on $WebUrl..."
        $WebProcess = Start-Process -FilePath $Node.Source -ArgumentList @(
            $ViteEntry, "--host", "127.0.0.1"
        ) -WorkingDirectory $FrontendRoot -NoNewWindow -PassThru
        Wait-UntilReady ${function:Test-WebReady} $WebProcess "OpenSLT web client"
    }
    else {
        Write-Step "Reusing the OpenSLT web client already running on port 5173."
    }

    Write-Host ""
    Write-Host "OpenSLT web is ready: $WebUrl" -ForegroundColor Green
    Write-Host "API documentation: $ApiUrl/docs"
    Write-Host "Press Ctrl+C to stop services started by this script."
    Write-Host ""

    if (-not $NoBrowser) {
        Start-Process $WebUrl
    }

    while ($true) {
        if ($ApiProcess -and $ApiProcess.HasExited) {
            throw "OpenSLT API stopped unexpectedly (exit code $($ApiProcess.ExitCode))."
        }
        if ($WebProcess -and $WebProcess.HasExited) {
            throw "OpenSLT web client stopped unexpectedly (exit code $($WebProcess.ExitCode))."
        }
        Start-Sleep -Seconds 1
    }
}
catch {
    $ExitCode = 1
    Write-Host ""
    Write-Host "[OpenSLT] $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    if ($WebProcess -or $ApiProcess) {
        Write-Step "Stopping services..."
    }
    Stop-ProcessTree $WebProcess
    Stop-ProcessTree $ApiProcess
}

exit $ExitCode
