@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register_wps_automation.ps1"
echo.
pause
endlocal
