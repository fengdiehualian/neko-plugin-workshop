# 🐱 猫娘小游戏 · 运行机制总纲（插件总规矩）

> 本插件是**游戏大脑总管家**。所有小游戏必须适配本插件，而非插件适配小游戏。
> 情感、图片、语音、帮助、配置、开关——全部由大脑统一负责。
>
> 📖 **适配新游戏前必读 [docs/pitfalls.md](pitfalls.md)（避坑手册）**：
> 双重回复、图片可见性通道、会话打断、后台发图门控、LLM 路由、测试随机性等
> 全部踩过的坑与解法都在那里，避免重复踩坑。

---

## 一、总体架构

```
┌──────────────────────────────────────────────────────┐
│                  插件主入口 __init__.py               │
│  生命周期 / AI 入口注册 / LLM 工具                    │
├──────────────────────────────────────────────────────┤
│                    core/runtime.py                    │
│      装配大脑 → 注入 LLM → 发现游戏 → tick 循环      │
├──────────────────────────────────────────────────────┤
│                    core/brain.py                     │
│        游戏结果→情感流→输出编排、会话管理             │
├──────────┬──────────┬──────────┬──────────┬──────────┤
│persona   │emotion   │memory    │proactive │registry  │
│猫娘人格  │情感渲染  │记忆      │主动性    │游戏注册  │
│情绪弧线  │模板/LLM  │会话+     │urge+     │开关+     │
│说话风格  │三级渲染  │里程碑    │邀请      │配置注入  │
│拟人化    │          │          │          │          │
├──────────┴──────────┴──────────┴──────────┴──────────┤
│                    adapters/                         │
│  LLMClient  │  PushSender  │  ImageRenderer  │  TTS  │
│  宿主注入   │  文字/图片/  │  猫娘表情+卡片   │  主项目│
│  配置自建   │  语音/帮助   │  +帮助图渲染     │ 自动TTS│
├──────────────────────────────────────────────────────┤
│                      games/                          │
│           fishing/  │  coinflip/  │  ...（纯逻辑）   │
├──────────────────────────────────────────────────────┤
│                     data/config/                      │
│  fishing/  ├─ config.json  ├─ help.json              │
│            ├─ emotion.json └─ keywords.json           │
│  coinflip/ ├─ config.json  ├─ help.json              │
│            ├─ emotion.json └─ keywords.json           │
├──────────────────────────────────────────────────────┤
│                     ui/index.html                     │
│              面板：游戏管理/配置/帮助/对话             │
└──────────────────────────────────────────────────────┘
```

---

## 二、数据目录规范（配置 / 帮助放哪）

### 2.1 配置

```
data/config/{game_id}/config.json
```

格式：JSON 键值对。由大脑在注册时注入到游戏实例的 `self._config`。

```json
{"base_catch_rate": 0.2, "daily_casts": 5}
```

- 游戏**只读**（`self._config.get("key", default)`）
- 面板 UI 可编辑保存（`save_game_config` 入口）
- 默认值在 game 代码里写死，config.json 只覆盖

### 2.2 帮助

```
data/config/{game_id}/help.json
```

```json
{"commands": [["钓鱼", "抛竿钓鱼（可加数量：钓鱼 3）"], ["鱼缸", "查看收藏的鱼"]],
 "text": "🎣 钓鱼：每日抛竿，收藏鱼获！"}
```

- 大脑读取 → 渲染为**帮助图**（分页）→ 原生图片通道推送
- 入口：面板「📖 帮助」按钮 / 聊天说"帮助"

### 2.3 情感模板

```
data/config/{game_id}/emotion.json
```

游戏提供自己的情感模板，键为 outcome/fact kind，值为模板句子列表：

```json
{
  "catch_common": ["钓到{name}了喵！{size}cm 呢", "哦哦！{name}一条！手感不错"],
  "win": ["赢啦赢啦！是我赢的喵！", "嘿嘿，赢下这一局"],
  "lose": ["唔…输了…但下次赢回来喵", "输了…主人别难过"]
}
```

- 大脑 `EmotionRenderer` 优先使用游戏模板，无匹配时使用兜底模板
- 无 emotion.json 的游戏走通用 win/lose 模板

### 2.4 关键词

```
data/config/{game_id}/keywords.json
```

游戏声明触发关键词，用于插件匹配用户输入：

