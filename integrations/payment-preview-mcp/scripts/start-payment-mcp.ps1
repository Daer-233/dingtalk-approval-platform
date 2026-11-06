param([switch]$Initialize)
$ErrorActionPreference = 'Stop'
$paymentRoot = Split-Path -Parent $PSScriptRoot
$paymentPython = Join-Path $paymentRoot 'data\payment-mcp\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $paymentPython)) { throw 'Prepare the payment MCP Python environment first.' }
Push-Location -LiteralPath $paymentRoot
try {
    if ($Initialize) {
        & $paymentPython scripts/payment-instance.py init
        if ($LASTEXITCODE -ne 0) { throw 'Payment initialization failed.' }
    }
    if (Get-NetTCPConnection -LocalPort 8070 -State Listen -ErrorAction SilentlyContinue) {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8070/health' -TimeoutSec 10
        if ($health.service -ne 'payment-preview-mcp') { throw '8070 belongs to another service.' }
        Write-Output 'Payment preview MCP already running.'
        exit
    }
    $logs = Join-Path $paymentRoot 'data\payments\runtime'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $pidFile = Join-Path $logs 'server.pid'
    if (Test-Path -LiteralPath $pidFile) {
        $recorded = [int](Get-Content -LiteralPath $pidFile -Raw)
        $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $recorded"
        if ($existing) {
            if ($existing.CommandLine -notmatch 'payment-instance\.py["'']?\s+serve(?:\s|$)') { throw 'Recorded PID identity mismatch.' }
            Write-Output 'Payment MCP is initializing.'
            exit
        }
    }
    $child = Start-Process -FilePath $paymentPython -ArgumentList @('scripts/payment-instance.py','serve') -WorkingDirectory $paymentRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs 'server.out.log') -RedirectStandardError (Join-Path $logs 'server.err.log')
    $child.Id | Set-Content -LiteralPath $pidFile
    Write-Output 'Payment preview MCP starting on 127.0.0.1:8070; check /health for enabled state.'
} finally { Pop-Location }
