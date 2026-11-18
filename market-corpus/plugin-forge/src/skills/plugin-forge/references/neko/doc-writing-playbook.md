# 文档编写能力底座（总监专属）

> **用途**：本文件由总监（云朵 / 交付总监）主用，是专家团「编写文档」能力的经验集成。
> **两大核心 skill**：`pr`（PR / 变更说明结构）与 **ASD-STE100 反 slop 工具包**（woosal1337/blog，ep01-the-cure-for-ai-slop）。其余 skill 经搜索、审阅后整合进「反 slop 编辑工具箱」与「长文 / 代码库文档」两节。
> **证据分级**：`[FACT]`=可验证 / `[INFERENCE]`=推导 / `[GUESS]`=证据不足 / `未核验`=未联网。
> **核验日期**：2026-11-09（来源：本机已装 `pr` skill 的 SKILL.md；ASD-STE100 源文件 `SKILL.md` / `ste-recurring-errors.md` / `output-style.md`；其余 skill 来自 2026-11-09 WebSearch，未做宿主侧安装验证）。

---

## 0. 定位：总监为什么要有文档能力

- 总监是**最终交付物汇编者**（报告 / SOP / PR 描述 / skill 文档 / 变更说明）。交付物的「可读性与零 AI slop」由总监兜底，不能甩给 slop 生成。
- 本团宪法已有「先结论后步骤（#1）/ 删冗余（#3）/ 完整可运行交付（#8）/ 证据分级（#6）」——这与 ASD-STE100 Layer 2 高度重合；本底座把它们**工程化、可 lint、可复现**。
- **范围**（人读的文本，Layer 1 生效）：聊天回复、交付报告、README、commit / PR 文本、代码注释、错误信息、release note、工具说明、wiki。代码 / 标识符 / 命令语法 / 日志输出不在内。

---

## 1. 两大核心 skill

### 1.1 `pr` skill（Humanlayer / Dex Horthy，源 `show-me`）

> 源：`~/.workbuddy/skills/pr/SKILL.md`（本机已装 `[FACT/LOCAL]`）。

**用途**：PR 描述、变更说明，以及任何「我改了什么 + 证据 + 风险」的说明。

**结构模板**：

```markdown
## Summary
<diagram / diff-sketch / tree>

## Evidence
- **Before:** <截图 / 输出 / 失败测试 run>
  **After:** <截图 / 输出 / 通过测试 run>

## Merge Danger
**Door:** <one-way / two-way>
**Blast Radius:** <one-word>
```

**要点**：
- **Summary 用最小视图讲清关键点**。可视化手法（按话题选，不堆）：
  - 逻辑 / 算法 → 伪代码块
  - 运行时控制流 → 调用树（`submitForm → createSession → …`）
  - UI 结构 → 组件树（含 state / module 边界）
  - 文件职责 / 重构 → 浅文件树
  - 交互 / 数据流 → Mermaid 时序图
  - 变更点 → `diff`（四种形态：组件变更 / 文件布局 / 调用树 / 状态机）
- **Evidence**：截图是 S 级；执行证据（测试结果、控制台输出）是 A 级。给「之前失败、现在通过」的**具体测试**。
- **Merge Danger**：`one-way door`（破坏性 / 难回退）vs `two-way door`（易回退）；`Blast Radius` 写潜在影响范围（如 layout shift / 消费者 breakage / 移动端响应式）。
- 省略开场白，正文简短；用 `GLOSSARY.md` 的域语言。

### 1.2 ASD-STE100 反 slop 工具包（woosal1337/blog，ep01-the-cure-for-ai-slop）

> 源：`https://github.com/woosal1337/blog/tree/main/videos/ep01-the-cure-for-ai-slop`（`asd-ste100/` 技能目录，v2.0.5，规范 ASD-STE100 Issue 9）。规范免费：`https://asd-ste100.org`（EU 商标 No. 017966390，本技能非官方、未附属）。

**两层，永远同时开。**

#### Layer 1 — 用词（ASD-STE100 简化技术英语）
控制句子里放哪些词、多长。

