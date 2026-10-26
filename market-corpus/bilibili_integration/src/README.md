# B站集成 N.E.K.O 插件

通过 `bilibili_api` 和 B站消息中心接口监听私信、评论回复与 `@` 通知，使用 N.E.K.O AI 自动回复。

## 功能特性

| 消息类型 | 接收 | 发送 |
|---------|------|------|
| 公开文字 / 图文动态 | — | ✅ |
| 文本 (TEXT) | ✅ | ✅ |
| 图片 (PICTURE) | ✅ | ✅ |
| 分享视频 (SHARE_VIDEO) | ✅ | — |
| 评论回复通知 | ✅ | ✅（回复原评论） |
| `@` 通知 | ✅ | ✅（回复原评论） |

### 详细说明

- **接收文本消息**：直接提取文本内容，交给 AI 生成回复
- **接收图片消息**：通过 Cookie 鉴权下载图片，转为 Base64 传递给 AI
- **接收分享视频**：获取视频标题、UP主、播放量等信息，拼接为富文本
- **发送文本消息**：通过 `send_msg` 发送纯文本回复
- **发送图片消息**：支持 URL 和 Base64 两种图片来源
- **评论回复与 @ 通知**：轮询 B站消息中心；启动时忽略历史通知，之后以通知 ID 去重
- **视频上下文**：视频评论会查询标题、简介、BV 号、链接和 UP 主，并随评论一起交给 AI
- **白名单**：默认仅白名单用户能触发私信、评论回复和 `@` 通知的自动回复
- **用户昵称解析**：通过 `User.get_user_info()` API 获取真实昵称，带内存缓存
- **权限管理**：支持 admin / trusted / normal 三级权限控制
- **记忆同步**：管理员对话自动同步到 Memory Server

## 配置

在插件管理器中打开「B站集成」面板完成配置。面板可以：

- 保存或更新 B站 Cookie
- 选择白名单、黑名单或开放回复模式
- 设置评论回复和 `@` 通知的开关、轮询间隔与单次获取数量
- 管理信任用户和管理员
- 开始或停止私信、评论回复和 `@` 通知监听
- 查看脱敏后的凭据状态

配置保存在运行时插件数据目录的 `business_config.json`，不会写入仓库中的
`plugin.toml`。Windows 默认路径为：

```text
%LOCALAPPDATA%\N.E.K.O\plugins\bilibili_integration\data\business_config.json
```

该文件与微信集成插件采用相同的放置方式，使用原子写入避免文件损坏。Cookie
仍以明文保存在本机，请勿分享该文件，并确保系统账号和磁盘受到妥善保护。面板
提交的 Cookie 参数会在通用 Run 事件与返回元数据中统一脱敏，不会进入运行记录。

### B站 Cookie

| 字段 | 类型 | 说明 |
|------|------|------|
| `sesdata` | string | B站 Cookie 中的 `SESSDATA`（必填，配置文件内部字段） |
| `bili_jct` | string | B站 Cookie 中的 `bili_jct`（CSRF Token，必填） |
| `buvid3` | string | B站 Cookie 中的 `buvid3` |
| `dedeuserid` | string | B站 Cookie 中的 `DedeUserID` |
| `ac_time_value` | string | B站 Cookie 中的 `ac_time_value` |

### 权限等级

| 等级 | 说明 |
|------|------|
| `admin` | 管理员，享有最高权限，使用完整记忆上下文 |
| `trusted` | 信任用户，可获得 AI 回复 |
| `normal` | 普通用户，不自动回复 |

## 插件入口

| Entry ID | 名称 | 说明 |
|----------|------|------|
| `start_listening` | 开始监听 | 启动 B站私信、评论回复和 `@` 通知监听 |
| `stop_listening` | 停止监听 | 停止全部 B站集成监听 |
| `publish_dynamic` | 发布动态 | 发布文字或最多 9 张图片的公开动态 |
| `send_message` | 发送私信 | 向指定 B站用户发送一条私信 |
| `add_trusted_user` | 添加信任用户 | 添加信任用户到白名单 |
| `remove_trusted_user` | 移除信任用户 | 从白名单中移除用户 |
| `set_user_nickname` | 设置用户昵称 | 为信任用户设置专属称呼 |
| `list_trusted_users` | 列出信任用户 | 列出所有信任用户 |

## 发布动态

保存登录凭据后，在插件面板的「发布 B站动态」区域填写内容，选择本地图片或输入图片 URL，然后点击「发布动态」。无需开启私信或评论监听。发布成功后可直接打开动态链接。

