# NEKO 专家团 · 会话启动「完整更新」标准作业流程（SOP）

> 结论先行：「每次会话开始时完整更新」的原生机制是 `SessionStart` + 上下文注入，官方事实已核验；但本工程**已在 `~/.workbuddy/settings.json` 的 `hooks.SessionStart` 挂载第 3 条**（2026-11-18 现读数：workbuddy 根 3 条、codebuddy 根 1 条，本工程只在 workbuddy 侧）。这次挂载早于下面的同意条款落笔，**追认或回滚都须用户单独显式同意**（见 §8 与 AUDIT 的 W08 条目）。自动化范围被严格限定为 **Sense（只读感知差异提醒）**，**绝不自动化 Act（安装或更新第三方 skill / 包 / MCP / 插件）**；每次引入第三方代码仍需人工安全审计。无差异时只报「基线一致，最后核验 `<BASELINE_DATE>`」并**不改任何文件**；有差异才允许由主理人确认后改文件。
>
> 本版相对前一版的实质变更：吸收 neko-security（云安）的 **5 点官方 Hook 语义专项修正**（见 §1.2），并据此重写 §5 去重、§6 降级、§0A 自动化边界、§8 挂载状态；**另补录 `~/.codebuddy` 配置根 3 条 `codebuddy-*` 自动执行 hook**（云安 `security-refresh-notes.md` §5 第 12 行 / §6 新发现），见 §1.4。

## 0. 适用范围与红线

- 范围：MCP / 连接器 / 插件 / marketplace / hooks / skills 的**本地底座比对**；不含远程版本解析、不含端到端探活、不含任何安装或升级动作。
- 红线 1：检测阶段**只读**，不写文件、不联网、不改配置；落盘更新一律由主理人在会话内决定并执行。
- 红线 2：不得把「未核验」写成「已更新」；不得伪造证据。
- 红线 3：循环 / 自动行为须先查官方文档并取得用户同意（用户长期规则）。本次已完成前置动作：WebFetch 核验官方 Hook 文档；云安另做了事件专项语义核验（§1.2）。
- 红线 4：注入上下文是**不可见 system-reminder**（§1.2-d），不得用它夹带指令、诱导模型越权，或替代用户授权。

## 0A. 自动化边界：Sense / Act（硬边界，不可协商）

| 层 | 允许做什么 | 明确禁止 |
|---|---|---|
| **Sense（可自动化）** | 只读扫描本地配置/清单 → 计算受控字段指纹 → 与基线比对 → 输出「有差异 / 无差异 + 差异摘要 + 当前快照」→ 提醒主理人 | 写任何文件、联网、调用 MCP/连接器工具、修改 settings.json |
| **Act（绝不自动化）** | 由**人在会话内**判断并手工执行：更新基线文件、升级插件版本、安装 skill / npm 包、新增或启用 MCP、安装/更新 marketplace 插件 | 任何形式的自动 install、自动 update、自动 pull、自动写 `settings.json` / `mcp.json` / `plugin.json` / `marketplace.json` |

- 「完整更新」在本工程中的**唯一含义**是：会话开始时把底座**真实状态**完整感知并注入上下文，让主理人据此决策；**不等于**自动把第三方组件升到最新。
- **每次引入第三方代码（skill / 包 / MCP / 插件）仍需人工安全审计**（来源核验、权限面、凭据面、供应链风险），审计通过前不得安装或启用；审计结论不得由本 SOP 的自动检测代替。
- 配套脚本 `scripts/session_capability_refresh.py` 只实现 Sense，且自身**不写任何文件**；检测到差异时它只在上下文中提示「请由主理人确认后更新」，不执行更新。

## 1. 触发时机与机制

### 1.1 基础事实（[FACT/DOCS]，2026-11-06 联网核验 `workbuddy.ai/docs/ide/Features/hooks`）

| 项 | 官方事实 |
|---|---|
| 触发 | `SessionStart` 在会话启动时触发，**每个新会话仅一次**；系统按 `conversationId` 判定，同一会话内多次请求不重复触发；切换/清空会话会再次触发（Session Tracking） |
| Matcher | 匹配字段 `source`，当前仅支持值 `startup`；配置写 `"matcher": "startup"` |
| 注入机制 | hook stdout 输出 JSON，`hookSpecificOutput.additionalContext` 的文本被**注入会话上下文** |
| 输出基本结构 | `{"continue":true,"suppressOutput":false,"systemMessage":"...","hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"..."}}` |
| 输入 stdin | 通用 `session_id / transcript_path / cwd / hook_event_name`；SessionStart 另有 `source` |
| 退出码（通用表格） | `0` 成功（stdout 可被处理并注入）；`1` 非阻塞警告（stderr 显示为警告）；`2` 阻塞错误；其他按 `1` 处理 |
| 调度 | 多 hook **并行执行、顺序不保证**；自动去重；Session Tracking 避免重复触发 |
| 配置位置 | 官方文档写用户级 `~/.codebuddy/settings.json`；**本机推断生效根为 `~/.workbuddy/settings.json`**，`~/.codebuddy/settings.json` 属旧体系并存（详见 `capability-baseline.md` §3.1，[INFERENCE]） |

