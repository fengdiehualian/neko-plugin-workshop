# 插件商店搜索 N.E.K.O 插件

从 N.E.K.O 插件商店搜索可用插件。支持按名称、标签、作者等关键词搜索，并返回插件的详细信息（评分、下载量、仓库地址等）。

## 功能特性

- **关键词搜索**：通过插件名称、描述、标签、作者名等字段匹配搜索
- **多种排序**：支持按下载量、点赞数、评分、创建时间、名称排序
- **插件详情**：获取指定插件的完整信息（版本、状态、仓库地址等）
- **LLM 工具**：注册 `search_plugins` 工具，AI 可自主搜索插件商店
- **i18n 支持**：内置中文（zh-CN）和英文（en）国际化

## 配置项

通过 WebUI 配置以下字段。默认值定义在 `config.example.toml`，首次创建配置档案时会作为初始值使用。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_base_url` | string | `https://market.project-neko.cn/api/v1` | 插件商店 API 地址 |
| `page_size` | int | `100` | 每次请求获取的插件数量上限 |
| `timeout_seconds` | int | `15` | API 请求超时时间（秒） |

## 插件入口

| Entry ID | 名称 | 说明 |
|----------|------|------|
| `search_plugins` | 搜索插件 | 按关键词搜索插件，支持排序和数量限制 |
| `plugin_detail` | 插件详情 | 通过插件名称或 slug 获取详细信息 |

## LLM 工具

| 工具名 | 说明 | 参数 |
|--------|------|------|
| `search_plugins` | 搜索插件商店 | `keyword`(关键词)、`sort_by`(排序方式)、`limit`(返回数量) |

### 搜索参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `keyword` | string | `""` | 搜索关键词，匹配名称、描述、标签、作者名 |
| `sort_by` | string | `downloads` | 排序方式：`downloads` / `likes` / `rating` / `created_at` / `name` |
| `limit` | int | `10` | 返回结果数量上限（最大 50） |

## 依赖

- `httpx` — 异步 HTTP 客户端
- `plugin.sdk` — N.E.K.O 插件 SDK（>=0.1.0）

## 文件结构

```text
store_search/
├── __init__.py          # 插件主实现
├── plugin.toml          # 插件清单
├── config.example.toml  # 运行时配置默认值
├── pyproject.toml       # Python 运行时依赖声明
├── vendor/              # CLI/CI 在线生成的插件私有依赖（不提交）
├── i18n/                # 国际化资源
│   ├── zh-CN.json
│   └── en.json
├── tests/               # 插件测试
└── README.md            # 本文件
```

## 开发与发布检查

从 N.E.K.O 仓库根目录运行：

```bash
uv run python -m plugin.neko_plugin_cli check store_search
uv run --with pip python -m plugin.neko_plugin_cli sync store_search --clean
uv run python -m plugin.neko_plugin_cli check --release --market-release store_search
```

`.neko-plugin` 包内的顶层 `manifest.toml` 由 CLI 构建时自动生成，不在插件源码中手工维护。
`vendor/` 由本地构建或 CI 在发布检查前根据 `pyproject.toml` 在线生成，不进入 Git。
