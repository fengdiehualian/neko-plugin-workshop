# 喵宇宙社区聊天

让 YUI 接入喵宇宙社区的本地多人聊天插件：本地跑一个消息服务（127.0.0.1:7870），
提供类似 QQ 的聊天面板——打开 http://127.0.0.1:7870/chat，
与 YUI 实时对话（后台唤醒 YUI、YUI 用 community_chat_send 回消息），聊天历史存本地（%LOCALAPPDATA%/N.E.K.O/neko_community_chat）。
全球互通：公共 MQTT 通道（零配置，多 broker 自动回退）+ 局域网自动发现 + 自建 Cloudflare 中继（自动跟随系统代理）。
面板内嵌社区动态流（自建社区墙，所有节点发帖）。
何时用：用户说'和YUI聊天/社区聊天/喵宇宙聊天/打开聊天面板/社区动态/看看社区有什么新动态'时。
回复纪律：直接给出消息内容或操作结果，不编造聊天记录、不假装已发送未发出的消息。

## Development

The plugin source and its Git repository live at:

```text
N.E.K.O/plugin/plugins/neko_community_chat
```

插件源码及其 Git 仓库直接位于：

```text
N.E.K.O/plugin/plugins/neko_community_chat
```

プラグインのソースと Git リポジトリは次の場所にあります：

```text
N.E.K.O/plugin/plugins/neko_community_chat
```

When publishing to the plugin market, use this GitHub repository name:

发布到插件市场时，请使用以下 GitHub 仓库名：

プラグインマーケットへ公開する際は、次の GitHub リポジトリ名を使用してください：

```text
n.e.k.o_plugin_neko_community_chat
```

From this plugin repository root:

```bash
uvx ruff==0.12.4 check --ignore-noqa --config ruff.toml .
```

From the N.E.K.O repository root / 在 N.E.K.O 仓库根目录中 / N.E.K.O リポジトリのルートで：

```bash
uv run neko-plugin sync neko_community_chat --clean
uv run neko-plugin check neko_community_chat
uv run neko-plugin check -r neko_community_chat
```

Python runtime dependencies are declared in `pyproject.toml` and synced into
`vendor/` for packaging. The generated `vendor/` directory is not committed;
local builds and CI recreate it before release checks.

Python 运行时依赖声明在 `pyproject.toml` 中，并在打包时同步到 `vendor/`。
生成的 `vendor/` 不提交；本地构建和 CI 会在发布检查前重新生成它。

Python ランタイム依存関係は `pyproject.toml` に宣言し、パッケージ化時に
`vendor/` へ同期します。生成された `vendor/` はコミットせず、ローカルビルドと
CI が公開前チェックで再生成します。

## Market release / Market 发布 / Market 公開

Publish the version declared in `plugin.toml`. By default this pushes the Git
tag, waits for the standard GitHub Release, and notifies the plugin market.

发布 `plugin.toml` 中声明的版本。默认会推送 Git tag、等待标准 GitHub
Release，然后通知插件市场。

`plugin.toml` で宣言されたバージョンを公開します。既定では Git tag を
push し、標準 GitHub Release を待ってからプラグインマーケットへ通知します。

```bash
uv run neko-plugin publish neko_community_chat
```

To run only one half explicitly / 如需仅执行一部分 / 一方のみを実行する場合:

```bash
uv run neko-plugin publish github neko_community_chat
uv run neko-plugin publish market https://github.com/owner/repo/releases/tag/v0.1.0
```

The generated `.github/workflows/release.yml` builds and uploads
`neko_community_chat.neko-plugin`. The market independently verifies that Release
before publishing it.

生成的 `.github/workflows/release.yml` 会构建并上传插件包；Market 会独立验证
该 Release 后再发布。

生成された `.github/workflows/release.yml` がプラグインパッケージをビルドして
アップロードし、Market はその Release を独立検証してから公開します。

## Entry

```toml
entry = "plugin.plugins.neko_community_chat:NekoCommunityChatPlugin"
```
