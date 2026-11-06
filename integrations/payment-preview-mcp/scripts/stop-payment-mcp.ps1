$ErrorActionPreference = 'Stop'
$paymentRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $paymentRoot 'data\payments\runtime\server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output 'No payment PID recorded.'; exit }
$paymentId = [int](Get-Content -LiteralPath $pidFile -Raw)
$paymentProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $paymentId"
if ($paymentProcess -and $paymentProcess.CommandLine -notmatch 'payment-instance\.py["'']?\s+serve(?:\s|$)') { throw 'Recorded PID belongs to another process.' }
if ($paymentProcess) { Stop-Process -Id $paymentId }
Remove-Item -LiteralPath $pidFile
Write-Output 'Payment MCP stopped.'
