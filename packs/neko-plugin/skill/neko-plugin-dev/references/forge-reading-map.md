# plugin-forge 接入线 · 读文档地图（forge-reading-map）

> 正典在 `src/`。本文件只解决一个浪费最多时间的问题：**先读哪一份、哪条事实去哪核**。
> 这里不写行号——行号会漂，路径和章节名不会。

## 读的顺序（别跳）

1. `src/skills/plugin-forge/SKILL.md` —— 铁律、编制、平台 profile 机制。
2. 按平台读 profile：
   - N.E.K.O → `src/skills/plugin-forge/references/profile-neko.md`（**先跑它文首的陈旧门禁**）
   - 其他平台 → `src/skills/plugin-forge/references/profile-template.md` 的调研模板
3. 按任务读工作流：`references/workflows.md`（A–H）。
4. 只在需要时读细节：`sdk-reference` / `entries` / `decorators` / `hosted-ui` 等。

## N.E.K.O 的硬事实（先记这几条，别再猜）

| 事实 | 结论 | 去哪核 |
|---|---|---|
| `plugin.toml` 必填 | `id`、`name`、`version`、`entry` | `src/skills/plugin-forge/references/profile-neko.md` |
| `[plugin].entry` 形状 | `module.path:ClassName`，解析到 `NekoPluginBase` 子类 | 同上（schema 段） |
| 目录名与 id | 必须一致；打包/发布/安装都按 id 建目录 | 同上 |
| `type` 取值 | 只有 `plugin`（默认）/ `adapter`；`extension` 早已移除 | 同上 |
| `[plugin.host]` | 已移除，写了直接报错 | 同上 |
| `auto_start` 缺省 | 没写按 `false`：插件会注册但不会 autostart | 同上 |
| 入口写法等价 | `plugin.plugins.<目录>` 与 `plugins.<目录>` 都能装；裸模块名（`main:Symbol`）装不进 | 同上 + `src/skills/neko-plugin-sandbox/scripts/dry_load.py` 的判据注释 |
| 进程模型 | 插件在独立进程，靠 ZMQ IPC 与宿主通信；插件内不做长阻塞同步 IO | `src/skills/plugin-forge/references/profile-neko.md` |
| 已移除 API | `type=script/extension`、`plugin._types.result`、`plugin.sdk.extension`、`self.memory`、push_message v1 字段等 | 同上「黑名单」段 |

> 这些结论都钉在 v2.8.0 / `722529a`。上游改了之后要重抓语料；本文件不承担「永远正确」，
> 只承担「告诉你去哪核」。

## 要拉哪些文件（依赖关系）

- 只拉 `dry_load.py` 不够：它 `from sandbox import env_is_secret / repo_dir / venv_python`，
  缺 `sandbox.py` 会 fail-closed（退出码 2，`mode=blocked`），拿不到结论。
- 要跑安全分诊：`scripts/security_audit.py` 是单文件，零第三方依赖。
- 要跑契约门禁：`scripts/check_contract_approval.py` 也是单文件。

安装版的语料由 `node workbench/pull_forge.cjs` 一起落下，不用手工挑文件。

## 常见的「凭记忆写错」

| 症状 | 真正的原因 | 去哪核 |
|---|---|---|
| 写了 `type = "extension"` | 该类型已删除 | `profile-neko.md` 黑名单段 |
| 写了 `[plugin.host]` | 该段已移除 | 同上 |
| 期望 `auto_start` 默认 true | 缺省是 false | 同上 |
| 入口写成 `main:MyPlugin` | 裸模块名在 source_only 模式装不进 | `dry_load.py` 判据注释 |
| 用了 `self.memory` | 已移除，换 `self.store` / `data_path` | `sdk-reference.md` |
| push_message 用 `content=` / `message_type=` | 那是 v1，v2 用 `parts` / `visibility` / `ai_behavior` | `tool-calling.md` / `best-practices.md` |

## 去哪查整套索引

`src/skills/plugin-forge/references/` 下的文件按主题分：manifest（`profile-neko.md`）、
工作流（`workflows.md`）、契约（`contract-approval.schema.json`）、安全（`security-checklist.md`）、
交付自检（`delivery-self-check.md`）、任务分级（`task-size-routing.md`）、
文档风格（`style-anti-slop.md`）、制度模型（`constitution.md`）。
逐份用途见 `forge-flow.md` 的「去哪查」表。
