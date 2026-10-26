# 历史记录：0.5.47 内部候选安装说明（2026-10-16）

> 本页只保留当时的版本与本地资产记录，不是当前安装指南。现行步骤见 [从公开源码安装](install-source.md)；共享 Git 已包含公开原始资源，不需要取得下文历史 ZIP。

当时只有本地待发布资产，没有已发布下载地址。请向候选维护者取得下列原始文件；公开发布授权后才能补上不可变下载链接。不要把本说明当成任意干净机器或长期生存、完整 3D 画面已经验收的证明。

## 准备固定后端

要求 Python 3.11+、Node.js >=22 与 npm。插件安装后使用宿主管理的 Python 解释器，避免调用另一个未安装依赖的系统 Python。插件固定框架 `agent-mc-autonomy 0.1.67`，源码 `minecraft-ai-friend@18c1758705b3ffafe217bae235c41b35b9808118`；核心 `0.5.7-dev.20261016.9`，源码 `ac08d3fcc1e1cc7fe736922afeada8fd5757ac8a`。该固定依赖提供紧凑检查、原始证据分页、独立公开人格绑定与同一主脑意图口播；检查不会确认策略，最终判断需先完成当前检查，待复盘死亡的最终判断须携带有依据的教训或明确暂缓原因；0.5.47 尚未部署或发布，现场与观战升级另行验收。

取得 `agent-mc-world-0.5.7-dev.20261016.9.tgz`，SHA256 必须为 `32f356a7a7d589bf469b638b697f7d16880f708e06bb5f66586f8fbd52dfe574`。在插件安装目录执行（路径全部替换成自己机器上的真实路径）：

```powershell
$env:PYTHONPATH = 'C:/Neko/plugin/plugins/minecraft_autonomous/vendor'
& 'C:/Neko/.venv/Scripts/python.exe' 'C:/Neko/plugin/plugins/minecraft_autonomous/backend/install.py' --destination 'C:/MinecraftAgent/backend-20261016' --archive 'C:/Downloads/agent-mc-world-0.5.7-dev.20261016.9.tgz'
```

安装器检查归档 SHA、构建凭据、执行文件和类型清单，并运行 `npm ci --ignore-scripts --omit=dev --no-audit --no-fund`。通常需要联网取得固定 npm 依赖；已有完整缓存时可显式加 `--offline`。没有承诺提供离线依赖缓存包。使用空的专用目标目录，不覆盖现有私有运行目录。在插件设置中填入该后端绝对目录；默认启动与 autonomy 均关闭，安装不会连接游戏。

已有独立 body 使用旧版框架（包括 0.1.66）时，须先通过现有控制流程正常收尾，再使用固定的 0.1.67 启动该 body；仅重载 0.5.47 适配器不会升级独立进程。新旧框架仍共用上述核心 .9；0.1.67 才能读取独立静态人格投影，并继续保护并发提交的死亡反思与完成回执，不重投旧目标或口播。

## 可选观战资源

文字模式不需要渲染器。需要观战时取得以下独立伴随资产，不要把它们塞进薄插件包：

| 文件 | 字节数 | SHA256 |
| --- | ---: | --- |
| `mc-visual-console-6265b4f3-vanilla1.21.1.zip` | 9160334 | `ab987aaff6b887a048028e04caaaa14854ee57f4ee6fa30929350e64a1eaccdc` |
| `mc-visual-console-8f5abc63-sounds1.21.1.zip`（可选声音，约 714 MB） | 713993073 | `ade4821717da2e8902cdf69e37e6cb3fb75ccb0691dd7dcf3eaa1e4e9adae41b` |

共享查看器固定源码 `mc-visual-console@6265b4f3cc0dff1489a31484015f9cbb373c010c`，303 个源码文件摘要 `5bb682f0db05254cf6ead85809b2183a795f420038cc1eb6fa6004edc7f6f334`。也可使用该精确提交的源码 checkout，但运行目录应使用固定提交的独立副本，避免运行时构建读到正在修改的文件。仍需匹配版本资源。ZIP 内共同根目录为 `mc-visual-console/`；解压视觉包到专用工作目录。原版声音资源未改动，沿用上表经校验的声音包。若需要声音，将可选声音 ZIP 解压到同一父目录，合并相同根前缀；不能使用其它版本声音替代。

