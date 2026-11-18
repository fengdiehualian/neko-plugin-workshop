# N.E.K.O 官方源（插件开发研究）

> 专家团做 N.E.K.O 插件开发研究时的**权威上游源**。研究需求优先查本文档定的官方源，再结合本地 `neko-plugin-sdk` skill。结论须可溯源；官方文档与本地 SDK 冲突时，以上游文档为准（并记录差异）。

## 1. 源码仓库
- **仓库**：https://github.com/Project-N-E-K-O/N.E.K.O
- **定位**：开源"伙伴 Agent（Companion Agents）"及养育社区；实时语音/视觉、长期记忆、Live2D/3D Avatar、游戏伴侣、社交互访、插件市场。
- **技术栈**：Python（FastAPI、`uv` 管理依赖，顶层 `plugin/`、`app/`、`nekoclaw/`、`main_logic/`、`config/`）+ React/TypeScript 前端；Live2D / VRM / MMD / PNGTuber；LiveKit / TRTC；Docker / Nginx。
  顶层目录清单读于 main `a222e50`（2026-11-15）。旧文档常提的 `brain/` 在该提交已不在仓库根，`nekoclaw/` 已在；引用包路径前先列一次根目录。
- **与插件相关**：
  - `plugin/` 目录（插件代码与 FastAPI 挂载点，须扫描并挂载门禁）
  - 环境变量：`PLUGIN_CONFIG_ROOT`、`PLUGIN_PACKAGES_ROOT`、`PACKAGE_PROFILES_ROOT`
  - 依赖隔离在 `vendor/`
  - `docs/development` 与 `docs/<lang>/guide` 开发指南
  - 门禁契约：`InstanceAccessMiddleware`（所有服务须遵循，有静态契约测试）

## 2. 开发者文档（中文站）
- **首页**：https://project-neko.online/zh-CN/
- **插件 SDK 专页（必读）**：https://project-neko.online/zh-CN/plugins/
  - 装饰器 API、异步生命周期钩子、插件间通信、启用后的智能体入口、插件市场。
- **其他相关页**：
  - 架构：https://project-neko.online/zh-CN/architecture/
  - 记忆系统：https://project-neko.online/zh-CN/architecture/memory-system
  - 智能体框架：https://project-neko.online/zh-CN/architecture/agent-system
  - 前端指南：https://project-neko.online/zh-CN/frontend/
  - API Provider 配置：https://project-neko.online/zh-CN/config/api-providers

## 3. 本地 SDK 参考
- `neko-plugin-sdk` skill（本机）：`plugin.toml`、NekoPluginBase、装饰器（`@plugin_entry` / `@lifecycle` / `@timer_interval` / `@message` / `@llm_tool`）、`Ok` / `Err` 结果类型、stub-SDK 测试模式、常见故障 `entry-404` / `startup-failed` / `model-403`。

## 4. 实战工作坊（补充源）
- **仓库**：https://github.com/fengdiehualian/neko-plugin-workshop —— N.E.K.O. 插件工坊（一句话生成猫娘计划插件的 AI 工坊；MIT，Owner fengdiehualian）。
- **定位**：面向"零代码"用户的插件生成器，但对插件开发者是**真实插件范式语料库**：内置官方插件规范 skill（`packs/`，含 15 页官方文档引用 + 15 条铁律）、市场能力使用率统计与全量插件卡片。
- **数据时效警告（勿引用旧值）**：工作坊 README 所称「46 个官方市场插件源码（`market-corpus/`）」及旧统计 `llm_tool` 50% / `push_message` 57% / `store` 37% 均已过时且与实测冲突；当前主分支仍有 `market-corpus/`（2026-11-14 实测 128 项），但根级 `references/` 已无，真实统计文件位于 `packs/neko-plugin/skill/neko-plugin-dev/references/market-patterns.md`，覆盖 **127 个在架插件（126 份可读源码）**。使用率一律以本包 `references/neko-market-patterns.md` 为准（`@llm_tool` 52% / `push_message` v2 44% / `store` 15% 等，2026-10-26 抓取）。**此 127/126 为能力统计样本分母，2026-11-06 复核确认不改**（在架总数 2026-11-06 复核仍为 135，属另一时点，勿混用）。
- **工具链（可借鉴定）**：`cli.mjs`（scaffold/verify）、`mcp.mjs`（MCP：`wb_scaffold` / `wb_verify` / `wb_install_skill`）、`wb-studio.mjs`（网页工坊）、`src/index.ts`（生成→check→build 闭环内核 + 错误解析器）。
- **插件市场**：https://market.project-neko.cn（上架插件参考）。
- **市场惯用法速查（已蒸馏）**：`references/neko-market-patterns.md` —— 127 个在架插件能力使用率 + 四大惯用法，提炼自上游工作坊 `packs/neko-plugin/skill/neko-plugin-dev/references/market-patterns.md`（数据 2026-10-26 抓取）。**口径保护**：此处 127/126 是该次能力统计的样本分母（固定历史快照），2026-11-06 复核时**未改**；市场在架总数 2026-11-04 为 135、2026-11-06 复核仍为 135（见 `references/neko-ecosystem-research-playbook.md`），两者不可混用，改分母会使全部百分比失效。
- **用法**：做"真实插件长什么样 / 市场惯用法 / 规范自检"研究时优先挖这里；与官方文档、本地 SDK 冲突时以上游文档为准。

## 5. 使用约定
- 研究任务由 **云雨（neko-plugin-dev）** / **云远（neko-architect）** 主导，先读本文档定的官方源，再结合本地 SDK skill。
- 引用上游结论时标注来源链接；外部资源不可用就跳过并说明，不伪造。
- 本文件随专家包分发，路径 `references/neko-sources.md`。

## 6. 目录范围（`references/neko/` 23 份笔记怎么读）

本目录是 N.E.K.O profile 的**扩展阅读**，不是规则来源。规则只在上一级的 `profile-neko.md`（平台事实）与 `constitution.md` / `SKILL.md`（团队制度）。

| 类型 | 文件 | 读法 |
|---|---|---|
| 上游审计与调研（公开事实，带 commit / 日期） | `neko-court-*`（6）、`neko-ecosystem-*`（2）、`neko-market-patterns`、`rea-tool-surface`、`third-party-skill-audit`、`dev-agent-ecosystem`、`doc-writing-playbook`、`doc-writing-skill-audit`、`mattpocock-skills`、`plugin-system-prompt-template`、本文件 | 可引用，引用时把日期一起带上；上游一变就要重核 |
| **作者单机状态**（只描述写它那台机器） | `capability-baseline`、`session-refresh-sop`、`security-refresh-notes`、`skill-inventory`、`safe-dev-capabilities` | **不得当跨机器结论**：里面的 MCP 名、hook 指纹、克隆目录、已装清单都是单机快照，换机器不成立 |
| 归档的外部提示词 | `system.llm` | 不是本团行为规范，见该文件头 ARCHIVED 声明 |

对外发布的三条硬约束：
1. 不写明文密钥，也不写 key 前缀；失效 key 只记「已失效」。
2. 不写真实用户名与本机绝对路径；需要位置时写 `~` 或 `<user>`。
3. 脱敏只在输出侧做，读取仍用真值（`os.path.expanduser`）。把字面 `~` 留在代码里会让`scripts/session_capability_refresh.py` 恒报「路径缺失」，已踩过一次。
