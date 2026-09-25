# AnySearch 联网搜索（N.E.K.O 插件）

在 N.E.K.O 里接入 [AnySearch](https://anysearch.com) 统一搜索 API，让猫娘在聊天里直接联网搜索，并在面板里可视化检索结果。

- 纯标准库 `urllib` 实现，**零第三方依赖**
- 支持**匿名模式**（无需 Key，按客户端 IP 每日免费额度，约 1000 次/天）与 **API Key 模式**（更高并发与配额）
- 聊天入口：`anysearch_search`（猫娘可直接调用）
- 面板：搜索框 + 区域/数量/内容类型过滤 + 结果卡片（标题/链接/摘要/来源/质量分/正文）+ 最近搜索历史

## 安装

在 N.E.K.O 插件市场搜索 `anysearch` 安装，或手动：

```bash
git clone https://github.com/baixiangyuan/n.e.k.o_plugin_anysearch.git
# 把仓库根目录作为插件目录挂载到 N.E.K.O 插件目录
```

## 配置（plugin.toml 的 `[anysearch]` 段）

| 字段 | 说明 | 默认 |
| --- | --- | --- |
| `api_key` | AnySearch API Key（可选，留空用匿名） | 空 |
| `default_zone` | 区域：`cn` / `intl` | `cn` |
| `default_language` | 偏好语言，如 `zh-CN` / `en` | `zh-CN` |
| `default_max_results` | 默认返回数量 | `5` |
| `search_timeout` | 单次搜索超时（秒） | `15` |
| `max_recent` | 面板保留的最近搜索条数 | `20` |

获取 API Key：https://anysearch.com/console/api-keys

## 在聊天里使用

直接对猫娘说类似「帮我搜一下 xxx」即可触发 `anysearch_search`，返回格式化结果。

## 在面板里使用

打开「AnySearch 联网搜索」面板，输入关键词，可选填区域（cn/intl）、数量、内容类型（web/news/doc/code/academic 逗号分隔），点「搜索」即可看到结果卡片。

## 接口说明

请求：`POST https://api.anysearch.com/v1/search`

```json
{
  "query": "你的问题",
  "max_results": 5,
  "zone": "cn",
  "language": "zh-CN",
  "content_types": ["web", "news"]
}
```

匿名请求不带 `Authorization`；鉴权请求带 `Authorization: Bearer <API_KEY>`。
返回标准 JSON：`results[]`（title/url/description/content/source/score/quality_score/published_at）+ `metadata`。

## 开发

```bash
python -m unittest tests.test_smoke -v
```

## 许可证

MIT
