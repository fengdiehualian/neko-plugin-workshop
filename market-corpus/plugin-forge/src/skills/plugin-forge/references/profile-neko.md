# profile：N.E.K.O（猫娘桌宠）

## ⓪ 陈旧门禁（用本 profile 前必跑，一条命令）

```
gh api repos/Project-N-E-K-O/N.E.K.O/contents/scripts/custom_channels/neko_channel.py --jq .sha
```

- 返回 sha → NekoClaw 重写切换点（issue #3683 的 PR3）尚未进 main，§3/§7 契约仍有效；
- 返回 404 → 切换点已进 main：§3/§7 视为**过期**，先按新 main 重核再用，重核不了的标 [未核验]；
- 本机宿主版本已含切换点 → 同 404 处理。
- **最近一次全量重核：2026-11-17**。上游 main 先取 `db81c1be0fc18259b651ec9902c4d3ef8e5e52ac`，当天又前进一次，
  最终口径在同一命令下于 `e14f6196395a346ff422e0eb852c5eae3befc1cf` 复跑取得。
  `python scripts/profile_drift.py --strict` 现在一次核对两份文档（本文件与 `skills/neko-plugin-sandbox/SKILL.md`），
  在 `e14f61963` 上报「锚点 131 条（本文件 105 条，沙箱 SKILL 那份 26 条）、漂移 0、核不了 0、取不到 0、写法核不了 0」（2026-11-18 实跑），
  CI 里这一步阻塞。**这几个数随文档变，别抄进新结论，跑一次命令自己取。**
  裸文件名写锚点会被「写法核不了」拦住：上游同名文件成把（技能说明文件 36 份、README 28 份、
  清单文件 16 份，2026-11-18 实数）。指认本仓自己的文件要写仓内相对路径。
  讲解坏写法的例句在句内写 `drift-ignore`，它只豁免自己那半句（工单 W10 之后豁免按小句生效）。
改口的判据是硬的：一行里探针能看到的每个锚点都在当前 main 上命中，才把那行的 `读于` 换成新 sha。
按这条判据本轮逐行改口，改动清单记在 AUDIT.md 的 2026-11-17 条目。现在没有任何锚点还标 `acfbdbc50`，
那个 sha 只剩本文的历史叙述（上一条与「上一次核验」那条）提到。
**探针现在仍看不见的一处**：§9 市场那行只列 `docs/plugins/` 的文件名，不带行号，也没有符号锚点。
引用它之前先按上一条规则重核。以前这里列过一组「行号对上了但没重读语义」的行，
那句话探针证明不了，本轮起不再写。
- **上一次核验：2026-11-14 复跑本命令返回 404**。#3705 于当天把切换点合入 main，删除了 `scripts/custom_channels/neko_channel.py`，该目录已空。按上一条规则，§3/§7 本应按过期面处理，但 **2026-11-15 已对本轮触及的锚点对 main `acfbdbc50` 逐条重核**（各 `[FACT]` 标该 sha）；未标 sha 或标 `[未核验]` 的仍按上一条规则先重核再引用。
- 更早的核验记录：2026-11-11（sha 85ea534）当时判为有效。**历史记录不是时效证明。**
- 同日的市场兼容快照（逐行证据与 commit SHA 在 neko-plugin-workshop `market-corpus/compat-v2/`，见 <https://github.com/fengdiehualian/neko-plugin-workshop> PR#5。注意：PR#5 于 2026-11-16 核对为「已关闭、未合并」，该目录只在那条分支上，main 里没有。引用前先确认它可得，不可得就重跑三步）：144 个在架插件三态标注，现在就坏 1、将坏 0、有兜底会降级 1、仅文档/测试面 4、待核 11、兼容 127。这组数随市场变，引用前按快照的 scan_date 重跑三步。
- **2026-11-11 基线推进到 0984249**（#3686 合入）：diff 文件清单核对——改动全部落在
  memory/long_term_sync/chat_sync/prompts/locales 内部，插件契约（48916、/runs、SDK 装饰器、
  user_plugin_server 入口、/health）零变化，当时 §3/§7 结论维持有效（仅对 0984249 基线成立，其后切换点已进 main，见上面 2026-11-14 记录）。
- **漂移探针**：`python scripts/profile_drift.py`。它把本文件的「路径 + 行号」锚点全量抽出来，对上游当前 main 逐条核对行号是否还落在该标识符上，直连 REST 不需要 gh 登录，默认只报告不改文件。**CI 现在按阻塞接**（作者 2026-11-17 定：warnings 当 errors）：行号漂退 1，取不到上游退 2 并重试三次。它还有两条判据：记录行全是空行算漂，1MB 以上的文件改走 raw 端点取正文（Contents API 对这类文件不给正文，旧版算成取不到）。重新钉基线前先跑它，跑完再批量改 sha。
- **junction 警告**：plugin_skill_manager 安装目录里的 skills\* 全是 junction，写它们=直写
  .zcode / .agents / .workbuddy / .qwenpaw 等源目录；改前先 dir /a 看是不是 <JUNCTION>。


> 首例平台 profile。团队核心平台无关，接入别的平台请另写 profile，不要改本文件语义。
> **核验基线：带行号的锚点以 main `db81c1be0fc18259b651ec9902c4d3ef8e5e52ac`（2026-11-17 重核）为准**，由 `scripts/profile_drift.py --strict` 在 CI 里逐条把门；上一批 `acfbdbc50d88ac243dbe95ee6f364a1e83620bc3`（2026-11-15）只对本文没带行号、也没有被探针覆盖的那几组仍然有效，引用前跑文首 ⓪ 的门禁；更早的 main @ `0984249`（源码在 `d597dae` 2026-11-10 实读）仅作为 §10 历史记录保留**；
> 沙箱契约另经 `neko-plugin-sandbox` 复核（commit `645da54`，2026-11-07）。
> 每条能力面结论带 `文件:符号名`（裸行号随上游 PR 漂移，不单独作锚；确需行号时同时给符号名）。上游若升版，先复核再改。

## 0. 扩展阅读

本 profile 之外还有一批 NEKO 笔记在 `references/neko/`（调研笔记加 1 份归档的外部系统提示词 `system.llm`，它不是 plugin-forge 的行为规范）（ops 16 + court 6，2026-11-11 并入）。冲突时以本 profile 为准；`neko-court-*` 与 `capability-baseline.md` 是历史快照。

