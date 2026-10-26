# 📜 版本历史（开发者）

> 完整迭代记录，供开发者查阅。插件市场门面 README.md 只保留最新版本。

## v0.5.10 (2026-10)

**一句话主题**：发布包 **4.37 MB → 1.72 MB** —— 塔罗牌面（251 MB）移出包体、首次运行
自动从 Gitee 下载补齐；大数据改 `.gz`；UI 素材 WebP 化后继续随包（离线可用）。

### 新增

- **外部素材库 `adapters/asset_store.py`** — 塔罗牌面 251 MB PNG 不再进包：
  - 远端 manifest（Gitee 主源 `gitee.com/maoyvna/taluopai` → GitHub 插件仓库 `assets`
    分支兜底）→ 下载后核对 **sha256 + 字节数**（不符即丢弃重试，绝不用坏图）
    → 落 `plugin.cache_path("assets")`；
  - 多源回退、失败重试、线程池下载不阻塞宿主、原子落盘（`.part` → `os.replace`）、
    已下载跳过（重启不重下）；首次运行后台预热（`shutdown` 可取消）；
  - 游戏侧统一入口 `GameAdapter.asset_path(rel)`，拿不到返回 `None`（回退占位图，不报错）。
  - 实测：空缓存 + 包里无图 → 首次渲染自动补齐 4 个素材（4 成功 0 失败）。

- **素材分类与"本地优先"** — 清单按 `tarot/` `ui/` `panel/` 分类；`DEFAULT_LOCAL_MAP`
  让仍随包的素材本地直接用（不下载、离线可用），只有本地没有的才联网。
  帮助图四个素材（头像/背景/立绘/徽章，共 288 KB）**刻意留在包内**。

- **工具 `tools/`** — `build_asset_manifest.py`（按分类压缩导出素材 + 生成清单）、
  `compress_data_json.py`（重新生成 `.gz`）、`estimate_package_size.py`
  （按官方打包规则预估包体，发版前自查）。

### 变更 / 优化

- **大数据 `.gz`** — 人生重开 `age.json`(1.88 MB) + `events.json`(0.36 MB) + 修仙/钓鱼数据
  共 2.59 MB → **0.17 MB**（省 94%）：仓库保留明文（开发/审阅/再生成），打包用
  `exclude_files` 排除明文，运行时 `core/datafile.py` 优先读 `<name>.gz` 透明解压。
- **UI 素材瘦身** — 背景图 PNG 原图 1.89 MB → WebP 132 KB（按代码实际使用的 820px 宽导出）；
  图标 139 KB(480px，实为 JPEG 却叫 `.png`) → 16 KB(256px `icon.jpg`)；
  无任何代码引用的 `yui横.png` 506 KB → 46 KB WebP 留档。
- 打包范围：`exclude_dirs` 增加 `games/tarot/data`、`static/cards`；
  `exclude_files` 增加大数据明文 JSON。发布包构成：代码 ~1 MB + `static/index.html`
  228 KB + UI 素材 288 KB + `.gz` 数据 132 KB。

### 测试

- 新增 `tests/test_asset_store.py`（11 项）、`tests/test_datafile.py`（5 项）；
  `tests/test_help_render.py` 增加"素材路径注入优先"用例。
  全量 **211 passed**，ruff 全绿。

## v0.5.9 (2026-09)

> 该版本尝试放宽帮助图交付宽度（宿主原生气泡 280px 上限），随后**决定保持原样并撤回**：
> 改动见 revert 提交，结论记录在 `docs/pitfalls.md` §2.6（markdown 通路可突破上限但会
> 超出气泡白底，取舍待定）。

## v0.5.8 (2026-09)

**一句话主题**：修掉线上「一句两答」——用户可见推送不再走 `ai_behavior="read"`，
并给宿主的重复回调加了守门。

### 修复

- **双重回复（图片推送漏传 `ai_behavior`）** — `text_with_image` /
  `text_with_image_url` 不传 `ai_behavior`，落到 `_push_native_image` 的默认值
  `"read"`。宿主对三个值的投递模式完全不同（`plugin/server/messaging/proactive_bridge.py`）：
  `respond`=proactive、`read`=passive（**注入模型上下文**，并开一轮 run）、
  `blind`=silent（跳过 LLM 注入）。于是棋盘图/状态卡同时走了 `summary` 与
  "被注入的推送"两条路，LLM 复述一遍；那一轮 run 还会带着**同一条旧输入**回调
  `play_game`——实测 2026-09-27 五子棋：用户「我下在J9吧」落子成功并推状态卡，
  8 秒后同一句又进来，第二次落子被判非法（"有子了喵"）→ 用户看到两份状态卡。
  现在图片/卡牌/照片推送默认 `ai_behavior="blind"`（`visibility=["chat"]` 仍保证
  在聊天窗显示，与 `ai_behavior` 无关）；确需模型真看图才显式传 `"read"`。