### 1.2 云安（neko-security）官方 Hook 语义专项修正 5 点

> 来源：neko-security（云安）对官方 hooks reference 的专项核验，逐字原文与可追溯出处见 `references/security-refresh-notes.md §1.1「O2 适用边界澄清与可追溯出处」`；证据等级 **`[FACT/DOCS]`**（核验日 2026-11-06，URL + 章节锚点 + 逐字原文齐备，可由他人独立复核）。**冲突不成立**：官方 `### Exit code output` 的 `Blocking error` 条目（**不是表格**）原文自带 "On [events that can block](#exit-code-2-behavior-per-event), Claude Code stops the action" 限定；边界由 `#### Exit code 2 behavior per event` 表的 `Can block?` 列界定，`SessionStart = No`，属 per-event 例外，二者是「通用条目 + 例外表」关系而非互相推翻。以下 5 点按此 `[FACT/DOCS]` 口径采纳。

**(a) SessionStart 上 `exit 2` 不阻塞会话。** `[FACT/DOCS]`
官方 `#### Exit code 2 behavior per event` 表头为 `| Hook event | Can block? | What happens on exit 2 |`，其中 `| SessionStart | No | Shows stderr to user only |`；官方并明确 "Claude doesn't see it, and the session or subagent proceeds."。通用 `Blocking error` 条目仅适用于 **`Can block? = Yes` 的事件**（如 `PreToolUse` 阻止工具调用、`UserPromptSubmit` 拒绝提示），`SessionStart` 不在此列。**Decision control 表管 JSON 结构化决策字段，与 exit 2 能否阻塞是两个维度**：反例 `PostToolUse` 有 `updatedToolOutput` 决策字段但 `Can block? = No`；`SessionStart` 在该表为 `Context only … No blocking or decision control`，可作佐证但不是边界定义。
出处：`references/security-refresh-notes.md §1.1「O2 适用边界澄清与可追溯出处」`；官方页面 `https://code.claude.com/docs/en/hooks`（由 `https://docs.claude.com/en/docs/claude-code/hooks` 重定向，核验日 2026-11-06）；4 个锚点 `#exit-code-output` / `#exit-code-2` / `#exit-code-2-behavior-per-event` / `#decision-control`。
本 SOP 仍要求脚本统一返回 `exit 0`（理由见 §6），但不再把「误返回 exit 2 会阻塞会话」当作事实前提；相应地，「绝不产生 exit 2」降级为**工程纪律**而非**会话可用性硬约束**。

**(b)「相同命令自动去重」的真实语义：跨 settings 层级的同一 handler 重复定义只跑一次。**
即：同一个 handler 若在**用户级 / 项目级 / 插件级**等多个 settings 层级被重复定义，系统只执行一次。**不是**「同一文件内相似命令被合并」。
→ 这**推翻**了前一版 SOP「新 hook 的 command 字符串必须与现有 2 条 SessionStart 命令不同，否则会被去重」的说法，该说法是错的。同一 `hooks.SessionStart` 数组内可以并存多个不同 handler（本机现状已有 2 条）；需要检查的是**不要在其他 settings 层级复制粘贴同一个 handler**。

**(c) SessionStart 纯文本 stdout 也会作为上下文注入。**
不强制要求 JSON。纯文本 stdout 同样进入上下文。本方案**仍输出结构化 `HookOutput` JSON**，
图的是 `additionalContext` 的位置与格式确定性（见 d）。
2026-11-18 现读 `emit()` 与真跑 stdin：顶层只有 `hookSpecificOutput`，
脚本**不写** `continue`、`suppressOutput`、`systemMessage` 三个控制位，放行由宿主自己决定。

**(d) `hookSpecificOutput.additionalContext` 是不可见 system-reminder，位于第一条用户提示之前。**
- 注入内容**用户不可见**、对模型可见，且**排在第一条 user prompt 之前**，会先于用户诉求影响模型判断。
- 因此注入文本必须：**客观、简洁、只陈述事实与差异**；**不得**包含指令性语句、角色扮演诱导、权限提升暗示，或任何试图绕过用户授权的内容。
- 不得把它当作「悄悄让用户同意自动更新」的通道 —— 这正是 §0A 把 Act 排除在自动化之外的直接原因。

**(e) SessionStart 时 MCP 尚不可用。**
- hook 执行阶段**不能调用任何 MCP / 连接器工具**；`list_installed_plugins`、MCP server 探活、远端版本查询在此时都不可用。
- 这解释了为什么配套脚本坚持「只读本地文件」而非调用工具：这是**机制约束**，不只是安全选择。
- 连接器运行时状态、远端版本等信息只能在**会话内 MCP 就绪之后**由主理人显式补齐，并标 `[FACT/RUNTIME]` / `[未联网核验]`；hook 阶段一律不得断言。

