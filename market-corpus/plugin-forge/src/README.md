# plugin-forge —— 插件制造专家团

一个团队，一个插件。1 主理人 + 13 专家，为任意宿主平台（N.E.K.O / VSCode / Chrome / Obsidian / MCP / Bot / CLI…）设计、实现、测试、审查、加固并验证可安装运行的插件。

原 neko-plugin-ops（12 角色）与 neko-plugin-court（朝议流水线）已并入：角色由 plugin-forge 14 人编制承接；两个团队的 NEKO 平台资产（NEKO 笔记一批：调研笔记加 1 份归档的外部提示词，逐份范围说明见 `references/neko/neko-sources.md` 的「目录范围」节；运行时沙箱 skill；会话能力刷新脚本）降级为 N.E.K.O profile 的扩展阅读，保留全部功能。历史在 git log（tag 前最后一次三团队提交）。

N.E.K.O 是 Apache-2.0 项目。本仓库只引用它的路由、契约、端口等可验证事实，不含任何 N.E.K.O 源码。

## 成员名册

名册唯一来源是仓库根 `roster.json`。下表与各文档的人数声明由 `python scripts/generate_roster_tables.py --write` 从它生成，标记之间不得手改；CI 用 `--check` 拦截漂移。

<!-- BEGIN roster:readme -->
| Agent ID | 角色 | 名号 | 职责 |
|---|---|---|---|
| plugin-forge-lead | 主理人 | 交付总监（主理人） | 编排、分拣、契约门禁把关、三段回奏汇编 |
| plugin-forge-contract | 专家 | 契约审议师 | 按目标平台真实能力面审议方案，可封驳 |
| plugin-forge-dev | 专家 | 插件开发工程师 | 脚手架 / manifest / 生命周期 / 错误约定 / 修故障 |
| plugin-forge-tdd | 专家 | 测试驱动工程师 | 红-绿-重构、最小可跑测试 |
| plugin-forge-architect | 专家 | 架构设计师 | 模块接口、架构图、深化扫描 |
| plugin-forge-simplifier | 专家 | 极简工程师 | 删冗余 / 死代码 / 过度抽象 |
| plugin-forge-reviewer | 专家 | 代码审查师 | 双轴审查、修法清单 |
| plugin-forge-planner | 专家 | 规划问诊师 | 需求 sharpen、能力路由 |
| plugin-forge-skill-ops | 专家 | 能力运营师 | skill/prompt 创建、查重、审计、挂接 |
| plugin-forge-reliability | 专家 | 运维可靠性工程师 | CI/CD、发布就绪、故障响应 |
| plugin-forge-security | 专家 | 安全工程师 | 密钥 / 注入 / 依赖供应链门禁 |
| plugin-forge-market-research | 专家 | 生态调研分析师 | 平台生态调研、缺口分析 |
| plugin-forge-sandbox | 专家 | 运行时验证工程师 | 隔离运行时真实加载验证 |
| plugin-forge-docs | 专家 | 文档工程师 | README / 教程 / 变更说明 / PR 文本 / 对外沟通，双风格文档门禁 |
<!-- END roster:readme -->

## 机制