- **宿主重复回调守门** — `core/brain.py::handle_action` 新增：同一条输入在上一次
  **没有推进状态**（`illegal/unknown/idle/hint/error`）之后 `DUP_INPUT_WINDOW`(15s)
  内再次出现 → 判为宿主 run 的重复回调：不再执行、不再推送，只回一条
  "这是重复回调、别复述"的 summary。只对"没推进"的结果生效，连点「抛竿」这类
  正常重复输入不受影响。

### 测试

- `tests/test_push_sender.py`：图片推送断言改为 `ai_behavior == "blind"`，并新增
  逃生口测试（显式 `read` 透传、blind 不改 `visibility`）。
- `tests/test_duplicate_reply.py`（新增 4 项）：重复非推进输入被忽略、推进输入照常
  执行、窗口过期后允许重试、不同输入互不干扰。

### 变更 / 升级注意

- 版本号 0.5.7 → **0.5.8**；行为变化只影响"推送是否喂给 LLM"，出图与显示不变。
- 排查同类问题：宿主日志里 `POST /runs` 紧跟推送出现，就是宿主 run 的重复回调。

## v0.5.7 (2026-09)

**一句话主题**：新增棋类对弈（六种棋），并把「陪伴 / LLM / 渲染」三条通道彻底收归插件本体。

### 新增

- **棋类对弈（`boardgame`）** — 一个游戏入口挂六种棋：五子棋(15×15)、黑白棋(8×8)、
  中国象棋、四子棋(7×6)、围棋(9×9)、国际象棋。规则各自独立在 `boards/<棋种>.py`，
  `game.py` 只做「模式路由 + 状态搬运 + 调本体陪伴」。支持三档难度（随手/正常/认真）、
  悔棋（默认 3 次）、认输、换棋种、棋盘重发；**落子后由渲染桥的 `board` 版式块出棋盘图**
  （交叉点 / 格心两种摆法 + 坐标标签 + 最后一手标记 + 星位）。六种棋的落子报法按棋种区分
  （坐标 / 起点终点 / 列号）。
- **统一陪伴层（`core/companion.py`）** — 台词、心情、闲聊回应、局中主动搭话全部归本体：
  台词三级降级（LRU 缓存 → LLM → 游戏 `emotion.json` 模板）；心情由事件键驱动
  （highlight/lose/chat…）；`should_nudge()` 把静默阈值、次数上限、节流策略收在一处，
  游戏只报"我在等他动作 + 上次活动时间"。台词模型可用句尾 JSON（`{"mood":…}`）控制情绪延续。
- **LLM 网关（`core/llm_gateway.py`）** — 游戏要"生成内容"（出题 / 剧情 / 文案）时只声明
  场景名，网关统一做缓存（场景 + 内容哈希 + TTL）、失败兜底与按场景用量统计；
  调用失败不抛异常，退回 fallback。
- **人格热更新（`core/persona.py`）** — 猫娘人设从宿主角色配置读取，带文件签名
  （mtime/size）+ TTL 缓存：宿主换角色后台词立即跟着换；新增 `apply_persona()`
  供热重载，换角色时清空台词缓存，不再挂着上一位的口吻。
- **出厂内容随升级刷新（`core/config_manager.py`）** — `help / keywords / emotion`
  属于出厂内容，升级时按字节比对同步进用户数据目录；`config.json` 是用户设置，
  永不覆盖。修掉旧实现"用户目录只要存在过 `config/`，升级带来的新 help/keywords
  就永远不补 → 帮助图只剩一句文本"的问题。
- **契约测试** — `tests/test_adapter_contract.py` 静态扫描 `games/**`：出现
  `self.push_text*` / `self.send_page(` / `self.render_card(` / `self._llm` / `_QUIPS =`
  即判违规（白名单只留图库本体 neko_photo）；`tests/test_keyword_style.py` 审计
  keywords 跨游戏重复/子串截胡与单字词；另新增 `test_all_games_smoke` /
  `test_game_switch` / `test_persona_refresh` / `test_llm_gateway` / `test_boardgame`。

