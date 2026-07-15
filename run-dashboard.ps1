param([switch]$NoOpen)

& (Join-Path $PSScriptRoot "launch-dashboard.ps1") -NoOpen:$NoOpen
exit $LASTEXITCODE
