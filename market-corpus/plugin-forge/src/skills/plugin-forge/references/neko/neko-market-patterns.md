# NEKO 插件市场惯用法速查

> 来源：蒸馏自 `addsas222/neko-plugin-workshop`（fork of `fengdiehualian/neko-plugin-workshop`）的 `packs/neko-plugin/skill/neko-plugin-dev/references/market-patterns.md`。
> 原始数据：market.project-neko.cn 全量插件，**2026-10-26 抓取，127 个在架插件（126 份可读源码）**。
> 用途：团队做"真实插件长什么样 / 市场惯用法"研究时优先查此；与官方文档冲突时以上游文档为准（详见 `references/neko-sources.md`）。
> 注：该 workshop 仓库 README 提到的 "46 个插件" 已过时，实际分析覆盖 **127** 个。
> **⚠️ 口径保护（2026-11-06 复核）**：本表的 **127 / 126** 是这次能力统计的**样本分母（固定历史快照）**，不是"当前在架总数"。市场在架总数已增长（2026-10-26=127、2026-11-04=135，2026-11-06 复核仍为 135），但**本表数字与百分比一律不得随之更新**——改分母会让下表 7 项使用率全部失效。需要"当前总数"请用 `references/neko-ecosystem-research-playbook.md` 的实测快照并标日期。

## 一、能力使用率（126 份可读源码）

| 能力 | 使用率 | 说明 |
|------|--------|------|
| `@llm_tool` | **52%（65）** | 市场第一大主流能力——让猫娘在对话里直接调用你的功能 |
| `push_message` v2 | 44%（55） | 主动推消息到聊天窗 / HUD，提醒类必备 |
| `self.data_path()` | 34%（43） | 持久化 JSON / SQLite，重启不丢 |
| http 客户端（httpx / requests） | 31%（39） | 联网类插件（搜索 / 音乐 / 邮件）标配 |
| 静态 UI `register_static_ui` | 27%（34） | 插件自带网页面板 |
| `self.store` | 15%（19） | 官方键值存储（需 plugin.toml 开 `[plugin.store] enabled=true`） |
| `@timer_interval` | 15%（19） | 定时任务 |
| `PluginSettings` 声明式配置 | 少数但官方推荐 | hot 字段热更新 |
| 动态入口 / PluginRouter / list_actions | <10% | 大型插件才用 |

**结论（四大支柱）**：`@plugin_entry` + `@llm_tool` 双注册、`push_message` v2、`data_path` / `store` 持久化，是市场插件的四个支柱。任何"用户一句话能触发"的功能，**必须同时注册为 `llm_tool`**。

## 二、四大惯用法

1. **`llm_tool` + `plugin_entry` 双注册**（范例 `shell_cmd`）
   - 同一方法叠加两个装饰器，既是面板 / Agent 手动入口，又是猫娘对话可调用工具；不写两份代码。
   - `llm_tool.name` 用 `插件主题_动作` 全局唯一格式（如 `shell_cmd_run`）；`description` 写"当用户想…时使用"，LLM 靠它决定是否调用。
   - `llm_result_fields`（plugin_entry 上）控制回给 LLM 的字段，大结果必须过滤省 token。

2. **联网插件标准结构**（范例 `tavily_search`）
   - `httpx.AsyncClient(timeout=30.0)` 异步 + 显式超时；阻塞式 `requests` 会拖死事件循环。
   - API key 从配置读，绝不硬编码；上游失败 `return Err(SdkError(...))`，不抛异常；结果截断限流（如 `[:5]`）。

3. **定时提醒类状态机**（范例 `sys_monitor`）
   - 防重复轰炸：用"上次提醒键值 + 时间戳"去重（`alert_key` + `now - last_at > repeat_interval`）。
   - `visibility=["chat"]` 只进聊天窗，要弹 HUD 加 `"hud"`；`priority` 紧急 5 / 普通 3。
   - **定时回调里绝不 raise**——异常吞掉记日志，返回 `Ok(...)`。

4. **持久化选择**
   - 结构化数据（笔记 / 记录 / 列表）→ `self.data_path("x.json")` + json 读写；
   - 简单键值（缓存状态 / 开关）→ `self.store.set/get`（开 `[plugin.store]`）；
   - 可丢弃缓存 → `self.cache_path(...)`。

## 三、让插件被 Agent 选中（plugin.toml 可见性字段）

`description`（完整功能）、`short_description`（一句话）、`keywords`（用户可能说的词，正则数组）决定插件是否被猫娘 Agent 选中执行。例：

```toml
description = "随机返回一句古诗,支持按作者/主题搜索"
short_description = "古诗随抽与检索"
keywords = ["古诗", "诗", "唐诗", "宋词", "来一首诗"]
```

## 四、完整范例（market-examples/）

| 范例 | 学什么 |
|------|--------|
| `shell_cmd/` | 双注册、子进程执行、多文件组织 |
| `tavily_search/` | httpx 异步联网、API key 配置、结果裁剪、`llm_result_fields` |
| `sys_monitor/` | 定时监控、push_message v2、防重复状态机、静态 UI |

## 五、配套规范（写代码的硬约束，另查）

- 官方插件 SDK：`https://project-neko.online/zh-CN/plugins/`
- 工坊插件规范铁律：`neko-plugin-workshop` 的 `packs/neko-plugin/skill/neko-plugin-dev/SKILL.md` 与 `rules.json`（R1–R12：`plugin_id` 规则、entry 格式、`Ok`/`Err` 返回、已移除 API 黑名单、`push_message` v2、`@lifecycle` 初始化、`data`/`cache` 路径、跨插件调用、参数注解、日志分级与加锁等）。
