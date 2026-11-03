@echo off
setlocal EnableExtensions
chcp 65001 >nul
title 企业业务平台 - 重启局域网服务
pushd "%~dp0" || (
  echo 无法进入项目目录：%~dp0
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_lan_service.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
popd

echo.
if "%EXIT_CODE%"=="0" (
  echo 局域网服务已停止。
) else (
  echo 重启失败，请查看上方提示或 data\logs\api-error.log。
  echo 退出码：%EXIT_CODE%
)
echo 按任意键关闭此窗口...
pause >nul

endlocal & exit /b %EXIT_CODE%
