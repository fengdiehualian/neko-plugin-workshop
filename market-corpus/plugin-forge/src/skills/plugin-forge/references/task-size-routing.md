# 任务规模分拣（task-size-routing）

主理人接单先判规模，再决定派谁。判据取最大项：文件数、是否动 manifest/公开接口、是否新增宿主能力调用。

门禁不因规模豁免：契约准奏（含 `contract-approval.json` 落盘与 `verify_commands`，这台门禁只验形状不执行；简档也要给得出验证命令，改一行也得有一条能跑的检查）与安全门禁始终生效；运行时验证做不了时如实标"未验证+原因"，不豁免声明。

阈值出处（可调默认值，不是门禁豁免）："小"档的 ≤3 个文件与安全门禁的总分 ≥90 放行线（评分表见 `security-checklist.md`）都是 Codex 整理 v2.1.0 时定的经验默认值，非宿主或上游规定的固定数字，可按仓库体量调（如 ≤2 或 ≤5、≥85 或 ≥95）。改动时同步写回本文档与 security-checklist 两处。调数字不改变原则：门禁本身任何规模、任何阈值下都不豁免——调档只改变"走精简流"的范围，不改变"要过的关"。

## 三档规模

| 规模 | 判定标准 | 必到成员 | 按需成员 |
|---|---|---|---|
| 小 | ≤3 个文件；不动 manifest 与公开接口；不新增宿主能力；修 bug / 调配置 / 改文案 | lead + dev + tdd | market-research（要写代码时先跑 1a 快速档）、reviewer（复核）、sandbox（仅运行时问题） |
| 中 | 加功能、改行为；接口不变 | Workflow A 全量七步 | architect（有接口争议时）、simplifier（有膨胀信号时）、reviewer（提交前双轴） |
| 大 | 跨模块重构、新入口类型、换宿主/迁移存储、manifest 能力声明变更 | A 全量 + architect + reviewer + simplifier + sandbox 交付前冒烟 | reliability（发布就绪） |

## 小改动精简流（lead 派 dev + tdd，要写代码时先派 market-research 跑 1a 快速档）

1. lead 分拣并一句话定规模，记录判据（文件数/接口/能力三项）。跳过 planner / architect。
2. 要写代码就先过 1a 快速档：lead 派 market-research 跑 3 条检索串，回三行 `Reused / Change / Risk`。
   纯文档 / 文案改动不触发 1a；一旦动到代码就触发。免跑快速档只有一种情形：本任务已走 Workflow A
   全量，里面含完整的 1a（三轮检索 + 四档判决），不再另跑一遍。
3. contract 极简审议，三行结论：能力点是否仍存在 / 入口类型是否不变 / 验收口径是否不变。准奏仍落盘 `contract-approval.json`，scope 只写实际要动的 ≤3 个文件。
4. tdd 写最小失败测试 → dev 修到绿 → 测试通过即交付。architect / simplifier / reviewer **默认不派**。
5. 实现中发现要动接口 / manifest / 新能力：立即停下，升档重走第 3 步，不得按小改动继续写。
   升档到「中」以上时，1a 从快速档升级为完整的三轮检索 + 四档判决。

## 原则

- architect / simplifier / reviewer 是按需角色：出接口争议、出膨胀信号、要提交前双轴审查时才派；派了就要有产出（图 / 删改清单 / 修法清单）。
- 派工可以降级，责任不能降级：宪法第 5 条（默认带测试与 Review）在小改动下合并为一次简短检查，不省略。
- 规模由 lead 判定并写明依据；用户对规模有异议时按用户档位走，lead 记录分歧。
