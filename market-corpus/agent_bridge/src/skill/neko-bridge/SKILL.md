---
name: neko-bridge
description: |
  Dispatch non-interactive tasks to the local coding-agent CLIs (CodeBuddy, Claude Code, dsh, omp,
  EvoX, OpenCode, OpenClaw, AtomCode, QwenPaw) and, when N.E.K.O. is running, discover and invoke
  its plugins/entries and read its recent messages. Use when the user wants this agent to hand a
  task to another local agent CLI, or to drive/query a running N.E.K.O. (猫娘). Everything is
  stdlib-only Python: no installs, no network beyond 127.0.0.1.
metadata:
  author: "local"
  version: "0.2.0"
  requires:
    bins: ["python"]
  cliHelps: ["python bin/neko.py agents --json", "python bin/neko.py dispatch --help", "python bin/neko.py doctor --json"]
---

# neko-bridge — 本机 Agent 派发 + N.E.K.O. 调用

两族能力，**第一族不依赖 N.E.K.O.**：

| 族 | 动词 | 依赖 | 用途 |
|---|---|---|---|
| **主干：本机 Agent** | `agents` / `dispatch` | 只依赖本机装了那些 CLI | 把任务派给 CodeBuddy / Claude Code / dsh / omp / EvoX / OpenCode / OpenClaw / AtomCode / QwenPaw |
| 附加：N.E.K.O. | `doctor` / `plugins` / `entries` / `run` / `messages` | N.E.K.O. 在跑（127.0.0.1） | 发现并调用它的插件入口、读它最近的消息 |

## 主干一：看看本机有哪些 Agent

```bash
python bin/neko.py agents                 # 9 个 Agent 的安装状态 + 版本（版本有 24h 缓存）
python bin/neko.py agents --no-version    # 只探测安装位置（快）
python bin/neko.py agents --json
```

输出形如 `✓ omp  Oh My Pi  path  omp/18.3.0`；`.cmd` 包装器（evox / qwenpaw）会显示 `cmd-shim`。

## 主干二：把任务派给某个 Agent

```bash
python bin/neko.py dispatch omp "把 README 的拼写检查一遍，只报告问题"           # 非交互式一次性执行
python bin/neko.py dispatch claude "解释 src/main.py 的启动流程" --model sonnet
python bin/neko.py dispatch opencode "跑一下 pytest -q 并总结失败" --cwd /path/to/repo
python bin/neko.py dispatch dsh "写个脚本：统计目录下每种扩展名的行数" --timeout 900
python bin/neko.py dispatch evox "..." --dry-run      # 只回显将执行的命令行（不跑）
```

- 参数拼装按各家形态（`-p` 开关 / 位置参数 / 固定子命令），与 agent_bridge 的 `AGENT_SPECS` 同源；
- 结果给出 `exit_code / duration_ms / stdout / stderr`，`--json` 可选，`--json-output` 会要求 Agent 输出 JSON 并尝试解析；
- 别名可用：`cbc`=codebuddy、`pi`/`oh-my-pi`=omp、`oc`=opencode、`claw`=openclaw、`atom`=atomcode、`qwen`=qwenpaw；
- 子进程会清掉 `PYTHONHOME/PYTHONPATH`（N.E.K.O. 的嵌入式 Python 会让子 Agent 起不来）；
- `--timeout` 超时会**连子孙进程一起清理**（`.cmd` 包装的 Agent 真身是孙进程；只杀直接子进程
  会让它继续跑、继续花额度）—— 超时结果里 `timed_out=true`。

## 附加：N.E.K.O. 侧（需要它在跑）

```bash
python bin/neko.py doctor --json                        # 端口自动发现 + 连通性
python bin/neko.py plugins --running                    # 哪些插件在跑
python bin/neko.py entries --plugin bot_bridge --json    # 入口 + 参数 schema
python bin/neko.py run 'bot_bridge:bridge_status' --json # 调入口（POST /runs → 轮询 → /export）
python bin/neko.py messages --limit 5                   # 最近进对话的消息（GET /plugin/messages）
```

`run` 传参：`--args '{"a":1}'` 或 `--arg a=1`（值按 JSON 解析，解析不了当字符串）。

## MCP（给支持 MCP 的 Agent）

```bash
claude mcp add neko-bridge -- python "<绝对路径>/bin/neko.py" mcp
codex  mcp add neko-bridge -- python "<绝对路径>/bin/neko.py" mcp
```

工具：`neko_agents` / `neko_dispatch`（主干）+ `neko_status` / `neko_plugins` / `neko_entries` /
`neko_run` / `neko_messages` / `neko_say`（N.E.K.O. 侧）。

## 硬性约束（重要）

1. **只走 127.0.0.1**：不访问外部网络，不上传任何内容。
2. **`dispatch` 会真的执行别的 Agent**（可能读写文件、花掉额度）：只在用户要求时用；
   不确定先 `--dry-run` 看命令行。
3. **先 `entries` 看 schema 再 `run`**：入口参数是 JSON schema，别凭猜。
4. **破坏性 / 外发入口要用户确认**：名字带 `delete/drop/remove/clear/stop/uninstall/install/overwrite`
   的入口，先复述「调哪个插件、哪个入口、什么参数」再调。
   **外部平台发送器**（B 站私信/弹幕、微信、QQ、邮件…）尤其危险：实测自动挑选会命中
   `bilibili_danmaku:*`（真会发到站外）。
5. **`say` 目前基本用不了**：本机没有**已验证**的通用"把文本送进对话"入口，所以 `say` 只接受
   显式 `--entry 插件:入口`，默认只 dry-run，外部发送器需 `--allow-external` 且用户明确同意；
   要推内容优先用 `run` 调具体插件（例如 `bot_bridge` 的命令/工具入口）。
6. **N.E.K.O. 没在跑**：`doctor` 会以退出码 2 失败 —— 如实说「N.E.K.O. 没启动」，别假装成功。
7. **「退出码 0」不等于「任务真跑了」**：这些 CLI 未登录时会**退出码 0**、只在 stderr 打一行
   `Authentication required. Please use /login ...`。看到 stderr 里有登录提示就是没干活，
   换一台已登录的 Agent 重试，别把那句提示当成任务结果汇报给用户。
   N.E.K.O 插件侧的 `agent_run` 已经特判：返回 `needs_login=true` + `retry_with=[...]`，
   且该 Agent 会被自动跳过后续派发（重新加载插件才恢复）。
