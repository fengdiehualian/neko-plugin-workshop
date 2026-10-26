# 原神游戏搭子

让猫娘知道你现在在玩哪款游戏，然后换用那款游戏的语境与术语陪你聊。

A companion plugin that switches game context and terminology on demand.

## 它做什么 / What it does

- 登记当前在玩的游戏（原神、鸣潮，可自行扩展）
- 加载该游戏的术语库（`terms/<game>/`，四个维度：core / slang / characters / systems）
- 把一段小语境注入宿主 LLM 上下文（`visibility=[]` + `ai_behavior="read"`，不打断对话）
- 模型按需查术语（`lookup_game_term`），且严格限定在当前游戏内
- 切换游戏时卸载旧索引、加载新索引

## 它不做什么 / What it does not do

- 不检测游戏（不看窗口标题、不扫进程、不截图、不读内存）
- 不操作游戏、不做决策、不自行调用 LLM、不发任何网络请求
- 不读宿主的 `bus`（conversations / frames 属隐私面）

## 入口与工具 / Entries and tools

| 类型 | id | 说明 |
|---|---|---|
| entry | `set_game` | 登记 / 切换当前游戏 |
| entry | `get_current_game` | 查看当前登记的游戏 |
| entry | `list_games` | 列出可用游戏 |
| entry | `refresh_game_context` | 重新注入语境（并重新读一遍术语覆盖层） |
| llm tool | `set_current_game` | 对话里说"我在玩 XX"时由模型调用 |
| llm tool | `lookup_game_term` | 按需查术语，只查当前游戏 |

## 配置 / Configuration

插件清单里的 `[genshin_game_buddy]` 与 `[games.*]` 都可以被用户运行配置覆盖：

```text
<用户数据根目录>/plugins/genshin_game_buddy/config/plugin.toml   # 登记 / 调整游戏
<用户数据根目录>/plugins/genshin_game_buddy/data/terms/<游戏>/   # 覆盖术语（按 key 逐字段合并）
```

面向用户的使用说明见 `docs/quickstart.md`（会显示在插件管理器的指南页）。

## Development

The plugin source and its Git repository live at:

```text
N.E.K.O/plugin/plugins/genshin_game_buddy
```

插件源码及其 Git 仓库直接位于：

```text
N.E.K.O/plugin/plugins/genshin_game_buddy
```

プラグインのソースと Git リポジトリは次の場所にあります：

```text
N.E.K.O/plugin/plugins/genshin_game_buddy
```

When publishing to the plugin market, use this GitHub repository name:

发布到插件市场时，请使用以下 GitHub 仓库名：

プラグインマーケットへ公開する際は、次の GitHub リポジトリ名を使用してください：

```text
n.e.k.o_plugin_genshin_game_buddy
```

From this plugin repository root:

```bash
uvx ruff==0.12.4 check --ignore-noqa --config ruff.toml .
```

From the N.E.K.O repository root / 在 N.E.K.O 仓库根目录中 / N.E.K.O リポジトリのルートで：

```bash
uv run --with pip neko-plugin sync genshin_game_buddy --clean
uv run neko-plugin check genshin_game_buddy
uv run neko-plugin check -r genshin_game_buddy
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
uv run neko-plugin publish genshin_game_buddy
```

To run only one half explicitly / 如需仅执行一部分 / 一方のみを実行する場合:

```bash
uv run neko-plugin publish github genshin_game_buddy
uv run neko-plugin publish market https://github.com/owner/repo/releases/tag/v0.1.0
```

The generated `.github/workflows/release.yml` builds and uploads
`genshin_game_buddy.neko-plugin`. The market independently verifies that Release
before publishing it.

生成的 `.github/workflows/release.yml` 会构建并上传插件包；Market 会独立验证
该 Release 后再发布。

生成された `.github/workflows/release.yml` がプラグインパッケージをビルドして
アップロードし、Market はその Release を独立検証してから公開します。

## Entry

```toml
entry = "plugin.plugins.genshin_game_buddy:GenshinGameBuddyPlugin"
```