- **短常用词**：`start`(非 begin/commence)、`use`(非 utilize/leverage)、`help`(非 facilitate)、`do`(非 perform/conduct)、`give`/`supply`(非 provide)、`before`(非 prior to)、`after`(非 subsequent to)、`about`(非 regarding)、`get`(非 obtain)、`show`(非 demonstrate)、`also`(非 additionally/furthermore/moreover)。
- **一词一义**；同一动作只用同一个词，不轮换 `check / verify / validate / confirm`。
- **主动语态**；仅简单时态（无现在完成时、无堆叠助动词）；用动词表动作（`analyze the log`，不 `perform an analysis of the log`）。
- **无短语动词**（`spin up` / `dive into` / `kick off` / `roll out`）；**无营销形容词**（`seamless` / `robust` / `powerful` / `cutting-edge` / `effortless` / `world-class` / `next-generation` / `revolutionary`）。
- 一句一指令（≤20 词），描述句 ≤25 词；一段一主题（≤6 句）；多词名词至多 3 词。
- 无分号（拆两句）；无缩写；保留冠词；美式拼写。
- **严格模式**（流程 / runbook / 安全文本 / 错误信息）：全套规则 + 严格词集——`but` 非 however、`because` 非 since、`can` 非 may、`must` 非 should/shall、`obey` 非 follow、`push` 非 press、`use`/`with` 非 using。
- **STE-flavored（默认，README / PR / 文档）**：放松 875 词字典锁与严格词集，保留句长 / 段落 / 时态 / 主动 / 名词簇 / 无短语动词纪律，使文本读起来自然。
- **39 个高频错误替换清单**见源 `references/ste-recurring-errors.md`；软件文档常中招：`ensure→make sure`、`however→but`、`since→because`、`may→can`、`should/shall→must`、`perform→do`、`using→use/with`、`check/test` 动词化时只用一个词。

#### Layer 2 — 结构（来自 ADHD 读者，非 STE 标准）
让回复「可用」而非仅「可读」。

- 下一动作 / 命令 / 路径放**第一行**，不是上下文。
- 多步任务**编号**，每步一个有限动作；动作列表**至多 5 项**（超了拆「现在做 / 以后做」）。
- **无开场白、无复述、无收尾**；答案说完即停。
- 每轮重述状态：`step N of M`、已完成、下一步。
- 时间估计用分钟 / 小时 / 天，不写「一会儿」；读者没要就别加估计。
- 错误就事论事：先因后果。
- **适用**：聊天回复、任务 / issue / PR / commit。**不适用**：README / 参考文档 / release note / 工具说明（保留主题所需结构）、博客 / 营销（需声音，两层都不用）。

#### Linter `ste-lint.py`
- 指标：violations per 100 words，**越低越干净**。
- 模式：`flavored`（通用散文）/ `--strict`（加 STE 词集、数 em dash）/ `--shape`（加 Layer 2 计数）。
- 标志：`--json`（机器可读）/ `--fail-over N`（超 N 退出 1，CI 门禁 / 预提交）。
- 门禁三档（ste-gate）：`<2.5` 无事；`2.5–4.0` 写警告到会话文件、下轮显示、不重发；`>4.0` 当轮一次阻断（lock 文件防重试叠加）。
- **实测证据**（6 写作任务 × 4 条件 × 2 模型，`[FACT/引用博客实验]`）：STE skill 把 slop 降 **74%**（sonnet 4.36→1.12）/ **50%**（gpt-5.5 3.54→1.76）；禁词表最不可靠（-3%~-40%）。**教训**：给模型一套写作系统，slop 减半以上；禁词表最不可靠。

#### 三种用法
`write`（新写）/ `rewrite`（转写，保事实）/ `review`（不改写，输出 `Rule | Original | Simplified` 表 + 一行留白说明）。

---

## 2. 相关 skill 全量审阅与整合

> **完整台账**：`references/doc-writing-skill-audit.md`（候选池、上游、许可、脚本/hooks、安全判定、纳入/排除理由、勘误记录）。
> **红线**：第三方 skill 安装前必须先安全审计；循环 / 自动行为须先查官方文档并取得单独同意。本轮**只做只读审阅，一个都没装、没执行任何第三方脚本或 hook**。
> **优先级**：本机已装（§2.1，零成本、无需审计）> 外部候选（§2.2，仅借鉴规则）。

### 2.1 本机已装（优先层，11 项）

> 依据：`references/skill-inventory.md`（68 个 skill 快照）+ 本机 `SKILL.md` 实读 `[FACT/LOCAL]`。⚠️ 标 `user-invoked` 的均为 `disable-model-invocation: true`，**只能用户显式调用，不会自动触发**；2026-11-09 逐项 frontmatter 实测共 **8 项**（`writing-fragments` / `writing-beats` / `writing-shape` / `grill-with-docs` / `handoff` / `to-spec` / `to-tickets` / `triage`），可模型直调 3 项。**完整任务→skill 路由见 §8**。

