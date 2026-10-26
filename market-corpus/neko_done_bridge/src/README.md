# neko_done_bridge — D-one 任务管家

把 [D-one](https://github.com/acekira3-afk/d-one-releases)（macOS 极简优先级便签）接入 N.E.K.O：任务变更时，猫娘按优先级用人格化语气提醒你。

## 数据流

```
D-one (localStorage 任务变更)
  │  done-bridge.js（注入脚本）
  ▼  POST /hook/tasks（全量快照）
本插件 HTTP Server (127.0.0.1:48917)
  │  diff 出 add/complete/promote/... 事件
  │  冷却时间内合并为一条提醒
  ▼  push_message
N.E.K.O 猫娘（p0 催促 / p1 提醒 / p2 温柔）
```

## 语气风格

| 风格 | p0（催促） | p2（温柔） |
|------|-----------|-----------|
| `neko_default`（默认） | "哼，「XX」还挂着呢……本喵可一直盯着你哦，快去做喵！" | "「XX」完成啦~今天也辛苦了喵。" |
| `calm` | 平静的助理式提醒 | 简短确认 |
| `idol` | 元气应援风（不引用任何真实角色） | 元气庆祝 |

## 接入 D-one（原型阶段）

**方式 A（持久）**：D-one 源码 `index.html` 尾部加入

```html
<script src="done-bridge.js"></script>
```

**方式 B（一次性演示）**：D-one 窗口打开 DevTools Console，粘贴 `assets/done-bridge.js` 内容执行。

之后每次在 D-one 中改动任务（新增/完成/拖动优先级/编辑/删除），脚本会自动推送快照，插件 diff 出事件并提醒。

**还没有 D-one？** 面板里有"模拟注入"，无需 D-one 即可体验完整提醒链路。

## LLM 入口

| 入口 | 用途 |
|------|------|
| `get_tasks` | 主人问"我有哪些事要做"时，返回按 p0/p1/p2 分组的未完成任务 |
| `get_top_task` | 主人问"我现在该干嘛"时，只返回最重要的一件事（D-one 哲学） |
| `get_status` | 桥接状态 |
| `set_style` | 切换语气风格 |
| `test_push` | 演示一条 p0 提醒 |

## 配置（plugin 运行时配置 `neko_done_bridge` 节）

| 键 | 默认 | 说明 |
|----|------|------|
| `port` | 48917 | HTTP 监听端口 |
| `cooldown_seconds` | 90 | 提醒冷却（秒） |
| `style` | neko_default | 语气风格 |
| `api_token` | 空 | Bearer token（可选，启用后桥接请求需携带） |

## 设计原则

- D-one 是数据权威方，插件只维护只读快照，不回写
- `edit` / `delete` 事件只记录不打扰；`promote` / `add` 优先提醒
- 默认猫娘语气，不绑定任何歌姬角色名（版权安全）；`idol` 风格为原创应援语气
