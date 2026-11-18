# Matt Pocock Skills 权威上游（专家团技能体系来源）

> 专家团成员映射的多数技能来自本仓库。本文件登记权威上游、完整清单、与专家团的映射对照、安全审计结论与安装方式。
> **审计结论：通过** —— 纯提示词集合，无恶意外发、无自动执行钩子、无混淆载荷。
> **⚠️ 数据时效（2026-11-06 复核）**：本文全部数字为 2026-10-31 上游 commit 快照 + 2026-11-03 映射补齐快照；本次仅做本机只读复核，**未联网核验上游现状**，引用时须标日期。本机实装基线见 `references/skill-inventory.md`；会话启动刷新口径见 `references/capability-baseline.md` / `references/session-refresh-sop.md`（均于 2026-11-06 已落盘：分别为 12 检测点指纹基线 / 会话启动能力刷新 SOP）。
> **⚠️ 口径保护**：「上游 39」≠「本机实装 38」≠「已挂接 25」，三者口径不同不得互相代换；「未挂接」≠「未安装」。

## 1. 目标

| 项 | 值 |
|---|---|
| 上游 | https://github.com/mattpocock/skills |
| 定位 | *Skills For Real Engineers* —— Matt Pocock 日常使用的 agent skill，强调小而可组合、不接管流程 |
| 形态 | 纯提示词 skill 集合（`SKILL.md` + `openai.yaml` 元数据），非运行时工具 / MCP |
| 许可 | MIT |
| 审计 commit | `4ae2317ddb22b046e26455e4f9c9b3aaaf62212d`（2026-10-31，`software-factory: glossary skill for designing a factory (#1316)`） |
| 本地克隆 | 工作区内 `mattpocock-skills`（`--depth 1`，184 文件；**排除 `.git` 口径**，含 `.git` 为 213 文件，2026-11-06 本机复核） |
| 规模 | **39 个 skill**（含 `SKILL.md`），5 个 bucket（**2026-10-31 上游快照 / 2026-11-06 本机复核**；数字准确不改，本机实装 38/39，见 `references/skill-inventory.md`） |

## 2. 两个分类维度

**调用方式**（README Reference 定义）：
- **User-invoked**：只能用户显式触发（如 `/grill-me`），负责编排；可调用 model-invoked，但**不可调用另一个 user-invoked**。
- **Model-invoked**：用户或模型按任务自动触发，承载可复用纪律（如 `tdd`、`code-review`）。

**Bucket**：
| Bucket | 数量 | 说明 |
|---|---|---|
| `engineering` | 20 | 日常代码工作主集合 |
| `productivity` | 7 | 通用工作流（非代码专用） |
| `in-progress` | 8 | 公开征求反馈，**不建议默认挂接** |
| `misc` | 4 | 保留但不在日常安装中推广，**不建议默认挂接** |
| `deprecated` | 0（空） | 已退役 |

### 完整清单（39）

- **engineering / user-invoked**：`ask-matt`、`grill-with-docs`、`triage`、`improve-codebase-architecture`、`setup-matt-pocock-skills`、`to-spec`、`to-tickets`
- **engineering / user-invoked（续）**：`implement`、`implement-spec`、`wayfinder`、`retro`
- **engineering / model-invoked**：`prototype`、`diagnosing-bugs`、`research`、`tdd`、`domain-modeling`、`codebase-design`、`code-review`、`pr`、`wizard`
- **productivity / user-invoked**：`grill-me`、`handoff`、`teach`、`to-questionnaire`、`wait-what`
- **productivity / model-invoked**：`grilling`、`writing-for-agents`
- **in-progress（8）**：`chief-of-staff`、`claude-handoff`、`loop-me`、`setup-ts-deep-modules`、`software-factory`、`writing-beats`、`writing-fragments`、`writing-shape`
- **misc（4）**：`git-guardrails-claude-code`、`migrate-to-shoehorn`、`scaffold-exercises`、`setup-pre-commit`

