---
name: knowledge-import
description: 把文件导入知识库并建立语义索引。当用户提供新文档、要求"学习/记住这个文件"，或需要把一批 md/txt/pdf 资料纳入检索范围时使用。
license: Apache-2.0
metadata:
  author: neko-plugin
  version: "1.0"
---

# 知识文件导入

## 何时使用

- 用户给出一个目录或文件路径，要求导入/学习/记住
- 用户拖来一批 `.md` / `.txt` / `.pdf` 文档，希望之后能被搜到
- 需要为后续 `knowledge-search` 建立索引

## 如何使用

调用知识库插件的 `kb_import` 入口：

| 参数 | 类型 | 说明 |
|------|------|------|
| `source_dir` | string | 要扫描的目录；留空用默认知识目录 `<插件数据>/knowledge/` |
| `single_file` | string | 导入单个文件（优先于 source_dir） |

流程：扫描 → 按段落分块（512 tokens，重叠 64）→ all-MiniLM-L6-v2 向量化 →
写入索引（内容哈希去重，重复导入自动跳过）。

返回 `{imported, skipped, chunks, files}`。

## 注意

- 仅支持 `.md` / `.txt` / `.pdf`；其他扩展名会被拒绝
- 导入是幂等的：同一文件重复导入只会计数，不会产生重复块
- 导入完成后告诉用户可以开始提问，或用 `knowledge-search` 验证