## 1. 身份与安装形态
- N.E.K.O. = 桌面猫娘 AI 宿主；插件 = **独立 Python 进程**（独立进程天然隔离崩溃）。
- 消息平面走 **ZMQ RPC**，默认 `tcp://127.0.0.1:38865`，可用 `NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT` 改写
  `[FACT] plugin/settings.py 的 `MESSAGE_PLANE_ZMQ_RPC_ENDPOINT`:533-536，读于 db81c1be0，2026-11-17`；另有 ZMQ event bridge 承载 events/lifecycle/runs，那句 maxsize 注释见下一行
  `[FACT] plugin/core/zmq_transport.py`、`plugin/settings.py:603（maxsize=4096 那句注释，读于 db81c1be0，2026-11-17）`。
- 对外 HTTP 面在插件服务器（见 §7）。`GET /health` 200 只证明**进程活着**，不证明插件被 autostart。
- 安装形态：源码目录（开发模式）或 `.neko-plugin` 包上架市场 `[FACT] plugin/settings.py: get_builtin_plugin_config_root（69-71）与 get_user_plugin_exec_root（102-117，其中 legacy `PLUGIN_CONFIG_ROOT` 覆盖仍生效，见 105-108 的 docstring）；行号读于 db81c1be0，2026-11-17`。

## 2. Manifest 契约
- 文件 `plugin.toml`。必填性：`id` **必填**、`name` **必填**、`entry` **必填**、`type` 选填、`version` 选填
  `[FACT] plugin/config/schema.py: PluginSectionSchema（id 212、name 213、entry 214 三处 Field(...) 必填；type 217 默认 plugin、version 219 默认 0.1.0＝选填；三处均读于 db81c1be0，2026-11-17。旧 plugin/PLUGIN_DEVELOPMENT_GUIDE.md 已从 main 删除，且该类不叫 PluginManifestSchema）`。
- `type` 只允许 `plugin`（默认）/ `adapter`，其他历史类型已移除
  `[FACT] plugin/config/schema.py: PluginSectionSchema.type（217 默认 plugin）与 validate_plugin_type（241 调 require_supported_plugin_type）`、`plugin/_types/plugin_types.py: PluginType = Literal["plugin", "adapter"]`（:8，三处读于 db81c1be0，2026-11-17）、`plugin/server/application/plugin_projects.py: PluginType = Literal["plugin", "adapter"]`（:53，读于 db81c1be0，2026-11-17）。
- `[plugin.host]` 已移除，写了直接报错 `[FACT] plugin/config/schema.py: format_removed_plugin_host import（18）、reject_removed_host_config（233-235）、顶层 host 键 ConfigValidationError（442-446，读于 db81c1be0，2026-11-17）`。
- `id` 的宿主与 CLI 硬校验是 `^[A-Za-z0-9_-]+$`（`[FACT] plugin/config/schema.py: PluginSectionSchema.id 的 Field pattern（212）与 plugin/neko_plugin_cli/commands/validate_cmd.py: _require_string [plugin].id（189），读于 db81c1be0，2026-11-17`）；脚手架另收 Python 标识符式 `^[A-Za-z_][A-Za-z0-9_]*$`（`[FACT] plugin/neko_plugin_cli/templates/generator.py: _PYTHON_PLUGIN_ID_RE（19，读于 db81c1be0，2026-11-17）`），新插件按严的来；
  `entry` 形如 `模块路径:类名`（模块 `[A-Za-z_][A-Za-z0-9_]*(\.[...])*`、类名 `[A-Za-z_][A-Za-z0-9_]*`）
  `[FACT] plugin/neko_plugin_cli/commands/validate_cmd.py: _require_string 处 [plugin].entry 的 pattern（192）与 _check_entry_target（def 在 921）内 module / class 两处 re.fullmatch（936、939，读于 db81c1be0，2026-11-17；旧记 181、760、763 处命中的不是这三条正则。宿主 `plugin/config/schema.py:214` 的 entry pattern 更宽（^[a-zA-Z0-9_.]+:[a-zA-Z0-9_]+$），发布以 CLI 校验为准）`。
- `[plugin.sdk]` 兼容区间字段 `recommended` / `supported` 存在（可为空）`[FACT] plugin/config/schema.py: PluginSdkSchema.recommended / supported（71-72）与 PluginDependencySchema 同名字段（195-196），读于 db81c1be0，2026-11-17`（`plugin/sdk` 兼容区间字段；GUIDE 已删除，旧锚全部作废）。
- `[plugin_runtime]` 节：`enabled` / `auto_start`（**默认 `False`**）/ `timeout`（必须 `0 < t <= 300`）；
  写成字符串布尔会被判为问题 `[FACT] plugin/config/schema.py: PluginRuntimeSchema（264-282 读于 db81c1be0，2026-11-17；另有 priority、startup_failure 两字段，timeout 上限常量 _PLUGIN_RUNTIME_TIMEOUT_MAX = 300.0 在 :22）`、`plugin/config/plugin_toml_semantics.py:136-150（runtime_section 那段，读于 db81c1be0，2026-11-17）`。
- `id` 发布后不可改；改 id = 另一个插件身份，升级只换代码，保留 config/data/cache。

## 3. 入口与生命周期
- 三个入口互不可混用（最常踩的坑）：
  | 入口 | 写在哪 | 用途 |
  |---|---|---|
  | 宿主加载入口 | `plugin.toml` `[plugin].entry` | 指向 `NekoPluginBase` 子类 |
  | 运行时入口 | `@plugin_entry(id=...)` 方法 | 面板 / 跨插件 / Agent 调用 |
  | LLM 工具 | `@llm_tool(name=...)` 方法 | 对话期 tool-calling |
