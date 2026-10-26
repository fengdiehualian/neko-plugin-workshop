# neko-bridge — 把 N.E.K.O. 变成一个 skill，装到本机所有 Agent 软件

本目录是 `agent_bridge` 的**可移植版**，两族能力：

1. **主干：本机 Agent 派发**（`agents` / `dispatch`）—— 把 `agent_bridge/bridge/registry.py`
   的登记表与 `runner.py` 的派发逻辑移植成**只用标准库**的 CLI，**不依赖 N.E.K.O.**，
   所以在 Claude Code / Codex / dsh / omp 里都能直接成立；
2. **附加：N.E.K.O. 侧**（`doctor` / `plugins` / `entries` / `run` / `messages`）—— 需要 N.E.K.O.
   在跑；端口自动发现，硬路径是插件服务器已验证的 `/plugins`、`/runs`、`/plugin/messages`。

`install.py` 一次装到 Claude Code / Codex / dsh / omp / EvoX / QwenPaw / OpenFang / WorkBuddy /
Doubao / Pencil（本机探测到的都装，缺失的跳过）。

```text
skill/neko-bridge/
├── SKILL.md      # Agent 读的说明书（front-matter + 两族用法 + 硬性约束）
├── bin/neko.py   # 纯标准库 CLI：agents / dispatch（主干）+ doctor / plugins / entries / run / messages / say / mcp
├── install.py    # 装到各 Agent：--list / --dry-run（默认）/ --apply
└── README.md     # 就是本文件
```

## 装

```bash
python skill/neko-bridge/install.py --list      # 看本机探测到哪些目标、各自状态
python skill/neko-bridge/install.py --apply     # 真的装（只写 <目标>/skills/neko-bridge/）
python skill/neko-bridge/install.py --mcp-config# 打印 MCP 注册片段
```

只增不改：每个目标里只创建 `neko-bridge/`；目标 skills 目录不存在的（那台机器没装这个 Agent）
直接跳过；带「副本/copy/backup」字样的 workspace 不装。

## 用

```bash
# 主干：本机 Agent（不需要 N.E.K.O.）
python skill/neko-bridge/bin/neko.py agents
python skill/neko-bridge/bin/neko.py dispatch omp "把 README 的拼写检查一遍"
python skill/neko-bridge/bin/neko.py dispatch evox "..." --dry-run

# 附加：N.E.K.O.（需要它在跑）
python skill/neko-bridge/bin/neko.py doctor --json
python skill/neko-bridge/bin/neko.py entries --plugin bot_bridge --json
python skill/neko-bridge/bin/neko.py run 'bot_bridge:bridge_status' --json
```

MCP（给支持 MCP 的 Agent）：

```bash
claude mcp add neko-bridge -- python "<绝对路径>/bin/neko.py" mcp
codex  mcp add neko-bridge -- python "<绝对路径>/bin/neko.py" mcp
```

## 为什么是"skill"而不是"再写一个插件"

- **零依赖**：只用 Python 标准库，任何 Agent 环境都能跑，不需要 pip/uv/node 安装步骤；
- **零副作用**：只读 + 显式调用，不注册后台服务、不改别人的配置；
- **两种接入方式**：会读 skill 的 Agent 直接按 `SKILL.md` 调 CLI；支持 MCP 的 Agent 用 `neko.py mcp`；
- **端口自动发现**：N.E.K.O. 的端口是动态的，`doctor` 会先试默认值再在 48900-48999 里按特征探测。

## 与 agent_bridge 的关系

| 方向 | 谁在做 | 形态 |
|---|---|---|
| N.E.K.O. → 编码 Agent CLI（派活） | `agent_bridge` 插件（entries + 对话期工具） | NEKO 插件 |
| 编码 Agent CLI → N.E.K.O.（调用/读/推） | 本目录的 `neko-bridge` skill | 跨 Agent 的 skill + MCP server |

两者共用同一份"哪些 Agent 在本机、怎么起"的认知（`AGENT_SPECS` 在
`agent_bridge/bridge/registry.py`，skill 里是它的 stdlib 移植，改动请两边同步）。

## 装完之后

* **文件层**：`<各目标>/skills/neko-bridge/{SKILL.md,README.md,bin/neko.py}` —— 立即可用
  （`python <该路径>/bin/neko.py agents` 就能跑）。
* **`skill://neko-bridge`**：本 harness / 各 Agent 的 skill 索引通常是**会话启动时建**的，
  所以**新开一个会话**才会出现；不是安装失败。