> 注：`implement` / `implement-spec` / `wayfinder` / `retro` 在 README 中列于 Engineering 的 user-invoked 段；`prototype` 等列于 model-invoked 段。
> **2026-11-06 本机复核**：`software-factory` 在克隆中存在（`skills/in-progress/software-factory`），本机 `~/.workbuddy/skills/` 未安装；按 §3「in-progress 不默认挂接」属预期，非缺装。

## 3. 与专家团的映射对照（权威）

### 已挂接（25 个，2026-11-03 补齐；2026-11-06 复核未变）

| 成员 | 已挂接的本仓库 skill |
|---|---|
| 云朵（主理人）`neko-plugin-ops-team-lead` | `to-spec`、`to-tickets`、`triage`、`handoff` |
| 云雨 `neko-plugin-dev` | `prototype`、`research`、`diagnosing-bugs`、`implement` |
| 云测 `neko-tdd` | `tdd` |
| 云远 `neko-architect` | `codebase-design`、`improve-codebase-architecture`、`domain-modeling`、`grill-with-docs`、`wayfinder` |
| 云严 `neko-reviewer` | `code-review`、`pr` |
| 云技 `neko-skill-ops` | `setup-matt-pocock-skills`、`writing-for-agents` |
| 云问 `neko-planner` | `ask-matt`、`grill-me`、`grilling`、`to-questionnaire`、`wait-what` |
| 云稳 `neko-reliability` | `retro`、`wizard` |
| 云简 `neko-simplifier` | （本仓库无映射；`ponytail` / `simplify-codebase` 为仓库外来源） |
| 云安 `neko-security` | （本仓库无映射；挂接 `security-audit` 与 `rea`） |

映射同时落在四处：`agents/*.md` 的「映射技能（Matt Pocock Skills）」小节、README「团队构成」表、主理人「团队成员」表、intro.html 卡片标签。

### 仓库外来源（明确区分，勿混淆）

`neko-plugin-sdk`（本地 SDK）、`archify`、`ponytail`、`simplify-codebase`、`skill-creator`、`find-skills`、`skill-supply-chain-hardening`、`security-audit`（Cloudflare）、`rea`（MCP 工具面）。

> **口径（2026-11-06 本机复核）**：上述 9 项中，`neko-plugin-sdk` / `archify` / `ponytail` / `simplify-codebase` / `skill-supply-chain-hardening` 位于用户级 `~/.workbuddy/skills/`；`skill-creator` / `find-skills` 为宿主内置 / 插件缓存 skill（不在用户级目录）；`security-audit` 为工作区克隆件；`rea` 为 MCP 工具面、非 skill 目录。另本机存在 `ponytail-audit` / `ponytail-debt` / `ponytail-gain` / `ponytail-help` / `ponytail-review` 5 个 ponytail 衍生 skill，本清单未登记，见 `references/skill-inventory.md`。

### 已补充映射（2026-11-03 执行，18 个）

| 成员 | 已补充 | 理由 |
|---|---|---|
| 云雨 `neko-plugin-dev` | `prototype`、`research`、`diagnosing-bugs`、`implement` | 原型验证、一手资料调研、故障诊断、按 spec 实现 |
| 云远 `neko-architect` | `domain-modeling`、`grill-with-docs`、`wayfinder` | 领域模型、术语澄清 + ADR、超大工作块规划 |
| 云严 `neko-reviewer` | `pr` | PR body 形态（最小可视化 + before/after 证据 + 合并风险） |
| 云技 `neko-skill-ops` | `writing-for-agents` | 写 skill / AGENTS.md / 指针文档 |
| 云问 `neko-planner` | `grilling`、`to-questionnaire`、`wait-what` | 可复用面试原语、异步问卷、消息没讲清时重述 |
| 云稳 `neko-reliability` | `retro`、`wizard` | 会话后环境改进、人工步骤 bash wizard（CI/凭据/迁移） |
| 主理人 | `to-spec`、`to-tickets`、`triage`、`handoff` | 会话转 spec、拆 tracer-bullet tickets、issue 状态机、跨会话交接 |

