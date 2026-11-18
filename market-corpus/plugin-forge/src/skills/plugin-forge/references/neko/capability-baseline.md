# NEKO 专家团 · 能力底座基线（Capability Baseline）

> 结论先行：现行基线是 **`baseline-2026-11-07-02`**（本机只读重算，反映专家团 **v1.6.0**）。
> 上一版 `baseline-2026-11-06-02`（v1.4.0 时期）已被它取代，只在 §0 历史里留痕。
> 本文件每轮更新时只改这一行与 §0 的「基线版本 / 核验日期」，别在多处各写一个版本。底座无阻断性故障；`~/.workbuddy/mcp.json` 现状为 **playwright / rea 启用、lighthouse-ops / hindsight 禁用**。存在 1 个高风险配置歧义：**双配置根并存** —— `~/.workbuddy/settings.json` 推断为本机生效配置，`~/.codebuddy/settings.json` 属旧 CodeBuddy 体系（勿挂错）。本文件是「每次会话开始完整更新」的比对基准，配套检测脚本只读注入、不落盘。
>
> 自动化边界：本文件与配套脚本**只做 Sense（只读感知差异提醒）**，绝不自动化 Act（安装或更新第三方 skill / 包 / MCP / 插件）。**当前状态已变（2026-11-17 实测）**：`~/.workbuddy/settings.json` 的 `hooks.SessionStart` 有 3 条，
>   其中一条就是本检测脚本（`~/.workbuddy/hooks/session_capability_refresh.py`，timeout 10）。
>   这条挂载发生在本文写完之后的部署动作里，本文 §6 的「未挂载」表述已过期；挂载是否需要用户同意，见 §6 与 sop §8。详见 `session-refresh-sop.md`。

## 0. 元数据

- 基线版本：`baseline-2026-11-07-02`（**沙箱重构 + 升版 v1.6.0 实测重算版**：anysearch 改匿名、专家团 v1.5.0→**v1.6.0**（新增 `neko-sandbox`）、my-experts 1→**3** 插件、skills 68→**69**；前一版 `-01` 为 anysearch 新增 + v1.5.0 版，`-04` 为 security-audit 安装 + rea 升级版，`-02` 为 v1.4.0 刷新版）
- 核验日期：**2026-11-07 23:2x**（本机只读复算）。当时脚本 `BASELINE` 常量还没同步，5 项漂移明细见 §4 末尾；
  同日 23:42 已同步（见下条 H6）。这两条讲的是同一天的先后两个时刻，不是两个结论。
- **本版增量（2026-11-06 晚，用户裁决）**：① 安装 `security-audit`（来源 Cloudflare，审计 commit `c1c8a8c` = 上游 HEAD，20 文件）→ `skills` **67 → 68**、`skills_fp` `c49a435f50f7` → **`0c0c53fd5b3e`**；② `rea` **6.7.0 → 6.8.0**（与审计快照一致）→ `mcp.workbuddy` `58519682cba3` → **`780a37b38c89`**；③ `playwright` 按用户裁决**保持 `@latest`**（显式接受浮动版本风险，不再视为待修项）。
- **✅ 脚本 `BASELINE` 已同步（H6，2026-11-07）**：用户原话「1，现在就要」明确同意立即刷新；6 处等长字面量（`BASELINE_DATE` + `mcp.workbuddy` + `plugin.neko-plugin-ops` + `my-experts` + `skills_count` + `skills_fp`），逻辑零改动，AST 抹值后完全同构，全文仅 47 字节不同。部署 H6 `b6d35cf2ff4016c6511e0cac8c91905e72b74cc8834fcc914ebf5a513d71b79c`，首跑恢复「无变更（基线一致，最后核验 2026-11-07）」。回滚点 H5 `0483245c…` 在 `hooks/session_capability_refresh.py.bak-H5-2026-11-07`。
- 历史：H5 `0483245c…`（2026-11-06 T1 三值刷新，回滚点 H4 `cb2b02e0…` 在 `.bak-H4-2026-11-06`）。
- 核验人：云稳（neko-reliability）
- 核验手段：本机 `Read` / `Bash(ls, find, stat, python)` / `Grep` / `list_installed_plugins` / WorkBuddy 官方 Hook 文档 WebFetch
- 本版变更：**仅文档增量，全部指纹与脚本 `BASELINE` 未变**。补录 `~/.codebuddy` 配置根的 3 条 `codebuddy-*` 自动执行 hook（云安 `security-refresh-notes.md` §5 第 12 行 / §6 新发现）、`permissions.defaultMode`、`trustedDirectories`、`.mcp.json` 与两份 hindsight 备份；把**双根 hook 集合**明确纳入检测点 #4/#5；新增 §3.3。当下重新实算 `settings.codebuddy=684c0eef3067`、`settings.workbuddy=02fb107c4263`，与 `BASELINE` 一致，首跑仍「无变更」。
backups.workbuddy=2  backups.codebuddy=2
- 前一版（-02）变更：仅 `plugin.neko-plugin-ops` 与 `marketplaces.my-experts` 两项指纹因 v1.4.0 升级而变化；其余 10 个检测点复算与 -01 完全一致
- 配置根：`~/.workbuddy`（推断生效）+ `~/.codebuddy`（旧体系，并存）
- 指纹算法：`json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',',':'))` → UTF-8 → `sha256` → 取前 12 位十六进制
- 证据等级：`[FACT/LOCAL]` 本机文件实测 · `[FACT/RUNTIME]` 运行时工具实测 · `[FACT/DOCS]` 官方文档联网核验 · `[INFERENCE]` 有事实支撑的推断（须写明依据） · `[未联网核验]` 需联网或运行时确认，不断言