| skill | 状态 | 在文档能力里的位置 |
|---|---|---|
| `pr` | 已登记 | **核心之一**：PR 三段 + 6 种可视化（§1.1） |
| `writing-for-agents` | 已登记 | 写「给 agent 读的文档」：skill / `AGENTS.md` / `CLAUDE.md`；**context pointer** 措辞决定触发可靠性、context load vs cognitive load、one trigger per branch |
| `writing-fragments` | 未挂接 / user-invoked | **长文 explore**：追问采碎片，不承诺结构 |
| `writing-beats` | 未挂接 / user-invoked | **长文 exploit A**：beat 旅程 + **Grounding**（概念先落地才能被后续引用） |
| `writing-shape` | 未挂接 / user-invoked | **长文 exploit B**：逐段生长，每块形式需可辩护；与 beats 二选一 |
| `grill-with-docs` | 已登记 / user-invoked | 追问 + 边问边产出 **ADR 与 glossary**；外部 ADR Skill 的本机替代，优先用 |
| `handoff` | 已登记 / user-invoked | 会话交接；「不复制既有 artifact，用路径/URL 引用」与宪法 #3 一致 |
| `to-spec` / `to-tickets` / `triage` | 已登记 / user-invoked | 会话 → spec / tickets / issue 状态机，主理人编排已在用 |
| `delivery-no-pseudoblock` | 可选参考（来源与许可未确立，见 `AUDIT.md`） | 交付前行为闸门 D1–D12 的**参考清单**；必经口径是 plugin-forge SKILL §6 自己写的三条，与 §6.1 文风清单**分层串行** |

### 2.2 外部候选（只读审阅，不安装）

- **stop-slop** · `hardikpandya/stop-slop` · MIT `[FACT/UPSTREAM]`：`SKILL.md` + `references/{phrases,structures,examples}.md`，**无 scripts / 无 hooks / 无网络调用**。`phrases.md` 禁开场清嗓、强调拐杖、商务黑话、副词、模糊陈述、元评论；`structures.md` 禁二元对比、负面列举、戏剧化碎片、修辞铺垫、**false agency**（无生命物当主语）、被动；句规则禁 Wh- 开头、em dash、断奏碎片、绝对化词。自评分 5 维各 1–10（**Directness / Rhythm / Trust / Authenticity / Density**），**<35/50 改写**。**适合**：AI 草稿终稿润色。
- **humanizer** · `blader/humanizer` · MIT `[FACT/UPSTREAM]`：基于 Wikipedia「Signs of AI Writing」。⚠️ **模式数上游自相冲突**——README 写 **26 patterns**，v2.11.0 的 PR #223 commit 写 **35 patterns**，引用须标区间、不得写死。五个最强 tell：Not X but Y / 单行收尾金句 / 故作深刻 / 铺垫式开场 / 与无人争辩。流程为「标记全部 tell（按强度）→ 不把原结构当固定地起草改写 → 对照 patterns 与原 claims 复核 → 定稿」，展示初稿 + 短评 + 终稿。声音匹配：贴 2–3 段本人文字，跟随其节奏 / 用词 / 标点。**Guard（与 ASD-STE100 一致）**：不编造事实，姓名/数字/日期/引文必须来自来源，缺细节就问；改文件只动 prose，保留 code / data / frontmatter / link targets。
- **SimpleEnglish** · `AminBlg/SimpleEnglish` · MIT · 官方页 4.3k stars `[FACT/UPSTREAM]`：ASD-STE100 同源，**53 条 Issue 9 规则**在 `rule-catalog.md`，含 **Stop / Codex hooks**、`evals/ste_lint.py`、`output-styles/`。2.1.1 特意清掉 skill 自身违反自己规则的 slop（粗体引导 / em dash / "not X but Y" 标题）——「规则自身也要过规则」值得借鉴。**排除安装**：带 hooks + 脚本、与 §1.2 同源重复、未做本机审计。
- **open-steps** · `kharmanskyi/open-steps` v0.4.8 · MIT `[FACT/UPSTREAM]`：8 个 `os-*` plain-language 报告 skill。⚠️ **最高风险**：**2 个 hooks**、`doctor.sh`、`allowed-tools` 预授权、**`gh pr merge` 自动合并 PR**、会追加 `~/.claude/CLAUDE.md`、premortem 会 spawn 新 agent。**明确排除安装**（四条都碰用户红线）。仅借鉴：`done or not / anything needed from you / new debt / safe to close` 一屏报告结构；**"Measured and assumed stay apart"**（yes 必须点名证据，未查就写 "not checked"）；坏消息单独一行不藏在段落中间。
- **no-ai-slop**（petergyang）/ **unslop**（cursor/plugins）/ **iso-24495**（GaZmagik / danyuchn）/ **document-superpowers**（binbinao）：`[GUESS/未核验]` —— 仅第三方聚合站摘要，**未拉源**，星数不得当事实引用。前三者仅登记为受控语言 / 反 slop 备选；`document-superpowers` 的四阶段已由本机 `writing-fragments/beats/shape` 覆盖，降级备选。