先在共享仓库 `packages/modern-viewer/renderer-src` 安装明确的渲染器依赖，然后回到共享仓库根目录运行准备工具，不能在旧宿主或根目录安装 npm 依赖：

```powershell
Set-Location 'C:/MinecraftAgent/mc-visual-console/packages/modern-viewer/renderer-src'
npm ci --ignore-scripts --no-audit --no-fund
Set-Location 'C:/MinecraftAgent/mc-visual-console'
node tools/prepare-viewer-assets.mjs --verify vanilla-1.21.1
node tools/prepare-viewer-assets.mjs vanilla-1.21.1 'C:/MinecraftAgent/viewer-assets'
# 只有解压了匹配的声音包才选择：
# node tools/prepare-viewer-assets.mjs vanilla-1.21.1 'C:/MinecraftAgent/viewer-assets' --sounds
```

npm 需要联网或完整已有缓存；离线可显式加 `--offline`。该 vanilla 包已包含精确原版 1.21.1 registry，不需要额外启动服务器或重新导出 registry。准备工具可能输出通用 registry 提示，应使用本包已带的版本资源。原生宿主会在第一次显式启动观战时构建浏览器资源；`npm ci` 不是渲染验收。

保存 UTF-8 的 `C:/MinecraftAgent/viewer-catalog.json`：

```json
{
  "schemaVersion": 1,
  "packs": [{
    "id": "vanilla-1.21.1",
    "minecraftVersion": "1.21.1",
    "registry": "vanilla",
    "registrySha256": "55893d269459e1e05387903c77b2db877d7cece875ed31a5a6a2f965822ea546",
    "assetDirectory": "./viewer-assets"
  }]
}
```

插件设置 `viewer.renderer_directory` 为 `C:/MinecraftAgent/mc-visual-console/packages/modern-viewer/renderer-src`；`viewer.catalog_file` 为上述 catalog 绝对路径。两者一起填写或一起留空；资产相对目录以 catalog 所在目录为基准。安装面板只读有限元数据，不扫描全部资产哈希。显示版本匹配仅代表可尝试渲染，不证明资源完整、角色绑定或场景正确；显式启动另行执行完整后端校验。没有匹配画面时保留文字回退。

## 连接与本人授权

本机地址通常填 `127.0.0.1`、端口 `25565`；远程域名/IP 选择自定义连接。地址和账户都必须填。千灯纪预设只修改草稿中的地址、端口与版本，保留用户昵称及认证选择；空账户仍须本人填写。保存不会启动或连接。

离线昵称为 1–16 位字母、数字或下划线，不能登录要求正版验证的服务器。Microsoft 需填写账户标识并由本人授权：可在保存前选一个空闲的 `1024–65535` 本机账户端口，显式启动后在运行机器打开 `127.0.0.1` 账户页；若保持端口 `0`，由本人查看私有原生设备授权日志。授权码和令牌不进入观战页，不放入公开配置。首次授权、取消和再次启动仍需干净账户实测。

默认使用宿主 decision 模型；vision 缺失或失败使用文字，fast 默认规则。随包 YUI slim/YSM 只影响 Web 外观，默认跟随服务器；不修改正版账号或服务器皮肤。

## 已有证据与剩余验收

固定提交构建、有限元数据与拒绝保存回归、官方结构检查属于离线证据。以下资源准备和部署记录属于 0.5.44 历史证据，不代表 0.5.47 已部署或通过现场验收。新目录视觉 ZIP 解压和 npm 缓存安装完成：8456 个原始资源校验通过，浏览器包与 registry 已准备；声音伴随包 3712 个资源校验与合并准备通过，资源准备未监听端口或创建游戏客户端。因为复用了 npm 缓存，这不是任意干净机器保证。0.5.44 实际托管面板已验证完整草稿保存、非法端口字段提示及预设草稿取消；正常排空后已部署匹配核心和固定观战副本，并核对实际服务构建摘要。本人账号授权、移动场景的完整表现、持续自主生活与模型回退仍须分别验收。生存测试仍有死亡及反复搜寻，不能公开宣称长期生存通过。性能复放与验收边界见 [本次记录](performance-release-20261016.md)。候选资产只暂存本地；未创建 tag、GitHub Release 或 Market 提交。
