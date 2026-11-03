@echo off

chcp 65001 >nul

title 审批诊断实验版

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_experiment.ps1" -Action stop

if errorlevel 1 pause