> **勘误**：上一轮写的「humanizer 33 个模式」「二次审计」「15.2K★ / 33.7K★ 等星数」均已更正，逐条见台账 §5。

**选用组合（总监自定，可复现）**：
1. 起草 / 转写 → ASD-STE100（Layer1+2）打底；长文先走 `writing-fragments` → `writing-beats` 或 `writing-shape`；
2. 终稿润色 → stop-slop 自评分 + humanizer 声音匹配（仅借鉴规则，不装）；
3. PR / 变更说明 → 套 `pr` skill 三段结构 + 可视化；
4. 代码库文档 → 优先 `grill-with-docs` 出 ADR，其次走 §3 local-first 技能；
5. 门禁 → 行为闸（SKILL §6 三条，实测为准；`delivery-self-check.md` 为可选参考）→ 文风清单 §6.1 → `ste-lint.py --fail-over 2.5`（装了才跑）。

---

## 3. 长文与代码库文档

### 3.1 长文（本机三件套优先）

`writing-fragments`（explore：采碎片，不承诺结构）→ `writing-beats` 或 `writing-shape`（exploit：beat 旅程 / 逐段塑形）。二者共同的核心是 **Grounding**：概念必须先在读者已知或前文中落地，后续块才能引用它；单位**是概念不是词**，没有术语也可能踩空。`[FACT/LOCAL]`
> `document-superpowers`（binbinao，四阶段）`[GUESS/未核验]` 已被本机三件套覆盖，仅登记为备选。

### 3.2 代码库文档（local-first 备选池）

> 源：`getclaudeskills.com` 2026-06-16 榜单 `[FACT/UPSTREAM]`；均为 `SKILL.md` 文件夹，`npx skills add <repo>/<path>` 安装。星数未在官方页复核 `[INFERENCE]`。**本团一律不安装**，需要时走「克隆 → 审计 → 挂接」。

| skill | 上游 | 许可 | 脚本需求 | 输出物 |
|---|---|---|---|---|
| Documentation & Modernization | `github/awesome-copilot` | MIT | 无 | 单一可验证架构文档（静态，非 living wiki） |
| Acquire Codebase Knowledge | `github/awesome-copilot` | MIT | Python + shell | 7 份：`STACK`/`STRUCTURE`/`ARCHITECTURE`/`CONVENTIONS`/`INTEGRATIONS`/`TESTING`/`CONCERNS` |
| CLAUDE.md Improver | `anthropics/claude-plugins-official` | Apache-2.0 | Node.js + shell | 质量报告 + 定向更新，非整体重写 |
| **ADR Skill** | **`vercel/ai`** | **Apache-2.0** | **Node.js + shell** | Socratic 提问 → 四阶段，带 agent-readiness 校验 |
| Code Tour | `github/awesome-copilot` | MIT | Python + shell | `.tour` 文件，链接真实 file:line，20 种 persona |
| Update Skills & Instructions | `microsoft/vscode` | MIT | 无 | 「learn!」触发，沉淀可复用经验 |
| Write Coding Standards From File | `github/awesome-copilot` | MIT | 无 | 从真实代码反推编码规范 |
| Update Specification | `github/awesome-copilot` | MIT | 无 | 人 + agent 双可读的 living spec |
| Changelog Generator | `google-gemini/gemini-cli` | Apache-2.0 | Node.js | `latest.md`/`preview.md`/`index.md` |
| Azure Resource Visualizer | `github/awesome-copilot` | MIT | 无 | Mermaid 架构图（Azure 专用，本团不适用） |

