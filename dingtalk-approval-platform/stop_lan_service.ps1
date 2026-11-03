param(
    [switch]$Quiet
)

$ErrorActionPreference = 'Continue'
$servicePorts = @(5174, 8011)

if (-not $Quiet) {
    Write-Host ''
    Write-Host '正在关闭企业业务平台服务...' -ForegroundColor Cyan
}

$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $servicePorts -contains $_.LocalPort })
$processIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)

if ($processIds.Count -eq 0) {
    if (-not $Quiet) {
        Write-Host '未发现正在运行的平台服务（端口 5174 / 8011）。' -ForegroundColor Yellow
    }
    exit 0
}

foreach ($processId in $processIds) {
    try {
        $process = Get-Process -Id $processId -ErrorAction Stop
        Stop-Process -Id $processId -Force -ErrorAction Stop
        if (-not $Quiet) {
            Write-Host ("  已关闭 {0}（PID {1}）" -f $process.ProcessName, $processId) -ForegroundColor Green
        }
    }
    catch {
        Write-Host ("  无法关闭 PID {0}：{1}" -f $processId, $_.Exception.Message) -ForegroundColor Red
    }
}

for ($attempt = 1; $attempt -le 20; $attempt++) {
    $remaining = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $servicePorts -contains $_.LocalPort })
    if ($remaining.Count -eq 0) { break }
    Start-Sleep -Milliseconds 250
}

$remaining = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $servicePorts -contains $_.LocalPort })

if ($remaining.Count -gt 0) {
    $remainingPorts = ($remaining | Select-Object -ExpandProperty LocalPort -Unique) -join '、'
    Write-Host "关闭未完成，以下端口仍被占用：$remainingPorts。" -ForegroundColor Red
    exit 1
}

if (-not $Quiet) {
    Write-Host '平台前后端服务已全部停止。' -ForegroundColor Green
}
exit 0