### 1.3 由修正导出的设计约束（汇总）

1. 脚本只做本地文件读取 + 指纹计算，不调用任何工具、不联网（e）。
2. 输出结构化 JSON 但内容保持客观陈述，不夹带指令（c、d）。
3. 退出码统一 `exit 0` 是纪律；即使异常返回 `exit 2`，SessionStart 上也不阻塞会话（a）。
4. 幂等由「每会话一次 + 跨层级去重 + 脚本无状态」共同保证，不依赖「命令串必须不同」（b）。
5. 「完整更新」= 完整感知 + 差异提醒，**不含** Act（§0A）。
6. **两个配置根属不同产品**（WorkBuddy vs 旧 CodeBuddy 体系），各自的自动执行 hook 见 §1.4；它们**不构成官方 dedup 的「跨 settings 层级同一 handler」，不可假设只跑一次**（§1.2-b）。新 hook **只能挂 `~/.workbuddy/settings.json`**，**不得写入 `~/.codebuddy/settings.json`**（会形成第二套自动执行注册），也不得内置进专家包 `hooks/` 目录 —— 与云安 `security-refresh-notes.md` §6 / §7 红线 3 一致。

### 1.4 已存在的自动执行代码 / 每次会话会跑什么（双配置根，只读清单）

> 措辞与云安 `security-refresh-notes.md` §6 检测清单一致；`[FACT/LOCAL]` 由云稳 2026-11-06 只读复核。**当前未被代码级审计覆盖，属已知缺口**（云安 §5 第 12 行：「第二套自动执行 hook 未被任何审计记录覆盖，且权限模式更宽」）。以下均为宿主既有配置，**本工程未新增、未修改、未启用任何一条**。

| 配置根 | 事件 | 自动执行命令 | timeout | 审计覆盖状态 |
|---|---|---|---|---|
| `~/.workbuddy/settings.json`（推断生效，40882 B） | SessionStart #1 | hindsight `workbuddy-sessionstart-hook.js` | 30 | 未被代码级审计覆盖 |
| 同上 | SessionStart #2 | haloflux-lab `session-beacon.mjs` | 15 | 未被代码级审计覆盖（且本身写文件） |
| 同上 | UserPromptSubmit | hindsight `workbuddy-hook.js` | 30 | 未被代码级审计覆盖 |
| 同上 | Stop | hindsight `workbuddy-stop-hook.js` | 60 | 未被代码级审计覆盖 |
| `~/.codebuddy/settings.json`（旧体系，1063 B） | **SessionStart** | hindsight **`codebuddy-sessionstart-hook.js`** | 30 | **当前未被代码级审计覆盖，属已知缺口** |
| 同上 | **UserPromptSubmit** | hindsight **`codebuddy-hook.js`** | 30 | **当前未被代码级审计覆盖，属已知缺口** |
| 同上 | **Stop** | hindsight **`codebuddy-stop-hook.js`** | 60 | **当前未被代码级审计覆盖，属已知缺口** |

补充事实（云安 §5 第 12 行 / §6，云稳只读复核）：
- `~/.codebuddy/settings.json` 另有 `permissions.defaultMode: "fullAccess"`、`trustedDirectories: ["~/**","E:/**"]`，权限模式更宽。
- 该根另有 `~/.codebuddy/.mcp.json`（244 B，`hindsight` server，`HINDSIGHT_MCP_HARNESS=codebuddy`）与两份 hindsight 备份（`.mcp.json.hindsight-backup` 244 B、`settings.json.hindsight-backup` 254 B）；`~/.codebuddy/skills/.ef-manifest.json` **缺失属正常，非异常**。
- **两个配置根都进检测清单**（`capability-baseline.md` §2 #4/#5 双根 hook 集合指纹），但**不得在此根挂新 hook**；二者属不同产品、**不构成官方 dedup 的「跨 settings 层级同一 handler」，不可假设只跑一次**。
- **已知覆盖缺口（不伪造）**：上述配套项（`.mcp.json` / 两份备份 / `permissions` / `trustedDirectories` / skills manifest 存在性）当前为**文档级检测项**，尚未纳入脚本指纹；脚本仅对两个 `settings.json` 的 `hooks` + `enabledPlugins` 受控字段取 `sha256[:12]`（`settings.codebuddy=684c0eef3067`），不得宣称「已覆盖」。

## 2. 逐步 Checklist（会话启动时执行）

