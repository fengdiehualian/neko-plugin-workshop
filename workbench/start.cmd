@echo off
rem N.E.K.O. Plugin Workshop launcher (console auto-hides)
rem lock branch: if a live studio is serving, just open a window to it;
rem              if the lock is stale (service dead), clear it and start fresh.
chcp 65001 >nul
title N.E.K.O. Workshop
set "PATH=%~dp0_py;%PATH%"
set "PYTHONPATH=%~dp0_site"
if exist "%~dp0runtime\studio.lock" (
  powershell -NoProfile -Command "$alive=$false;try{Invoke-WebRequest -Uri 'http://127.0.0.1:5099/api/health' -UseBasicParsing -TimeoutSec 2|Out-Null;$alive=$true}catch{};if($alive){$c=Get-Content '%~dp0runtime\studio.lock' -Raw;if($c){$exe=$c.Trim();if(Test-Path $exe){Start-Process $exe -ArgumentList ('--user-data-dir=\"'+$env:LOCALAPPDATA+'\Programs\NEKOWorkshop\runtime\edge-profile\"','--app=http://127.0.0.1:5099','--window-size=1200,860','--no-first-run')}};exit 0};exit 1"
  if errorlevel 1 (
    rem stale lock: studio dead, clear and start a fresh one
    del /q "%~dp0runtime\studio.lock" >nul 2>&1
  ) else (
    exit /b
  )
)
start /min "" "%~dp0_bin\bun.exe" "%~dp0wb-studio.mjs"
exit /b
