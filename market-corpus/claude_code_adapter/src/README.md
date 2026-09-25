# Claude Code Adapter

让猫娘指挥 Claude Code 干活的 N.E.K.O 插件。把 Claude Code CLI 注册为猫娘可调用的工具集：全异步执行（无超时限制，专为半小时以上的长任务设计）、完成自动推送结果、执行中同会话纠正、会话续跑控制、cc-switch 风格多供应商切换，并内置 cc-switch 风格的会话管理面板。

## 前置条件

本机已安装 Claude Code CLI：

```bash
npm install -g @anthropic-ai/claude-code
```

## 使用教程：怎么和猫娘说

你不需要记任何工具名，用自然语言跟猫娘说即可，下面是推荐话术：

### 1. 执行任务（全异步，无超时限制）

> 用 Claude Code 帮我把 `D:\myproject` 里的登录页加上表单校验

v0.6.0 起插件只有异步执行入口（同步会被网关 ~60s ReadTimeout 卡死，且 Claude Code 本就擅长长任务）。流程：

1. 猫娘调用 `claude_code_submit` 提交任务，立即拿到 `task_id`，先回复你「任务正在执行」；
2. 任务在后台执行，**没有超时限制**——半小时到一小时以上的长任务完全没问题；
3. 任务完成后插件**自动把结果推送给猫娘**（push_message 注入对话频道），她会主动来汇报；
4. 想提前盯进度就说「任务跑到哪了」，猫娘用 `claude_code_wait`（单次最多 55s，可反复调用，返回含实时进度：已输出消息数/最近动态）或 `claude_code_poll` 查询；想中止就说「取消那个任务」。

### 2. 会话控制（重点）

每个 Claude Code 会话都有一个唯一 UUID（即工具返回的 `session_id`，与 cc-switch 会话管理里看到的是同一个）。本插件以它作为会话主标识，提供三档明确的控制，猫娘不会再「想续会话却开了新会话」：

| 你想做什么 | 怎么跟猫娘说 | 背后行为 |
|---|---|---|
| 接着上次继续（默认） | 「继续刚才的项目改一下 xxx」 | `session_mode=auto`，自动复用同目录会话 |
| 强制开新会话 | 「开个新会话来做这个」 | `session_mode=new` |
| 接回任意历史会话 | 「继续这个会话：`<UUID>`」 | `session_mode=resume` + `session_id`，插件自动定位会话归属目录，无需指定路径 |
| 执行中纠正/追加指令 | 「让 Claude Code 改一下：xxx」 | `claude_code_followup`：任务运行中先中断它，再在同一会话（同一 UUID）里续发纠正指令，已有上下文全部保留；任务已完成则直接续发 |

会话 UUID 从哪来？三个渠道：
1. 每次执行返回的 `session_id` 字段（维持同一会话直接把它传回去）；
2. 插件面板的**会话管理**页：左侧列表点选会话，右侧标题栏有 UUID，点击即可复制；
3. 让猫娘调用 `claude_code_list_sessions(include_history=true)` 列出所有历史会话。

resume 模式对 UUID 很宽容：完整 UUID、唯一前缀（如面板里截断显示的 `d7d17ec1-...`）、甚至整条 `claude --resume <uuid>` 命令都能识别。插件会从 `~/.claude/projects` 存档解析会话归属的项目目录并自动切换过去（Claude CLI 按目录索引会话，目录不对 `--resume` 必然失败）；若你显式传了不一致的 cwd，会报清晰错误提示。

任务失败时插件会自动用 `session_id` 以 `--resume` 续跑而非重开新会话，尽量保留上下文。

### 3. 供应商切换（cc-switch 风格）

> 切换 Claude 供应商到 relay

- `claude_code_list_providers`：列出所有供应商（token 打码显示）
- `claude_code_switch_provider`：切换/注册供应商；传空名称则回退用户全局 `~/.claude` 配置
- `claude_code_remove_provider`：删除供应商

