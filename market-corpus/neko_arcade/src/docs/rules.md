# 🐱 猫娘小游戏 · 插件规则（接入标准）

> 本插件是**游戏大脑总管家**。所有小游戏必须**适配本插件**，而非插件适配小游戏。
> 游戏提供玩法逻辑（facts + outcome），情感、图片、语音、帮助、配置、开关、输入路由全部由大脑统一负责。
>
> 📖 **适配新游戏前必读 [docs/pitfalls.md](pitfalls.md)（避坑手册）**：
> 双重回复、图片可见性通道、会话打断、后台发图门控、LLM 路由、测试随机性等
> 全部踩过的坑与解法都在那里，避免重复踩坑。

## 1. 小游戏形式

每个小游戏一个文件夹，统一放入 `games/` 目录，由 `registry.discover()` 自动扫描发现——
**零登记**：新增游戏无需改主插件、README 或面板，启动后自动出现：

```
games/
├── fishing/          # 复杂游戏：多个文件
│   ├── __init__.py
│   ├── game.py
│   └── data.py
└── coinflip/         # 简单游戏：两个文件
    ├── __init__.py
    └── game.py
```

`__init__.py` 必须声明 `game_class = "ClassName"`，指向 GameAdapter 子类。

## 2. 硬性契约（必须遵守）

### 2.1 游戏必须实现

```python
async def handle_action(self, user_id: str, cmd: str, args: dict | None = None) -> dict
```

返回值必须含：

| 字段 | 必须 | 说明 |
|------|:----:|------|
| `facts` | ✅ | list[dict]，每条事实必须含 `kind` 字段 |
| `outcome` | ✅ | str，结果类型，大脑据此分类情绪。不认识指令时返回 `"unknown"` |
| `message` | ❌ | str，游戏自身结算文本 |

### 2.2 facts 的 kind 约定

| kind | 含义 | 大脑映射情绪 |
|------|------|--------------|
| `catch` | 捕获/获得 | rarity=legendary/？/epic/rare → 高光 |
| `trash` | 垃圾 | 平淡 |
| `empty` | 空手 | 平淡 |
| `win` / `lose` | 胜负 | win→高光，lose→低谷 |
| `event` | 随机事件 | 平淡 |
| `sell` / `buy` / `equip` / `tank_upgrade` | 交易/装备 | 平淡 |

### 2.3 outcome 约定

| 值 | 含义 |
|----|------|
| `caught_legendary` / `big_win` / `highlight` | 高光 → LLM 渲染 + 图片卡片 |
| `lose` / `lowlight` / `air` | 低谷 → LLM 安慰 |
| `unknown` | 不认识的指令 → 大脑触发邀请流程 |
| 其他 | 日常 → 游戏模板或通用模板 |

### 2.4 禁止事项

- ❌ 游戏内不得自行管理情感话术（"好开心"），只报事实
- ❌ 不得直接读写主项目配置
- ❌ 不得直接调用 `push_message`（应使用 `self.push_text()` 等服务接口）
- ❌ 不得在 `games/__init__.py` 中手动导入游戏（自动发现）
- ❌ **不得自绘图片**：`import PIL` / `ImageDraw` / 起浏览器 / 自带 HTML 模板 /
  调用 `render_html` 一律禁止——渲染是插件的事，游戏只给数据
  （CI 守卫：`tests/test_render_bridge.py::test_games_do_not_render_images_themselves`）
- ✅ 用 `self._config`（大脑注入的配置）、`get_user_data`/`save_user_data`（存档）
- ✅ 可用 `self.push_text()` / `self.render_card()` / `self.render_page()` 等服务接口
- ✅ 出图需求写成**版式块数据**交给渲染桥接（`blocks`: chips/table/cards/stats/
  steps/flow/slots/bag/text），样式与主题由插件统一决定

### 2.4.5 ⚠️ 命令命名规范：每个游戏的指令独立，禁止裸泛化词（重要）