- 公开装饰器全集共 14 个：13 个在 `[FACT] plugin/sdk/plugin/decorators.py`（下行起的 13 个行号读于 db81c1be0，2026-11-17），`@llm_tool` 不在该文件，它在 `[FACT] plugin/sdk/plugin/llm_tool.py: llm_tool 装饰器定义`；两者都由 `plugin/sdk/plugin/__init__.py` 从 `plugin.sdk.plugin` 导出。`@neko_plugin`:53（必需）、`@on_event`:57、
  `@plugin_entry`:88、`@lifecycle`:127（**六个**取值 `startup`/`shutdown`/`reload`/`freeze`/`unfreeze`/`config_change`，`plugin/sdk/plugin/decorators.py: lifecycle` 形参 id 的 Literal，129 行读于 db81c1be0，2026-11-17）、`@message`:137、
  `@timer_interval`:156、`@custom_event`:175、`@hook`:200、`@before_entry`:210、`@after_entry`:219、
  `@around_entry`:228、`@replace_entry`:237、`@quick_action`:246。`kind` 默认 `"action"`（`custom_event` 为 `"custom"`）
  `[FACT] plugin/sdk/plugin/decorators.py: on_event 与 plugin_entry 签名的 kind 默认 "action"、custom_event 签名的 kind 默认 "custom"。这三处都是 `kind: EntryKind` 那一行，行号 64、95、182，读于 e14f61963，2026-11-17；`_PluginDecorators` 另有私有镜像，行号 263、302、391，纯 grep 会多命中）`。
- 所有入口、生命周期、定时器、消息处理器**必须 `async def`**。
- 一律从 `plugin.sdk.plugin` 导入；**`plugin.sdk.shared` 是内部实现细节，不应直接导入**（这句的出处是下一行引的 sdk 根 docstring；SKILL.md 只禁「新增」shared 导入，口径更窄）
  `[FACT] plugin/sdk/__init__.py:9-10（docstring 原文：shared 是 internal implementation detail、should NOT be imported directly by plugin developers）；.agent/skills/neko-plugin/SKILL.md:18（Prefer public SDK facades 那句，69 同文档）、docs/plugins/sdk-reference.md:29（supported developer import surface 那句）口径较窄，前者只禁「新增」shared 导入，后者只谈受支持导入面（均读于 db81c1be0，2026-11-17；旧 plugin/PLUGIN_DEVELOPMENT_GUIDE.md 已从 main 删除，全 profile 的 GUIDE: 速记失效）`（`plugin/sdk/shared/__init__.py` 的 docstring 在第 3 行自述 `shared` 是 lower-level primitives，部分子包仍在演进；行号读于 db81c1be0，2026-11-17）。
- ctx 侧：`push_message` `[FACT] plugin/core/context.py: push_message`（1050 行，pinned db81c1be0，2026-11-17 实读；部分 v1 参数上游已标 deprecated）、`push_message_async` `[FACT] plugin/core/context.py: push_message_async`（1509 行，读于 db81c1be0，2026-11-17；只是 `asyncio.to_thread` 包同步 `push_message`，不做参数翻译）、
  `export_push` / `export_push_async` 共支持 **五类** `export_type`：`text` / `json` / `url` / `binary` / `binary_url`，其余值抛 `ValueError`；`export_push_{text,binary,url,binary_url}` 各有 普通 / `_async` / `_sync` 三个包装，**`json` 没有包装**，只能 `export_push(export_type="json", json_data=...)`（要显式异步就同参换 `export_push_async`；包装只覆盖 text/binary/url/binary_url 四类）；内联 `binary` 受 `EXPORT_INLINE_BINARY_MAX_BYTES` 限制 `[FACT] plugin/core/context.py: _export_push_async 的 export_type 分支（737-756 行，pinned db81c1be0，2026-11-17 实读）`；
  `run_id` 只有经 `/runs` 执行的入口才自动拿到：宿主从 `args._ctx["run_id"]` 注入运行域（`plugin/core/entry_executor.py: _run_scope(host_context.get("run_id"))`，130 行，读于 db81c1be0，2026-11-17）。**需要** `run_id` 的只有 `ctx.export_push*` 与 run 状态/进度类（`run_update*` / `run_progress*`）：它们走 `require_run_id`，在 `/runs` 之外调用又没显式传 `run_id=` 才抛 `RuntimeError`；`push_message` / `push_message_async` **不需要** `run_id` `[FACT] plugin/core/context.py: require_run_id（637-641）、_export_push_async def 710 与调用 727、_run_update_async def 869 与调用 882；均读于 db81c1be0，2026-11-17`。
- 使魔把可见入口当工具调，只拿到交办那句话＋用户原话（无对话历史）：入口的 `description` / `input_schema` 必须脱离对话历史自解释；叫停时宿主调 `POST /runs/{id}/cancel`，运行中的入口可能被 `asyncio.CancelledError` 中途打断（做到一半不能停的操作要自己收尾）；同一入口可能被**重复调用**，所以写文件 / 发消息 / 扣款类入口要幂等；`args._ctx` 注入 `lanlan_name` / `conversation_id` / `entry_timeout` / `latest_user_request`。`[FACT] 符号存在性已在 main e14f61963 由探针核过（2026-11-17；语义实读仍是 2026-11-15 那次）：入口当工具 = nekoclaw/host/gateway.py: _definition；无对话历史 = nekoclaw/host/task_agents.py: TaskAgentManager._brief；_ctx 四键注入 = nekoclaw/host/plugin_runs.py: PluginRunner.run_entry（run_id 另由 plugin/runs/manager.py: _execute_run 补；latest_user_request 用 if latest_user_request: 条件写入，她自发起的轮次可能缺省，读它要带默认值）；叫停路由 = plugin/server/routes/runs.py: runs_cancel，agent 侧发起 = nekoclaw/host/plugin_runs.py: PluginRunner._send_cancel；被打断 = plugin/core/rpc.py: RpcDispatcher.submit（RPC_CANCEL → task.cancel()）＋ plugin/core/entry_executor.py: execute_entry_command（CancelledError → CANCELLED）；control_ingress.py 只在取消准入处（ControlIngress._admit）；并发 = config/nekoclaw_settings.py: TASK_AGENT_MAX_CONCURRENT；issue #3683 §4.2『没变的』_ctx 条、§4.2『行为变了』第 2/4/5/7 条、§5 自查 5、6、8`
- `@plugin_entry` 可声明 `params`（Pydantic 自动生 `input_schema`）、`llm_result_model` / `llm_result_fields`（`@llm_tool` 只有 `parameters`=手写字面 JSON Schema，无这三项）
  `[FACT] plugin/sdk/plugin/decorators.py: plugin_entry 签名（params/llm_result_fields/llm_result_model 94/100/101）、docs/plugins/sdk-reference.md: LLM Result Field Filtering 节 246；宿主侧只透传已解析的 input_schema/llm_result_schema：plugin/core/communication.py: _handle_entry_update（929-931）、plugin/core/registry.py: `_set_declared_result_fields` def 在 832 行、`_router_entry_preview` 在 847 行（该节到 876）；以上行号均读于 db81c1be0，2026-11-17；两文件内 llm_result_model 零命中；旧 GUIDE 已从 main 删除`。
