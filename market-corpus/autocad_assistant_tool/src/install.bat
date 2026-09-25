@echo off
REM CAD辅助绘图插件 — 一键安装启动器
REM 双击此文件即可运行 PowerShell 安装脚本
cd /d "%~dp0"
powershell.exe -ExecutionPolicy Bypass -File "%~dp0install-plugin.ps1"