**执行结果**：上述 18 个已写入 7 个 agent 文档、README 团队构成表、主理人成员表与 intro.html 卡片标签；现共挂接 **25 个**（原 7 + 补 18；2026-11-03 快照，2026-11-06 复核未变）。**本机实装 38 个本仓库 skill，其中 25 个登记挂接、13 个已装未挂接**；全量 67 个 skill 中另有 37 个已装未登记，见 `references/skill-inventory.md`。

**仍未挂接（14 个）**：`implement-spec`、`teach`（暂不纳入）；`in-progress/` 8 个（征反馈中）；`misc/` 4 个（不推广）；`deprecated/`（空）。

> **2026-11-06 口径澄清**：此处「未挂接」指**未登记到成员映射**，不是「未安装」。14 项中 13 项本机已安装（清单见 `references/skill-inventory.md` §2.2），仅 `software-factory` 未安装。

## 4. 安全审计结论：通过

| 审计项 | 结论 | 证据 |
|---|---|---|
| 网络外发 | **无** | 184 文件中仅 7 个代码文件（184 为排除 `.git` 口径，2026-11-06 本机复核）；网络请求模式 0 处 |
| 子进程 / 命令执行 | **无** | `child_process` / `spawnSync` / `exec` 0 处 |
| 环境变量 / 动态执行 | **无** | `process.env` / `eval` / `new Function` / `vm.` 均 0 处 |
| npm 生命周期钩子 | **无** | `package.json` 仅 `changeset` / `version` / `check-plugin-version`；无 `preinstall`/`postinstall`/`prepare`/`install` |
| 文件写入 | 仅 2 处 | `scripts/sync-plugin-version.mjs` 写本仓库 `.claude-plugin/plugin.json` 的版本号（发布用） |
| 提示词注入 / 外发指令 | **0 命中** | 全量 `SKILL.md` 扫描 |
| 混淆载荷 | 无 | 无异常长 base64 串 |
| 安装脚本 | dev-only | `scripts/link-skills.sh` 头部 L4–6 明确「不是受支持的安装器」；它向 `~/.claude/skills`、`~/.agents/skills` 建符号链接，跳过 `deprecated/` 与 `misc/`，并有「DEST 指向本 repo 时 bail out」防护；**本专家团不执行** |
| 合规 | 完整 | MIT；`LICENSE` / `CONTRIBUTING.md` / `SCOPE.md` / `GLOSSARY.md` 齐备 |

**风险定级：低**（与 `rea` 的高权限运行时能力不同）。唯一需留意的是 `setup-matt-pocock-skills` 会配置 issue tracker、triage labels 与 `GLOSSARY.md`/ADR 位置——但它是 **prompt-driven，探索后须用户确认才写**（`disable-model-invocation: true`），不是确定性脚本。

## 5. 安装与使用约定

**安装（复制即可用，本专家团不代为执行）**：
```bash
# 任一 agent，或要可编辑副本：
npx skills@latest add mattpocock/skills -a <agent>   # cursor / opencode / devin / windsurf / amp / pi；省略 -a 交互选择
# Claude Code：
claude plugin install mattpocock-skills@claude-plugins-official
# Codex：
codex plugin marketplace add mattpocock/skills && codex plugin add mattpocock-skills@mattpocock
```
安装时在选择界面**包含 `setup-matt-pocock-skills`**；装完后每个 repo 跑一次 `/setup-matt-pocock-skills` 配置 issue tracker 与文档位置。更新用 `npx skills@latest update`。

**约定**：
1. 这些 skill 是 **T2 补充**，与团队宪法（T1，见主理人文档 / `references/neko/system.llm`，该文件已标归档）冲突时以宪法优先。
2. 安装方式二选一（plugin 自动更新 / skills.sh 手动更新），**不要同时装**，否则每个 skill 出现两份。
3. `user-invoked` 与 `model-invoked` 的调用边界按 README Reference 执行，不混用。
4. 本专家包**不内置**这些 skill 文件，仅登记权威上游；上游更新无需同步包内副本。
