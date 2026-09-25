# QQ机器人桥接 (qqbot_bridge)

在 **N.E.K.O** 里连接**官方 QQ 机器人**（[q.qq.com](https://q.qq.com) / `api.sgroup.qq.com`），实现 N.E.K.O 猫娘与 QQ 的双向桥接。

> 本插件仅用 Python 标准库实现整个 QQ 机器人协议（含一个迷你 WebSocket 客户端），**零第三方依赖**。

## 它能做什么

- 📥 **接收消息**：群@消息（`GROUP_AT_MESSAGE_CREATE`）、私聊（`C2C_MESSAGE_CREATE`）、频道@消息（`AT_MESSAGE_CREATE`）。
- 📤 **自动回复**：把收到的消息交给「回复源」生成回复，再发回 QQ。
- 🐱 **猫娘主动发**：通过 `qq_send` 入口，让猫娘 / 其他插件 / 面板主动给 QQ 发消息。
- 🖥️ **面板**：查看连接状态、最近消息、发送测试、一键重连。

## 回复源（reply_mode）

| 模式 | 行为 |
| --- | --- |
| `echo` | 把收到的消息原样返回（**最易验证连通性**，建议先试用） |
| `webhook` | 把消息 `POST` 到你的 N.E.K.O Agent 端点，把返回值发回 QQ（**真正链接 N.E.K.O**） |
| `none` | 只接收并展示，不自动回复 |

### 把回复接到 N.E.K.O 猫娘（webhook 模式）

1. 准备一个能接收 `POST` 的 N.E.K.O Agent 端点（你的 N.E.K.O 若开放了聊天 HTTP API，填它的地址即可）。
2. 插件会以 `POST` 发送：
   ```json
   { "message": "用户说的原文", "meta": { "channel": "group", "author": "u_9", "...": "..." } }
   ```
3. 端点返回 `JSON {"reply": "猫娘的回复"}` 或纯文本，插件将其发回 QQ。

> 若你的 N.E.K.O 暂无对外 HTTP 接口，可先用 `echo` 验证收发，再自行接入 webhook。

## 配置（plugin.toml 的 `[qqbot_bridge]` 段）

在 [q.qq.com](https://q.qq.com) 创建机器人后获得 `AppID` 与 `Client Secret`，填入：

```toml
[qqbot_bridge]
app_id = "你的AppID"
client_secret = "你的ClientSecret"
reply_mode = "webhook"
webhook_url = "https://your-neko-agent.example.com/api/chat"
auto_reply = true
```

- 只填 `app_id` + `client_secret` 即可，插件会自动换取 `access_token`。
- 也可直接填 `bot_token` 跳过换取步骤。
- `intents` 默认订阅群@与私聊，一般不需要改。

## 本地验证（无需 N.E.K.O SDK）

```bash
cd qqbot_bridge
python -m unittest tests.test_smoke -v
```

测试覆盖：包结构契约、Python 语法、WebSocket 帧编解码、消息事件归一化。

## 目录结构

```
qqbot_bridge/
├── plugin.toml          # 插件清单 + [qqbot_bridge] 配置段
├── __init__.py          # QqbotBridgePlugin 入口
├── qq_core.py           # 官方 QQ 机器人协议客户端
├── qq_ws.py             # 标准库 WebSocket 客户端
├── ui/panel.tsx         # 宿主原生面板（hosted-tsx）
├── i18n/                # 中文 / 英文文案
├── tests/test_smoke.py   # 冒烟测试
└── config.example.toml   # 配置示例
```

## 发布

推送到 GitHub 并打 `v0.1.0` 标签即触发 `plugin-market-release` 工作流，发布到 N.E.K.O 插件市场。
