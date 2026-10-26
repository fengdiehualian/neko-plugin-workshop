# 太空发射（Space Launch）

<p align="center">
  <img src="assets/icon.jpg" alt="星河拓航Studio Galaxy Exploration Studio" width="180">
</p>

<p align="center">
  由 <b>星河拓航Studio</b>（Galaxy Exploration Studio）开发与维护
</p>

查询全球**下一次**以及未来即将进行的太空发射任务，并支持跨来源检索航天资料：
航天器、火箭型号、空间站、航天机构、宇航员、中文百科条目、国内卫星百科
（灰机wiki）、NASA 技术文献，以及网页兜底搜索。

数据来源：

| 来源 | 用途 |
| --- | --- |
| [Launch Library 2 API](https://thespacedevs.com/llapi) | 发射计划、航天器、火箭、机构、宇航员（The Space Devs，免费开放） |
| [NASA NTRS](https://ntrs.nasa.gov/) | NASA 技术报告与会议论文（纯文字文献） |
| [百度百科](https://baike.baidu.com/) | 中文航天条目（国内可直连） |
| [卫星百科（灰机wiki）](https://sat.huijiwiki.com/) | 中文航天百科条目：火箭、卫星、航天器、发射场、机构等（国内可直连） |
| [Bing](https://cn.bing.com/) | 上述来源都查不到时的网页兜底搜索 |

## 功能

| 入口 | 名称 | 说明 |
| --- | --- | --- |
| `next_launch` | 下一次太空发射 | 返回下一次发射的任务名、火箭、服务商、本地时间、倒计时、发射场与任务简介 |
| `upcoming_launches` | 即将进行的发射列表 | 返回未来 1–20 次发射，可按服务商 / 火箭 / 发射场筛选 |
| `search_space_objects` | 航天器与机构检索 | 在 LL2 中检索航天器 / 火箭型号 / 空间站 / 航天机构 / 宇航员 |
| `search_nasa_documents` | NASA 技术文献检索 | 在 NASA NTRS 中检索技术报告与会议论文（纯文字，含摘要与全文链接） |
| `baike_search` | 中文百科检索 | 在百度百科中检索中文航天条目 |
| `sat_wiki_search` | 卫星百科检索 | 在卫星百科（灰机wiki / sat.huijiwiki.com）中检索中文航天条目 |
| `web_fallback_search` | 网页兜底搜索 | 前几个来源都查不到时，用 Bing 做一次网页搜索 |
| `launch_stats` | 发射数据概览 | 返回即将进行的发射总数与当前 API 配置 |

`search_space_objects` 的 `category` 参数可选值：

| category | 检索对象 | 示例 |
| --- | --- | --- |
| `spacecraft`（默认） | 航天器 | Cargo Dragon 2 |
| `launcher` | 火箭型号 | Falcon 9 |
| `station` | 空间站 | ISS |
| `agency` | 航天机构 | NASA、CNSA |
| `astronaut` | 宇航员 | — |

同时注册了五个供猫娘在对话中主动调用的 LLM 工具：

| 工具 | 回答什么问题 |
| --- | --- |
| `get_next_space_launch` | “下一次火箭发射是什么时候” |
| `list_upcoming_space_launches` | “接下来有哪些发射任务” |
| `search_space_knowledge` | “猎鹰九号是什么火箭”“国际空间站的资料” |
| `search_nasa_literature` | “某项航天技术的原理 / 研究资料” |
| `lookup_satellite_wiki` | “风云四号是什么卫星”“文昌发射场”等卫星百科资料 |
| `lookup_space_info` | 综合检索：先在 LL2、百度百科、卫星百科里找，都没有再走网页兜底 |

## 目录结构

```
space_launch/
├── plugin.toml            # 插件清单（ID、入口类、默认业务配置）
├── config.example.toml    # 用户配置模板
├── pyproject.toml         # 项目元数据（供 neko-plugin CLI 使用）
├── __init__.py            # 插件实现
├── README.md
├── LICENSE                # GNU General Public License v3.0
├── ruff.toml              # 代码检查配置
├── assets/
│   └── icon.jpg           # 星河拓航Studio 图标
├── tests/
│   └── test_smoke.py
└── .github/workflows/     # 标准 CI（verify.yml / release.yml）
```

## 配置

首次运行时，N.E.K.O 会把 `config.example.toml` 复制到用户数据目录的
`config/plugin.toml`。可配置项位于 `[space_launch]` 段：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `api_base_url` | `https://ll.thespacedevs.com/2.3.0/` | LL2 接口基础地址 |
| `ntrs_base_url` | `https://ntrs.nasa.gov/` | NASA NTRS 接口基础地址 |
| `baike_base_url` | `https://baike.baidu.com/` | 百度百科基础地址（中文航天资料） |
| `bing_base_url` | `https://cn.bing.com/` | 网页兜底搜索基础地址 |
| `huijiwiki_base_url` | `https://sat.huijiwiki.com/` | 卫星百科基础地址（灰机wiki / 中文航天资料） |
| `timeout_seconds` | `15` | 单次请求超时（3–60 秒） |
| `default_limit` | `5` | 列表默认返回条数 |
| `include_descriptions` | `true` | 是否附带任务描述 |
| `cache_ttl_seconds` | `300` | 响应内存缓存时间 |
| `auto_brief_enabled` | `true` | 自动播报：在后台定期把「下一次发射」写进角色的上下文 |
| `auto_brief_interval_seconds` | `180` | 自动播报间隔（30–3600 秒） |

### 自动播报：为什么它不用你开口

以前是问答式：你问一句，猫娘才去查一次接口。现在换成自动播报 —— 插件在
后台按间隔查一次「下一次发射」，把结果写进她的上下文里。做法是
`push_message(ai_behavior="read")`：消息会进模型的上下文，但**不会打断对话、
不会突然插话**；等你下次开口时，她已经知道下一次发射是什么、什么时候、在哪儿。

不查游戏、也不需要游戏在跑 —— 这个插件本来就是联网查发射计划的。

几个细节：

- **同键合并**（`coalesce_key`）：队列里同一时刻只保留最新一条，不会越堆越长。
- **查不到就不说话**：接口失败或没有即将发射的任务时保持沉默，不刷「查不到」
  的噪音；本来是好的、突然连不上了，才播报一次提醒旧数据可能过时。
- **默认 180 秒**：LL2 免费额度约每小时 15 次，加上插件内 300 秒缓存，
  这个间隔实际每小时只打十几次接口。想更实时可以调小到 30 秒。
- 想退回问答式就把 `auto_brief_enabled` 关掉。

### 关于请求频率

Launch Library 2 的免费额度约为**每小时 15 次请求**。插件因此内置了带 TTL 的
内存缓存，默认 5 分钟内相同查询不会重复打接口。如果你需要更高频率，请在
[The Space Devs 的 Patreon](https://thespacedevs.com/supportus) 申请更高额度，
并把 `cache_ttl_seconds` 调小（不建议低于 60）。

## 入口参数

`next_launch`：

```json
{ "provider": "SpaceX", "rocket": "Falcon 9", "location": "Cape Canaveral" }
```

三个参数都是可选的关键字过滤，留空表示不限制。筛选在插件本地完成，
因此不会因为额外的查询参数增加 API 调用。

`upcoming_launches`：

```json
{ "limit": 5, "provider": "", "rocket": "", "location": "" }
```

## 卫星百科检索示例

`sat_wiki_search` 与 `lookup_satellite_wiki` 都走灰机wiki 的 MediaWiki
标准接口 `api.php`（`action=query&list=search`），返回结构化 JSON。

```json
{ "query": "长征一号", "limit": 3 }
```

命中后的 `results` 形如：

```json
{
  "found": true,
  "source": "卫星百科（灰机wiki）",
  "summary": "卫星百科（灰机wiki）中与「长征一号」相关的 2 个条目：\n1. 长征一号\n   我国第一款成功入轨的运载火箭…\n2. 长征一号丙\n   …",
  "results": [
    {
      "source": "卫星百科（灰机wiki）",
      "title": "长征一号",
      "snippet": "我国第一款成功入轨的运载火箭，1970 年将东方红一号送入太空。",
      "url": "https://sat.huijiwiki.com/wiki/%E9%95%BF%E5%BE%81%E4%B8%80%E5%8F%B7",
      "pageid": 3625
    }
  ],
  "count": 2,
  "total": 2
}
```

几点说明：

- **摘要已清洗**：灰机wiki 的搜索摘要带 `<span class="searchmatch">` 高亮标签
  和 HTML 实体，插件会去标签、反转义后再返回，便于直接朗读。
- **链接是规范链接**：由条目标题 URL 编码拼成 `/wiki/<编码标题>`，
  不依赖搜索结果里可能带跳转参数的原始链接。
- **`lookup_space_info` 的调用顺序**：航天数据库（LL2 航天器 → LL2 火箭型号）
  → 百度百科 → **卫星百科（灰机wiki）** → 网页兜底。任一来源命中即返回，
  所以卫星百科只在百度百科也没收录时才会被请求。
- **风控兜底**：机房 / 代理 IP 请求灰机wiki 常被 Cloudflare 拦成 HTTP 403
  挑战页。插件识别到这种情况会明确返回「站点风控拦截了本次请求」，
  并建议改用 `baike_search`，**不会**把风控页当作「未收录」，
  也不会用旧缓存或猜测内容顶替。

> **待补充**：`sat_wiki_search` 命中效果的实测截图需要在国内家用网络下运行后截取
> （机房 / 代理 IP 会被灰机wiki 的 Cloudflare 风控拦掉，截不到有效结果）。
> 截好后放到 [`assets/`](assets/) 目录，并把图片引用补到这一行。
## 返回示例

`next_launch` 的 `summary` 字段是可直接朗读的中文描述：

> 下一次太空发射是「Falcon 9 Block 5 | USSF-259」，使用 Falcon 9 Block 5 火箭，
> 由 SpaceX 执行，计划于 2026-09-17 09:00:00 中国标准时间发射，发射场：
> Vandenberg SFB, CA, USA（还有 5 小时 3 分钟）。当前状态：Go for Launch，
> 任务类型：Government/Top Secret。

结构化的 `launch` 字段包含：`name`、`rocket`、`provider`、`mission`、
`mission_type`、`orbit`、`pad`、`location`、`country`、`net_utc`、`net_local`、
`window_start_local`、`window_end_local`、`countdown`、`countdown_seconds`、
`status`、`webcast_live`、`url`、`image`。

## 开发与测试

```bash
uv run neko-plugin check space_launch
uv run pytest plugin/plugins/space_launch/tests -q
```

在 N.E.K.O. 的插件页面刷新列表后启动插件，切换到「入口点」即可触发
**下一次太空发射**。

## 关于「不会瞎编」

航天资料最怕模型**编**——编出来的推力、尺寸、服役时间看着都很像真的，但会误导人。
所以插件在做不到的时候会明确说做不到，而不是让模型自由发挥：

- **查不到条目** → 返回 `found: false` 并附一段明确引导：
  「本地数据库没有收录这个条目……请直接告诉用户『我这边没有查到』，
  绝对不要凭猜测描述它的型号、尺寸、推力、服役时间等具体信息。」
- **搜索结果不相关** → 会被相关性过滤掉。例如搜「UR-700A」时 Bing 返回的是
  服装品牌 URBAN REVIVO 的页面，这类命中会被丢弃而不是当作结果交上去
- **接口失败 / 超时** → 如实返回错误，不会用上一次的缓存冒充新数据

## 关于星河拓航Studio

本插件由 **星河拓航Studio**（Galaxy Exploration Studio）开发与维护。

星河拓航Studio 是一个由来自五湖四海的航天爱好者组成的非正式线上航天科普组织，
成员多为在校学生。我们希望通过有趣、可靠的方式，让更多人了解真实的航天发射任务。

- 官方网站：<https://xhth.top/>
- B 站主页：<https://space.bilibili.com/3546949529635067>
- 联系邮箱：<contact@xhth.top>

欢迎各位同志加入我们，也欢迎反馈插件的问题与建议。

## 说明

- 数据版权归 [The Space Devs](https://thespacedevs.com/) 所有，本插件仅做查询展示。
- 发射时间（`net`）会随任务动态调整，插件展示的是查询时刻 API 返回的最新值。
- `launches/upcoming/` 端点偶尔会返回刚刚发射完、状态尚未刷新的任务（例如已标记为
  `Launch Successful` 却仍排在列表开头的记录）。插件会过滤掉这些条目，确保
  「下一次太空发射」指向的确实是还没发射的任务；发射时间待定的任务排在列表末尾。
- 只使用**国内可直连**的来源（LL2 / NASA NTRS / 百度百科 / 卫星百科（灰机wiki）/ Bing）。
  Wikipedia 与 Wikidata 因需要代理，未被纳入。
- 卫星百科（灰机wiki）开启了 Cloudflare 托管挑战；被拦截时接口返回 **HTTP 403
  挑战页**（`cf-mitigated: challenge`）。插件会识别为风控并如实返回
  「站点风控拦截了本次请求」，建议改用 `baike_search`，不会用旧缓存冒充或编造内容。
  该风控主要针对机房 / 代理 IP，国内家用宽带一般可正常访问。

## 许可证

本项目采用 [GNU General Public License v3.0](LICENSE)（GPL-3.0）许可。

你可以自由地使用、修改和分发本插件；但**如果你分发修改后的版本，必须以同样的
GPL-3.0 协议开源你的修改**，并保留原有的版权声明。

```
Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
```

本程序为自由软件，但**不提供任何担保**。完整条款见 [LICENSE](LICENSE)。
