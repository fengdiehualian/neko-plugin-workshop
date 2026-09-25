# BrowserSkill 浏览器插件

通过本地 `bsk` CLI 与 Chrome/Edge 扩展，让 N.E.K.O Agent 在用户自己的浏览器中完成网页任务。
插件运行时不会下载、安装或更新 BrowserSkill。

## 功能

- 网页搜索、打开和阅读页面，以及多步骤网站流程。
- 点击、填写表单、操作已有登录态，并进行 Web UI 测试。
- 在同一会话中查询实时状态，或追加、替换、取消正在执行的浏览器任务。
- 优先使用 DOM、可访问性树和 HTML 观察页面；信息不足时可临时截图并使用视觉模型辅助观察。
- 支持复用 Agent Window、经确认借用用户标签页、按需新建标签页，以及媒体播放后保留窗口。
- 通过 **BrowserSkill 控制台** 查看 CLI、浏览器、路由、后台任务和脱敏调试事件，并调整执行策略。

## 运行要求

1. 安装与本插件协议匹配的 BrowserSkill 浏览器扩展，并确认扩展显示浏览器已连接。
2. 在 N.E.K.O 中配置可用的 Agent 模型。
3. 多个 Chrome 或 Edge 实例同时连接时，在控制台设置 `browser_label`；只有一个实例时留空即可。

插件包内置官方 **bsk CLI 0.1.10** 的 macOS（Intel、Apple Silicon）、Linux（x64、ARM64）和
Windows x64 可执行文件。默认 `bsk_executable = "bundled"` 会选择当前平台的文件、校验
SHA-256 后直接执行；不会解压文件或运行其他平台的二进制。也可以指定自定义绝对路径，或留空
以从 `PATH` 查找 `bsk`。

## 开发与本地打包

源码仓库不保存这些大体积二进制。开发、测试或打包前，在插件目录下载并校验锁定版本的资源：

```powershell
python scripts/fetch_bsk.py
```

随后从 N.E.K.O 源码根目录调用上游 CLI 打包：

```powershell
uv run python -m plugin.neko_plugin_cli.cli build n.e.k.o_plugin_browser_skill --out n.e.k.o_plugin_browser_skill/dist/browser_skill.neko-plugin
```

若插件目录不在 N.E.K.O 源码树中，将上面命令中的 `n.e.k.o_plugin_browser_skill` 替换为该目录的
绝对路径。CI 也使用同一个上游打包器；`[tool.neko.build]` 会将 `scripts/` 排除在最终插件包外。

## 控制台与执行策略

从插件 UI 打开 **BrowserSkill 控制台**，可配置可执行文件、目标浏览器、路由、守护进程、会话范围、
动作检查点、超时、页面观察额度、视觉回退、标签页和媒体行为。

- 推荐 `routing_mode = "auto"`：根据主对话模型能力选择原生工具或宿主回退通道，并为同一用户请求
  去重。`native`、`fallback` 和 `hybrid` 主要用于诊断。
- 默认 `session_scope = "plugin"` 与 `reuse_existing_window = true` 会复用插件专属的 Agent Window。
  可以改为按角色或按对话复用。
- `max_steps` 是安全检查点，不是任务必须执行的步数。到达上限后会保留会话，由主模型或用户决定继续、
  改目标或关闭。
- 浏览器 daemon 未运行时，可让插件启动已有的本地 daemon 并重试连接；它不会安装或更新 BrowserSkill。
- 控制台保存的设置位于插件数据目录，并覆盖 `[browser_skill]` 默认值。保存新设置会先安全关闭本插件的
  活跃任务与已注册会话。

## 会话与安全

插件默认使用专属的 Agent Window。开启 `allow_additional_agent_tabs` 后，只有用户明确要求时才会新建
标签页；关闭时会继续复用当前 Agent 标签页。开启标签页借用后，插件需要逐次确认才能临时操作普通用户
标签页，并会在任务结束、失败或取消时归还控制权。

借用用户标签页和具有外部后果的操作都需要任务级确认。密码、OTP 验证码、CAPTCHA、支付认证和浏览器
权限提示始终由用户通过 BrowserSkill 的 `request-help` 流程完成。停用插件或关闭会话时，只清理本插件
登记的会话，不会停止其他客户端可能共用的 BrowserSkill daemon。调试日志仅记录脱敏后的调用、会话和
错误诊断信息，不记录页面正文、输入值、凭据、截图或模型原始输出。

