# plugin-forge 接入线 · 角色（forge-roles）

> 正典编制表在 `src/skills/plugin-forge/SKILL.md` §1，机器可读在 `src/roster.json`。
> 每个角色的完整职责在 `src/agents/plugin-forge-*.md`。本文件只写「什么时候召集谁、谁收货」。

## 编制：1 主理人 + 13 专家

| Agent | 名号 | 什么时候召集 |
|---|---|---|
| `plugin-forge-lead` | 交付总监（主理人） | **总是**：编排、分拣、汇编、收货 |
| `plugin-forge-contract` | 契约审议师 | 方案写好之后、写码之前（**门禁**，见 `forge-contract-gate.md`） |
| `plugin-forge-dev` | 插件开发工程师 | 起脚手架 / 实现 / 修故障 |
| `plugin-forge-tdd` | 测试驱动工程师 | 实现前写最小测试、实现后转绿 |
| `plugin-forge-architect` | 架构设计师 | 模块接口 / 架构图 / 深化扫描 |
| `plugin-forge-simplifier` | 极简工程师 | 实现后极简化、删冗余 |
| `plugin-forge-reviewer` | 代码审查师 | 提交前双轴审查 |
| `plugin-forge-planner` | 规划问诊师 | 需求含糊、方案需要 sharpen |
| `plugin-forge-skill-ops` | 能力运营师 | 需要新 skill / 审计第三方能力 |
| `plugin-forge-reliability` | 运维可靠性工程师 | CI/CD、发布就绪、故障响应 |
| `plugin-forge-security` | 安全工程师 | 交付前安全门禁（密钥 / 注入 / 依赖） |
| `plugin-forge-market-research` | 生态调研分析师 | 生态 / 市场缺口；Workflow A 第 1a 步（rbb 门禁） |
| `plugin-forge-sandbox` | 运行时验证工程师 | 「宿主里到底跑不跑」/ 交付前真实冒烟 |
| `plugin-forge-docs` | 文档工程师 | Workflow G 主力：README / 教程 / 变更说明 / PR 文本 |

## 常见组合（按工作流）

| 工作流 | 谁上 | 关键交接 |
|---|---|---|
| A 从零做插件 | lead → planner（如需）→ contract → market-research（1a）→ dev + tdd → simplifier → reviewer → security → sandbox → docs | contract 不过不许进 dev |
| B 修故障 | lead → dev → tdd → reviewer | 先拿 `check` 的结构化 issues 再动手 |
| C 极简重构 | lead → simplifier → reviewer | 只改结构不改行为，用测试证明 |
| E 生态分析 | lead → market-research → docs | 候选要给许可与最近提交日期 |
| F 运行时验证 | lead → sandbox | 必须逐字贴 `dry_load` 原生输出 |
| G 文档交付 | lead → docs → reviewer | 风格门禁见 `style-anti-slop.md` |
| H 破坏性变更 | lead → architect → reliability → docs | 先机队体检，再谈回帖 |

## 谁收货、什么情况打回

- **收货人只有 lead**。成员之间不互相验收。
- lead 打回的常见理由（正典 `workflows.md` 的「高频封驳原因」）：
  没有验收证据、证据里缺退出码或输出、把降级说成通过、契约 scope 与改动不符、
  改了但没写测试。
- sandbox 的回奏没有逐字贴 `dry_load` 原生输出 → 直接打回。
- 契约被驳回后不许继续写码，回去改方案再送审（正典 `workflows.md` 的「封驳重拟」）。

## 去哪查

| 想知道 | 读 |
|---|---|
| 编制与人数（唯一事实源） | `src/roster.json` |
| 编制表与铁律 | `src/skills/plugin-forge/SKILL.md` §1 |
| 某个角色的完整职责 | `src/agents/plugin-forge-<role>.md` |
| 工作流步骤与交接物 | `src/skills/plugin-forge/references/workflows.md` |
| 团队编制在本仓的镜像 | `packs/neko-plugin/skill/neko-plugin-dev/references/forge-pin.json` 的 `entry.roster` |

## 红线

- 角色表以 `roster.json` 为准，本文件是导读；数字对不上时以正典为准并回来改本文件。
- 不要把 14 个角色全塞进一次任务：按工作流取最小集合，lead 负责分拣。
