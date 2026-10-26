# mcbot_bridge — mcbot 同伴桥接

把本地 **mcbot**（Fabric 假人同伴，自带 LLM 大脑）接进 N.E.K.O.：猫娘可以指挥同伴做事、
替同伴向你转达问题、并实时转播同伴的进展。

这是 `game_agent_minecraft`（接 mc-agent，裸 WebSocket 48909）的**同形兄弟**，
但接的是另一条线、另一种传输。

---

## 它连的是什么

mcbot 出于安全考虑只在**回环地址**上开桥，而且**只在 Minecraft 客户端处于某个世界时才开**——
进世界时启动，断开时关闭。所以"连不上"是常态而非故障：Minecraft 没开、或者停在主菜单上，
桥就是不在。

| 面 | 端点 | 说明 |
|---|---|---|
| REST | `POST /v1/task` `{text, wait_s}` | → `{task_id, done, status?, fragments[]}`；`wait_s` 为**整数**，`0` 立刻返回 |
| | `POST /v1/task/{id}/cancel` | → `{ok}`；**id=0 表示取消全部** |
| | `POST /v1/ask` `{text}` | 同步问答，135s 后才放弃（504 ANSWER_TIMEOUT） |
| | `POST /v1/answer` `{question_id, text}` | → `{ok}`；未知/过期 id → 404 NOT_FOUND |
| | `GET /v1/status` | 状态快照（含 `contract_version` / `session_id`） |
| SSE | `GET /v1/events` | `progress` / `done` / `question` / `state`；200 条环形缓冲；支持 `Last-Event-ID` 重放；15s 心跳 |

鉴权是 `Authorization: Bearer <token>`，token 就是 `<gameDir>/mcbot/bridge.token` 的内容。
错误体是 `{error, error_code}`，`error_code` 是固定类别（`UNAUTHORIZED` / `NOT_FOUND` /
`BODY_TOO_LARGE` / `ANSWER_TIMEOUT` / `TASK_FAILED` / `INVALID_REQUEST` / `INTERNAL`）；
`error` 是给运维看的散文，**本插件从不转发它**。

### 契约版本与会话（v1.0）

本插件按 **桥接 v1.0** 写，下面几条不是可选优化，是契约要求：

- **`session_id` 是桥实例标识**，每次进世界新建桥就换一个，且**每个新桥的 SSE id 从 1 重新开始**。
  所以断线后必须先查 status：会话没变才复用 `Last-Event-ID`；
  **会话变了就把游标清零**，否则新会话的 1..N 帧会被"id 大于旧游标"这个条件整段静默吞掉——
  不报错，只是没有。
- 会话变化时，**上一个会话的任务标为未知**（既不是成功也不是失败——桥走了，它没法交代），
  未答的反问作废，**不自动续跑、不重投**。契约明确禁止把连接丢失自行报成成功。
- `contract_version` 必须读到。桥报的不是 `1.0` 时按"旧版兼容接入"处理并告警，
  不能当作 v1.0 验收通过。
- **`done.status` 只有 `completed` 算成功**。`failed` 报失败；`cancelled` / `superseded`
  是"被有意停掉/取代"，**不产生任何播报**（模型自己知道，播了只会引发重投风暴——
  与 `game_agent_minecraft` 踩过的同一个坑）。
- **命令类指令的 `completed` 不是"做完了"**。mcbot 把 `恢复目标` / `目标状态` / `放弃目标`
  当命令处理，三者都以 `completed` 收尾，但按 `mcbot/docs/BRIDGE.md`，命令的 `completed`
  **只表示受理**：`恢复目标` 之后真正的执行在新 `task_id` 上继续，`目标状态` 则根本不动世界。
  所以连接器按**派发时的指令原文**识别这三条，走 `CUE_COMMAND_RECEIPT`（"这是回执"），
  不套"我这边做完了 / 我已经停下来了"的框架。
