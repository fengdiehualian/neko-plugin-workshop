# 文档编写能力 · 相关 skill 审阅台账

> **核验日期**：2026-11-09
> **方法**：① 本机 `~/.workbuddy/skills/` 只读扫描 + 逐目录 `SKILL.md` frontmatter/正文抽样；② WebFetch 拉取上游 GitHub 仓库 README / 文件树 / commit 记录；③ 对照 `references/skill-inventory.md`（2026-11-06 快照，68 个 skill）。
> **证据分级**：`[FACT/LOCAL]`=本机文件可直接验证 / `[FACT/UPSTREAM]`=上游仓库页可验证 / `[INFERENCE]`=推导 / `[GUESS/未核验]`=仅第三方聚合站摘要、未拉源 / `未核验`=外部未取到。
> **红线（用户长期规则）**：第三方 skill / 包**安装前必须先安全审计**；**循环 / 自动行为须先查官方文档并取得单独同意**。本轮**只做只读审阅，未安装、未克隆、未执行任何第三方脚本或 hooks**。

---

## 1. 结论速览

上一轮 playbook 只从搜索摘要里登记了 12 个外部 skill，**漏了本机已装的文档/写作类 skill**，且有 2 处事实错误（见 §5）。本轮补齐：

| 层 | 数量 | 处置 | 理由 |
|---|---|---|---|
| **本机已装**（§2） | 11 项 | **纳入 / 直接可用** | 零安装成本、无需第三方审计；2026-11-09 逐项 frontmatter 实测 **8 项** `disable-model-invocation: true` 只能 user-invoked（原写 4 项，见 §5 #8） |
| **外部·纯文本 SKILL.md**（§3.1） | 2 项 | **纳入规则借鉴** | 已拉源；MIT；无脚本无 hooks；但**未安装** |
| **外部·带 hooks/脚本**（§3.2） | 3 项 | **排除安装，仅借鉴思想** | 触碰自动行为 / hooks 红线；且与 §1.2 的 ASD-STE100 高度重叠 |
| **外部·代码库文档类**（§3.3） | 10 项 | **备选，不安装** | 需 Python/Node/shell 执行；本机已有 `grill-with-docs` 等替代 |
| **外部·未拉源**（§3.4） | 3 项 | **登记为未核验** | 仅第三方聚合站摘要，不得当作事实引用 |
| **排除（非写作）** | 4 项 | 不纳入 | `llm-wiki`（建库）、`markitdown-skill`（格式转换）、`docs-site-to-mcp`（建 MCP）、`global-biblio-base`（文献检索）属**输入/检索侧**，非出稿 |

---

## 2. 本机已装（优先层）

> 依据：`skill-inventory.md` 全量清单 + 本机 `SKILL.md` 实读 `[FACT/LOCAL]`。**这些无需安装、无需第三方审计**，是「整合进总监经验」性价比最高的一层。

