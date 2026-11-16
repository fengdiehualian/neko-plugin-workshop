# 安装包口径核验(ASCII-only 源码:中文 Windows 会把无 BOM 的 UTF-8 .ps1 当 GBK 解析,
# 脚本里的中文串会变成非法记号 —— 实测踩过,所以这里消息一律用英文)
#
# 官方安装器只支持覆盖升级,所以包内条目必须齐:
#   历史上漏打 _zcode 会让包小 30MB;误把 build/ 提交进仓库又会让载荷变 217MB;
#   market-corpus / tools/probes 绝不能进包(第三方许可与本机路径线索)。
# 路径不写死:默认相对本脚本所在仓库,换机器也能跑(issue #6)。
param([string]$Exe = "")
$ErrorActionPreference = "Stop"
$FAIL = 0
if (-not $Exe) { $Exe = Join-Path (Split-Path $PSScriptRoot -Parent) "build\N.E.K.O.PluginWorkshop-Setup.exe" }

$work = Join-Path $env:TEMP ("wbpk-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $work | Out-Null
Write-Output ("extract dir: " + $work)

# IExpress SFX: /T 指定目标 /C 只解压不执行安装命令
& $Exe /T:$work /C | Out-Null
Start-Sleep -Seconds 6
$payload = Join-Path $work "payload.zip"
$deadline = (Get-Date).AddMinutes(6)
while (-not (Test-Path $payload) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 800 }
if (-not (Test-Path $payload)) { Write-Output "FAIL: payload.zip not found after extract"; Remove-Item -Recurse -Force $work; exit 1 }

Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [System.IO.Compression.ZipFile]::OpenRead($payload)
$names = @($z.Entries | ForEach-Object { $_.FullName })
Write-Output ("payload.zip entries: " + $names.Count)

$must = @(
  "wb-studio.mjs", "wb-agent-lib.mjs", "package.json", "start.cmd",
  "BUILD_INFO.json",
  "_zcode/node.exe", "_zcode/zcode.cjs", "_zcode/provider/zcode-builtin.json",
  "packs/neko-plugin/rules.json",
  "experts/neko-plugin-ops/.codebuddy-plugin/plugin.json"
)
$miss = @()
foreach ($m in $must) {
  $hit = @($names | Where-Object { $_ -eq $m -or $_ -eq ("./" + $m) -or $_.EndsWith("/" + $m) })
  if (-not $hit.Count) { $miss += $m }
}
if ($miss.Count) { Write-Output ("MISSING REQUIRED: " + ($miss -join ", ")); $FAIL = 1 }
else { Write-Output "required entries present (incl _zcode)" }

# 禁含条目:第三方语料、本机探针、以及本机配置都绝不能出现在安装包里
# (wb-studio.config.json 一旦进包,覆盖安装就会把用户的端口/引擎口/模型/界面设定冲回开发机默认值)
$forbidden = @("market-corpus/", "tools/probes/", "wb-studio.config.json", "packs/neko-plugin/skill/neko-plugin-dev/references/market-examples/")
$bad = @()
foreach ($f in $forbidden) {
  $hit = @($names | Where-Object { $_.StartsWith($f) })
  if ($hit.Count) { $bad += ("$f -> $($hit.Count) entries") }
}
if ($bad.Count) { Write-Output ("FORBIDDEN CONTENT PACKAGED: " + ($bad -join "; ")); $FAIL = 1 }
else { Write-Output "forbidden entries check passed (no corpus / no probes)" }

$node = @($z.Entries | Where-Object { $_.FullName -like "*_zcode/node.exe" })
if ($node.Count) { Write-Output ("_zcode/node.exe raw=" + $node[0].Length + " compressed=" + $node[0].CompressedLength) }
$z.Dispose()

$bytes = (Get-Item $Exe).Length
$sha = (Get-FileHash -Algorithm SHA256 $Exe).Hash
Write-Output ("exe " + $bytes + " bytes  SHA256:" + $sha)
Remove-Item -Recurse -Force $work
Write-Output ("PKG_CHECK_DONE " + $(if ($FAIL) { "FAILED" } else { "OK" }))
if ($FAIL) { exit 1 }
