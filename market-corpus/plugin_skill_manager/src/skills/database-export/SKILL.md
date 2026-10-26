---
name: database-export
description: 把热数据表中沉淀的知识导出为 Markdown 或 JSON 文件。当用户要求导出/备份沉淀知识、生成学习报告，或想把高频知识带到其他设备时使用。
license: Apache-2.0
metadata:
  author: neko-plugin
  version: "1.0"
---

# 热知识导出

## 何时使用

- 用户说"导出沉淀的知识 / 把高频知识存成文件 / 备份知识库精华"
- 需要把 `hot_knowledge` 的内容交给其他工具（选 JSON）
- 想要一份人类可读的知识汇总（选 Markdown）

## 如何使用

调用知识库插件的 `kb_hot_export` 入口：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `path` | string | 自动 | 目标文件路径；留空写到 `<插件数据>/exports/hot_knowledge.<fmt>` |
| `fmt` | string | md | `md`（Markdown 汇总）或 `json`（结构化，便于程序处理） |
| `include_demoted` | bool | false | 是否包含已降级的冷知识 |

返回 `{path, count, format}`；Markdown 文件按条目分节，含来源、命中次数、
时间戳与原文。

## 注意

- 导出是只读操作，不影响热数据表
- 文件写入后把绝对路径告诉用户
- 30 天未被命中的知识默认不导出（已被降级），需要时打开 `include_demoted`
