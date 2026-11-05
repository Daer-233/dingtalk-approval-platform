@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing_python
".venv\Scripts\python.exe" -X utf8 "scripts\diagnose_modern_readonly.py"
set "RESULT=%ERRORLEVEL%"
echo.
echo Read-only diagnostics finished. No real approval or attachment upload.
pause
exit /b %RESULT%
:missing_python
echo Local Python environment is missing. No requests were sent.
pause
exit /b 1
