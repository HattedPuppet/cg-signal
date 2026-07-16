param([switch]$Quiet)

$ErrorActionPreference = "Stop"
$pidFile = Join-Path $PSScriptRoot ".cache\server.pid"
$stopped = $false

function Show-CGSignalMessage([string]$message) {
    if ($Quiet) {
        return
    }

    try {
        $shell = New-Object -ComObject WScript.Shell
        $null = $shell.Popup($message, 5, "CG Signal", 64)
    }
    catch {
        # Stopping the server does not depend on Windows Script Host.
    }
}

if (Test-Path -LiteralPath $pidFile) {
    $serverProcessId = 0
    $rawProcessId = (Get-Content -LiteralPath $pidFile -Raw).Trim()

    if ([int]::TryParse($rawProcessId, [ref]$serverProcessId)) {
        $process = Get-Process -Id $serverProcessId -ErrorAction SilentlyContinue
        $listener = Get-NetTCPConnection -LocalPort 4310 -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.OwningProcess -eq $serverProcessId } |
            Select-Object -First 1

        if ($process -and $process.ProcessName -like "python*" -and $listener) {
            Stop-Process -Id $serverProcessId -ErrorAction Stop
            $null = $process.WaitForExit(5000)
            $stopped = $true
        }
    }

    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

if ($stopped) {
    Show-CGSignalMessage "CG Signal has stopped."
}
else {
    Show-CGSignalMessage "CG Signal is not currently running."
}
