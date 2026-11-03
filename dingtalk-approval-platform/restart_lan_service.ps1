$ErrorActionPreference = 'Stop'
$stopScript = Join-Path $PSScriptRoot 'stop_lan_service.ps1'
$startScript = Join-Path $PSScriptRoot 'start_lan_service.ps1'

try {
    if (-not (Test-Path -LiteralPath $stopScript)) {
        throw "找不到关闭脚本：$stopScript"
    }
    if (-not (Test-Path -LiteralPath $startScript)) {
        throw "找不到启动脚本：$startScript"
    }

    Write-Host ''
    Write-Host '正在重新启动企业业务平台局域网服务...' -ForegroundColor Cyan

    & $stopScript
    if ($LASTEXITCODE -ne 0) {
        throw '旧服务未能完全关闭，已取消重新启动。'
    }

    Start-Sleep -Milliseconds 500
    & $startScript
    $startExitCode = $LASTEXITCODE
    if ($startExitCode -ne 0) {
        throw "启动脚本执行失败（退出码 $startExitCode）。"
    }

    exit 0
}
catch {
    Write-Host ''
    Write-Host ("重启失败：{0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}
