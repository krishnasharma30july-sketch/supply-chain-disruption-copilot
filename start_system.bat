@echo off
rem Double-click to start the whole system (also after a reboot). Options: -Build  -LocalLlm  -NoBrowser
cd /d "%~dp0"
if "%SystemRoot%"=="" set "SystemRoot=C:\Windows"
rem Call PowerShell by its full path: on some machines System32 is missing from PATH and plain "powershell" is not found.
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_system.ps1" %*
echo.
pause