> 抗噪设计：只 hash **受控字段子集**，不 hash 整文件 —— `settings.json` 含 `requestDeliveries`、`officeFileAssociationsRepairMarker`、时间戳类易变字段，整文件 hash 会造成每次会话误报。文件 mtime 变化、JSON 缩进/键顺序变化不计为变更（指纹已 `sort_keys` 归一）。

## 1. 全量基线表

> **两个配置根均存在每会话自动执行代码**，下表按事件名分列：`~/.workbuddy/settings.json` 有 5 条（SessionStart×**3** / UserPromptSubmit×1 / Stop×1）〔2026-11-06 17:1x 复核修正：原记 ×2 少算一条；实测 3 条 SessionStart 分别为 hindsight hook / haloflux-lab scripts / python session_capability_refresh〕，`~/.codebuddy/settings.json` 有 3 条（SessionStart / UserPromptSubmit / Stop 各 1 条 hindsight `codebuddy-*` hook）。二者属**不同产品**，不构成官方 dedup 的「跨 settings 层级同一 handler」，**不可假设只跑一次**；详见 §3.3。

| 类别 | 名称 | 配置位置 | 启用状态 | 版本/端点 | 最后核验时间 | 证据等级 |
|---|---|---|---|---|---|---|
| MCP Server | anysearch | `~/.workbuddy/mcp.json` | **启用**（2026-11-07 新增，基线 `-04` 无此项） | streamableHttp `https://api.anysearch.com/mcp` | 2026-11-07 | [FACT/LOCAL] |
| MCP Server | lighthouse-ops | `~/.workbuddy/mcp.json` | **禁用**（`disabled: true`） | streamableHttp `https://lightai.cloud.tencent.com/workbuddy/mcp`, timeout 30000 | 2026-11-06 | [FACT/LOCAL] |
| MCP Server | playwright | `~/.workbuddy/mcp.json` | **启用**（无 disabled 字段） | `npx -y @playwright/mcp@latest --browser chromium`, timeout 60000；`@latest` 实际解析版本 [未联网核验] | 2026-11-06 | [FACT/LOCAL] |
| MCP Server | rea | `~/.workbuddy/mcp.json` | **启用**（无 disabled 字段、无 timeout） | `cmd.exe /d /c npx -y rea-agents@6.7.0 mcp` | 2026-11-06 | [FACT/LOCAL] |
| MCP Server | hindsight | `~/.workbuddy/mcp.json` | **禁用**（`disabled: true`） | `node C:\Users\<user>\.hindsight\coding-agents\dist\mcp-server.js`, env `HINDSIGHT_MCP_HARNESS=workbuddy` | 2026-11-06 | [FACT/LOCAL] |
| MCP 目录模板 | `connector:*` 共 208 项 | `~/.workbuddy/connectors/default/mcp.json` | 全部 `disabled: true`（**模板默认值**，非用户实际状态） | 含 github / ima-mcp / tencent-docs / lexiang 等端点，部分带 `${*_TOKEN}` 占位符；详见文件 | 2026-11-06 | [FACT/LOCAL] |
| MCP（skill 级） | lexiang | `~/.workbuddy/skills/lexiang-knowledge-base/mcp.json` | `enabled: true` | streamable-http `https://mcp.lexiang-app.com/mcp?company_from=${COMPANY_FROM}`, `Authorization: Bearer ${LEXIANG_TOKEN}` | 2026-11-06 | [FACT/LOCAL] |
| 连接器 | ima-mcp（显示名 ima） | 运行时注册表 `list_installed_plugins` | `status: connected / bound: true / enabled: true` | `ima-mcp@workbuddy-connector-plugins-official` | 2026-11-06 | [FACT/RUNTIME] |
| 连接器 | tencent-docs（腾讯文档） | 同上 | `connected / bound:true / enabled:true` | `tencent-docs@workbuddy-connector-plugins-official` | 2026-11-06 | [FACT/RUNTIME] |
| 连接器 | tmeet（腾讯会议） | 同上 | `connected / bound:true / enabled:true` | `tmeet@workbuddy-connector-plugins-official` | 2026-11-06 | [FACT/RUNTIME] |
| 连接器 | github（GitHub） | 同上 | `connected / bound:true / enabled:true` | `github@workbuddy-connector-plugins-official` | 2026-11-06 | [FACT/RUNTIME] |
| 配置标记 | `enabledPlugins` 4 项 = false | `~/.workbuddy/settings.json` 行 1306-1322 | `github` / `tmeet` / `tencent-docs` / `ima-mcp`@`workbuddy-connector-plugins-official` 与 `wecom-unified@workbuddy-wecom-unified` 为 false；其余内置 true | 与连接器 `enabled` 属**不同语义层**，勿直接对比 [INFERENCE] | 2026-11-06 | [FACT/LOCAL] + [INFERENCE] |
| 插件 | neko-plugin-ops | `my-experts/plugins/neko-plugin-ops/.codebuddy-plugin/plugin.json` | 已安装（`expertType: team`，1 lead + 11 members） | **v1.6.0**（2026-11-07 23:16 升版：新增 `neko-sandbox` 成员 + 沙箱重构落地） | 2026-11-07 | [FACT/LOCAL] |
| 插件注册 | neko-plugin-ops | `my-experts/.codebuddy-plugin/marketplace.json` | 已注册，`plugins=3` | **v1.6.0**, `source: ./plugins/neko-plugin-ops` | 2026-11-07 | [FACT/LOCAL] |
| 打包产物 | neko-plugin-ops.zip | `my-experts/packages/` | 文件存在 | 与插件目录内容一致性 [未联网核验 / 未解压比对] | 2026-11-06 | [FACT/LOCAL] |
| Marketplace | cb_teams_marketplace | `~/.workbuddy/plugins/marketplaces/<name>/.codebuddy-plugin/marketplace.json` | 清单存在 | 29 插件 | 2026-11-06 | [FACT/LOCAL] |
| Marketplace | codebuddy-plugins-official | 同上 | 清单存在 | 206 插件 | 2026-11-06 | [FACT/LOCAL] |
| Marketplace | experts | 同上 | 清单存在 | 5 插件 | 2026-11-06 | [FACT/LOCAL] |
| Marketplace | my-experts | 同上 | 清单存在 | **3 插件**：edict-sansheng 1.0.0 / neko-plugin-court 1.0.0 / neko-plugin-ops 1.6.0 | 2026-11-07 | [FACT/LOCAL] |
| Marketplace | workbuddy-connector-plugins-official | 同上 | 清单存在 | 350 插件 | 2026-11-06 | [FACT/LOCAL] |
| Marketplace | workbuddy-wecom-unified | 同上 | 清单存在 | 1 插件 | 2026-11-06 | [FACT/LOCAL] |
| 目录（非 marketplace） | plugin-data | `~/.workbuddy/plugins/marketplaces/plugin-data/` | **无 marketplace.json**，数据目录 | 合计 **7 目录 / 6 份清单** | 2026-11-06 | [FACT/LOCAL] |
| Hook（workbuddy 根·自动执行） | SessionStart #1 hindsight | `~/.workbuddy/settings.json` 行 1328-1337 | 已挂载，无 matcher | `node ...\workbuddy-sessionstart-hook.js`, timeout 30 | 2026-11-06 | [FACT/LOCAL] |
| Hook（workbuddy 根·自动执行） | SessionStart #2 haloflux-lab | 同上 行 1338-1346 | 已挂载，无 matcher | `node ~/.workbuddy/skills/haloflux-lab/scripts/session-beacon.mjs`, timeout 15 | 2026-11-06 | [FACT/LOCAL] |
| Hook（workbuddy 根·自动执行） | UserPromptSubmit | 同上 行 1348-1358 | 已挂载 | `node ...\workbuddy-hook.js`, timeout 30 | 2026-11-06 | [FACT/LOCAL] |
| Hook（workbuddy 根·自动执行） | Stop | 同上 行 1359-1369 | 已挂载 | `node ...\workbuddy-stop-hook.js`, timeout 60 | 2026-11-06 | [FACT/LOCAL] |
| **Hook（codebuddy 根·自动执行）** | **SessionStart** | `~/.codebuddy/settings.json` `hooks.SessionStart` | 已挂载，无 matcher | `node ...\codebuddy-sessionstart-hook.js`, timeout 30 | 2026-11-06 | [FACT/LOCAL] |
| **Hook（codebuddy 根·自动执行）** | **UserPromptSubmit** | 同上 `hooks.UserPromptSubmit` | 已挂载 | `node ...\codebuddy-hook.js`, timeout 30 | 2026-11-06 | [FACT/LOCAL] |
| **Hook（codebuddy 根·自动执行）** | **Stop** | 同上 `hooks.Stop` | 已挂载 | `node ...\codebuddy-stop-hook.js`, timeout 60 | 2026-11-06 | [FACT/LOCAL] |
| **Hook 配置根（风险）** | `~/.workbuddy/settings.json` | 用户级、**推断为本机生效**配置；自动执行代码 4 条 | 3 事件 / 5 条命令 | 40882 B, mtime 2026-11-04 16:50:21 +0800；生效性 [INFERENCE]，依据见 §3.1 | 2026-11-06 | [FACT/LOCAL] + [INFERENCE] |
| **Hook 配置根（风险）** | `~/.codebuddy/settings.json` | **旧 CodeBuddy 体系、并存（勿挂错）**；自动执行代码 3 条 | 3 事件 / 3 条命令（`codebuddy-*`），不含 haloflux-lab beacon；`permissions.defaultMode: "fullAccess"`、`trustedDirectories: ["~/**","E:/**"]` | 1063 B, mtime 2026-10-19 22:46:09 +0800 | 2026-11-06 | [FACT/LOCAL] |
| codebuddy 根配套 | `.mcp.json` + 两份 hindsight 备份 | `~/.codebuddy/` | 存在 | `.mcp.json` 244 B（`hindsight` server, `HINDSIGHT_MCP_HARNESS=codebuddy`）；`.mcp.json.hindsight-backup` 244 B；`settings.json.hindsight-backup` 254 B；`~/.codebuddy/skills/.ef-manifest.json` **缺失属正常，非异常** | 2026-11-06 | [FACT/LOCAL] |
| Hook 脚本 | 8 个 | `~/.hindsight/coding-agents/dist/*.js`（7 个，含 3 个 `codebuddy-*.js`）+ `~/.workbuddy/skills/haloflux-lab/scripts/session-beacon.mjs` | **8/8 存在** | 双根 hook 脚本存在性检查通过 | 2026-11-06 | [FACT/LOCAL] |
| Skill | 73 个目录（检测脚本口径 69，差异为是否要求 `SKILL.md`） | `~/.workbuddy/skills/` | 已安装 | 明细清单由 neko-skill-ops 维护，本表仅记锚点 | 2026-11-07 | [FACT/LOCAL] |
| 运行时 | Python 3.13.14 / Node v22.22.2 | 系统 PATH | 可用 | 检测脚本零第三方依赖 | 2026-11-06 | [FACT/LOCAL] |
| 官方规范 | SessionStart / additionalContext | `workbuddy.ai/docs/ide/Features/hooks` | 已联网核验 | 每会话一次（conversationId）、matcher `source=startup`、并行执行、自动去重、Session Tracking；**事件专项语义修正见 `session-refresh-sop.md` §1** | 2026-11-06 | [FACT/DOCS] |