插件启动后，会向聊天 AI 注册 `publish_bilibili_dynamic` 工具。保存 B站登录凭据后，可在聊天中让 AI 编写并发布文字或图文动态。工具使用与面板相同的校验和发布逻辑，成功返回动态 ID 与链接，失败明确标记为工具错误；结果不确定时不会自动重试。

插件仍为 `passive = true`，通过聊天模型的直接工具调用执行，不参与 Agent 自动插件选择。未新增定时或自主发布触发器。工具处理预算为 50 秒（包括等待配置锁），注册的调用超时为 60 秒。

也可调用插件入口 `publish_dynamic`：

```json
{"text": "今天的分享", "images": ["https://example.com/photo.png"]}
```

- `text`：可选，最多 2000 个字符；按普通文本发布，不自动解析 `@` 或表情标记。
- `images`：可选，最多 9 个 HTTP(S) 图片 URL 或 `data:image/...;base64,...` 数据 URL。支持 PNG、JPEG、GIF、WebP，每张非空且不超过 10 MiB。本地文件由面板转换为数据 URL；插件入口不读取任意本地文件路径。
- 文字和图片至少提供一项。图片 URL 与本地选择的图片合计最多 9 张。
- 返回 `dynamic_id`（字符串）和 `url`。发布使用当前保存的 B站账号，对外公开。
- 操作总超时为 45 秒，图片请求超时为 15 秒。请求失败或超时不会由插件自动重新发布；无法确认结果时，请先检查 B站动态页，避免重复发布。面板在失败时保留草稿。

## 首次使用

1. 使用浏览器登录 [bilibili.com](https://www.bilibili.com)
2. 打开浏览器开发者工具（F12）→ Application → Cookies
3. 找到并复制以下字段的值：
   - `SESSDATA`（必填）
   - `bili_jct`（必填）
   - `buvid3`
   - `DedeUserID`
   - `ac_time_value`
4. 打开 B站集成插件面板并保存这些字段
5. 在面板中至少添加一个信任用户，或将权限模式改为 `open`
6. 点击「开始监听」

> Cookie 有效期有限，过期后需重新获取并在面板中更新。已保存的字段不会回显；
> 输入框留空表示保留原值。

## 依赖

- `bilibili_api` — B站 API 封装库
- `httpx` — 异步 HTTP 客户端（用于图片下载）

Pillow 固定为 `12.2.0`：`13.0.0` 的 Linux wheel 内含一个原生库，其打包压缩比超过
N.E.K.O 安装器的 200:1 限制，导致发布校验失败。固定版本保留图片能力，并保持归档安全检查不变。

## 开发与发布

本仓库是独立插件源，源码目录可保留仓库名 `n.e.k.o_plugin_bilibili_integration`。
运行安装目录必须使用插件 ID `bilibili_integration`，与入口包名一致；仅将仓库名目录
放入 `plugin/plugins/` 不会完成运行安装。以下命令假设插件已按 ID 放置，从 N.E.K.O 仓库根目录运行：

```bash
uv run --with pip python -m plugin.neko_plugin_cli.cli sync bilibili_integration --clean
uv run python -m plugin.neko_plugin_cli.cli check bilibili_integration
uv run python -m plugin.neko_plugin_cli.cli check -r bilibili_integration
```

运行时依赖由 `pyproject.toml` 声明。发布前同步到 `vendor/`；生成的 `vendor/`
目录不提交到仓库。推送与 `plugin.toml` 版本一致的标签（例如 `v1.3.1`）会由
GitHub Actions 创建 `.neko-plugin` 发布资产。

## 文件结构

```text
bilibili_integration/
├── __init__.py        # 插件主实现与面板入口
├── config_store.py    # 运行时业务配置存储
├── plugin.toml        # 插件清单（不保存凭据）
├── bili_client.py     # B站私信及评论通知客户端封装
├── permission.py      # 权限管理模块
├── static/            # 插件前端面板
└── README.md          # 本文件
```

## 注意事项

- 图片下载需要携带 B站 Cookie 才能正常访问，客户端内部已自动处理
- 私信使用 `Session` 轮询机制；评论回复和 `@` 通知默认每 20 秒检查一次
- 评论与 `@` 的自动回复也遵循当前权限模式；默认只处理白名单用户
- 通知会按游标连续拉取；待处理积压最多保留 500 条，超载时优先丢弃最旧通知
- 管理员对话会自动同步到 Memory Server，用于构建连续对话上下文
- 超过 5 分钟空闲的会话会自动回收并结算记忆
