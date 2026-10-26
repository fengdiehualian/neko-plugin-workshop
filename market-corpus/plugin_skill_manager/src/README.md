# plugin_skill_manager

N.E.K.O Agent Skill 插件（接口层）。统一管理**两类**符合
[Agent Skills 规范](https://agentskills.io) 的 Skill：

| 类别 | 来源 | 行为 |
|------|------|------|
| **自带 builtin** | 插件目录 `skills/<name>/SKILL.md`，随包分发 | 4 个可执行 Skill 注册为运行时入口 |
| **外部 external** | 用户文件夹里各 Agent 的 skills 根 | 挂进 `skills/` 统一登记；**默认不注册入口** |

## 自带的四个可执行 Skill

| Skill | 功能 | 转发目标（plugin_knowledge_base） |
|-------|------|-----------------------------------|
| `knowledge-search` | 语义搜索知识库 | `kb_search` |
| `knowledge-import` | 导入文件到知识库 | `kb_import` |
| `database-query` | 查询热数据表 | `kb_hot_list` |
| `database-export` | 导出热数据为文件 | `kb_hot_export` |

另有 `neko-plugin-dev`：随包分发的「说明书」Skill，只登记不注册入口。

## 外部 Skill：目录联接（junction），零拷贝

用户 skill 动辄几十上百 MB（`pptx-swarm` 带一个 261 MB 的 `scripts/kimi_ppt_dsl.pyz`），
而宿主同步是**整树复制**，复制一份就是几秒到几十秒的启动开销。

因此 `skill_sync` 用 **Windows 目录联接**：`<plugin>/skills/<name>` 只是一个指向
`~/.agents/skills/<name>` 的重解析点。零拷贝、源目录改了立刻生效、不占额外磁盘。
普通符号链接需要管理员权限（本机实测失败），`mklink /J` 不需要。

扫描的根（`engine/external.py` 的 `ROOT_PATTERNS`，存在才采用）：

```
~/.agents/skills          ~/.claude/skills        ~/.codex/skills
~/.zcode/skills           ~/.workbuddy/skills     ~/.pencil/skills
~/.omp/agent/skills       ~/.evox/agent/skills    ~/Doubao/skills
~/DoubaoWork/skills       */.agent/skills         */.agents/skills  …
```

同名多副本按上面的**顺序**取第一份（`.agents` 是 agent_bridge 安装器认定的公共位）；
同名冲突记进 `notes`。`node_modules`、备份目录一律跳过。

## 入口

| 入口 | 说明 |
|------|------|
| `skill_list` | 列出全部 Skill、来源、挂载方式、启用/注册状态（可按 `origin` 过滤） |
| `skill_sync` | 重扫用户文件夹各 skills 根并挂载（junction 优先） |
| `skill_validate` | 按规范逐份校验 SKILL.md（自带 + 外部），结构化错误，不崩溃 |
| `skill_enable` / `skill_disable` | 持久化启用状态；**有路由的**才注册/注销运行时入口 |
| `skill_reload` | 重新扫描 + 按启用状态注册（热更新 SKILL.md） |
| `skill_invoke` | 手动触发可执行 Skill（等价 Agent 调用路径，含统计） |

启动时（`auto_register`）按持久化状态把**有路由且已启用**的 Skill 注册为动态入口
（`register_dynamic_entry`）。

## 为什么外部 Skill 默认不注册入口

绝大多数用户 skill 是「给 Agent 看的指令包」，没有可执行语义。`SKILL_ROUTES` 是硬编码的
KB 路由表，外部 skill 没有路由，注册成入口只会让 Agent 拿到一堆
`has no KB route` 的错误，污染宿主入口表。

要放行：在 `<用户数据目录>/plugins/plugin_skill_manager/config/plugin.toml` 里
把 `builtin_only` 设为 `false`，并给该 skill 补一条路由：

```toml
[skill_manager]
builtin_only = false

[skill_manager.routes.my-skill]
entry = "kb_search"
params = ["query", "top_k"]
```

## 调用统计

每次**入口调用**记录耗时与成败，经知识库插件转发写入数据库插件的 `skill_stats` 表
（`Skill -> KB -> DB` 链路，本插件不直接操作数据库）：

```sql
SELECT skill, calls, errors, total_ms, last_called_at FROM skill_stats;
```

用 `kb_query_skill_stats` 查询（含 `avg_ms`）。

## 依赖

- **plugin_knowledge_base**（调用期依赖）：四个可执行 Skill 转发给它
- 自身零第三方依赖（frontmatter 解析优先宿主自带 pyyaml，缺失时使用内置的
  最小 YAML 子集解析器；junction 用 `cmd /c mklink /J`）

## 测试

```bash
cd plugin/plugins/plugin_skill_manager
python -m pytest tests -q          # registry + external 单测（20 项）
python tests/stub_run.py           # 桩 SDK 全链 Skill->KB->DB + 外部挂载（28 项）
```

> 注：入口层测试需要宿主 SDK 在 `sys.path` 上。若从别处跑 pytest 报
> `ModuleNotFoundError: No module named 'plugin'`，把 `PYTHONPATH` 指到 N.E.K.O 仓库根
> （与 plugin_database / plugin_knowledge_base 的行为一致）。