**上表两行的现值更正（2026-11-18 现读，不改写 11-06 那一列，历史值留着）。**
`Hook 脚本 | 8 个 | 8/8 存在` 已不成立：`~/.hindsight/coding-agents/` 整目录不存在（实测
`os.path.isdir` 为假），7 条 hindsight 脚本全部不在位。两根 settings.json 里那 6 条注册仍然挂着，
指向不存在的文件，所以每个会话这三个事件都会起 node 打空。
现读计数：`~/.workbuddy/settings.json` hooks = 3 事件 / 5 条命令（含本工程挂载的第 3 条），
`~/.codebuddy/settings.json` = 3 事件 / 3 条命令。检测脚本的判据同步改掉：
写死的 8 条路径换成「本工程 1 条 + 扫注册表查悬空」，理由与验收写在 AUDIT 的 W12 条目。


## 2. 变更检测清单（下次会话开始逐一核对）

| # | 文件路径 | 必须核对的字段 | 基线期望值（2026-11-06 实算） | 判定为「有变更」 |
|---|---|---|---|---|
| 1 | `~/.workbuddy/mcp.json` | `mcpServers` 键集合；每项 `type/url/command/args/timeout/disabled/env` | 5 项：anysearch / playwright / rea 启用；lighthouse-ops / hindsight 禁用 | 指纹 `7d7adece4903` 不等 |
| 2 | `~/.workbuddy/connectors/default/mcp.json` | `mcpServers` **数量**与键集合、`disabled` 状态 | 208 项，全 disabled | 指纹 `72896b8dcc73` 或计数 ≠ 208 |
| 3 | `~/.workbuddy/skills/lexiang-knowledge-base/mcp.json` | `mcpServers.lexiang` 的 `enabled/url/headers` | enabled:true | 指纹 `c891097aa218` 不等 |
| 4 | `~/.workbuddy/settings.json`（**推断生效根**） | **双根 hook 集合指纹之一**：`hooks`（事件名、命令串、timeout、matcher）+ `enabledPlugins` 两项受控字段。**`pluginConfigs` 不参与指纹**：`_ext_settings()` 只取这两项（2026-11-17 读代码核对），所以插件配置页里的改动不会被本检测点看见，这是已知缺口，不是「已覆盖」 | 3 事件 / 5 命令（SessionStart×3 / UserPromptSubmit×1 / Stop×1）；4 连接器 false | 指纹 `02fb107c4263` 不等 |
| 5 | `~/.codebuddy/settings.json`（**旧体系并存根**） | **双根 hook 集合指纹之二**：`hooks`（SessionStart / UserPromptSubmit / Stop 各 1 条 `codebuddy-*` 命令、timeout）+ `enabledPlugins`（该根无此键）；另按 §3.3 核对 `permissions.defaultMode` / `trustedDirectories` / `.mcp.json` / 两份 hindsight 备份 / `skills/.ef-manifest.json` 缺失属正常 | 3 事件 / 3 命令；hook 集合指纹 `684c0eef3067` | 指纹 `684c0eef3067` 不等，或任一 hook 事件增删 |
| 6 | 6 份 `marketplaces/*/.codebuddy-plugin/marketplace.json` | `name`、`plugins[].{name,version,source}`、插件数量 | 29 / 206 / 5 / **3** / 350 / 1；my-experts 指纹 `e5c3984b0016` | 各自 `fp` 不等，或清单增删 |
| 7 | `marketplaces/` 一级目录集合 | 目录名集合 | 7 个（含 `plugin-data`） | 新增/删除目录；或 `plugin-data` 突然出现清单 |
| 8 | `my-experts/plugins/neko-plugin-ops/.codebuddy-plugin/plugin.json` | `version`、`agents[]`、`teamInfo`、`expertType` | **v1.6.0**，1 lead + 11 members | 指纹 `2689e205c4d0` 不等 |
| 9 | `~/.workbuddy/skills/` 一级目录集合 | 目录名集合 | 69 个 | 数量或清单指纹 `2e24b30b2315` 变化（明细由 neko-skill-ops 维护） |
| 10 | 本工程自己的 hook 脚本 1 条 + 两根 settings.json 的注册 | 存在性与「注册指向的文件在不在」 | 就绪 1/1，注册无悬空 | 注册悬空 → 每次会话起进程打空，须报警（2026-11-18 实测 6 条，见工单 W12） |
| 11 | 连接器运行时状态 | `list_installed_plugins` 的 `status/bound/enabled` | 4 个 connected/bound:true/enabled:true | **只读 hook 无法覆盖**（SessionStart 时 MCP 尚不可用）；须在会话内 MCP 就绪后显式调用补齐，标 [FACT/RUNTIME] |
| 12 | 远程版本 / 云端目录 | `@latest`、npm 包、marketplace 云端新增、官方文档修订、第三方 skill/MCP 上游更新、`packages/*.zip` 一致性 | 不设基线值 | **一律标 [未联网核验]**，禁止断言「已更新」；需用户确认后联网核验 |

