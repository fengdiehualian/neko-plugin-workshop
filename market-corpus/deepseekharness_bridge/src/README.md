# deepseekharness_bridge

让 N.E.K.O. 的猫娘把长任务、代码类任务交给本地 **DeepSeek Harness**（`dsh`）执行，
并把结果读回对话。

## 能力

| 入口 | 用途 |
| --- | --- |
| `run_task` | 执行一次 harness 任务，返回最终回复 |
| `status` | 报告 SDK / CLI 可用性与历史运行记录 |
| `dsh_run_task` | 上面 `run_task` 的 LLM 工具版本，供模型在对话中直接调用 |
| `panel_refresh` / `panel_run_task` | 面板专用入口，由 `@ui.action` 暴露 |

## 面板

插件带一个 Hosted TSX 面板（`ui/panel.tsx`），在插件管理器里直接打开，包含：

- **状态总览** — harness 是否就绪、累计运行次数、当前调用方式
- **Harness 详情** — Python SDK 可用性、`dsh` 可执行文件、模型、profile、超时、工作目录
- **运行任务** — 一个表单，直接把任务交给 harness，显示提交结果
- **最近运行** — 最近 20 次运行的会话 ID、任务长度、耗时、调用方式、成败

面板只回传元数据（会话 ID、长度、耗时、模式），不回传任务正文和模型回复。

## 运行方式

插件按以下优先级选择 harness：

1. **Python SDK** — 环境里能 `import deepseek_harness` 时使用，通过
   `DeepSeekHarness` 上下文管理器运行，子进程由 SDK 管理。
2. **`dsh` CLI** — SDK 不可用但 PATH 或配置里能找到 `dsh` 时，以一次性子进程调用。
3. **都不可用** — `status` 会明确报告，`run_task` 返回可读错误，不会静默失败。

## 配置

配置写在运行配置的 `[deepseekharness_bridge]` 段（见 `plugin.toml` 的默认值）：

| 键 | 说明 |
| --- | --- |
| `repo_path` | DeepSeek Harness 源码检出路径；SDK 模式下作为隔离 home 的根 |
| `dsh_executable` | 显式指定 `dsh` 可执行文件 |
| `workspace` | harness 的工作目录，默认为插件存储目录下的 `workspace/` |
| `profile` | 传给 `dsh` 的 profile，默认 `headless`（桌面端装法唯一可用的选项） |
| `model` | 模型 ID，默认 `deepseek-flash`；面板上有下拉可切 |
| `task_timeout` | 单次任务超时（秒），默认 600 |
| `log_dir` | 运行日志目录，默认为 `storage_dir/runs` |

## 模型

面板的「模型」卡片列出当前 DSH 支持的模型，选中即用，之后的任务立即生效。

| ID | 名称 | 说明 |
| --- | --- | --- |
| `deepseek-flash` | DeepSeek-V4.1-Flash | 默认，快且支持图像输入 |
| `deepseek-v4-pro` | DeepSeek-V4-Pro | 更强编码与推理，成本更高 |

这份目录来自 DSH `0.2.1-rc.2` 内置的 `DEFAULT_MODELS`，同时作为面板下拉的
数据源。账号路由启用后，DSH 还会在线拉一份 `/models` 列表与它合并，所以
新模型通常不用改代码就能出现。

## 凭据

默认走 **DeepSeek 账号**，直接复用你已经在 DSH 里完成的登录，不需要 API key。

headless profile 原本把 `credentials` / `authorization` / `deepseek-account`
三项都设成 `disabled: true`，所以插件每次运行会生成一个 patch 覆盖层把它们
重新打开。这是 cordis 后层覆盖前层的正常用法，不改 DSH 自身，也不动你的登录
状态。

想改用 API key 的话，把配置里的 `provider` 设成 `deepseek-official`，
并提供 `DEEPSEEK_API_KEY`。

## 日志

每次运行追加一行 JSON 到 `<log_dir>/runs.jsonl`，只记录会话 ID、任务长度、
耗时和调用模式，不记录任务正文。

## 已知限制

- 账号路由下，模型列表依赖 DSH 能连上 `api.deepseek.com`；离线时回落到内置目录。
- 会话延续依赖 DSH 自身的 session ID；本插件只透传，不维护额外状态。
- CLI 回退路径依赖 `dsh run --prompt-file` 等参数，不同 DSH 版本可能不同，
  若你的版本不一致，请改用 SDK 路径。
