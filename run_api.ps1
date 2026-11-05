$ErrorActionPreference='Stop'
if (Get-NetTCPConnection -State Listen -LocalPort 8012 -ErrorAction SilentlyContinue) { throw '端口 8012 已占用。' }
Set-Location -LiteralPath $PSScriptRoot
& (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') -m uvicorn app.main:app --app-dir $PSScriptRoot --host 127.0.0.1 --port 8012
exit $LASTEXITCODE