> 新增 `references/*.md` 与 `scripts/` 目录**不在指纹范围内**（只覆盖上表 **14** 个检测点（含 2026-11-06 新增 `backups.workbuddy` / `backups.codebuddy` 两项存在性计数）），因此落盘交付物本身不会触发误报。
>
> **#4 / #5 共同构成「双配置根 hook 集合」指纹检测点**：脚本 `collect_fingerprints()` 已同时读取两个 `settings.json`（`_ext_settings` 取 `hooks` + `enabledPlugins` 受控字段后 `sha256[:12]`），`settings.workbuddy=02fb107c4263` 与 `settings.codebuddy=684c0eef3067` 当下实算均与 `BASELINE` 一致。两个根属**不同产品**（WorkBuddy vs 旧 CodeBuddy 体系），**不构成官方 dedup 的「跨 settings 层级同一 handler」，不可假设只跑一次**；详见 §3.3。

## 3. 已知语义说明与风险（避免误判）

### 3.1 双配置根风险（最高，必读）

- `~/.workbuddy/settings.json`（40882 B，2026-11-04 16:50）**推断为本机 WorkBuddy 生效配置**；`~/.codebuddy/settings.json`（1063 B，2026-10-19 22:46）属**旧 CodeBuddy 体系**，自带另一套 hindsight `codebuddy-*-hook.js` hooks。
- 推断依据（**均为 [INFERENCE]**，无官方配置加载日志实证）：
  1. `.workbuddy/settings.json` 的 `officeFileAssociationsRepairMarker` 实测含 `E:\Users\<user>\AppData\Local\Programs\WorkBuddy\WorkBuddy.exe:WorkBuddy:user:...`，指向**当前运行的 WorkBuddy 应用**；`.codebuddy` 是 CodeBuddy 命名体系。
  2. 主理人实测的 SessionStart 2 条（hindsight `workbuddy-sessionstart-hook.js` + haloflux-lab `session-beacon.mjs`）**只存在于 `.workbuddy/settings.json`**；`.codebuddy` 不含 beacon。
  3. `.workbuddy/projects` mtime 2026-11-05 20:54 且含当前会话目录；`.codebuddy/projects` mtime 2026-10-08 12:19，仅旧 N.E.K.O / MaiBot 项目。**两个 projects 目录都真实存在**，只是活跃度差约一个月。
  4. 体积 40882 B 且近期持续被改写 vs 1063 B 近一个月未动，符合「主配置 vs 静态残留」。
