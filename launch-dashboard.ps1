param([switch]$NoOpen)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$serverScript = Join-Path $projectRoot "server.py"
$dashboardUrl = "http://127.0.0.1:4310"
$healthUrl = "$dashboardUrl/api/health"

function Test-CGSignalHealth {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
        return ($response.ok -eq $true -and $response.service -eq "CG Signal")
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

    if (Test-CGSignalHealth) {
        if (-not $NoOpen) {
            Start-Process $dashboardUrl
        }
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
