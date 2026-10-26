# 🐱 猫娘小游戏 · 开发文档（小游戏接入标准）

> ⚠️ **输出契约已升级（重要）**：游戏**不得在 `handle_action` 内调用
> `push_text` / `push_text_image` / `push_help`**——那是旧架构。新架构下游戏
> 只返回 `{facts, outcome, message, images?}`，一切推送由 brain 统一编排
> （详见 [rules.md §2.5](rules.md) 与 [pitfalls.md](pitfalls.md)）。
> 下方「游戏内可用的服务」中的 push 系列仅保留给 on_tick 后台提醒/历史兼容。

## 标准契约

所有小游戏必须实现 `core.contracts.GameAdapter` 抽象基类。

### 最小实现

```python
from neko_arcade.core.contracts import GameAdapter

class MyGame(GameAdapter):
    id = "my_game"
    name = "我的游戏"
    description = "一句话介绍"
    icon = "🎲"

    async def handle_action(self, user_id, cmd, args=None):
        # 只返回结构化结果，不带情感
        return {"facts": [{"kind": "result", "name": "something"}],
                "outcome": "done"}
```

### 结构化结果

`handle_action` 必须返回 3 个字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `facts` | list[dict] | 事实列表（大脑读取，不包含情感）。每条事实 **必须** 含 `kind` 字段 |
| `outcome` | str | 结果类型，用于情绪分类。建议：`caught_legendary` / `caught` / `trash` / `win` / `lose` / `done` |
| `message` | str | 游戏自身结算文本（可选，供 UI 透传） |

不认识指令时返回 `outcome: "unknown"`，大脑会自动触发邀请流程。

### 事实（facts）的 kind 约定

| kind | 含义 | 可选字段 |
|------|------|----------|
| `catch` | 捕获/获得 | name, rarity, size, weight, value |
| `trash` | 垃圾/无用 | item |
| `empty` | 空手/无事 | 无 |
| `event` | 随机事件 | message |
| `sell` | 出售 | count, total_value |
| `equip` | 换装备 | item, type |
| `buy` | 购买 | item, price |
| `tank_upgrade` | 升级 | new_level, capacity |
| `win` / `lose` | 胜负 | 自定义 |

## 完整 GameAdapter 接口

### 必须实现

```python
async def handle_action(self, user_id: str, cmd: str, args: dict = None) -> dict
```

### 可选覆写

```python
# 生命周期
async def on_register(self): ...      # 注册时初始化
async def on_unload(self): ...        # 卸载时清理
async def on_start(self, uid): ...    # 会话开始
async def on_stop(self, uid): ...     # 会话结束
async def get_status(self, uid): ...  # 面板状态

# 行为定制（有默认实现，可覆写）
def get_keywords(self) -> list          # 触发关键词，默认 [name, id]
def get_emotion_templates(self) -> dict # 情感模板，默认从 emotion.json 加载
def classify_event(self, outcome, facts) -> str  # 事件分类
async def on_milestone(self, outcome, facts, memory)  # 里程碑处理
def format_fact_for_card(self, fact) -> tuple  # 卡片格式化
def wants_card(self, outcome, facts) -> bool  # 是否生成卡片
```

### 游戏内可用的服务（由插件注入，游戏只调接口，主插件负责桥接）

```python
# 推送（仅 on_tick 后台提醒/历史兼容；handle_action 用返回 message/images）
await self.push_text("文字")                          # 推送文字到聊天框
await self.push_text_image("文字", image_bytes)        # 推送文字+图片
await self.push_help("标题", image_bytes, "文字")       # 推送帮助文档

# 渲染桥接（推荐；游戏只给数据, 详见 help-config.md）
await self.render_help(topic="纳戒")                    # 本游戏帮助图(可带功能主题)
await self.render_page("今日战绩", subtitle="第3天",     # 自定义页面(官方组件+版式块)
                       blocks=[{"type": "stats", "title": "属性",
                                "items": [{"label": "颜值", "value": 8, "max": 10}]}],
                       commands=[["签到", "每日签到"]], tip="加油喵")
await self.send_page("面板", blocks=[...])              # 渲染 + 推送一条龙
await self.render_card("游戏名", "标题", lines, mood)   # 结果卡片

# 发图桥接（PhotoBridge，主插件统一实现）
await self.pick_photo_for_delivery(category=...)       # 取图(不推), 返回 images
await self.send_photo(user_id, category=...)            # 自主发图(非 handle_action)
await self.send_auto_photo(user_id)                     # 后台自动发图
await self.upload_photo(user_id, name, data_b64=...)    # 上传图片到图库
self.photo_categories()                                 # 图库分类列表
self.build_image("配文", img_bytes, "image/png")        # 构造 images 元素

# 交互
self.tts_note("文字")                                   # 标记 TTS 短句(宿主自动播)
await self.call_llm("prompt")                           # 调用 LLM(主插件限流统计)
```

