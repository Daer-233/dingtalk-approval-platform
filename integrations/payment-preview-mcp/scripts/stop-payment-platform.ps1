$ErrorActionPreference = 'Stop'
$paymentRoot = Split-Path -Parent $PSScriptRoot
$record = Join-Path $paymentRoot 'data\payments\runtime\platform-processes.json'
if (-not (Test-Path -LiteralPath $record)) { Write-Output 'No platform processes recorded by this launcher.'; exit }
$entries = (& node -e "console.log(require('node:fs').readFileSync(process.argv[1],'utf8'))" $record) | ConvertFrom-Json
foreach ($entry in $entries) {
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$entry.pid)"
    if (-not $candidate) { continue }
    if ($candidate.ExecutablePath -ine ([string]$entry.file).Replace('/','\')) { throw 'Platform process identity changed; refusing to stop it.' }
    if (($entry.name -eq 'platform-backend' -and $candidate.CommandLine -notmatch 'uvicorn\s+app\.main:app.*--port\s+8000') -or
        ($entry.name -eq 'platform-web' -and $candidate.CommandLine -notmatch 'vite\.js.*--port\s+5173')) { throw 'Platform process arguments changed.' }
    Stop-Process -Id ([int]$entry.pid)
}
Remove-Item -LiteralPath $record
Write-Output 'Only platform processes recorded by the payment launcher were stopped.'