**核心原则：命令是游戏专属的，不能跨游戏串台。** 玩家输入 → `parse_input`
子串匹配（`keyword in input`）→ 路由到游戏。若两个游戏 keywords.json
里有同一个裸词（如「背包」「状态」「商店」「任务」「成就」「技能」），
无会话时先到先得 → 串游戏（玩家想查修仙状态，却被路由去钓鱼）。

**规范**：

| 反例（裸泛化词） | 正例（游戏专属前缀） |
|------------------|----------------------|
| `我的状态` | 猫猫进化→`猫猫状态`、修仙→`修仙状态`、海龟汤→`海龟汤状态` |
| `我的背包` | 猫猫进化→`猫猫背包`、修仙→`我的纳戒` |
| `商店` | 钓鱼→`鱼店`、修仙→`市场 买 X`、猫猫进化→`猫猫商店` |
| `我的任务` | 猫猫进化→`猫猫任务`、修仙→`每日任务` |
| `成就` | 猫猫进化→`猫猫成就`、修仙→`成就`（修仙语境下唯一） |

**硬性规则**：

1. **keywords.json 不许出现与其他游戏重复的词**（含子串关系：A 的短词
   in B 的长词也算冲突，如「探索」in「探索秘境」→ 修仙的探索秘境会被
   猫猫进化的探索截胡）。
2. 命令名带游戏专属前缀/语境词（猫猫X、修仙X、轮盘X、鱼X、海龟汤X…），
   让玩家输入无歧义。
3. 游戏内 `_RULES` 可保留旧别名（会话内规则 3 兜底仍可用），但
   **keywords.json 必须干净**——它是唯一的路由依据。
4. 写完 help.json / keywords.json 后跑 `tests/test_command_naming.py`，
   它会扫描全量跨游戏关键词冲突（重复 + 子串截胡），不通过就是有串台词。

**判定口诀**：任何命令词，玩家说出口时应当**只能指向一个游戏**；指向不
确定 = 命名不规范，必须加前缀。

### 2.5 ⚠️ 输出契约：游戏适配插件，不参与推送（最重要，必须读懂）

**核心原则：游戏只返回结构化结果，一切推送由主插件（brain）统一编排。**

`handle_action` 的返回契约：

```python
return {
    "facts": [...],          # ✅ 必须
    "outcome": "win",        # ✅ 必须
    "message": "结算文本",    # 用户可见文本(由 brain 统一推送)
    "images": [              # 可选: 需要展示的图片数据(由 brain 统一推送)
        {"text": "配文", "bytes": img_bytes, "mime": "image/png"},
        # 或 {"text": "配文", "url": "http://..."}
    ],
}
```

**为什么（双重回复的教训）：**

游戏若自己 `push_text`/`push_text_image` 推送（旧架构），又返回 `message`/`summary`，
宿主会把 summary 喂给主 LLM，LLM 复述一遍 → 用户看到两条几乎一样的话。

**新架构彻底解决：**

| 谁 | 做什么 |
|----|--------|
| 游戏 | 只返回 `{facts, outcome, message, images?}`，**不调用任何 push 方法** |
| brain | 统一推送：有 images → 推「配文/文本 + 图片」一次；无 images → 推 message 一次 |
| brain | 统一生成 summary（用情感模板 neko_text，与用户已见内容不同），宿主 LLM 自然演绎不复述 |
| brain | 统一处理高光卡片、状态锚、防打断、发图桥接 |

**游戏可用的桥接（通过 GameAdapter，全部由主插件实现，游戏只调接口）：**