- **`done` 的正文只有 cue 一条通道**（工具调用本身返回空壳），因此完成报告不再截到 600 字；
  只在超过 `DONE_TEXT_LIMIT`（4000）时截断并显式标注"后半段省略"。
- 桥在**提交任务前**就先取了事件游标，所以一个极快的 `done` 可能在 `POST` 返回之前
  就到达 SSE。这类帧先**暂存**，等拿到 `task_id` 再归组，避免归错任务。
- SSE 游标**只在整帧处理成功后前移**；处理失败则留在原处，靠重连补发而不是丢帧。

**mcbot 还有一条 MCP 面**（`POST /mcp`）。本插件**刻意不走它**，原因见"为什么不用 MCP"。

---

## 两条通道的分工

插件对外的能力分给两个不同的调用者，二者**互斥可见**：

| 能力 | 通道 | 谁调用 | 为什么 |
|---|---|---|---|
| 派活、提问、答复、取消 | `@llm_tool` | **对话 LLM** 直接调 | 「去挖点木头」得由正在跟用户对话的那个模型当场拍板，且需要它的类型化参数 |
| 查状态、重载配置 | `@plugin_entry` | **analyzer**、插件面板、CLI | 这是事实与设置，不该占用对话模型的工具槽位；把"查状态"做成工具会诱导模型把查询当成一个动作派下去 |

互斥是**结构性**的，不是约定：

- `@plugin_entry` 永远进不了 main_server 的工具注册表——只有 `@llm_tool` 会发 `LLM_TOOL_REGISTER`。
- analyzer 看到的插件视图里，所有 `__llm_tool__` 前缀的条目会被剥掉
  （`app/agent_server/api_runtime.py` 的 `_http_plugin_provider`，注释写明了理由：
  避免同一个工具被两个延迟量级不同的 LLM 抢着触发）。

第三条是**反向**通道：SSE 事件被翻译成 `push_message`，于是猫娘能主动反应，不需要任何人轮询。

| SSE 事件 | 分类 | ai_behavior | visibility | priority | coalesce_key |
|---|---|---|---|---|---|
| `progress` 工具回执 `ok=true` | 纯叙事 | `blind` | `["hud"]` | 4 | `mcbot_progress` |
| `progress` 工具回执 `ok=false` | 中途失败 | `read` | `[]` | 4 | `mcbot_progress` |
| `progress` 护栏 / 长活中间帧 | 不给模型 | `blind` | `[]` | 4 | `mcbot_progress` |
| `done` | 终局 | `respond` | `[]` | 7 | `mcbot_completion` |
| `done`（命令类：恢复/状态/放弃目标，`completed` 只是受理） | 回执 | `respond` | `[]` | 7 | `mcbot_completion` |
| `question` | 反问 | `respond` | `[]` | 8 | `mcbot_question` |
| `state` task_id=0（同伴会话事件） | 触发探针 | `read` | `[]` | 4 | `mcbot_state` |
| `state` 「主人已回答」 | 答案闭环 | `read` | `[]` | 4 | `mcbot_state` |
| `state` queued / running / parked | 不给模型 | `blind` | `[]` | 4 | `mcbot_state` |

`visibility=[]` 意味着用户看不到 cue 本身：模型吸收它，并在它开启的那一轮里**用自己的话讲出来**。
"猫娘在解说同伴"就是产品本身——兄弟插件收敛到了同一形态。
`blind` 完全压住注入（宿主 `character_runtime` 明写 blind 绝不进 LLM 通道）。纯叙事的进展不对模型讲话，
避免第一人称的"我在挖石头"被对话模型当成"我还在忙，不该再派活"。

### 通道验收表（改动通道时必查）

这份表记录每个字段由谁产生、宿主是否真的读它。凡是改动 cue 的字段、`visibility` / `ai_behavior`、
工具 schema，或新增"依赖宿主替我们做 X"的假设，都要对照此表逐行核对：

