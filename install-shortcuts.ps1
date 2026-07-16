param([switch]$Quiet)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$programsFolder = [Environment]::GetFolderPath("Programs")
$windowsScriptHostPath = Join-Path $env:SystemRoot "System32\wscript.exe"
$shell = New-Object -ComObject WScript.Shell

function New-CGSignalShortcut {
    param(
        [string]$Name,
        [string]$ScriptName,
        [string]$Description,
        [int]$IconIndex
    )

    $shortcutPath = Join-Path $programsFolder "$Name.lnk"
    $scriptPath = Join-Path $projectRoot $ScriptName
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $windowsScriptHostPath
    $shortcut.Arguments = "//B //Nologo `"$scriptPath`""
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.Description = $Description
    $customIcon = Join-Path $projectRoot "static\favicon.ico"
    if ($Name -eq "CG Signal" -and (Test-Path -LiteralPath $customIcon)) {
        $shortcut.IconLocation = "$customIcon,0"
    }
    else {
        $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,$IconIndex"
    }
    $shortcut.Save()
}

New-CGSignalShortcut `
    -Name "CG Signal" `
    -ScriptName "launch-dashboard.vbs" `
    -Description "Open the local CG and game-development news dashboard" `
    -IconIndex 220

New-CGSignalShortcut `
    -Name "CG Signal - Stop" `
    -ScriptName "stop-dashboard.vbs" `
    -Description "Stop the local CG Signal dashboard" `
    -IconIndex 131

$pinnedTaskbarFolder = Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"
$pinnedLaunchShortcut = Join-Path $pinnedTaskbarFolder "CG Signal.lnk"
$startMenuLaunchShortcut = Join-Path $programsFolder "CG Signal.lnk"
if (Test-Path -LiteralPath $pinnedLaunchShortcut) {
    Copy-Item -LiteralPath $startMenuLaunchShortcut -Destination $pinnedLaunchShortcut -Force
}

if (-not $Quiet) {
    $null = $shell.Popup("CG Signal shortcuts were added to the Start menu.", 5, "CG Signal", 64)
}
