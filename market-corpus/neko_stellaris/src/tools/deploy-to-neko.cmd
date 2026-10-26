@echo off
rem ---------------------------------------------------------------------------
rem Mirror this repository into N.E.K.O's plugin folder.
rem
rem   tools\deploy-to-neko.cmd            copy (mirror) into the host
rem
rem The plugin folder name must stay `neko_stellaris`: plugin.toml's entry point is
rem plugin.plugins.neko_stellaris, so the host looks for that directory name.
rem
rem Point NEKO_ROOT at a N.E.K.O checkout if it does not sit next to this repo.
rem NOT the same thing as `build_cli.py combined --install`, which writes the
rem *game* mod (sound/, events/, ...) into the Stellaris mod folder.
rem ---------------------------------------------------------------------------
setlocal

for %%I in ("%~dp0..") do set "REPO=%%~fI"
if not defined NEKO_ROOT set "NEKO_ROOT=%REPO%\..\N.E.K.O"
for %%I in ("%NEKO_ROOT%") do set "NEKO_ROOT=%%~fI"
set "DEST=%NEKO_ROOT%\plugin\plugins\neko_stellaris"

if not exist "%NEKO_ROOT%\plugin\plugins" (
    echo [X] Not an N.E.K.O checkout: "%NEKO_ROOT%"
    exit /b 2
)

echo [i] from = %REPO%
echo [i] to   = %DEST%
echo.

rem /MIR: the destination is a build target, so stale files from an older layout
rem must not survive. Repo-only things (.git, .github, .vscode) and prose
rem (docs, workshop) stay behind -- the host only needs code + data.
robocopy "%REPO%" "%DEST%" /MIR /XD .git .github .vscode docs workshop __pycache__ .pytest_cache /XF *.pyc /NFL /NDL /NJH /NJS
if errorlevel 8 (
    echo [X] robocopy failed ^(exit %ERRORLEVEL%^).
    exit /b 1
)

echo [OK] deployed. Verify with:
echo      tools\neko-check.cmd
echo      "%DEST%\smoke_offline.py"
