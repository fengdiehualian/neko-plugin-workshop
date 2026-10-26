---
name: database-query
description: 查询热数据表中的高频知识。当用户询问"最近常问的内容"、"沉淀下来的知识"，或需要快速获取已被反复检索确认的知识条目时使用。
license: Apache-2.0
metadata:
  author: neko-plugin
  version: "1.0"
---

# 热知识查询

## 何时使用

- 用户想看"哪些知识被频繁问到 / 最近沉淀了什么"
- 需要快速回答而不必重新做全量语义检索——热数据表里的条目都是
  7 天窗口内命中 ≥ 5 次的高频知识
- 回顾知识库的演化状态（活跃 / 已降级条目）

## 如何使用

调用知识库插件的 `kb_hot_list` 入口（转发数据库插件 `hot_query`）：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `limit` | int | 50 | 最多返回条数（1-500），按最近命中倒序 |
| `include_demoted` | bool | false | 是否包含已降级的冷数据 |

每条记录含 `content`、`source`、`hit_count`、`first_hit_at`、`last_hit_at`、
`promoted_at`、`status`（active / demoted）。

## 注意

- 优先答 `content` 原文；`hit_count` 可作为可信度参考
- 若结果为空，说明还没有知识达到提升阈值，建议改用 `knowledge-search`
