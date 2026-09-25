<#
.SYNOPSIS
  CAD辅助绘图插件 — 一键安装脚本
.DESCRIPTION
  将当前文件夹内的 autocad_assistant_tool 插件安装到 N.E.K.O 插件目录。
  本脚本应放在插件文件夹内运行（与 __init__.py / plugin.toml 同级）。
.USAGE
  方法1: 右键本脚本 → 使用 PowerShell 运行
  方法2: PowerShell 中执行 .\install-plugin.ps1
  方法3: 双击 install.bat（如果提供了的话）
#>
param(
  [switch]$Force        # 跳过确认，直接覆盖
)

$ErrorActionPreference = "Stop"

# ============================================================
# 1. 确定源目录（本脚本所在目录 = 插件根目录）
# ============================================================
$src = $PSScriptRoot
if (-not $src) { $src = Split-Path -Parent $MyInvocation.MyCommand.Path }

# 关键文件检查
$required = @("plugin.toml", "__init__.py", "_bootstrap.py", "autocad_controller.py")
$missing = @()
foreach ($f in $required) {
  if (-not (Test-Path (Join-Path $src $f))) { $missing += $f }
}
if ($missing.Count -gt 0) {
  Write-Host "[错误] 找不到关键文件: $($missing -join ', ')" -ForegroundColor Red
  Write-Host "请确认 install-plugin.ps1 放在 autocad_assistant_tool 文件夹内。" -ForegroundColor Red
  Read-Host "按回车键退出"
  exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  CAD辅助绘图插件 · 安装程序" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "源目录: $src" -ForegroundColor DarkYellow

# ============================================================
# 2. 检测 N.E.K.O 插件目录
# ============================================================
$dst = Join-Path $env:LOCALAPPDATA "N.E.K.O\plugins\autocad_assistant_tool"
$nekoRoot = Join-Path $env:LOCALAPPDATA "N.E.K.O"

$nekoExists = Test-Path $nekoRoot
$nekoPluginsDir = Join-Path $env:LOCALAPPDATA "N.E.K.O\plugins"
$pluginsDirExists = Test-Path $nekoPluginsDir

Write-Host "N.E.K.O 目录: $nekoRoot" -ForegroundColor DarkYellow
if ($nekoExists) {
  Write-Host "[OK] N.E.K.O 已安装" -ForegroundColor Green
} else {
  Write-Host "[警告] 未检测到 N.E.K.O 安装目录" -ForegroundColor Yellow
  Write-Host "       安装后需要手动创建: $nekoPluginsDir" -ForegroundColor Yellow
}
Write-Host ""

# ============================================================
# 3. 确认操作
# ============================================================
if (-not $Force) {
  Write-Host "目标: $dst" -ForegroundColor DarkYellow
  if (Test-Path $dst) {
    Write-Host "[提示] 该目录已存在，安装将覆盖旧版本" -ForegroundColor Yellow
  }
  $confirm = Read-Host "确认安装? (Y/N)"
  if ($confirm -ne "Y" -and $confirm -ne "y") {
    Write-Host "已取消。" -ForegroundColor Gray
    exit 0
  }
}

# ============================================================
# 4. 执行安装
# ============================================================
Write-Host ""
Write-Host "[1/5] 创建目标目录..." -ForegroundColor Cyan

# 确保 N.E.K.O\plugins 目录存在
if (-not $pluginsDirExists) {
  New-Item -ItemType Directory -Force -Path $nekoPluginsDir | Out-Null
  Write-Host "  创建 plugins 目录: $nekoPluginsDir"
}

# 清理旧安装的缓存文件
if (Test-Path $dst) {
  Write-Host "[2/5] 清理旧缓存..." -ForegroundColor Cyan
  Get-ChildItem -Path $dst -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  Get-ChildItem -Path $dst -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
  Write-Host "  已清理 __pycache__ 和 .pyc"
}

New-Item -ItemType Directory -Force -Path $dst | Out-Null

# 要复制的目录（相对于 $src）
$dirs = @(
  "vendor",
  "vendor_cp311",
  "vendor_cp312",
  "vendor_cp313",
  "static",
  "docs"
)

# 要复制的文件（相对于 $src）
$files = @(
  "__init__.py",
  "_bootstrap.py",
  "autocad_controller.py",
  "plugin.toml",
  "config.example.toml",
  "pyproject.toml",
  "ruff.toml"
)

# 排除的目录/文件
$excludeDirs = @("_wheels", "__pycache__", ".git", ".github", "tests")

Write-Host "[3/5] 复制目录..." -ForegroundColor Cyan
$dirCount = 0
foreach ($d in $dirs) {
  $srcDir = Join-Path $src $d
  if (-not (Test-Path $srcDir)) { continue }
  $dstDir = Join-Path $dst $d
  New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
  $roboArgs = @($srcDir, $dstDir, "/MIR", "/XD", "__pycache__", "_wheels", ".git", "/XF", "*.pyc", "/NFL", "/NDL", "/NJH", "/NJS")
  $null = & robocopy @roboArgs 2>&1
  $count = (Get-ChildItem -Path $dstDir -Recurse -File -ErrorAction SilentlyContinue).Count
  Write-Host "  $d ($count files)" -ForegroundColor Green
  $dirCount++
}

Write-Host "[4/5] 复制文件..." -ForegroundColor Cyan
$fileCount = 0
foreach ($f in $files) {
  $srcFile = Join-Path $src $f
  if (-not (Test-Path $srcFile)) { continue }
  Copy-Item -Path $srcFile -Destination (Join-Path $dst $f) -Force
  Write-Host "  $f" -ForegroundColor Green
  $fileCount++
}

# ============================================================
# 5. 验证关键文件
# ============================================================
Write-Host "[5/5] 验证关键文件..." -ForegroundColor Cyan
$critical = @(
  "__init__.py",
  "_bootstrap.py",
  "autocad_controller.py",
  "plugin.toml",
  "vendor\pywintypes.py",
  "vendor\pythoncom.py"
)

# 至少需要一个版本专属 vendor
$cpFound = $false
foreach ($cp in @("cp311", "cp312", "cp313")) {
  $pyd = Join-Path $dst "vendor_$cp\_win32sysloader.pyd"
  $dll = Join-Path $dst "vendor_$cp\pywin32_system32\pythoncom$($cp.Substring(2)).dll"
  if ((Test-Path $pyd) -or (Test-Path $dll)) { $cpFound = $true; break }
}
if (-not $cpFound) {
  Write-Host "  [警告] 未找到任何 vendor_cp31X 目录中的 .pyd/.dll 文件" -ForegroundColor Yellow
} else {
  Write-Host "  [OK] 版本专属二进制文件已就绪" -ForegroundColor Green
}

$allOk = $true
foreach ($c in $critical) {
  $p = Join-Path $dst $c
  if (Test-Path $p) {
    Write-Host "  [OK] $c" -ForegroundColor Green
  } else {
    Write-Host "  [MISSING] $c" -ForegroundColor Red
    $allOk = $false
  }
}

# ============================================================
# 6. 完成
# ============================================================
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
if ($allOk -and $cpFound) {
  Write-Host "  安装成功！" -ForegroundColor Green
  Write-Host "========================================" -ForegroundColor Cyan
  Write-Host ""
  Write-Host "插件已安装到: $dst" -ForegroundColor DarkYellow
  Write-Host ""
  Write-Host "后续步骤:" -ForegroundColor Cyan
  Write-Host "  1. 重启 N.E.K.O" -ForegroundColor White
  Write-Host "  2. 手动打开 AutoCAD（或中望CAD/浩辰CAD）" -ForegroundColor White
  Write-Host "  3. 在 N.E.K.O 中与 AI 角色对话: "连接CAD"" -ForegroundColor White
  Write-Host "  4. 然后说 "画一个 100x50 的矩形"" -ForegroundColor White
} else {
  Write-Host "  安装完成但有文件缺失！" -ForegroundColor Yellow
  Write-Host "========================================" -ForegroundColor Cyan
  Write-Host "请检查上方 [MISSING] 项，确保插件文件夹完整。" -ForegroundColor Yellow
}
Write-Host ""
Read-Host "按回车键退出"
