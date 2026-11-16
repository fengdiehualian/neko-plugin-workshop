# N.E.K.O. 插件 pack(neko-plugin)

生成符合 N.E.K.O. 官方规范的 Python 插件。规范正文来自官方文档快照(`skill/neko-plugin-dev/references/` 下的官方规范页,另有 `market-catalog.md` / `market-patterns.md` 两份市场分析)。
页数的唯一算法在 `mcp.mjs`(运行时数 `references/*.md`),**任何文档里都不许再抄一遍数字**——
由 `test/docs-consistency.test.ts` 与 `test/rules-numbering.test.ts` 对着磁盘实际文件校验。

## 结构

- `pack.json` — pack 元数据(供工作台 pack 注册器读取)
- `rules.json` — 生成/检查规则:`x_hard_rules`(R 号)+ `x_workflow_rules`(W 号)+ check 闸门 + 发布流程;**条数与末号只以该文件为准,别在文档里抄数字**
- `templates/` — 三个起步模板;合规与否由 `test/templates.test.ts` 按 `rules.json` 当前 R 号校验(条数不抄在这里)
  - `hello_world` — 最小插件(入门)
  - `reminder` — 生命周期 + 定时任务 + 线程锁(进阶)
  - `notes` — Pydantic 校验 + data_path 持久化 + llm_result_fields(高级)

## 模板占位符

`{{PLUGIN_ID}}`(插件 ID)、`{{PLUGIN_NAME}}`(显示名)、`{{CLASS_NAME}}`(类名,PascalCase)。

## 模板选取建议

| 用户意图 | 模板 |
|---|---|
| 「打个招呼」「最小的例子」 | hello_world |
| 「定时」「自动」「每天做某事」 | reminder |
| 「记住」「保存数据」「笔记/记录」 | notes |

## 硬规则速查(R 号,末号见 rules.json)

见 `rules.json` 的 `x_hard_rules`。生成代码时按 `rules.json` 当前的 R 号逐条核对;`check` 闸门通过后才允许进入发布流程。

## 后续(待宿主侧答复)

- 沙箱试运行:依赖 N.E.K.O. 宿主 dev 加载机制(已发询问,见 opencode-workbench/向NEKO侧提问-dev加载机制.md)
- 发布直通车:M4 实现(check→sync→仓库→publish 已文档化,不依赖询问结果)