原理与 cc-switch 相同：通过 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` 等环境变量切换 API 端点，但**只注入 CLI 子进程**，不写你的全局 `~/.claude/settings.json`，随时可回退。自定义供应商写入插件目录下的 `providers.json`（已 gitignore），模板见 `providers.example.json`。

## 前端面板：会话管理

面板界面照搬 cc-switch 的「会话管理」页：

- **左侧**：会话列表（标题 + 项目名 + 最近活跃时间），按最近活跃排序
- **右侧**：会话详情——标题、创建时间、**会话 UUID（点击复制）**、项目目录、完整对话内容（用户/AI/工具消息分色气泡展示）
- **删除会话**：右上角红色按钮，确认后删除会话文件及附属数据

数据源是 Claude CLI 原生的会话存档（`~/.claude/projects/**/*.jsonl`），因此不仅猫娘发起的会话，你自己在终端里跑 `claude` 的会话也能看到。

## 工具全集

| 工具 | 用途 |
|---|---|
| `claude_code_submit` | 唯一的执行入口：提交异步任务（无超时限制），返回 task_id，支持 session_mode/session_id；完成后自动推送结果给猫娘 |
| `claude_code_wait` | 提前盯进度（单次最多 55s 可反复调用，运行中返回实时进度） |
| `claude_code_poll` | 单次查询任务状态（运行中含实时进度） |
| `claude_code_cancel` | 取消任务 |
| `claude_code_check_health` | 检查 CLI 是否可用、版本、配置 |
| `claude_code_list_sessions` | 列出会话索引；`include_history=true` 时额外列出 `~/.claude/projects` 所有历史会话 UUID |
| `claude_code_clear_session` | 清除会话索引（按目录或全部） |
| `claude_code_get_config` | 查看当前适配器配置 |
| `claude_code_list_providers` | 列出供应商 |
| `claude_code_switch_provider` | 切换/注册供应商 |
| `claude_code_remove_provider` | 删除供应商 |

## 配置（plugin.toml `[claude_code]` 节）

```toml
command = ""                      # CLI 路径，留空自动检测
model = ""                        # 默认模型，留空用 CLI 默认
effort = ""                       # "" | "low" | "medium" | "high"
max_turns_per_run = 0             # 单次最大轮次，0=默认
dangerously_skip_permissions = true
timeout_sec = 0                   # 单次执行超时（秒），0=不限制（默认，长任务无需限制）
cwd = ""                          # 默认工作目录
append_system_prompt_file = ""    # 附加系统提示文件
skills_dir = ""                   # 技能目录（--add-dir）
max_retries = 1                   # 失败自动重试次数
```

## 开发

此仓库设计为位于：

```text
N.E.K.O/plugin/plugins/claude_code_adapter
```

发布到插件市场时，使用此 GitHub 仓库名：

```text
n.e.k.o_plugin_claude_code_adapter
```

从 N.E.K.O 仓库根目录运行检查：

```bash
uv run python -m plugin.neko_plugin_cli.cli check claude_code_adapter
uv run python -m plugin.neko_plugin_cli.cli check -r claude_code_adapter
```

本地测试：

```bash
python -m pytest tests/ -v
ruff check . && ruff format --check .
```

## 市场发布

推送与 `plugin.toml` 版本匹配的标签以创建 GitHub Release 资源：

```bash
git tag v0.6.0
git push origin v0.6.0
```

`.github/workflows/release.yml` 会上传 `claude_code_adapter.neko-plugin`，在插件市场发布版本时使用该 GitHub Release URL。

## 入口点

```toml
entry = "plugins.claude_code_adapter:ClaudeCodeAdapterPlugin"
```

## 版本历史

### v0.6.3 (2026-08-13)
- **修复面板仍然 0 会话（v0.6.2 后端已扫到 73 条但前端读不到）**：宿主 `call_surface_action` 返回的是包装结构 `{plugin_id, action_id, result}`，真实数据在 `result` 里；面板直接读 `result.sessions` 永远是 undefined。现按内置插件标准做法（neko_live / neko_warthunder）用 `unwrapActionResult` 解包，list/get/delete 三处全修
- **面板体验参考 cc-switch 会话管理页完善**：新增会话搜索框（标题/项目目录/会话 ID）、相对时间（刚刚/X 分钟前/X 小时前）、长消息折叠（>3000 字符折叠到 1500 + 展开按钮）、消息数与匹配数徽标
- **修复长任务偶发“中断但状态永远 running”**：① asyncio StreamReader 默认单行上限仅 64KB，stream-json 长行（长文本/工具输出/图片）超限会抛 ValueError 杀死读取协程，管道写满后 CLI 永久卡死——现调大到 32MB 且超限只截断不中断；② CLI 退出后若子孙进程继承管道写端句柄，readline 永不 EOF，drain 无限等待——现限时 15s，超时取消读取协程继续收尾并记警告日志
- **防御性状态核对**：poll/wait/监控发现任务协程已意外结束但状态仍停在 running 时，强制纠正为 error 并给出提示（可用 session_id 续跑），猫娘不会再无限拿到 running

### v0.6.2 (2026-08-13)
- **修复面板扫描 0 会话**：Steam 启动的插件进程 HOME/USERPROFILE 环境变量与真实用户目录不一致，仅靠 `os.path.expanduser("~")` 会扫错目录。参照 cc-switch（Rust `dirs::home_dir()` 走 Windows Known Folder API）改为多候选解析：Known Folder API / USERPROFILE / expanduser 全部尝试并合并去重，另支持 `CLAUDE_CONFIG_DIR` 环境变量（Claude Code 官方）
- 会话扫描入口增加诊断日志（候选目录 + 扫描数量），便于后续取证

### v0.6.1 (2026-08-13)
- **修复孤儿进程（严重安全问题）**：任务取消/插件重载 shutdown 时只取消了 asyncio 任务，CLI 子进程树从未被杀——Windows 上 `proc.kill()` 只杀 cmd shim，claude.exe 成为孤儿进程在用户不知情下继续自主执行旧指令（甚至内部再 spawn 新进程）。现在统一按进程树强杀（Windows `taskkill /F /T`、POSIX `killpg(SIGKILL)`），取消/超时/shutdown 三条路径全覆盖
- TaskManager.stop() 补上 PENDING 任务取消（此前只取消 RUNNING，队列中的任务关闭后仍会跑）
- **修复面板报“扫描会话失败：Failed to query plugin UI action context”**：插件缺少 `@ui.context(id="dashboard")` provider 注册，面板所有 action 调用直接失败；已按宿主约定注册 UI context，并为会话管理入口注册 `@ui.action`

### v0.6.0 (2026-08-13)
- **全异步化**：移除同步 `claude_code_execute`（会被网关 ~60s ReadTimeout 卡死），`claude_code_submit` 成为唯一执行入口
- **无超时限制**：默认 `timeout_sec = 0`，支持半小时到一小时以上的长任务（修复异步任务硬编码 300s 超时导致长任务必失败的问题）
- **完成自动推送**：任务完成/失败时插件经 push_message 把结果注入对话频道，猫娘自动收到并主动汇报，无需轮询到底
- **实时进度**：poll/wait 运行中返回 progress（已输出消息数/最近动态），猫娘能向用户汇报「任务在干吗」
- `claude_code_wait` 单次夹到 55s 以内（超过会被网关 ReadTimeout），定位为可选的提前盯进度工具
- 结果保留时间 600s → 3600s，最大并发任务数 2 → 3

### v0.5.0 (2026-08-13)
- 新增 `claude_code_followup`：任务执行中发现方向跑偏，可中断当前任务并在同一会话（同一 UUID）里追加纠正指令，已有上下文全部保留；已完成的会话也可直接续发补充指令
- session_id 早期捕获：CLI init 事件到达时立即记录会话 UUID，任务执行中途也能感知，支持中断后同会话续发
- 修复前端面板不可见：plugin.toml 补齐 `[plugin.ui]` + `[[plugin.ui.panel]]` 声明（此前缺少声明导致 cc-switch 风格会话面板不显示）

### v0.4.0 (2026-08-13)
- 确立以 Claude Code 原生会话 UUID 作为会话主标识（与 cc-switch 会话管理中的 UUID 同源），维持/新开/回溯全部围绕它展开
- resume 模式增强：支持任意历史会话，自动从 `~/.claude/projects` 存档解析会话归属目录并切换 cwd（修复跨目录 `--resume` 必失败的问题）；cwd 与会话归属目录不一致时报清晰错误
- UUID 宽容识别：裸 UUID、唯一前缀、`claude --resume <uuid>` 命令形态均可
- `claude_code_list_sessions` 新增 `include_history` / `cwd` / `limit` 参数，可列出所有历史会话 UUID（含标题、resume_command）
- 执行/提交结果新增 `session_title` / `resumed_session_title`，猫娘能说出「接回了哪个会话」
- 面板读取/删除会话入口同步支持 UUID 规范化与前缀匹配

### v0.3.0 (2026-08-13)
- 新增 `claude_code_wait` 智能等待工具：插件内部自适应退避轮询，任务完成立即返回，超时给出 `suggested_next_wait_sec` 续等建议
- 新增会话控制：execute/submit 支持 `session_mode`（auto/new/resume）+ `session_id`，明确控制续跑或新开会话
- 异步任务失败时自动用 `session_id` 续跑而非重开新会话
- 新增 cc-switch 风格的会话管理面板：会话列表、对话详情、UUID 复制、删除会话（数据源为 `~/.claude/projects` 原生存档）
- poll/wait 返回中携带 `session_id`，方便猫娘记忆与续跑
- plugin.toml 描述升级为完整使用教程

### v0.2.0 (2026-08-12)
- 借鉴 cc-switch 新增多供应商切换（list/switch/remove_provider）
- provider env 经子进程注入，不污染全局配置

### v0.1.0 (2026-07-27)
- 初始版本发布
- 通过 @llm_tool 将 Claude Code CLI 注册为猫娘可调用的工具集
- 支持执行项目开发任务：写代码、改 bug、跑测试、查文档
- 支持会话恢复、技能注入、流式输出解析和错误自动恢复
