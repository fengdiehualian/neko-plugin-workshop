# 从公开源码安装

这是源码开发者的安装入口。普通接收者按 [完整离线包安装](../README.md#完整离线包安装) 解压、准备并导入，无需此处的构建工具。版本以本次 checkout 的 `plugin.toml` / `pyproject.toml` 为准；核心和观战的不可变来源以同步后 `vendor/agent_mc_autonomy/core_distribution/source.json` 为准。历史候选记录不替代这些输入。源码验证、包安装和真实游戏验收分别记录。

## 1. 准备独立构建环境和源码

需要 Git、uv、与目标宿主匹配的 Python，以及后端使用的 Node.js >=22。当前官方 N.E.K.O 源码环境要求 Python 3.11；Windows CPython 3.11 宿主应在 Windows、相同架构的 CPython 3.11 上构建。冻结版 EXE 不是可用于 pip/sync 的普通 Python 解释器，不向其共享环境全局安装依赖，也不手写 `dist-info`。

源码开发者使用下面的“构建 → 插件中心导入”。在宿主之外新建构建目录，按 [N.E.K.O 官方源码环境说明](https://github.com/Project-N-E-K-O/N.E.K.O#readme) 准备真实 SDK（不要复制或伪造 SDK）。下面的路径都是自行选择的构建目录示例，不是运行目录：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$work = 'C:/MinecraftBuild' # 新建的专用目录
New-Item -ItemType Directory -Force $work | Out-Null
Set-Location $work
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git N.E.K.O
Set-Location "$work/N.E.K.O"
uv sync --python 3.11
$sdk = (Get-Location).Path
$builderPython = "$sdk/.venv/Scripts/python.exe"
& $builderPython -c "import sys, platform; print(sys.version); print(platform.system(), platform.machine())"
git rev-parse HEAD # 保存 SDK 提交供复现
Set-Location $work
git clone https://github.com/jcs130/n.e.k.o_plugin_minecraft_autonomous.git minecraft_autonomous
$plugin = (Resolve-Path ./minecraft_autonomous).Path
git -C $plugin rev-parse HEAD # 保存本次插件提交
```

已有真实 SDK 开发环境可以直接设置 `$sdk` / `$builderPython`，无需更新它。其它平台使用对应虚拟环境 Python 路径；应先核对目标宿主版本与架构，不能把不同平台/版本的二进制 wheel 复制过去。

## 2. 同步、检查并构建插件包

保持插件源码目录名为 `minecraft_autonomous`。在 SDK 根目录调用其正式 CLI，目标为刚克隆的源码目录：

```powershell
Set-Location $sdk
$env:NEKO_ROOT = $sdk # 官方检查中的离线测试子进程也要能定位真实 SDK
& $builderPython -m plugin.neko_plugin_cli sync $plugin --python $builderPython
if ($LASTEXITCODE -ne 0) { throw '依赖同步失败，请保留日志并停止' }
$env:PYTHONPATH = "$plugin/vendor"
& $builderPython -m plugin.neko_plugin_cli check --release --market-release $plugin --target-dir "$work/checked-packages"
if ($LASTEXITCODE -ne 0) { throw '插件检查失败，请停止' }
$package = "$work/minecraft_autonomous.neko-plugin"
& $builderPython -m plugin.neko_plugin_cli build $plugin --out $package
if ($LASTEXITCODE -ne 0) { throw '插件构建失败，请停止' }
Get-FileHash $package -Algorithm SHA256
```

同步从 `pyproject.toml` 安装全部声明的 Python 依赖到这个源码目录的 `vendor/`，包括真实的 `agent_mc_autonomy-*.dist-info`。需要网络或完整缓存；不要只复制一个框架源码子目录。当前声明含固定 Git 框架和版本范围依赖；保存构建包、SHA256 和实际依赖清单，不能据固定框架 pin 宣称所有传递依赖永久锁定。

构建输出是 ZIP 格式的 `.neko-plugin`；交付前用归档工具确认其中存在：

- `payload/plugins/minecraft_autonomous/plugin.meta.json`：真实 SDK 从入口导出的静态元数据及源码指纹；本版应含 18 个公开入口及规划语音定时处理器。SDK 可能在元数据探测失败时警告后继续打包，单看 CLI 退出成功不够，缺失时应停止交付并排查导入错误。
- `payload/plugins/minecraft_autonomous/vendor/`：含声明依赖及其 `dist-info`。
- `payload/dependencies.toml`、`manifest.toml` 和 `metadata.toml`。
- 插件静态界面、YUI slim 皮肤和原始 YSM 外观资源。

不得手写元数据来绕过失败。普通用户在 **N.E.K.O 插件中心 → 导入** 选择该包，按中心的安装/升级确认流程完成；保留既有配置和运行数据。`neko-plugin install` 是兼容提示命令，官方 CLI 明确拒绝直接写运行目录。复制 raw Git checkout 或 `sync` 后目录到 `plugin/plugins` 不等于完成包安装。

`--market-release` 在这里仅执行官方结构检查，不发布 Market；它要求 clone 的 origin 保持真实 GitHub 地址，并运行离线测试。

源码宿主开发者另可在支持该功能的插件中心启用开发模式、注册源码目录；这是官方 development-source 注册路径，与普通包导入不同。不要把手动复制目录称为注册，也不要自动给正在使用的宿主注册或启用插件。

修改源码后必须重新执行检查和 `build`，再导入新包；开发模式也沿用宿主正式注册及重新扫描流程。不要改 `plugin.meta.json` 的 `schema_version` / `source_bytes`，也不要关闭源码指纹校验。新包排除 `.bak`、`.orig`、`.rej`；已经装进运行目录的旧备份可能使指纹不匹配，应通过干净包升级，而不是修改统计值。

## 3. 准备精确后端

插件包包含 Python 依赖、适配层、静态 UI 与 YUI 外观；不包含 Node.js、执行核心运行目录、共享观战源码/原版资源、游戏账号、历史或模型密钥。文字运行不需要观战资源。

先完成同步，才能读取固定框架提供的权威核心清单。在构建机或独立后端准备机执行：

```powershell
Set-Location $plugin
$env:PYTHONPATH = (Resolve-Path ./vendor).Path
$corePinPath = & $builderPython -c "from pathlib import Path; import agent_mc_autonomy; print(Path(agent_mc_autonomy.__file__).parent / 'core_distribution/source.json')"
$corePin = Get-Content $corePinPath -Raw | ConvertFrom-Json
$corePin | Select-Object version, source_revision, archive, sha256
# 新的专用核心 checkout；不要切换正在运行或有未提交工作目录的版本。
$coreSource = "$work/minecraft-ai-friend-core"
git clone $corePin.repository $coreSource
git -C $coreSource fetch origin $corePin.source_revision
git -C $coreSource checkout --detach $corePin.source_revision
pnpm --dir "$coreSource/packages/agent-mc-world" fetch
# 当前固定核心工具链为 pnpm 11.5.0 / Node >=22；构建输出目录必须为空。
$coreBuild = "$work/core-build"
& $builderPython backend/build.py --checkout $coreSource --destination $coreBuild
if ($LASTEXITCODE -ne 0) { throw '核心构建失败，请停止' }
$backend = "$work/backend" # 新的空目录，后续在插件配置中填写它的绝对路径
& $builderPython backend/install.py --destination $backend --archive "$coreBuild/$($corePin.archive)"
if ($LASTEXITCODE -ne 0) { throw '精确归档校验或后端安装失败，请停止' }
& $builderPython backend/check_disconnected.py --directory $backend
```

构建工具转发核心官方 `build:engine`，不打插件私有补丁。安装检查归档 SHA256、固定构建凭据、运行文件及类型清单，再以 lockfile 执行 `npm ci --ignore-scripts --omit=dev`；联网或完整缓存时可用，安装器支持显式 `--offline`。工具链不同而无法复现精确哈希时停止核查，不能跳过校验。如果已有完全匹配 `$corePin.archive` / `$corePin.sha256` 的归档，可跳过 clone/build，直接 install/check；不要求存在公开 Release 或私有 ZIP 下载。

独立运行且不使用 Neko 的开发者可跳过 SDK/插件包步骤：在专用 Python 环境执行 `python -m pip install .` 后，令 `$builderPython` 指向该环境解释器，设置 `$work` / `$plugin`；省略上述 `PYTHONPATH=vendor`，同一条 Python 命令会定位已安装框架的清单。

冻结版用户可把在匹配平台准备并核验的后端单独交付到新目录，目标机仍需 Node.js；依赖平台不同应在目标平台重新准备。不要覆盖既有身体运行目录或删除账本。断开检查会启动自己拥有的 MCP 子进程，但拒绝网络、不连接游戏或模型，不证明已登录或可持续生存。

## 4. 可选：公开的原版观战和声音资源

共享仓库是 [jcs130/mc-visual-console](https://github.com/jcs130/mc-visual-console)。原版资源已经在 Git 中，不需要候选维护者提供未公开的视觉/声音 ZIP。使用上一步清单里的 `external_viewer.source_revision`，不能随意改成共享仓库 main 或本机其它 override：

```powershell
$viewer = "$work/mc-visual-console" # 新的专用目录
$viewerPin = $corePin.external_viewer
git clone $viewerPin.repository $viewer
git -C $viewer fetch origin $viewerPin.source_revision
git -C $viewer checkout --detach $viewerPin.source_revision
git -C $viewer rev-parse HEAD
# 只安装 renderer 自己的依赖，不在共享根目录或旧宿主安装依赖。
Set-Location "$viewer/packages/modern-viewer/renderer-src"
npm ci --ignore-scripts --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'renderer 依赖安装失败，请停止' }
Set-Location $viewer
node tools/prepare-viewer-assets.mjs --verify vanilla-1.21.1
$viewerAssets = "$work/viewer-assets" # 新输出，不与源码重叠
node tools/prepare-viewer-assets.mjs vanilla-1.21.1 $viewerAssets
if ($LASTEXITCODE -ne 0) { throw '原始资源校验或准备失败，请停止' }
# 可选声音：先验证，再在另一个新输出目录准备含声音的完整资源。
# node tools/prepare-viewer-assets.mjs --verify java-1.21.1-sounds
# $viewerAssets = "$work/viewer-assets-with-sounds"
# node tools/prepare-viewer-assets.mjs vanilla-1.21.1 $viewerAssets --sounds
```

当前固定提交里 `vanilla-1.21.1` 为 native 资源包，`--sounds` 会选择同版本 `java-1.21.1-sounds`；视觉和声音的每个原始文件都按 `pack.json` 的字节数及 SHA256 校验。`.gitattributes` 的 `-text` 保留资源原始字节。资源准备不启动游戏。该 vanilla 包已带精确 registry；工具的通用 native 提示不代表还要开服导出一份。`npm ci` 也不是完整画面验收。

生成 UTF-8 catalog；注册表摘要从已准备文件计算，避免把移动清单值抄到多份文档：

```powershell
$registryHash = (Get-FileHash "$viewerAssets/registry/block-states.jsonl" -Algorithm SHA256).Hash.ToLowerInvariant()
$catalog = @{ schemaVersion = 1; packs = @(@{
  id = 'vanilla-1.21.1'; minecraftVersion = '1.21.1'; registry = 'vanilla'
  registrySha256 = $registryHash; assetDirectory = $viewerAssets
}) } | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText("$work/viewer-catalog.json", $catalog, [System.Text.UTF8Encoding]::new($false))
```

自定义安装时，插件配置 `viewer.renderer_directory` 为 `$viewer/packages/modern-viewer/renderer-src`，`viewer.catalog_file` 为 `$work/viewer-catalog.json`；两者一起填写。当前框架的有限元数据检查与实际集成都使用 `tools/native-world-preview-host.mjs`。使用完整离线包时留空即可选择已准备缓存；此处只证明资源准备，尚未证明该新目录可现场渲染。

声音资源可选，浏览器还需要用户点击等手势解除自动播放限制；启用资源不代表实际听到了声音或音乐。YUI/YSM 已在插件包中，无需外部下载。未配置或画面不匹配时保留文字状态回退。首次显式启动观战可能构建浏览器资源；完整场景、身体绑定和实际音效须另行验收。

## 5. 配置并显式启动

建议从插件面板配置连接，避免手写本机路径。必须编辑 TOML 时，Windows 路径使用正斜杠 `directory = 'C:/Minecraft/runtime/backend'`，或单引号字面量 `directory = 'C:\Minecraft\runtime\backend'`。双引号字符串内的反斜杠必须写成 `\\`，不能把 `"C:\Users\..."` 直接粘入 TOML。中文及空格可保留。

完整离线包先运行相邻的 `prepare.cmd`，然后导入同版插件；留空后端路径才会使用匹配的准备登记。显式自定义 Node、后端或观战路径不会混用旧缓存。缺失或版本不匹配时应重新准备完整包，不要把历史缓存名称当成正确的安装目录。

导入及保存配置不会开始游戏。按 `config.example.toml` 或 `standalone.example.toml` 填写本人授权的服务器、账号、后端绝对目录及模型绑定；不沿用维护者的私有路径、账号或端点。默认 `plugin_runtime.auto_start=false`、`autonomy.enabled=false`。准备好后才显式允许 autonomy，并使用内嵌 `start` 或独立 `run`；保留显式停止。已有独立进程先正常收尾，再以匹配框架启动，仅重载适配器不会升级旧身体。

离线模式、Microsoft 本人授权、模型与控制语义见 [README](../README.md)；观战验收边界见 [原版观战](vanilla-viewer.md)。[2026-10-16 的 0.5.47 记录](install-candidate-20261016.md) 仅用于历史追溯。
