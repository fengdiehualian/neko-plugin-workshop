# QQ集成 (qq_auto_reply)

通过 OneBot v11（正向/反向 WebSocket，兼容 SnowLuma / NapCat / LLOneBot / go-cqhttp / Lagrange 等任意 OneBot 后端）或 QQ 官方开放平台接入 QQ 的完整机器人集成插件。

- **双通道接入**：OneBot v11 泛用连接（正向/反向）+ QQ 官方开放平台
- **多群注意力管理**：群间注意力竞争、焦点切换、回溯补回
- **接话判定（谁在跟谁说话）**：@你 / 引用你 / 引用别人 / @别人 / 叫你的名字 / 她刚说完的
  第一条 —— 明确在跟别人说话时不抢话，判据同时进打分、门控与提示词
- **动态回复缓冲**：多条缓冲汇总、发送门控自缓冲、频率软提示
- **多模态输出**：文本、语音、图片、表情包、戳一戳、键盘
- **文件内容读取**：文本 / VLM 图片描述
- **提示词编辑**：运行时动态修改系统提示词与场景模板

## 插件信息

| 字段 | 值 |
| --- | --- |
| 插件 ID | `qq_auto_reply` |
| 类型 | `plugin` |
| 版本 | `0.13.0` |
| SDK | `>=0.1.0,<0.3.0` |
| 被动模式 | 是 |

## 开发与验证

```bash
uv run neko-plugin check qq_auto_reply
uv run neko-plugin build qq_auto_reply
```

## 配置

运行时配置（注意力阈值、回溯参数、群信任列表等）由 `business_config.json` 提供，位于 N.E.K.O 数据根目录 `data/plugins/qq_auto_reply/` 下。

- 群名单 `trusted_groups` 里的群**默认就是信任群**（走注意力门控正常参与）：条目漏写
  `level`、写空、写错级别都按信任群收录。想让她只在被 @ 时按
  `normal_relay_probability`（默认 0.1）低概率转发给主人，必须**显式**写
  `"level": "normal"`。

### SnowLuma 用户注意（真机实测，v1.14.20）

- 正向 WebSocket 请连**根路径**（如 `ws://127.0.0.1:3002/`）：收事件 + 调动作。
  SnowLuma 的 `wsServers` 在 `path: "/"` + `role: "Universal"` 时另有按路径分流的
  语义——`/api` **只能调动作、不推送消息事件**（连上但收不到消息），`/event` 只收事件。
  token 两种携带方式（`?access_token=` 与 `Authorization: Bearer`）都支持。
- `trusted_users` / `trusted_groups` 条目是对象（`{"qq": "123", "level": "trusted"}`）。
  写成字符串数组也能启动（按默认档收录），但面板编辑建议用对象格式。
