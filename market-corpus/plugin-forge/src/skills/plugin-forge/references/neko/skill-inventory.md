# Skill 全量清单基线表（本机实装快照）

> **核验日期：2026-11-06**（本次落盘前已重新执行只读扫描复核，非沿用旧值）
> **数据来源**：本机磁盘 `C:\Users\<user>\.workbuddy\skills\` 的 `ls -1` / `find -maxdepth 1 -printf` / 逐目录 `SKILL.md` 存在性检查；上游对照为本地克隆 `C:\Users\<user>\WorkBuddy\2026-11-02-21-48-31\mattpocock-skills`（**非联网**）；引用对照 `agents/neko-plugin-ops-team-lead.md` 与 `README.md`；来源线索 `HARDENING.md`、`.ef-manifest.json`、各 `SKILL.md`。
> **非网络推测**：本表不含任何联网抓取结果；上游仓库 2026-11-06 的当前状态**未核验**。
> **证据分级**：`[FACT/LOCAL]` = 2026-11-06 本机只读扫描可直接验证 / `[INFERENCE]` = 由命名、关键词、团队文档归类推导 / `[GUESS]` = 证据不足仅登记 / `未核验` = 未联网或未做宿主侧验证。
> **配套文档**：`references/mattpocock-skills.md`（上游权威）、`references/dev-agent-ecosystem.md`（候选源索引）、`references/safe-dev-capabilities.md`（安全子集）。

## 0. 口径定义

| 项 | 定义 |
|---|---|
| 本机已装 | `C:\Users\<user>\.workbuddy\skills\<name>\SKILL.md` 存在。**磁盘存在 ≠ 宿主已加载为可用 skill**，见 §2.5-4 |
| 被 team-lead.md 引用 | 在 `agents/neko-plugin-ops-team-lead.md`（主理人文档）或 `README.md` 的「团队构成 / 主理人编排 skill / 信息检索与工具顺序」中以 skill 名出现 |
| 来源/归属 | 以本机文件与本地克隆的**可核验证据**判定；无证据一律写「未知（未核验）」，不臆测 |
| 项目级 skills | **不存在**（本次实测未发现项目级 skills 目录）[FACT/LOCAL] |

### 0.1 口径对齐（与会话启动能力刷新体系）

- 本表是「会话启动能力刷新」的 **skill 面基线**，口径遵循 `references/capability-baseline.md`（能力总基线）与 `references/session-refresh-sop.md`（刷新 SOP）：一律采用「**快照日期 + 复核日期 + 口径保护**」写法，绝对数字不得脱离日期单独引用。[FACT/LOCAL]
- **对齐状态（2026-11-06 二次复核）**：两份口径文档**均于 2026-11-06 已落盘**（云稳 neko-reliability）。交叉引用定义：`references/capability-baseline.md` = **12 个检测点的指纹基线**（现状清单 + 变更检测清单 + 指纹块）；`references/session-refresh-sop.md` = **会话启动能力刷新 SOP**（逐项比对与幂等输出约定）。已交叉核对：`capability-baseline.md` §1 第 59 行与 §2 检测点 #9 均记 `~/.workbuddy/skills/` **68 个目录**、§4 指纹块记 `skills=68 (fp 0c0c53fd5b3e)`，并注明「明细清单由 neko-skill-ops 维护，本表仅记锚点」，与本表实装合计 68 一致；`session-refresh-sop.md` L97 以该基线的 12 个检测点为比对范围，其中 #9 即 skills 目录集合。两文档口径与本表**一致，无需回改**；完整回查见 §3.1。[FACT/LOCAL]
- **口径保护（三条不得互相代换）**：「上游 39」≠「本机实装 38」≠「已挂接 25」；「未挂接」≠「未安装」；`safe-dev-capabilities.md` 的 A 级 2 / B 级 31 是**外部候选源登记引用**，不得与本机实装数相加。[FACT/LOCAL]

**本次复核实测总量 [FACT/LOCAL]**：顶层 **71** 个条目 = **68 个 skill 目录（68/68 全部含 `SKILL.md`）** + 1 个文档 `HARDENING.md` + 3 个隐藏配置文件（`.ef-manifest.json`、`.disable_to_model_invocation_migration.json`、`.model_invocation_to_override_migration.json`）。`ls -1` 默认不显示点文件，故可见清单为 **68 行**（68 + HARDENING.md）。

**归属判定方法 [FACT/LOCAL]**：以本地克隆内 39 个 `SKILL.md` 所在目录名与本机 68 个目录做集合交集，得 38 项精确匹配（mattpocock）；`software-factory` 为唯一「克隆有 / 本机无」项；其余 29 项为「本机有 / 克隆无」。各 `SKILL.md` 内出现的 `github.com/...` 链接多为内容引用或工具引用（如 `pr` 内含 `humanlayer/skills`、`setup-ts-deep-modules` 内含 `sverweij/dependency-cruiser`），**不等同于该 skill 的来源**；来源一律以集合交集与团队文档归类为准。[INFERENCE]

## 1. 全量基线表

> 前 68 行为本机实装；末 6 行为文档 / 主理人提及但本机用户级目录未实装（含宿主内置、插件缓存、MCP 等非用户级类型，已在来源列标明）。

| skill 名称 | 本机是否已装 | 是否被 team-lead.md 引用 | 来源/归属 | 证据等级 |
|---|---|---|---|---|
| archify | 是 | 是 | 第三方 / `tt-a1i/archify`（dev-agent-ecosystem §2、safe-dev §4） | [FACT/LOCAL] |
| ask-matt | 是 | 是 | mattpocock / engineering·user-invoked | [FACT/LOCAL] |
| chief-of-staff | 是 | 否 | mattpocock / in-progress | [FACT/LOCAL] |
| claude-handoff | 是 | 否 | mattpocock / in-progress | [FACT/LOCAL] |
| code-review | 是 | 是 | mattpocock / engineering·model-invoked | [FACT/LOCAL] |
| codebase-design | 是 | 是 | mattpocock / engineering·model-invoked | [FACT/LOCAL] |
| delivery-no-pseudoblock | 是 | 否 | 未知（未核验） | [GUESS] |
| diagnosing-bugs | 是 | 是 | mattpocock / engineering·model-invoked | [FACT/LOCAL] |
| docs-site-to-mcp | 是 | 否 | 自研 / NEKO 本地（SKILL.md 含 NEKO 关键词） | [INFERENCE] |
| domain-modeling | 是 | 是 | mattpocock / engineering·model-invoked | [FACT/LOCAL] |
| dsh-plugin-web-lab | 是 | 否 | 未知（未核验） | [GUESS] |
| ef-broadcast | 是 | 否 | 第三方 / EigenFlux `phronesis-io/eigenflux`（官方签名件 + 本地安全改造） | [FACT/LOCAL] |
| ef-communication | 是 | 否 | 第三方 / EigenFlux（同上；SKILL.md 含 eigenflux 链接） | [FACT/LOCAL] |
| ef-onboarding | 是 | 否 | 第三方 / EigenFlux（同上） | [FACT/LOCAL] |
| ef-profile | 是 | 否 | 第三方 / EigenFlux（同上） | [FACT/LOCAL] |
| git-guardrails-claude-code | 是 | 否 | mattpocock / misc | [FACT/LOCAL] |
| global-biblio-base | 是 | 否 | 未知（未核验） | [GUESS] |
| grill-me | 是 | 是 | mattpocock / productivity·user-invoked | [FACT/LOCAL] |
| grill-with-docs | 是 | 是 | mattpocock / engineering·user-invoked | [FACT/LOCAL] |
| grilling | 是 | 是 | mattpocock / productivity·model-invoked | [FACT/LOCAL] |
| haloflux-lab | 是 | 否 | 未知（未核验） | [GUESS] |
| handoff | 是 | 是 | mattpocock / productivity·user-invoked | [FACT/LOCAL] |
| hindsight-coding-agent | 是 | 否 | 未知（未核验） | [GUESS] |
| ima-skills | 是 | 否 | 第三方（与宿主插件同名，上游未核验） | [INFERENCE] |
| implement | 是 | 是 | mattpocock / engineering·user-invoked | [FACT/LOCAL] |
| implement-spec | 是 | 否 | mattpocock / engineering·user-invoked（文档标「暂不纳入」） | [FACT/LOCAL] |
| improve-codebase-architecture | 是 | 是 | mattpocock / engineering·user-invoked | [FACT/LOCAL] |
| lexiang-knowledge-base | 是 | 否 | 第三方（与宿主插件同名，上游未核验） | [INFERENCE] |
| llm-wiki | 是 | 否 | 未知（未核验） | [GUESS] |
| loop-me | 是 | 否 | mattpocock / in-progress | [FACT/LOCAL] |
| markitdown-skill | 是 | 否 | 第三方 / `microsoft/markitdown`（SKILL.md 内链接） | [INFERENCE] |
| migrate-to-shoehorn | 是 | 否 | mattpocock / misc | [FACT/LOCAL] |
| neko-agent-bridge | 是 | 否 | 自研 / NEKO 本地（SKILL.md 含 `oraios/serena` 链接） | [INFERENCE] |
| neko-bridge | 是 | 否 | 自研 / NEKO 本地 | [INFERENCE] |
| neko-plugin-sdk | 是 | 是 | 自研 / NEKO 本地 SDK（团队文档登记） | [FACT/LOCAL] |
| obsidian | 是 | 否 | 第三方（与宿主插件同名，上游未核验） | [INFERENCE] |
| ponytail | 是 | 是 | 第三方 / `DietrichGebert/ponytail`（safe-dev §4） | [FACT/LOCAL] |
| ponytail-audit | 是 | 否 | 第三方 / ponytail 衍生 | [INFERENCE] |
| ponytail-debt | 是 | 否 | 第三方 / ponytail 衍生 | [INFERENCE] |
| ponytail-gain | 是 | 否 | 第三方 / ponytail 衍生 | [INFERENCE] |
| ponytail-help | 是 | 否 | 第三方 / ponytail 衍生（SKILL.md 含 `DietrichGebert/ponytail`） | [FACT/LOCAL] |
| ponytail-review | 是 | 否 | 第三方 / ponytail 衍生 | [INFERENCE] |
| pr | 是 | 是 | mattpocock / engineering·model-invoked | [FACT/LOCAL] |
| prototype | 是 | 是 | mattpocock / engineering·model-invoked | [FACT/LOCAL] |
| research | 是 | 是 | mattpocock / engineering·model-invoked | [FACT/LOCAL] |
| retro | 是 | 是 | mattpocock / engineering·user-invoked | [FACT/LOCAL] |
| reverse-engineer-anything | 是 | 否 | 第三方 / rea 相关（SKILL.md 含 `morluto/rea`） | [INFERENCE] |
| scaffold-exercises | 是 | 否 | mattpocock / misc | [FACT/LOCAL] |
| setup-matt-pocock-skills | 是 | 是 | mattpocock / engineering·user-invoked | [FACT/LOCAL] |
| setup-pre-commit | 是 | 否 | mattpocock / misc | [FACT/LOCAL] |
| setup-ts-deep-modules | 是 | 否 | mattpocock / in-progress | [FACT/LOCAL] |
| simplify-codebase | 是 | 是 | 第三方（团队文档列为「仓库外来源」，具体上游未标） | [FACT/LOCAL] |
| skill-supply-chain-hardening | 是 | 是 | 自研 / 团队能力（mattpocock-skills §3 列为仓库外来源） | [INFERENCE] |
| tdd | 是 | 是 | mattpocock / engineering·model-invoked | [FACT/LOCAL] |
| teach | 是 | 否 | mattpocock / productivity·user-invoked（文档标「暂不纳入」） | [FACT/LOCAL] |
| to-questionnaire | 是 | 是 | mattpocock / productivity·user-invoked | [FACT/LOCAL] |
| to-spec | 是 | 是 | mattpocock / engineering·user-invoked | [FACT/LOCAL] |
| to-tickets | 是 | 是 | mattpocock / engineering·user-invoked | [FACT/LOCAL] |
| triage | 是 | 是 | mattpocock / engineering·user-invoked | [FACT/LOCAL] |
| wait-what | 是 | 是 | mattpocock / productivity·user-invoked | [FACT/LOCAL] |
| wayfinder | 是 | 是 | mattpocock / engineering·user-invoked | [FACT/LOCAL] |
| wizard | 是 | 是 | mattpocock / engineering·model-invoked | [FACT/LOCAL] |
| wps-knowledgebase | 是 | 否 | 第三方（与宿主插件同名，上游未核验） | [INFERENCE] |
| writing-beats | 是 | 否 | mattpocock / in-progress | [FACT/LOCAL] |
| writing-for-agents | 是 | 是 | mattpocock / productivity·model-invoked | [FACT/LOCAL] |
| writing-fragments | 是 | 否 | mattpocock / in-progress | [FACT/LOCAL] |
| writing-shape | 是 | 否 | mattpocock / in-progress | [FACT/LOCAL] |
| software-factory | 否（克隆内 `skills/in-progress/software-factory` 存在，本机无） | 否 | mattpocock / in-progress | [FACT/LOCAL] |
| skill-creator | 否（用户级目录无；宿主内置 skill 存在） | 是 | 宿主内置 / workbuddy-builtin（非用户级） | [FACT/LOCAL] |
| find-skills | 否（用户级目录无；插件缓存 skill 存在） | 是 | 宿主插件 / codebuddy-plugins-official（非用户级） | [FACT/LOCAL] |
| security-audit | **是**（2026-11-06 安装，20 文件，上游 HEAD `c1c8a8c` 与审计 commit 一致） | 是 | 第三方 / Cloudflare（`references/third-party-skill-audit.md` 审计通过） | [FACT/LOCAL] |
| rea | 否（非 skill 目录，MCP 工具面；工作区有 `rea` 克隆） | 是 | MCP 工具面 / `morluto/rea` | [FACT/LOCAL] |
| build-with-exa | 否（用户级目录无） | 是（以 `npx skills use ... --skill "build-with-exa"` 形式） | 第三方 / Exa（外部 skill，未安装） | [INFERENCE] |

## 2. 差异汇总（2026-11-06 复核值）

### 2.1 文档声称数 vs 实装数

| 口径 | 数量 | 说明 |
|---|---|---|
| mattpocock 上游 skill（2026-10-31 commit 快照） | 39 | 克隆内实测 39 个 `SKILL.md`；数字准确 [FACT/LOCAL] |
| 本机实装的 mattpocock skill | **38 / 39** | 集合交集实测；唯一缺口 `software-factory` [FACT/LOCAL] |
| 本机非 mattpocock skill | 29 | 第三方 / 自研 / 未知，分类见 §2.6 [FACT/LOCAL + INFERENCE] |
| **本机 skill 实装合计** | **68** | 68/68 全部含 `SKILL.md`；顶层 72 条目另含 1 文档 + 3 隐藏 json [FACT/LOCAL] |
| team-lead / README 已登记且本机已装 | 30 | = 25 mattpocock 挂接 + 5 仓库外来源 [FACT/LOCAL] |
| **已装未登记** | **37** | = 13 mattpocock 已装未挂接 + 24 非 mattpocock [FACT/LOCAL] |
| 被引用但不在用户级目录 | **6** | 见 §2.3 [FACT/LOCAL] |
| safe-dev A / B 级（外部候选源，非本机 skill） | 2 / 31 | B=14+6+11 自洽；**不可与本机 68 相加** [FACT/LOCAL] |

### 2.2 未被登记的已装 skill（37 个）

- **mattpocock 系（13）**：`chief-of-staff`、`claude-handoff`、`git-guardrails-claude-code`、`implement-spec`、`loop-me`、`migrate-to-shoehorn`、`scaffold-exercises`、`setup-pre-commit`、`setup-ts-deep-modules`、`teach`、`writing-beats`、`writing-fragments`、`writing-shape`
  > 恰为 `mattpocock-skills.md` §3「仍未挂接 14 个」中除 `software-factory` 外的 13 个。**「未挂接」≠「未安装」**，这 13 个均在本机 [FACT/LOCAL]。
- **非 mattpocock 系（24）**：`delivery-no-pseudoblock`、`docs-site-to-mcp`、`dsh-plugin-web-lab`、`ef-broadcast`、`ef-communication`、`ef-onboarding`、`ef-profile`、`global-biblio-base`、`haloflux-lab`、`hindsight-coding-agent`、`ima-skills`、`lexiang-knowledge-base`、`llm-wiki`、`markitdown-skill`、`neko-agent-bridge`、`neko-bridge`、`obsidian`、`ponytail-audit`、`ponytail-debt`、`ponytail-gain`、`ponytail-help`、`ponytail-review`、`reverse-engineer-anything`、`wps-knowledgebase`

### 2.3 被引用但不在用户级目录的 6 项

`software-factory`（上游克隆有 / 本机无）、`skill-creator`（宿主内置）、`find-skills`（插件缓存）、`security-audit`（**已于 2026-11-06 安装到用户级**）、`rea`（MCP 工具面，非 skill）、`build-with-exa`（外部 skill，未安装）。
> 口径提示 [FACT/LOCAL]：`skill-creator` / `find-skills` 在宿主 `<available_skills>` 中存在，**只是不在用户级 skills 目录**；不应判为「缺失」。`rea` 按团队文档本就不是用户级 skill（MCP 工具面），属类型差异而非缺失；`security-audit` 已于 2026-11-06 安装到用户级，不再是缺口。

### 2.4 已登记且已装的 30 个（便于核对）

`archify`、`ask-matt`、`code-review`、`codebase-design`、`diagnosing-bugs`、`domain-modeling`、`grill-me`、`grill-with-docs`、`grilling`、`handoff`、`implement`、`improve-codebase-architecture`、`neko-plugin-sdk`、`ponytail`、`pr`、`prototype`、`research`、`retro`、`setup-matt-pocock-skills`、`simplify-codebase`、`skill-supply-chain-hardening`、`tdd`、`to-questionnaire`、`to-spec`、`to-tickets`、`triage`、`wait-what`、`wayfinder`、`wizard`、`writing-for-agents`

### 2.5 已废弃 / 需复核条目

1. **已废弃：无** [FACT/LOCAL]。mattpocock `deprecated/` bucket 为 0，本机无 deprecated 目录。
2. **供应链复核点 —— 已由云安（neko-security）复核完毕，本表不自行下结论** [FACT/LOCAL]：2026-11-06 只读扫描，`~/.workbuddy/skills/.ef-manifest.json` 实测字段为 `sequence: 0`、`key_id: ""`、`signature: ""`、`revision: "a1c730980c9392e5"`、`cli_version: "0.0.50"`、`managed_by: "eigenflux-cli"`，列 `ef-broadcast` / `ef-communication` / `ef-onboarding` / `ef-profile` 4 个 skill 及各自 sha256；同目录 `HARDENING.md` 第 110 行记录的锚点为 `sequence 27` / `revision fd90aedc78c4e0ab` / `key_id 28cf75683b196247` / `cli_version 0.0.50`。**两组值不一致，且当前 manifest 的 `signature` 与 `key_id` 为空字符串**。以上仅为字段实测登记，**不对签名有效性、是否被篡改、是否可安全使用作任何判断**。
   > **交叉核对结论（见 `references/security-refresh-notes.md` §11，云安 2026-11-06）**：[VALUE JUDGMENT] **不阻断本次汇编** —— 空 `signature` / `key_id` 与 `HARDENING.md` 锚点不一致属**离线 bundle 安装的预期形态**（`HARDENING.md §5` 记载 `eigenflux skills install --from-bundle`，四处宿主 manifest 完全相同），10 个加固文件横幅全部仍在位；但 [FACT] 本地清单**无密码学完整性保证**（签名字段为空、4 条 sha256 为自声明未签名），**不得作为可信基线**；`HARDENING.md` 锚点已过期（`~/.agents` 签名 manifest 已推进至 `sequence 29` / `cli 0.0.51`）。处置：4 个 `ef-*` 技能与 manifest / `HARDENING.md` 列入**只读监控例外**，比对字段变化与横幅是否存在（T5 / T6），**禁止任何 Act 触碰**（尤其 `eigenflux skills sync` 会覆盖加固文件）。本表只登记字段、转述该结论，不扩展判断。
3. **维护失效项** [FACT/LOCAL]：`HARDENING.md` §4 明确 `reapply-hardening.py 已丢失`，且 `eigenflux skills sync` 会覆盖安全改造。台账抬头写的位置是 `C:\Users\<user>\.agents\skills\HARDENING.md`，本机副本在 `.workbuddy\skills\`（§5 多宿主分发已说明，非错误，建议标注）。
4. **宿主加载口径差异** [INFERENCE / 未核验]：宿主 `<available_skills>` 中用户级目录段落列出约 44 项，磁盘有 68 项，约 23 项未出现在宿主可用清单（集中在 mattpocock 的 user-invoked / in-progress 系）。同目录两个迁移标记文件均为 `scanned: 7 / migrated: 0 / skipped: 7 / failed: 0`。疑与 `disable-model-invocation` 及迁移标记有关，**未做宿主侧验证**；本表「已装」仅指磁盘存在 `SKILL.md`。
5. **ponytail 衍生 5 项未登记** [FACT/LOCAL]：`ponytail-audit` / `-debt` / `-gain` / `-help` / `-review` 存在于本机，三份既有文档此前均未提及（仅登记 `ponytail` 主项）。

### 2.6 来源归属未核验清单

- 标 [GUESS]「未知（未核验）」6 项：`delivery-no-pseudoblock`、`dsh-plugin-web-lab`、`global-biblio-base`、`haloflux-lab`、`hindsight-coding-agent`、`llm-wiki`；另有 `docs-site-to-mcp` 依 NEKO 关键词标 [INFERENCE]。
- 标 [INFERENCE] 的 15 项均仅依据命名前缀、SKILL.md 关键词或团队文档归类，**未逐一比对上游仓库**。
- 所有上游仓库 2026-11-06 的当前状态、许可、规模数字均为**未核验**。

## 3. 基线维护约定

1. 本表是 **2026-11-06 本机快照**，与三份「上游 / 候选源」文档口径不同，引用时须注明口径与日期。
2. 新增 / 删除 skill 后重跑 `ls -1` + 逐目录 `SKILL.md` 检查 + 集合交集，并更新本表与 §2 差异汇总；会话启动时按 `references/session-refresh-sop.md` 的 12 个检测点刷新（其中 #9 为 `~/.workbuddy/skills/` 目录集合），明细以本表为准。
3. 「来源/归属」列中 [GUESS] / [INFERENCE] 项需补上游证据后方可升为 [FACT/LOCAL]。
4. §2.5-2 供应链复核点**已由 `neko-security` 交叉核对完毕**，结论见 `references/security-refresh-notes.md` §11；对外引用以该文档为准，本表不自行下结论、不扩展判断。
5. `capability-baseline.md` / `session-refresh-sop.md` 均于 2026-11-06 已落盘；按本条约定完成口径回查（见 §0.1 与 §3.1），两文档与本表一致。

### 3.1 口径回查结论（2026-11-06，按 §3 第 5 条执行）

| 回查项 | 本表口径 | 两份文档口径 | 结论 |
|---|---|---|---|
| skills 数量 | 68 个目录（68/68 含 `SKILL.md`） | `capability-baseline.md` §1 L59 / §2 检测点 #9 = **68**；§4 指纹块 `skills=68 (fp 0c0c53fd5b3e)` | **一致** [FACT/LOCAL] |
| skills 指纹 | 本表为明细清单，不自行计算指纹 | `skills_fp = 0c0c53fd5b3e`；云稳 `--regen` 三条验证命令 exit 0（**由 team-lead 通报，本次未自行复跑、未触碰 `scripts/`**） | **一致** [FACT/LOCAL + 通报] |
| 已装未登记 37 | 13 mattpocock + 24 非 mattpocock，见 §2.2 | 两份文档统计的是本机配置 / 清单检测点，不作成员映射口径统计，未出现冲突数字 | **不冲突** [FACT/LOCAL] |
| A 级 2 / B 级 31 不得与 68 相加 | §0.1 口径保护第 3 条 | `capability-baseline.md` 12 个检测点均为本机配置 / 目录 / 运行时项，不含外部候选源登记引用 | **不冲突** [FACT/LOCAL] |
| 幂等 | 本表仅在实装清单变化时更新 | `capability-baseline.md` L133「**有差异才改文件，无差异只报『基线一致，最后核验 2026-11-06』**」；`session-refresh-sop.md` §3.2 L123 无差异零写入、不刷新时间戳 | **不冲突** [FACT/LOCAL] |
| 落盘物不计为差异 | 本表属 `references/*.md` | `capability-baseline.md` L80 / `session-refresh-sop.md` L117：新增 `references/*.md` 与 `scripts/` 不在 12 个检测点内、不触发误报 | **不冲突** [FACT/LOCAL] |

**结论**：6 项口径回查**全部一致 / 不冲突**，**未发现任何冲突**，故未改动基线（`capability-baseline.md` / `session-refresh-sop.md` 全程只读、未写入）；本次仅修正本表与三份既有文档的状态标注。若后续发现冲突，先报主理人、不自行改基线。[FACT/LOCAL]