> ⚠️ **不要自绘图片**：`import PIL` / 起浏览器 / 自带 HTML 模板都不允许
> （CI 有守卫测试）。需要一张图就把内容写成**版式块数据**交给渲染桥接。
> 版式块：`chips` / `table`(可配 `headers`) / `cards` / `stats` / `steps`·`flow` /
> `slots` / `bag` / `text`。`render_html` 已废弃（仅兼容历史游戏）。

### 情绪自动映射

大脑按 `game.classify_event()` 或以下默认规则自动映射猫娘情绪：

| 条件 | 情绪级别 | 渲染方式 |
|------|----------|----------|
| outcome 含 legendary/highlight/big_win 或 facts 含 legendary/？/epic/rare | highlight | LLM 生动生成，推送图片 |
| outcome 含 lose/lowlight/air | lowlight | LLM 安慰/吐槽 |
| 其他 | routine | 游戏模板或通用模板生成 |

### 存档工具

```python
await self.get_user_data(uid, default)     # 读取玩家存档
await self.save_user_data(uid, data)        # 写入玩家存档
```

## 目录结构

```
games/
└── my_game/
    ├── __init__.py          # game_class = "MyGame"
    ├── game.py              # 实现 GameAdapter
    └── data.py              # 数据（可选）
```

> 📌 **零登记原则**：插件启动自动发现 `games/` 下的游戏包，**新增游戏无需修改
> README / 面板 / 主插件任何代码**——面板自动出现游戏卡片，聊天直接可玩。
> 文档里出现的游戏名都只是**示例**，不是登记清单；完整游戏清单以插件面板为准。

## 配置规范

### data/config/{game_id}/

| 文件 | 必需 | 说明 |
|------|:----:|------|
| `config.json` | ❌ | 游戏参数，大脑注入到 `self._config` |
| `help.json` | ❌ | 帮助指令列表，大脑渲染帮助图 |
| `emotion.json` | ❌ | 情感模板，大脑优先使用 |
| `keywords.json` | ❌ | 触发关键词，`parse_input` 匹配用户输入 |

### help.json

**结构由游戏自己决定**（插件只渲染）：写 `groups` 就是分组帮助，只写 `commands`
就是平铺单页；`"auto_groups": true` 表示"请插件代劳分组"。完整契约见
[help-config.md](help-config.md)。

```json
{
  "commands": [["钓鱼", "抛竿钓鱼（可加数量：钓鱼 3）"]],
  "text": "🎣 钓鱼：每日抛竿，收藏鱼获！",

  "title": "钓鱼", "subtitle": "每日抛竿 · 鱼市换鱼蛋",
  "groups": [
    { "id": "fish", "name": "钓鱼玩法", "icon": "star", "aliases": ["钓鱼", "抛竿"],
      "blocks": [{"type": "table", "title": "冷却", "rows": [["钓鱼", "无 CD"]]}],
      "commands": [["钓鱼", "抛竿钓鱼"]] },
    { "id": "tank", "name": "鱼缸收藏", "icon": "bag", "aliases": ["鱼缸"],
      "commands": [{"cmd": "鱼缸", "desc": "查看收藏的鱼", "kind": "view",
                    "blocks": [{"type": "bag", "title": "鱼缸", "cols": 8, "cells": 16}]}] }
  ]
}
```

指令可以写成 `["指令","说明"]`，也可以写成对象（带 `aliases` / `kind` / `params` /
`related` / `blocks`）；带 `blocks` 的指令命中时会出**自己的专属图**。

### emotion.json

```json
{
  "win": ["赢啦！运气站在我这边喵！", "嘿嘿，猜对了！"],
  "lose": ["唔…输了喵", "运气不好，再来一次！"]
}
```

### keywords.json

```json
["钓鱼", "钓", "鱼缸", "鱼市", "售鱼", "鱼竿", "鱼饵", "换竿", "换饵", "鱼店", "购买", "升级鱼缸"]
```