---

# BrowserSkill Plugin

This N.E.K.O plugin uses the local `bsk` CLI and a Chrome/Edge extension to let an
Agent perform web tasks in the user's own browser. It never downloads, installs, or
updates BrowserSkill at runtime.

## Features

- Search, open, and read web pages, and complete multi-step website workflows.
- Click controls, fill forms, use an existing signed-in browser state, and test Web UIs.
- Inspect live task status or append, replace, and cancel an in-progress browser task.
- Prefer DOM, accessibility-tree, and HTML observations; optionally use a temporary
  screenshot and a vision model when those observations are insufficient.
- Reuse an Agent Window, borrow a user tab after confirmation, open extra tabs on
  request, and keep a window open after media playback.
- Use the **BrowserSkill Console** to inspect CLI, browser, routing, background-task,
  and redacted debugging state, and to adjust execution policy.

## Requirements

1. Install a BrowserSkill browser extension compatible with this plugin and confirm it
   reports a connected browser.
2. Configure an available Agent model in N.E.K.O.
3. When more than one Chrome or Edge instance is connected, set `browser_label` in the
   console. Leave it empty when there is only one instance.

The package bundles the official **bsk CLI 0.1.10** executables for macOS (Intel and
Apple Silicon), Linux (x64 and ARM64), and Windows x64. The default
`bsk_executable = "bundled"` selects the current platform's executable, verifies its
SHA-256, and executes it directly. It neither extracts binaries nor runs a binary for
another platform. You can configure a custom absolute path or leave the setting empty
to resolve `bsk` from `PATH`.

## Development and local packaging

The source repository does not store these large binaries. From the plugin directory,
download and verify the pinned release assets before developing, testing, or packaging:

```powershell
python scripts/fetch_bsk.py
```

Then build from the N.E.K.O source root through the upstream CLI:

```powershell
uv run python -m plugin.neko_plugin_cli.cli build n.e.k.o_plugin_browser_skill --out n.e.k.o_plugin_browser_skill/dist/browser_skill.neko-plugin
```

If the plugin is outside the N.E.K.O source tree, replace `n.e.k.o_plugin_browser_skill`
with its absolute path. CI uses the same upstream builder, and `[tool.neko.build]`
excludes development-only `scripts/`, `tests/`, `.vscode/`, `.ruff_cache/`,
`.package-tmp/`, `.gitignore`, and `ruff.toml` from the final archive.

## Console and execution policy

Open **BrowserSkill Console** from the plugin UI to configure the executable, target
browser, routing, daemon, session scope, action checkpoints, timeouts, page-observation
budgets, vision fallback, and tab and media behavior.

- `routing_mode = "auto"` is recommended. It selects the native tool or host fallback
  route from the main conversation model's capabilities, and deduplicates the same user
  request. `native`, `fallback`, and `hybrid` are primarily diagnostic modes.
- `session_scope = "plugin"` and `reuse_existing_window = true` reuse the plugin-owned
  Agent Window by default. You can instead reuse by character or conversation.
- `max_steps` is a safety checkpoint, not a required number of actions. When it is
  reached, the session remains available for the main model or user to continue, revise,
  or close it.
- If its daemon is not running, the plugin can start the already-installed local daemon
  and retry the connection. It never installs or updates BrowserSkill.
- Console settings are stored in the plugin data directory and override `[browser_skill]`
  defaults. Saving settings safely closes this plugin's active tasks and registered
  sessions first.

## Sessions and safety

The plugin uses a dedicated Agent Window by default. With `allow_additional_agent_tabs`
enabled, an extra tab is created only when the user explicitly asks for one; otherwise
the current Agent tab is reused. With tab borrowing enabled, each temporary operation on
a normal user tab requires confirmation, and control is returned when the task ends,
fails, or is cancelled.

Borrowing a user tab and consequential external actions require task-scoped confirmation.
Passwords, OTP codes, CAPTCHAs, payment authentication, and browser permission prompts
are always completed by the user through BrowserSkill's `request-help` flow. Disabling
the plugin or closing its sessions cleans up only sessions registered by this plugin; it
does not stop a BrowserSkill daemon that another client may share. Debug logs are
redacted and exclude page bodies, entered values, credentials, screenshots, and raw
model output.
