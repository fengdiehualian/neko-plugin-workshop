<p align="center">
  <h1 align="center">📋 泰拉瑞亚猫娘 · 逐文件说明</h1>
  <p align="center"><a href="../README.md">🏠 README</a> · <a href="../docs/guide.md">📖 使用指南</a> · <a href="MOD_BUILD.md">🔧 Mod 构建</a> · <a href="PLUGIN_ARCHITECTURE_GUIDE.md">📐 架构规范</a></p>
</p>

---

## 📑 目录

- [项目结构总览](#-项目结构总览)
- [入口点层 entries/](#-入口点层-entries)
- [LLM 工具层 llm/](#-llm-工具层-llm)
- [游戏桥 bridge/](#-游戏桥-bridge)
- [核心服务 core/](#-核心服务-core)
- [自主行为与交互 autonomous/](#-自主行为与交互-autonomous)
- [拟人化 polish/](#-拟人化-polish)
- [C# Mod mod/](#-c-mod-mod)
- [前端 static/](#-前端-static)
- [数据流图](#-数据流图)
- [入口点统计](#-入口点统计)
- [开发规则](#-开发规则)

---

## 🗂️ 项目结构总览

```
neko_terraria/
│
├── 📄 __init__.py                 # ★ 主插件类 NTerrariaPlugin
├── ⚙️ plugin.toml                 # 插件元数据与运行时配置
├── 📦 pyproject.toml              # Python 包元数据与依赖
│
├── 🎯 entries/                    # N.E.K.O 入口点层
├── 🔧 llm/                        # LLM 工具层（约 30 个 @llm_tool）
├── 🌉 bridge/                     # 游戏交互桥
├── ⚙️ core/                       # 核心服务
├── 🧠 autonomous/                 # 自主行为与交互引擎
├── ✨ polish/                     # 拟人化
├── 🎮 mod/NekoTerrariaLink/       # tModLoader C# Mod
├── 🖥️ static/                     # 前端面板
├── 📚 docs/  archive/             # 使用指南 / 文档归档
├── 🌍 i18n/                       # 多语言包
└── 📁 data/                       # 运行时数据（配置/缓存）
```

---

## 🎯 入口点层 `entries/`

| 文件 | 职责 |
|:-----|:-----|
| **lifecycle_mixin.py** | `on_load`/`on_unload`；初始化 Agent/大脑/服务；注入宿主 LLM；加载角色人设 |
| **ui_actions.py** | 7 个 `@plugin_entry`：面板状态/连接/指令/配置 |
| **ui_context.py** | 2 个 `@ui.context`：dashboard（猫娘状态）/ guide |
| **memory_entries.py** | 3 个 `@llm_tool`：`remember`/`recall`/`forget`（SQLite） |

---

## 🔧 LLM 工具层 `llm/`

| 文件 | 工具 | 说明 |
|:-----|:-----|:-----|
| **goal_tools.py** | 4 个 | `set_goal`/`interrupt`/`chat`/`command`——目标/打断/闲聊/指令主入口 |
| **action_tools.py** | 23 个 | 挖矿/合成/给物/背包/箱子/任务/配方/攀爬/能力/状态 |
| **intent_parser.py** | — | LLM 意图解析（longterm/finite/stop/chat），失败降级正则 |
| **prompts.py** | — | 猫娘人格、行为边界、工具描述 |

---

## 🌉 游戏桥 `bridge/`

### 协议与连接

| 文件 | 职责 |
|:-----|:-----|
| **connection.py** | TCP 单通道 + **后台读循环**（事件消费）+ `req_id`→future 匹配 |
| **launcher.py** | 启动独立 tModLoader 进程；自动检测 Steam 路径；窗口控制 |
| **mod_link.py** | 全部 mod 命令封装；`navigate_async` 流式导航 |
| **mod_registry.py** | mod 物品注册缓存 |
| **item_npc_dict.py** | 原版物品/NPC ID 映射 |

### 任务与大脑

| 文件 | 职责 |
|:-----|:-----|
| **agent.py** | 中枢：启动/状态循环/事件处理/任务入口/导航 |
| **coordinator.py** | 一句话指令：LLM 意图 → 分发 longterm/finite/stop/chat |
| **executor.py** | 单槽位执行器：owner>auto 优先级、打断、回调 |
| **task_brain.py** | 想(评估)→处理(规划)→做(执行) |
| **task_chain.py** | 多步目标链 |
| **standing_jobs.py** | 长期任务：迟滞带跟随/一直挖/守点 |
| **longterm.py** | 长期任务生命周期：让路/恢复/停止 |
| **intent.py** | 正则意图识别（LLM 降级） |
| **task_inquiry.py** | 任务中决策询问 |

### 专业引擎

| 文件 | 职责 |
|:-----|:-----|
| **mining.py** | 挖矿引擎：按需刷新背包计数 |
| **combat.py** | 战斗：风筝走位/黑名单/低血保命 |
| **equipment.py** | 自动穿戴/使用/转交 |
| **inventory_ops.py** | 物品/箱子操作 |
| **capability.py** | 能力评估：钩锁/垫土/镐子 |
| **planner.py** | 深坑分段爬升规划 |
| **reasoner.py** | 缺材料补救推理 |
| **recipe_book.py** | mod 真实配方书 |
| **world_model.py** | 虚拟背包世界推演 |

---

## ⚙️ 核心服务 `core/`

| 文件 | 职责 |
|:-----|:-----|
| **config_store.py** | 配置中心：DEFAULTS、路径、user_config 读写 |
| **context.py** | 状态锚点/深度上下文/`build_ai_guidance` |
| **service.py** | 状态快照周期推 LLM；绑定事件发射器 |
| **vision.py** | 截图管线：节流/压缩/LLM Vision |

---

## 🧠 自主行为与交互 `autonomous/`

| 文件 | 职责 |
|:-----|:-----|
| **brain.py** | 四层思考：无聊/自保/LLM 决策/交互引擎；executor 回调 |
| **interaction_engine.py** | ★ 交互引擎：场景/情绪/冲动/事件分级/主人追踪/危险衔接/去重 |
| **game_event_emitter.py** | 19 种事件检测：战斗/挖矿/探索/危险/社交/环境 |
| **event_bus.py** | 事件总线：指令打断/受击/游戏事件 |
| **motivation.py** | 5 种动机竞争：采集/战斗/探索/社交/舒适 |
| **internal_state.py** | 能量/无聊/情绪，零 LLM 成本演变 |

---

## ✨ 拟人化 `polish/`

| 文件 | 职责 |
|:-----|:-----|
| **human_timing.py** | 反应延迟正态分布 + 动作时长变异 |
| **imperfections.py** | 回复结巴/手滑/忘词/语气词 |
| **attention.py** | 注意力漂移 |
| **habits.py** | 个人习惯/性格种子 |

---

## 🎮 C# Mod `mod/`

| 文件 | 说明 |
|:-----|:-----|
| **NekoTerrariaLink.cs** | Mod 主逻辑：TCP 监听/命令分发/事件推送/**NekoControlPlayer**(ModPlayer) |
| **NekoConfig.cs** | Mod 配置（监听端口） |
| **Json.cs** | 轻量 JSON 解析/序列化 |

> 命令/事件协议见 [MOD_BUILD.md](MOD_BUILD.md)

---

## 🖥️ 前端 `static/`

| 文件 | 说明 |
|:-----|:-----|
| **index.html** | 米色液态玻璃面板：猫娘状态/指令输入/连接控制/世界信息/行为日志（无血量/背包）；2s 轮询 |

---

## 🔄 数据流图

```
                语音/聊天指令 / UI 按钮 / 自主大脑
                              │
                              ▼
                    NTerrariaPlugin (主类)
      ┌───────────────┬───────────────┬─────────────────┐
      │ llm/ 30工具    │ entries/ 入口  │ autonomous/     │
      │ coordinator   │ ui_actions    │ brain(四层)      │
      │               │ ui_context    │ interaction_engine
      └───────┬───────┴───────┬───────┴────────┬────────┘
              │               │                │
              ▼               ▼                ▼
       ┌────────────────────────────────────────────┐
       │              bridge/ 游戏交互桥             │
       │  agent → coordinator → executor → mod_link │
       │  mining / combat / standing_jobs           │
       │  connection（读循环消费事件）               │
       └────────────────────┬───────────────────────┘
                            │ TCP 9877
                            ▼
       ┌────────────────────────────────────────────┐
       │        NekoTerrariaLink.cs (C# Mod)        │
       │  命令分发 → NekoControlPlayer(ModPlayer)    │
       │  事件推送 → player_status/game_state/combat_hit/nav_*
       └────────────────────────────────────────────┘
```

---

## 🔢 入口点统计

| 类型 | 数量 | 位置 |
|:----:|:----:|:-----|
| `@neko_plugin` | 1 | `__init__.py` |
| `@lifecycle` | 2 | `entries/lifecycle_mixin.py` |
| `@plugin_entry` | 7 | `entries/ui_actions.py` |
| `@llm_tool` | ~30 | `llm/` + `entries/memory_entries.py` |
| `@ui.context` | 2 | `entries/ui_context.py` |

---

## 📐 开发规则

| 规则 | 说明 |
|:-----|:-----|
| Mixin 不含 `__init__` | 属性在主类 `__init__` 初始化 |
| `@neko_plugin` 无参 | SDK 装饰器不接受参数 |
| `@lifecycle` 用 `id=` | 有效值: `startup` / `shutdown` |
| 服务传 `self` | 统一传入 plugin 实例 |
| 修改后删 `__pycache__` | 旧字节码会导致改动不生效 |

### 添加新 LLM 工具

```python
@llm_tool(name="terraria_my_action", description="...", parameters={...})
async def llm_my_action(self, ...):
    return Ok({"output": "..."})
```

1. 在 `llm/` 创建 Mixin（只含方法，不含 `__init__`）
2. 在 `__init__.py` 加入 `NTerrariaPlugin` 继承列表
3. 删除 `__pycache__` 后重启

---

<p align="center">
  📋 <b>泰拉瑞亚猫娘</b> — 让 AI 成为会思考、会动手、会撒娇的队友<br>
  📘 <a href="https://project-neko.online/plugins/">N.E.K.O 文档</a>
</p>