| 字段 | 生产者 | 消费侧 | 结论 |
|---|---|---|---|
| cue `metadata` | 插件 SDK 装进 wire | 宿主 `main_logic/core/callback_render.py` 只读 `summary` / `detail` | ❌ 到不了模型，不要用它传 `question_id` 之类的标识 |
| 桥帧 `companion` | 模组只盖 `contract_version` + `session_id` | 插件从 `/v1/status` 读取 | ❌ 帧里没有它，不要从帧里读 |
| progress 帧 `ok` | 模组 `AgentRunner` 的 `onToolInvoked` 发出 | 插件据此分类 | ✅ 用于区分失败与成功回执 |
| `visibility` / `ai_behavior` | 插件发 | 宿主显式校验并真分流 | ✅ 唯二验过的通道之一 |
| cue 正文 / 工具 schema | 插件 | 对话模型 | ✅ 模型可见的只有这两个通道 |

**模型可见通道只有 cue 正文与工具 schema**，不存在"不朗读的模型可见标识符"通道。
因此 `question_id` 由插件侧解析：`mcbot_answer` 省略它时，只有恰好一个仍有效的待答问题会被采用；
没有或多于一个则报错要求澄清，绝不取"最近的一个"。

---

## 验证状态（2026-10-10 首次真机）

**全链路已跑通**：对话 LLM → `mcbot_task` → 桥 → 同伴执行 → SSE → cue → 宿主 → 猫娘口播。

真机环境：MC 1.21.11 + Fabric Loader 0.19.5（PCL 版本隔离实例）+ mcbot 0.1.0
+ 打包版 N.E.K.O. 宿主 v0.9.0.1。

已验证：

- 4 个 `@llm_tool` 注册进宿主 `ToolRegistry`（`source=plugin:mcbot_bridge`），对话 LLM 真的会调
- SSE 帧合 v1.0 契约（每帧带 `contract_version` / `session_id`），含 `ACCEPTED:` 受理回执 + `job_id`
- **session 变化 → 游标重置**：退世界再进后新桥 id 从 1 重发，插件检出
  `bridge session changed; previous session state discarded` 并清零游标，新会话事件正常到达宿主。
  （若未重置，新会话的 1..N 会被旧游标「id 大于」的条件整段**静默吞掉**——不报错，只是没有。）
- 看门狗在预算耗尽时释放任务槽

**待办（下次优先）**：

1. **cue 刷屏**。`progress` 节流 6s ≈ 10 条/分钟；`state` 现已按 `state_min_interval_seconds` 节流，
   完成/超时/反问的 `coalesce_key` 已按任务或问题区分，不再互相折叠。被动 cue 会攒批，用户下一轮开口时一次性倒进上下文——
   需要真机复测确认没有淹掉用户自己的请求（表现为「发了指令但没执行」）。
2. `done.status = failed` 的措辞复测。首次真机时她曾把失败任务说成「挖好啦」，
   但那次跑的是**不含 `CUE_DONE_FAILED` 修复的旧代码**——修复后需要重新验一次。
3. `task_wait_timeout_seconds` 已由 300 调到 **900**（实测 300s 会在复合任务中途开火、
   提前释放任务槽），待复测不再提前开火。
4. **面板**。`surfaces/mcbot.tsx` 显示在线状态、当前身体、当前任务、待答问题，并有「停止任务」按钮
   （对应 `mcbot_stop` UI action，等同于让同伴放下全部任务）。面板尚未在真机上验过。

## 已知限制与与兄弟插件的差异

- **没有画面**。mcbot 的桥是 task 级的，不暴露截图，所以**图片进不了视觉上下文**。
  兄弟插件有这条能力，本插件没有——不要以为两者是对齐的。