| 能力 | 用法 |
|------|------|
| 取图(不推) | `await self.pick_photo_for_delivery(category=...)` → 返回 images 数据交 brain 推 |
| 发图(后台/工具) | `await self.send_photo(...)`（仅 on_tick/LLM 工具等非 handle_action 路径） |
| 后台自动发图 | `await self.send_auto_photo(user_id)` → 只发本地图库, 配文随机 |
| 图库分类 | `self.photo_categories()` → 分类名列表 |
| 上传图片 | `await self.upload_photo(user_id, name, data_b64=..., category=...)` → 存图库 |
| 渲染卡片 | `await self.render_card(...)` → 生成 bytes 放进 images |
| 渲染帮助图 | `await self.render_help(topic="纳戒")` → 本游戏帮助图(多页 PNG bytes) |
| 渲染自定义页 | `await self.render_page(title, blocks=..., commands=..., tip=...)` → 单页 PNG |
| 渲染并推送 | `await self.send_page(title, blocks=...)` → 渲染+推送一条龙 |
| 构造图片数据 | `self.build_image(text, bytes, mime)` → 生成 images 元素 |
| 语音标记 | `self.tts_note(text)` → 标记短句, 宿主自动 TTS 播放 |
| 调用 LLM | `await self.call_llm(prompt)` → 主插件统一限流/统计 |

**禁止：**

- ❌ **自绘图片**（PIL / 浏览器 / HTML 模板 / `render_html`）——用渲染桥接给数据；
  `render_html` 仅保留兼容历史游戏, 调用会打废弃告警
- ❌ `handle_action` 内调用 `push_text`/`push_text_image`/`push_text_image_url`/`push_help`
  （这些方法仅保留给 on_tick 后台提醒/历史兼容，新游戏 handle_action 不得使用）
- ❌ 直接调用 `plugin.push_message` / 读宿主 ctx 私有属性（如 `ctx.user_id`）
- ❌ 返回 `pushed` / `summary` 字段（已废除，brain 统一处理）
- ❌ 自己推 audio part（宿主不支持, 见 [pitfalls.md §2.5](pitfalls.md)）

**判断口诀：** 一条游戏动作，用户最多看到「游戏输出 + 猫娘自然回应」两条。
游戏自身不推送任何内容——所有用户可见输出都经 brain 一条通道发出。

## 3. 配置统一管理

配置文件在 `data/config/{game_id}/`：

```
data/config/
├── fishing/
│   ├── config.json       # 游戏参数
│   ├── help.json         # 帮助数据
│   ├── emotion.json      # 情感模板（可选）
│   └── keywords.json     # 触发关键词（可选）
└── coinflip/
    ├── config.json
    ├── help.json
    ├── emotion.json
    └── keywords.json
```

### config.json

```json
{
  "max_plays_per_day": 20
}
```

游戏内读取（启动时大脑自动注入）：

```python
self._config.get("max_plays_per_day", 20)
```

### help.json（帮助文档）

```json
{
  "commands": [
    ["猜硬币 正", "猜硬币正面"]
  ],
  "text": "🪙 猜硬币：和猫娘比手气！"
}
```

玩家说「帮助」时，大脑读取 help.json → 渲染成帮助图推送
（分页多页图，单页 ≤~600px，原生图片通道优先）。

### emotion.json（情感模板，可选）

```json
{
  "win": ["赢啦！运气站在我这边喵！", "嘿嘿，猜对了！"],
  "lose": ["唔…输了喵", "运气不好，再来一次！"]
}
```

大脑 `EmotionRenderer` 优先使用游戏模板，无匹配时使用通用兜底模板。

### keywords.json（触发关键词，可选）

```json
["钓鱼", "钓", "鱼缸", "鱼市", "售鱼", "鱼竿", "鱼饵", "换竿", "换饵", "鱼店", "购买", "升级鱼缸"]
```

`parse_input()` 遍历游戏关键词匹配用户输入。无此文件时默认使用 `[name, id]`。

## 4. 存档

```python
data = await self.get_user_data(uid, {})    # 读
await self.save_user_data(uid, data)         # 写
```

存档自动按 `game:{id}:user:{uid}` 隔离，跨重启保留。

## 5. 生命周期（可选）

```python
async def on_register(self): ...   # 注册时
async def on_unload(self): ...     # 卸载时
async def on_start(self, uid): ... # 会话开始
async def on_stop(self, uid): ...  # 会话结束
async def get_status(self, uid): ... # 面板状态
```

## 6. 大脑提供的公共能力

### 配置与数据

| 能力 | 用法 |
|------|------|
| 配置 | `self._config` |
| 帮助数据 | `self._help` |
| 存档 | `self.get_user_data` / `self.save_user_data` |
| 元数据 | `self.id` / `self.name` / `self.icon` |