- **最小骨架四件**（优先跑 §8 的 `uv run neko-plugin init <id>`；无 CLI 时按这四件手写，不要自造 key）：
  ① `plugin.toml` 的 `[plugin]` 段写 `id` / `name` / `version` / `type` / `entry`；② `from plugin.sdk.plugin import NekoPluginBase, Ok, neko_plugin, plugin_entry`；
  ③ `@neko_plugin class XPlugin(NekoPluginBase)` 内一个 `async def` 挂 `@plugin_entry(id="hello", name="Hello", description="...")`，返回 `Ok({...})`；
  ④ 业务逻辑与 SDK 分层：业务函数不 import SDK，stub 宿主注入后离线单测（`[FACT] plugin/neko_plugin_cli/templates/generator.py: _render_plugin_toml、_render_quick_start_init、_render_smoke_test`）

## 4. 能力面
- **明确支持** `[FACT]`：入口注册、消息推送、图片上传（`ctx.images`，`[FACT] plugin/core/context.py: images（属性 def 在 304 行，pinned db81c1be0，2026-11-17；旧锚 :274 命中的是私有字段）`）、
  模型调用（`ctx.models`，`plugin/core/context.py: models（属性 def 在 314 行，读于 db81c1be0，2026-11-17）`；HTTP 面 `POST /chat/completions`、`GET /usage`、`GET /slots`）、
  store/配置、`data_path`/`cache_path`、Hosted TSX 面板、HTML 卡片、跨插件调用、i18n、
  定时 / 生命周期 / 消息 / 自定义事件 / 入口拦截链 / 快捷操作（见 §3 装饰器全集）。
  `[FACT] 这六类各有对应装饰器（读于 e14f61963，2026-11-17；一条一个符号，免得探针只比前六个）：`
  plugin/sdk/plugin/decorators.py: `timer_interval` 的 def 在 156 行、`lifecycle` 在 127 行、
  `message` 在 137 行、`custom_event` 在 175 行、`before_entry` 在 210 行、`quick_action` 在 246 行。
  入口拦截链那一组的四个装饰器（`after_entry`、`around_entry`、`replace_entry` 与 `before_entry`）
  逐个记在 §3，锚点同为这个文件。
- **只读 OS 活动快照** `[FACT] plugin/sdk/plugin/activity.py: get_os_activity_snapshot 定义与 OsActivitySnapshot`：`await get_os_activity_snapshot(source="<插件 id>")` →（**`async def`，必须 await；`source` 必填**）
  `OsActivitySnapshot(os_signals_available, foreground_category, system_idle_seconds, privacy_state)`，
  `privacy_state ∈ visible|private|unavailable`。**拉取式，且只有这四个字段。**
- **明确不支持** `[FACT]`：Avatar 表情 / 动作 API、情绪修改 API、`[plugin.host]`；插件包 `type` 无 `extension`（上游 `.agent/skills/neko-plugin/SKILL.md:18` 仍把 `plugin.sdk.extension` 列为公共门面、:27 还把 extension 当包类型，但 db81c1be0 的 `plugin/sdk/` 下没有 extension（仅 `__init__.py`、adapter、hosted-ui、plugin、shared），`plugin/sdk/__init__.py` 里 `__getattr__` def 在 36 行、`__dir__` def 在 46 行，这两个函数只放行 plugin 与 adapter（行号读于 db81c1be0，2026-11-17；文件头的说明文字也只看这两项）——那两行 SKILL.md 是过时文档，照它 import 会 ModuleNotFoundError）；
  在 `plugin/sdk` 全树 grep `avatar|emotion|petting|mouse|pointer` **零命中** →
  **没有桌面鼠标/触摸事件推送**，插件拿不到"摸头"这类宿主交互事件，只能读上面的快照。
- 降级路径：表情/交互诉求 → HTML 卡片或视图表达；"用户在不在、在忙什么" → `get_os_activity_snapshot`
  （别承诺能拿到点击/触摸事件）。

## 5. 错误约定
- 成功 `Ok({...})`，失败 `Err(SdkError("原因"))`；七个名字都从 `plugin.sdk.plugin` 导出
  `[FACT] plugin/sdk/plugin/__init__.py: Ok 55、Err 56、Result 57、unwrap 58、unwrap_or 59、SdkError 69、TransportError 70（读于 e14f61963，2026-11-17）`。**不要 `raise`**（定时回调里 raise 会拖垮调度）。
- 工具错误返回脱敏错误码；别把原始对话 / 用户密钥 / 隐私 payload 写进日志或结果。

## 6. 硬性限制速查
- `push_message` 单条 payload 默认 **512 KiB**，可经 `NEKO_MESSAGE_PLANE_PAYLOAD_MAX_BYTES` 调整；
  插件侧与宿主 ingest 共用同一常量（防止限制漂移）`[FACT] plugin/settings.py: MESSAGE_PLANE_PAYLOAD_MAX_BYTES 常量块`。大图先 `ctx.images.upload` 换 URL part。
- **持久数据一律写 `data_path` / 缓存写 `cache_path`** `[FACT] docs/plugins/sdk-reference.md 持久数据表（78-79）与 data_path / cache_path 两节（134、143，读于 db81c1be0，2026-11-17；GUIDE 已删除；本节其余锚点未标 sha，按文首基线声明沿旧基线）`；
  `self.plugin_dir` 是随包安装目录（代码 / manifest / 静态资源）`[FACT] plugin/sdk/plugin/base.py: plugin_dir 定义（118-121，读于 db81c1be0，2026-11-17；同文件 123-126 的 config_dir 上游自述是 plugin_dir 的兼容别名，不是第四个目录，别往里写）`、`docs/plugins/sdk-reference.md:48`（plugin_dir 那行表格；GUIDE 已删除，旧锚作废），
  **不是可写区**。（`plugin_dir` 的强制只读由本机蒸馏文档 `neko-plugin-dev/reference/sdk-core.md` 第 20 行与第 40 行断言，
  本轮未在上游逐行核到拦截点 → 该"强制层"记 `[未核验]`；但"往安装目录写"一律按平台约定封驳。）