| skill | 来源 / 状态 | 与文档能力的关系 | 判定 |
|---|---|---|---|
| `pr` | mattpocock / 已登记 `[FACT/LOCAL]` | **核心之一**：PR 三段结构 + 6 种可视化 | 已整合（§1.1） |
| `writing-for-agents` | mattpocock / 已登记（云技映射）`[FACT/LOCAL]` | 写「给 agent 读的文档」：skill / `AGENTS.md` / `CLAUDE.md`。核心概念 **context pointer**（措辞决定触发可靠性）、**两种负载**（context load vs cognitive load）、one trigger per branch | **补入 §2.1**：写 skill / 参考文档时走它，ASD-STE100 管词句、它管可触发性 |
| `writing-fragments` | mattpocock / in-progress·**未挂接**·`disable-model-invocation: true` `[FACT/LOCAL]` | **长文 explore**：追问采碎片，不承诺结构；只写一个 markdown 文件，首个 H1 即工作标题 | **补入 §3**：长文 Phase 1 |
| `writing-beats` | 同上 | **长文 exploit A**：beat 旅程，2–3 候选起点 → 用户选 → 只写该 beat → 重读磁盘 → 再给候选。**Grounding**：概念必须先落地才能被后续 beat 引用 | **补入 §3**：长文 Phase 2 |
| `writing-shape` | 同上 | **长文 exploit B**：读完整素材堆 → 2–3 候选开头 → 逐段生长，每块形式（段落/列表/表格/引用/代码块）需可辩护 | **补入 §3**：与 beats 二选一 |
| `grill-with-docs` | mattpocock / 已登记 `[FACT/LOCAL]` | 追问 sharpen 方案，**边问边产出 ADR 与 glossary**（内部调 `grilling` + `domain-modeling`） | **补入 §3**：外部 ADR Skill 的本机替代，优先用它 |
| `handoff` | mattpocock / 已登记 `[FACT/LOCAL]` | 会话交接文档；明确「不复制已存在于其他 artifact 的内容，用路径/URL 引用」——与宪法 #3「删冗余」一致 | **补入 §2.1** |
| `to-spec` / `to-tickets` / `triage` | mattpocock / 已登记（主理人编排 skill）`[FACT/LOCAL]` | 会话 → spec / tracer-bullet tickets / issue 状态机 | **已在用**，文档能力沿用 |
| `delivery-no-pseudoblock` | 自研 / **未挂接** `[FACT/LOCAL]` | **交付前 D1–D12 闸门**：责任转嫁、半途交付、最优步隐藏、伪阻断提问、尾巴豁免、自主闭环、工程严谨…要求「真加载 Skill 工具」才算过闸，禁止用声明替代扫描 | **补入 §6 分工**：它管**行为/责任**，§6.1 管**文风/文字**，两层串行、不互相替代 |
| `code-review` | mattpocock / 已登记 `[FACT/LOCAL]` | 审查而非写作 | 不纳入（云严已有） |

> ⚠️ **口径提醒（2026-11-09 逐项 frontmatter 实测，共 8 项）**：`writing-fragments` / `writing-beats` / `writing-shape` / `grill-with-docs` / `handoff` / `to-spec` / `to-tickets` / **`triage`** 均为 `disable-model-invocation: true`，**只能用户显式调用**，不会自动触发 `[FACT/LOCAL]`。可模型直调 3 项：`pr` / `writing-for-agents` / `delivery-no-pseudoblock`。**完整任务→skill 路由见 `doc-writing-playbook.md` §8**。

---

## 3. 外部候选审阅（只读，不安装）

### 3.1 纯文本 SKILL.md，无脚本 / 无 hooks —— 规则可借鉴

- **stop-slop** · `hardikpandya/stop-slop` · **MIT** `[FACT/UPSTREAM]`
  - 结构：`SKILL.md` + `references/{phrases,structures,examples}.md` + `README.md` + `LICENSE`；**无 scripts/、无 hooks/、无网络调用** `[FACT/UPSTREAM]`。
  - 规则：phrases 禁开场清嗓 / 强调拐杖 / 商务黑话 / 副词 / 模糊陈述 / 元评论；structures 禁二元对比、负面列举、戏剧化碎片、修辞铺垫、**false agency**（无生命物当主语，2026-03-17 commit `8da1f03` 新增）、被动；句规则禁 Wh- 开头、em dash、断奏碎片、绝对化词。
  - 自评分 5 维各 1–10（**Directness / Rhythm / Trust / Authenticity / Density**），**<35/50 改写** `[FACT/UPSTREAM]`。
  - 判定：**纳入规则借鉴**。安全面最干净，但按红线仍不安装。
