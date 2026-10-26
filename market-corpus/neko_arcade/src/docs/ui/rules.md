# 📜 接入规则（简版）

> 硬性契约的**精简版**，供面板快速查阅。
> 完整规则与踩坑手册在 GitHub 仓库：
> **`docs/rules.md`**（插件规则）与 **`docs/pitfalls.md`**（避坑手册）。

## 核心契约

| 规则 | 说明 |
|------|------|
| 输出 | 游戏只返回 `{facts, outcome, message, images?}`，**不自行推送** |
| 推送 | 一切用户可见输出由 brain 统一编排（防双重回复） |
| 渲染 | **游戏不参与渲染**：不 import PIL / 不起浏览器 / 不写 HTML；出图走桥接（`render_help` / `render_page` / `send_page`），只给数据 |
| 帮助 | `help.json` 自己声明结构（`groups` 分组 / 平铺 / `auto_groups:true` 请插件代劳），别名别和子分组抢词 |
| 命名 | 命令带游戏专属前缀，keywords.json 不得跨游戏重复/子串截胡 |
| 配置 | 配置/帮助/情感/关键词放 `data/config/{id}/` |
| 存档 | 用 `get_user_data` / `save_user_data`，不自建存储 |
| 图库 | 用户上传的图放 `games/neko_photo/data/`（**不进 git**，打包默认空） |
| 发现 | 游戏包放 `games/` 自动发现，零登记 |

> 适配前必读完整版 `docs/rules.md` + `docs/pitfalls.md`——踩过的坑都在里面。

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
