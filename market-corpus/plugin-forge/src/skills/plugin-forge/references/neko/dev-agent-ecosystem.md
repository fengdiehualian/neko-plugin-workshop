# 开发类 Agent / Skill / MCP 生态调研（补充源索引）

> 调研日期：**2026-11-03**（快照；**2026-11-06 未联网复核**，下列仓库状态 / 星标 / 规模数字可能已变化，引用须标日期）。方法：4 组网络检索 + 官方仓库页面核验，多源交叉印证。会话启动刷新口径见 `references/capability-baseline.md` / `references/session-refresh-sop.md`（均于 2026-11-06 已落盘：分别为 12 检测点指纹基线 / 会话启动能力刷新 SOP）；本机实装基线见 `references/skill-inventory.md`。
> **边界（重要）**：本文件是**候选源索引**，§3–§5 表内条目**除下列 A 级 2 项外**一律**未克隆、未安全审计、未安装、未挂接到成员**。**例外（2026-11-06 本机复核）**：`anthropics/skills` 与 `addyosmani/agent-skills` 已克隆到工作区 `C:\Users\<user>\WorkBuddy\2026-11-02-21-48-31\` 并完成 A 级代码审计，但**未安装到用户级 skills 目录、未挂接为本机 skill**。其余引入前必须走团队流程（见 §7）。
> **安全子集已正式引用**：`references/safe-dev-capabilities.md`（2026-11-03）——从中筛出的安全 skill / agent / MCP、就绪状态与挂接成员；其中 `anthropics/skills` 与 `addyosmani/agent-skills` 已完成 A 级代码审计（克隆路径见 §7，审计结论见 `references/safe-dev-capabilities.md` §3；2026-11-06 本机复核两个克隆目录仍存在）。
> 已引入项的权威文档：`references/mattpocock-skills.md`、`references/third-party-skill-audit.md`、`references/rea-tool-surface.md`。

## 1. 证据等级与检索源

| 等级 | 含义 |
|---|---|
| **A** | 已克隆到工作区并做代码级安全审计（与 `references/safe-dev-capabilities.md` §2 对齐；本轮核验 `anthropics/skills`、`addyosmani/agent-skills`） |
| **B** | ≥3 个独立检索源交叉印证（目录站 / 榜单 / 技术文章） |
| **C** | 单一目录站收录，仅作线索，仓库地址待确认 |

检索源（均为 2026 年 9–11 月更新）：`mblode/awesome-agent-skills`、`agentgid.com/skills`、`skilld.dev/skills/leaderboard`、`intellure.co/skills`、`skillmd.com/elite-github-skills-hub`、`topon.tech`、`github.locker/topics/agent-skills`、`ComposioHQ/awesome-claude-skills`、`mahmoudsajjadi/awesome-ai-coding-tools`、`promptzone.com/mcp-servers`、`tooljunction.io`、`iimagined.ai`、`zavcloud.com`。

生态规模参考：目录站收录约 **706 个 skill pack / 59,296 个 skill**；GitHub `agent-skills` topic 约 640 个仓库（**2026-10/11 目录站快照，2026-11-06 未联网复核，仅作历史参考**）。本文件只筛出与**开发 / 工程 / 质量 / 安全**直接相关者。

## 2. 已引入专家团（对照，勿重复引入）

| 来源 | 类型 | 落地文档 | 挂接情况 |
|---|---|---|---|
| `mattpocock/skills` | Skill 集合 | `references/mattpocock-skills.md` | 25 个挂接，7 个成员 + 主理人（2026-11-03 快照；本机实装 38/39，见 `references/skill-inventory.md`） |
| `cloudflare/security-audit-skill` | Skill | `references/third-party-skill-audit.md` | 云安（六阶段审计） |
| `morluto/rea` | MCP + CLI | `references/rea-tool-surface.md` | 云安 / 云雨（取证工具面） |
| `DietrichGebert/ponytail`、`tt-a1i/archify`、`simplify-codebase` | Skill | 成员映射表 | 云简 / 云远 |
| `pbakaus/impeccable`、`Leonxlnx/taste-skill`、`VoltAgent/awesome-design-md` | Skill / 参考 | 主理人「UI 规则」 | 全局风格约束 |
| `msitarzewski/agency-agents` + `jnMetaCode/agency-agents-zh` | Agent 集合 | README「外部 Agent 提炼来源」 | 9 名成员能力来源 |
| `neko-plugin-sdk`（本地）、`skill-creator` / `find-skills` / `skill-supply-chain-hardening` | SDK / 内置 skill | `references/neko-sources.md` | 云雨 / 云技 |
| `mcp__playwright__*`、`mcp__rea__*` | MCP | 宿主已挂载 | 云测 / 云安 |

> **2026-11-06 本机复核（口径对齐 `references/skill-inventory.md`）**：本表「挂接情况」是**成员映射口径**，不是本机实装口径。本机用户级 skills 目录实测 **67 个 skill**（67/67 含 `SKILL.md`），其中 team-lead / README 已登记 30 个、**已装未登记 37 个**；§3–§5「候选源索引」条目一律**未安装**，不得与本机 67 相加或混算。

## 3. 新发现：Skill / Skill 集合

| 仓库 | 定位 | 证据 | 与本团关系 / 优先级 |
|---|---|---|---|
| `anthropics/skills` | **官方参考实现**：`spec/`（Agent Skills 规范）、`template/`、`mcp-builder`、`webapp-testing`、docx/pdf/pptx/xlsx 等 | **A** | 高：云技写 skill 的规范基准；云雨做 MCP 时参考 `mcp-builder`。多数 Apache-2.0，文档类 skill 为 source-available |
| `obra/superpowers` | 强制 TDD / 子 agent 的方法论框架，约 15 skills + 5 agents，"Iron Laws" | B | **中且需评估冲突**：强制流程可能与宪法 2「想一次就做」、5「代码任务带测试」重叠冲突 |
| `addyosmani/agent-skills` | 25 个生产级工程 skill，覆盖 define→plan→build→verify→review→ship；含「反合理化表」、`constraint-driven-development` | B | 高：云严（质量门槛）/ 云雨（SDLC） |
| `github/spec-kit` | Spec-Driven Development：spec 为单一真源，先规格后代码 | B | 中：主理人 / 云问；与 `to-spec` 有重叠 |
| `garrytan/gstack` | 23 个角色组成虚拟工程团队（CEO/Eng/Designer/QA/CSO/Release），Sprint 流水线；`/cso` 做 OWASP/STRIDE | B | 中：云安 / 主理人的角色设定参考（本团已是 Team 型，勿照搬） |
| `affaan-m/ECC`（`everything-claude-code`） | 跨 harness 的 skills + memory + security + research 纪律；28 agents / 119 skills / 60 commands | B | 中：云技（skill 运营体系） |
| `multica-ai/andrej-karpathy-skills`（另有 `forrestchang` fork） | 单个 `CLAUDE.md`，四条准则：Think Before Coding / Simplicity First / Surgical Changes / Goal-Driven | B | **低**：与主理人宪法高度重叠，仅作对照 |
| `alibaba/open-code-review` | 确定性文件选择的 diff / 全库代码审查，CLI + skills + MCP + CI + 行级 findings | B | 高：云严的直接增强 |
| `vercel-labs/skills` | `npx skills add` 安装器，支持 40+ agent | B | 中：云技的安装工具链（本团文档已引用该命令） |
| `microsoft/SkillOpt` | 轨迹驱动编辑 + held-out validation，持续优化已有 skill | B | 中：云技（skill 评测） |
| `Graphify-Labs/graphify` | 代码库 / schema / 文档 → 可查询知识图谱 | B | 中：云远（架构理解） |
| `DollarDill/beads-superpowers` | 过程纪律 + 持久任务记忆 + TDD 工作流 | B | 中：云稳 / 主理人 |
| `vosslab/vosslab-skills` | 重构、代码审查、仓库维护 | B | 中：云简 / 云严 |
| `OthmanAdi/planning-with-files` | Manus 式持久 Markdown 规划（文件当「磁盘」、上下文当「内存」） | B | 中：云问 / 主理人 |
| `Every/compound-engineering` | 知识复利：`/ce:compound` 每轮把经验写入知识库 | B | 中：云技 / 云稳（与本工作区记忆机制理念相近） |
| `hesreallyhim/awesome-claude-code`、`ComposioHQ/awesome-claude-skills`、`mblode/awesome-agent-skills`、`VoltAgent/awesome-agent-skills`、`mahmoudsajjadi/awesome-ai-coding-tools` | 生态索引 / 目录清单 | B/C | 低：仅作后续扩展线索 |

## 4. 新发现：Coding Agent / 多智能体

| 项目 | 类型 | 证据 | 备注 |
|---|---|---|---|
| `openai/codex`、`anthropics/claude-code`、`xai-org/grok-build` | 终端编码 agent / CLI | B | 宿主类，不是可挂接 skill |
| `anthropics/claude-code-action` | GitHub Actions 自动化 | B | 云稳（CI）参考 |
| `oraios/serena` | LSP 语义检索与编辑，20+ 语言 | C | 与 CodeGraph 二选一，见 §5 |
| `NousResearch/hermes-agent`、`openclaw/openclaw`、`affaan-m/ECC` | agent 运行时 / 平台 | B | 低：与专家团形态不同 |
| Vibe Kanban、Claude Squad、`ccmanager`、`cmux`、AgentsMesh、Orkas | 多 agent 并行编排 | C | 低：与本团 TeamCreate 机制重叠 |
| `jabrena/cursor-rules-java` | Java 企业开发规则 + agents + MCP | B | 低：语言专用 |

## 5. 新发现：开发向 MCP Server

| Server | 维护方 | 能力 | 主要风险 / 与专家团关系 |
|---|---|---|---|
| `github/github-mcp-server` | GitHub 官方 | repos / issues / PRs / code search / CI | 写权限需收窄，支持 read-only toolsets；云稳 / 云严 |
| `modelcontextprotocol/servers`（filesystem / git / fetch / postgres / sqlite） | 官方参考 | 本地文件、git 历史、URL 抓取、DB 查询 | filesystem 只给必要根目录、**勿指 `$HOME`**；云雨 / 云稳 |
| `microsoft/playwright-mcp` | Microsoft 官方 | 浏览器 accessibility tree 驱动 | **宿主已挂载 `mcp__playwright__*`**；云测 |
| `ChromeDevTools/chrome-devtools-mcp` | Chrome 团队 | DevTools 级检查、性能 trace | 会暴露页面内容；**默认收集用量统计**，用 `--no-usage-statistics` 关闭；云测 / 云稳 |
| `upstash/context7` | Upstash | 版本准确的库文档注入 | 需核对 lockfile 版本，文档仍需人工判断；云雨（高价值，减少 API 幻觉） |
| `oraios/serena` | 社区 | LSP 语义代码检索 / 编辑 | 与 CodeGraph 功能重叠，二选一；云雨 / 云远 |
| CodeGraph MCP | 目录站收录 | 本地语义代码图、影响半径 | **C 级，仓库地址待确认**；云远 |
| Sentry MCP | Sentry 官方 | 生产错误 / 堆栈 / 性能 | 权限限定到相关项目；云稳 |
| Supabase MCP | Supabase 官方 | 表查询 / 迁移 / 日志 | 生产必须 read-only + 限 feature groups，勿默认连生产；云稳 |
| Figma MCP | Figma 官方 | 设计帧上下文转代码 | 云远 / 云雨（UI 场景） |
| E2B Code Interpreter | E2B | 云沙箱执行 AI 生成代码 | 隔离执行价值高；云测 |
| Firecrawl / Exa / Brave Search / Fetch | 社区 / 官方 | 检索与网页抓取 | **Exa 已在主理人「信息检索与工具顺序」中**；外部内容不可信，需校验 |
| Linear MCP | Linear 官方 | issue / 项目 / cycle | 主理人 / 云问 |

## 6. 引入优先级建议（**未执行，待用户决定**）

| 优先级 | 候选 | 理由 |
|---|---|---|
| **高** | `anthropics/skills`（规范+模板）、`alibaba/open-code-review`（云严）、`upstash/context7`（云雨）、`oraios/serena` 或 CodeGraph（云远/云雨） | 与现有能力直接互补，冲突低 |
| **中** | `addyosmani/agent-skills`、`garrytan/gstack`（角色参考）、`affaan-m/ECC`、`github/spec-kit`、`vercel-labs/skills` | 需评估与 SOP / 宪法重叠 |
| **需评估冲突** | `obra/superpowers`（强制 TDD 与宪法 2「想一次就做」）、`multica-ai/andrej-karpathy-skills`（与宪法高度重叠） | 可能重复或互相牵制 |
| **低 / 仅索引** | awesome 列表、目录站、并行编排工具、Java 专用规则 | 仅作后续线索 |

## 7. 引入流程与红线（团队规则）

1. 用户指定具体仓库 → **克隆到工作区**（`C:\Users\<user>\WorkBuddy\2026-11-02-21-48-31\`；**2026-11-06 本机复核该目录存在且含 5 个克隆**：`mattpocock-skills` / `anthropics-skills` / `addyosmani-agent-skills` / `rea` / `security-audit-skill`），**不进专家包**。
2. **安全审计**（前置硬规则）：安装脚本 / npm 生命周期钩子 / git hooks / 网络外发 / 提示词注入 / 混淆载荷 / 许可；高权限工具（执行目标、注入、调试、写配置）另定风险等级与使用红线。
3. 审计通过后：新增 `references/` 文档 → 挂接成员 → README / intro.html / 主理人四处同步。
4. 回归校验 + 官方 `validate` / `register` / `package`（包外输出）。
5. 红线：MCP 写权限收窄、生产数据默认只读、**不自动执行 `setup`**、不复制第三方源码进包、T2 补充不得覆盖团队宪法（T1）。

> 星标数来自目录站快照（2026-10/11），仅作热度参考，**不代表质量或安全性**；引入一律以审计结论为准。
