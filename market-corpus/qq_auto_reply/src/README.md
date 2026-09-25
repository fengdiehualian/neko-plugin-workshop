# QQ集成 (qq_auto_reply)

通过 OneBot v11（正向/反向 WebSocket，兼容 NapCat / LLOneBot / go-cqhttp / Lagrange 等任意 OneBot 后端）或 QQ 官方开放平台接入 QQ 的完整机器人集成插件。

- **双通道接入**：OneBot v11 泛用连接（正向/反向）+ QQ 官方开放平台
- **多群注意力管理**：群间注意力竞争、焦点切换、回溯补回
- **动态回复缓冲**：多条缓冲汇总、疲劳系统、发送门控自缓冲
- **多模态输出**：文本、语音、图片、表情包、戳一戳、键盘
- **文件内容读取**：文本 / VLM 图片描述
- **提示词编辑**：运行时动态修改系统提示词与场景模板

## 插件信息

| 字段 | 值 |
| --- | --- |
| 插件 ID | `qq_auto_reply` |
| 类型 | `plugin` |
| 版本 | `0.8.0` |
| SDK | `>=0.1.0,<0.3.0` |
| 被动模式 | 是 |

## 开发与验证

```bash
uv run neko-plugin check qq_auto_reply
uv run neko-plugin build qq_auto_reply
```

## 配置

运行时配置（注意力阈值、回溯参数、群信任列表等）由 `business_config.json` 提供，位于 N.E.K.O 数据根目录 `data/plugins/qq_auto_reply/` 下。
