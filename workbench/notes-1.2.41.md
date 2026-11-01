## 1.2.41 — ZCode 能用工坊的技能与开发文档了

**之前的问题**:ZCode 只认工作区里的 `.agents/skills/`,而工坊的插件开发规范、社区插件自带的技能都在安装目录里,所以切到 ZCode 后它看不到。

**现在**:启动时自动把技能挂到 `工作区/.agents/skills/`(Windows junction,不占空间,源文件改了立刻生效),已实测 ZCode 能识别并主动调用:

| 技能 | 来源 | 实测 |
|---|---|---|
| `neko-plugin-dev` | `packs/neko-plugin/skill` | ✅ 问"plugin.toml 必填字段"→ 准确答 `id/name/version/entry` |
| `knowledge-search` 等 4 个 | `market-corpus/plugin_skill_manager` | ✅ 问用途 → 准确描述"导入文档的语义搜索" |
| `neko-bridge` | `market-corpus/agent_bridge` | ✅ 已挂载可见 |

`references/` 下的开发文档也能按需读取(实测能读出 `cli.md` 第 49 行原文并标注行号)。

### 顺手修的一个坑
`references/cli.md`、`quick-start.md` 等 4 篇文档里还是 `uv run neko-plugin …` 的老写法,和 SKILL.md「禁止 uv run」冲突——模型照着文档跑会触发数 GB 依赖重建。已在每篇顶部加醒目警示,统一指向 `python plugin\neko_plugin_cli\cli.py …`。