### 修复

- **陪伴层心情映射写错 id（静默失效）** — `MOOD_BY_EVENT` 原写成 `excited/curious`，
  而 `persona.EMOTION_POOL` 的规范 id 是 `excitement/curiosity`；`Mood.trigger`
  对未知 id 静默忽略，表现为"赢了猫娘也不兴奋、提示词里的心情永远是平静"。
  现改为规范 id + 别名归一（`excited` / `兴奋` → `excitement`），`apply_control`
  同时加异常护栏；新增 `tests/test_companion.py`（14 项）覆盖映射、三级降级与搭话策略。
- **推送被宿主拒绝却当成成功** — `push_message` 对超限内联图**不抛异常**，只回一条
  `rejected: reason=payload_too_large` 回执；`push_sender` 现在把 rejected 回执当失败，
  继续走下一档通道（原生 → 上传 → markdown），不再出现"以为发了其实没图"。
- **棋类关键词覆盖不全** — 补齐 `西洋棋 / 翻转棋 / 奥赛罗 / 五连 / 四连棋 / 对弈` 等别名。
- **棋类帮助内容不全** — `help.json` 的 `text` 原只写 4 种棋，现补齐六种；
  分组由"两种棋硬凑一对"改为 `选棋种 / 六种棋 / 对局操作`，并补 `title` / `subtitle`。
- **市场发布校验被标准仓库文件卡住** — `.vscode/settings.json` / `.vscode/tasks.json`
  是市场 `setup-repo --github-actions` 的托管文件，严格模式下缺失直接判 error：
  CI 最后一步 `[FAIL] neko_arcade: check --release blocked by validation errors`，
  而本地 pytest / ruff 全绿 —— 属于"本地看不出来"的失败。已补齐，
  并把这两条校验（外加入口类 `@neko_plugin` 装饰器检查）镜像进
  `tests/test_smoke.py`，本地即可拦住同类问题。
- **入口写法非规范** — `plugin.entry` 原写 `plugins.neko_arcade:NekoArcadePlugin`，
  市场校验判为"模块在插件目录之外，跳过静态入口检查"；改为规范写法
  `plugin.plugins.neko_arcade:NekoArcadePlugin`——宿主 `normalize_plugin_entry_point()`
  对外部安装会自动规范化为 `plugins.<id>:Class`，两种安装形态都能加载，
  改完既消警告又恢复静态入口检查（类装饰器 / 基类 / 生命周期）。

### 变更 / 升级注意

- 版本号 0.5.6 → **0.5.7**；`plugin.toml` 描述与关键词补齐棋类（现共 11 款游戏）。
- 游戏侧**不得**自绘图片、不得直接摸 LLM provider（`self._llm`）、不得内嵌台词表
  `_QUIPS`：统一走渲染桥 / LLM 网关 / `emotion.json`（契约测试会拦）。
- 老用户升级后 `help / keywords / emotion` 会自动刷新，个人 `config.json` 不受影响。
- 塔罗牌面图仍为原图（包体约 252 MB），压缩优化留待后续版本。

## v0.5.6 (2026-08)

**统一帮助系统**：帮助图的渲染、配色、排版、图标全部收归插件端，游戏只写
`help.json`（写 `groups` 分组 / 只写 `commands` 平铺 / `auto_groups:true` 请插件代劳）。
- 三层寻址：游戏 → 分组（可两级）→ 指令，每层都能用别名直达；
  未命中回目录页并给近似建议
- 指令专属图：`「我的纳戒」`这类查看指令挂 `blocks` 即出自己那张图（背包格/装备栏）
- 版式块：`chips` / `table`(可配表头) / `cards` / `stats`(进度条) / `steps`·`flow` /
  `slots` / `bag` / `text`
- 视觉 = 官方 UI Kit（token 与类名逐字取自 `hosted/ui-kit/styles.css`），
  素材（猫娘图标/竖版立绘/徽章）内联进图，离线可用；目录页按内容估高自适应分页
- 砍掉两张游戏侧自绘：`remake` 的 400 行 PIL 人生总结图迁移为
  `cards + stats + table` 数据（删除 drawer.py、图片素材、像素字体）

