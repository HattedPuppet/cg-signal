param([switch]$NoOpen)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$serverScript = Join-Path $projectRoot "server.py"
$serverPidFile = Join-Path $projectRoot ".cache\server.pid"
$dashboardUrl = "http://127.0.0.1:4310"
$healthUrl = "$dashboardUrl/api/health"

function Get-CGSignalHealth {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
        if ($response.ok -eq $true -and $response.service -eq "CG Signal") {
            return $response
        }
    }
    catch {
        # No compatible CG Signal server is listening yet.
    }
    return $null
}

function Test-CGSignalHealth {
    $response = Get-CGSignalHealth
    return ($null -ne $response -and $response.source_revision -eq $serverSourceRevision)
}

function Stop-StaleCGSignal([object]$health) {
    if (-not $health.pid -or -not (Test-Path -LiteralPath $serverPidFile)) {
        return $false
    }

    $candidateProcessId = 0
    $rawProcessId = (Get-Content -Raw -LiteralPath $serverPidFile).Trim()
    if (-not [int]::TryParse($rawProcessId, [ref]$candidateProcessId) -or $candidateProcessId -le 0) {
        return $false
    }
    if ([int]$health.pid -ne $candidateProcessId) {
        return $false
    }

    try {
        $process = Get-Process -Id $candidateProcessId -ErrorAction Stop
        if ($process.ProcessName -notin @("python", "python3", "py")) {
            return $false
        }
        Stop-Process -Id $candidateProcessId -ErrorAction Stop
        $null = $process.WaitForExit(5000)
        return $process.HasExited
    }
    catch {
        return $false
    }
}

function Show-CGSignalMessage([string]$message, [int]$icon = 64) {
    try {
        $shell = New-Object -ComObject WScript.Shell
        $null = $shell.Popup($message, 8, "CG Signal", $icon)
    }
    catch {
        # The launcher can still fail cleanly if Windows Script Host is disabled.
    }
}

$mutex = New-Object System.Threading.Mutex($false, "Local\CGSignalLauncher")
$hasMutex = $false

try {
    try {
        $hasMutex = $mutex.WaitOne(5000)
    }
    catch [System.Threading.AbandonedMutexException] {
        $hasMutex = $true
    }

    if (-not $hasMutex) {
        exit 0
    }

    $pythonPath = $null
    $pythonArguments = @()
    $codexPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

    if (Test-Path -LiteralPath $codexPython) {
        $pythonPath = $codexPython
    }
    else {
        $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($pyLauncher) {
            $pythonPath = $pyLauncher.Source
            $pythonArguments += "-3"
        }
        else {
            $python = Get-Command python -ErrorAction SilentlyContinue
            if ($python) {
                $pythonPath = $python.Source
            }
        }
    }

    if (-not $pythonPath) {
        Show-CGSignalMessage "CG Signal needs Python 3. Install it, then open CG Signal again." 16
        exit 1
    }

    $revisionArguments = @($pythonArguments)
    $revisionArguments += @($serverScript, "--print-source-revision")
    try {
        Push-Location $projectRoot
        $serverSourceRevision = (& $pythonPath @revisionArguments).Trim()
    }
    finally {
        Pop-Location
    }
    if (-not $serverSourceRevision -or $serverSourceRevision -notmatch '^[0-9a-f]{64}$') {
        Show-CGSignalMessage "CG Signal could not determine its source revision. Close it, then open CG Signal again." 16
        exit 1
    }

    $health = Get-CGSignalHealth
    if ($null -ne $health) {
        if ($health.source_revision -eq $serverSourceRevision) {
            if (-not $NoOpen) {
                Start-Process $dashboardUrl
            }
            exit 0
        }
        if (-not (Stop-StaleCGSignal $health)) {
            Show-CGSignalMessage "CG Signal is running older code and could not be restarted safely. Close it, then open CG Signal again." 16
            exit 1
        }
    }

    $pythonArguments += @($serverScript, "--no-browser")
    $serverProcess = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList $pythonArguments `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -PassThru

    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if (Test-CGSignalHealth) {
            if (-not $NoOpen) {
                Start-Process $dashboardUrl
            }
            exit 0
        }

        if ($serverProcess.HasExited) {
            break
        }

        Start-Sleep -Milliseconds 250
    }

    Show-CGSignalMessage "CG Signal could not start. Port 4310 may be in use by another application." 16
    exit 1
}
finally {
    if ($hasMutex) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
