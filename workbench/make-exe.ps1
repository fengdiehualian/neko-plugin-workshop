# make-exe.ps1 - Pack the N.E.K.O. workshop into ONE self-extracting installer exe
# Zero third-party tools: uses .NET Framework csc.exe (built into Windows) + ZipArchive.
# Usage: powershell -File make-exe.ps1 -Stage "C:\path\workshop folder" [-Out "C:\path\setup.exe"]
param(
  [Parameter(Mandatory = $true)][string]$Stage,
  [string]$Out = "$env:TEMP\NEKOWorkshopSetup.exe",
  [string]$WorkDir = "$env:TEMP\neko_wb_build"
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path $Stage)) { throw "Stage not found: $Stage" }
$csc = @(
  "$env:SystemRoot\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
  "$env:SystemRoot\Microsoft.NET\Framework\v4.0.30319\csc.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) { throw "csc.exe not found (need .NET Framework 4.x)" }

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$csSource = Join-Path $here "sfx-installer.cs"
if (-not (Test-Path $csSource)) { throw "sfx-installer.cs not found next to this script" }

# 0. fresh work dir
if (Test-Path $WorkDir) { Remove-Item $WorkDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path "$WorkDir\payload" | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Out) | Out-Null

# 1. payload = stage minus runtime (user data; recreated on first run)
robocopy $Stage "$WorkDir\payload" /E /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with $LASTEXITCODE" }
$runtime = "$WorkDir\payload\runtime"
if (Test-Path $runtime) { Remove-Item $runtime -Recurse -Force }

# 2. zip the payload (workshop files at zip root)
Write-Host "Zipping payload ..."
$zipPath = "$WorkDir\payload.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path "$WorkDir\payload\*" -DestinationPath $zipPath -CompressionLevel Optimal

# 3. compile SFX: resource embeds the zip; subsystem windows (no console flash)
# NOTE: no /win32icon - csc failed to consume bun.exe's icon (CS1567); default icon is fine
Write-Host "Compiling self-extracting installer ..."
& $csc /nologo /target:winexe /platform:anycpu `
  /out:"$Out" `
  /r:System.IO.Compression.FileSystem.dll `
  /r:System.IO.Compression.dll `
  /resource:"$zipPath,payload.zip" `
  "$csSource"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Out)) { throw "csc failed (exit $LASTEXITCODE)" }

$exe = Get-Item $Out
Write-Host ("Done: {0}  {1:N1} MB" -f $exe.FullName, ($exe.Length / 1MB))
