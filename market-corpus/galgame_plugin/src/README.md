# Galgame游玩助手

让猫娘陪伴你一起玩galgame

## Development

This standalone repository is the source of truth for the plugin. Until the
v1.0.1 market package is accepted, keep the built-in N.E.K.O copy read-only.
Removing that copy is a separate change in the N.E.K.O source tree.

```text
n.e.k.o_plugin_galgame_plugin
```

本独立仓库是插件源码的唯一来源。在 v1.0.1 市场包验收完成前，请保持 N.E.K.O
源码树中的内置副本只读。移除该副本是 N.E.K.O 源码树中的独立改动。

```text
n.e.k.o_plugin_galgame_plugin
```

この独立リポジトリをプラグインの唯一のソースとして使用します。v1.0.1 のマーケット
パッケージが承認されるまで、N.E.K.O の組み込み版は読み取り専用のままにしてください。
削除は N.E.K.O 側で行う別個の変更です。

```text
n.e.k.o_plugin_galgame_plugin
```

When publishing to the plugin market, use this GitHub repository name:

发布到插件市场时，请使用以下 GitHub 仓库名：

プラグインマーケットへ公開する際は、次の GitHub リポジトリ名を使用してください：

```text
n.e.k.o_plugin_galgame_plugin
```

From this plugin repository root:

```bash
uv run pytest
uvx ruff==0.12.4 check --ignore-noqa --config ruff.toml .
```

`uv run pytest` is the standard standalone test command. It uses the Python
version and development dependencies declared by this repository; do not invoke
a `pytest` executable inherited from PATH. PyTorch-dependent ONNX export tests
are optional training checks and are skipped when PyTorch is not installed.

The built-in-to-market test migration ledger is maintained in
[`docs/test-migration-status.md`](docs/test-migration-status.md). Unresolved
plugin-owned entries in that ledger block the v1.0.1 market release even when
the current standalone suite is green.

`uv run pytest` 是独立仓库的标准测试命令，会使用本仓库声明的 Python 版本与
开发依赖；不要直接调用 PATH 中继承的 `pytest`。依赖 PyTorch 的 ONNX 导出测试
属于可选训练检查，未安装 PyTorch 时会跳过。

内置版到市场版的测试迁移清单维护在
[`docs/test-migration-status.md`](docs/test-migration-status.md)。即使当前独立
测试全绿，清单中未解决的插件自有项目仍会阻止 v1.0.1 发布。

From the N.E.K.O repository root / 在 N.E.K.O 仓库根目录中 / N.E.K.O リポジトリのルートで：

```bash
uv run neko-plugin check /path/to/n.e.k.o_plugin_galgame_plugin
uv run neko-plugin check -r /path/to/n.e.k.o_plugin_galgame_plugin
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
uv run neko-plugin publish galgame_plugin
```

To run only one half explicitly / 如需仅执行一部分 / 一方のみを実行する場合:

```bash
uv run neko-plugin publish github galgame_plugin
uv run neko-plugin publish market https://github.com/owner/repo/releases/tag/v1.0.0
```

The generated `.github/workflows/release.yml` builds and uploads
`galgame_plugin.neko-plugin`. The market independently verifies that Release
before publishing it.

生成的 `.github/workflows/release.yml` 会构建并上传插件包；Market 会独立验证
该 Release 后再发布。

生成された `.github/workflows/release.yml` がプラグインパッケージをビルドして
アップロードし、Market はその Release を独立検証してから公開します。

## Entry

```toml
entry = "plugin.plugins.galgame_plugin.plugin_core:GalgamePlugin"
```
