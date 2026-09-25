<#
.SYNOPSIS
  部署 course_schedule 插件：增量同步开发目录到本机 N.E.K.O 已安装插件目录；
  可选：打 v* tag 并推送触发官方插件商店 CI 发布（解决商店"暂无可下载版本"）。

.DESCRIPTION
  1. 自动定位开发目录（脚本所在文件夹）。
  2. 自动定位 N.E.K.O 已安装插件目录：
     - 优先使用参数 -InstallDir
     - 其次使用参数 -NekoPluginsDir
     - 其次使用环境变量 NEKO_PLUGINS_DIR
     - 否则用默认路径 $env:LOCALAPPDATA\N.E.K.O\plugins\<PluginId>
  3. 读取 plugin.toml 的 id/version，校验与 pyproject.toml 版本一致（发布前必须一致）。
  4. 若目标目录不存在会自动创建（可用于首次部署）。
  5. robocopy /E 增量同步（保留运行时 data / config）。
  6. 可选：-Release 发布模式：校验版本 → 打 vX.Y.Z tag → 推送 tag → 触发官方
     plugin-market-release.yml CI，自动构建并上传到插件商店，审核后可直接下载。

.PARAMETER PluginId
  插件 ID，默认从 plugin.toml 的 [plugin] id= 自动读取，读取失败则用目录名。

.PARAMETER InstallDir
  目标安装插件目录（绝对路径）。留空则自动推导（LOCALAPPDATA\N.E.K.O\plugins\<PluginId>）。

.PARAMETER NekoPluginsDir
  N.E.K.O 的 plugins 根目录，留空用 LOCALAPPDATA\N.E.K.O\plugins。
  最终 InstallDir = $NekoPluginsDir\<PluginId>。

.PARAMETER Message
  自定义 commit message。不指定则用 "deploy: 同步代码 <时间戳>"。

.PARAMETER DryRun
  预览将执行的操作，不实际执行。

.PARAMETER SkipSync
  跳过同步，只做 git 提交推送/发布。

.PARAMETER SkipGit
  跳过 git 提交推送，只做同步。

.PARAMETER CreateIfMissing
  目标插件目录不存在时自动创建（默认 true，可传 -CreateIfMissing:$false 保持旧行为直接报错）。

.PARAMETER Release
  发布模式（商店）：1) 校验 plugin.toml 与 pyproject.toml 的 version 一致；
  2) 若本地/远端已存在 v<version> tag 则报错（避免覆盖已有版本）；
  3) 提示确认后创建 annotated tag v<version> 并推送 origin；
  4) GitHub Actions release.yml 会自动构建并发布到官方插件市场。
  （首次发布前请完成：git remote 已配置并可推送、verify.yml 通过、plugin.store.enabled=true。）

.PARAMETER ReleaseTag
  发布时使用自定义 tag（默认 v<plugin.toml.version>），例如 "v0.2.0-hotfix1"。

.EXAMPLE
  .\deploy.ps1
  .\deploy.ps1 -Message "fix: 修复课表解析"
  .\deploy.ps1 -NekoPluginsDir "D:\N.E.K.O\plugins"
  .\deploy.ps1 -InstallDir "C:\Tools\N.E.K.O\plugins\course_schedule" -DryRun
  .\deploy.ps1 -SkipGit     # 只同步，不提交 git
  .\deploy.ps1 -SkipSync    # 跳过同步，只做 git 提交推送
  .\deploy.ps1 -Release -DryRun   # 预览发布（打 tag 推送商店触发 CI）
  .\deploy.ps1 -Release     # 发布当前版本到插件商店（等商店审核后可下载）
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$PluginId,
    [string]$InstallDir,
    [string]$NekoPluginsDir,
    [string]$Message,
    [switch]$DryRun,
    [switch]$SkipSync,
    [switch]$SkipGit,
    [bool]$CreateIfMissing = $true,
    [switch]$Release,
    [string]$ReleaseTag
)

$ErrorActionPreference = 'Stop'

# ── 1. 定位开发目录（脚本所在目录，动态获取，无需写死）──
$DevDir = $PSScriptRoot
if (-not (Test-Path (Join-Path $DevDir 'plugin.toml'))) {
    throw "deploy.ps1 必须与 plugin.toml 位于同一目录，当前 DevDir=$DevDir"
}