- **Windows 数据根取 `LOCALAPPDATA`**（不是 `HOME` / `USERPROFILE`）`[FACT] utils/config_manager/storage_roots.py: _get_standard_data_directory_candidates（def 367，win32 分支 370 到 373，函数体到 381；读于 e14f61963，2026-11-17）`。
- 用户插件执行根可被 `PLUGIN_CONFIG_ROOT` 覆盖（**宿主已标 legacy**：环境变量仍生效，新代码用 `PLUGIN_CONFIG_ROOTS` / `USER_PLUGIN_CONFIG_ROOT`；`plugin.settings` 的同名属性已废弃并返回内置根），否则 `<数据根>/N.E.K.O/.neko-plugin-installations/plugins`；内置根 = 仓库 `plugin/plugins`
  `[FACT] plugin/settings.py 的 `get_builtin_plugin_config_root`:69-71 与 `get_user_plugin_exec_root`:102-118，读于 db81c1be0，2026-11-17`。用户配置在 `<数据根>/plugins/<id>/config/plugin.toml`。
- **插件启动超时 `NEKO_PLUGIN_STARTUP_TIMEOUT` 默认 `10.0` 秒，>300 直接 `ValueError`** `[FACT] plugin/settings.py:314-315（PLUGIN_STARTUP_TIMEOUT）、766-769（>300 的 raise 在 768-769，读于 db81c1be0，2026-11-17）`。
- `@llm_tool` 的 `callback_url` 与 `/api/tools` **只接受 127.0.0.1**。
- 外部文本不得拼进系统提示词；URL 拼接必须 `quote`。

## 7. 沙箱与验证命令
- standalone：`build_plugin_server_app(manage_lifecycle=True)` `[FACT] plugin/user_plugin_server.py: 模块级 app = build_plugin_server_app(...)（:81 读于 db81c1be0，2026-11-17）`；
  该参数**默认 False** `[FACT] plugin/server/http_app.py: build_plugin_server_app 的 manage_lifecycle 形参`, autostart 由 `if manage_lifecycle: await lifecycle_startup()` 决定
  `[FACT] plugin/server/http_app.py: plugin_server_lifespan 内 startup(:198) 与 shutdown(:255) 两处 if manage_lifecycle（行号读于 db81c1be0，2026-11-17）`。
- **`NEKO_PLUGIN_HOSTED_BY_AGENT` 已不是开关**：只剩 agent_server 在写 `[FACT] app/agent_server/api_runtime.py: startup() 内 os.environ 赋值（:559 读于 db81c1be0，2026-11-17）；main 全库检索其余命中仅 http_app 注释与 3 个测试文件`；
  `[FACT] plugin/server/http_app.py: _EMBEDDED_BY_AGENT 那句注释在 193 行，位置就在 manage_lifecycle: bool = False 那行（280）上方，明说早先读它会在 import 时冻结（读于 e14f61963，2026-11-17）`。
- Windows 强制 `WindowsSelectorEventLoopPolicy` `[FACT] plugin/user_plugin_server.py: _configure_windows_event_loop_policy（def :59、set_event_loop_policy :66、import 时调用 :74；三处行号读于 db81c1be0，2026-11-17；仅在 win32 且该类存在时生效）`。
- 端口 env `NEKO_USER_PLUGIN_SERVER_PORT`（兼容名 `USER_PLUGIN_SERVER_PORT`，再退回 Electron 写的 `port_config.json`），上游默认 `48916`
  `[FACT] config/network.py: USER_PLUGIN_SERVER_PORT = _read_port_env("USER_PLUGIN_SERVER_PORT", 48916)`（:193 读于主 sha db81c1be0，2026-11-17；符号才是锚点；`resolve_user_plugin_base` 在调用时重读 env，非仅 import）
- 「占用时向后找并回写」只在 standalone 入口成立，不在 config 层：`_find_available_port` 从 base_port 起最多试 50 个端口（def :150、调用 :180），扫不到就 `RuntimeError`；回写只进本进程 `os.environ`（`__main__` 块内，:184），兄弟进程读不到
  `[FACT] plugin/user_plugin_server.py: `_find_available_port` def 在 :150，选端口那行调用 `_find_available_port` 在 :180，把结果回写进 `NEKO_USER_PLUGIN_SERVER_PORT` 在 :184；行号读于 db81c1be0，2026-11-17`
- 给全家用的端口由 **launcher** 定：`launcher_core.runtime.apply_port_strategy` 在 spawn 任何子进程前写好 env；嵌入态（agent_server 托管 app）只绑拿到的端口，绑不上就失败，不向后找；沙箱里别写死 48916，读 standalone 日志 `User plugin server starting on`  `[FACT] config/network.py: resolve_user_plugin_base docstring（203-216 行读于 db81c1be0，2026-11-17；launcher 本体未读，此半句置信度来自上游注释）`
- **只读判定路由** `[FACT]`（函数名与行号同读于 db81c1be0，2026-11-17。每个号都带全路径与函数名：裸文件名与光秃秃的行号探针看不见，上一版就是这样漏过了 metrics 与 logs 两组）：
  无 token 可探活：plugin/server/routes/health.py 的 `health()`:20 就是 `GET /health`、`available()`:25 就是 `/available`；plugin/server/routes/plugins.py 的 `list_plugins()`:42、`plugin_status()`:35、`get_plugin()`:53；plugin/server/routes/messages.py 的 `get_plugin_messages()`:22。
  hosted UI 同样不带 require_admin，全在 plugin/server/routes/plugin_ui.py：`plugin_ui_surfaces()`:607、`plugin_hosted_ui_source()`:621、`plugin_hosted_ui_context()`:646、`plugin_ui_info()`:594、`plugin_display_sdk()`:293（那是 `/plugin-ui-sdk/display.js`）。
  这批只读路由要 `require_admin`："只读"不等于"免 token"。plugin/server/routes/health.py 的 `server_info()`:33；plugin/server/routes/metrics.py 的 `get_all_plugin_metrics()`:26、`get_plugin_metrics()`:34、`get_plugin_metrics_history()`:42；plugin/server/routes/logs.py 的 `get_plugin_logs_endpoint()`:81、`get_plugin_log_files_endpoint()`:104、`get_plugin_log_directory_endpoint()`:112、`export_plugin_log_endpoint()`:120；plugin/server/routes/config.py 的 `get_plugin_config_endpoint()`:108。
