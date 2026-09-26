@echo off
rem N.E.K.O. Plugin Workshop launcher (console auto-hides)
rem lock branch: if a live studio is serving, just open a window to it;
rem              if the lock is stale (service dead), clear it and start fresh.
chcp 65001 >nul
title N.E.K.O. Workshop
set "PATH=%~dp0_py;%PATH%"
set "PYTHONPATH=%~dp0_site"

rem ---- Guard: running from a temp/zip-extract location ----
rem When users double-click start.cmd INSIDE a zip, archive tools extract only
rem small files to a temp folder; the 175MB engine exe is missing there.
echo %~dp0 | findstr /I /C:"\Temp\" >nul && goto :badlocation
if not exist "%~dp0_engine\opencode-cli.exe" goto :noengine
goto :main

:badlocation
echo.
echo  ============================================================
echo   [!] You are running this from a TEMPORARY folder.
echo   Please EXTRACT THE WHOLE ZIP first (right-click the zip,
echo   choose "Extract All"), then run start.cmd inside the
echo   extracted folder. Do not double-click files inside the zip.
echo  ============================================================
echo.
pause
exit /b 1

:noengine
echo.
echo  ============================================================
echo   [!] Engine file missing: _engine\opencode-cli.exe
echo   Cause 1: you double-clicked inside the zip - extract the
echo            whole zip to a real folder first.
echo   Cause 2: antivirus removed it - restore/allow it in your
echo            antivirus, then re-extract the zip.
echo  ============================================================
echo.
pause
exit /b 1

:main
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