# ── 2. 读取 plugin.toml（id / version）并校验 pyproject.toml 版本一致 ──
$pluginTomlPath = Join-Path $DevDir 'plugin.toml'
$pluginTomlContent = Get-Content $pluginTomlPath -Raw -Encoding UTF8
# PluginId
if (-not $PluginId) {
    if ($pluginTomlContent -match '(?mi)^\s*id\s*=\s*"([^"]+)"') {
        $PluginId = $Matches[1]
    } else {
        $PluginId = Split-Path $DevDir -Leaf
    }
}
# plugin.toml version
$PluginVersion = $null
if ($pluginTomlContent -match '(?mi)^\s*version\s*=\s*"([^"]+)"') {
    $PluginVersion = $Matches[1]
}
# pyproject.toml version（可选，但 Release 模式必须两者一致）
$pyprojectVer = $null
$pyprojectPath = Join-Path $DevDir 'pyproject.toml'
if (Test-Path $pyprojectPath) {
    $pyContent = Get-Content $pyprojectPath -Raw -Encoding UTF8
    if ($pyContent -match '(?mi)^\s*version\s*=\s*"([^"]+)"') {
        $pyprojectVer = $Matches[1]
    }
}
if ($Release -and $PluginVersion -and $pyprojectVer -and $PluginVersion -ne $pyprojectVer) {
    Write-Err "Release 模式要求 plugin.toml(version=$PluginVersion) 与 pyproject.toml(version=$pyprojectVer) 版本一致，请先同步后再发布。"
    exit 1
}

# ── 3. 推导目标安装目录 ──
if (-not $InstallDir) {
    if ($NekoPluginsDir) {
        $pluginsRoot = $NekoPluginsDir
    } elseif ($env:NEKO_PLUGINS_DIR) {
        $pluginsRoot = $env:NEKO_PLUGINS_DIR
    } else {
        # 默认路径：%LOCALAPPDATA%\N.E.K.O\plugins
        if (-not $env:LOCALAPPDATA) {
            throw "环境变量 LOCALAPPDATA 不存在，请用 -NekoPluginsDir 或 -InstallDir 手动指定目标目录"
        }
        $pluginsRoot = Join-Path $env:LOCALAPPDATA 'N.E.K.O\plugins'
    }
    $InstallDir = Join-Path $pluginsRoot $PluginId
}

$ExcludeDirs = @('.git', '__pycache__', '.pytest_cache', '.ruff_cache', '.venv', '.vscode')