- **humanizer** · `blader/humanizer` · **MIT** `[FACT/UPSTREAM]`
  - 基于 Wikipedia「Signs of AI Writing」。**模式数存在不一致**：README 明写「checks for **26 patterns**」，而 v2.11.0 的 PR #223 commit 描述写「keep all **35 patterns**」 `[FACT/UPSTREAM, 两处冲突]` → 引用时须写区间 / 标冲突，**不得写死某个数**。
  - 五个最强 tell：Not X but Y / 单行收尾金句 / 故作深刻 / 铺垫式开场 / 与无人争辩。
  - 流程：**标记所有 tell（按强度排序）→ 不把原结构当固定地起草改写 → 对照 patterns 与原 claims 复核 → 定稿**；展示「初稿 + 仍显人工的短评 + 终稿」。**不是**「跑一遍『明显 AI 生成』审计再重写一次」——上一轮表述有误，见 §5。
  - 声音匹配：贴 2–3 段本人文字，跟随其节奏 / 用词 / 标点 / 破折号习惯。
  - **Guard（与 ASD-STE100 高度一致）**：`It does not make things up.` 姓名 / 数字 / 日期 / 引文 / 引用必须来自原文或作者，缺细节就**问**而不是编；改文件时只动 prose，保留 code / data / frontmatter / link targets。
  - 安装需 `npx skills add` 或 plugin marketplace，Claude Code ≥ 2.1.142 `[FACT/UPSTREAM]`。
  - 判定：**纳入规则借鉴**，Guard 并入 §6.1 C 段。

### 3.2 带 hooks / 脚本 / 自动行为 —— 排除安装

- **asd-ste100 kit**（§1.2 核心，woosal1337/blog）· MIT（规范本身不含）`[FACT/UPSTREAM]`
  - 含 `install.py` + **4 个 hooks**（inject / refresh / pregate / gate）+ `ste-lint.py`，installer 会改 `~/.claude/settings.json` 并 symlink output style。
  - **本轮未安装、未执行 installer**。playbook §6.4 只登记命令并显式标注前提。→ **保留为规则源，不装**。
- **SimpleEnglish** · `AminBlg/SimpleEnglish` · **MIT** · 官方页显示 **4.3k stars** `[FACT/UPSTREAM]`
  - 同源 ASD-STE100，**53 条 Issue 9 规则**存于 `rule-catalog.md`，`SKILL.md` 两种 register 约 1,700 tokens；含 `src/hooks`（**Stop hook** / Codex hook）、`evals/ste_lint.py`、`tools/ste-dictionary`、`output-styles/`。
  - CI 用 `check_numbers.py` 校验所有已发布数字；2.1.1 专门清掉 skill 自身的 slop（自相矛盾的粗体引导 / em dash / "not X but Y" 标题）`[FACT/UPSTREAM]` —— 这个「规则自身也要过规则」的做法值得借鉴。
  - 判定：**排除安装**。理由：① 带 hooks + eval 脚本，触碰自动行为红线；② 与 §1.2 的 asd-ste100 **同源重复**；③ 未做本机代码审计。
- **open-steps** · `kharmanskyi/open-steps` v0.4.8 · **MIT** `[FACT/UPSTREAM]`
  - 8 个 `os-*` skill：`os-done-or-not`（一屏报告含 verdict）、`os-step-by-step`、`os-ask-simple`、`os-what-could-go-wrong`（premortem，**起全新 agent**）、`os-whats-next`、`os-check-work`、`os-say-simple`、`os-big-picture`。
  - ⚠️ **最高风险面**：**2 个 hooks**（`session-start.sh` 注入路由表 / `stop-report.sh` 会话结束要报告）、`doctor.sh`、census 脚本、`allowed-tools` 预授权、**`gh pr merge` 自动合并 PR**（README 明写 "The pack finishes finished work by itself"）、会往 `~/.claude/CLAUDE.md` 追加路由块、premortem 会 spawn 新 agent `[FACT/UPSTREAM]`。
  - 值得借鉴的思想：plain-language 报告结构（done or not / anything needed from you / new debt / safe to close）；**"Measured and assumed stay apart"**（yes 必须点名证据，未查就说 "not checked"）；坏消息单独一行不藏在段落中间。
  - 判定：**明确排除安装**（自动合并 + hooks + 改 CLAUDE.md + spawn agent，四条都碰用户红线）。仅借鉴报告结构，已并入 §6.1 C 段。