- **变更类路由的鉴权与只读路由不同，别拿来当探活**：plugin/server/routes/plugins.py 只有一份 `router = APIRouter()`:28，变更路由靠形参 `require_admin` 加 `Depends(require_plugin_mutation_access)` 把关。带 `mutation_router` 的一共 **7 个**路由文件，各自那一行就是 `mutation_router = APIRouter(route_class=...)`：plugin/server/routes/runs.py 的 `mutation_router`:26、plugin/server/routes/config.py 的 `mutation_router`:33、plugin/server/routes/plugin_ui.py 的 `mutation_router`:46、plugin/server/routes/model_config.py 的 `mutation_router`:25、plugin/server/routes/plugin_install.py 的 `mutation_router`:44 用 `PluginPageMutationGuardedRoute`；plugin/server/routes/plugin_cli.py 的 `mutation_router`:21 与 plugin/server/routes/plugin_projects.py 的 `mutation_router`:31 用 `PluginMutationGuardedRoute`（七个文件逐个 grep，routes 目录下其余文件零命中）。
  变更路由逐个记号（同读于 db81c1be0，2026-11-17）：plugin/server/routes/plugins.py 的 `start_plugin_endpoint`:88、`stop_plugin_endpoint`:130、`reload_plugin_endpoint`:196、`set_plugin_auto_start_endpoint`:159、`refresh_plugin_endpoint`:118、`refresh_plugins_endpoint`:184、`delete_plugin_endpoint`:172、`reload_all_plugins_endpoint`:208；
  plugin/server/routes/runs.py 的 `runs_create`:35、`runs_create_upload`:52、`runs_cancel`:91、`uploads_put`:74。
- **易错路由**：集合 `GET /runs` 不存在（无该路由，按 Starlette 路由匹配回 405，未实测），但单个 run 有 `GET /runs/{run_id}`、`/blobs/{blob_id}`、`/export`
  `[FACT] plugin/server/routes/runs.py: runs_get 45、runs_get_blob 83、runs_export 109 三个读取入口（装饰器在 44、82、108）；行号读于 db81c1be0，2026-11-17`；裸 `/messages` 不存在（真实路径 `/plugin/messages`）。打错会误判成插件故障。
- **autostart 判定链（缺一即不启动）**：`manage_lifecycle=True` ＋ `[plugin_runtime] auto_start=true`（省略即 false）
  ＋ `enabled`/`runtime_enabled` 非 `False` ＋ `runtime_load_state != "failed"` ＋ 源未缺失 ＋ id 不在待审批集合 ＋ 非工作台项目（注册表 meta.source ≠ PROJECT_SOURCE，六个过滤集中在 `plugin/server/application/plugins/registry_service.py: _get_autostart_plugin_ids_sync`（1254-1275，读于 db81c1be0，2026-11-17））
  `[FACT] plugin/config/schema.py: PluginRuntimeSchema.auto_start（:267）、plugin/core/communication.py: _handle_entry_update 以默认 False 复制 auto_start（:939，入口级默认，非 [plugin_runtime] 节默认）；均读于 db81c1be0，2026-11-17`、`plugin/server/infrastructure/autostart_approvals.py`、
  `[FACT] plugin/server/application/plugins/query_service.py: get_autostart_pending_snapshot 导入（:22）与 _prepare_plugin_projection（534-553，行号读于 db81c1be0，2026-11-17）`——`autostart_pending` 出现在 `GET /plugins` 与 `GET /plugins/{id}` 的每个插件卡上，**`/plugin/status` 不含该字段**（它走 status_manager）。
- 静态检查 CLI 动词实测 **11 个**：`init`、`setup-repo`、`create-repo`、`sync`、`check`、`lint`、`precheck`、
  `build`、`install`、`analyze`、`publish` `[FACT] plugin/neko_plugin_cli/cli.py: build_parser 里的 9 个 *_cmd.register 调用（main db81c1be0 于 2026-11-17 实测为 105-113 行）` + 各 `*_cmd.register`
  （`sync` 由 `deps_cmd.py` 注册；`validate_cmd.py` 与 `release_cmd.py` 都存在但**不注册动词**：`release_cmd` 是 check/precheck/publish 的内部引擎，`validate_cmd` 由 `release_cmd` 调用）。
  **没有 `run` / `release` / `deps` 动词** —— 别按这些名字调，会 unknown command。
- 沙箱脚本：`skills/neko-plugin-sandbox/scripts/sandbox.py`（`doctor` / `prepare` / `sync` / `install` / `plugin-add` / `plugin-rm` / `serve` / `start` / `status` / `stop`）。原 `neko-plugin-ops/` 那份已在 v2.0.0 退役（AUDIT.md 整合记录），缺 plugin-id 校验与停止前的进程身份检查，不要用。`inspect` 是宿主 `neko-plugin` CLI 的输出对象，不是沙箱子命令。
  加载插件前 `uv run neko-plugin check <id>` 需 0 error；`inspect` 出现 "declared but NOT registered" = entry-404 根因。

### 7.1 打包与自安装（本机实测 2026-11-12；下列符号已在 main `e14f61963` 复核存在（2026-11-17），并把每个符号的行号一并写出来。以前只写符号名，探针看不见这一节，行号漂了也没人知道）