function Write-Step($m) { Write-Host "[deploy] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[deploy] $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[deploy] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "[deploy] $m" -ForegroundColor Red }

Write-Step "插件: $PluginId"
Write-Step "开发目录: $DevDir"
Write-Step "安装目录: $InstallDir"

# ── 4. 同步代码到已安装插件目录 ──
if (-not $SkipSync) {
    Write-Step "同步: $DevDir -> $InstallDir"
    if (-not (Test-Path $InstallDir)) {
        if ($CreateIfMissing) {
            if ($DryRun) {
                Write-Warn "DRY-RUN: 跳过创建目录 $InstallDir"
            } else {
                $null = New-Item -ItemType Directory -Path $InstallDir -Force
                Write-Ok "自动创建目标目录: $InstallDir"
            }
        } else {
            Write-Err "已安装插件目录不存在: $InstallDir (用 -CreateIfMissing 或先创建)"
            exit 1
        }
    }
    if ($DryRun) {
        Write-Warn "DRY-RUN: 跳过 robocopy"
    } else {
        $rcArgs = @($DevDir, $InstallDir, '/E', '/R:1', '/W:1', '/NFL', '/NDL', '/NJH', '/NP')
        foreach ($d in $ExcludeDirs) { $rcArgs += @('/XD', $d) }
        & robocopy @rcArgs
        # robocopy 退出码 0-7 都算成功（7 = 有文件复制 + 跳过一些）
        if ($LASTEXITCODE -ge 8) {
            Write-Err "robocopy 失败 (退出码 $LASTEXITCODE)"
            exit 1
        }
        Write-Ok "同步完成 (robocopy 退出码 $LASTEXITCODE)"
    }
}

# ── 5. Git 提交推送 ──
if (-not $SkipGit) {
    Push-Location $DevDir
    try {
        # 确认是 git 仓库
        if (-not (Test-Path (Join-Path $DevDir '.git'))) {
            Write-Warn "非 Git 仓库，跳过 git 提交/推送"
        } else {
            Write-Step "检查 git 变更"
            $status = git status --porcelain
            if (-not $status) {
                Write-Ok "工作区干净，无变更需要提交"
            } else {
                $msg = if ($Message) { $Message } else { "deploy: 同步代码到已安装插件目录 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" }
                Write-Step "提交: $msg"
                if ($DryRun) {
                    Write-Warn "DRY-RUN: 跳过 git add/commit/push"
                } else {
                    git add -A
                    git commit -m $msg
                    if ($LASTEXITCODE -ne 0) { Write-Err "git commit 失败"; exit 1 }
                    git push
                    if ($LASTEXITCODE -ne 0) { Write-Err "git push 失败（请先 git pull --rebase）"; exit 1 }
                    Write-Ok "提交并推送完成"
                }
            }
        }
    } finally {
        Pop-Location
    }
}

# ── 6. Release 模式：打 v* tag 推送触发官方商店 CI 发布 ──
if ($Release) {
    Push-Location $DevDir
    try {
        if (-not (Test-Path (Join-Path $DevDir '.git'))) {
            Write-Err "-Release 模式需要 Git 仓库，且需要可推送的 remote origin，用于打 tag 触发 CI。"
            exit 1
        }
        # 先确认工作区干净
        $status = git status --porcelain
        if ($status) {
            Write-Err "工作区仍有未提交变更，请先 commit/push 后再 -Release（或不加 -SkipGit 让脚本提交）。变更清单："
            $status | ForEach-Object { Write-Err "  $_" }
            exit 1
        }
        # Tag 名：默认 v<PluginVersion>，可用 -ReleaseTag 覆盖
        if (-not $ReleaseTag) {
            if (-not $PluginVersion) {
                Write-Err "plugin.toml 未找到 version 字段，无法推导 ReleaseTag，请显式传 -ReleaseTag vX.Y.Z"
                exit 1
            }
            $ReleaseTag = "v$PluginVersion"
        }
        if ($ReleaseTag -notmatch '^v') {
            Write-Err "-ReleaseTag 必须以 'v' 开头（例如 v0.2.0），当前 ReleaseTag=$ReleaseTag。release.yml 的 on.push.tags: v* 将只匹配这种 tag。"
            exit 1
        }
        # 本地已存在同名 tag → 报错（禁止覆盖）
        $localExist = (git tag -l $ReleaseTag)
        if ($localExist) {
            Write-Err "本地已存在 tag $ReleaseTag 。若确需重发，请先手动删除："
            Write-Err "  git tag -d $ReleaseTag ; git push origin :refs/tags/$ReleaseTag"
            exit 1
        }
        # 远端 origin 是否已存在同名 tag
        try {
            $remoteExist = (git ls-remote --tags origin $ReleaseTag 2>$null)
        } catch { $remoteExist = $null }
        if ($remoteExist) {
            Write-Err "远端 origin 已存在 tag $ReleaseTag，不允许覆盖（商店 CI 会认为这是同一版本，避免审核污染）。"
            Write-Err "请在 plugin.toml / pyproject.toml 中升级 version 后重新运行 -Release。"
            exit 1
        }
        # store.enabled 检查
        if ($pluginTomlContent -notmatch '(?msi)\[plugin\.store\][\s\S]*?enabled\s*=\s*true') {
            Write-Warn "[plugin.store] enabled=true 未检测到；若官方商店未收录该插件，推送 tag 后市场仍可能显示'暂无可下载版本'。"
        }
        Write-Step "将创建并推送 tag: $ReleaseTag"
        if ($PluginVersion) {
            Write-Step "  plugin.toml  version = $PluginVersion"
        }
        if ($pyprojectVer) {
            Write-Step "  pyproject   version = $pyprojectVer"
        }
        Write-Step "推送 tag 后，GitHub release.yml 将触发官方 plugin-market-release.yml，构建完成并审核通过后，插件商店即可直接下载本版本。"
        if (-not $DryRun) {
            $confirm = $PSCmdlet.ShouldProcess("git tag -a $ReleaseTag && git push origin $ReleaseTag", "是否确认发布 tag $ReleaseTag 到商店？", "发布确认")
            if (-not $confirm) {
                # fallback：交互式确认（ShouldProcess 可被 -Confirm/-WhatIf 控制）
                $ans = Read-Host "确认发布 tag ${ReleaseTag} 到 origin 并触发商店 CI? (y/N)"
                if ($ans -notmatch '^[Yy](es)?$') {
                    Write-Warn "用户取消 Release 发布"
                    exit 0
                }
            }
            git tag -a $ReleaseTag -m "Release $ReleaseTag"
            if ($LASTEXITCODE -ne 0) { Write-Err "git tag 创建失败"; exit 1 }
            git push origin $ReleaseTag
            if ($LASTEXITCODE -ne 0) { Write-Err "git push tag 失败（检查 remote origin 权限/网络）"; exit 1 }
            Write-Ok "=== 发布成功：tag $ReleaseTag 已推送 origin ==="
            Write-Ok "请查看 GitHub Actions: release.yml 运行日志（约 1-3 分钟）"
            Write-Ok "构建通过 + 商店审核通过后，插件市场将显示该版本为可下载版本。"
        } else {
            Write-Warn "DRY-RUN: 跳过 git tag/push 发布操作"
        }
    } finally {
        Pop-Location
    }
}

Write-Ok "部署流程结束"