1. **读 stdin 并排空**：脚本不使用 payload 里的任何字段。读的理由只有一个——宿主不关 stdin 时，不读就会等 EOF 而挂住。读失败不得报错，按空输入继续（已实测降级路径可用）。旧版本还顺手 `json.loads` 一份却没人读，2026-11-17 删掉，本节同期改正这句话。
2. **扫描 12 个检测点**：严格按 `capability-baseline.md` §2 的「变更检测清单」逐项读取（路径与字段见该表，此处不重复）。
3. **计算指纹**：受控字段子集 → `json.dumps(sort_keys=True, ensure_ascii=False, separators=(',',':'))` → sha256 前 12 位；与 `BASELINE` 常量比对。
4. **就绪检查**：核对本工程那 1 条 hook 脚本的存在性，并扫两个配置根的注册表——注册指向不存在的文件就记入差异（工单 W12 选 B 之后改的判据：以前写死 8 条路径，其中 7 条属于外部组件 Hindsight，整目录消失后每轮都报「缺失」却修不掉，而真正坏的「注册还在、文件没了」这一类反而看不见）。
5. **生成当前快照**：MCP 启用/禁用清单、连接器目录项数、生效配置 hooks 事件/命令数、marketplace 目录与清单数、插件版本、skills 数、hook 脚本就绪数。
6. **判定有无差异**：按 §3 规则。
7. **输出上下文**：只有 `hookSpecificOutput.additionalContext`，内容按 §3 的幂等输出约定（无差异只报一句话 + 快照，不写文件）。脚本不写 `continue` 字段，同段下一条与 `emit()` 为准。
   输出形态只有 `hookSpecificOutput.additionalContext`：脚本不写 `continue` 字段，宿主自己决定放行（2026-11-17 读 `emit()` 核对）；措辞须符合 §1.2-d 的客观、无指令要求。
8. **会话内补齐（非 hook 阶段、MCP 就绪后）**：若需连接器运行时状态，显式调用 `list_installed_plugins`；若需远程版本，须用户确认后联网。此步**不属于**自动 hook（e）。
9. **决策**：有差异时由主理人确认是否更新基线（§4）；**绝不**触发安装/升级动作（§0A）。

## 3. 「有差异」判定规则与幂等输出约定

### 3.1 判定为「有差异」的充要条件（满足任一即报）

- 任一受控字段指纹 ≠ `BASELINE` 对应值；
- `mcpServers` / marketplace 清单 / marketplace 目录 / skills 目录出现**新增或删除**；
- `connectors/default/mcp.json` 项数 ≠ 208；
- `plugin.json` 的 `version` 变化；
- hook 注册悬空（settings.json 里注册的命令指向不存在的文件）；
- 本工程自己的 hook 脚本缺失；
- 任一文件 `<MISSING>` / `<UNREADABLE>`（视为差异 + 读取异常，不得静默）。

**不计为差异**（避免误报噪声）：`settings.json` 中 `requestDeliveries`、`officeFileAssociationsRepairMarker`、时间戳类字段；文件 mtime 变化；JSON 缩进/键顺序变化（指纹已 `sort_keys` 归一）；新增 `references/*.md` 或 `scripts/` 目录（不在 12 个检测点内）。

### 3.2 幂等输出约定（硬要求）

| 结果 | 允许的输出 | 是否允许改文件 |
|---|---|---|
| **无差异** | **只报「无变更（基线一致，最后核验 <BASELINE_DATE>）」**（配套脚本机器侧结论为「无变更」，两者是同一状态的两层表述），可附简短当前快照与证据声明 | **否**。不写任何文件、不刷新时间戳、不追加历史记录、不生成「本次已检查」标记 |
| **有差异** | 报「有变更（N 项）」+ 差异明细（最多 20 条，超出注明另有 M 项）+ 当前快照 + 证据声明 + 「请由主理人确认后更新」 | **否**（hook 阶段）；由主理人在会话内确认后按 §4 落盘 |

> 「最后核验 YYYY-MM-DD」取脚本常量 `BASELINE_DATE`（现值 **2026-11-07**），与 `capability-baseline.md` 的核验日期同值；每次真正落盘更新基线时同步刷新该日期。无差异时**不得**把日期改成当天 —— 那会造成「每次会话都改文件」，直接违反幂等。

## 4. 变更后写到哪里（落盘决策矩阵）

| 检测结果 | hook 阶段是否落盘 | 由谁决定 | 写到哪里 |
|---|---|---|---|
| 无差异 | **否**（幂等，绝不重复写） | — | 仅上下文注入 |
| 有差异（本地文件） | **否**（只读） | 主理人在会话内确认后执行 | ① `references/capability-baseline.md`：§1 表格、§2 清单、§4 指纹块、基线版本与核验日期；② `references/session-refresh-sop.md`：相关基线描述；③ `scripts/session_capability_refresh.py` 的 `BASELINE` 常量 |
| 有差异（连接器运行时状态） | 否 | 会话内 MCP 就绪后由主理人调用工具确认 | 同上，并新增 `[FACT/RUNTIME]` 与核验时间 |
| 有差异（远程/云端项） | 否 | **用户确认联网后** | 同上，并新增 `[FACT/DOCS]` / `[FACT/REMOTE]`；未核验则维持 `[未联网核验]` |
| 脚本自身异常 / 超时 | 否 | — | 仅降级提示，不写文件 |

