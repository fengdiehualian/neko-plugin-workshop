<p align="center">
  <h1 align="center">📐 泰拉瑞亚猫娘 · 插件架构规范</h1>
  <p align="center"><a href="../README.md">🏠 README</a> · <a href="../docs/guide.md">📖 使用指南</a> · <a href="MOD_BUILD.md">🔧 Mod 构建</a> · <a href="FILES.md">📋 文件说明</a></p>
</p>

> 本规范适用于 **neko_terraria** 插件。基于 N.E.K.O SDK 官方行为和本项目实际调试经验整理。
> **重要**：N.E.K.O. SDK 不原生支持 Mixin `__init__` 链式调用，需遵循本文规范。

---

## 📑 目录

- [项目总览](#-项目总览)
- [`__init__.py` 标准写法](#-__initpy-标准写法)
- [Mixin 文件规范](#-mixin-文件规范)
- [装饰器正确用法](#-装饰器正确用法)
- [服务初始化规则](#-服务初始化规则)
- [变量名规范](#-变量名规范)
- [通信规范（推送式）](#-通信规范推送式)
- [新增功能适配流程](#-新增功能适配流程)
- [常见错误速查](#-常见错误速查)

---

## 🗂️ 项目总览

```
neko_terraria/
├── __init__.py              # 插件主类 NTerrariaPlugin ★ 所有属性在此初始化
├── plugin.toml              # 插件配置（[neko_terraria] 段为唯一生效配置源）
│
├── entries/                 # 入口点层（生命周期/UI/记忆）
├── llm/                     # LLM 工具层 + 意图解析 + 人格
├── bridge/                  # 游戏交互桥
├── core/                    # 核心服务（配置/上下文/状态推送/视觉）
├── autonomous/              # 自主行为 + 交互引擎
├── polish/                  # 拟人化
├── mod/                     # tModLoader 服务端 mod (C#)
└── static/                  # 前端 Hosted UI 面板
```

---

## 📄 `__init__.py` 标准写法

### 核心原则

| 规则 | 说明 |
|------|------|
| **所有属性在 `__init__` 初始化** | 不在 Mixin 的 `__init__` 中设置（不会被调用） |
| **`@neko_plugin` 不带参数** | SDK 签名是无参装饰器 |
| **`@lifecycle` 用 `id=`** | 有效值: `startup` / `shutdown` |
| **服务统一传 `self`** | 所有服务/引擎构造函数接受 plugin 实例 |

### 真实写法

```python
@neko_plugin
class NTerrariaPlugin(
    NekoPluginBase,
    LifecycleMixin,
    UiActionsMixin,
    UiContextMixin,
    GoalToolsMixin,
    ActionToolsMixin,
):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.logger = self.enable_file_logging(log_level="INFO")
        self._agent = None
        self._autonomous_brain = None
        self._config: Dict[str, Any] = {}
        self._loop = None
        self._init_core_services()
```

---

## 🧩 Mixin 文件规范

```
❌ 错误：Mixin 中定义 __init__
✅ 正确：Mixin 只包含方法 + 类常量
```

**原因**：`NekoPluginBase.__init__` 不链式调用 Mixin 的 `__init__`，混合类的 `__init__` 永远不会被执行。

---

## 🏷️ 装饰器正确用法

| 装饰器 | 正确写法 | 错误写法 |
|--------|---------|---------|
| `@neko_plugin` | `@neko_plugin` (无参) | `@neko_plugin(id="...")` ❌ |
| `@lifecycle` | `@lifecycle(id="startup")` | `@lifecycle(event="load")` ❌ |
| `@plugin_entry` | `@plugin_entry(id="nt_connect", name="...")` | 正确 |
| `@llm_tool` | `@llm_tool(name="terraria_mine", ...)` | 正确 |
| 导入 | `from plugin.sdk.plugin import NekoPluginBase, neko_plugin` | `PluginContext` 不存在 ❌ |

### 入口点统计

| 类型 | 数量 | 位置 |
|------|------|------|
| `@neko_plugin` | 1 | `__init__.py` |
| `@lifecycle` | 2 | `entries/lifecycle_mixin.py` |
| `@plugin_entry` | 7 | `entries/ui_actions.py` |
| `@llm_tool` | ~30 | `llm/` + `entries/memory_entries.py` |
| `@ui.context` | 2 | `entries/ui_context.py` |

---

## ⚙️ 服务初始化规则

| 组件 | 正确 | 错误 |
|------|------|------|
| TerrariaAgent | `TerrariaAgent(self)` | `TerrariaAgent(config)` ❌ |
| AutonomousBrain | `AutonomousBrain(self)` | `AutonomousBrain(self._config)` ❌ |
| TerrariaService | `TerrariaService(self, push_message=...)` | 传 `config` ❌ |
| 其它 bridge 引擎 | 统一传 `self` 或 `self._agent` | 多参 ❌ |

---

## 🏷️ 变量名规范

| 变量 | 正确命名 | 说明 |
|------|---------|------|
| 猫娘 Agent | `self._agent` | 主类中存储 |
| 自主大脑 | `self._autonomous_brain` | 长期行为决策 |
| 配置字典 | `self._config` | 来自 `plugin.toml` |
| 事件循环 | `self._loop` | 异步任务调度 |

---

## 📡 通信规范（推送式）

### 数据流

```
Mod 主动推事件（后台读循环消费）：
  player_status(1s) / game_state(2s) / combat_hit / boss / invasion / nav_*
Python 按需请求（req_id→future 匹配）：
  move / use_item / navigate_stream / craft / give / get_inventory / get_state ...
```

### 关键点

- **后台读循环**：`bridge/connection.py` 的 `_read_loop` 独占读流——事件推给回调，带 `req_id` 的响应塞回对应 future
- **按需请求与事件推送并行**：`_mod_lock` 只串行发送
- **背包不随推送**：C# `PushGameState` 已移除背包，Python 每 30s 兜底 + 挖矿/查询时按需 `get_inventory`
- **C# 控制走 ModPlayer**：`NekoControlPlayer.PreUpdateMovement` 每帧应用 control 状态（状态型命令）
- **唯一生效配置**：`data/config/user_config.json`

---

## 🚀 新增功能适配流程

```
1. 创建 entries/ 或 llm/ 下的新 Mixin（只含方法，不含 __init__）
   ↓
2. 在对应包的 __init__.py 添加导入和 __all__
   ↓
3. 在主 __init__.py 添加 Mixin 导入和继承
   ↓
4. 如需新属性，在主 __init__.py 的 __init__ 中初始化
   ↓
5. 删除 __pycache__ 后重启 N.E.K.O 验证加载
```

---

## ⚠️ 常见错误速查

| 错误 | 原因 | 修复 |
|------|------|------|
| `TypeError: lifecycle() got unexpected keyword 'event'` | 用了 `event=` | 改为 `id=` |
| `TypeError: neko_plugin() got unexpected keyword 'id'` | 传了参数 | 移除所有参数 |
| `AttributeError: '_agent'` | Mixin `__init__` 未执行 | 属性移到主 `__init__` |
| `ModuleNotFoundError` | 导入路径层级错误 | 检查相对导入 `..bridge.xxx` |
| `TypeError: __init__() missing 'plugin'` | 服务没传 self | 改为 `Xxx(self)` |
| mod 回执串线 / 状态错乱 | 无 req_id 关联 + 写流未加锁 | 注入并校验 `req_id`，`Send` 加 `lock` |
| 改动不生效 | 旧 `.pyc` 残留 | 删除 `__pycache__` 后重启 |

---

<p align="center">
  📐 <b>泰拉瑞亚猫娘</b> — 让 AI 成为会思考、会动手、会撒娇的队友<br>
  📘 <a href="https://project-neko.online/plugins/">N.E.K.O 文档</a>
</p>