```json
["钓鱼", "钓", "鱼缸", "鱼市", "售鱼", "鱼竿", "鱼饵", "换竿", "换饵", "鱼店", "购买", "升级鱼缸"]
```

- `parse_input()` 遍历游戏关键词匹配用户输入
- 无 keywords.json 的游戏默认使用 `[name, id]`
- ⚠️ **命令必须游戏专属**：keywords.json 不许与其他游戏重复或子串截胡
  （如「商店」已改为钓鱼专属「鱼店」、修仙专属「市场」），否则跨游戏串台。
  详见 [rules.md §2.4.5](rules.md) 与 [pitfalls.md §7](pitfalls.md)。

---

## 三、LLM 情感互动机制

### 3.1 LLM 注入优先级

```
配置自建客户端 > 无 LLM（模板兜底）
```

- 配置自建：面板填入 provider/model/api_key，自建 LLMClient（所有游戏情感
  渲染走此接口，统一限流/统计）
- 模板兜底：无配置时走预制模板（游戏提供或通用兜底），猫娘照样说话
- ⚠️ 宿主**不提供**「插件直调 LLM」的 `__call_llm` API——插件不直接调宿主
  LLM；对话的自然回应由宿主按 summary（`llm_result_fields=["summary"]`）
  演绎，详见 [pitfalls.md](pitfalls.md)

### 3.2 三级情感渲染

```
游戏结果（facts + outcome）
    │
    ├─→ 高光（legendary/big_win/record）
    │        → LLM 生动生成（兴奋语气）
    │        → 图片卡片推送（带猫娘表情）
    │        → TTS 语音（兴奋）
    │
    ├─→ 低谷（lose/air/lowlight）
    │        → LLM 安慰/吐槽
    │        → 正常推送
    │
    └─→ 日常（caught/trash/tank_view）
            → 游戏模板或通用模板即时渲染（零成本）
            → 正常推送
```

### 3.3 限流

每分钟最多 N 次 LLM 调用（默认 15，面板可调）。超限自动降级到模板。

### 3.4 人格

猫娘有 5 根情绪弧线（兴奋/好奇/得意/委屈/困倦）：
- 事件触发 → 峰值 → 衰减 → 残留（随时间平复）
- 情绪影响说话风格（激动多话带感叹、失落简短）
- 主项目 characters.json 人设自动加载（性格特质、说话习惯）

---

## 四、猫娘怎么知道有什么小游戏

### 4.1 自动发现

插件启动时，`registry.discover()` 扫描 `games/` 目录，自动注册所有实现 `GameAdapter` 的游戏。无需手动导入。

### 4.2 游戏列表

猫娘大脑持有 `registry`，可随时查询：
- 所有游戏列表（id/name/icon/description）
- 开关状态（enabled/disabled）
- 实时状态（每日剩余次数、局数）

### 4.3 记忆

猫娘知道**玩过什么**：
- 每个游戏的游玩局数、最近游玩时间
- 收集到的物种/成就
- 里程碑纪录（传说鱼、最高分）
- 当前会话的最近事件

### 4.4 LLM 工具

`play_game` 用 `@llm_tool` 静态注册（SDK 启动自动注册），只接受 `input`
（用户原话），插件自动判断游戏和指令。描述写通用调用规则（提到游戏名=
明确指令直接调用，不先问不扮演），不枚举游戏列表——靠对话上下文。

```
用户说"钓鱼"      → play_game(input="钓鱼")      → 插件匹配钓鱼游戏 → 抛竿1次
用户说"钓鱼3次"    → play_game(input="钓鱼3次")    → 插件匹配钓鱼游戏 → 抛竿3次
用户说"我想钓鱼"   → play_game(input="我想钓鱼")   → 游戏不认识 → 发邀请
用户说"猜硬币正"   → play_game(input="猜硬币正")   → 插件匹配猜硬币游戏 → 猜正面
```

**入口可见性（重要）**：`@llm_tool` 走宿主 ToolRegistry（主对话 LLM 直接调用）；
`@plugin_entry(id="play_game")` 兼作宿主 task_executor 路由入口（必须非
`agent_hidden`，否则宿主判"无可用入口"）。查询类 entry（`list_games`/
`game_status`/`game_help`）全部 `agent_hidden`——宿主路由只看到一个游戏入口，
避免评估 LLM 把"玩钓鱼"错路由成"先查列表"。详见 [pitfalls.md §5.1](pitfalls.md)。