- **诚实边界**：不能断言 `.codebuddy` 完全不被任何组件读取（可能是旧版残留或并行安装）；最可靠验证是挂载后新开会话观察 hook 是否触发。因此检测清单把**两个根都纳入**（#4 / #5），但**只往 `~/.workbuddy/settings.json` 挂**。官方文档写用户级 `~/.codebuddy/settings.json` 是 CodeBuddy 时代路径，与本机实际根不一致 —— 这正是挂错文件导致 hook 不生效的根源。

### 3.2 其他语义

- `connectors/default/mcp.json` 的 `disabled: true` 是**模板默认值**，不代表用户实际启用状态；4 个连接器在运行时注册表中为 `enabled: true`。两者各自独立比对。
- `enabledPlugins` 的 false 与连接器 `enabled: true` 属**不同语义层** [INFERENCE]，官方未查到该字段定义，勿据此判定「连接器被禁用」。
- `plugin-data` 不是 marketplace，统计时须注明「7 目录 / 6 清单」。
- Hook 事件专项语义（exit 2 在 SessionStart 不阻塞、自动去重真实语义、纯文本 stdout、`additionalContext` 为不可见 system-reminder、SessionStart 时 MCP 不可用）见 `session-refresh-sop.md` §1，以该处为准。

### 3.3 codebuddy 配置根：自动执行代码与已知缺口（2026-11-06 补录）