## 与主项目通信

大脑通过 `adapters/` 层统一与主项目通信：

| 渠道 | 适配器 | 说明 |
|------|--------|------|
| 文字推送 | `PushSender.text()` | 推送到聊天和面板 |
| 图片推送 | `PushSender.text_with_image()` | 结果卡片（原生通道优先） |
| 帮助文档图 | `PushSender.help_doc()` | 帮助图（brain.show_help 调用，原生通道优先） |
| 语音（TTS） | 宿主自动播放 chat 通道文字（无需推音频数据） |
| LLM 渲染 | `LLMProvider.call()` | 配置自建，无则模板兜底 |
| 图片渲染 | `ImageRenderer` | 游戏结果卡片、帮助文档图 |

游戏可通过 `self.push_text()` 等服务接口调用，也可选择只返回 facts + outcome 由大脑统一输出。

## 帮助文档图片

游戏在 `data/config/{id}/help.json` 中配置指令列表，大脑渲染为帮助图。

- **结构自己定**：`groups`（可两级）分组帮助 / 只写 `commands` 平铺单页 /
  `"auto_groups": true` 请插件代劳分组
- **每层都能直达**：用户说「修仙帮助」看目录、「修仙帮助 纳戒」看分组、
  「修仙帮助 装备 X」看单条指令（带 `blocks` 的指令还会出专属图）
- **别名**：`aliases` 写用户可能怎么称呼（纳戒/背包/储物），插件做模糊匹配，
  未命中回目录页并给近似建议
- **不要写样式**：配色/排版/图标/主题全在插件端（官方 UI Kit 视觉）

> ⚠️ **命令命名**：`help.json` 的命令和 `keywords.json` 必须用**游戏专属前缀**
> （猫猫状态 / 修仙状态 / 鱼店 / 海龟汤状态…），禁止裸泛化词
> （我的状态 / 背包 / 商店 / 任务 / 成就）。跨游戏共用裸词会串台
> （详见 [rules.md §2.4.5](rules.md) 与 [pitfalls.md §7](pitfalls.md)），
> 改完跑 `tests/test_command_naming.py` 全量扫描冲突。
>
> ⚠️ **分组别名别和子分组抢词**：父分组别名若与子分组同名（父别名「纳戒」、
> 子组也叫「纳戒背包」），用户永远只能命中父页。测试里有防重校验。

## 游戏开关

面板自动显示每个游戏的开关（switch），调用 `set_game_enabled` 入口。

## 快速模板

```python
"""我的游戏。"""

from neko_arcade.core.contracts import GameAdapter, build_fact

class MyGame(GameAdapter):
    id = "my_game"
    name = "我的游戏"
    description = "好玩的小游戏"
    icon = "🎲"

    async def handle_action(self, user_id, cmd, args=None):
        if cmd == "开始":
            return {"facts": [build_fact("win")], "outcome": "win", "message": "你赢了！"}
        return {"facts": [], "outcome": "unknown", "message": ""}
```

## 完整示例：猜硬币游戏

```python
"""猜硬币。"""

import random
from neko_arcade.core.contracts import GameAdapter, build_fact

class CoinFlipGame(GameAdapter):
    id = "coinflip"
    name = "猜硬币"
    description = "和猫娘玩猜硬币正反面"
    icon = "🪙"

    async def handle_action(self, user_id, cmd, args=None):
        c = (cmd or "").strip()
        if not c or c in ("猜硬币", "猜", "硬币"):
            return {"facts": [], "outcome": "done",
                    "message": "说「猜硬币 正」或「猜硬币 反」喵"}
        pick = "正" if ("正" in c) else "反"
        result = random.choice(["正", "反"])
        win = pick == result
        data = await self.get_user_data(user_id, {"stats": {"wins": 0, "plays": 0}})
        data["stats"]["plays"] += 1
        if win:
            data["stats"]["wins"] += 1
        await self.save_user_data(user_id, data)
        return {"facts": [build_fact("win" if win else "lose", pick=pick, result=result)],
                "outcome": "win" if win else "lose",
                "message": f"硬币落下…是{result}！你猜{pick}，" + ("赢啦！" if win else "输啦…")}

    async def get_status(self, user_id="default"):
        data = await self.get_user_data(user_id, {}) or {}
        return {"wins": data.get("stats", {}).get("wins", 0),
                "plays": data.get("stats", {}).get("plays", 0)}
```