- **没有 nudge 轮询**。兄弟插件里那 ~130 行的 `_system_prompt_loop` 之所以存在，是因为 mc-agent
  从不主动汇报，只能轮询游戏状态去催 LLM。mcbot 是**推**的，这一整块被删掉了。这是本插件相对
  兄弟的主要简化。
- **桥随世界启停**。见上；`mcbot_status` 会如实说没连上，而不是报错。
- **token 要到第一次进世界才存在**。所以重连循环每一轮都会重读 token 文件，
  插件在 Minecraft 从没启动过的情况下装好，之后也能自己接上，不需要重启。

---

## 配置

全部在 `plugin.toml` 的 `[mcbot]` 一节（面板里的表单由 `config.schema.json` 驱动）：

| 键 | 默认 | 说明 |
|---|---|---|
| `base_url` | `http://127.0.0.1:17897` | 桥的地址 |
| `game_dir` | `""` | Minecraft 游戏目录，用来定位 `bridge.token`；留空用平台默认 |
| `token_file` | `""` | 直接指定 token 文件；填了就不看 `game_dir` |
| `reconnect_interval_seconds` | `5.0` | 事件流断开后的重连间隔 |
| `http_timeout_seconds` | `10.0` | 短调用（派活/取消/答复/查状态）的超时 |
| `ask_timeout_seconds` | `150.0` | `/v1/ask` 的超时；**必须**大于 mcbot 自己的 135s 上限 |
| `task_wait_timeout_seconds` | `900.0` | 等完成事件的预算；超时则放开任务槽并如实说"没等到回音"。300s 实测会在复合任务中途开火 |
| `stream_events` | `true` | 是否订阅事件流；关掉后同伴不再主动开口 |
| `progress_min_interval_seconds` | `6.0` | 工具失败的中途播报（`ok=false`）的最小间隔；纯叙事进展不进对话，不受此限 |
| `state_min_interval_seconds` | `6.0` | 状态播报的最小间隔 |

`[plugin_runtime] auto_start = false`：手动启动。别在用户打开插件时就去连一个还没跑起来的 Minecraft。

---

## 启用步骤

1. **N.E.K.O. 侧**：插件面板里启用 `mcbot_bridge`，必要时在设置里填 `game_dir`（或留空用默认）。
2. **Minecraft 侧**：装好 mcbot，客户端进入一个世界——这时桥才会开始监听，`bridge.token` 才会存在。
3. 在对话里让猫娘指挥同伴，例如「让同伴去挖点木头」。
4. 想确认连通性：问一句「同伴在干嘛」，或直接调 `mcbot_status`。

---

## 文件分工

| 文件 | 角色 |
|---|---|
| `__init__.py` | **facade**：SDK 胶水（装饰器、lifecycle、handler、`_ensure_service_started`、`_on_command_loop_start`）。不持有游戏协议状态 |
| `service.py` | **状态与语义**：任务占坑、SSE 事件路由、cue 发射、token 读取。不 import 插件 SDK，可独立测试 |
| `client.py` | **纯传输**：`httpx.AsyncClient` 的五个 REST 方法 + 单连接 SSE 泵。不持有游戏状态，不 import SDK |
| `prompts.py` | 全部 LLM 可见措辞，按 8 种语言键控 |
| `config.schema.json` | 设置表单契约（由 `plugin/tests/unit/plugins/test_bundled_config_schemas.py` 强制校验） |

### 两个不显然的设计点

**为什么 `client.py` 不自己管重连？** 连接策略（何时重试、token 还没出现时怎么办）放在 service 里，
让 `client.pump_events()` 只做"连一次、泵到结束就返回"。因为 mcbot 的 token 要到第一次进世界才生成，
"token 不存在"和"桥没在听"都是**每一轮都要重新判断**的可恢复状态——把重连循环交给调用方，
才能每轮都重新读一次 token。