**原则**：文档技能成败在「输出是否反映仓库当前真实状态」；强项都显式 local-first + 产出可核对 artifact（而非开放散文）。两个仓库级 mapping skill 别连着跑（输出冗余，选一个即可）。**ADR 优先用本机 `grill-with-docs`**，它在追问阶段就顺手产出 ADR 与 glossary。

---

## 4. 总监文档编写工作流（Workflow H）

- **Phase 1 定读者与类型**：交付报告 / PR·变更说明 / 长文 / 代码库文档 / 反 slop 润色。
- **Phase 2 起草**：
  - PR·变更 → `pr` skill 三段 + 可视化（伪代码 / 树 / diff / Mermaid）。
  - 长文 → 本机 `writing-fragments` → `writing-beats` / `writing-shape`（§3.1），用 Grounding 保证概念先落地。
  - 代码库文档 → 优先 `grill-with-docs` 出 ADR，备选走 §3.2 local-first 池。
  - 产物是 skill / `AGENTS.md` / `CLAUDE.md` → 加 `writing-for-agents` 的 context pointer 措辞检查。
  - 通用交付物 → ASD-STE100 Layer1+2 打底。
- **Phase 3 反 slop 润色**：ASD-STE100 → 借鉴 stop-slop 自评分与 humanizer 声音匹配（**仅借鉴规则，不安装**）。**Guard：绝不删事实 / 数字 / 条件 / 范围限定词凑长度**（如 `on macOS only` / `measured one time` / `n=6` 必须留）；缺细节就问，绝不编造。
- **Phase 4 门禁（三层串行）**：① 行为闸（SKILL §6 三条 + 台账 D1–D12 参考清单）→ ② §6.1 文风清单 → ③ 装了 kit 才跑 `ste-lint.py --fail-over 2.5`；PR 另走 `pr` 结构与证据分级。任一层不过回到对应阶段。
- **Phase 5 收口**：结论先行 + 证据分级（`[FACT]`/`[INFERENCE]`/`[GUESS]`/`未核验`）+ 完整可运行（命令 / 配置可复制）。

---

## 5. 与团队宪法的接口

- 本底座是宪法「先结论后步骤（#1）/ 删冗余（#3）/ 完整可运行交付（#8）/ 证据分级（#6）」的**文档侧工程化落地**，冲突时宪法优先。
- 反 slop 是**形式**修复，不替空洞段落补真；Guard 明令保留事实与限定词。
- **路由**：用户「写文档 / 报告 / PR / 反 AI slop 润色」→ 总监自处理；需沉淀为 skill / reference 时交 `neko-skill-ops`。

---

## 6. 交付出稿自检清单（可直接粘贴）

> **用法**：把 6.1 整段复制到草稿末尾，逐项打勾；任一项不通过，按 6.2 的修法改完再出稿。6.1 零依赖，任何环境可用。
> 规则来源：`pr` SKILL.md（本机已装 `[FACT/LOCAL]`）+ ASD-STE100 v2.0.5 `Verify` 段与 Issue 9 规则号（源 `[FACT/引用上游]`）。

### 6.1 可直接复制的清单