### 4.5 指令注入（只注入当前游戏）

每次执行结果附带当前游戏的 `game_commands` 指令列表，LLM 知道该游戏有什么指令可用，不需要全量注入所有游戏的指令。

### 4.6 主动邀请

久不玩某个游戏 → 猫娘主动邀请（每日限 3 次）：
- "好久没一起钓鱼了喵，要不要去河边坐坐？"
- 邀请基于记忆（上次玩的时间、玩过的游戏）

---

## 五、帮助系统（统一渲染 + 三层寻址）

### 5.1 数据存储

```
data/config/{game_id}/help.json     ← 游戏只提供**数据**, 视觉/排版全在插件端
```

结构由**游戏自己声明**（插件不擅自改）：写 `groups` 就是分组帮助，只写 `commands`
就是平铺单页，写 `"auto_groups": true` 才是"请插件代劳分组"。

```jsonc
{ "text": "...", "commands": [["老格式","仍然可用"]],     // 老字段保留
  "title": "诸天修仙", "subtitle": "...", "theme": "inherit",
  "groups": [                                             // 一级分组
    { "id": "bag", "name": "装备与道具", "icon": "bag", "aliases": ["纳戒","背包"],
      "blocks": [ {"type":"bag","title":"纳戒背包","cols":8,"cells":16,"filled":[0]} ],
      "commands": [
        ["我的纳戒", "查看背包"],
        {"cmd": "装备 X", "desc": "穿戴装备", "params": ["装备名"],
         "aliases": ["穿戴"], "related": ["卸下 X"],
         "blocks": [ {"type":"bag","title":"纳戒背包","cols":8} ]}   // 指令专属图
      ]},
    { "id": "gear", "name": "装备道具", "aliases": ["装备"],
      "groups": [ /* 二级子分组: 同样挂指令/版式块 */ ] }
  ]}
```

版式块（`blocks`，可挂在分组或单条指令上）：
`chips` / `table`(可配 `headers`) / `cards`(卡片组) / `stats`(进度条) /
`steps`·`flow`(阶梯/流程) / `slots`(槽位) / `bag`(格子) / `text`。

### 5.2 三层寻址（用户不必知道层级）

```
游戏 → 分组(可两级) → 指令
```

| 用户说 | 命中 | 渲染 |
|---|---|---|
| 修仙帮助 / 怎么玩 | 游戏 | 目录页（功能分组卡 + 代表指令，条数多时按估高分页） |
| 修仙帮助 纳戒 / 储物 | 分组别名 | 分组页（版式块 + 指令表） |
| 修仙帮助 装备 X | 指令 | 指令页；带 `blocks` 的指令出**自己的专属图** |
| 修仙帮助 怎么装备 | 包含匹配 → 装备 X | 指令页 |
| 都没命中 | — | 目录页 + "你是想看 XX 吗？" |

帮助意图由**插件统一拦截**（`帮助/攻略/玩法/怎么玩/说明/指令表/help`），游戏无需实现。

### 5.3 调用链路

```
面板「📖 帮助」 / 聊天「修仙帮助 战斗」
    → brain.show_help(game_id, topic)
    → core.help.contract: normalize_help(help.json) → resolve_topic(doc, topic)
    → core.help.renderer: 版式块 → HTML（官方 UI Kit token/类名 + 插件素材内联）
    → ImageRenderer.render_html（**宿主内置浏览器**优先）→ PNG（按 HTML 哈希缓存）
    → push_sender.help_doc(...) 原生图片通道推送(#2835), 旧宿主回退 markdown
降级链: 统一渲染 → 旧 HTML 渲染 → PIL → 纯文本（任何一环失败都不给用户空白）
```

### 5.4 入口

| 方式 | 说明 |
|------|------|
| 面板「📖 帮助」按钮 | 直调 `game_help` 入口（可带 `topic`） |
| 聊天说「帮助」/「XX帮助 功能名」 | brain 拦截帮助意图 → `show_help(game_id, topic)` |
| AI 对话 | LLM 调 `play_game(input="帮助")`，插件自动路由 |

### 5.5 渲染桥接（游戏只给数据，不参与渲染）

游戏**不得自带渲染**（不准 `import PIL`、不准起浏览器、不准写 HTML/CSS 模板），
需要图就调桥接（由 `bind_services(render=...)` 注入）：

