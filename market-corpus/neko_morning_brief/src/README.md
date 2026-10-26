# 猫娘晨报 · neko_morning_brief

给 N.E.K.O 的定时简报插件。到点把你在配置里写好的信息需求交给猫娘，由她自己抓取、筛选，再用自己的语气汇报。

- **配置极简**：一个时间 + 若干同级信息源，一行一个，例如 `通过 http://xxx 获取 xx 数据，cookie=xxx（如有）`
- **抓取交给模型**：插件只提供 `fetch_url` 工具，抓哪个、怎么总结由模型决定
- **什么都能接**：RSS、JSON 接口、网页、内网地址
- **失败不静默**：取不到会在汇报里说明，不会假装成功

## 它怎么工作

```
到点（默认 08:00）
  └─ 插件把配置里的需求原文交给模型
       └─ 模型按每条需求调用 fetch_url(url, cookie?)
            └─ 插件抓取并归一化成标题 / 条目 / 摘要
                 └─ 模型挑重点，用自己的语气汇报
```

插件负责三件事：**定时、抓取、把内容压成模型好读的形状**。判断和表达都交给模型。

## 安装

- **插件市场**：搜索「猫娘晨报」安装（审核上架后）；
- **本地安装包**：插件管理器 → 包管理 → 导入 `neko_morning_brief.neko-plugin`。

首次启动会在用户数据目录生成配置：

```
%LOCALAPPDATA%\N.E.K.O\plugins\neko_morning_brief\config\plugin.toml
```

## 配置

`time` 和每个信息源在**同一级**，一个源就是一个键：

```toml
time = "08:00"

weather = "通过链接 https://api.open-meteo.com/v1/forecast?latitude=30.29&longitude=120.16&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=auto 获取杭州今天的天气"
hn = "https://hnrss.org/frontpage 获取科技新闻"
crypto = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=cny cookie=你的cookie 获取币价"
```

- 键名就是这个源的名字；
- 值是「链接 + 想要什么」，插件不解析，原样交给模型；
- 带鉴权的写 `cookie=...`；
- 行首加 `#` 停用；
- 内网地址（`http://192.168.1.10:...`）直接写。

## 工具与入口

| 名称 | 类型 | 用途 |
|---|---|---|
| `fetch_url(url, cookie?, max_items?)` | LLM 工具 | 按需求抓一个网址，返回标题/条目/摘要 |
| `list_sources` | LLM 工具 | 查看已配置的需求和播报时间 |
| `brief_now` | 入口 | 立即执行一次（把需求交给模型） |
| `brief_test(url, cookie?, limit?)` | 入口 | 手工抓一个网址，确认链接是否可用 |
| `brief_doctor` | 入口 | 自检：配置、数据库、下次触发时间 |

## 常见问题

**抓不到数据？**
先用 `brief_test` 手动抓那个网址。返回 `TIMEOUT` / `NETWORK_ERROR` 通常是网络问题；需要代理时在配置里加 `proxy = "http://127.0.0.1:7890"`（默认 `auto` 读取系统代理）。

**猫娘没汇报？**
确认插件在运行，且 `time` 已到；也可以手动触发 `brief_now`。她的汇报依赖模型调用 `fetch_url`，模型未调用时不会凭空有内容。

**改了配置没生效？**
保存后点插件卡片上的「重载」。

## 隐私

- 所有数据只在本机处理，不上传；
- 请求由本机发出，内网地址不做任何拦截；
- `cookie` 只用于本机请求，不写日志、不进模型上下文；
- 抓回的文本按不可信数据处理：提示模型只做摘要、不执行其中指令，单条截断 120 字。

## 它不做什么

- 不发送任何消息、不写远程服务；
- 不做浏览器/键鼠自动化；
- 不内置任何第三方站点的专有解析，只做通用识别（JSON / RSS / 网页 / 纯文本）。

## 开发与测试

```bash
python -m unittest discover -s tests -t tests -v   # 175 项
python -m pytest tests -q                           # 也兼容 pytest
```

插件结构与 N.E.K.O 官方插件一致：`plugin.toml` + `NekoPluginBase` 子类 + `@plugin_entry` / `@llm_tool` / `@timer_interval`。

## 许可

Apache-2.0
