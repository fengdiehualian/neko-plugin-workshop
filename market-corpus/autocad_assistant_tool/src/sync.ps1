<#
.SYNOPSIS
  一键同步 autocad_assistant_tool 插件到 N.E.K.O 插件目录。
.DESCRIPTION
  将开发目录中的插件文件同步到 %LOCALAPPDATA%\N.E.K.O\plugins\autocad_assistant_tool\。
  清除旧缓存，复制全部运行期文件（含 vendor_cp311/312/313 版本专属二进制）。
  同步前请先关闭 N.E.K.O，避免文件被占用。
.EXAMPLE
  .\sync.ps1              # 同步并验证
  .\sync.ps1 -DryRun      # 仅预览，不实际复制
#>
param(
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# --- 路径 ---
$src = $PSScriptRoot
$dst = Join-Path $env:LOCALAPPDATA "N.E.K.O\plugins\autocad_assistant_tool"

Write-Host "源  目录: $src"
Write-Host "目标目录: $dst"
if ($DryRun) { Write-Host "模式: DryRun（仅预览）`n" } else { Write-Host "" }

# --- 需要复制的目录 ---
$dirs = @(
  "vendor",
  "vendor_cp311",
  "vendor_cp312",
  "vendor_cp313",
  "static",
  "docs",
  "tests",
  ".github\workflows"
)

# --- 需要复制的单个文件 ---
$files = @(
  "__init__.py",
  "_bootstrap.py",
  "autocad_controller.py",
  "plugin.toml",
  "config.example.toml",
  "pyproject.toml",
  "ruff.toml",
  ".gitignore"
)

# --- 排除的目录/文件（不复制）---
$exclude = @("_wheels", "__pycache__")

# --- 检查 N.E.K.O 是否在运行 ---
$nekoProc = Get-Process -Name "N.E.K.O" -ErrorAction SilentlyContinue
if ($nekoProc -and -not $DryRun) {
  Write-Host "[警告] N.E.K.O 正在运行，文件可能被锁定。" -ForegroundColor Yellow
  Write-Host "建议先关闭 N.E.K.O 再同步。按 Ctrl+C 退出，或按任意键继续..."
  $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}

if (-not $DryRun) {
  # --- 清理目标旧缓存 ---
  if (Test-Path $dst) {
    Write-Host "[1/4] 清理旧缓存..." -ForegroundColor Cyan
    Get-ChildItem -Path $dst -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
      Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Path $dst -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue |
      Remove-Item -Force -ErrorAction SilentlyContinue
    Write-Host "  已清理 __pycache__ 和 .pyc"
  }
  # --- 创建目标目录 ---
  New-Item -ItemType Directory -Force -Path $dst | Out-Null
}

# --- 复制目录 ---
Write-Host "[2/4] 复制目录..." -ForegroundColor Cyan
foreach ($d in $dirs) {
  $srcDir = Join-Path $src $d
  if (-not (Test-Path $srcDir)) {
    Write-Host "  跳过（不存在）: $d" -ForegroundColor DarkGray
    continue
  }
  $dstDir = Join-Path $dst $d
  if ($DryRun) {
    $count = (Get-ChildItem -Path $srcDir -Recurse -File -ErrorAction SilentlyContinue).Count
    Write-Host "  [DRY] $d -> $dstDir [$count files]"
  } else {
    New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
    # 用 robocopy 复制（高效处理大量文件 + 排除 __pycache__）
    $roboArgs = @($srcDir, $dstDir, "/MIR", "/XD", "__pycache__", "/XF", "*.pyc", "/NFL", "/NDL", "/NJH", "/NJS")
    $null = & robocopy @roboArgs 2>&1
    $count = (Get-ChildItem -Path $dstDir -Recurse -File -ErrorAction SilentlyContinue).Count
    Write-Host "  $d [$count files]" -ForegroundColor Green
  }
}

# --- 复制单个文件 ---
Write-Host "[3/4] 复制文件..." -ForegroundColor Cyan
foreach ($f in $files) {
  $srcFile = Join-Path $src $f
  if (-not (Test-Path $srcFile)) {
    Write-Host "  跳过（不存在）: $f" -ForegroundColor DarkGray
    continue
  }
  $dstFile = Join-Path $dst $f
  $dstParent = Split-Path $dstFile -Parent
  if (-not (Test-Path $dstParent)) {
    New-Item -ItemType Directory -Force -Path $dstParent | Out-Null
  }
  if ($DryRun) {
    Write-Host "  [DRY] $f"
  } else {
    Copy-Item -Path $srcFile -Destination $dstFile -Force
    Write-Host "  $f" -ForegroundColor Green
  }
}

# --- 验证关键文件 ---
Write-Host "[4/4] 验证关键文件..." -ForegroundColor Cyan
$critical = @(
  "__init__.py",
  "_bootstrap.py",
  "autocad_controller.py",
  "plugin.toml",
  "vendor\pywintypes.py",
  "vendor\pythoncom.py",
  "vendor_cp311\_win32sysloader.pyd",
  "vendor_cp311\pywin32_system32\pythoncom311.dll",
  "vendor_cp313\pywin32_system32\pythoncom313.dll"
)
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

# --- 汇总 ---
Write-Host ""
if ($DryRun) {
  Write-Host "DryRun 完成 — 未实际复制文件。" -ForegroundColor Yellow
} elseif ($allOk) {
  Write-Host "同步完成！关键文件已验证。" -ForegroundColor Green
  Write-Host "下一步：重启 N.E.K.O → 打开 AutoCAD → 与角色对话绘图。" -ForegroundColor Cyan
} else {
  Write-Host "同步完成，但有文件缺失！请检查上方 [MISSING] 项。" -ForegroundColor Red
}