### 3.3 代码库文档类（备选，不安装）

> 源：`getclaudeskills.com` 2026-06-16 榜单 `[FACT/UPSTREAM]`；均为 `SKILL.md` 文件夹，用 `npx skills add <repo>/<path>` 安装。星数为**该榜单文章所载**，未在 GitHub 官方页复核 `[INFERENCE]`。

| skill | 上游 | 许可 | 脚本需求 | 输出物 / 备注 |
|---|---|---|---|---|
| Documentation & Modernization | `github/awesome-copilot` | MIT | 无 | 单一可验证架构文档；静态、非 living wiki |
| Acquire Codebase Knowledge | `github/awesome-copilot` | MIT | **Python + shell** | 7 份：`STACK`/`STRUCTURE`/`ARCHITECTURE`/`CONVENTIONS`/`INTEGRATIONS`/`TESTING`/`CONCERNS` |
| CLAUDE.md Improver | `anthropics/claude-plugins-official` | Apache-2.0 | **Node.js + shell** | 质量报告 + 定向更新，非整体重写 |
| ADR Skill | **`vercel/ai`** | **Apache-2.0** | **Node.js + shell** | Socratic 提问 → 四阶段；带 agent-readiness 校验 |
| Code Tour | `github/awesome-copilot` | MIT | Python + shell | `.tour` 文件，链接真实 file:line，20 种 persona |
| Update Skills & Instructions | `microsoft/vscode` | MIT | 无 | 「learn!」触发，沉淀可复用经验 |
| Write Coding Standards From File | `github/awesome-copilot` | MIT | 无 | 从真实代码反推编码规范 |
| Update Specification | `github/awesome-copilot` | MIT | 无 | 人 + agent 双可读的 living spec |
| Changelog Generator | `google-gemini/gemini-cli` | Apache-2.0 | Node.js | `latest.md`/`preview.md`/`index.md` |
| Azure Resource Visualizer | `github/awesome-copilot` | MIT | 无 | Mermaid 架构图（Azure 专用，本团场景不适用） |

> 上一轮漏登记的后 6 项已补齐。**原则**沿用：local-first > 凭空 plausible 散文；产出可核对 artifact > 开放散文；两个仓库级 mapping skill 别连着跑（会冗余）。

### 3.4 未拉源，登记为未核验

| skill | 仅有的信息 | 判定 |
|---|---|---|
| `no-ai-slop`（petergyang） | 第三方聚合站摘要：审计 / 最小改写，删具名 AI 模式不磨平声音；约 5.8K★ | `[GUESS/未核验]` 不引用星数、不当事实 |
| `unslop`（cursor/plugins） | 第三方聚合站摘要：Cursor 散文纪律，删 filler 与 AI tell；约 4.8K★ | `[GUESS/未核验]` 同上 |
| `document-superpowers`（binbinao） | 第三方聚合站摘要：四阶段（brainstorm → plan → execute → review） | `[GUESS/未核验]` 且本机已有 `writing-fragments/beats/shape` 可替代，降级为备选 |
| `iso-24495`（GaZmagik / danyuchn） | 第三方聚合站摘要：ISO 24495-1:2023 plain language，7 个 skill + output style | `[GUESS/未核验]` 受控语言另一参照系，仅登记 |

> `stop-slop` 15.2K★ / `humanizer` 33.7K★ 同样来自第三方聚合站，**未在官方 GitHub 页核验** `[GUESS/未核验]`；只有 `SimpleEnglish` 的 4.3k 在官方页可见 `[FACT/UPSTREAM]`。

---

## 4. 安全判定汇总（按用户长期规则）

