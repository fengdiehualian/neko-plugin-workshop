# N.E.K.O. 插件 pack(neko-plugin)

生成符合 N.E.K.O. 官方规范的 Python 插件。规则来源:`neko-docs/`(2026-09-02 版,15 篇)。

## 结构

- `pack.json` — pack 元数据(供工作台 pack 注册器读取)
- `rules.json` — 生成/检查规则:12 条硬规则 + check 闸门 + 发布流程
- `templates/` — 三个起步模板,均通过 R1–R12 硬规则
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

## 硬规则速查(R1–R12)

见 `rules.json` 的 `x_hard_rules`。生成代码时逐条核对;`check` 闸门通过后才允许进入发布流程。

## 后续(待宿主侧答复)

- 沙箱试运行:依赖 N.E.K.O. 宿主 dev 加载机制(已发询问,见 opencode-workbench/向NEKO侧提问-dev加载机制.md)
- 发布直通车:M4 实现(check→sync→仓库→publish 已文档化,不依赖询问结果)
