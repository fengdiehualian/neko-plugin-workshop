# 猫娘每日计划 (Catgirl Daily Planner)

让猫娘每天为主人定制专属计划,并温柔地按点提醒该做什么事。

## 功能

- 🌅 **早安播报** — 起床时猫娘主动推送今日计划概览
- ⏰ **定时提醒** — 每个任务到点用猫娘专属口吻推送
- ✅ **任务管理** — 添加/标记完成/跳过/删除
- 🌙 **晚安复盘** — 睡前总结今日完成度并道晚安
- 💾 **持久化** — 计划保存在 PluginStore,重启不丢

## 配置 (plugin.toml `[planner]` 段)

| 字段 | 默认 | 说明 |
|------|------|------|
| `wake_hour` / `wake_minute` | 8:00 | 早安播报时间 |
| `goodnight_hour` / `goodnight_minute` | 22:00 | 晚安复盘时间 |
| `auto_daily_regenerate` | true | 自动每日重建 |
| `max_tasks_per_day` | 30 | 单日最大任务数 |
| `timezone` | Asia/Shanghai | 时区 |
| `catgirl_nickname` | 喵喵 | 猫娘自称 |
| `master_nickname` | 主人 | 主人称呼 |

## 入口

| 入口 | 说明 |
|------|------|
| `create_plan` | 一次性创建整日计划 |
| `add_task` | 追加单条任务 |
| `mark_done` | 标记完成/跳过 |
| `list_today` | 查看今日计划 |
| `delete_task` | 删除任务 |
| `clear_plan` | 清空整日 |
| `trigger_morning` | 手动早安播报 |
| `trigger_goodnight` | 手动晚安复盘 |
| `get_schedule` | 查看早安/晚安时间、昵称 |
| `set_schedule` | 运行时修改早安/晚安时间、昵称、可选重置今日标志 |

## 依赖

- `memo_reminder` 插件(用于底层定时提醒),开箱即用
- `plugin.store.enabled = true`

## 使用示例 (LLM 调用)

```
请帮我安排今天的事:9点写日报,12点午休,15点开会,18点健身。
```

LLM 会调用 `create_plan` 自动创建任务并设好定时提醒。
