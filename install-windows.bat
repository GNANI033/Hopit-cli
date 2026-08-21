@echo off
:: lazyctl Windows installer -- just launches the PowerShell setup script.
:: Run this once; everything else is handled automatically.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1"
pause