```markdown
## 出稿自检

### A. 结构（Layer 2 / pr）
- [ ] 第一行是下一动作、命令或路径，不是背景
- [ ] 首句没有「我将 / 下面介绍 / 在这篇文档里…」这类宣告
- [ ] 末句没有「还需要别的吗 / 以上就是…」这类收尾或复述
- [ ] 多步任务已编号，每步一个有限动作；动作清单 ≤5 项（超了拆「现在做 / 以后做」）
- [ ] 多轮任务标了 step N of M、已完成、下一步
- [ ] 时间估计用分钟 / 小时 / 天；没有「一会儿 / 一些工作」；读者没要就不加
- [ ] 旁支信息移到单独一句或单独问题，没有「顺便说一下」
- [ ] [PR] 三段齐全：Summary / Evidence / Merge Danger
- [ ] [PR] Summary 用了最小视图（伪代码 / 调用树 / 组件树 / 文件树 / Mermaid / diff 之一），没有全堆上
- [ ] [PR] Evidence 给了 Before → After 的具体测试或输出（截图 S 级 / 执行证据 A 级）
- [ ] [PR] Merge Danger 写了 one-way/two-way door + Blast Radius

### B. 用词（Layer 1，ASD-STE100）
- [ ] 指令 ≤20 词、描述句 ≤25 词；超了拆句
- [ ] 主动语态、仅简单时态；无现在完成时（has received）、无堆叠情态（may help to improve）
- [ ] 无分号、无缩写、无短语动词（spin up / kick off / dive into / roll out）
- [ ] 无营销形容词（seamless / robust / powerful / cutting-edge / world-class / next-generation）
- [ ] 用短常用词（start / use / make sure / do / give / show / before / about / get / also）
- [ ] 同一动作只用一个词，没有轮换 check / verify / validate / confirm
- [ ] 用动词表动作（analyze the log，不 perform an analysis of the log）
- [ ] 多词名词 ≤3 词；缩写首次定义
- [ ] 冠词与指示词在位（Remove the bolts from the panel，不 Remove bolts from panel）
- [ ] 一段一主题、≤6 句；相关句用 then / but / thus / as a result 连接
- [ ] [strict] 流程 / 错误信息 / 安全文本：but 非 however、because 非 since、can 非 may、must 非 should/shall、obey 非 follow、use/with 非 using
- [ ] [strict] 安全文本 WARNING/CAUTION/NOTE 用法正确，且紧贴在所保护步骤之前

### C. 事实与范围（Guard，最高优先）
- [ ] 没有为凑长度或条数删掉任何事实、数字、条件、单位、错误串、安全措辞
- [ ] 保留范围限定词（on macOS only / measured one time / n=6），没有把有界断言写成全称
- [ ] 代码标识符、部件号、单位、错误字符串原样保留
- [ ] 只改了违规的最小片段，没有顺手重写合规段落
- [ ] 每条结论有证据分级 [FACT] / [INFERENCE] / [GUESS] / 未核验
- [ ] 命令与配置完整可复制，没有「其余同理 / TODO 占位」

### D. 门禁
- [ ] 已跑 lint（或按 B 段逐项人工过一遍），分数 <2.5 / 百词
- [ ] 单独读第一行和最后一行：第一行说清「下一步做什么」，最后一行说清「刚发生了什么」
```

### 6.2 检查点 → 规则号 → 修法速查

**Layer 1（用词）**

| 检查点 | 规则号 | 不通过时的修法 |
|---|---|---|
| 长句 | 5.1 / 6.3 | 指令 >20 词、描述 >25 词 → 拆句 |
| 分号 | 8.1 | 改成两句 |
| 缩写 | 4.2 | 展开（don't → do not） |
| 现在完成时 / 情态堆叠 | 3.2 / 3.4 | 换简单时态：`we received` / `this improves X` |
| 被动（施动者已知） | 3.6 | 改主动：`the parser reads the file` |
| -ing 主动词 / 名词化 / 短语动词 | 3.5 / 3.7 / 9.3 | 换普通动词：`analyze the log` |
| 多词名词 ≥4 词 | 2.1 | 拆成「the handler that sets task-queue priority」或连字符 |
| 漏冠词 | 4.5 | 补 a / an / the / this / these |
| 一物两名 | 1.11 / 9.4 | 选一个名字全篇复用 |
| 标签被扩成句 | 4.5 vs Layer2 | 保持短标签，不要为加冠词扩成句 |
| 一段多主题 | 6.5 / 6.6 | 拆段，≤6 句 |
| 相关句断开 | 4.4 | 用 then / but / thus / as a result 连接 |

**Layer 2（结构）**：首行放动作 → 删宣告句 → 删收尾 → 旁支移走 → 估计具体化 → 动作清单 ≤5 → 多轮标 `step N of M`。与宪法 #1/#3/#8 一致，冲突时宪法优先。

**pr 三段**：`Summary`（最小视图 + 6 种可视化择一）→ `Evidence`（Before/After，S/A 级证据）→ `Merge Danger`（Door + Blast Radius）。省略开场白，用 `GLOSSARY.md` 域语言。

### 6.3 39 个高频错误速查（离线可用）

> 上排为软件文档最常中招的 10 条；全表来自源 `references/ste-recurring-errors.md`（ASD-STE100 Issue 9 字典引言）。`--strict` 只强制其中 7 条（however / since / may / should / shall / using / follow），flavored 模式放过。

