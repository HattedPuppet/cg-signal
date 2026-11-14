<#
.SYNOPSIS
Imports daily CG Signal Markdown from a synced Windows inbox.
.DESCRIPTION
Pass -PullGit for a dedicated clone of a private notes repository. Pass
-RegisterTask after a successful manual run to repeat at logon and every
15 minutes. The task runs as the current Windows user and uses that user's
existing Git credentials.
.EXAMPLE
.\sync_tech_notes.ps1 -Inbox "$HOME\CG-Tech-Notes-Inbox" -Archive "$HOME\CG-Tech-Notes" -PullGit
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Inbox,
    [Parameter(Mandatory)][string]$Archive,
    [string]$ImporterPath = (Join-Path $PSScriptRoot 'import_tech_notes.py'),
    [string]$Python = 'python.exe',
    [switch]$PullGit,
    [switch]$RegisterTask
)

$ErrorActionPreference = 'Stop'
$Inbox = (Resolve-Path -LiteralPath $Inbox).ProviderPath
$Archive = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Archive)
$ImporterPath = (Resolve-Path -LiteralPath $ImporterPath).ProviderPath
$Python = (Get-Command $Python -CommandType Application | Select-Object -First 1).Source

if ($RegisterTask) {
    $taskName = 'CG Signal Technical Notes'
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        throw "Task '$taskName' already exists. Remove it in Task Scheduler before reinstalling."
    }

    $shellName = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh.exe' } else { 'powershell.exe' }
    $shell = Join-Path $PSHOME $shellName
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -Inbox "{1}" -Archive "{2}" -ImporterPath "{3}" -Python "{4}"' -f `
        $PSCommandPath, $Inbox, $Archive, $ImporterPath, $Python
    if ($PullGit) {
        $arguments += ' -PullGit'
    }
    $action = New-ScheduledTaskAction -Execute $shell -Argument $arguments
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $user
    $repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(15) `
        -RepetitionInterval (New-TimeSpan -Minutes 15)
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($logon, $repeat) `
        -Principal $principal -Settings $settings
    return
}

if ($PullGit) {
    $insideWorkTree = & git -C $Inbox rev-parse --is-inside-work-tree
    if ($LASTEXITCODE -ne 0 -or $insideWorkTree -ne 'true') {
        throw 'Git inbox must be the root of its own checkout.'
    }
    $prefix = & git -C $Inbox rev-parse --show-prefix
    if ($LASTEXITCODE -ne 0 -or $prefix) {
        throw 'Git inbox must be the root of its own checkout.'
    }

    $previousTerminalPrompt = $env:GIT_TERMINAL_PROMPT
    try {
        $env:GIT_TERMINAL_PROMPT = '0'
        & git -C $Inbox -c credential.interactive=false pull --ff-only
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    finally {
        $env:GIT_TERMINAL_PROMPT = $previousTerminalPrompt
    }
}

& $Python $ImporterPath --inbox $Inbox --out $Archive
exit $LASTEXITCODE
