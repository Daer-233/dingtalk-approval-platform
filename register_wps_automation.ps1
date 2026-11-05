$ErrorActionPreference = 'Stop'
$wpsRoot = Join-Path $env:LOCALAPPDATA 'Kingsoft\WPS Office'
$spreadsheet = Get-ChildItem -Path $wpsRoot -Filter et.exe -Recurse -File -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending |
    Select-Object -First 1

if (-not $spreadsheet) {
    Write-Host '未找到 WPS 表格（et.exe）。请确认本机已安装 WPS Office。' -ForegroundColor Yellow
    exit 1
}

Write-Host "正在注册 WPS 表格自动化组件：$($spreadsheet.FullName)" -ForegroundColor Cyan
Write-Host '请确保 WPS 已关闭。该操作仅注册本机 WPS 的自动化接口，不会修改您的业务文件。' -ForegroundColor DarkGray
Write-Host '正在执行注册并检测，预计不超过 30 秒……' -ForegroundColor DarkGray

# WPS 的 /regserver 在部分版本中会启动后持续驻留；不能用 -Wait，否则脚本会无限等待。
$null = Start-Process -FilePath $spreadsheet.FullName -ArgumentList '/regserver' -PassThru
Start-Sleep -Seconds 3

$registered = $false
for ($attempt = 1; $attempt -le 10; $attempt++) {
    try {
        $wps = New-Object -ComObject 'ket.Application'
        $wps.Quit()
        $registered = $true
        break
    }
    catch {
        Start-Sleep -Seconds 2
    }
}

if ($registered) {
    Write-Host 'WPS 表格自动化组件注册成功。现在可回到网站使用“本机 WPS / Microsoft Excel 授权导入”。' -ForegroundColor Green
    exit 0
}

Write-Host '注册命令已经启动，但 23 秒内仍未检测到自动化组件。请先重启电脑后再试；若仍失败，可使用已安装的 Microsoft Excel 作为后备。' -ForegroundColor Yellow
exit 1