> 措辞与云安 `security-refresh-notes.md` §5 第 12 行 / §6 检测清单保持一致；本轮云稳已做**只读复核**，未做代码级审计。

- `[FACT/LOCAL]` `~/.codebuddy/settings.json`（1063 B）顶层键为 `trustedDirectories / model / codebuddy / permissions / autoCompactWindow / hooks`；其中 `permissions.defaultMode: "fullAccess"`、`trustedDirectories: ["~/**","E:/**"]`，权限模式比 workbuddy 根更宽。
- `[FACT/LOCAL]` 该根有**独立的 3 条自动执行 hook**：`SessionStart` → `codebuddy-sessionstart-hook.js`(30s)、`UserPromptSubmit` → `codebuddy-hook.js`(30s)、`Stop` → `codebuddy-stop-hook.js`(60s)，均指向 `~/.hindsight/coding-agents/dist/`，不含 haloflux-lab beacon。
- `[FACT/LOCAL]` 该根另有 `~/.codebuddy/.mcp.json`（244 B，含 `hindsight` MCP server，`HINDSIGHT_MCP_HARNESS=codebuddy`）与**两份** hindsight 备份：`.mcp.json.hindsight-backup`（244 B）、`settings.json.hindsight-backup`（254 B）；`~/.codebuddy/skills/.ef-manifest.json` **缺失属正常，非异常**。
- `[FACT]` **第二套自动执行 hook 未被任何审计记录覆盖**，且 `fullAccess` 权限模式更宽 → 必须进检测清单，但**不得在此根挂新 hook**（会形成第二套自动执行注册）。来源：云安 `security-refresh-notes.md` §5 第 12 行 / §6。
- **两配置根属不同产品**（WorkBuddy vs 旧 CodeBuddy 体系），**不构成官方 dedup 的「跨 settings 层级同一 handler」，不可假设只跑一次**；官方去重只针对「同一 handler 定义在多个 settings 文件」，见 `session-refresh-sop.md` §1.2-b。
- **已知覆盖缺口（不伪造）**：配套项（`.mcp.json`、两份备份、`permissions` / `trustedDirectories`、`skills/.ef-manifest.json` 存在性）当前是**文档级检测项**（§2 #5 与本节），**尚未纳入脚本指纹**；脚本 `collect_fingerprints()` 仅对两个 `settings.json` 的 `hooks` + `enabledPlugins` 受控字段取 `sha256[:12]`。若要把这些配套项纳入脚本级检测，须按 §4 刷新 `BASELINE`、重新做代码级审计并重新取得用户同意（**已挂载 H4**，刷新 BASELINE 须按 T1 重新审计 + 重新同意）。
- **状态描述不一致的典型**：`~/.workbuddy/mcp.json` 中 `hindsight.disabled:true` 与 `~/.workbuddy/settings.json` 的 3 条活跃 hindsight hook 并存 —— 本表已如实分列两行，不得据 `disabled:true` 断言「hindsight 未运行」。