> 三处必须**原子式同步**，否则下次会话会持续误报。刷新命令（只读，不改文件）：
> ```bash
> python "~/.workbuddy/hooks/session_capability_refresh.py" --regen   # 挂载份是宿主真执行的那一份；要 --regen 就指它，别指安装态
> ```
> 这是**唯一允许改文件**的时机，且必须由主理人（不是 hook）执行；改的只是本专家团自己的文档与常量，**不是**安装或更新任何第三方组件。

## 5. 幂等与去重要求

- **每会话一次**：官方 SessionStart 按 `conversationId` 去重，配合 Session Tracking，无需脚本自行实现会话锁。
- **自动去重的真实语义**（§1.2-b）：**同一 handler 跨 settings 层级（用户级 / 项目级 / 插件级）重复定义时只执行一次**。因此：
  - 同一 `hooks.SessionStart` 数组内并存多个**不同** handler 是允许的（本机现状已有 hindsight 与 haloflux-lab 两条）；
  - 挂载前要检查的是**别在多个 settings 层级重复定义同一个 handler**，而不是「命令串不能和别人相似」；
  - 前一版 SOP 中「命令串必须与现有 2 条不同否则被去重」的表述**已作废**。
- **并行安全**：多 hook 并行执行、顺序不保证 → 本脚本不得依赖其他 hook 的副作用、输出或执行顺序；不读写共享临时文件。
- **纯函数**：脚本无状态、无缓存、不写日志、不写临时文件；同一输入重复执行结果一致。
- **不重复落盘**：无差异时零写入（§3.2）。
- **抗噪指纹**：只 hash 受控字段，不 hash 整文件。

## 6. 失败降级（不得阻塞会话）

1. **框架层**：官方通用 `Blocking error` 条目自带 "On events that can block" 限定，与 §1.2-a 的 per-event 例外表**不构成冲突**——通用表格列 `exit 0` 成功 / `exit 1` 非阻塞警告 / `exit 2` 阻塞（仅适用于 `#### Exit code 2 behavior per event` 表中 `Can block? = Yes` 的事件）；按 §1.2-a，**`SessionStart` 在该表为 `Can block? = No`，`exit 2` 仅把 stderr 显示给用户、不阻塞会话**（"Claude doesn't see it, and the session or subagent proceeds"）。若脚本进程无法启动或超过宿主 `timeout`，会话仍继续。
2. **脚本层（工程纪律）**：所有可预期异常（文件缺失、JSON 解析失败、stdin 非法 JSON、内部软截止）均在脚本内捕获，输出**合法 `HookOutput` JSON + 降级提示 + `exit 0`**。选 exit 0 而非 1 的理由：`exit 1` 会丢弃 stdout（`exit 1` 时 stderr 显示为警告、stdout 忽略），降级提示只能进 stderr；`exit 0` 能让「检测失败、请手动复核」通过 `additionalContext` 进入上下文。统一 exit 0 是纪律要求，**不是**因为 exit 2 会阻塞会话（§1.2-a 已否定该前提）。
3. **超时层**：宿主 `timeout: 10`；脚本内置 **`SOFT_DEADLINE = monotonic() + 8.0s`**（`scripts/session_capability_refresh.py:39`），到点抛 `TimeoutError` 被捕获，输出固定单行降级文本 `FALLBACK_CONTEXT`，**不输出部分结果**（与脚本头「超时安全」那句同一口径），留 2 秒余量给 JSON 序列化与进程退出。
4. **降级文本同样受 §1.2-d 约束**：客观陈述、不夹带指令、不诱导越权。

> 已实测：正常 stdin / 空 stdin / 非法 JSON stdin 三路径均输出合法 JSON、`exit 0`，顶层键只有 `hookSpecificOutput`（2026-11-18 现读 `emit()`，并真跑 stdin 复核）。
> 2026-11-17 补测：软截止路径同样只输出 `FALLBACK_CONTEXT` 一条（`--self-test` 里钉成断言，破坏它的那组反向对照立刻变红）。本节改的是对「输出什么」的描述，降级、不阻塞、合法 JSON、`exit 0` 四项契约没动。

## 7. 证据分级与「未联网核验项」标注规则

- 分级固定：`[FACT/LOCAL]`（本机文件只读）、`[FACT/RUNTIME]`（运行时工具实测）、`[FACT/DOCS]`（官方文档联网核验）、`[INFERENCE]`（有事实支撑的推断，须写明依据）、`[未联网核验]`（不断言）。云安 5 点修正的证据等级为 **`[FACT/DOCS]`**（URL + 章节锚点 + 逐字原文见 §1.2 (a) 与 `security-refresh-notes.md §1.1`，可由他人独立复核）；云稳未另行联网，直接引用该已核验出处，按 §1.2 的标注处理。
- 注入上下文**必须**包含一行证据声明：本机文件只读为 `[FACT/LOCAL]`；连接器运行时状态、skill 远程版本、官方文档更新均为 `[未联网核验]`，此处不断言。
- 以下项在检测阶段**一律标 `[未联网核验]`，禁止写成「已更新 / 最新」**：
  1. `@latest`、npm 包、marketplace 云端目录的实际远端版本；
  2. 连接器远端服务健康度与 OAuth 有效性（`list_installed_plugins` 的 `connected` 是本地注册状态，非端到端探活 [INFERENCE]）；
  3. 官方文档 / 插件规范的后续修订；
  4. 第三方 skill / MCP 的上游更新；
  5. `packages/*.zip` 与插件目录内容一致性。
