# DESIGN — deepseekharness_bridge

## 身份锁定

| 表面 | 值 |
| --- | --- |
| 目录名 | `plugin/plugins/deepseekharness_bridge/` |
| `[plugin].id` | `deepseekharness_bridge` |
| `[plugin].name` | DeepSeek Harness 桥接 |
| `[plugin].entry` | `plugin.plugins.deepseekharness_bridge:DeepseekharnessBridgePlugin` |
| 主类 | `DeepseekharnessBridgePlugin` |

包类型 `plugin`（独立功能），不是 `extension`（无宿主），也不是 `adapter`
（不桥接外部协议进来，而是主动调用本地 harness）。

## 要解决什么

N.E.K.O. 的猫娘擅长对话和短任务，但遇到「读这个仓库、跑测试、改这几处代码」
这类长任务时，缺一个真正能干活的 agent 执行体。DeepSeek Harness (dsh) 正好
是干这个的。本插件把 harness 变成猫娘可以驱动的一个能力。

## 明确不做的事

- **不做 N.E.K.O. 侧的 adapter。** 不把外部协议桥进插件系统。
- **不管会话管理。** session ID 只透传给 harness，不额外维护状态。
- **不做 UI。** 纯入口 + LLM 工具，无 Hosted TSX 面板。
- **不碰平台代码。** 全部逻辑在插件目录内。

## 运行方式（三级回退）

优先级从高到低，任何一级不可用都明确报错，不静默降级：

1. **Python SDK** — `import deepseek_harness` 成功时用
   `DeepSeekHarness` 上下文管理器，子进程由 SDK 托管。
2. **`dsh` CLI** — PATH 或配置里能找到 `dsh` 时，起一次性子进程。
3. **都不可用** — `status` 报告缺什么，`run_task` 返回可读错误。

选择在 `startup` 时探测一次并存住，避免第一次调用工具时才发现装错了。

## 入口与 LLM 工具

| 名称 | 种类 | 用途 |
| --- | --- | --- |
| `run_task` | `plugin_entry` | 执行一次 harness 任务 |
| `status` | `plugin_entry` | 自检：SDK/CLI 可用性、历史运行数 |
| `dsh_run_task` | `llm_tool` | 上面那个的模型可调用版本 |

`@llm_tool` 是推荐路径：注册、IPC 路由、停机清理都由 SDK 处理，不需要手写
`register_llm_tool` 的样板代码。

## 安全考量

- **任务正文不进 argv。** CLI 回退路径把任务写临时文件再传路径，避免任务
  内容出现在进程列表里（`ps` / 任务管理器可见）。
- **运行日志脱敏。** `runs.jsonl` 只记会话 ID、任务长度、耗时、调用模式，
  不记任务正文和模型回复。
- **凭据不落在代码里。** API key 交给 DSH 自己的凭据管理，插件不读不写。
- **路径可预期。** workspace 和日志目录默认落在插件自己的 `storage_dir`
  下，不写用户目录根部。

## 已知限制

- CLI 回退路径依赖 `dsh run --prompt-file` 等参数。DSH 处于 developer
  preview，官方明确说会有破坏性变更，参数可能变。SDK 路径更稳。
- SDK 路径下 `repo_path` 被当作隔离 home 的根使用，要求该目录可写。
- 单次任务默认超时 600 秒，长任务需要在配置里调大。

## 验证

`tests/test_smoke.py` 只测纯逻辑（manifest 契约、目录名一致性、async 入口、
无密钥泄漏），不依赖宿主和真实 harness，可在 CI 里跑。
真实联调需要：N.E.K.O. 运行中 + DSH 已安装，见仓库根的 README。