- build 的 `-o/--out` **必须是完整产物文件名**（`...\<id>-<版本>.neko-plugin`）。传目录的后果：
  `export_package` 先把该路径当产物文件写出来（`[FACT] plugin/neko_plugin_cli/core/build.py: export_package（def 435），体内 `package_path.parent.mkdir(parents=True, exist_ok=True)`（441）再 `with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive`（447），读于 e14f61963，2026-11-17`），随后 `BuildResult` 才校验扩展名并
  报错（`[FACT] plugin/neko_plugin_cli/core/models.py: `class BuildResult`（277）与 `_validate_layout`（292，体内 293 行先 is_file 再判后缀；读于 e14f61963，2026-11-17；正因 export_package 已写出垃圾文件，is_file 通过，看到的才是扩展名错）`）⇒ 现场留下一个「拿目录名
  当文件名」的垃圾文件。下一次改成正确写法时那句 mkdir 撞上这个同名文件，报 `[WinError 183]`
  （已存在的**目录**反而被 exist_ok 吞掉）。补救：先删掉那个垃圾**文件**。
  同一个根因的三种表现（连败 4 次约 12 分钟）：`-o` 给已存在目录 → `[Errno 13] Permission denied`
  （zip 打不开目录写）；`-o` 给不存在的名字 → 先落一个垃圾文件、再报 pydantic 扩展名错；下一次
  正确写法落进那个垃圾文件名下 → `[WinError 183]`。别把 `Errno 13` 误判成沙箱不许写 Downloads。
- 自安装两步（loopback API，不带 Origin；`cli install` 被宿主禁用时的唯一路径）：
  `POST /plugin-cli/install-plan {"package": "<绝对路径>"}` → 取 `action` 与 `confirmation_token`
  `[FACT] plugin/server/routes/plugin_cli.py: `install-plan` 路由（350，读于 e14f61963，2026-11-17）`；
  `POST /plugin-cli/install {"package":…, "confirm_upgrade": true, "confirmation_token": …}`
  `[FACT] 同文件 install 路由（327，挂 mutation_router，鉴权与只读路由不同）、`plugin/server/application/plugin_cli/service.py` 的 on_conflict（300、438）与 confirmation_token（59、305）字段，on_conflict 只接受 fail；`must be inside` 那句在 210。读于 e14f61963，2026-11-17`。
  成功判据四字段：`payload_hash_verified=true`、`operation`（upgrade/reinstall）、`restarted`、
  `rollback_status`。实测 43.5 MB 包 install 用时 13.1 秒。
- `install-plan` 的 `package` **必须位于 `.neko-plugin-packages` 根内**，否则 400
  「package '…' must be inside …」`[FACT] plugin/server/application/plugin_cli/service.py: _require_within（def 205、第一处调用 546，读于 e14f61963，2026-11-17）` ⇒ 产物先复制进该目录再调 API；`Downloads` 里的包不能直接装。
- 装完四件套核对（缺一不算交付）：执行目录逐文件哈希 == 包内载荷；`plugins.lock.json` 的
  `package_sha256` == 包哈希；`GET /plugins` 的版本号 == 新版本；`GET /plugin/{id}/logs` 0 ERROR/Traceback。
- Windows 执行坑（同日两次实测代价）：**PowerShell 管道喂 Python 会把非 ASCII 变成 `?`**。
  含中文的脚本必须 `$s=@'…'@; Set-Content -LiteralPath <file> -Encoding UTF8; python <file>`，
  不要写成 `@'…'@ | python -`——后果一次是「断言字符串不匹配」假失败，一次是 API 中文入参存成
  `???`。Python 往 PowerShell 控制台打印中文仍需
  `io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")`。
- 沙箱内删文件：`Remove-Item -Recurse -Force` 会触发审批（策略可自动拒绝）；删自己刚建的单个文件
  用不带 `-Recurse` 的 `Remove-Item -LiteralPath <file>` 就能过。

## 8. 生态与市场
- 市场：<https://project-neko.online/zh-CN/plugins/>；仓库：`Project-N-E-K-O/N.E.K.O`（个人 fork 属主用 `gh api user -q .login` 探测，勿写死——本机账号已由 addsas222 改名 HorizonCloudy，旧名 URL 靠 GitHub 重定向续命，但 gh 按旧名 list/搜索会挂）。
- 开发教程：仓库 `docs/plugins/`（sdk-reference.md / entries.md / tool-calling.md 等，旧 `plugin/PLUGIN_DEVELOPMENT_GUIDE.md` 已从 main 删除，发布检查清单现在 `docs/plugins/cli.md:309` 的 `One-page checklist` 一节，另一份散在 `docs/plugins/best-practices.md:220` 的 `Plugin checklist`，对当前 main 实读于 2026-11-18）；脚手架：`uv run neko-plugin init <id>`。
- 宿主侧市场桥接前缀 `/market`（catalog / submissions / install / oauth / status 等）
  `[FACT] plugin/server/routes/market_bridge.py: router = APIRouter(prefix="/market")（:91 读于 db81c1be0，2026-11-17）`；同一文件里 `plugin/server/routes/market_bridge.py` 的四段路由：`catalog` 从 761 到 816、`submissions` 从 1060 到 1166、`install` 从 1709 到 1898、`oauth` 从 2109 到 2618。
  已失效的旧号（只作历史，不再当锚点）：87 与 757-812、1055-1150、1693-2049；2026-11-17 又失效一批：90、760-815、1059-1154、1708-1897、2108-2617。知识库桥 `/market/knowledge` `[FACT] plugin/server/routes/knowledge_bridge.py: router = APIRouter(prefix="/market/knowledge")（:48 读于 db81c1be0）`。
- **插件制作工作台不是纯前端**：`/plugin-projects/*`（`APIRouter(prefix="/plugin-projects", dependencies=[require_admin])`
  `[FACT] plugin/server/routes/plugin_projects.py: router = APIRouter 30、plugin_project_toolchain 163、plugin_project_publish_status 171、latest_plugin_project_publish_task 196（行号读于 db81c1be0，2026-11-17）`
  与 `/plugin-cli/*` 在宿主进程里调 CLI `[FACT] plugin/server/routes/plugin_cli.py: list_plugin_cli_plugins 265、list_plugin_cli_packages 273、plugin_cli_build 281、plugin_cli_inspect 305、plugin_cli_verify 316、plugin_cli_install 327、plugin_cli_install_plan 350（行号读于 db81c1be0，2026-11-17）`。
- **沙箱不验 Market 提审/发布**（要真实 GitHub 凭据，触红线）；最多验到桥接可达 + 未授权时干净拒绝。
- 市场惯用法历史样本数据在本仓库 `references/neko/neko-market-patterns.md`（原 `neko-plugin-ops/` 那份随 v2.0.0 退役；127 样本 @2026-10-26，分母固定）。