- 需确认上述项时，必须**在会话内 MCP 就绪后**显式发起联网或工具调用（§1.2-e），并标注新的证据等级与核验时间。
- 当用户要求「完整更新」但上述项未核验时，交付物必须写明：「已完成本地全量比对（12 个检测点）；远程 N 项未核验，原因与建议命令如下」，不得含糊带过，更不得借自动安装冒充「已更新」。

## 8. 挂载状态与用户同意（当前：不挂载）

- **当前状态（2026-11-17 实测：已挂载）**：`~/.workbuddy/settings.json` 的 `hooks.SessionStart` 共 3 条 —— 两条既有配置（hindsight、haloflux-lab）与本工程的检测脚本 `~/.workbuddy/hooks/session_capability_refresh.py`（timeout 10）。这次挂载早于下面那些同意条款落笔，是否要追认由 云玩家 定；本文以前写的是「未挂载任何新增 SessionStart hook」，那是部署前的旧状态，`~/.codebuddy/settings.json` 亦保持原样（其 3 条 `codebuddy-*` hook 是宿主既有配置，见 §1.4，**当前未被代码级审计覆盖，属已知缺口**）。本次工程只落盘文档与刷新脚本常量，**未修改任何 settings.json**。
- 用户长期规则：循环 / 自动行为须先查官方文档并取得同意。已完成前置：官方文档 WebFetch 核验 + 云安事件专项语义核验（§1.2）。
- **启用自动化需用户单独、显式同意**，至少明确以下 6 点：
  1. 是否同意在 `~/.workbuddy/settings.json`（**推断生效根**，勿挂到 `~/.codebuddy/settings.json`）新增一条 SessionStart hook；
  2. 是否同意每个新会话自动执行一次只读扫描，并把不可见 system-reminder 注入上下文（§1.2-d）；
  3. 是否同意自动化范围**仅限 Sense**，明确排除安装/更新第三方组件（§0A）；
  4. 是否同意「有差异时由主理人在会话内更新三处基线文件」，hook 本身永不写文件；
  5. 是否同意联网核验项纳入「完整更新」；若需要，须**额外**授权联网行为；
  6. 是否知悉**不得写入 `~/.codebuddy/settings.json`**（该根已有 3 条 `codebuddy-*` 自动执行 hook，会形成第二套自动执行注册；两配置根属不同产品，**不构成官方 dedup 的「跨 settings 层级同一 handler」，不可假设只跑一次**；与云安 `security-refresh-notes.md` §6 / §7 红线 3 一致）。
- **未获同意时的等价降级**：由主理人在每次会话开始时手动按 §2 清单逐项复核（清单可直接照抄执行），无差异只报「基线一致，最后核验 <BASELINE_DATE>」。机制上仍是「完整更新」，只是不自动化。
- 特别注意 §1.2-e：即便挂载，hook 阶段也**拿不到 MCP**，所以连接器状态、远端版本永远需要会话内补齐，自动化不能覆盖这部分。

## 9. 挂载前检查清单 / 回滚 Runbook（**当前不执行**，仅供用户同意后使用）

### 挂载前检查
```bash
# 1) 备份推断生效配置（回滚基线）
cp "~/.workbuddy/settings.json" "~/.workbuddy/settings.json.pre-neko-baseline-hook.bak"

# 2) 确认 Python 与脚本语法
python --version
python -m py_compile "~/.workbuddy/hooks/session_capability_refresh.py"

# 3) 手动跑一次：应输出合法 JSON、结论「无变更」、exit 0
echo '{"session_id":"manual","hook_event_name":"SessionStart","source":"startup"}' \
  | python "~/.workbuddy/hooks/session_capability_refresh.py" \
  | python -m json.tool > /dev/null && echo "output JSON valid"

# 4) 确认改的是推断生效根；并按 §1.2-b 检查「同一 handler 是否在其他 settings 层级重复定义」
grep -n '"SessionStart"' "~/.workbuddy/settings.json"
```

### 建议挂载片段（需用户单独显式同意后才写入 `~/.workbuddy/settings.json` 的 `hooks.SessionStart` 数组）
> 下面是 **2026-11-06 实际部署的形态**（原文照抄，可据此复现或核对）：`matcher` 为 `null`（不加事件修饰）、命令指向**部署副本** `~/.workbuddy/hooks/session_capability_refresh.py`（不是包内源文件）、显式指定解释器、超时 10s 与脚本 8s 软截止留 2s 余量。
```json
{
  "matcher": null,
  "hooks": [
    {
      "type": "command",
      "command": "\"~/.workbuddy/binaries/python/versions/3.13.12/python.exe\" \"~/.workbuddy/hooks/session_capability_refresh.py\"",
      "timeout": 10
    }
  ]
}
```

