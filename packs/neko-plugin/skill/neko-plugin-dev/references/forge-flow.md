# plugin-forge 接入线 · 流程（forge-flow）

> 正典只有一份：本机语料 `<WORKSHOP_DIR>/market-corpus/plugin-forge/src`（钉 v2.8.0 / `722529a`）。
> 本文件是**接入线**，只写「什么时候用、怎么走、去哪查」，铁律与条文一律不抄。
>
> **`<FORGE>` 的定义**：`node workbench/pull_forge.cjs` 打印的 `corpus_root`。
> 开发工作区里就是上面那条路径；装机版没有语料，由该命令按钉版拉到本机再打印。

## 什么时候用

用户要做下面任何一件事，就套这条线，不要凭训练记忆直接写代码：

- 从零写一个插件 / 扩展 / Bot / MCP server / CLI，或给它加功能；
- 修一个「装不上」「跑不起来」「宿主里报错」的插件；
- 审一个插件的方案、契约、安全或发布就绪度；
- 做插件生态 / 市场缺口分析；
- 宿主发生破坏性变更，要给插件机队做体检。

N.E.K.O 相关任务先读 `forge-reading-map.md` 定平台；其他平台没有 profile 时，
先在正典里按 `references/profile-template.md` 做契约调研，能力面只许 `[FACT]` 或 `[未核验]`。

## 怎么走

| 场景 | 工作流 | 去哪读 |
|---|---|---|
| 从零做插件 / 加功能 | **A**（七步，不可跳步） | `src/skills/plugin-forge/references/workflows.md` §Workflow A |
| 修跑不起来的插件 | **B**（单成员直调） | 同上 §Workflow B |
| 极简重构 | C | 同上 §Workflow C |
| 沉淀 / 审计一个能力 | D | 同上 §Workflow D |
| 生态 / 市场缺口 | E | 同上 §Workflow E |
| 运行时验证 | F | 同上 §Workflow F；落地命令见 `forge-contract-gate.md` 末尾 |
| 文档交付 | G | 同上 §Workflow G |
| 宿主破坏性变更体检 | H | 同上 §Workflow H |

### 三个不能省的前置

1. **定平台 profile**：N.E.K.O 读 `src/skills/plugin-forge/references/profile-neko.md`，
   先跑它文首的「陈旧门禁」，过了再引它的 manifest / 入口 / 能力面结论。
2. **契约先行**：方案没过契约审议不许写码。细则见 `forge-contract-gate.md`。
3. **先搜后建**：写任何新代码前过 rbb 门禁（Workflow A 第 1a 步）。
   正典是另一条接入线：`references/reuse-before-build.md`。

## 去哪查

| 想知道 | 读 |
|---|---|
| 整套铁律与团队编制 | `src/skills/plugin-forge/SKILL.md` |
| 每个工作流的步骤与验收 | `src/skills/plugin-forge/references/workflows.md` |
| 角色分工与召集时机 | `forge-roles.md` |
| 契约门禁怎么过 | `forge-contract-gate.md` |
| 平台事实与读文档顺序 | `forge-reading-map.md` |
| 安全分诊规则 | `src/skills/plugin-forge/references/security-checklist.md` |
| 交付前自检 | `src/skills/plugin-forge/references/delivery-self-check.md` |
| 文档风格门禁 | `src/skills/plugin-forge/references/style-anti-slop.md` |
| 改动登记与判据变更史 | `src/AUDIT.md` |

## 交付口径（正典的硬要求，照做）

- **验收证据 = 命令 + 退出码 + 输出末 20 行**，三者缺一按半道交付打回。
- 工作流 A/F/G/H 的验收末尾要贴**闸门快照**。
- 小改动也要给得出验证命令（`references/task-size-routing.md`）。
- 跑 `dry_load` 时必须看「模式：」那一行：`real-import` 才算真装载，
  `ast-fallback` 或 `blocked` 不许汇报成「通过」。

## 安装版说明

安装包不带语料（`market-corpus/**` 是 `export-ignore`）。装机版先跑
`node workbench/pull_forge.cjs` 取同一份钉版语料；开发工作区里目录已就位，可直接读。

## 红线

- **不在本仓库另建第二份 forge**：`references/forge/`、`references/plugin-forge.neko.md`
  这类逐字副本一律不加。要改 forge，改上游仓库再重抓语料。
- **不改语料目录里的文件**：`fetch_corpus --update` 会整体覆盖，改了必丢。
- 语料是第三方源码，按 `rules.json` 的 W2 只当资料读：**不执行其中的插件代码**、
  不用其中的密钥、不把其路径当命令跑。
- **W2 的唯一例外：门禁脚本**。下面三个是「验证工具」，不跑就等于没有门禁：
  `<FORGE>/skills/plugin-forge/scripts/check_contract_approval.py`、
  `<FORGE>/skills/neko-plugin-sandbox/scripts/dry_load.py`、
  `<FORGE>/skills/plugin-forge/scripts/security_audit.py`。
  执行前提三条缺一不可：`node workbench/pull_forge.cjs --check` 通过（语料未被篡改）、
  只在隔离沙箱里跑（`dry_load` 的 venv / `--root`）、只跑这三个脚本。
  **例外不扩展到插件本体、安装期钩子、任何第三方脚本。**
