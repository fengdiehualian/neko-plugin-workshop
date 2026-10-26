# BiliLearn 桥接插件（N.E.K.O）

让 N.E.K.O 猫娘**完整操控**独立的 [BiliLearn](https://github.com/xiaoyaya191/bilibili_learning_bot)
（B站 AI 学习机器人）程序。

**开箱即用**：装上插件就能用——不需要你下载安装包、不需要填 exe 路径。
插件按下面的顺序自动搞定 BiliLearn：

1. **本地已装** → 直接用（含之前自己解包的运行时）；
2. **自动下载** → 从 GitHub release 拉最新版（**多镜像源自动切换**，带 SHA256 校验）后解包。

之后以无头模式（`--serve`）拉起，**不弹面板、不建系统托盘**；
已在运行则直接附接，绝不重复拉起。

## 它能做什么

1. **自动就位**：见上。本地没有 BiliLearn 时，插件自己取得并解包安装包，
   全部落在插件数据目录里（`data/runtime/`），不写系统目录，清理即消失。
2. **自动登录面板**：BiliLearn 3.1.6 的面板对 `/api/*` 有「确认免责声明 → 登录」两道门槛。
   插件会自动完成这个握手（默认使用 BiliLearn 首次启动预置的本地账号 `yaya`），
   并保持会话 Cookie，让每个调用都落在已登录态上。会话过期会自动重登一次。
3. **全功能可控**：把 BiliLearn 3.1.6 的 **308 个 Web API** 全部接入猫娘可调用范围。
   日常功能有语义化入口（`bililearn_bot_start` 等 36 个），长尾功能走通用透传
   `bililearn_api_call`（一个入口即可调用任意接口），配合接口检索做能力发现。

## 入口一览（共 43 个）

### 程序与面板（本插件自带，不带前缀）

| id | 说明 |
|----|------|
| `start` | 启动/附接 BiliLearn（无头模式，返回地址） |
| `stop` | 停止**本插件拉起**的进程（手动启动的实例不动） |
| `restart` | 停后重启 |
| `status` | 运行状态 + 端口 + 面板登录状态 + 机器人摘要 |
| `open_panel` | 在浏览器弹出网页面板 |
| `login` | 确保桥接会话已登录（可传 username/password） |
| `panel_set_credentials` | 保存面板凭据到插件数据目录并验证 |

### BiliLearn 功能（前缀 `bililearn_`）

| 分组 | 入口 |
|------|------|
| 通用透传 | `api_call`（调任意接口）、`api_batch`（批量）、`api_groups`、`api_search`、`api_describe` |
| 机器人 | `bot_start`、`bot_stop`、`bot_status`、`bot_output` |
| 监听/待机 | `monitor_start`、`monitor_stop`、`monitor_status`、`standby_start`、`standby_stop` |
| B站账号 | `bili_qr_start` |
| 视频 | `video_summarize`、`video_to_web`、`video_mindmap`、`send_danmaku`、`deep_dive` |
| 知识库 | `kb_stats`、`kb_search`、`kb_tutor_chat` |
| 兴趣/搜索 | `interest_add`、`search_trigger` |
| 学习目标 | `goal_status`、`goal_set`、`goal_stop` |
| Agent | `agent_start`、`agent_stop` |
| 人格/心情 | `personas`、`persona_activate`、`mood_status` |
| 其他 | `diary`、`logs`、`config_get` |

> **没被列出的功能照样能用。** 例如「设置代理」：
> `bililearn_api_call(path="/api/network/proxy", method="POST", body={"enabled": true, "url": "http://127.0.0.1:7890"})`。
> 不知道接口名时，先 `bililearn_api_groups` 看分组，再 `bililearn_api_search(keyword="proxy")`。

## 配置（config.toml）

```toml
[bililearn_bridge]
exe_path = ""            # 一般留空：插件自动取得 BiliLearn（本地/自动下载）
auto_install = true      # 本地没有时是否自动下载解包（false 则只找本地）
port = 18083             # 面板端口（插件会优先读账户注册表里的真实端口）
launch_mode = "serve"    # serve=无头服务器; silent=无头无托盘
auto_launch = true       # N.E.K.O 启动时自动拉起
panel_username = ""      # 面板账号；留空则用 BiliLearn 预置的 yaya
panel_password = ""      # 面板密码（你在面板里改过密码就填这里）
auto_setup = true        # 面板尚无账号时自动创建一个
request_timeout = 20     # 单请求超时（秒）
# search_dirs = ["D:\\Tools"]   # 额外的 exe 搜索目录（可选）
```

## 前置

无。默认配置即可开箱即用。仅以下情况需要手动干预：

- 所有下载源都连不上（离线/代理异常）→ 自己下载解压后填 `exe_path`
  （或放进插件会自动搜索的位置）。
- 你在 BiliLearn 面板里改过默认密码 → 把新密码填到 `panel_password`。

### 关于自动下载与镜像源

插件不附带安装包，改为一律自动下载：本地找不到 BiliLearn 时，从
[GitHub release](https://github.com/xiaoyaya191/bilibili_learning_bot/releases)
拉取最新版，下载完成后校验 SHA256，再解包到插件数据目录。

国内直连 GitHub 经常超时，因此内置了**多镜像源自动切换**，按顺序尝试，
任一成功即止；单个源失败自动换下一个，全部失败才报错：

| 顺序 | 源 | 说明 |
|------|----|------|
| 1 | `gh-proxy.com` | 国内镜像，实测最快 |
| 2 | `ghfast.top` | 国内镜像 |
| 3 | `official` | GitHub 官方直连（海外/无墙环境更快） |

镜像清单在 `installer.py` 的 `_DOWNLOAD_SOURCES`，加一个源就是加一行
`("名字", "https://域名/{}")`——`{}` 是原始 URL 的占位符。

另外，网络请求（含元数据查询）采用「**先直连、失败再走系统代理**」策略：
某些机器上 `HTTP_PROXY` 指向一个没在监听的端口，会让 urllib 直接放弃直连、
所有请求报 `WinError 10061`；先试直连可正常穿越这种环境。

## 关于登录

BiliLearn 面板默认账号是 `yaya` / `yaya`（首次启动自动创建，**强烈建议**首次登录后改掉）。
插件按以下顺序尝试登录，任一成功即止，不会反复试错触发面板的登录限流：

1. 配置里的 `panel_username` / `panel_password`
2. 插件数据目录里保存过的凭据（`data/panel_credentials.json`）
3. BiliLearn 预置的 `yaya` / `yaya`

面板尚未创建账号时（`auto_setup = true`），插件会自动创建一个账号并把凭据存进插件数据目录。

## 依赖

无。仅使用 Python 标准库（`subprocess` / `urllib` / `http.cookiejar` / `zipfile` /
`asyncio`），可直接随 N.E.K.O 加载。

## 开发

```bash
# 运行测试（离线，无需 N.E.K.O SDK 也能跑）
python -m unittest tests.test_smoke -v

# 重新生成接口目录（BiliLearn 升级后）
python tools/extract_api_catalog.py bilibili_learning_bot/web_panel.py tools/api_catalog.json
python tools/extract_api_params.py  bilibili_learning_bot/web_panel.py tools/api_params.json
python tools/gen_catalog_module.py  tools/api_catalog.json tools/api_params.json bridge_plugin/catalog.py
```