### 服务接口（由 `bind_services()` 注入）

| 能力 | 用法 |
|------|------|
| 渲染卡片 | `await self.render_card("游戏名", "标题", lines, mood)` → 生成 bytes 放进 images |
| 渲染帮助图 | `await self.render_help(topic="纳戒")` → 本游戏帮助图多页 bytes（主题可选） |
| 渲染自定义页 | `await self.render_page(title, blocks=..., commands=..., tip=...)` → 单页 PNG |
| 渲染并推送 | `await self.send_page(title, blocks=...)` → 渲染 + 推送一条龙 |
| 渲染头像 | `await self.render_avatar("excitement", 128)` |
| 取图(交brain推) | `await self.pick_photo_for_delivery("可爱")` → 返回 images 数据 |
| 构造图片数据 | `self.build_image("配文", img_bytes, "image/png")` |
| 语音（TTS） | `self.tts_note("文字")` 标记短句, 宿主自动播放 chat 文字 |
| 调用 LLM | `await self.call_llm("prompt")` |
| ~~自绘 HTML~~ | ~~`render_html(html, css=...)`~~（已废弃：游戏不参与渲染，改 `render_page`） |
| ~~推送文字~~ | ~~`push_text`~~（已废弃，仅 on_tick 后台提醒/历史兼容；handle_action 用 message 返回） |
| ~~推送图片~~ | ~~`push_text_image`~~（已废弃，handle_action 用 images 返回） |

### 行为定制（可选覆写）

| 能力 | 用法 |
|------|------|
| 触发关键词 | `def get_keywords(self) -> list` |
| 情感模板 | `def get_emotion_templates(self) -> dict` |
| 事件分类 | `def classify_event(self, outcome, facts) -> str` |
| 里程碑处理 | `async def on_milestone(self, outcome, facts, memory)` |
| 卡片格式化 | `def format_fact_for_card(self, fact) -> tuple` |
| 卡片开关 | `def wants_card(self, outcome, facts) -> bool` |

游戏**不需要**（也不允许）自己实现：开关管理、情感渲染、输入路由、邀请流程、帮助渲染、记忆。

## 7. 接入检查清单

- [ ] 实现 `GameAdapter`，id/name/description/icon 齐全
- [ ] `handle_action` 返回 facts + outcome，不认识指令返回 `outcome="unknown"`
- [ ] 不自行管理情感话术
- [ ] 配置和帮助放 `data/config/{id}/`
- [ ] 可选：`emotion.json` 放情感模板，`keywords.json` 放触发关键词
- [ ] `games/{id}/__init__.py` 声明 `game_class = "ClassName"`
- [ ] 不在 `games/__init__.py` 中手动导入
- [ ] **双重回复检查**（见 2.5）：若在 `handle_action` 里 `push_text`/`push_text_image` 自推了用户可见内容，确认返回的 `message`/`summary` **不是同一文本**；不自推时只返回 `message` 即可

## 8. 与主项目通信

大脑通过 `adapters/` 层统一完成：

| 渠道 | 适配器 |
|------|--------|
| 文字推送 | PushSender.text |
| 图片推送（结果卡片） | PushSender.text_with_image（原生通道优先） |
| 帮助文档图 | PushSender.help_doc（brain.show_help 统一调用，原生通道优先） |
| 语音（TTS） | 主项目自动播放 chat 文字（插件只保证短句） |
| LLM 渲染 | LLMProvider（配置自建，无则模板兜底） |
| 猫娘表情图 | ImageRenderer.render_neko_avatar |

游戏可通过 `self.push_text()` 等服务接口调用，或只返回 facts + outcome 由大脑统一输出。

## 9. 输入路由与邀请机制

### 输入匹配

```
用户说"钓鱼"   → parse_input("钓鱼")   → 遍历游戏关键词 → 匹配 fishing → cmd="钓鱼"
用户说"我想钓鱼" → parse_input("我想钓鱼") → 匹配 fishing → cmd="我想钓鱼"
```

