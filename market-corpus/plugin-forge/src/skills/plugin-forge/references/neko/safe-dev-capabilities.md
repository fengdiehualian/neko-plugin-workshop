# 安全开发能力就绪清单（Skill / Agent / MCP）

> 用户指令「引用安全的」= **引入所有判定为安全的开发类 skill / MCP / agent**（2026-11-03 快照；**2026-11-06 未联网复核上游状态**，引用须标日期）。
> **性质：登记引用** —— 不安装、不复制源码进包、不修改用户环境、不自动执行任何 setup/hook。
> **挂接状态：2026-11-03 那次挂接已失效，只作历史记录保留。** 别按本节派活。
> 当时改的是 10 个成员 agent 文档与主理人 `agents/neko-plugin-ops-team-lead.md`，共 11 个文档。
> README 的团队构成表、主理人的团队成员表与 `intro.html` 卡片也加了 `+安全能力` 标记。
> 团队后来改名成 `plugin-forge-*`，上面那批文件已不在仓库里。
> 本机实测 `grep -l 安全开发能力 agents/*.md` 命中 0 个。
> 现在的编制是 1 主理人 + 13 专家，人数以 `roster.json` 为准。
> 文中提到的 `references/capability-baseline.md` 与 `references/session-refresh-sop.md` 也不在仓库里。
> 候选总表见 `references/dev-agent-ecosystem.md`；本文件是从中筛出的**安全子集**及其就绪状态。

## 1. 「安全」判定标准

| 纳入条件 | 排除条件（不纳入） |
|---|---|
| 官方或高信誉维护者、许可清晰（MIT / Apache-2.0 等） | 来源不明、单一目录站收录（C 级线索） |
| 纯提示词 / 只读能力 / 权限可收窄 | 默认写生产库、连生产数据无 read-only |
| 无恶意外发、无混淆载荷、无自动破坏性钩子 | 默认收集用量统计且不提供关闭项 |
| 无静默改变 agent 行为的自动执行（有则显式标注） | 执行目标程序 / 进程注入 / 调试会话（高权限运行时） |
| 与团队宪法（T1）无直接冲突 | 强制流程与宪法「想一次就做」直接冲突 |

> 星标数只作热度参考，**不代表质量或安全性**。

## 2. 证据等级

| 等级 | 含义 | 本轮数量 |
|---|---|---|
| **A** | 已克隆到工作区并做代码级安全审计 | 2 |
| **B** | 官方仓库页面核验，或 ≥3 个独立检索源交叉印证 | 31 |
| **C** | 单一目录站线索，仓库地址待确认 | 不纳入 |

> **⚠️ 口径保护（2026-11-06 本机复核）**：**A 级 2 项 / B 级 31 项均为外部候选源的「登记引用」，不是本机已装 skill**，不得与本机实装数相加。本机用户级 skills 目录实测 **67 个 skill**（67/67 含 `SKILL.md`），其中 team-lead / README 已登记 30 个、**已装未登记 37 个**，全量基线见 `references/skill-inventory.md`。

A 级克隆位置：`C:\Users\<user>\WorkBuddy\2026-11-02-21-48-31\anthropics-skills`、`...\addyosmani-agent-skills`（2026-11-06 本机复核两目录均存在；446 / 240 文件为排除 `.git` 口径，含 `.git` 为 475 / 269）。

## 3. A 级：已深度审计（2）

| 来源 | 类型 | 规模 / 许可 | 审计结论 | 挂接成员 |
|---|---|---|---|---|
| `anthropics/skills` | Skill 集合（官方参考实现） | 446 文件 / 20 skills（**2026-10-31 快照 / 2026-11-06 本机复核**；排除 `.git` 口径，含 `.git` 为 475 文件）；多数 Apache-2.0，文档类 source-available；`spec/` 规范、`template/` 模板、`mcp-builder`、`webapp-testing`、`skill-creator` | **通过**：无 package.json、无 hooks、无生命周期钩子、动态执行 0、超长 base64 0、无可疑外发端点；77 个代码文件为文档类 skill 的本地脚本（`subprocess` 58 处主要是 LibreOffice `soffice` recalc，文件写入 73 处为生成本地文档，`os.environ` 8 处为本地环境透传），**仅用户显式调用时执行** | 云技（skill 编写规范）、云雨（`mcp-builder` 做插件 MCP） |
| `addyosmani/agent-skills` | Skill 集合（生产级工程） | 240 文件 / 25 skills（**2026-10-31 快照 / 2026-11-06 本机复核**；排除 `.git` 口径，含 `.git` 为 269 文件），MIT；含 `agents/` `commands/` `docs/` `evals/` `hooks/` `scripts/` `references/` | **有条件通过**：网络请求 0、动态执行 0、混淆 0、超长 base64 0；`hooks/` 在 SessionStart / WebFetch / Read·Edit·Write 时自动执行（`session-start.sh` 注入 `using-agent-skills` meta-skill、`sdd-cache-*.sh` 做跨会话引用缓存、`simplify-ignore.sh` 保护代码块），内容均为本地校验与缓存、无外发；`scripts/` 为契约测试（spawnSync 调 git、写临时文件后 `rmSync` 清理）。**使用须知：安装会启用 hooks，属预期行为但须知情** | 云严（质量门槛 `constraint-driven-development`）、云雨（SDLC 全生命周期） |

