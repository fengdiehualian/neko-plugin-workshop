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
- **能力档位 / 相对弱项 / 周趋势（需登录态）**：成长报告在「薄弱/未涉及标签」之外，
  还会给出**相对自身均值**的薄弱标签与难度分布（避免「一次没 AC 就算弱」的误判）、
  近 N 周趋势，以及按解题证据估算的能力档位——每道 AC 按「难度 × 尝试次数 × 新鲜度」
  加权，用加权中位数（少量样本回退均值）取中心，抑制孤立的难题离群值，再按窗口通过率
  修正，最后按上一次档位缓慢校准（`estimate_ability` entry 的 `recalibrate` 参数），
  档位用洛谷自身的 1-8 难度单位表示。
- **刷题热力图（Hosted UI 第二个面板）**：按天展示 AC **去重**题数（同题重复 AC 只算 1 题）、
  活跃天数、当前/最长连续天数与最佳单日，支持近 3 月 / 半年 / 1 年前端切换；色阶按**你自己
  活跃日的分布排名**而不是固定阈值（「每天 1~3 题」也能看出层次），并按本地时区分日。
  页面只读缓存——UI context 的父进程预算只有约 5 秒——重算由 `refresh_heatmap` 完成，
  首次打开会自动计算一次。
- **冷却式每日题单 + 复习库**：每日题单会记住推荐过的题并按冷却阶梯（14→7→3→1 天）避开，
  只有当整个候选池都填不满题单时才逐档放宽——**先要新鲜、再谈难度精准、最后才允许重复**，
  并把实际使用的难度窗口与冷却档位一并回传，不会假装题单是全新的；难度窗口默认取能力档位
  （向上 2 档、向下 1 档，因为练习要向上够），被饿死时逐档放宽到 1-8。复习库按
  1/2/4/7/15/30/60 天的间隔阶梯调度，四档反馈（forgot 归零 / shaky 原地重复 /
  ok 进一档 / easy 跳一档），到期题数会并入每日题单文本与成长报告。
- **逐题元数据回填**：洛谷提交列表带难度但**不含标签**，所以插件会按需逐题抓取
  `/problem/{pid}` 补齐难度与标签，并把结果缓存进插件 store——每次运行最多回填
  `backfill_max_per_run` 题（客户端限速约 1 请求/秒），失败的题在
  `backfill_backoff_seconds` 后重试，命中缓存的题不再重复请求。提交时间是秒级
  时间戳，入库时归一化为 ISO-8601 UTC 字符串，便于按日期分周与排序。

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
