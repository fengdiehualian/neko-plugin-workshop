# 安装包口径核验:官方安装器只支持覆盖升级,包内条目必须齐(历史上缺 _zcode 少 30MB)
param([string]$Exe = "C:\Users\Administrator\dev\neko-plugin-workshop\build\N.E.K.O.PluginWorkshop-Setup.exe")
$ErrorActionPreference = "Stop"
$work = Join-Path $env:TEMP ("wbpk-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $work | Out-Null
Write-Output ("解包目录:" + $work)
# IExpress SFX:/T 指定目标 /C 只解压不执行安装命令
& $Exe /T:$work /C | Out-Null
Start-Sleep -Seconds 6
$deadline = (Get-Date).AddMinutes(6)
$exe0 = Join-Path $work "payload.zip"
while (-not (Test-Path $exe0) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 800 }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [System.IO.Compression.ZipFile]::OpenRead($exe0)
$names = @($z.Entries | ForEach-Object { $_.FullName })
$total = $names.Count
Write-Output ("payload.zip 条目数:" + $total)
$must = @("wb-studio.mjs", "wb-agent-lib.mjs", "package.json", "start.cmd", "_zcode/node.exe", "_zcode/zcode.cjs", "_zcode/provider/zcode-builtin.json", "packs/neko-plugin/rules.json", "experts/neko-plugin-ops/.codebuddy-plugin/plugin.json")
$miss = @()
foreach ($m in $must) {
  $hit = $names | Where-Object { $_ -eq $m -or $_ -eq ("./" + $m) -or $_.EndsWith("/" + $m) }
  if (-not $hit) { $miss += $m }
}
if ($miss.Count) { Write-Output ("缺条目:" + ($miss -join ", ")) } else { Write-Output "必查条目齐全(含 _zcode)" }
$big = @($z.Entries | Where-Object { $_.FullName -like "*_zcode/node.exe" })
if ($big.Count) { Write-Output ("_zcode/node.exe 原始大小:" + $big[0].Length + " 压缩后:" + $big[0].CompressedLength) }
$z.Dispose()
$bytes = (Get-Item $Exe).Length
$sha = (Get-FileHash -Algorithm SHA256 $Exe).Hash
Write-Output ("exe " + $bytes + " 字节  SHA256:" + $sha)
Set-Content -Encoding ascii -Path (Join-Path $work "..\wbpk-last.txt") -Value ($bytes.ToString() + " " + $sha)
Remove-Item -Recurse -Force $work
Write-Output "PKG_CHECK_DONE"
