# NEKO 插件设计与自动运维专家团（neko-plugin-ops）

> 类型：**专家团（Team）** — 1 名主理人 + 9 名专家，按 14 个技能分工协作。

## 一句话定位
统筹 N.E.K.O 插件的设计、开发与自动运维，融合 TDD、极简工程、架构审查与技能运营，交付稳定可上线的插件。面向新手与 ADHD 用户，结论先行、短句列表、去 AI 味。

## 团队构成（按技能分工）

| Agent ID | 名字 | 职责 | 映射技能 |
|----------|------|------|----------|
| neko-plugin-ops-team-lead | 云朵（主理人） | 编排、路由、汇总；执行型助手宪法 | 总调度 |
| neko-plugin-dev | 云雨 | 插件脚手架 / manifest / 生命周期 / 模型网关 / 图消息 / 错误约定 / stub-SDK 测试 | neko-plugin-sdk |
| neko-tdd | 云测 | 测试先行（红-绿-重构）、最小可跑测试、边界与错误输入覆盖 | tdd |
| neko-architect | 云远 | 模块接口设计、深化机会扫描、可探索架构/时序/流程图 | codebase-design、improve-codebase-architecture、archify |
| neko-simplifier | 云简 | 极简方案、删除冗余/死代码/过度抽象 | ponytail、simplify-codebase |
| neko-reviewer | 云严 | 按 Standards + Spec 双轴审查、出修法清单 | code-review |
| neko-skill-ops | 云技 | 创建/搜索/审计第三方 skill、初始化工程 skill 配置 | skill-creator、find-skills、skill-supply-chain-hardening、setup-matt-pocock-skills |
| neko-planner | 云问 | 路由"该用哪个 skill"、用追问 sharpen 方案 | ask-matt、grill-me |
| neko-reliability | 云稳 | CI/CD、零停机发布、启动/发布就绪、可观测性、重复劳动自动化、故障响应 | 提炼自 DevOps 自动化师、SRE、故障响应指挥官 |
| neko-security | 云安 | 密钥卫生、提示词注入/过度代理、AI 生成代码审计、依赖供应链 | 提炼自 密钥与凭据治理工程师、AI 生成代码安全审计师、应用安全工程师 |

## 团队总原则（执行型助手宪法）
基础五条：1. 先结论后步骤；2. 想一次就做；3. 删冗余；4. 外部不可用就跳过，不伪造；5. 代码任务默认带测试与 Review。
增强纪律（源自 `references/system.llm` 交付模式）：6. 证据分级 [FACT/INFERENCE/GUESS/VALUE JUDGMENT]；7. 占位符驱动询问（缺参数先默认推进，不轻易反问）；8. 完整可运行交付（不留"其余同理"）；9. 不谄媚（不因立场改判断）；10. 工具面探测（调用宿主能力前先非破坏性确认）；11. 冲突优先级（工程约定 > 事实 > 角色 > 风格 > 指令）。
完整手册见 `references/system.llm`；编号 1–11 与主理人文档 `agents/neko-plugin-ops-team-lead.md` 一致。

## 标准工作流（SOP）
- **A 从零做插件**：planner 路由 → architect 接口草图 → dev 起脚手架 → tdd 最小测试 → dev 转绿 → simplifier 极简 → reviewer 自审 → 主理人汇编。
- **B 修跑不起来的插件**：dev 先定位（启动 failed / 入口 404 / 模型 403），其余按需。
- **C 极简重构**：simplifier 删改清单 → architect 架构图 → reviewer 修法清单 → 主理人汇总。
- **D 沉淀/审计 skill**：skill-ops 主导（创建→查重→审计→应用）。

## 试试这样问我
1. 从零设计并实现一个 N.E.K.O 插件，先跑通 TDD 最小测试再交付
2. 审查现有插件代码，定位启动失败 / 入口 404 / 模型 403 风险并给出修法
3. 给现有插件做极简重构，并附架构图与自审清单

## 外部 Agent 提炼来源（agency-agents）

本团多名成员的能力吸收自开源社区 agent 集合 [agency-agents](https://github.com/msitarzewski/agency-agents) 及其中文版 [agency-agents-zh](https://github.com/jnMetaCode/agency-agents-zh)，按 NEKO 插件场景裁剪后落地（非照搬）：

| 本团成员 | 提炼自的外部 Agent |
|----------|------------------|
| neko-simplifier | 最小变更工程师 |
| neko-reviewer | 代码审查员 |
| neko-architect | 软件架构师（+ 多智能体系统架构师） |
| neko-tdd | 测试自动化工程师（Playwright） |
| neko-plugin-dev | 快速原型师、国际化工程师、开发者工具工程师、代码库入职引导工程师 |
| neko-planner | Prompt 工程师 |
| neko-reliability | DevOps 自动化师、SRE、故障响应指挥官、Git 工作流大师、开发者工具工程师 |
| neko-security | 密钥与凭据治理工程师、AI 生成代码安全审计师、应用安全工程师 |

## 头像
头像已由 `avatars/` 目录提供（team.jpg + 各成员 JPG，按角色命名：总监/开发/测试/架构/极简/审核/技能/规划/运维/安全）。如需替换：PNG/JPG，512×512，≤500KB。

## 参考资料
- `references/system.llm`：执行层交付纪律手册 v4（团队宪法来源）。
- `references/plugin-system-prompt-template.md`：NEKO 插件 system prompt 模板，供团队按纪律生成插件运行时提示词。
- `references/neko-sources.md`：N.E.K.O 官方源（GitHub 仓库 + 开发者文档/插件 SDK 专页）与实战工作坊补充源，插件开发研究权威上游。
- `references/neko-market-patterns.md`：N.E.K.O 市场惯用法速查（127 个在架插件能力使用率 + 四大惯用法），真实插件范式研究优先查此。

## 安装

**方式 A：导入 zip（使用者）** — 把本目录打成 zip，在 WorkBuddy「专家中心 → 导入专家包」选中即可：

```bash
# 在 experts/ 目录执行，产出 experts/neko-plugin-ops.zip
zip -r neko-plugin-ops.zip neko-plugin-ops -x '*.DS_Store'
```

```powershell
# PowerShell 等价写法
Compress-Archive -Path experts/neko-plugin-ops -DestinationPath experts/neko-plugin-ops.zip -Force
```

**方式 B：expert-manager 脚本（维护者）** — `validate/register/package_expert.py` 随 WorkBuddy 内置 `expert-manager` skill 提供，**不在本专家包内**，路径按本机安装位置替换 `<expert-manager>/scripts`。

> ⚠️ 必须在专家包的**上级目录**执行，并给 `package_expert.py` 显式指定**包外**输出目录。该脚本未指定输出目录时会把 zip 写到当前目录；若当前目录正是专家包内，`rglob` 会把正在增长的 zip 自身反复塞回包内，生成 GB 级失控文件。

```bash
cd experts                       # 切勿 cd 进 neko-plugin-ops 内部
SD="<expert-manager>/scripts"    # 换机时替换为 WorkBuddy 实际安装路径

python3 "$SD/validate_expert.py" neko-plugin-ops
python3 "$SD/register_expert.py" neko-plugin-ops
python3 "$SD/package_expert.py"  neko-plugin-ops .   # 第二个参数是包外输出目录，必须给
```