| 不写 | 写 | 不写 | 写 | 不写 | 写 |
|---|---|---|---|---|---|
| ensure | MAKE SURE | however | BUT | therefore | THUS / AS A RESULT |
| since | BECAUSE | may | CAN | should / shall | MUST |
| perform | DO | using | USE / WITH | ensure | MAKE SURE |
| acceptable | PERMITTED | alternate | ALTERNATIVE | any | 换句式 |
| avoid | PREVENT | both | THE TWO | check(v) | do a CHECK(n) |
| cover(v) | COVER(n) | complete | COMPLETED | damage(v) | DAMAGE(n) |
| fit | INSTALL | follow | OBEY | further | MORE |
| have to | 祈使动作动词 | insert | PUT | main | PRIMARY |
| need | NECESSARY | now | AT THIS TIME | old | REMAINING / USED / EXPIRED |
| over | ABOVE / ON / ALONG | people | PERSON / PERSONNEL | portion | PART |
| press | PUSH | reach | GET | repeat | DO … AGAIN |
| required | NECESSARY | rotate | TURN | secure | ATTACH / SAFETY |
| test(v) | TEST(n) | under | BELOW / IN / LESS THAN | — | — |

### 6.4 门禁命令（可选，需先装 kit）

> ⚠️ **前提**：`asd-ste100` kit **本机未安装、未核验**（`[未核验]`）。下列命令来自源 `output-style.md`，仅在按 §1.2 装好 kit 后可执行；未装时一律以 6.1 人工清单代替，不假跑。

```bash
# flavored（通用散文，默认）
node "$HOME/.claude/skills/asd-ste100/hooks/run-python.cjs" \
  "$HOME/.claude/skills/asd-ste100/scripts/ste-lint.py" DRAFT.md

# strict（流程 / 错误信息 / 安全文本：加 STE 词集、数 em dash）
node "$HOME/.claude/skills/asd-ste100/hooks/run-python.cjs" \
  "$HOME/.claude/skills/asd-ste100/scripts/ste-lint.py" --strict DRAFT.md

# shape（加 Layer 2 计数）
node "$HOME/.claude/skills/asd-ste100/hooks/run-python.cjs" \
  "$HOME/.claude/skills/asd-ste100/scripts/ste-lint.py" --shape --json DRAFT.md

# CI / 预提交门禁：>2.5 分退出 1
node "$HOME/.claude/skills/asd-ste100/hooks/run-python.cjs" \
  "$HOME/.claude/skills/asd-ste100/scripts/ste-lint.py" --fail-over 2.5 DRAFT.md
```

门禁三档：`<2.5` 无事 / `2.5–4.0` 写警告到会话文件、下轮显示、不重发 / `>4.0` 当轮一次阻断（lock 文件防重试叠加）。**真修法在清单里：发送前先 lint 草稿，gate 就永不触发**。

---

## 7. 审阅台账与勘误索引

- **`references/doc-writing-skill-audit.md`**：本能力相关 skill 的**全量审阅台账**——本机已装 11 项、外部候选分 5 档（纯文本 / 带 hooks / 自动行为 / 需脚本 / 未拉源）、逐项许可与脚本需求、安全判定、纳入/备选/排除理由、以及 **8 处**事实错误的勘误记录（第 8 条为 user-invoked 数量口径）。
- **§8 文档任务路由**：本机 11 项 skill 的「任务 → skill → 调用方式 → 产出 → 收口」直调表，含长文两阶段、三层门禁串行顺序与总监直调纪律；主理人直调路由表的文档细分行即由此表派生。
- **引用纪律**：星数除 `SimpleEnglish` 4.3k（官方页可见）外，一律标 `[GUESS/未核验]`，不得当事实；`humanizer` 模式数 26 与 35 上游冲突，引用须标区间。
- **安装纪律**：本轮**只读审阅，零安装**。任何第三方 skill 真要用，走「克隆 → 审计 → 挂接」；带 hooks / 自动合并 / spawn agent 的（`open-steps`、`SimpleEnglish`、asd-ste100 installer）须先取得用户单独同意，默认不装。

---

## 8. 文档任务路由（本机 skill 直调表）