### 邀请流程

```
用户说"我想钓鱼" → game.handle_action() 不认识 → 返回 outcome="unknown"
    → brain 检测到 unknown → 调用 _invite_game()
    → 推送邀请到聊天框（含游戏指令列表）
    → 返回 invitation + game_commands 给 LLM
```

## 10. 简单游戏模板

`games/my_game/__init__.py`：

```python
"""我的游戏包。"""

game_class = "MyGame"

from .game import MyGame

__all__ = ["MyGame"]
```

`games/my_game/game.py`：

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
            # ✅ 游戏只返回结构化结果, brain 统一推送(游戏不参与推送)
            return {"facts": [build_fact("win")], "outcome": "win",
                    "message": "你赢了！"}
        return {"facts": [], "outcome": "unknown", "message": ""}
```

**需要推送图片/卡片的游戏（返回 images 数据）：**

```python
async def handle_action(self, user_id, cmd, args=None):
    if cmd == "开始":
        img = await self.render_card("我的游戏", "开局", [("赢啦", "win")])
        images = []
        if img:
            # 构造 images 数据(brain 统一推送), 游戏不 push
            images.append(self.build_image("你赢了!", img, "image/png"))
        return {"facts": [build_fact("win")], "outcome": "win",
                "message": "你赢了！", "images": images}
    return {"facts": [], "outcome": "unknown", "message": ""}
```

> 关键：游戏**绝不调用** `push_text`/`push_text_image`；所有用户可见输出
> 都通过返回 `message` + `images` 交给 brain 统一编排，从架构上杜绝双重回复。

---

## 统一调配：游戏只给数据，陪伴/LLM/推送/渲染全归本体

> 这一节是 v0.6 起的新规矩。**新游戏必须遵守**；存量游戏按 `tests/test_adapter_contract.py`
> 的白名单逐个迁移，清完删白名单，测试即全量强制。

### 1. 四条通道只有一个主人

| 通道 | 唯一归属（插件本体） | 游戏只提供 |
| --- | --- | --- |
| 陪伴/台词 | `core/companion.py`（事件→台词：LLM + LRU 缓存 + `emotion.json` 兜底） | 事件键 + 局面描述 |
| 心情 | `core/persona.py` 的 `Mood`（五条情绪弧） | 事件（由本体映射到情绪） |
| LLM 调用 | `core/llm_gateway.py`（场景标签 + 缓存 + 限流 + token 统计） | `llm_scene("soup.puzzle", 变量)` |
| 出图/渲染 | `RenderBridge` / `HelpRenderer`（棋盘块、卡片、帮助图） | 数据块（`blocks`/`rows`） |
| 推送/会话/记忆 | `core/brain.py`（推送编排、状态锚、切换确认、`memory`） | 无 |

### 2. 游戏里**禁止**出现（契约测试会拦）

```python
self.call_llm(...)        # ✗ 绕过 llm_gateway 直接调 LLM
self._llm                 # ✗ 直接摸 provider
self.push_text / push_text_image / push_help
self.send_page / render_html / render_card
_QUIPS = { ... }          # ✗ 台词表要放 data/config/<game>/emotion.json
```

要说话就**返回数据**，由本体说：

```python
return {"facts": [{"kind": "catch", "name": "鲤鱼", "rarity": "uncommon"}],
        "outcome": "caught_rare",
        "message": "",          # 可选：机械性文本（推给用户看，不触发 AI）
        "images": [...]}        # 可选：`self.build_image(text, png)`
```

本体随后会：按 `outcome`/`facts` 匹配 `emotion.json` 模板 → 不行再让 Companion 用
LLM 生成 → 套上当前心情 → 推送 → 把要点写进 `summary` 交给宿主 LLM 演绎。

### 3. 需要 LLM 生成**内容**（出题/剧情/文案）时

```python
text = await self.llm_scene("soup.puzzle", prompt, cache_key=f"soup:{day}",
                            fallback="今天先来道老题喵")
