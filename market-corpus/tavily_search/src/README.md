# Tavily 搜索

通过 Tavily 提供两个可供 Agent 调用的入口：

- `search`：互联网搜索，支持 `general` / `news` 主题与 `basic` / `advanced` 深度。
- `extract_webpage`：提取指定 URL 的 Markdown 网页正文。

## 配置

在插件管理面板中打开配置，为 `tavily.api_key` 填入从 <https://app.tavily.com/home> 创建的 API Key。`max_results` 默认为 5，最多为 20；`timeout_seconds` 默认为 30。

`config.example.toml` 只包含示例值，不能提交真实 API Key。

## Development

This repository is meant to live at:

```text
N.E.K.O/plugin/plugins/tavily_search
```

When publishing to the plugin market, use this GitHub repository name:

```text
n.e.k.o_plugin_tavily_search
```

From the N.E.K.O repository root:

```bash
uv run --with pip python -m plugin.neko_plugin_cli.cli sync tavily_search --clean
uv run python -m plugin.neko_plugin_cli.cli check tavily_search
uv run python -m plugin.neko_plugin_cli.cli check -r tavily_search
```

Python runtime dependencies are declared in `pyproject.toml` and synced into
`vendor/` for packaging. The generated `vendor/` directory is not committed;
local builds and CI recreate it before release checks.

## Market release

Push a tag matching `plugin.toml` version to create a GitHub Release asset:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The generated `.github/workflows/release.yml` uploads `tavily_search.neko-plugin`.
Use that GitHub Release URL when publishing a version in the plugin market.

## Entry

```toml
entry = "plugin.plugins.tavily_search:TavilySearchPlugin"
```