**渲染桥接（游戏不参与渲染）**：新增 `self.render_help / render_page / send_page`，
游戏只给数据；`render_html` 标记废弃。新增 CI 守卫测试，游戏侧出现 PIL / 浏览器 /
HTML 模板即失败。

**出图走宿主内置浏览器**：优先宿主自己的浏览器查找函数与
`PLAYWRIGHT_BROWSERS_PATH`，启动链先"不指定 exe"，失败才退显式路径；全链失败再降级。

**喵图相册修复**：
- 图库定位回到 `games/neko_photo/data/`（用户可直接丢图），该目录**不进 git**
  （打包默认空图库）；只读安装位置自动退化到宿主私有存储
- 空图库也必有一张图可发：兜底链 = 本地图库 → 动态猫娘表情 → 插件自带 `assets/icon.png`
  （修掉"发指令不出图 / 无法自动发图"）
- 发图频率改由 LLM 决定，插件封顶：最短间隔 / 每小时 / 每日三道闸门；
  被拦返回 `limited:true + retry_after`；定时刷图默认关闭
- 修掉 `0` 被 `cfg or default` 吞掉的配置 bug

**其它**：聊天图片优先内联原始字节（帮助图不再被 JPEG 重编码降质）；9 个游戏补齐
各自的功能分组声明；11 个 md 文档同步更新。

## v0.5.5 (2026-08)

修复旧宿主（未合并 #2835，如 0.9.1）图片显示成代码：旧宿主前端
ReactMarkdown 只开 remark-gfm/rehype-katex（无 rehype-raw），`<img>` HTML
不渲染。PushSender 兜底通道改为标准 markdown 图片语法 `![alt](url)`；
落盘前大图缩放 ≤720px 转 JPEG，防窄窗溢出。新增缩放回归测试。

## v0.5.4 (2026-08)

文档体系重构：README 改为插件市场门面（普通用户视角，架构/入口表/版本表移出）；
版本历史移入 CHANGELOG.md；面板 UI 只展示 docs/ui/ 简版文档（使用指南改猫娘语气，
纯用户视角）；完整开发者文档（rules/pitfalls/ARCHITECTURE/development）保留 GitHub
不进发布包；UI 素材图（logo/yui）从图库目录归位 static/img/，防发图串台；
删除废弃 docs/guide.md。

## v0.5.3 (2026-08)

命令命名规范化：每个游戏指令带专属前缀（猫猫状态/修仙状态/鱼店/海龟汤状态/市场…），
消除跨游戏裸词串台；新增串台回归测试（tests/test_command_naming.py）。

## v0.5.2 (2026-08)

帮助图/图片推送走原生图片通道(#2835)：聊天窗原生图片气泡适配窗口，旧宿主自动回退
markdown 防溢出。

## v0.5.1 (2026-08)

修复游戏指令路由瘫痪（宿主路由选错入口，只留 play_game 可见）；play_game 改用
@llm_tool 静态注册（MC 模式）；文档纠正 LLM 路由结论。

## v0.5.0 (2026-08)

新增 塔罗牌（78张+9牌阵,3主题）、历史上的今天、猫猫进化路、喵图相册（聊天随机发图）
四个小游戏；消息监听+状态锚（LLM不脱节）、后台发图活动窗口门控、弱指令路由
（再来一局回最近游戏）、推送带 target_lanlan（多角色不丢消息）、避坑文档 pitfalls.md。

## v0.4.0 (2026-08)

新增 俄罗斯轮盘（猫粮赌局）、人生重开（天赋/属性重开）两个小游戏；帮助图/卡片精美化
（渐变+圆角,去猫娘头像遮挡）；游戏状态锚（LLM 脱节时自动注入当前游戏指令）；
README 适配插件市场样式。

## v0.3.0 (2026-08)

新增诸天修仙小游戏（境界/炼体、突破渡劫、采集炼丹炼器、拍卖行、仙宠、宗门、
道侣师徒，猫娘全程陪玩）；runtime/LLM 优化；ruff 检查修复。

## v0.2.0 (2026-08)

玻璃拟态面板（开始/停止/状态标签）；schema 驱动中文配置抽屉；海龟汤、猜硬币；
渲染桥；品牌页脚；i18n。

## v0.1.0 (2026-07)

初版：聚合框架（自动发现/注册/分发/存档）+ 钓鱼示例游戏。
