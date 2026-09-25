# NEKO Speak Serve

主要功能是让外部程序控制N.E.K.O。

现在实现了N.E.K.O→DSH→N.E.K.O和DSH→N.E.K.O的对话顺序。

因为一些原因这里暂时只做了对DSH框架的适配，DSH侧插件见https://github.com/xy28816/dsh-external-brain

N.E.K.O侧发起的对话是通过修改对话模型配置实现的(在入口点选项里控制，可以修改回来，记得重新加载软件，出大问题了的话，在api配置的页面里改一下就可以完整恢复），返回N.E.K.O的信息时可以这样理解：它是一个绑定在本机127.0.0.1:48930的小HTTP服务器，谁POST文本进来，它就把这段文本塞进N.E.K.O的对话管线，让TA说话。

可能会有一堆奇奇怪怪的gub，但是至少基础的对话已经可以用了.

有一个叫“给AI的”的文档，可以单独下载下来喂给ai教他怎么用，（这个本来应该放在另一半的，但是重新上传感觉有点麻烦就放这里了）

（应该是我电脑性能的原因吧，打开这些软件后DSH回复时间特别长开始，时间已经可以按分钟算了，以至于放了一段时间后甚至堆积了近百条未处理的消息qwq)

(我好像记得还要写点什么的...算了，想起来再写吧。）

## Development

The plugin source and its Git repository live at:

```text
N.E.K.O/plugin/plugins/neko_mcp_serve
```

插件源码及其 Git 仓库直接位于：

```text
N.E.K.O/plugin/plugins/neko_mcp_serve
```

プラグインのソースと Git リポジトリは次の場所にあります：

```text
N.E.K.O/plugin/plugins/neko_mcp_serve
```

When publishing to the plugin market, use this GitHub repository name:

发布到插件市场时，请使用以下 GitHub 仓库名：

プラグインマーケットへ公開する際は、次の GitHub リポジトリ名を使用してください：

```text
n.e.k.o_plugin_neko_mcp_serve
```

From this plugin repository root:

```bash
uvx ruff==0.12.4 check --ignore-noqa --config ruff.toml .
```

From the N.E.K.O repository root / 在 N.E.K.O 仓库根目录中 / N.E.K.O リポジトリのルートで：

```bash
uv run --with pip neko-plugin sync neko_mcp_serve --clean
uv run neko-plugin check neko_mcp_serve
uv run neko-plugin check -r neko_mcp_serve
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
uv run neko-plugin publish neko_mcp_serve
```

To run only one half explicitly / 如需仅执行一部分 / 一方のみを実行する場合:

```bash
uv run neko-plugin publish github neko_mcp_serve
uv run neko-plugin publish market https://github.com/owner/repo/releases/tag/v0.1.0
```

The generated `.github/workflows/release.yml` builds and uploads
`neko_mcp_serve.neko-plugin`. The market independently verifies that Release
before publishing it.

生成的 `.github/workflows/release.yml` 会构建并上传插件包；Market 会独立验证
该 Release 后再发布。

生成された `.github/workflows/release.yml` がプラグインパッケージをビルドして
アップロードし、Market はその Release を独立検証してから公開します。

## Entry

```toml
entry = "plugin.plugins.neko_mcp_serve:NekoMcpServePlugin"
```