| 接口 | 入参 | 出参 |
|---|---|---|
| `self.render_help(topic="")` | 主题词 | `List[bytes]` 帮助图 |
| `self.render_page(title, blocks=..., rows=..., commands=..., tip=...)` | 纯数据 | `bytes \| None` |
| `self.send_page(...)` | 纯数据 | `{ok, pages, summary}`（渲染+推送） |
| `self.render_card(...)` | 标题/条目 | `bytes \| None`（结果卡片） |

渲染不可用时一律返回空，游戏降级为文本。守卫测试
`test_games_do_not_render_images_themselves` 会在 CI 拦下自绘代码。

### 5.6 猫娘图库（喵图相册）

| 项 | 位置 / 策略 |
|---|---|
| 图库根目录 | `games/neko_photo/data/<分类>/`（用户可直接丢图/建分类） |
| git | **不跟踪该目录**（.gitignore 排除 → 打包默认空图库） |
| 只读安装位置 | 退化到宿主私有存储，保证上传/发图仍可用 |
| 空库兜底 | 插件自带 `assets/icon.png`，图库为空也能出图 |
| 发图频率 | 由 LLM 决定；插件闸门：`send_min_interval`/`send_max_per_hour`/`max_photos_per_day`；`auto_send_enabled` 定时刷图默认关 |

---

## 六、插件与猫娘的交互流程

### 6.1 玩家说指令（完整链路）

```
玩家说「钓鱼」
    │
    ├─ LLM 工具路由
    │   → 主项目 LLM 发现用户想玩游戏
    │   → 调用 play_game(input="钓鱼")
    │
    ├─ 面板输入路由
    │   → callEntry("play_game", {game: <游戏id>, cmd: <用户原话>})
    │
    ▼
runtime.parse_input("钓鱼") → 匹配钓鱼游戏关键词 → (game="fishing", cmd="钓鱼")
    │
    ▼
brain.handle_action("fishing", "钓鱼")
    │
    ├─ 1. 检查游戏是否启用
    ├─ 2. 自动开始会话（如果是首次）
    ├─ 3. 游戏执行：fishing.handle_action() → 返回 facts + outcome
    │       └─ 不认识指令 → outcome="unknown" → 触发邀请
    ├─ 4. 事件分类：游戏 classify_event() 或默认规则
    ├─ 5. 人格触发：persona.on_event("highlight") → 兴奋+得意
    ├─ 6. 记忆记录：memory.record("fishing", "caught_legendary", facts)
    ├─ 7. 里程碑更新：游戏 on_milestone() 或默认规则
    ├─ 8. 情感渲染：emotion.render() 使用游戏模板或 LLM
    ├─ 9. 拟人化：polish() → 加语气词"喵"
    ├─ 10. 输出编排：
    │       ├─ push_message(text) 文字立即推送
    │       ├─ render_card() 渲染图片卡片（游戏 format_fact_for_card）
    │       └─ push_message(text + image) 图片推送
    ├─ 11. TTS 标记：主项目自动播报文字
    ├─ 12. 主动性更新：proactive.on_result() 冲动+0.25
    └─ 13. 返回结果 + 当前游戏指令列表 game_commands 给 LLM
```

### 6.2 邀请流程

```
用户说「我想钓鱼」→ play_game(input="我想钓鱼")
    → parse_input 匹配"钓鱼"关键词 → game="fishing", cmd="我想钓鱼"
    → fishing.handle_action() 不认识 → 返回 outcome="unknown"
    → brain 检测到 unknown → 调用 _invite_game()
    → 推送邀请到聊天框（含游戏指令列表）
    → 返回 invitation + game_commands 给 LLM
    → LLM 知道邀请已发送，不再迷惑
```

### 6.3 面板交互

```
面板 → 轮询 get_arcade_state（3s 间隔）
    → 返回：游戏列表 / 状态 / 猫娘心情 / 记忆统计
    → 渲染：卡片网格 / 心情徽章 / 统计
```

| 面板按钮 | 调用入口 |
|----------|----------|
| 🔄 刷新 | get_arcade_state |
| ▶ 开始 | start_game |
| ⏹ 停止 | stop_game |
| 📖 帮助 | game_help |
| ⚙️ 配置 | get_game_config / save_game_config |
| 开关 switch | set_game_enabled |
| 指令输入 | play_game |