## 9. 高频封驳原因（NEKO 特有）
1. 方案要表情 / 动作 / 情绪 / 桌面交互事件 API（平台没有；只有 §4 的只读活动快照）
2. 把 `@llm_tool` 当运行时入口用（应 `@plugin_entry` ＋ `llm_result_fields` / `llm_result_model`）
3. 定时回调里 `raise`
4. 外部文本拼进系统提示词
5. 没写失败降级
6. 把"没 autostart"归因于目录名或环境变量（真因在 §7 那条判定链，尤其 `auto_start` 默认 false 与待审批集合）
7. 拿 `GET /runs` 或裸 `/messages` 探活（405 / 404，正确路由见 §7）
8. 往安装目录写文件 / 改写宿主配置（含"插件自注册"）—— 违反 §6
9. 调用不存在的 CLI 动词（`run` / `release` / `deps` / `validate`）—— `[FACT] plugin/neko_plugin_cli/cli.py:105` 起是那 9 个 `*_cmd.register` 调用，里面没有这四个动词，读于 e14f61963，2026-11-17`
10. 入口的 `description` / `input_schema` 脱离对话历史就填不对；或入口被中途取消后留半态、被重复调用不安全（见 §3）
11. Release job 的 `permissions:` 缺 `id-token: write` / `attestations: write` → 每次 tag 的 Release job 直接 `startup_failure`（1 秒即挂，无步骤日志）。判定 = Release run 1s 内 startup_failure 且无步骤日志。先查再生效：模板 `plugin-market-actions-v1` 的 `render_release_workflow` 在 main（`plugin/neko_plugin_cli/templates/generator.py:859-871`，`def render_release_workflow` 在 859，三项 permissions 在 869-871）与本 profile 钉的两个基线上都已写全 `contents` + `id-token` + `attestations` 三项，所以新生成的 `release.yml` 不该缺；缺的是改名前旧模板产出的历史文件。修法：只给自己仓库那份 caller 补齐三项，别改上游。
12. 缺 `.vscode/settings.json` / `tasks.json`：Market 的 Release check（`neko-plugin check -r`）硬性要求这两个文件存在且为合法 JSON，缺失即阻断发布；Verify 流程先挂 Ruff 时该错误从不暴露（步骤顺序掩盖）。修法：`neko-plugin setup-repo <dir> --no-readme --no-tests --no-gitignore` 只生成支持文件；入库前把 `nekoPlugin.repoRoot` 的本机绝对路径改为空串（公开仓不带机器路径）。

## 10. 复核记录
- **2026-11-10 对齐上游 main `d597dae`（实读源码）**：
  ① §4 新增「只读 OS 活动快照」支持项——旧版把桌面能力一刀切否掉，会**误封**只需"用户忙闲 / 前台类别 / idle 秒数"的方案；
  同时把"不支持"精确到**无鼠标/触摸事件推送**（`plugin/sdk` grep 零命中）。
  ② §3 补齐装饰器全集与 `@before/after/around/replace_entry` 拦截链、`@quick_action`（旧版只列 3 个入口，会漏判可实现性）；
  补 `plugin.sdk.shared` 禁导入的上游出处（原 GUIDE:1717；GUIDE 已删除，现锚 `.agent/skills/neko-plugin/SKILL.md:18（Prefer public SDK facades 那句）、69` 与 `docs/plugins/sdk-reference.md:29`（supported developer import surface 那句），db81c1be0）。
  ③ §7 路由表按源码逐条带行号重写：`/runs` 语义由"只有 POST"精确化为"集合无 GET、单个 run 有 GET"；
  新增 `logs*` / `metrics` / `surfaces` / `hosted-ui/*` / `PUT .../auto-start` / start|stop|reload|refresh；
  并区分只读路由与变更路由（2026-11-15 修正：plugins.py 无 `mutation_router`，其变更路由靠 `require_admin` + `require_plugin_mutation_access`；`mutation_router` 在 runs / config / plugin_ui / model_config / plugin_install / plugin_cli / plugin_projects 七个文件，以 §7 为准）。
  ④ 补 `NEKO_MESSAGE_PLANE_PAYLOAD_MAX_BYTES`(默认 512 KiB，可调)、`NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT`(38865)、
  `NEKO_PLUGIN_STARTUP_TIMEOUT`(10.0s，>300 raise)、Windows 数据根 `LOCALAPPDATA`、执行根 `.neko-plugin-installations/plugins`、
  `[plugin_runtime]` 字段与 autostart 判定链。
  ⑤ CLI 动词实测 11 个，确认 `validate` 不注册动词、无 `run/release/deps`。
  ⑥ 确认 `type` 仅 `plugin|adapter`、`[plugin.host]` 已移除并报错。
- **2026-11-11 NekoClaw 重写公告（issue #3683，六个 PR 直进 main，PR3 是切换点）**：
  插件稳定契约由宿主承诺守住（/runs、push_message、@llm_tool、args._ctx 的
  lanlan_name/conversation_id/entry_timeout/latest_user_request、delivery 元数据）；但**调用方从两阶段判断器
  换成"使魔"把入口当工具调**——description/input_schema 要能脱离对话历史自解释、
  取消真正打到插件（POST /runs/{id}/cancel）、默认**同时干活的使魔上限 10**（`config/nekoclaw_settings.py: TASK_AGENT_MAX_CONCURRENT`，不是入口并发）、同一入口可能被重复调用。
  PR3 删旧分析器的派工路径（`neko_channel.py` 等）与 `brain.*` / `app.agent_server` 内部件；**48915 Tool Server 与端口不变**（设计稿「进程形态不变」段、`[FACT] config/network.py: TOOL_SERVER_PORT = _read_port_env("TOOL_SERVER_PORT", 48915)`（:192 读于 db81c1be0，仍可被 `NEKO_TOOL_SERVER_PORT` 覆盖））；PR4 删随包 Chromium +
  playwright 运行依赖。新插件一律不写 48915/brain.* 依赖、不靠宿主 playwright；
  重核义务已落实为文首 ⓪ 陈旧门禁（一条命令自动判定，不靠记性）。
- **未核验**：应用版本号（`pyproject.toml:7` 为 `dynamic = ["version"]`，`[tool.hatch.version]` 指向 `APP_VERSION` 常量，本轮检出路径未含该文件，分支线索为 `chore/bump-0.9.1`）；
  `plugin_dir` 强制只读的拦截点（见 §6 标注）。SDK 升版需重核。
