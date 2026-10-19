# make-exe.ps1 - Pack the N.E.K.O. workshop into ONE self-extracting installer exe
# Zero third-party tools: uses .NET Framework csc.exe (built into Windows) + ZipArchive.
# Usage: powershell -File make-exe.ps1 -Stage "C:\path\workshop folder" [-Out "C:\path\setup.exe"]
param(
  [Parameter(Mandatory = $true)][string]$Stage,
  [string]$Out = "$env:TEMP\NEKOWorkshopSetup.exe",
  [string]$WorkDir = "$env:TEMP\neko_wb_build",
  # 仅当 $Stage 是「临时构建副本」时使用:压完 zip 立即删掉它。
  # 磁盘紧张时(剩余 <700MB)能把峰值降低约 330MB;千万别对真实安装目录开这个开关。
  [switch]$DropStageAfterZip
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
#    若 stage 里没有 runtime,直接用 stage 压缩,省掉一份 330MB 的副本
$stageRuntime = Join-Path $Stage "runtime"
$hasRuntime = (Test-Path $stageRuntime) -and ((Get-ChildItem $stageRuntime -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0)
if ($hasRuntime) {
  robocopy $Stage "$WorkDir\payload" /E /NFL /NDL /NJH /NJS | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "robocopy failed with $LASTEXITCODE" }
  if (Test-Path "$WorkDir\payload\runtime") { Remove-Item "$WorkDir\payload\runtime" -Recurse -Force }
  $zipSource = "$WorkDir\payload\*"
} else {
  Write-Host "Stage has no runtime, zipping in place (saves ~330MB disk)"
  $zipSource = "$Stage\*"
}

# 2. zip the payload (workshop files at zip root)
Write-Host "Zipping payload ..."
$zipPath = "$WorkDir\payload.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path $zipSource -DestinationPath $zipPath -CompressionLevel Optimal

# 2.5 打完包就把 payload 副本删掉:它和 $Stage 内容重复,留着会让峰值磁盘占用
# 多出整整一份(约 330MB)。低配机器(剩余空间 <1GB)会因此构建失败。
if (Test-Path "$WorkDir\payload") { Remove-Item "$WorkDir\payload" -Recurse -Force -ErrorAction SilentlyContinue }

# 2.6 低磁盘模式:stage 只是临时副本,zip 打完就删,给 csc 腾出空间
if ($DropStageAfterZip -and (Test-Path $Stage) -and -not $hasRuntime) {
  Write-Host "Dropping build stage to free disk ..."
  Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
}

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