| 等级 | 项 | 处置 |
|---|---|---|
| **A · 纯文本，无脚本/hooks** | `pr`、`writing-*`、`grill-with-docs`、`handoff`、`to-spec`、`to-tickets`、`delivery-no-pseudoblock`（本机已装）；`stop-slop`、`humanizer`（外部） | 本机已装可直接用；外部仅借鉴规则，**不安装** |
| **B · 带 hooks / linter / installer** | asd-ste100 kit、`SimpleEnglish` | **排除安装**；只留规则与命令，命令须标前提 |
| **C · 自动行为 / 高权限** | `open-steps`（自动 merge PR + 2 hooks + 改 CLAUDE.md + spawn agent） | **明确排除**，仅借鉴报告结构 |
| **D · 需脚本执行** | 代码库文档类 6 项（Python/Node/shell） | **备选用**，需先审计 + 单独同意 |
| **E · 未拉源** | §3.4 四项 | 只登记，不得当事实引用 |

**本轮实际动作**：只读扫描本机文件 + WebFetch 拉网页。**未安装、未克隆、未执行任何第三方脚本 / installer / hook，未改动 `~/.workbuddy/settings.json`**。

---

## 5. 上一轮 playbook 勘误记录

| # | 原表述（错） | 更正（本轮） | 依据 |
|---|---|---|---|
| 1 | humanizer「**33 个模式**」 | README 写 **26 patterns**，v2.11.0 PR #223 commit 写 **35 patterns**，两处冲突，引用须标区间 | `[FACT/UPSTREAM]` |
| 2 | humanizer「**二次审计**：改写后再跑『明显 AI 生成』审计再改一次」 | 实为「标记 tell → 起草改写 → 对照 patterns 与原 claims 复核 → 定稿」，且展示初稿+短评+终稿三段 | `[FACT/UPSTREAM]` |
| 3 | 「15.2K★ / 33.7K★ / 5.8K★ / 4.8K★」当作事实 | 全部来自第三方聚合站，**未在官方页核验**，降为 `[GUESS/未核验]`；仅 SimpleEnglish 4.3k 官方页可见 | `[INFERENCE]` |
| 4 | ADR Skill 未标来源 | 上游 **`vercel/ai`**，Apache-2.0，需 Node.js + shell | `[FACT/UPSTREAM]` |
| 5 | 只登记 4 个代码库文档 skill | 补齐榜单全部 10 项及各自上游/许可/脚本需求 | `[FACT/UPSTREAM]` |
| 6 | 完全未提本机已装的文档/写作 skill | 补 §2 共 11 项，含 `writing-fragments/beats/shape`、`writing-for-agents`、`delivery-no-pseudoblock` | `[FACT/LOCAL]` |
| 7 | 未评估 hooks / 自动行为风险 | 补 §3.2 / §4 安全分级，明确排除 `open-steps` 与 `SimpleEnglish` 安装 | 用户长期红线 |
| 8 | 「本机 11 项中 **4 项** `disable-model-invocation: true`」（§1）、§2 口径提醒列 **7 项**、口头表述「7 项」 | 2026-11-09 逐项实读 11 份 `SKILL.md` frontmatter：实为 **8 项**（漏 `triage`），可模型直调 3 项（`pr` / `writing-for-agents` / `delivery-no-pseudoblock`）；§1 / §2 / playbook §2.1 / 主理人 MD 四处同步更正，并新增 playbook §8 直调路由表 | `[FACT/LOCAL]` |

---

## 6. 与 §6 出稿自检清单的分工

| 层 | 管什么 | 工具 |
|---|---|---|
| **行为 / 责任** | 有没有把活推回用户、半途交付、最优步藏起来、尾巴豁免 | `delivery-no-pseudoblock` D1–D12（要求真加载 Skill 工具） |
| **文风 / 文字** | 句长、时态、被动、冠词、开场白、事实保留、门禁分数 | `doc-writing-playbook.md` §6.1（A/B/C/D 四段 32 项） |
| **结构 / 可触发性** | 长文 Grounding、agent 文档的 context pointer | `writing-fragments/beats/shape`、`writing-for-agents` |

三层**串行、不互相替代**：先过行为闸 → 再过文风清单 → 若产物是 skill/AGENTS.md 再加「指针措辞」检查。