## 4. 指纹基线（与检测脚本 `BASELINE` 常量同步维护）

```
mcp.workbuddy=7d7adece4903  mcp.catalog=72896b8dcc73 (208)          # anysearch 改匿名（删失效 Authorization）
mcp.skill.lexiang=c891097aa218  settings.workbuddy=02fb107c4263
settings.codebuddy=684c0eef3067  plugin.neko-plugin-ops=2689e205c4d0   # v1.6.0（含 11 号成员 neko-sandbox）
marketplaces: cb_teams=e2cf94afd6bb(29) codebuddy-official=46c87e0afe88(206)
  experts=d19afc65e7c6(5) my-experts=e5c3984b0016(3)                    # edict-sansheng / neko-plugin-court / neko-plugin-ops
  connector-official=2e6c4f72e6e1(350) wecom-unified=36af02a124f3(1)
marketplace_dirs_fp=（脚本未实现该指纹，仅核对 7 个目录名）  skills=69 (fp 2e24b30b2315)
```

> **✅ 脚本 `BASELINE` 已同步（H6，2026-11-07 23:42 部署）**：文档基线与脚本常量现已一致，首跑实测「无变更（基线一致，最后核验 2026-11-07）」。上表 5 项即本次刷新的对象，成因全部来自用户已批准的改动（anysearch 改匿名 / 专家团升 1.6.0 并新增 `neko-sandbox` / 新建两个朝议专家团 1→3 插件 / 安装 security-audit 68→69），**不是异常漂移**。刷新过程与独立复审结论见 `security-refresh-notes.md` §10 变更记录（H6 行）。
>
> **2026-11-17 本机复跑记录（仓库那份脚本，未联网）**：报「有变更（10 项）」——
> 3 项是指纹变化（`mcp.workbuddy` 7d7adece4903 → de986b4d4759、`settings.workbuddy` 02fb107c4263 → 797ab575a7bc、
> 一个 marketplace 清单 fp 36af02a124f3 → f1b1e85e0f8f），7 项是 `~/.hindsight/coding-agents/dist` 整个目录已不存在，
> 里面 7 个 hook 脚本全部报缺失。**这 10 项都是未登记的漂移，不是谁批准的改动**，所以本轮不刷 `BASELINE`。
> 要不要按新机重算基线，请 云玩家 拍板；在那之前，本表所有旧指纹仍是「上一次批准的状态」。
>
> **刷新未制造静默区**：独立复审用 6 例内存漂移注入逐项验证（含 `my-experts.plugins` 计数单独变化），6/6 均正常报警；5 个参与指纹的文件密钥模式扫描 0 命中，未将任何凭据洗白进基线。

