# luogu

洛谷 (Luogu) 插件 —— 面向算法竞赛刷题的**个性化成长助手**：基于登录用户本人的提交记录
做成长分析（AC/通过率、薄弱与未涉及标签），每天生成针对性题单，并提供点到为止的解题
思路提示（不含代码）；同时覆盖查题/详情/比赛/用户公开资料等基础查询。**成为OIer吧！**

## 功能

- **搜索题目**：关键词 / 难度 / 标签筛选洛谷题库。
- **题目详情**：题面、难度、标签、通过/提交数。
- **比赛列表**：近期洛谷比赛。
- **用户资料**：公开主页信息。
- **提交记录 / 成长报告 / 每日题单（需登录态）**：统计薄弱/未涉及标签，按难度区间
  与当日确定性 seed 产出当天推荐题单，并通过 `push_message` 注入主 AI，
  可选经 `push_entries` 跨插件发到 QQ 群/私聊。

## 配置

登录 Cookie 在插件 Hosted UI 里填写（F12 → 应用 → Cookie 复制 `_uid`、`__client_id`）。
非敏感配置见下方 `config.example.toml`。

## Development

This directory is both the editable plugin source and its Git repository.

当前目录既是可编辑的插件源码，也是插件自己的 Git 仓库。

このディレクトリは、編集するプラグインソースであり、プラグイン自身の Git リポジトリでもあります。

When publishing to the plugin market, use this GitHub repository name:

发布到插件市场时，请使用以下 GitHub 仓库名：

プラグインマーケットへ公開する際は、次の GitHub リポジトリ名を使用してください：

```text
n.e.k.o_plugin_luogu
```

From this plugin repository root:

```bash
uvx ruff==0.12.4 check --ignore-noqa --config ruff.toml .
```

From this plugin repository root / 在当前插件仓库根目录中 / このプラグインリポジトリのルートで：

```bash
uv run --with pip --project "../N.E.K.O" neko-plugin sync . --clean
uv run --project "../N.E.K.O" neko-plugin check .
uv run --project "../N.E.K.O" neko-plugin check -r .
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
uv run --project "../N.E.K.O" neko-plugin publish .
```

To run only one half explicitly / 如需仅执行一部分 / 一方のみを実行する場合:

```bash
uv run --project "../N.E.K.O" neko-plugin publish github .
uv run --project "../N.E.K.O" neko-plugin publish market https://github.com/owner/repo/releases/tag/v0.1.0
```

The generated `.github/workflows/release.yml` builds and uploads
`luogu.neko-plugin`. The market independently verifies that Release
before publishing it.

生成的 `.github/workflows/release.yml` 会构建并上传插件包；Market 会独立验证
该 Release 后再发布。

生成された `.github/workflows/release.yml` がプラグインパッケージをビルドして
アップロードし、Market はその Release を独立検証してから公開します。

## Entry

```toml
entry = "plugin.plugins.luogu:LuoguPlugin"
```