- 契约先行：`agents/plugin-forge-contract.md` 持真实否决权，没有准奏不写码。准奏落盘 `contract-approval.json`（含 `verify_commands`：每条都得是能直接跑起来的验收命令），写码前由 `skills/plugin-forge/scripts/check_contract_approval.py` 机器校验；scope 外的文件同样拒绝。
- 先搜后建（rbb）：Workflow A 第 1a 步做三轮检索，判决取加入上游 / 做官方扩展 / 分叉并保持同步 / 自建四档之一，小改动走快速档。权威本体是上游插件 reuse_before_build，本团只引用不内嵌。条文见 skills/plugin-forge/SKILL.md 铁律 3 与 skills/plugin-forge/references/workflows.md。
- TDD + 极简 + 安全门禁 + 运行时验证（`skills/neko-plugin-sandbox/` 真实冒烟；`dry_load.py` 做干装载：有宿主环境就调上游自己的导入函数真装载一次，环境不全就降级为 AST 级检查并在输出里明写降级原因）。
- 工作流 H：宿主破坏性变更（RFC/changelog）对插件机队的影响审计与上游回帖礼仪。
- 双风格文档门禁：`plugin-forge-docs` 内置 ASD-STE100 + humanizer（中英同等），见 `skills/plugin-forge/references/style-anti-slop.md`；外部内容入库先过 `AUDIT.md` 流程（四项安全检查与评分规则见 `skills/plugin-forge/references/security-checklist.md`）。
- 平台 profile：N.E.K.O 平台契约的**本地权威** `skills/plugin-forge/references/profile-neko.md`（基线是哪一批 sha、分工怎么写，只在 profile 文首 ⓪ 那一处声明；这里不复述，免得两处各说各话。漂移由 `python scripts/profile_drift.py --strict` 在 CI 里对当前 main 逐条把门，一次核对 profile-neko.md 与沙箱 SKILL.md 两份文档）。它不是「唯一」权威，也不能替代上游：文首 ⓪ 陈旧门禁若返回 404，§3/§7 立即降为过期面，先按新 main 重核再用。与 workshop `rules.json` 的编号冲突时，以 §3 入口规则与 `AGENTS.md` 为准并在 AUDIT 记录。NEKO 扩展阅读在 `references/neko/`。
- 版本号只有一个来源：`.claude-plugin/plugin.json` 的 `version`，即插件发布号。`SKILL.md` 与 `agents/*.md` 不带自己的版本号，避免两处版本各说各话。

## 安装

**跑本仓的回归集（可选，只在你要改脚本时需要）**

pytest 是唯一第三方依赖，且只用于开发：产品脚本 `scripts/*.py`、`skills/*/scripts/*.py`
仍然零第三方依赖。

```bash
python -m pip install -r requirements-dev.txt
python -m pytest            # 收集 tests/，覆盖 roster 门禁、契约门禁、干装载三条线
```

- **Claude Code / marketplace 宿主**：本仓库即插件，marketplace 单条目指向 `./`。
- **Codex**：复制 `skills/plugin-forge/` 到 `~/.codex/skills/plugin-forge/`；`codex/prompts/plugin-forge.md` 到 `~/.codex/prompts/`（触发 `/plugin-forge`）。沙箱 skill 同样复制到 skills 目录。
- **zcode**：`agents/*.md` → `~/.zcode/agents/`，`skills/plugin-forge/` → `~/.zcode/skills/plugin-forge/`。
- **CodeBuddy**：按 marketplace 安装，或复制 plugin.json 字段到 `.codebuddy-plugin/`。

## 目录

```
agents/            14 个人设：1 主理人 + 13 专家
skills/plugin-forge/       范式 skill + references（含 neko/ 扩展阅读）+ scripts（契约门禁校验、安全审计扫描）
skills/neko-plugin-sandbox/ NEKO 运行时沙箱 skill + scripts/sandbox.py
codex/prompts/     Codex 单代理运行入口
scripts/           generate_roster_tables.py（名册表生成）+ session_capability_refresh.py（能力刷新，只读）
                   + profile_drift.py（profile 锚点漂移核对，读上游当前 main；仓库维护面，不装进宿主）
                   + verify_installs.py（仓外执行副本与仓库字节一致性，本地 --check、CI 只跑 --self-test）
roster.json        团队名册唯一来源
.claude-plugin/    marketplace.json 与 plugin.json（版本号唯一来源）
tests/             pytest 回归集（名册门禁、契约门禁、干装载、人设接线）
pytest.ini / requirements-dev.txt  测试路径配置与开发依赖（只有 pytest，运行期零第三方依赖）
.github/           sync-check CI：名册漂移、门禁自检
AUDIT.md NOTICE LICENSE
```

MIT（完整正文在 `LICENSE`）。第三方摘要署名与设计致谢见 `NOTICE` 与 AUDIT.md：`NOTICE` 不是许可的一部分，
不给上面的授权加条件。