> `settings.codebuddy=684c0eef3067` 即 **`~/.codebuddy/settings.json` 的 hook 集合指纹**（`hooks` + `enabledPlugins` 受控字段），与 `settings.workbuddy=02fb107c4263` 共同构成 §2 #4/#5 的双根检测。两者 2026-11-06 重新实算均与 `BASELINE` 一致，本版补录已刷新 `settings.workbuddy`（挂载自指所致）并新增 `backups.*` 两项，其余指纹值不变。

刷新方式（**只读打印，不写文件**）：
```bash
python scripts/session_capability_refresh.py --regen   # 在仓库根目录跑仓库那一份
```
刷新命令只认**仓库那一份**脚本。同一份代码在本机有三处副本：仓库 `scripts/`、
`~/.workbuddy/hooks/` 挂载态、marketplace 安装态。**会话里真跑的是挂载态那一版**，
所以它最容易落后。2026-11-17 实测三处 sha256 前 12 位互不相同，挂载态比仓库旧 8 小时，安装态旧 10 天。
别把这三个数抄进长期结论——它们每轮都变，量一次就知道：

```
python - <<'PY'
import hashlib, os
for p in ('scripts/session_capability_refresh.py',
          '~/.workbuddy/hooks/session_capability_refresh.py',
          '~/.workbuddy/plugins/marketplaces/my-experts/plugins/neko-plugin-ops/scripts/session_capability_refresh.py'):
    f = os.path.expanduser(p)
    print(hashlib.sha256(open(f, 'rb').read()).hexdigest()[:12] if os.path.isfile(f) else '缺失', p)
PY
```

同步口径要说准：`install_sync.py` 管 `.codex` / `.claude` / `.zcode` 三棵宿主的**四面**：skill 树（`skills/plugin-forge` 与 `skills/neko-plugin-sandbox` 两棵）、`agents/*.md` 人设、`codex/prompts/`（只有 `.codex` 有 prompts 目录）。预演输出按这四面分行报数，判覆盖面看输出别看本文。
**它不碰 `~/.workbuddy/hooks/` 那份挂载副本，也不碰 marketplace 安装态**。
那两处要单独复制；复制前先跑仓库脚本的 `--self-test`。别拿挂载态或安装态的输出当仓库结论。

拿到新值后须**原子同步三处**：本文件 §1 表格 / §2 清单 / §4 指纹块、`session-refresh-sop.md` 的相关基线描述、脚本 `BASELINE` 常量；并同步刷新「基线版本」与「核验日期」。**有差异才改文件；无差异时报「无变更（基线一致，最后核验 <BASELINE_DATE>）」**——
日期取脚本常量 `BASELINE_DATE`（现值 2026-11-07），别在文档里另写一个日期。
