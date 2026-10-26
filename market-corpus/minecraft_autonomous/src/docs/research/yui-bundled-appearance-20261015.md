Owner: minecraft_autonomous 的自带外观资源，以及 mc-visual-console 的人物外观注册与选择边界。

# YUI 自带普通皮肤与 YSM 模型追溯

日期：2026-10-15。仅读取现有源码与专用素材目录、计算文件哈希、检查 ZIP 与 JSON、查看已有预览图。未读取凭据、私人角色/记忆/语音缓存，未调用模型、启动服务或游戏，未修改实现、配置、Git 或原素材。

## 结论与真实位置

找到了用户此前制作的完整 YUI 蓝白洛丽塔资源，统一位于：

**D:/neko-mc-trial/mc-agent-viewer-contrib**

- 普通 Minecraft 皮肤：[skins/yui-lolita-slim.png]，64×64、RGBA、slim，5,610 B；neko-skin.png 是逐字节相同的默认 alias。
- YSM：[models/ysm/neko_yui.zip]，13,358 B；已展开的 [neko_yui/] 是完整原始资源，共 7 文件、98,078 B。
- 可编辑生成来源：[build-neko-yui.mjs]，14,469 B；打包来源：[pack-neko-yui.py]，1,301 B。
- [皮肤预览] 35,695 B；[YSM 预览] 1,230,854 B，已查看，分别展示前/后与前/侧/后三视图，不是运行必需资源。
- 本次范围没有发现另一份独立命名为 yuyi 的 Minecraft/YSM 成品；这里实际 ID 为 neko_yui，材质为 default。

素材仓库读取时 HEAD 为 9cb808b2ea65e65adc02ab4415abc9b7ef220658；皮肤来源提交 f7641acdee4794aeb4b0733168939a37e7eb3260（2026-10-11），YSM 来源提交 cf004bd3fec21de4121e5dbf9fa3d1df27d61a99（2026-10-11）。研究对照的共享查看器源码为 D:/neko-mc-trial/mc-visual-console-upstream-20261014，HEAD 6b8a0ce232ee1f2ad5d59ffba73c13bddb297966；player-models 副本 HEAD d878f21ca8b6d0ad2b5e43eb44ede32f2e390f06。

## 文件、结构与完整性

本次重新计算 SHA-256，和 [neko-yui-manifest.json] 的全部 7 条原始资源记录一致；ZIP 也逐条核对了名称、长度和解压后哈希，零差异，根目录直接包含 ysm.json。不是只相信旧报告。

| 路径（YSM 项相对于 neko_yui/） | 字节 | SHA-256 |
|---|---:|---|
| skins/yui-lolita-slim.png / skins/neko-skin.png | 各 5,610 | f8ed6510dec8647895fb633dde7cad0d3338fe7b39f78e2566d389e4ec33709f |
| ysm.json | 946 | 3d3a820530319531619f8f8a6ef9ef4484b9b412f1ebbd9cb283e7788f06aa68 |
| models/main.json | 64,587 | 1feb1121e6946a899c75ea020b32d01809672010ac043c410e2d049426e58205 |
| models/arm.json | 6,297 | 1ca1332dac123c6c72966660a694b770caea895ea41b95964c6866a27ab6c9e5 |
| animations/main.animation.json | 17,120 | 0cbd59d07acfa203438855d678e077da0af88e751d00e6fba713b1e775771ef5 |
| animations/arm.animation.json | 1,958 | b1973bb324b9e4cc805937dcd6d6baa809507814ba522b7424d1da322671c0c6 |
| animations/extra.animation.json | 1,560 | 5c39123acf2008ec6682a298086a3096f9a3646acdbfb4e66b7252e4f16ae922 |
| textures/default.png | 5,610 | f8ed6510dec8647895fb633dde7cad0d3338fe7b39f78e2566d389e4ec33709f |
| models/ysm/neko_yui.zip | 13,358 | dabb81be3a60a8a9927553e8ca53b975f8c5ab999bd5b71f8083d3e6e861b10e |
| models/ysm/neko-yui-manifest.json | 1,114 | 976bf7361222c92dc9b22676fc0b9f16865e78d34976ab435057504d80189983 |
| models/ysm/build-neko-yui.mjs | 14,469 | 13e1fcc4a949ea57a29396491aeb9308c6e184427dc017d356d207575509365a |
| models/ysm/pack-neko-yui.py | 1,301 | c3b211a7cd1a20bf5f941c6fb607ff1fd6371f15ae1bca601823acdebb9f601c |