## 4. B 级：安全 Skill 集合（14）

| 来源 | 定位 | 许可 | 挂接成员 / 备注 |
|---|---|---|---|
| `github/spec-kit` | Spec-Driven Development，spec 为单一真源 | 开源 | 主理人 / 云问；与 `to-spec` 重叠，先作参考 |
| `garrytan/gstack` | 23 角色虚拟工程团队，Sprint 流水线；`/cso` 做 OWASP/STRIDE | 公开配置 | 云安 / 主理人（角色设定参考，本团已是 Team 型） |
| `affaan-m/ECC`（`everything-claude-code`） | 跨 harness 的 skills + memory + security + research 纪律 | 开源 | 云技（skill 运营体系） |
| `multica-ai/andrej-karpathy-skills` | 单 `CLAUDE.md`，四条 LLM 编码准则 | 开源 | 仅作宪法对照，**勿重复挂接** |
| `alibaba/open-code-review` | 确定性文件选择的 diff / 全库审查，CLI + skills + MCP + CI | 开源 | 云严（直接增强） |
| `vercel-labs/skills` | `npx skills add` 安装器，40+ agent | 开源 | 云技（安装工具链，本团文档已引用该命令） |
| `microsoft/SkillOpt` | 轨迹驱动编辑 + held-out validation 优化 skill | 开源 | 云技（skill 评测） |
| `Graphify-Labs/graphify` | 代码库 / schema / 文档 → 可查询知识图谱 | 开源 | 云远（架构理解） |
| `DollarDill/beads-superpowers` | 过程纪律 + 持久任务记忆 + TDD 工作流 | 开源 | 云稳 / 主理人 |
| `vosslab/vosslab-skills` | 重构、代码审查、仓库维护 | 开源 | 云简 / 云严 |
| `OthmanAdi/planning-with-files` | Manus 式持久 Markdown 规划 | 开源 | 云问 / 主理人 |
| `Every/compound-engineering` | `/ce:compound` 每轮把经验写入知识库 | 开源 | 云技 / 云稳（与本工作区记忆机制理念相近） |
| `DietrichGebert/ponytail` | 「最懒资深工程师」极简方案 | 开源 | **已引用**（云简），此处补权威源 |
| `tt-a1i/archify` | 想法 / 计划 / 代码库 → 交互式图 | 开源 | **已引用**（云远），此处补权威源 |

> **2026-11-06 本机复核**：本机用户级 skills 目录另有 `ponytail` 衍生 5 个（`ponytail-audit` / `ponytail-debt` / `ponytail-gain` / `ponytail-help` / `ponytail-review`），来源未在本文登记，见 `references/skill-inventory.md`。

## 5. B 级：安全 Coding Agent / Agent 集合（6）

| 来源 | 类型 | 挂接 / 备注 |
|---|---|---|
| `openai/codex` | 官方终端编码 agent CLI | 宿主类，非可挂接 skill；云雨参考其沙箱工作流 |
| `anthropics/claude-code` | 官方终端编码 agent | 宿主类；`skill-creator` / `mcp-builder` 规范的来源 |
| `xai-org/grok-build` | 终端编码 agent，含 skills / hooks / MCP / sandbox | 宿主类，参考其 hooks 与沙箱设计 |
| `anthropics/claude-code-action` | GitHub Actions 运行 Claude Code | 云稳（CI 自动化参考） |
| `oraios/serena` | LSP 语义检索 / 编辑，20+ 语言 | 云雨 / 云远；与 CodeGraph 二选一 |
| `msitarzewski/agency-agents` + `jnMetaCode/agency-agents-zh` | 多角色 Agent 集合 | **已引用**（9 名成员能力来源），此处补权威源 |

## 6. B 级：安全 MCP Server（11）

