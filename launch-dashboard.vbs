Option Explicit

Dim shell, fileSystem, projectRoot, powerShellPath, scriptPath, command, argument

Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

projectRoot = fileSystem.GetParentFolderName(WScript.ScriptFullName)
powerShellPath = shell.ExpandEnvironmentStrings("%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")
scriptPath = fileSystem.BuildPath(projectRoot, "launch-dashboard.ps1")
command = Chr(34) & powerShellPath & Chr(34) & _
    " -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & _
    Chr(34) & scriptPath & Chr(34)

For Each argument In WScript.Arguments
    command = command & " " & argument
Next

shell.Run command, 0, False