**为什么 `mcbot_task` 不做 detached task？** 兄弟插件的 `minecraft_task` 需要把真正的等待甩到后台任务，
因为它要阻塞等一个 WebSocket 帧。这里 `wait_s=0` 让派发本身几毫秒就返回，所以 handler 里
claim + dispatch 同步做完即可，完成 cue 由 SSE 的 `done` 事件驱动。**没有 `_on_detached_task_done`。**

---

## 开发与测试

**测试不需要宿主环境。** 根目录的 `conftest.py` 在检测不到宿主 SDK 时会注入最小桩件，
并以 `mcbot_bridge_plugin` 为包名加载入口模块：

```bash
uv run --no-project --python 3.11 --with pytest --with pytest-asyncio --with httpx \
  python -m pytest tests -q
```

为什么需要这个 conftest：仓库根就是插件模块（`__init__.py` 是 `plugin.toml` 指的入口），
而目录名 `n.e.k.o_plugin_mcbot_bridge` 不是合法标识符。若 pytest 的配置放在仓库根，
它会把根目录当包溯源、去 `import __init__`，此时模块以顶层身份执行、相对 import 直接失败
——**tests/ 下每个用例都会在 setup 阶段报错**，连什么都不 import 的 `test_smoke.py` 也一样。
conftest 里 `sys.modules.setdefault("__init__", module)` 那行就是为此：让 pytest 的溯源
import 命中已加载的模块，而不是把入口当顶层模块重跑一遍。

**要在真实宿主里调试**，把插件复制进宿主源码树后 Reload 该插件：

```powershell
$dst = "E:\NEKOdm\N.E.K.O\plugin\plugins\mcbot_bridge"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item .\__init__.py,.\client.py,.\service.py,.\prompts.py,.\plugin.toml,`
  .\config.schema.json,.\README.md -Destination $dst -Force
Copy-Item .\i18n -Destination $dst -Recurse -Force
```

**接缝测试说明。** 离线单测把 `push_message` 换成假替身，天生看不见"宿主是否真的替我们做了 X"这类断裂
（65 项全绿时仍漏掉了 `metadata` 不可达、`question_id` 无法回传的死路）。凡新增"依赖宿主会替我们做 X"的假设，
必须在接缝处验一次（真实宿主，或对照宿主代码核对），不能只补 service 单测。

宿主会为这份副本生成 `plugin.meta.json`（它是构建产物，在源仓库里生成不了——探针要求插件
处在 `plugin.plugins.<id>` 路径上）。仓内那份副本是临时的，**不要提交到 N.E.K.O. 仓库**。

## 隐私

任务文本、同伴的回答、以及同伴的反问都是**对话内容**。它们：

- **不会**写进日志——日志只记形状与元数据（task_id、长度、状态码、异常类型）；
- **不会**以原始形式回到模型上下文——传输层异常会被改写成猫娘能说出口的话
  （比如 `ConnectError` → "现在找不到同伴——她的世界还没开，或者刚断开"），
  因为工具结果会永久留在对话历史里，主机名和端口不该被记进去。

---

## 为什么不用 MCP

mcbot 的 MCP 面只改配置就能接，但要丢产品特性：

1. **MCP 面没有服务端→客户端推送**。`progress` / `question` / `done` 只在 REST 的 `/v1/events` 上，
   而内置 `mcp_adapter` 的 `streamable-http` 只 POST、从不打开 GET SSE 流。
   **"猫娘主动反应"直接拿不到。**
2. MCP 条目走**分析器路由**而非对话 LLM 直调 ⇒ 多一跳，约 10s 决策延迟。
3. `mcbot_task` 会阻塞到 `wait_s`（上限 120）⇒ 必须把 adapter 的 `tool_timeout` 抬到 130+。
4. token 必须抄进 N.E.K.O. 的插件配置，与 mcbot「token 只留在 `bridge.token`」的卫生线冲突。
   走本插件则从文件读，无此冲突。

MCP 路线保留为协议兼容性烟雾测试与后备诊断，不作正式集成。
