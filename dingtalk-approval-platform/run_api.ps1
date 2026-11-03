$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Error '未找到 Python 独立环境，请先运行启动脚本。'
    exit 1
}

$env:APP_DATA_DIR = Join-Path $projectRoot 'data'
$env:PYTHONUNBUFFERED = '1'
Set-Location -LiteralPath $projectRoot
& $pythonPath -m uvicorn app.main:app --host 127.0.0.1 --port 8011
exit $LASTEXITCODE
