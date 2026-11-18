# N.E.K.O 生态与运行时隔离调研（2026-11）

> 调研时点 **2026-11-06/07**；执行：云调（neko-market-research）做检索，主理人做源码复核。
> 工具：**Exa 可用**（8 条并行查询 + 一手页面直连）；**AnySearch 不可用**（key 失效，见 §8）。

## 1. 一句话结论

N.E.K.O 插件侧在 2026-10~11 **没有破坏性变更**（此判断只到调研时点 2026-11-06/07；其后 11-11 公告的 NekoClaw 重写 issue #3683 承诺守住 /runs、push_message、@llm_tool 等插件契约，但改了调用方行为：取消真正打到插件、同一入口可能被重复调用、`_ctx.lanlan_name` 由宿主值覆盖，详见 profile-neko.md 的 2026-11-11 条目），官方**明确不承诺 SDK 路线图**（只能从 issue/PR/release 反推）；同类产品的隔离范式（OpenPets 的权限桥、bext 的渐进分层）比我们现在的沙箱更值得抄；工程侧「健康检查 + uv 隔离」是成本最低的加固基线。

## 2. 官方动态（[FACT]，带来源）

### 2.1 v0.9.1（2026-11-01）
插件侧只做**分发与安装体验**：插件管理器界面统一、市场多线路竞速下载 + 断点续传 + 可更新检查 + GitHub 镜像源；Galgame / 伴学 / 战雷等插件**改由市场分发**，本体更轻。运行时：Docker 加固（Nginx 分层、`.env` 注入、健康检查、备份）+ 低配 2C2N 方案 + 自愈看门狗。
来源：<https://github.com/Project-N-E-K-O/N.E.K.O/releases/tag/v0.9.1>（2026-11）

### 2.2 版本节奏
`v0.9.1`(2026-11-01) ← `v0.9.0 NekoVerse`(2026-08-20) ← `v0.8.3`(2026-06-27) ← `v0.8.2`(2026-06-12)；nightly 2026-11-03。
来源：GitHub Releases API（查询 2026-11-06）

### 2.3 ⚠️ SDK 路线图：官方明确不承诺
原文（仓库内文件，可直接引用）：
> This page does not publish version dates or promise future providers/platforms. Those claims become stale faster than the code and require product-owner decisions.

出处：`docs/contributing/roadmap.md:3`

**对专家团的后果**：写「向官方提 SDK 扩展需求」时**不得声称有路线图**；SDK 能力判断一律以官方文档页 + 源码为准。

## 3. 插件系统架构（主理人从源码复核）

| 事实 | 证据 |
|---|---|
| 进程隔离 + async IPC | `plugin/core/host.py:11` `import multiprocessing`；`:52` `from plugin.core.zmq_transport import …`；`:138-143` 明确「Never touch ZMQ」的 fd 管理约束（那句现在在 141） |
| 每入口超时可配 | **字段名是 `entry_timeout`**（`plugin/config/schema.py:107`），不是 `timeout`；校验要求 finite > 0 |
| **Extension 类型已不存在** | `plugin/sdk/` 内无 `ExtensionKind` / `class Extension` 定义；包类型只有 Plugin 与 Adapter |
| 每插件独立进程 | 官方插件文档：Plugin Host（生命周期/Bus/ZMQ IPC）+ Plugin Server（FastAPI/注册表/消息队列），Plugin / Adapter 各自独立进程 |

## 4. 同类产品范式（同类参考，非 N.E.K.O）

| 产品 | 机制 | 可借鉴点 |
|---|---|---|
| **OpenPets**（TS，MIT） | 插件 = manifest 校验的 package，跑在 sandbox，只通过 **permission-checked SDK bridge** 与宿主通信；**插件调用模型但永远看不到 key** | 「权限化桥 + 永不下发 key」 |
| **bext nsjail** | **渐进式分层隔离**：进程（stdio + wall-clock 超时）→ PID ns → NET ns → MOUNT ns → cgroup v2 → seccomp-bpf；**无 root 时降级**为独立进程 + stdio + 超时；IPC 为 JSON-lines over stdio | 「先能跑，再逐层加固」 |

