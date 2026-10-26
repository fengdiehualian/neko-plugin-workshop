# agent_bridge

N.E.K.O 本机 Agent 桥接插件。把本机的编码 Agent CLI
（CodeBuddy / Claude Code / dsh / omp / EvoX / OpenCode / OpenClaw / AtomCode / QwenPaw）
桥接成 N.E.K.O 的**运行时入口**，并注册为**对话期 LLM 工具**，
让猫娘在聊天里就能直接把活派给它们。

反方向见 [`skill/neko-bridge`](skill/neko-bridge/)：把 N.E.K.O. 做成可移植 Skill
装到那些 Agent 里，让它们反过来调用 N.E.K.O.。

## 入口

| 入口 | 类型 | 说明 |
|------|------|------|
| `agents_list` | 运行时入口 | 列出 9 个登记 Agent 的解析结果与就绪状态；`probe_versions=true` 时并发探测版本 |
| `agent_run` | 运行时入口 | 把一条任务派给指定 Agent，返回输出；支持 `cwd` / `timeout_seconds` / `model` |
| `agent_run_parallel` | 运行时入口 | 按 JSON 数组并行派发多条任务，受 `allow_parallel` / `max_parallel` 约束 |
| `agent_doctor` | 运行时入口 | 诊断桥接链路：平台、解释器、配置来源、每个 Agent 的解析与版本 |
| `agent_list` | LLM 工具 | 对话期列举可用 Agent（含未登录标注） |
| `agent_run` | LLM 工具 | 对话期派发任务（单次上限 300 秒，受协议硬限） |

## 关键设计

**「文件在」不等于「能用」。** 没登录的 CLI 一样能启动、一样退出码 0。
所以桥接会识别鉴权失败（`bridge/runner.py:_detect_auth_failure`），
把该 Agent 标记为 `needs_login` 并在后续派发中**跳过**，
`agents_list` / `agent_list` 都会如实标注，避免把「没干活」当成「干完了」。

**不经过 `cmd.exe`。** 早期实现对 `.cmd` 垫片走 `cmd.exe /c` 回退，
拼接用户传入的任务文本会构成**命令注入**。现在 `bridge/registry.py`
自行解析 `.cmd` 垫片（`parse_cmd_shim`）、展开变量（`_expand_vars`）、
构造 argv（`build_argv`），全部走 `subprocess` 的列表形式，不经过 shell。
解析不到可执行文件时返回 `AGENT_UNRESOLVED` 而不是回退到 `cmd.exe`。

**版本探测必须并发。** 每个 Agent 最坏 45 秒，9 个串行 405 秒会撞宿主看门狗；
`agents_list` 用 `asyncio.gather` 并发探测，最坏仍在 45 秒量级。

## 配置

`config.example.toml` 是模板。宿主首次运行会把它**逐字节复制**到
`%LOCALAPPDATA%\N.E.K.O\plugins\agent_bridge\config\plugin.toml`，
**之后用户改的是那一份**，改模板不会覆盖已有配置。

```toml
[bridge]
default_timeout_seconds = 600   # 单次派发的默认超时
max_timeout_seconds = 3600      # 上限
max_output_bytes = 400000       # 单次输出上限（超出截断尾部并标注）
default_cwd = ""                # 工作目录，留空不切换
audit_log = ""                  # 留空写到 data/dispatch.jsonl
allow_parallel = true
max_parallel = 4

# 按需覆盖单个 Agent
# [bridge.agents.dsh]
# enabled = true
# timeout_seconds = 900
# exe_override = "C:\\full\\path\\to\\dsh.exe"
# extra_args = ["--verbose"]
```

## 依赖

纯标准库 + 宿主自带 SDK，**无第三方依赖**，无需 `vendor/`。

## 测试

```bash
python -m pytest tests -q
```

`tests/conftest.py` 把本目录注册为命名空间包（只设 `__path__`），
以绕开插件 `__init__.py` 对 `plugin.sdk` 的导入 —— 这样单测不依赖宿主 SDK。

## 目录

```
bridge/registry.py   Agent 规格表 + .cmd 垫片解析 + argv 构造（命令注入防线）
bridge/runner.py     派发执行 + 鉴权失败识别 + 超时与输出截断
bridge/config.py     配置读取与逐 Agent 覆盖
bridge/tools.py      说明性模块（实际入口都在 __init__.py）
ui/panel.tsx         Hosted TSX 面板
skill/neko-bridge/   反向 Skill：把 N.E.K.O. 装进别的 Agent
```

## 许可

见仓库根 `LICENSE`（随 N.E.K.O 项目）。