### 回滚（SEV 止血：先恢复，再查根因）
```bash
cp "~/.workbuddy/settings.json.pre-neko-baseline-hook.bak" "~/.workbuddy/settings.json"
# 或从 hooks.SessionStart 数组中仅删除新增对象，保存后**新开会话**验证（SessionStart 每会话只触发一次）
```
验证回滚：新开会话，确认不再出现 `[NEKO 能力底座 · 会话启动检测]` 上下文；若仍出现，说明改到了 `~/.codebuddy/settings.json` 或存在项目级覆盖，按 §2 清单 #4 / #5 复查。

## 10. 可观测性与告警阈值（SRE 补充）

| 黄金信号 | 指标 | 阈值 / 动作 |
|---|---|---|
| 延迟 | 检测脚本执行耗时 | 脚本内 `SOFT_DEADLINE = monotonic() + 8.0s`（`scripts/session_capability_refresh.py:39`）软截止；宿主 `hooks.timeout = 10s`，超时即视为降级，须检查磁盘/文件数 |
| 错误 | 降级输出次数 | 连续 2 个新会话出现「本次检测不可用」这条降级文本 → 停止自动化并人工复核 |
| 流量 | 每会话注入字节数 | 实测 `additionalContext` ≈ 1.1 KB；超过 4 KB 应精简快照，避免挤占上下文（且它是不可见 system-reminder，见 §1.2-d） |
| 饱和度 | 误报率 | 无差异却连续报「有变更」→ 优先怀疑三处基线未原子同步（§4），或误用整文件 hash |

## 11. 第三方代码人工安全审计（不可省略）

- **每次**引入新的第三方 skill / npm 包 / MCP server / marketplace 插件前，必须由人完成安全审计：来源与维护者核验、最小权限、凭据面（如 `Authorization: Bearer ${TOKEN}`）、供应链风险、是否含 hooks 或自动执行逻辑。
- 审计结论记录到团队既有文档（如 `references/third-party-skill-audit.md` / `references/safe-dev-capabilities.md`），**不得**由本 SOP 的指纹比对代替。
- 本 SOP 的 Sense 检测**只报告「清单/配置变了」**，不判断变更是否安全、不决定是否安装；该判断永远属于人（§0A）。


---

## 附录 · 挂载后基线刷新记录与信号判据（2026-11-06，H2 → H4）

**历史 [FACT/LOCAL]**：H2 挂载后，因脚本扫描的 `hooks` 字段包含新增的第 3 条 `SessionStart` hook，`settings.workbuddy` 指纹由 `7b227d44f6ec` 变为 `02fb107c4263`（部署自指漂移，非真实底座变更；云安与云稳均用内存反证锁定因果）。H2 时代每次会话恒定报「有变更 1 项」。

**现状**：2026-11-06 已部署 **H4 `cb2b02e04aa32b2cf61b9f884cdf13cce99ff75eb428785c617ecd4a2fcdfa08`**，`BASELINE` 中 `settings.workbuddy` 已刷新为 `02fb107c4263`，并新增 `backups.workbuddy` / `backups.codebuddy` 两项存在性计数（当前各 2）。部署后首跑恢复「**无变更（基线一致）**」，差异为空。

**每次会话的信号判据**：
- 差异列表为空 → 基线一致，只报「基线一致，最后核验 `<BASELINE_DATE>`」，不写文件（幂等）。
- 出现任何差异项 → **真实变更**，按本 SOP 正常流程逐项核对；有差异才改文件。
- `settings.workbuddy` 当前值 ≠ `02fb107c4263` → 说明 hook 配置被改动（含卸载），按 `security-refresh-notes.md §10` T2 处理。
- `backups.*` 不再是 2 → 备份类文件增删，按 T8 处理。

**若日后需要再改脚本**：挂载生效后任何字节变化一律「重新代码级审计 + 重新取得用户同意」（`security-refresh-notes.md §4 S0 ②`），不得直接覆盖。

**撤销**：删除 `~/.workbuddy/settings.json` 的 `hooks.SessionStart` 第 3 条即可；配置备份 `settings.json.bak-2026-11-06-neko-hook`（SHA-256 `0b1a21a5…`），脚本回滚点 `~/.workbuddy/hooks/session_capability_refresh.py.bak-H2-2026-11-06`（H2 `b0ecd0e5…`）。

---

## 附录 B · 专家包结构完整性检查（每次 Workflow F 必做，2026-11-06 新增）