结构由实际 JSON 重新统计：

- ysm.json：spec=2，free=true，height_scale=width_scale=0.9，default_texture=default；目标 YSM 2.4.1。
- 主几何：Bedrock format_version=1.12.0，geometry.neko_yui，49 骨、168 cubes；64×64 纹理。
- 第一人称手臂：geometry.neko_yui_arm，8 骨、16 cubes；是独立模型，不含主身体/头。
- 动画：format_version=1.8.0；主动画 31 个，手部条件动作 14 个，extra 3 个。extra 副本同时进入主动画，分别为招手、欢呼、鞠躬。
- 主模型含 Head/ViewLocator/LeftHandLocator/RightHandLocator/ElytraLocator、双马尾及尾巴等组，后续加载须保留原骨骼、UV、pivot、scale 与动画文件。[几何构建]、[动画与元数据]、[验证源码]。

## 制作来源与许可证据

[皮肤 README] L3–9 记录：外观参考 N.E.K.O. yui-lolita，像素贴图由内置 imagegen 生成；prepare-yui-skin.mjs 只做 nearest-neighbor 缩放与二值 alpha，并验证标准 slim 基础 UV 面不透明。它不是从 Live2D 纹理直接裁切的文件。[皮肤导出源码]。

[YSM README] L58–62 与 [动画与元数据] L179–188 记录：jcs130 负责角色方案/模型委托，Codex 制作原创几何、数字关键帧动画及打包；贴图直接复制此前批准的普通皮肤。没有复制默认 YSM 角色网格，没有打包 Live2D moc3、Live2D 纹理或私有运行配置。实际皮肤、alias 与 YSM texture 的哈希完全相同，证实此复制关系。

仓库根 [LICENSE] 是 MIT（Copyright 2024 Kolby Nottingham）；ysm.json metadata.license.type 为 Custom，desc 明确“原创几何与动画按本仓库许可；角色设计参考 N.E.K.O. YUI，未包含其 Live2D 源文件”。这支持按仓库许可复用原创几何/动画，并保留现有元数据与源仓库 MIT 文本。皮肤 README 提供生成来源，当前用户也已明确要求复用此前素材。本研究没有把“参考 YUI 的造型”扩展解释为所有上游角色设计或 Live2D 资产均获 MIT 授权，也不需要为插件复制那些资产。

建议随插件资产保留：原 ysm.json、原制作说明/来源提交/哈希清单、MIT 文本与作者说明。不要把 Custom 改写成 CC0 来通过其他模型的校验。

## 已有加载方式与共享查看器接口

### 普通皮肤

原项目 [browser_viewer.js] L36–45 将 selfSkinPath/selfSkinModel 交 viewer；[viewer_self_skin.js] L4–16 只允许当前连接 bot 的本地 64×64 PNG，限制 128 KiB，校验文件头后生成同源 /head-texture/<sha256>.png 与 skinModel=slim。它不改其他玩家皮肤，也不代表其他原生 Minecraft 客户端已收到皮肤。

这可以作为共享资源注册层的皮肤 loader 参考。应迁移通用受校验加载能力，不复制派生的 modern/host.mjs，也不把旧账户或本机绝对素材路径固化到插件默认配置。

### YSM bundle 与选择器

