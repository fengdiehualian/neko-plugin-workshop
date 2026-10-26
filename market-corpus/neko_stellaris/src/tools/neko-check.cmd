@echo off
rem ---------------------------------------------------------------------------
rem Official N.E.K.O plugin check, run against THIS repository.
rem
rem   tools\neko-check.cmd              harmless readiness check (0 error = OK)
rem   tools\neko-check.cmd --strict     same, but missing repo files count as errors
rem   tools\neko-check.cmd -r           pre-release: strict + pytest + package + hash
rem
rem Why a wrapper at all: the CLI lives inside the N.E.K.O checkout (it imports
rem plugin.sdk and its own test helpers), so it has to be started from there with
rem that checkout's interpreter. This script finds both and passes the rest of
rem the arguments straight through to `neko-plugin check`.
rem
rem Point NEKO_ROOT at a N.E.K.O checkout if it does not sit next to this repo.
rem ---------------------------------------------------------------------------
setlocal

for %%I in ("%~dp0..") do set "REPO=%%~fI"
if not defined NEKO_ROOT set "NEKO_ROOT=%REPO%\..\N.E.K.O"
for %%I in ("%NEKO_ROOT%") do set "NEKO_ROOT=%%~fI"

set "PY=%NEKO_ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY="
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [X] No Python found. Install N.E.K.O's venv or put python on PATH.
    exit /b 2
)

if not exist "%NEKO_ROOT%\plugin\neko_plugin_cli\cli.py" (
    echo [X] Not an N.E.K.O checkout: "%NEKO_ROOT%"
    echo     Set NEKO_ROOT to the folder that contains plugin\neko_plugin_cli.
    exit /b 2
)

echo [i] repo   = %REPO%
echo [i] neko   = %NEKO_ROOT%
echo [i] python = %PY%
echo.

pushd "%NEKO_ROOT%"
"%PY%" -m plugin.neko_plugin_cli check "%REPO%" %*
set "RC=%ERRORLEVEL%"
popd

echo.
if "%RC%"=="0" (
    echo [OK] neko-plugin check finished without errors.
) else (
    echo [X] neko-plugin check reported problems ^(exit %RC%^).
)
exit /b %RC%