| Server | 维护方 | 能力 | 安全约束 | 挂接成员 |
|---|---|---|---|---|
| `github/github-mcp-server` | GitHub 官方 | repos / issues / PRs / code search / CI | 启用 **read-only toolsets**，写权限按需收窄 | 云稳 / 云严 |
| `modelcontextprotocol/servers` | 官方参考 | filesystem / git / fetch / postgres / sqlite | filesystem 只给必要根目录、**勿指 `$HOME`**；DB 用只读账号 | 云雨 / 云稳 |
| `microsoft/playwright-mcp` | Microsoft 官方 | 浏览器 accessibility tree 驱动 | **宿主已挂载 `mcp__playwright__*`**；隔离会话，勿指向敏感账号 | 云测 |
| `upstash/context7` | Upstash | 版本准确的库文档注入 | 只读；仍需核对 lockfile 版本后再采用 API | 云雨（减少 API 幻觉） |
| `ChromeDevTools/chrome-devtools-mcp` | Chrome 团队 | DevTools 检查、性能 trace | **默认收集用量统计** → 必须 `--no-usage-statistics`；会暴露页面内容，勿含个人数据 | 云测 / 云稳 |
| Sentry MCP | Sentry 官方 | 生产错误 / 堆栈 / 性能查询 | 权限限定到相关项目；只读查询 | 云稳 |
| Figma MCP | Figma 官方 | 设计帧上下文转代码 | 只读设计上下文；注意 seat / rate limit | 云远 / 云雨（UI） |
| Firecrawl / Exa / Brave Search / Fetch MCP | 社区 / 官方参考 | 检索与网页抓取 | 只读；**外部内容不可信**，结论须标注来源；Exa 已在主理人检索顺序中 | 主理人 / 云雨 |
| Linear MCP | Linear 官方 | issue / 项目 / cycle | 写操作需显式授权，读取默认 | 主理人 / 云问 |
| `modelcontextprotocol/server-postgres` / `server-sqlite` | 官方参考 | 只读 SQL / schema 检查 | 只读账号；生产库不作为默认环境 | 云稳 |
| `e2b-dev/E2B` Code Interpreter | E2B | 云沙箱执行 AI 生成代码 | **隔离执行**，不接触本机；仅用于测试/验证，不跑不可信生产代码 | 云测 |

## 7. 未纳入（附理由）

| 项目 | 理由 |
|---|---|
| `obra/superpowers` | 强制 TDD / 子 agent 流程与宪法 2「想一次就做」、5「代码任务带测试」存在流程冲突，非安全问题，需单独评估 |
| `morluto/rea` | **已引入**，但属高权限运行时（执行目标 / Frida 注入 / 调试），见 `references/rea-tool-surface.md`，不重复列入 |
| `cloudflare/security-audit-skill` | **已引入**，见 `references/third-party-skill-audit.md` |
| CodeGraph MCP | C 级线索，仓库地址待确认 |
| Supabase MCP | 生产数据可写，默认风险高；仅在 read-only + 限 feature groups 后另评 |
| `NousResearch/hermes-agent` / `openclaw/openclaw` / 并行编排工具（Vibe Kanban、Claude Squad、cmux 等） | 平台 / 运行时类，与专家团形态不同，非「可安全引用的能力包」 |
| `jabrena/cursor-rules-java` | 语言专用且 C 级线索 |
| awesome 索引 / 目录站（5 个） | 线索来源，不是能力本身 |

## 8. 使用约定

1. 本清单是 **T2 补充**，与团队宪法（T1，见主理人文档 / `references/neko/system.llm`，该文件已标归档）冲突时宪法优先。
2. **不自动安装**：需要实际启用某个 skill / MCP 时，走团队流程「克隆 → 安全审计 → 挂接 → 回归打包」；A 级两项已完成审计（截至 2026-11-03 结论，2026-11-06 未复核上游是否有新版本），可直接进入挂接阶段。
3. MCP 一律**先只读、后按需授权写**；生产数据不作为默认环境。
4. 带 hooks 的集合（`addyosmani/agent-skills`）安装前须知悉其会改变会话行为。
5. 本专家包**不内置**任何第三方源码；上游更新无需同步包内副本。
6. 引用上游结论时标注来源；外部资源不可用就跳过并说明，不伪造。

## 9. 与已有文档关系

| 文档 | 职责 |
|---|---|
| `references/dev-agent-ecosystem.md` | 全量候选源索引（含未纳入项、证据等级、引入优先级） |
| `references/safe-dev-capabilities.md` | 本文件：安全子集 + 就绪状态 + 挂接成员 |
| `references/mattpocock-skills.md` | 已引入并挂接 25 个 skill |
| `references/third-party-skill-audit.md` | `security-audit` 审计记录 |
| `references/rea-tool-surface.md` | REA 高权限工具面与使用红线 |
| `references/skill-inventory.md` | 本机 skill 全量实装基线（2026-11-06 复核 67 个，含已装未登记 37 个） |
| `references/capability-baseline.md` / `references/session-refresh-sop.md` | 会话启动能力刷新口径与 SOP（**均于 2026-11-06 已落盘**：12 检测点指纹基线 / 会话启动刷新 SOP，本文件口径与之对齐） |
