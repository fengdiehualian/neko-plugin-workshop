# Steam管理（steam_manager）

让 YUI 管理本地 Steam 客户端与游戏库（Windows），并充当**游戏助手**：读画面、跟踪行为、给建议、可授权代打；
v0.3.0 起同时充当 **Steam 账号/商店助手**：账户信息、好友列表、回复好友、查价格、查促销、打开任意游戏。

## 工具

### Steam 基础

| 工具 | 说明 |
|---|---|
| `steam_status()` | 检测 Steam 安装路径与客户端是否运行 |
| `list_games(keyword, limit)` | 列出本地游戏库已安装游戏（名称/AppID/占用空间/状态），支持模糊过滤 |
| `launch_game(game)` | 按名称或 AppID 启动已安装游戏（经 steam.exe -applaunch） |

### 账号 / 好友 / 商店（v0.3.0 新增）

| 工具 | 说明 | 凭证 |
|---|---|---|
| `steam_account(steam_id)` | 读取账户信息：本地昵称+SteamID；配置 Key 后含在线状态/国家/资料页 | 可选 STEAM_API_KEY |
| `steam_friends(steam_id, name_filter)` | 好友列表：昵称/SteamID/在线状态，支持昵称过滤 | 需要 STEAM_API_KEY |
| `steam_reply_friend(friend, text)` | 给好友发消息（steamcommunity 网页聊天，最长 500 字） | 需要 STEAM_API_KEY + steam_cookie.txt |
| `steam_game_price(game)` | 查询游戏国区价格（现价/原价/折扣），名称或 AppID | 无需凭证 |
| `steam_promotions(limit)` | 查询商店当前特惠/促销游戏（国区首页特色分类） | 无需凭证 |
| `steam_open_app(game)` | 打开任意游戏/应用：已安装直接启动；未安装打开商店页 | 无需凭证 |

### 游戏助手（v0.2.0 新增）

| 工具 | 说明 |
|---|---|
| `game_capture()` | 截取当前前台游戏窗口画面（BMP 文件），返回窗口/进程/截图路径 |
| `watch_game(on, interval)` | 开启/关闭后台画面监控：周期性截帧并记录行为时间线 |
| `game_state_summary()` | 汇总当前局面：监控/接管状态、当前窗口、最近截图与近期行为记录，供 YUI 给出建议 |
| `take_control(on)` | **玩家显式授权** YUI 接管键鼠（默认关闭）；随时可交还 |
| `game_action(action, x, y, key, text)` | 接管状态下执行一次键鼠动作（点击/按键/输入文本） |

## 可选凭证配置

- **STEAM_API_KEY**（账户信息增强 / 好友列表 / 回复好友时按昵称解析）：环境变量 `STEAM_API_KEY`，
  或把 Key 写入插件目录 `steam_api_key.txt`（明文，仅本地读取）。
- **steam_cookie.txt**（回复好友必需）：登录 `https://steamcommunity.com/` 后，把浏览器 Cookie 字符串
  写入插件目录 `steam_cookie.txt`（需含 `sessionid` 与 `steamLoginSecure`/`steamLogin`）。

## 游戏助手使用流程（示例）

1. 用户：帮我盯着这局游戏
   - YUI：`watch_game(on=true, interval=3)` → 开始后台截帧
2. 用户：现在什么局面？给点建议
   - YUI：`game_state_summary()` + `game_capture()` → 基于窗口与画面给建议
3. 用户：你来帮我操作
   - YUI：确认窗口在场后 `take_control(on=true)` → 授权 YUI 模拟键鼠
   - YUI：`game_action(action="click", x=..., y=...)` 操作游戏
4. 用户：停止接管
   - YUI：`take_control(on=false)` → 立即交还

## 安全边界（重要）

- Steam 侧：只读取本地库元数据与账号昵称；启动/打开游戏必须来自用户明确指令
- 凭证：API Key / cookie 只读取不输出到对话；回复好友仅在用户明确要求时发送
- 监控：后台截帧**默认关闭**，用户要求跟踪时才开启（间隔最小 1 秒，仅保留最近 20 帧，自动清理）
- 接管：键鼠模拟**默认关闭**；开启前提是有最近的窗口画面；仅作用于当前前台游戏窗口；每次动作都记录进时间线；用户可随时要求交还
- 不读取密码、令牌、云存档、聊天记录等敏感数据；不修改 Steam 配置

## 安装路径识别

1. 注册表 `HKCU\Software\Valve\Steam` → `SteamPath`
2. 兜底：`C:\Program Files (x86)\Steam`、`C:\Program Files\Steam`

## 实现说明

- 全部使用 Python 标准库 + ctypes（GDI 截屏存 BMP、SendInput/mouse_event/keybd_event 模拟输入），无第三方依赖
- 截图文件保存于 `%TEMP%\steam_manager\frames\`，随监控滚动清理
- 非 Windows 环境：相关工具返回明确提示，不报错崩溃
