# Steam管理（steam_manager）

让 YUI 管理本地 Steam 客户端与游戏库的 N.E.K.O 插件（Windows），并充当**游戏助手**：读取所玩游戏画面、实时跟踪行为、提供建议，玩家可授权 YUI 接管键鼠代打。
v0.3.0 起新增 **Steam 账号/商店助手**：账户信息、好友列表、回复好友、查价格、查促销、打开任意游戏。

## 功能

### Steam 基础

| 工具 | 说明 |
|---|---|
| `steam_status` | 检测 Steam 安装路径与客户端是否运行 |
| `list_games` | 列出本地游戏库已安装游戏（名称/AppID/占用空间/状态），支持模糊过滤 |
| `launch_game` | 按名称或 AppID 启动已安装游戏（`steam.exe -applaunch`） |

### 账号 / 好友 / 商店（v0.3.0）

| 工具 | 说明 | 凭证 |
|---|---|---|
| `steam_account` | 读取账户信息（本地昵称+SteamID；配置 Key 后含在线状态/国家/资料页） | 可选 STEAM_API_KEY |
| `steam_friends` | 好友列表：昵称/SteamID/在线状态，支持昵称过滤 | 需要 STEAM_API_KEY |
| `steam_reply_friend` | 给好友发消息（steamcommunity 网页聊天，最长 500 字） | 需要 STEAM_API_KEY + steam_cookie.txt |
| `steam_game_price` | 查询游戏国区价格（现价/原价/折扣），名称或 AppID | 无需凭证 |
| `steam_promotions` | 查询商店当前特惠/促销游戏 | 无需凭证 |
| `steam_open_app` | 打开任意游戏/应用：已装启动、未装开商店页 | 无需凭证 |

### 游戏助手（v0.2.0）

| 工具 | 说明 |
|---|---|
| `game_capture` | 截取当前前台游戏窗口画面，返回窗口/进程/截图路径 |
| `watch_game` | 开启/关闭后台画面监控：周期性截帧，记录行为时间线 |
| `game_state_summary` | 汇总当前局面与近期行为，供 YUI 推理给出合理建议 |
| `take_control` | 玩家显式授权 YUI 接管键鼠（默认关闭，随时交还） |
| `game_action` | 接管状态下执行一次键鼠动作（点击/按键/输入文本） |

## 安装

1. 打开 N.E.K.O 客户端 → 插件中心
2. 搜索「Steam管理」，点击安装
3. 本机需已安装 Steam 客户端（常见路径 `C:\Program Files (x86)\Steam`）
4. （可选）账户/好友/回复功能：配置 `STEAM_API_KEY`（环境变量或插件目录 `steam_api_key.txt`）；
   回复好友还需 `steam_cookie.txt`（steamcommunity 登录 Cookie，含 `sessionid`）

## 游戏助手用法

- 用户：**帮我盯着这局游戏** → `watch_game(on=true)` 开启后台监控
- 用户：**现在什么局面，给点建议** → `game_state_summary()` + `game_capture()`，YUI 基于窗口与画面推理建议
- 用户：**你来帮我操作** → 确认画面在场后 `take_control(on=true)` 授权，`game_action` 点击/按键/输入
- 用户：**停止接管** → `take_control(on=false)` 立即交还

## 账号关联说明

- 本插件**不需要、也不会登录你的 Steam 账号**；启动游戏复用本机 Steam 客户端的登录态
- 本地读取：库文件（`libraryfolders.vdf` / `appmanifest_*.acf`）与最近登录账号昵称/SteamID（`config/loginusers.vdf`）
- 在线能力：账户概要/好友列表走 Steam Web API（可选 Key）；回复好友走 steamcommunity 网页聊天（可选 cookie）
- 价格/促销走 Steam Store 公开 API，无需任何凭证
- 未下载到本机的游戏（仅购买未安装）不会出现在本地列表中，但可用 `steam_open_app` 打开商店页

## 安全说明

- 后台监控默认关闭，用户要求才开启；仅保留最近 20 帧自动清理
- 键鼠接管默认关闭，必须玩家显式授权；仅作用于前台游戏窗口；每步操作记入时间线
- 只读取本地库元数据与屏幕画面；API Key / cookie 只读取不输出；不读取密码、令牌、云存档、聊天记录
- 回复好友属于对外写操作，仅在用户明确要求时发送
- 不修改任何 Steam 配置；全部标准库 + ctypes 实现，无第三方依赖

## 安装路径识别

1. 注册表 `HKCU\Software\Valve\Steam` → `SteamPath`
2. 兜底：`C:\Program Files (x86)\Steam`、`C:\Program Files\Steam`

## 开发与构建

```bash
# 测试
python -m pytest plugin/plugins/steam_manager/tests/ -q
# 发布前检查
python -m plugin.neko_plugin_cli.cli check -r steam_manager
```
