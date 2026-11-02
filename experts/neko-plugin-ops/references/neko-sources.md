# N.E.K.O 官方源（插件开发研究）

> 专家团做 N.E.K.O 插件开发研究时的**权威上游源**。研究需求优先查本文档定的官方源，再结合本地 `neko-plugin-sdk` skill。结论须可溯源；官方文档与本地 SDK 冲突时，以上游文档为准（并记录差异）。

## 1. 源码仓库
- **仓库**：https://github.com/Project-N-E-K-O/N.E.K.O
- **定位**：开源"伙伴 Agent（Companion Agents）"及养育社区；实时语音/视觉、长期记忆、Live2D/3D Avatar、游戏伴侣、社交互访、插件市场。
- **技术栈**：Python（FastAPI、`uv` 管理依赖，`main_logic/`、`brain/`、`config/`）+ React/TypeScript 前端；Live2D / VRM / MMD / PNGTuber；LiveKit / TRTC；Docker / Nginx。
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
- **仓库**：https://github.com/addsas222/neko-plugin-workshop —— N.E.K.O. 插件工坊（一句话生成猫娘计划插件的 AI 工坊；MIT，Owner fengdiehualian）。
- **定位**：面向"零代码"用户的插件生成器，但对插件开发者是**真实插件范式语料库**：内置官方插件规范 skill（`packs/`，含 15 页官方文档引用 + 15 条铁律）、市场能力使用率统计与全量插件卡片。
- **数据时效警告（勿引用旧值）**：工作坊 README 所称「46 个官方市场插件源码（`market-corpus/`）」及旧统计 `llm_tool` 50% / `push_message` 57% / `store` 37% 均已过时且与实测冲突；当前主分支已无 `market-corpus/` 与根级 `references/`，真实统计文件位于 `packs/neko-plugin/skill/neko-plugin-dev/references/market-patterns.md`，覆盖 **127 个在架插件（126 份可读源码）**。使用率一律以本包 `references/neko-market-patterns.md` 为准（`@llm_tool` 52% / `push_message` v2 44% / `store` 15% 等，2026-10-26 抓取）。
- **工具链（可借鉴定）**：`cli.mjs`（scaffold/verify）、`mcp.mjs`（MCP：`wb_scaffold` / `wb_verify` / `wb_install_skill`）、`wb-studio.mjs`（网页工坊）、`src/index.ts`（生成→check→build 闭环内核 + 错误解析器）。
- **插件市场**：https://market.project-neko.cn（上架插件参考）。
- **市场惯用法速查（已蒸馏）**：`references/neko-market-patterns.md` —— 127 个在架插件能力使用率 + 四大惯用法，提炼自上游工作坊 `packs/neko-plugin/skill/neko-plugin-dev/references/market-patterns.md`（数据 2026-10-26 抓取）。
- **用法**：做"真实插件长什么样 / 市场惯用法 / 规范自检"研究时优先挖这里；与官方文档、本地 SDK 冲突时以上游文档为准。

## 5. 使用约定
- 研究任务由 **云雨（neko-plugin-dev）** / **云远（neko-architect）** 主导，先读本文档定的官方源，再结合本地 SDK skill。
- 引用上游结论时标注来源链接；外部资源不可用就跳过并说明，不伪造。
- 本文件随专家包分发，路径 `references/neko-sources.md`。