来源：<https://github.com/alvinunreal/openpets/blob/main/docs/plugins.md>、<https://docs.bext.dev/plugins/nsjail-plugins>（2026-11 访问）

## 5. 运行时隔离工程实践

- **健康检查双型**：`TCP`（端口可连）/ `Script`（跑命令看退出码）+ `healthcheck_retries` + 失败标记 `UNHEALTHY` + 可选重启 + `autostart`/`autorestart`。来源 <https://supervice.readthedocs.io/en/stable/healthchecks.html>（2026-11）
- **依赖隔离走 uv**：项目装在 `.venv` 与 shell 隔离；`uvx` 用**临时隔离环境**跑工具，不污染全局。来源 <https://docs.astral.sh/uv/concepts/projects/run/>（2026-11）
- **wall-clock 超时必须在宿主侧强制**：有实现明确「不提供内置 timeout/cancel」即被视为缺陷点（fidric 文档自陈）。

## 6. 对专家团的改进建议（按可落地性排序）

| # | 建议 | 落到哪 |
|---|---|---|
| 1 | **manifest 即契约**：沙箱启动前先校验清单（id / 入口 / 权限 / 依赖），拒绝未声明能力 | 沙箱 `prepare` / `plugin-add` 前置校验 |
| 2 | **权限化 SDK 桥**：插件只经桥拿能力，**永不直接持有 key/凭据** | 沙箱 `ctx.models` 保持 `TransportError`（已符合）；文档明写原则 |
| 3 | **分层降级隔离**：基线 = 独立进程 + stdio + 超时；有 root/Linux 再叠 namespace/cgroup/seccomp | 沙箱 README 的「能力边界」表 |
| 4 | **健康检查 + 自愈**：TCP/Script 双型 + retries + 自动重启 | 沙箱 `start` 的 `/health` 轮询已具备，补 retries 语义说明 |
| 5 | **依赖隔离走 uv**：每插件独立 `.venv` 或 `uvx`，禁装全局 | 沙箱 `install`（已用 uv 优先） |
| 6 | **流程层**：SDK 能力判断以官方页为准，**不得引用路线图** | team-lead.md Workflow E 措辞 |

## 7. 未核验清单

1. Reddit / Discord / X 上**具体插件**的用户评价（Exa 无实质返回）
2. LiPet 插件系统正文（403，仅有搜索摘要）
3. 第三方插件安装量 / 评分（属另一口径，避免与 135 / 10,413 快照混淆）
4. SteamDB 等第三方页的发布日与 GitHub API 有出入 —— 本文件一律采用 GitHub API 时点

## 8. 工具可用性（重要）

| 工具 | 状态 | 说明 |
|---|---|---|
| **Exa** | ✅ 可用 | skill `build-with-exa` v0.3.0（`~/.workbuddy/skills/build-with-exa/`），`EXA_API_KEY` 已配置；`POST https://api.exa.ai/search` 返回 200 |
| **AnySearch** | ❌ 不可用 | MCP 已接 `https://api.anysearch.com/mcp`，但服务端返回 `invalid_api_key`。根因两层：① `mcp.json` 里 `Authorization: Bearer ${ANYSEARCH_API_KEY}` 指向的 key 已失效；② 环境变量 `ANYSEARCH_API_KEY`（失效 key，前缀不记录）本身失效，且按官方优先级 env > 匿名，所以删 header 后仍报错 |
| 内置 WebSearch / WebFetch | ✅ 可用 | 兜底 |

**AnySearch 修复路径（二选一）**：
1. 清掉失效的 `ANYSEARCH_API_KEY` 环境变量 → 重开会话 → 走匿名（限流较低）
2. 换有效 key：`curl -s -X POST "https://api.anysearch.com/v1/auth/email/register" -H "Content-Type: application/json" -d '{"email":"<邮箱>"}'`（会创建账号，需用户同意）

**已做的处置**：`mcp.json` 已移除失效 `Authorization`（备份 `mcp.json.bak-2026-11-07-anysearch-anon`），但**当前会话连接不热重载**，需重开会话生效。
