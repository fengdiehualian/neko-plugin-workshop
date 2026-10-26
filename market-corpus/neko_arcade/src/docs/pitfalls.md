# 🐱 适配避坑手册（主插件做桥接，游戏只做玩法）

> **一句话原则：游戏适配插件，不是插件适配游戏。**
> 游戏只负责「玩法逻辑 + 返回结构化结果」，输出、状态、锚点、桥接、防重复、
> 防打断、后台发图……全部由主插件（brain）统一负责。适配新游戏前先读本文，
> 避免重复踩坑。

配合阅读：[rules.md](rules.md)（硬性契约）、[ARCHITECTURE.md](ARCHITECTURE.md)（运行机制）。

---

## 目录

1. [输出契约：游戏不参与推送（双重回复坑）](#1-输出契约游戏不参与推送双重回复坑)
2. [图片可见性：宿主通道的现实](#2-图片可见性宿主通道的现实)
3. [会话被打断：状态锚 + 消息监听](#3-会话被打断状态锚--消息监听)
3.5 [换游戏：不能静默切，先由 LLM 确认](#35-换游戏不能静默切先由-llm-确认新增)
4. [后台自动发图：活动窗口门控](#4-后台自动发图活动窗口门控)
5. [LLM 工具路由：别硬编码词表](#5-llm-工具路由别硬编码词表)
6. [消息监听：@message 真实存在](#6-消息监听message-真实存在)
7. [命令命名：裸泛化词会跨游戏串台（必须加专属前缀）](#7-命令命名裸泛化词会跨游戏串台必须加专属前缀)
8. [适配代码不得引用参考项目名](#8-适配代码不得引用参考项目名)
9. [测试坑：随机数据与固定断言](#9-测试坑随机数据与固定断言)
10. [帮助系统坑：结构、别名、专属图](#11-帮助系统坑结构别名专属图新增)
11. [图库坑：目录、git、空库](#12-图库坑目录git空库新增)
12. [发图频率坑：定时刷图刷屏](#13-发图频率坑定时刷图刷屏新增)
13. [配置坑：0 被 or 吞掉](#14-配置坑0-被-or-吞掉新增)
14. [开发环境坑：沙箱与临时目录](#15-开发环境坑沙箱与临时目录)
15. [适配自查清单](#16-适配自查清单)

---

## 1. 输出契约：游戏不参与推送（双重回复坑）

**坑**：旧架构里游戏自己 `push_text`/`push_text_image` 推送了用户可见内容，
又把同一文本放进返回的 `summary`。宿主把 `summary` 喂给主 LLM，
LLM 复述一遍 → 用户看到两条几乎一样的话（双重回复）。

**解法（已在插件内根治）**：

| 谁 | 做什么 |
|----|--------|
| 游戏 | 只返回 `{facts, outcome, message, images?}`，**绝不调用任何 push 方法** |
| brain | 统一推送：有 images → 推「配文/文本 + 图片」一次；无 images → 推 message 一次（通道1） |
| brain | 统一生成 summary（用情感模板 neko_text，**与用户已见内容不同**）→ 宿主 LLM 自然演绎不复述（通道2） |
| brain | 统一处理高光卡片、状态锚、防打断、发图桥接 |

**两条宿主通道（插件无法关闭，但 brain 统一管理）**：

```
通道1: brain push_message(ai_behavior="blind") → 宿主直接渲染到聊天窗(不进 LLM)
通道2: @plugin_entry 返回 Ok({summary}) → 宿主 task_result → 主 LLM 生成自然回复
```

- summary 一律用 `neko_text`（情感模板），**绝不**用用户已见的 `game_msg` 原文。
- 游戏返回的 `message`/`images` 由 brain 编排进通道1，游戏自身不推送。

**正确输出模式**：

| 场景 | 游戏返回 | brain 推送 |
|------|----------|-----------|
| 纯文本结果 | `{"message": "钓到一条鲲!"}` | 推 message 一次 |
| 带图片卡片 | `{"message": "...", "images": [{text, bytes, mime}]}` | 推图文一次 |
| 发图(neko_photo) | `pick_photo_for_delivery` 取图 → 返回 images | 推图文一次 |

**禁止**：

- ❌ `handle_action` 内调用 `push_text`/`push_text_image`/`push_text_image_url`/`push_help`
  （这些方法仅保留给 on_tick 后台提醒/历史兼容）
- ❌ 返回 `pushed` / `summary` 字段（已废除，brain 统一处理）
- ❌ summary 返回用户已见的原文
- ❌ **图片推送漏传 `ai_behavior`**（见下）

### 1.1 `ai_behavior` 漏传 = 又一版双重回复（2026-09-27 实机踩到）

**坑**：`text_with_image` / `text_with_image_url` 原先把 `ai_behavior` 一路默认为
`"read"`。宿主对三个值的处理完全不同（`plugin/server/messaging/proactive_bridge.py`）：

| ai_behavior | 宿主投递模式 | 后果 |
|---|---|---|
| `respond` | proactive | 主动说一轮 |
| `read` | passive | **注入模型上下文**（图走视觉通道，还开一轮 run） |
| `blind` | silent | 只渲染，**跳过 LLM 注入** |

所以图片/状态卡用 `read` 时，同一份内容走了两条路（`summary` + 被注入的推送），
LLM 复述一遍；更糟的是那一轮 run 会**带着同一条旧输入**回调 `play_game`：

```
23:25:01 [MESSAGE FORWARD] 这手我记下了喵      ← 用户「我下在J9吧」落子成功
23:25:09 POST /runs → 同一条输入又进来 → "有子了喵"（J9 已占）← 第二份状态卡
```

**规则**：用户可见的推送一律 `blind`（`visibility=["chat"]` 决定"显示在聊天窗"，与
`ai_behavior` 无关，所以 blind 照样出图）。确需模型真看图才显式 `read`。
`_push` 默认已是 blind；`_push_native_image` 现在也是。

### 1.2 宿主重复回调守门（`core/brain.py`）

即便推送都是 blind，宿主 run 仍可能拿旧输入再调一次工具。守门规则：

> 同一条输入在上一次**没有推进状态**（`illegal/unknown/idle/hint/error`）之后
> `DUP_INPUT_WINDOW`(15s) 内再次出现 → 判为重复回调：**不再执行、不再推送**，
> 只回一条「这是重复回调、别复述」的 summary。

只对"没推进"的结果生效，所以连点「抛竿」这类正常重复输入照常执行。
回归测试：`tests/test_duplicate_reply.py`。

---

## 2. 图片可见性：原生通道已可用（#2835 已合并）

**背景**：曾以为宿主没有「用户可见图片」通道——早期宿主确实只有
`visibility=[], ai_behavior="respond"/"read"` 把图片喂给 AI 视觉，
用户聊天窗看不到；唯一的用户可见方式是 markdown `![alt](url)` 变通。

**现状（上游 #2835 已合并）**：SDK 新增 `ctx.images.upload()` —— 上传
图片返回 **canonical image part**，配合 `visibility=["chat"]` 在用户聊天窗
渲染**原生图片气泡**，无需 markdown 变通。`ai_behavior` 独立控制模型行为
（`read` = 注入现有模型 session，无 session 则丢弃）。

**适配规则**：

- 游戏要展示图片：返回 `images: [{text, bytes, mime}]`，brain 统一推送。
- **PushSender.text_with_image 已自动走原生通道**（`ctx.images.upload` +
  `visibility=["chat"]`），SDK 不支持（旧宿主）时回退 markdown 通道。
- **帮助文档图（`help_doc`，由 brain.show_help 调用）同样已走原生通道**，
  `ai_behavior="blind"`（帮助图是给用户看的参考，不喂 LLM）。
- ⚠️ **旧宿主回退必须用 markdown 图片语法 `![alt](url)`，不能用 `<img>` HTML**：
  旧宿主前端 ReactMarkdown 只开 remark-gfm/rehype-katex（无 rehype-raw），
  `<img>` 会原样显示成代码（线上踩过：塔罗牌图/帮助图变成一串 HTML 文本）。
  且 markdown 图无 CSS 宽度限制 → `save_image` 落盘前先把图片缩放到
  最长边 ≤720px（PNG 转 JPEG），防窄窗溢出。
- 不要自己拼 markdown 外链——原生通道优先，markdown 仅旧宿主回退。
- **限制**：上传图片规范化 JPEG（最长边 ≤2048px，≤8MiB），单消息 chat
  可见图片 ≤8 张、累计 ≤8MiB。

## 2.5 音频/视频 parts 宿主不支持（坑）

**坑**：想推语音/音乐/视频给用户——但宿主**丢弃** audio/video parts。

**现实（源码核实）**：`character_runtime.py` 对 `type != "image"` 的 part
直接 warning 后 drop（`stream_audio` 是实时麦克风 PCM 管线，非通用文件
注入器；无 video API）。SDK schema 虽定义 `audio`/`video` part，消费端
未实现（#2835 只解决了图片，audio/video 仍缺消费端）。

**适配规则**：

- **不要**推 audio part（`text_with_audio` 已从 PushSender 移除）。
- 语音播报正确姿势：宿主自动把 chat 通道文字 TTS 播放（官方
  short_tts_line 契约）——插件只需保证文本是 TTS-friendly 短句
  （`self.tts_note()` 标记），无需推音频数据。

---

## 2.6 聊天窗图片气泡 max-width≈280px（帮助图别按 740 画）

**坑**：帮助图按 740px 宽渲染，但宿主聊天窗 CSS 是
`.message-block-image { max-width:280px }`、`.message-stack { max-width:min(86%,320px) }`
（窄窗 84%）。740px 被缩到 ~38%，13px 正文实际只剩 ~5px —— 用户原话
「图片渲染不适配聊天窗口，看不清晰帮助图」。

**解法（已落地）**：

- `HelpRenderer` 默认按 **320px CSS 宽 + 单列** 渲染（两列会挤压，改单列；
  表格改成「指令在上、说明在下」）；可用 `NEKO_ARCADE_HELP_WIDTH` 或主配置
  `[help] width` 调整；
- `ImageRenderer` 用 **`device_scale_factor=2`** 截图（`NEKO_ARCADE_IMAGE_SCALE`
  可调）→ 输出 ~640px 的清晰 PNG；气泡缩到 280px 时正文仍有 ~11px，清晰可读；
- PIL 回退（`render_help`）和旧 HTML 通道（`render_help_html`）同步改成 320px 单列，
  保证没有浏览器时也不糊。

---

## 3. 会话被打断：状态锚 + 消息监听

**坑**：游戏中宿主插入互动消息（或 LLM 长时间没调工具）后，LLM 会「脱节」——
用户说「继续」，LLM 当成普通聊天回应，而不是调用 `play_game` 传游戏指令，
游戏会话被「软打断」。

**解法（插件内，借鉴 MC 插件的 keep-going 思路）**：

1. **状态锚注入**：游戏进行中，brain 每 `game_anchor_interval`（默认 20s）
   向 LLM 上下文注入一条 read-only 文本（`visibility=[], ai_behavior="read"`）：
   「[游戏状态] 正在玩X，游戏没结束，引擎在等输入，可用指令：…」。
   - **只含当前游戏的指令**（前 6 条），绝不拼接全部游戏（防炸上下文）。
   - 每次 `handle_action`（LLM 调了工具）都会刷新锚节流 → 锚只在脱节时出现。
   - 间隔曾被调到 60s 导致脱节严重——20s 是验证过的平衡点。

2. **@message 即时刷新**：插件注册 `@message(id="chat_activity", source="chat")`，
   每次主人说话 → `brain.on_owner_speak()` → 立即把 `_last_anchor_ts` 归零，
   下一次 tick 立刻注入状态锚，让 LLM 尽快回到游戏。

3. **游戏中禁止 proactive 抢话**：猫娘不主动插话，避免把用户注意力拉走。

---

## 3.5 换游戏：不能静默切，先由 LLM 确认（新增）

**坑**：会话中用户发别的游戏名（玩钓鱼时说「修仙」），`parse_input` 会路由到
新游戏，但旧实现里 `brain._current_game` 没变——新游戏 `on_start` 不跑、旧游戏
`on_stop` 不跑、状态锚还在说旧游戏，玩家感觉「新游戏指令失效」。

**解法（已落地）**：

- 会话中路由到**别的**游戏时，brain 先返回 `outcome="switch_confirm"`，只给 LLM
  一条 summary 指令（**不推固定话术**）：让猫娘用自己的口吻问「要换吗」。
- 用户确认后，LLM 再调 `play_game` 并填 `switch_to=目标游戏名`；brain 才
  `旧游戏 on_stop → 新游戏 on_start → 执行新游戏`，summary 里补一条
  `[切换完成]` 让猫娘说「好，切过来了」。
- 确认有 TTL（默认 180s，`game_switch_confirm_ttl` 可调），过期作废；
  用户回到当前游戏 = 放弃切换。
- 面板「开始」直接开新游戏时也走 `on_stop` 收尾（修掉旧的只清字段 bug）。

**适配规则**：游戏不需要感知切换；`on_start`/`on_stop` 要可重入、幂等。

---

## 4. 后台自动发图：活动窗口门控

**坑**：后台游戏（如 neko_photo 标记 `background_tick=True`）在无人聊天时
也会持续自动发图 → 刷屏。另外 `background_tick` 的游戏 on_tick 只在
无会话时运行（有会话时当前游戏的 on_tick 已由主 tick 调用）。

**解法**：

- 游戏标记 `background_tick = True`，冷却逻辑在游戏内
  （如 `_next_auto_ts`，默认 60~180 秒一张，blind 推送不触发 AI 轮次）。
- brain 用**活动窗口门控**：`_last_activity_ts`（主人最后说话时间，由
  @message → on_owner_speak 刷新）距现在超过 `background_active_window`
  （默认 900s / 15 分钟）→ 后台自动发图整体暂停。
- 从未聊过天（`_last_activity_ts == 0`）也不发图。

**适配规则**：后台自动行为（发图/提醒）必须走 `on_tick` + `background_tick`，
不要用游戏内定时器，否则退出会话后仍在跑、且无法被统一门控。

---

## 5. LLM 工具路由：别硬编码词表

**坑**：主插件把用户输入交给 LLM 工具（`play_game`）判断，LLM 有裁量权——
所有官方插件都是同一机制，这是设计不是缺陷。曾出现 LLM 说
「要开始玩吗？」而不调 `play_game` 的「命令没有下文」问题。

**解法（不硬编码确认词表）**：

- **游戏侧**：`keywords.json` 写足触发词（如人生重开加「重启人生」）；
  `handle_action` 对确认/催促词（对的喵/对/是/没错/确定/确认）语义要接得住，
  返回对应的 outcome，让 LLM 知道下一步。
- **插件侧**：`play_game` 工具描述写清楚「传用户原话」；返回结果带
  `game_commands`（当前游戏指令）喂回 LLM。
- 交互语义（确认词/催促词）由 LLM 判断，主插件不硬编码词表；
  游戏自身通过 outcome 语义返回待选择提示。

**5.1 ⚠️ 宿主路由会选错入口——只留 play_game 对 LLM 可见（坑，重要）**

**坑**：用户说「塔罗牌」「钓鱼」「人生重开」等明确游戏名，LLM 猫娘却自己扮演
游戏流程（"找插件""选牌阵""帮你点开始"），游戏指令几乎瘫痪。日志表现：

```
用户说钓鱼  → [UserPlugin] has_task=True → Dispatching entry_id=list_games   ← 错
用户说钓鱼  → [UserPlugin] has_task=True → Dispatching entry_id=game_status  ← 错
用户抛竿吧  → [UserPlugin] has_task=True → Dispatching entry_id=game_status  ← 错
```

**根因**：宿主 `brain/task_executor.py` 的 Agent 路由（UnifiedAssessment）会让
LLM 从插件所有**非 `agent_hidden`** 的 `@plugin_entry` 里选一个调用。若
`list_games`（"列出所有小游戏名"）、`game_status`（"查看当前状态"）对 LLM
可见，评估 LLM 倾向选"先查列表/查状态"而不是 `play_game`——于是游戏指令
被路由到查询入口，游戏根本没启动。

**解法（参考 game_agent_minecraft 的做法）**：

- **`play_game` 用 `@llm_tool` 静态注册**（SDK 启动自动注册，走 ToolRegistry），
  描述写通用规则：「用户提到游戏名/游戏指令 = 明确的执行请求，直接调用，
  不要先问、不要自己扮演」。不枚举游戏列表，靠对话上下文。
- **查询类 entry（`list_games`/`game_status`/`game_help`）全部 `agent_hidden`**
  ——宿主路由只看到一个游戏入口 `play_game`，评估 LLM 没有"先查列表"的选项。
- 面板专用 entry（start_game/stop_game/配置类）本就 `agent_hidden`。
- ⚠️ **`play_game` 的 @plugin_entry 不能 agent_hidden**：宿主 task_executor 若
  看不到任何非 hidden entry，会判 `can_execute=false`（"无匹配的可执行插件
  任务"）→ 用户说游戏名也走不到插件。**必须有至少一个非 hidden entry**
  （play_game 兼作宿主路由入口），且它同时是 @llm_tool（主对话工具）。
- **适配规则**：游戏类插件若想让 LLM 路由稳定，模仿此结构——一个主工具
  `@llm_tool`（对话直接调用）+ 一个同名/对应非 hidden `@plugin_entry`
  （宿主路由），查询类 entry 全 hidden。

**5.2 ⚠️ 无会话弱指令（「再来一局/继续玩」）——last_game 兜底（坑）**

**坑**：用户没点名游戏，只说「再来一局」「继续玩」「玩点什么」，`parse_input`
匹配不到任何关键词 → `tool_play_game` 报「没有找到匹配的游戏」，LLM 无法启动。

**解法**：`parse_input` 新增规则 4——无会话 + 命中 14 个明确的「接着玩」
意图词（再来/继续/接着/再开/来一把/随便玩…）→ 路由到 `brain.last_game`
（最近玩过），并把 cmd 换成该游戏启动指令（keywords[0]，如「钓鱼」），
LLM 拿到可直接执行的指令。误伤防护：只收明确意图词，不收「又/想玩/换个」
等宽泛词；显式游戏名（规则 2）和当前会话（规则 3）永远优先。

---

## 6. 消息监听：@message 真实存在

**坑**：曾以为宿主没有「收到聊天消息」的监听器，导致插件无法感知主人说话。

**真相**：`@message(id="...", source="chat")` 存在（官方 neko_warthunder 等
插件在用）。本插件已注册：

```python
@message(id="chat_activity", source="chat")
async def on_chat_activity(self, **_):
    if self.rt and self.rt.brain:
        await self.rt.brain.on_owner_speak()
    return Ok({"status": "observed"})
```

**适配规则**：需要感知「主人说话」的游戏/功能，通过 brain 的
`on_owner_speak()` 挂钩（刷新活动窗口、状态锚），不要在游戏内自建监听。

---

## 6.5 推送必须带 target_lanlan（多角色会话，坑）

**坑**：`push_message` 不带 `target_lanlan` 时，宿主 `_get_session_manager("")`
返回 None；**多角色(多猫娘)会话时 fallback 也为空 → 推送被宿主直接丢弃**。
早期版本全部推送都没带 target_lanlan，单角色环境碰巧不丢（fallback 兜住），
多角色环境静默丢消息。

**真相**：官方 lifekit / neko_live 等插件推送一律带 `target_lanlan`（从
`ctx._current_lanlan` / 环境变量解析）。PushSender 已统一解析并带上：

```python
# 解析优先级: ctx._current_lanlan → ctx._host_ctx._current_lanlan →
# NEKO_TARGET_LANLAN / NEKO_LANLAN_NAME / NEKO_HER_NAME 环境变量
target = self._resolve_target_lanlan()
self.plugin.push_message(..., target_lanlan=target or None)
```

**适配规则**：任何走 PushSender / brain 的推送都不用手动传——它已统一处理。
游戏若绕过 PushSender 直接调 `plugin.push_message`，必须自己带 `target_lanlan`。

---

## 6.6 ctx 没有 user_id：不要按用户隔离（坑）

**坑**：SDK 的 `ctx` 没有 `user_id` 属性——`getattr(self.ctx, "user_id", "default")`
永远返回 `"default"`。若代码试图「按用户隔离存档/状态」，实际所有用户共享
同一份数据，且没有任何报错。

**真相**：宿主是单主人(单 master)会话模型，官方插件从不读 `ctx.user_id`。
本插件所有 `user_id` 参数实际恒为 `"default"`——这在当前架构下是正确的
（主人只有一个），不要为了实现「多用户隔离」去猜宿主 API。

**适配规则**：存档键用 `game:{id}:user:{uid}` 即可（uid 恒 default 无妨）；
**不要**依赖任何未在 SDK 文档中声明的 ctx 属性（如 user_id），拿到就当作
「该属性可能不存在」处理。

---

## 7. 命令命名：裸泛化词会跨游戏串台（必须加专属前缀）

**坑**：命令用裸泛化词（`我的状态`/`我的背包`/`商店`/`任务`/`成就`/
`技能`/`探索`/`领养`/`签到`/`猜`/`来一张`）——多个游戏 keywords.json
共用同一词。`parse_input` 无会话时按注册顺序先到先得，玩家说
「探索秘境」（修仙）可能被猫猫进化的「探索」截胡、「轮盘签到」（轮盘）
被修仙的「签到」截胡、「猜汤底」（海龟汤）被猜硬币的「猜」截胡 ——
命令串台，游戏错乱。

**根因**：`parse_input` 是子串匹配（`keyword in input`），keywords.json
是唯一路由依据；裸词天然指向不明确。

**解法（已在本插件全量落实）**：

- 每个游戏命令带**专属前缀/语境词**：猫猫进化→`猫猫状态/猫猫背包/猫猫任务`、
  修仙→`修仙状态/我的纳戒/每日任务/市场`、钓鱼→`鱼店`、海龟汤→`海龟汤状态`、
  轮盘→`轮盘签到`…
- **keywords.json 不出现跨游戏重复词，也不出现子串截胡**（A 的短词
  in B 的长词同样算冲突）。
- 游戏内 `_RULES` 可留旧别名（会话内规则 3 兜底仍可用），keywords.json
  必须干净。
- 改完跑 `tests/test_command_naming.py`：全量扫描跨游戏重复 + 子串截胡，
  不通过就是有串台词。

**自查**：任何命令词，玩家说出口只能指向一个游戏；指向不确定 = 命名
不规范，加前缀。

---

## 8. 适配代码不得引用参考项目名

**坑**：从别处移植/适配的游戏代码注释里写了原项目名
（nonebot-plugin-xxx、zhutianxiuxian 等），违反部署要求。

**规则**：适配的游戏代码（注释、docstring、README）一律**不得出现
参考项目名**。数据来源描述改为「data/ 下自带 JSON」这类中性表述。

---

## 9. 测试坑：随机数据与固定断言

**坑**：`games/remake` 的天赋池每次开局随机抽取，天赋的 `status` 字段会
**加成可分配属性总和**：`total = 20 + Σ(status)`（status ∈ [-20, +8]）。
测试硬编码「5 5 5 5」（和=20）→ total≠20 时随机失败（flaky）。

**解法**：

- 测试从游戏返回的 `facts` 里**动态读真实 total**（`kind == "prop_prompt"`
  的 fact 带 `total`），再构造合法拆分（每项≤10、和为 total）。
- 游戏侧示例提示同样必须跟真实 total 走（`_example_props(total)`），
  否则玩家照示例输入必被拒——这本身也是适配 bug（已修复）。

**通用规则**：凡依赖随机数据（随机天赋/随机牌/随机事件）的测试，
断言不要写死具体数值；要么从返回值动态取值，要么只断言结构/状态机。

---

## 11. 帮助系统坑：结构、别名、专属图（新增）

**坑 A：以为插件会替游戏分组。** 结果是"插件擅自改结构"或"什么都没分"。
**正解**：结构由**游戏自己声明**——写 `groups`（可两级）就是分组帮助，只写
`commands` 就是平铺单页，只有显式写 `"auto_groups": true` 才是请插件代劳。

**坑 B：父分组别名和子分组抢词。** 父分组别名写「纳戒」、子分组又叫「纳戒背包」，
用户说「纳戒」永远命中父页，钻不进子页。**正解**：父分组别名避开子分组的
名字/别名（测试里有防重校验）。

**坑 C：把"细分功能"全塞进一张总图。** 61 条指令铺一张图又长又难读。
**正解**：总图只放**功能分组 + 代表指令**；细分靠"分组页 / 指令专属图"承载——
`「修仙帮助 纳戒」`看分组，`「修仙帮助 我的纳戒」`直接出那张背包格图。

**坑 D：让"查看类"指令只出现一行表格。** 用户要的是**一张自己的图**。
**正解**：给指令挂 `blocks`（如 `{"type":"bag","cols":8,...}`），命中时先出专属图。

## 12. 图库坑：目录、git、空库（新增）

**坑 A：图库被提交进 git → 打包带上用户的私人照片。**
**正解**：图库目录 `games/neko_photo/data/` 写进 `.gitignore`（已加），
打包发布默认就是**空图库**；上传后 `git status` 必须干净。

**坑 B：图库为空 → 「发图」类指令和后台自动发图一起哑火。**
症状：`pick_photo` 返回 None → `send_photo` 报 `no_photo` → 聊天窗什么都没有。
**正解**：兜底链 = 本地图库 → 动态猫娘表情(PIL) → **插件自带 `assets/icon.png`**，
保证空库也有图可发。（指定某个**空分类**时仍如实报"该分类还没有图"，不拿默认图冒充。）

**坑 C：图库目录只读（只读安装位置）导致上传失败。**
**正解**：桥接会探测可写性，不可写时退化到宿主私有存储。

## 13. 发图频率坑：定时刷图刷屏（新增）

**坑**：固定 60~180 秒定时发图 → 猫娘刷屏，用户烦。
**正解**：**频率交给 LLM**（`send_photo` 工具想发就发），插件只兜上限：
`send_min_interval`(90s) / `send_max_per_hour`(6) / `max_photos_per_day`(50)；
被拦时返回 `ok:false + limited:true + retry_after`，LLM 看到就不会连环调用。
定时刷图 `auto_send_enabled` **默认关闭**，需要时再开。

## 14. 配置坑：0 被 `or` 吞掉（新增）

**坑**：`int(cfg.get("send_min_interval", 90) or 90)` —— 用户把冷却设成 **0**
（表示不限），`0 or 90` 得到 90，配置静默失效。
**正解**：显式判空（`None` / `""` 才用默认），0 是合法值。

## 15. 开发环境坑：沙箱与临时目录

- Windows 沙箱下 `tempfile.mkdtemp()` 不可写：构建脚本会 monkeypatch
  `tempfile.mkdtemp` 指向仓库内 `.tmp-build`，测试也把临时目录放到
  仓库内（`.tmp_xxx_test`）。
- pytest 缓存目录在沙箱锁定目录内会破坏收集：用
  `-p no:cacheprovider` + 显式测试文件列表运行。
- 同步到 live 插件目录需 `danger-full-access`，同步后要字节级校验；
  DSH 重启后插件变更才生效。

---

## 16. 适配自查清单

- [ ] 实现 `GameAdapter`，id/name/description/icon 齐全
- [ ] `handle_action` 返回 facts + outcome + message（+ images?），不认识指令返回 `outcome="unknown"`
- [ ] **不调用任何 push 方法**（`push_text`/`push_text_image`/`push_help` 仅 on_tick 保留）
- [ ] **不直接调 `plugin.push_message`、不读宿主 ctx 私有属性**（如 ctx.user_id，见 §6.6）
- [ ] **不推 audio/video part**（宿主丢弃，语音靠宿主自动 TTS，见 §2.5）
- [ ] **不自绘图片**（不 import PIL / 不起浏览器 / 不写 HTML 模板；出图走渲染桥接，见 §11）
- [ ] summary 用情感模板 neko_text，与用户已见内容不同（无双重回复）
- [ ] 需要展示图片 → 返回 `images`（bytes 或 url）交 brain 统一推
- [ ] 配置/帮助/情感/关键词放 `data/config/{id}/`，图片资源放 `games/{id}/data/`
- [ ] `help.json` 结构自己声明（`groups` 分组 / 平铺），别名不与子分组抢词（§11）
- [ ] 查看类指令若需要"自己的图"，用指令级 `blocks`（§11）
- [ ] 图库类游戏：图库目录在 .gitignore 里、空库有兜底图、发图有频率闸门（§12/§13）
- [ ] **命令带游戏专属前缀，keywords.json 无跨游戏重复/子串截胡**（跑 tests/test_command_naming.py 验证，见 §7）
- [ ] 后台自动行为走 `on_tick` + `background_tick=True`，冷却在游戏内
- [ ] 需要感知主人说话 → 依赖 brain.on_owner_speak()，不自建监听
- [ ] 随机数据的测试/提示动态取值，不写死固定数字
- [ ] 配置项读到 0/false 时要显式判空，别用 `or` 吞掉（§14）
- [ ] 注释/文档不出现任何参考项目名
- [ ] 需要的插件能力先查 [rules.md §2.5 桥接表](rules.md)，**主插件能做的不要游戏自己做**

---

## 17. 发布坑：本地全绿、CI 却红（2026-10 v0.5.10 实际踩到）

**现象**：本地 `pytest` + `ruff` 全绿，推 tag 后 CI 在 *Ruff check* / *Release check* 失败。

两个原因都跟"本地环境和 CI 不一样"有关：

1. **ruff 规则集不同** — CI 用的是
   `ruff check --ignore-noqa --isolated --target-version py311 --line-length 120 --select E4,E7,E9,F,I`
   （`--isolated` = **忽略仓库 ruff.toml**，且选了 **E7**）。
   仓库 `ruff.toml` 没选 E7，所以 `mb = lambda n: ...`（**E731**）本地不报、CI 报。
   → 发版前跑一次上面这条**原命令**（本地 `plugin-repo` 不存在会有个 E902，忽略即可）。

2. **测试依赖"仓库里有的东西"，CI 干净 checkout 里没有** —
   塔罗牌面已外置（不进仓库/不进包），CI checkout 后 `games/tarot/data/` 是空的，
   于是 `assert len(images) >= 3` 直接失败。
   → 规则：**测试不许依赖"仓库里有素材"**。要么用桩文件（`_local_scan_dir` 指到临时目录），
   要么按"素材就位与否"分别断言（就地出图 / 优雅降级），见 `tests/test_tarot_e2e.py`
   的 `_assets_ready()` 与 `test_card_image_*`。

**发版前自查清单**：

```powershell
python -m ruff check --ignore-noqa --isolated --target-version py311 --line-length 120 `
       --select E4,E7,E9,F,I --exclude vendor plugin-repo .
python -m pytest tests/ -q                 # 有素材态
# 再模拟一次"干净 checkout"（临时移走大素材目录）跑一遍：
Rename-Item games\tarot\data data.hidden; python -m pytest tests/ -q; Rename-Item data.hidden data
python tools\estimate_package_size.py -v    # 包体自查
```
