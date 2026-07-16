@echo off
start "" /b powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0launch-dashboard.ps1"