共享 [viewer-ysm-assets.mjs] L6–23 已提供 decodeYsmBundle：gzip 2 MiB、解压 8 MiB、逐文件哈希/长度/路径检查，格式为 ysm-bedrock-original、ysmVersion=2.4.1，并检查主模型/主动画/贴图/可选动画索引存在。

这里的 assetSha256 是 gzip 传输 bundle 的哈希，和上表的 ZIP 哈希、逐文件哈希不同；注册时应各自保留，不能互相代用。浏览器 asset(bundle) 只作缓存入口，可信本地资源仍应先经过宿主校验。

原素材 [verify-neko-yui.mjs] L75–95 已有通过真实共享 Bedrock parser/动画采样器加载的离线示例，以及把 7 文件转为 files[path]={bytes,sha256,base64} 并构成 bundle 的方法。运行原脚本不是本轮动作；本轮只读取它并独立复算文件/ZIP完整性。

共享 [PaperYsmPlayers] L111–157 提供：

- asset(bundle) 缓存资源；
- choices() 从 bundle.textures 枚举 ysm:<assetSha256>:<texture>；
- select(uuid,id) 要求 UUID 与实际 entity 匹配；
- effective() 记录 selectionSource=web_viewer_override，保留服务器原状态；
- selection(uuid) 返回 available/selected/choices/status/动画限制。

[appearance-settings.js] L1–23、33–62 是可复用 UI，只通知 onChange(id)，不含传输；已有“恢复服务器外观”和断线/切换恢复语义。

**实际缺口**：PaperYsmPlayers.state 目前只接受 source=freesia_worker；原生选择器 [native-appearance.js] 只列锁定 YSM2.6.5 JAR 来源与固定 profile。原生 [native-player-ysm.js] L43–64 还强制 CC0、scale=1 与固定模型定义。当前 YUI 的 Custom、0.9 比例是合法原素材属性，不能硬塞进这套针对官方模型的白名单，也不能伪造 Freesia row 使选择器启用。

## 最小结合方法与所需原文件

建议由 Neko 插件自带资产，向共享观战层注册两个明确来源的条目，例如“YUI 普通皮肤”“YUI YSM”，底层 modelId 保留 neko_yui、texture 保留 default。共享层新增明确的 plugin_bundled/local 资源来源，并复用现有 Bedrock parser、YSM actor/动画和 appearance-settings UI；资源须先按包内清单校验，再进入 choices。

插件应只提供中立注册数据（id、标签、kind、原始相对路径、尺寸、哈希、许可/来源、默认 texture/model 类型），不实现第二套渲染器，不调用服务器管理命令。共享层将选择绑定到真实同连接玩家 UUID/entity/epoch；本页外观保留 web_viewer_override 来源，原始服务器 YSM attachment 不改写。不能用上传截图、重造近似几何或假服务器回执代替原文件。

运行包最少需要：

1. 普通皮肤 PNG 1 文件；skin model 必须为 slim。
2. neko_yui/ 完整 7 文件，包括 arm 与 extra 动画，保留原相对结构。
3. 版本化清单与来源/许可文本。neko-skin.png 与 textures/default.png 为同字节，可在代码层复用文件；原 YSM 文件索引仍须解析到相同字节。
4. ZIP 可作为原生安装交付附带，但 Web 解析需要展开资源或受控 ZIP loader，不能仅把 ZIP 路径当模型 URL。
5. build-neko-yui.mjs、pack-neko-yui.py、prepare-yui-skin.mjs 与生成说明用于维护/复现；不必成为运行依赖。预览图可用于说明，非必需。
6. 不需要 Live2D 源文件、模型权重、Neko 角色配置、旧账户配置、服务器存档或任何语音文件。

普通皮肤的原生客户端可见性仍取决于服务器支持的 skin 上传机制；YSM 原生可见性需要对应服务器/Worker 安装及真实玩家绑定，既有说明给出 custom/neko_yui/ysm.json 与正规 ysm model set 入口。[YSM 安装说明] L12–38 只记录旧环境安装方式和 2026-10-11 的历史 Web 验收，不能作为本次新插件自带能力已部署、当前世界已启用或全部动画已验收的证明。