```

场景名用 `模块.用途`（`soup.puzzle` / `remake.event` / `xiuxian.story`），这样
`llm_gateway.snapshot()` 能按场景看清"谁烧了多少 token、缓存命中率多少"。
**不要自己拼提示词后直接调 provider**——缓存/限流/失败兜底就都漏了。

### 4. 可选契约（都有默认实现，老的不用改）

| 方法 | 作用 |
| --- | --- |
| `situation(user_id) -> str` | 局面一句话（喂给台词 LLM 当上下文） |
| `event_key(outcome, facts) -> str` | 事件名（默认返回 outcome） |
| `pending(user_id) -> bool` | 是否在等主人动作（本体据此决定要不要主动催） |
| `modes() -> list` | 子玩法列表（棋类用：五子棋/象棋…） |

### 5. 写一个新游戏现在只要三样

1. **规则**：一个纯逻辑模块（如 `games/boardgame/boards/xiangqi.py`）——可单测、无 IO
2. **事件**：`facts` + `outcome`（决定本体说什么、什么心情）
3. **台词**：`data/config/<game>/emotion.json`（事件键 → 2~4 句模板；有 LLM 时它只当风格参考）

配置面板 `config.json`、帮助图 `help.json`、路由词 `keywords.json` 照旧。
**不要再写**：台词引擎、提示词、心情表、推送、渲染——那些已经被统一了。

---

## 猫娘人格：由插件读取宿主，且**可热更新**

**游戏一行都不碰人设。** 插件把宿主的猫娘人格读出来，交给统一的陪伴层
(`core/companion.py`)，所有游戏台词都带上她本人的口吻。

- 读取 `core/persona.py::load_host_persona()`，来源按可信度查找：
  `NEKO_CHARACTERS_FILE` → `NEKO_APP_DIR` → **exe 旁边的 `config/characters/zh-CN.json`**
  （打包版宿主的真实位置；按目录层数上推的老写法在打包版里**找不到**，会退化成通用口吻）→
  已加载宿主模块反推 → cwd
- 拿到字段：`昵称 / 自称 / 核心特质 / 一句话台词 / 行为特点 / 厌恶 / 对主人的称呼`
- **人格会变**（宿主随时换猫娘或改配置）：
  - `load_host_persona()` 带 **文件签名(路径+mtime+size+指定角色) + TTL** 缓存 → 变了就重读，平时不重复解析
  - `brain._maybe_refresh_persona()` 每秒 tick 轻量检查；**一变就就地更新 `Persona`
    （`apply_persona`，缺字段不动）并清空台词缓存**——旧人格生成的台词不能再用
  - 回归测试见 `tests/test_persona_refresh.py`

## 棋类对弈：一个入口，多种棋

`games/boardgame/`（id `boardgame`，名「棋类对弈」），子玩法在 `boards/` 下，**只写规则**：

| 棋种 | 落子 | 摆法 | 规则要点 |
| --- | --- | --- | --- |
| 五子棋 `gomoku` | `H8` | 交叉点 | 连五 |
| 黑白棋 `othello` | `d3` | 格心 | 翻子/跳过/数子 |
| 中国象棋 `xiangqi` | `b3 b7` | 交叉点+红黑 | 蹩马腿/炮翻山/照面/将军 |
| 四子棋 `connect4` | `d` 或 `4` | 格心 | 落底/连四 |
| 围棋 `go` | `d4`，`过` 停一手 | 交叉点 | 提子/劫/自杀禁着/两停一手数子 |
| 国际象棋 `chess` | `e2 e4` | 格心 | 易位/吃过路兵/升变/将死/逼和 |

子玩法接口（`boards/base.py`，模块级函数即可，不必继承）：

```python
NAME, new_state(**cfg), parse_move(text, state), legal_moves(state),
apply_move(state, move) -> {"ok","facts","event","over"},
board_block(state, **kw)          # 渲染桥的棋盘数据(前端也可直接吃)
situation(state), text_board(state), pending(state), cat_move(state, level, rng)
可选 review(state, move) -> {"quality": "best|ok|loose|blunder"}   # 她据此有情绪
```

配置在 `data/config/boardgame/`：共用段（`level/undo_limit/show_image/persona_hint`）+
`modes.<棋种>.*`（如 `modes.gomoku.size`、`modes.xiangqi.level`）。

**事件键（决定她说哪句）**：`player_move/player_best/player_loose/player_blunder`、
`player_capture/player_check/player_hang`、`cat_move/cat_capture/cat_check/cat_block`、
`flip/drop/pass`、`win/lose/draw`、`undo/resign/illegal/chat/hint/nudge/board`。

## 对局交互细节（聊天窗为主）

- **开局履历**：宣言里带战绩与上一局结果（「上次是本喵赢了」），并喂给台词 LLM
- **悔棋余量**：状态行常驻「悔棋剩 N 次」；没落子就说"至少下两手"；用完说"本局 N 次都用完了"
- **状态收尾**：对局已结束 / 没有对局时的「认输」「悔棋」都有准话，不再回"没有对决"；
  中途换棋种会把上一局记为 `aborted`（战绩里可见）
- **棋盘出图三档**（`show_image`）：
  - `key`（默认）：开局/吃子/将军/升变/易位/送子/胜负 + 每 5 步出一张；其余步只给一句状态行
    （附「发『棋盘』可以随时出图」）
  - `always` 每步出 / `never` 只看文字；**说「棋盘」永远出图**
  - 渲染不可用时自动退回**文字棋盘**（兜底，游戏不需要自己画）
- **输入容错**：`H8` / `h8` / `下在H8` / `H 8`、`e2 e4` / `e2e4` / `E2→E4` 都认

## 局中主动搭话：策略在本体，推送也在本体

游戏的 `on_tick` **只返回要说的文字**（或 None），**不要自己推送**；
本体 `brain.tick` 统一推送，静默阈值/次数上限/节流由 `Companion.should_nudge()` 决定
（默认 75s / 一局最多 3 次 / 间隔 150s）。

---

## 收口现状：陪伴层 / LLM 入口 / 契约白名单

**① 陪伴层管到"情绪延续"**（`core/companion.py`）
台词尾部可以跟一行控制指令：

```
哼，这条不算喵！
{"mood": "proud", "intensity": 0.8}
```

`Companion` 会把这段 JSON **剥掉（用户看不到）**，并把 `mood/intensity` 应用到猫娘人格上
（`apply_control` → `persona.feel`），所以她的情绪跨步延续，**所有游戏共享同一套**。
人格本身来自宿主（见上一节），且**可热更新**（宿主换猫娘/改配置即刻生效，并清掉台词缓存）。

**② LLM 只有一个入口**
游戏**不要**自己碰 provider；需要生成内容/台词就：

```python
await self.call_llm(prompt, scene="soup.puzzle")   # 走 core/llm_gateway.py
```

`call_llm` 内部即 `LLMGateway.scene(...)`：场景标签 + 按 key 缓存 + 限流 + token 统计；
不传 scene 会用 `<游戏id>.llm` 兜底（不会漏统计）。
契约扫描只禁 **`self._llm`（直摸 provider）**——`call_llm` 本身就是正路。

**③ 契约白名单已归零**
`tests/test_adapter_contract.py` 的 `WHITELIST` 现在**只剩 `neko_photo`**（图库本体，发图是它的职责）。
其余 10 个游戏（含棋类对弈）全部守约：不自己推送、不自己渲染、不直连 LLM。
新游戏一旦违反，测试立刻失败——契约是**用测试钉死的**。

**迁移时踩过、值得记的坑**：
- 方法名别和框架注入的同名（`self._render` 是渲染桥、`self._llm` 是 provider）：曾用
  `_render(...)` 当自己的打包方法，把桥对象盖成了函数，线上直接 `TypeError`
- `on_tick` 现在**只返回文字**（推送交本体）。想一次说多句就拼成一条返回，别多次 push
- 写脚本批量改代码时，`\n` 这类转义在"外层字符串"里会被吃掉（我踩了三次）；
  要写多行就用 `chr(10)` 拼，或严格用字面 here-string