**背景 [FACT/LOCAL]**：2026-11-06 包根 `settings.json`（Team 型必需清单文件，内容仅 `{"agent":"neko-plugin-ops-team-lead"}`）**两次丢失**，每次都会让 `validate_expert.py` 报 `Team type must have settings.json`、`VALIDATE_EXIT=1`，专家包无法通过校验/注册。第二次丢失时间线：文件创建于 08:10:49，包根目录 mtime 变为 14:02（单文件被移除，非整体重写）。**根因未定位** —— 已排除：`register_expert.py` 无 `zipfile/extractall/copytree/rmtree`；`packages/neko-plugin-ops.zip`（2026-11-03 17:50）内含 `settings.json`，打包逻辑正常。假设性候选（均未证实，不采信）：某次目录级同步/重载、外部编辑器或清理工具误删。

**2026-11-06 16:40 第三次丢失 + 对照实验结论 [FACT/LOCAL]**：第三次发生在「`init_expert.py edict-sansheng` → 写文件 → validate → `register_expert.py`」之间（16:17 终检时该文件还在）。**已用对照实验排除 `register_expert.py`**：写回 `settings.json` → 对另一专家执行 `register_expert.py` → 立即查 + 3 秒后查，两个包的 `settings.json` 均仍在。两个官方脚本经 `grep -nE "remove|unlink|rmtree|shutil|cleanup"` 复核无任何删除逻辑，`_write_session_marker` 只写 `.created-by-session`。**根因仍未定位**；现行纪律：任何会改写 `plugins/` 或 `marketplace.json` 的操作后，立即复查所有专家包的 `settings.json`。

**2026-11-07 23:36 第四次至第七次丢失 —— 根因已定位 [FACT/LOCAL]**：文件不是被删，是**被宿主「搬」进 cache 物料区**。

- **证据链**：23:36 本会话加载专家时，包根目录 mtime 变为 `23:36:04`；同一时刻 `plugins/cache/my-experts/neko-plugin-ops/` 下**新建 `1.6.0/`**（mtime `23:36:11`），其内有 `settings.json`，内容与源包丢失的那份**逐字相同**：`{"agent": "neko-plugin-ops-team-lead"}`。全盘搜索「含 `neko-plugin-ops-team-lead` 的 `settings.json`」只命中这一处 —— 即源包那份已不在原地。
- **机制解释（能解释此前全部 6 次）**：宿主在**物化 cache（按 `plugin.json` 的 `version` 建目录）时，把包根 `settings.json` 移入 `cache/<name>/<version>/`**。因此：① 它只与「宿主加载/物化」相关，与 `register_expert.py` / `init_expert.py` 无关 —— 这就解释了对照实验为何怎么排除都排不掉；② 每次升版或重新物化都会重演；③ `mvp-dev-expert-team` 同样中招，因为它是同机制的另一个 Team 型包。
- **旁证**：`cache/1.2.0`/`1.3.0`/`1.4.0` 均无 `settings.json`（那三次物化时源包还没这个文件或已被取走）；`1.5.0` 无、`1.6.0` 有，与「取走一次」一致。
- **幂等正确对策**：跑 `python ensure_expert_settings.py`（工作区内，`--check` 用于只读判定，有问题退出码 1）重建源包副本。**不必也不能从 cache 搬回** —— cache 是运行产物，手改会被下次物化覆盖。重建后在下次物化前稳定可用。
- **仍不确定的部分 `[INFERENCE]`**：宿主是「移动」还是「复制后删除源文件」无法从外部区分；若是移动，理论上 cache 缺文件时会再次从源包取，但未观测到该路径。不做进一步断言。

**每次刷新必须逐项核对（人工，不进脚本指纹）**：
1. `settings.json` 存在，且 `agent` 字段 == `.codebuddy-plugin/plugin.json` 的 `agentName`（应为 `neko-plugin-ops-team-lead`）。
2. `plugin.json` 的 `agents[]` 每一项文件都存在；`agents/` 下无未声明的多余 `.md`。
3. `members[]` 每个 `avatar` 路径都存在；包级 `avatar` 存在。
4. `members` 中 `role=lead` 恰好 1 个；`teamInfo.memberAgents` 不含主理人；`members[].id` 与 `agents[]` 文件名 stem 双向覆盖。
5. 四份基线文档存在：`capability-baseline.md` / `session-refresh-sop.md` / `skill-inventory.md` / `security-refresh-notes.md`；`README.md` / `intro.html` 存在。
6. 包内无 `__pycache__` / `*.pyc` / `*.bak` / `*.tmp` / `*.orig` 残留。
7. 跑 `validate_expert.py <包路径>`，必须 `VALIDATE_EXIT=0`；不通过先修再继续。
8. **每次「宿主加载专家 / 升版物化」之后的第一件事**就是查第 1 项 —— 这是已知会触发丢失的时刻，别再花时间怀疑自己的写入。

**修复动作**：跑 `python ensure_expert_settings.py`（幂等自愈，只修 Team 型包）；或手工按官方 `team-spec.md:110-120` 与 `init_expert.py:317` 模板重建（仅一个 `agent` 字段），然后重跑 `validate_expert.py` 与 `register_expert.py`；**不要**用 `packages/*.zip` 解包覆盖，会带回旧版本文件；**也不要**从 `plugins/cache/` 反抄，那是宿主产物。