> **用途**：只回答「什么任务调哪个 skill、怎么调、产出什么、怎么收口」。规则正文见 §1（`pr` / ASD-STE100）、§3（长文与代码库文档）、§6（出稿自检清单）；审阅证据见 `references/doc-writing-skill-audit.md` §2。
> **依据**：2026-11-09 逐项实读 `~/.workbuddy/skills/*/SKILL.md` frontmatter `[FACT/LOCAL]`。
> ⚠️ **调用模式实测（11 项）**：`disable-model-invocation: true` 共 **8 项**，只能用户显式 `/skill` 调用、**模型不会自动触发**；可模型直调 3 项（`pr` / `writing-for-agents` / `delivery-no-pseudoblock`）。此前台账 §1 写「4 项」、§2 口径提醒列 7 项、口头表述「7 项」均错，已按实测更正（台账 §5 #8）。

### 8.1 路由表

| 任务（用户问法） | 调谁 | 调用方式 | 产出物 | 收口 |
|---|---|---|---|---|
| 写 / 改 PR 描述、变更说明 | `pr` | 模型直调 | `Summary` / `Evidence` / `Merge Danger` + 可视化 | §6.1 A 段 |
| 写 skill、改 `AGENTS.md` / `CLAUDE.md`、给 agent 读的文档 | `writing-for-agents` | 模型直调 | 可触发的 context pointer 文档 | §6.1 + 指针措辞复查 |
| 长文：还没素材，先聊出内容 | `writing-fragments` | **user-invoked** `/writing-fragments` | 一个 md 碎片堆（首个 H1 = 工作标题），**不承诺结构** | 交 §8.2 二选一 |
| 长文：已有素材堆，按叙事节拍成文 | `writing-beats` | **user-invoked** `/writing-beats` | beat 旅程，**Grounding**（概念先落地才能被引用） | §6.1 |
| 长文：已有素材堆，逐段塑形成文 | `writing-shape` | **user-invoked** `/writing-shape` | 逐段生长，每块形式需可辩护 | §6.1 |
| 方案追问、边问边定架构决策 / 术语表 | `grill-with-docs` | **user-invoked** `/grill-with-docs` | ADR + glossary（内部调 `grilling` + `domain-modeling`） | §6.1 |
| 把当前会话交接给下一个 agent | `handoff` | **user-invoked** `/handoff`（带 argument-hint） | 系统临时目录里的 handoff 文档，**不落工作区** | 用路径/URL 引用既有 artifact |
| 把讨论变成 spec | `to-spec` | **user-invoked** `/to-spec` | spec（发布到配置的 issue tracker） | 需 tracker + triage 词表 |
| 把 spec / 计划拆成 tracer-bullet tickets | `to-tickets` | **user-invoked** `/to-tickets` | 每票声明 blocking edges | 需 tracker 配置 |
| issue / 外部 PR 分诊、写 agent-ready brief | `triage` | **user-invoked** `/triage` | 状态机流转 + brief | 需 tracker 配置 |
| **任何交付物发出前的行为终检** | SKILL §6 三条（`delivery-no-pseudoblock` 为可选参考） | 模型直调，须真做实测而非声明 | D1–D12 参考清单 + 三条闸门快照（禁止声明替代扫描） | 与 §6.1 **串行，不互替** |

### 8.2 组合与顺序（不可互换）

1. **长文两阶段**：`writing-fragments`（explore，采碎片）→ `writing-beats` **或** `writing-shape`（exploit，二选一，不并行）。顺序颠倒或跳过 explore 会丢 Grounding。
2. **三层串行**：**行为/责任**（SKILL §6 三条为准，`delivery-no-pseudoblock` 是可选参考清单）→ 文风/结构 → 工具实测。任一层不过回到对应阶段，不得以下一层通过代上一层。
3. **门禁顺序**：行为闸 → 人工清单 → `ste-lint.py --fail-over 2.5`（**装了才跑，不假跑**；本机未装 asd-ste100 kit）。

### 8.3 总监直调纪律

1. **8 项 user-invoked 不会自动触发**：用户未显式点名时，总监**不得假装已调用**；需要时如实说明「该 skill 需显式调用」并给出对应 `/skill` 命令。
2. **路由只选 skill，不改规则正文**：规则出处固定 §1 / §6 / 台账，路由表不复制规则、不另立标准。
3. **第三方零安装**：`open-steps`（自动 `gh pr merge` + hooks + spawn agent）、`SimpleEnglish` / asd-ste100 installer（带 hooks）明确排除，理由见台账 §3.2 / §4。
4. **tracker 前置条件**：`to-spec` / `to-tickets` / `triage` 依赖 issue tracker 与 triage 标签词表；未配置时提示 `/setup-matt-pocock-skills` 或如实告知，**不得伪造「已发布」结果**。