---

## 七、猫娘如何陪着玩

### 7.1 会话生命周期

```
start_game("fishing") → 猫娘："来玩钓鱼了喵！"（兴奋+0.5）
    → 玩游戏 × N
    → stop_game() → 猫娘："不玩了喵，下次再来"
```

- 会话 = 一次"共同经历"
- 大脑记录：开始时间、玩了什么、结果
- 记忆延续：下次再玩时，猫娘记得上次的事

### 7.2 主动性（不等指令，自己找话）

| 场景 | 猫娘行为 |
|------|----------|
| 游戏等待时 | 碎碎念积累 → 超阈值说话（"嗯…在想要怎么玩呢"） |
| 结果后 | 冲动飙升 → 说话（"好厉害喵！"） |
| 久不玩 | 每日限次邀请 → "好久没一起玩了喵" |
| 主人说话 | 静默窗 20s，不抢话 |

### 7.3 情感连续性

```
游戏前：心情平静
游戏中钓到传说鱼：兴奋↑↑（持续衰减）
游戏后聊天：猫娘还带着兴奋情绪，说话风格活泼
过一会儿：情绪衰减回复平静
```

- 情绪不在游戏结束时重置——**自然衰减**
- 这让人感觉猫娘是真的"有感情"，而不是每次重置

### 7.4 记忆连续性

- 短期：当前会话最近 20 条事件（供 LLM 引用上下文）
- 长期：里程碑 store 持久化（图鉴、纪录、偏好）
- 下次打开插件，猫娘还记得你

### 7.5 输出渠道

| 渠道 | 内容 | 频率 |
|------|------|------|
| 文字 | 猫娘的话（情感渲染） | 每次互动 |
| 图片 | 结果卡片（高光事件） | 高光事件 |
| 帮助图 | 玩法帮助（分页多页图） | 用户请求 |
| 语音（TTS） | 主项目自动播放 | 所有聊天消息 |
| 面板 | 心情/会话/统计 | 3s 轮询 |

### 7.6 ⚠️ 输出架构：游戏返回数据，主插件统一编排（游戏适配插件）

**架构原则：游戏不参与任何推送，只返回结构化结果，brain 统一输出。**

```
游戏 handle_action 返回 {facts, outcome, message, images?}
    │
    ▼
brain.handle_action (core/brain.py)
    ├─ 统一推送: 有 images → 推「配文/文本 + 图片」一次; 无 images → 推 message 一次
    ├─ 统一渲染: 高光卡片(wants_card)
    ├─ 统一生成 summary(用情感模板 neko_text, 与用户已见内容不同)
    ├─ 统一状态锚: 游戏进行中定期注入"还在玩X, 用户话传给 play_game"(防打断)
    │    └─ @message(source="chat") 监听主人说话 → on_owner_speak() 立即刷新锚节流
    ├─ 统一禁止 proactive 抢话: 游戏中猫娘不主动插话
    └─ 统一发图桥接: PhotoBridge(取图/上传/后台发图)
         └─ 后台自动发图受活动窗口门控(background_active_window, 默认15分钟)
```

**为什么(双重回复的根因与根治)：**

旧架构里游戏自己 `push_text_image` 推送后又把同一文本放进 summary 返回，
宿主把 summary 喂给主 LLM，LLM 复述 → 双重回复。新架构让游戏**完全不推送**，
用户可见输出只经 brain 一条通道发出，从结构上杜绝。

**两条宿主通道(插件无法关闭, 但 brain 统一管理):**

```
通道1: brain push_message(ai_behavior="blind") → 宿主直接渲染到聊天窗(不进 LLM)
通道2: @plugin_entry 返回 Ok({summary}) → 宿主 task_result → 主 LLM 生成自然回复
```

- summary 一律用 `neko_text`(情感模板, 与用户已见内容不同)——LLM 自然演绎不复述
- 游戏返回的 `message`/`images` 由 brain 编排进通道1
- 游戏自身绝不调用 push_text/push_text_image(仅 on_tick 后台提醒保留桥接)

**正确输出模式(详见 [rules.md §2.5](rules.md))：**

| 场景 | 游戏返回 | brain 推送 |
|------|----------|-----------|
| 纯文本结果 | `{"message": "钓到一条鲲!"}` | 推 message 一次 |
| 带图片卡片 | `{"message": "...", "images": [{text, bytes, mime}]}` | 推图文一次 |
| 发图(neko_photo) | `pick_photo_for_delivery` 取图 → 返回 images | 推图文一次 |