本轮完成的是素材定位、来源/结构/哈希/ZIP复核与接口研究。后续实现应分别验收包内容、共享层真实注册/选择/恢复、同玩家绑定、WebGL实际外观和动画，以及需要时的原生客户端外观。

## 精确源码入口

[skins/yui-lolita-slim.png]: D:/neko-mc-trial/mc-agent-viewer-contrib/skins/yui-lolita-slim.png
[models/ysm/neko_yui.zip]: D:/neko-mc-trial/mc-agent-viewer-contrib/models/ysm/neko_yui.zip
[neko_yui/]: D:/neko-mc-trial/mc-agent-viewer-contrib/models/ysm/neko_yui/ysm.json:1
[build-neko-yui.mjs]: D:/neko-mc-trial/mc-agent-viewer-contrib/models/ysm/build-neko-yui.mjs:1
[pack-neko-yui.py]: D:/neko-mc-trial/mc-agent-viewer-contrib/models/ysm/pack-neko-yui.py:1
[皮肤预览]: D:/neko-mc-trial/mc-agent-viewer-contrib/skins/yui-lolita-preview.png
[YSM 预览]: D:/neko-mc-trial/mc-agent-viewer-contrib/models/ysm/neko-yui-preview.png
[neko-yui-manifest.json]: D:/neko-mc-trial/mc-agent-viewer-contrib/models/ysm/neko-yui-manifest.json:6
[几何构建]: D:/neko-mc-trial/mc-agent-viewer-contrib/models/ysm/build-neko-yui.mjs:116
[动画与元数据]: D:/neko-mc-trial/mc-agent-viewer-contrib/models/ysm/build-neko-yui.mjs:163
[验证源码]: D:/neko-mc-trial/mc-agent-viewer-contrib/models/ysm/verify-neko-yui.mjs:31
[皮肤 README]: D:/neko-mc-trial/mc-agent-viewer-contrib/skins/README.md:3
[皮肤导出源码]: D:/neko-mc-trial/mc-agent-viewer-contrib/skins/prepare-yui-skin.mjs:10
[YSM README]: D:/neko-mc-trial/mc-agent-viewer-contrib/models/ysm/README.md:58
[YSM 安装说明]: D:/neko-mc-trial/mc-agent-viewer-contrib/models/ysm/README.md:12
[LICENSE]: D:/neko-mc-trial/mc-agent-viewer-contrib/LICENSE:1
[browser_viewer.js]: D:/neko-mc-trial/mc-agent-viewer-contrib/src/agent/vision/browser_viewer.js:36
[viewer_self_skin.js]: D:/neko-mc-trial/mc-agent-viewer-contrib/src/agent/vision/viewer_self_skin.js:4
[viewer-ysm-assets.mjs]: D:/neko-mc-trial/mc-visual-console-upstream-20261014/packages/modern-viewer/renderer-src/host/viewer-ysm-assets.mjs:6
[verify-neko-yui.mjs]: D:/neko-mc-trial/mc-agent-viewer-contrib/models/ysm/verify-neko-yui.mjs:75
[PaperYsmPlayers]: D:/neko-mc-trial/mc-visual-console-upstream-20261014/packages/modern-viewer/renderer-src/src/modern-viewer/paper-ysm.js:111
[appearance-settings.js]: D:/neko-mc-trial/mc-visual-console-upstream-20261014/packages/modern-viewer/renderer-src/src/modern-viewer/appearance-settings.js:1
[native-appearance.js]: D:/neko-mc-trial/mc-visual-console-upstream-20261014/packages/modern-viewer/renderer-src/src/native-viewer/native-appearance.js:7
[native-player-ysm.js]: D:/neko-mc-trial/mc-visual-console-upstream-20261014/packages/modern-viewer/renderer-src/src/native-viewer/native-player-ysm.js:43
