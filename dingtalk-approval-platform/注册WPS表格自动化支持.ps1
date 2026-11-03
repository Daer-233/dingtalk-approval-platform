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
$process = Start-Process -FilePath $spreadsheet.FullName -ArgumentList '/regserver' -Wait -PassThru
if ($process.ExitCode -ne 0) {
    Write-Host "注册命令返回异常代码：$($process.ExitCode)" -ForegroundColor Red
    exit $process.ExitCode
}

try {
    $wps = New-Object -ComObject 'ket.Application'
    $wps.Quit()
    Write-Host 'WPS 表格自动化组件注册成功。现在可回到网站使用“本机 WPS / Microsoft Excel 授权导入”。' -ForegroundColor Green
}
catch {
    Write-Host '注册命令已执行，但自动化组件仍不可用。请重启 WPS 或电脑后再试。' -ForegroundColor Yellow
}