---

## 八、小游戏接入标准

见 [rules.md](rules.md) —— 硬性契约、禁止事项、配置规范、存档规范。
见 [development.md](development.md) —— 开发文档、GameAdapter 完整接口。

---

## 九、通信规则

```
游戏方（games/xxx/）
    ├── 只做：handle_action() 返回 facts + outcome + message + images?
    ├── 只做：get_user_data() / save_user_data() 存档
    ├── 只读：self._config（大脑注入的配置）
    ├── 可用：self.render_help() / render_page() / send_page() 走**渲染桥接**出图
    │          （只给数据: 版式块/表格/指令; HTML/CSS/主题/浏览器全在插件侧）
    ├── 可用：self.render_card() / pick_photo_for_delivery() 生成图片数据(交 brain 推)
    ├── 可用：self.call_llm() / tts_note() 等交互接口
    ├── 禁止：**自绘图片**(import PIL / 起浏览器 / 自带 HTML 模板 / 调 render_html)
    └── 禁止：直接调用 push_text/push_text_image/push_message、自行管理情感话术

大脑方（core/brain.py + core/help/ + adapters/）
    ├── 负责：LLM 调用、情感渲染、拟人化
    ├── 负责：统一帮助系统（契约/寻址/主题/渲染/缓存）与渲染桥接
    ├── 负责：图片渲染、帮助文档推送
    ├── 负责：会话管理、记忆、主动性
    ├── 负责：输入路由（parse_input 关键词匹配 + 帮助意图拦截）
    ├── 负责：未知指令检测 → 邀请流程
    └── 负责：通过 push_message 统一输出到主项目

主项目方（N.E.K.O）
    ├── 工具路由：LLM 通过 ToolRegistry 调用 play_game(@llm_tool) / 宿主 task_executor
    │   通过 entry_play_game(@plugin_entry, 非 agent_hidden) 路由插件任务
    ├── 注入：push_message、store、config、data_path
    ├── 提供：内置浏览器宿主环境（出图优先走它，见 adapters/image_renderer）
    ├── 自动：TTS 播放（文字走 chat 通道即播报）
    └── 自动：ASR 语音识别（语音进聊天已是文字）
    （注意：宿主不提供「插件直调 LLM」的 __call_llm API——插件情感渲染靠
    自建客户端或模板兜底，对话由宿主按 summary 演绎）
```

---

## 十、规矩总结

| 项目 | 规则 |
|------|------|
| 配置数据 | `data/config/{game_id}/config.json`，大脑统一管理 |
| 帮助数据 | `data/config/{game_id}/help.json`，**结构由游戏声明**（groups / 平铺），插件渲染 |
| 帮助寻址 | 游戏 → 分组(可两级) → 指令，每层都可用别名直达；未命中回目录页 |
| 帮助出图 | 官方 UI Kit token/类名 + 插件素材内联 + 宿主内置浏览器 + HTML 哈希缓存 |
| 渲染桥接 | 游戏只给数据（`render_help` / `render_page` / `send_page`）；**禁止自绘**（CI 守卫） |
| 图库 | `games/neko_photo/data/`（**不进 git**，打包默认空）；空库有自带图兜底 |
| 发图频率 | LLM 决定，插件封顶（最短间隔 / 每小时 / 每日）；定时刷图默认关 |
| 情感模板 | `data/config/{game_id}/emotion.json`，游戏提供，大脑使用 |
| 关键词 | `data/config/{game_id}/keywords.json`，游戏声明，`parse_input` 匹配 |
| 情感渲染 | 三级渲染，LLM 优先，游戏模板次之，通用模板兜底 |
| 游戏认知 | 自动发现 + 关键词匹配 + LLM 工具 |
| 指令注入 | 只注入当前游戏的指令，非全部游戏 |
| 邀请机制 | 游戏返回 `outcome="unknown"` → 大脑发邀请 |
| 主动性 | urge 机制 + 每日限次邀请 |
| 输出 | 文字/图片/语音 全部走大脑 push_message |
| TTS | 主项目自动播放，插件只保证短句 |
| 游戏 | 纯逻辑，提供 facts + outcome，可利用桥接服务交互 |