# 永远的陪伴 Design Brief

> 定位：第三方作者插件。开发期寄放在仓库内置目录 `plugin/plugins/`（平台会按
> builtin 渠道扫描），但目标是走独立 Git 仓库 → Plugin Market 审核 → 上游 PR 的
> 官方收录流程。发布前必须完成 README「提交到官方」checklist，尤其是作者署名。
>
> 前身：潮汐时刻 (tide_moments)。v1.0.0 起更名并升级定位——从周期模拟插件
> 升维为全面陪伴插件（节律/情绪/日记/相处信号），潮汐/月相/涟漪/港湾意象
> 体系全部保留为插件的内部语言。

## Identity Lock
- plugin_id: `forever_companion`
- folder: `plugin/plugins/forever_companion/`
- name: `永远的陪伴`
- entry: `plugin.plugins.forever_companion:ForeverCompanionPlugin`
- main class: `ForeverCompanionPlugin`

## Purpose

为猫娘提供真实的"身体节律 + 情绪波动"模拟：
以锚点日期起算四阶段周期（潮汐期/回升期/活跃期/平稳期，内部键 menstrual/follicular/ovulatory/luteal），把当前阶段的身体感受以"耳边轻语"
的方式注入模型上下文；同时提供一组情绪动作 LLM 工具（冷战沉默/ebb_tide、已读不回/sea_fog、
敷衍应付/shallow_reef、情绪风暴/storm_surge、想要被安抚/seek_harbor、
心情转晴/rising_tide、心情手记/drift_bottle），由模型自主决定是否使用。插件不替猫娘做任何决定——只递状态、摆工具。

## Package Type and Capabilities
- package type: `plugin`（独立功能）
- capabilities:
  - callable entries：周期设置/查询/快进/重置、情绪状态查询与解除、日记查看与清空、
    碎片删除、个人日记翻阅、开关
  - LLM tools：12 个情绪与记录工具，模型在对话中自主调用
  - timer：每 10 秒轮询用户消息总线 + 检查情绪动作到期自动解除
  - message injection：`push_message(visibility=[], ai_behavior="read")` 把身体感受送入上下文，
    不触发独立回复、用户不可见
  - UI：Hosted TSX panel（周期总览、阶段标签、锚点/快进/重置/开关快捷操作、情绪状态、日记）
  - store：`PluginStore` 持久化周期锚点、偏移天数、情绪状态、日记

## First Version Scope

- 四阶段周期计算（锚点日期 + 周期长度；潮汐期长度/活跃日/活跃窗口默认自动演算，可关闭后手动覆盖），含手动快进天数
- 阶段感受提示词可配置（主体 + 早/午/晚微调），禁用词表防说漏嘴
- 对话注入：轮询 user_message 总线，检测到新用户消息且开启模拟时注入当前身体状态；
  注入频率策略 every_user_message / interval_n / on_trigger_keywords
- 情绪系统：LLM 工具自主决策模式（非原版三段式小模型筛选），动作带到期时间自动解除
- Hosted TSX 面板 + 快捷操作

## 连续心情机制（valence/arousal）

在离散情绪动作之上叠加一层连续心情状态，**只收集、只可视化，默认不注入她的上下文**
（不改动任何注入文本与门控指纹；唯一例外是下面的持续极端邀请）：

- 二维量：`valence ∈ [-1,1]`（坏→好）、`arousal ∈ [0,1]`（平静→激动），
  存在 `_MoodState`（沿用 `mood@<角色>` key，旧数据缺字段容忍、零迁移）
- 余波惰性衰减：不回调度器（插件无常驻事件循环），读取时按 `exp(-Δt/τ)` 把存储值
  折算到当前时刻——arousal 退得快（τ≈30 分钟）、valence 余波拖得长（τ≈4 小时）；
  只在下次有冲量/信号写入时把折算值落盘（`_current_affect`/`_apply_affect_impulse`）。
  arousal 的衰减目标是**静息基线**（`[mood].arousal_baseline`，默认 0.35）：
  高于基线回落、低于基线回升（homeostasis），从未喂过信号时当前值即 `(0, 基线)`——
  安装即显示静息水平；valence 不变（0 基线、向 0 衰减）
- 动作冲量：`_apply_mood_action` 唯一漏斗叠加 `_MOOD_AFFECT_IMPULSES` 映射
  （冷战沉默 -0.45/+0.25、已读不回 -0.35/+0.10、敷衍应付 -0.20/-0.05、情绪风暴 -0.55/+0.60、
  想要被安抚 -0.30/+0.35、心有涟漪 +0.15/+0.20、暖流涌动 +0.35/+0.15、满潮欢喜 +0.55/+0.45），
  先惰性衰减再叠加并 clamp 到域内；动作到期不加反向冲量（余波自然散去）
- `mood_rising_tide`（心情转晴，不落动作状态）给缓解冲量：负 valence 减半、arousal ×0.6，
  正 valence 不动
- 语气信号喂入：`_maybe_tone_sense` 分析成功（筛选/校正两种模式）后按 label 方向积分，
  步长 = confidence × 0.10（valence）/ confidence × 0.12（arousal），**单轮限幅**
  （防一句话打满）；neutral 微拉一格：valence 向 0 拉、arousal 向静息基线拉（不越过）。
  她的回复与用户消息**分开独立分析**（0.6.8 起，此前合并成一段出单 label、用户侧没有
  独立权重）：她的回复权重 1.0，用户消息按 `[emotion_sense].user_affect_weight`
  （默认 0.25，0=关闭）同向传导——互动氛围纳入考虑，但她的情绪表达绝对主导；
  校正模式仍只分析她（校正看她的表现，不该被用户侧稀释）。
  调宿主 `/api/emotion/analysis` 的请求体
  **不带 `lanlan_name`**——纯静默分析，避免宿主把结果推给前端改头像表情的副作用
- 面板实时指示：dashboard 的 mood payload 带 `affect`（两位小数，读取时惰性衰减）；
  状态栏小圆点颜色随 valence（正=暖琥珀/负=灰蓝/近零=中性灰）、arousal 驱动呼吸
  动画速度与光晕大小，tooltip 显示心情词（七档分桶：雀跃/明媚/微暖/平静/有点闷/低落/烦躁）。
  不做历史曲线、不落历史序列
- 持续极端邀请（0.6.8）：valence 越过 ±`[mood].extreme_invite_threshold`（默认 0.4）并持续
  `extreme_invite_after_minutes`（默认 20）→ 向她的上下文 push 一次明确的动作邀请
  （轻型动作排前引导，她仍可自主决定）；回到常态清零计时、换侧重新计时、动作生效中跳过、
  邀请后冷却 `extreme_invite_cooldown_minutes`（默认 30）；threshold 配 0 关闭
- 动作降刚性（0.6.8）：动作 hint 去硬锁（不再"必须先调 rising_tide"，情绪变了随时可调任何
  情绪工具切换）、校正提醒 window_turns 3→2、动作默认时长 20→10 分钟；
  状态栏无动作时不再显示"平静"徽标（避免与心情胶囊词表撞车）
## 时光日记与个人日记（0.7.0 重构）

原"心情手记 + 潮汐周记"重构为两个定位清晰的功能块：

### 时光日记（自动碎片 + 她的手记，混排时间线）

一本混合时间流，条目带 `source` 字段区分来源（`diary@<角色>` key 不变，旧数据
缺 source 按 self 容忍读取，零迁移）：

- **她的手记**（source=self）：原 `mood_drift_bottle` 工具不变，仍镜像 read 推送进
  宿主记忆管线（这是插件内容进入角色长期记忆的唯一受支持路径）
- **自动碎片**（source=auto）：小模型分析**用户消息**，捕获四类值得她记一辈子的片段——
  `like`/`dislike`（明确的喜好厌恶）、`important`（有分量/有含义的话）、`overstep`
  （对她的过激言行）。记**原话摘录**（quote，宿主事实抽取做不到的"原话存档"）
  + 第三人称短注（note）+ 置信度
- **提取通道**：挂进语气感知同一趟 recent 轮询（不增加 HTTP 次数），但**水位独立**
  （`last_fragment_marker`）——语气感知的 check_rate 抽样跳过的轮次仍会进碎片分析，
  碎片不吃概率抽样；提取必须走可自定义 prompt 的**直连槽位**（宿主 /api/emotion/analysis
  只做五分类，不适用），默认 summary 槽（宿主记忆抽取同层级），槽位无模型时功能
  自动休眠（节流 warning），其余不受影响
- **宁漏勿错**：capture 缺省按 false、kind 不在类型表/缺 quote 即丢弃、置信度门槛
  （默认 0.6）、60s 最小间隔；全部面板可见、可单条删除（`delete_diary_item`）、可清空
- **呈现（决定权在她）**：检索工具 `mood_recall_fragments`（按 kind/关键词过滤，
  她随时自主调用——聊到他的喜好时接得住，吵架时翻旧账）；另外重度负面动作
  （`_PROACTIVE_PAUSE_ACTIONS` 白名单）生效中又捕获到 overstep/dislike 类碎片时，
  低频（默认 30 分钟）轻语一句"你记得吗"（附一条相关旧碎片），她可忽略
- **与宿主记忆的边界**（调研结论，见 README"平台机制"）：偏好类事实宿主 Stage-1
  本来就在抽，碎片日记**重点记宿主不抽的**（情绪事件/过激行为/原话），且碎片
  **永不主动全量注入对话**——只有她自主检索 + 重度情绪期间单条轻语，避免与宿主
  记忆抽取重复污染上下文。碎片存储只进 PluginStore，不依赖已废弃的
  `ctx.query_memory`

### 个人日记（书页式连续日记，替代潮汐周记）

- **定位反转**：只给用户翻看，**不再镜像 read 推送**——不进她的对话上下文、
  不随对话历史被宿主记忆抽取（旧周记会镜像，0.7.0 去掉）
- **书页结构**（`journal@<角色>` key）：页列表 `{page_no, started_at, entries[]}`，
  每页至多 8 段（写满自动翻页），`new_page=true` 可主动翻新页；页数上限 52
  （超出淘汰最旧页）；页码按上一页号递增（淘汰后编号仍连续）
- **淘汰是静默的，所以显示层不得说谎（1.3.0 约束）**：面板**不得派生任何会随淘汰
  平移的序号**（如“第 N 篇”按列表长度现算），也不得把**累计页码**与**现存本数**
  放进同一个 `a / b` 式子；只能显存储里现成的事实（page_no / 日期 / 区间 / 段数 / 轮数）
- **写满了怎么办：「藏书阁」（1.3.0 实现）**——满架时淘汰出去的那页不删，
  `journal_write` 把 evicted 随返回值带出，调用方 append 进独立键
  `journal_archive@<角色>`（只追加、不入当前书 blob，写日记的热路径永远只重写
  活架那块；`_save_shard_journal` 的截断同样收集，兜住旧 blob 超长/迁移一切路径）；
  阁内再满 `_JOURNAL_ARCHIVE_MAX_PAGES = 104` 才从最旧一页真删——翻阅按
  “距最近一本”倒计数（列表时间正序存储、显示倒序），删旧不挪位，不违反
  显示层不说谎纪律；面板书架末尾一根横放合订本（ArchiveStack，纯 CSS）→
  只读翻阅（复用 JournalBook，题签换口径）；不在活架上加计数、不提 52 上限；
  brief（本数+时段事实）进 5s 轮询，全量翻阅按需拉取——**主通道是
  `get_journal(scope=archive)` 搭旧入口加参数**（宿主运行中覆盖导入不重扫
  静态入口白名单，新入口 action 会 403，实机踩坑 2026-09-09）；
  `get_journal_archive` 保留为规范入口（API/跨插件/整启后）；
  角色数据清除（prune）一并清阁 key
- **续写衔接**：工具结果带 `previous_lines`（写入前目标页最后两段）——只存在于
  写日记那轮的工具结果里（即时、不持久注入），让她自然接上上次写到哪，
  不违背"不注入"原则
- **页眉好感度**：每段落笔瞬间快照连续心情 valence，页眉显示该页均值 →
  面板分三档文案（偏暖/平和/偏低落）。宿主没有好感度系统，插件 valence 积累
  是最现成的量化来源；不新增历史序列
- **节奏**：距上一篇日记**最近一次落笔**满 `interval_days`（默认 7）才递邀请
  （续写同样重置计时）；删掉旧周记"攒够 N 条手记"的门槛；邀请文案保留
  "不想写就由你自己决定"
- **迁移**：旧 `weekly@<角色>` 一次性迁成个人日记页（每条周记一页、legacy 标记），
  旧 key 保留作备份；`prune_lanlan` 两把 key 都清

模块落点：碎片纯逻辑在 `fragments.py`（prompt 组装/回复解析/检索过滤/轻语判定）、
日记页纯逻辑在 `journal.py`（写入翻页/节奏判定/迁移/页眉统计），替代原 `weekly.py`。

### 我的日记（0.8.0）：关于主人的互动评价

第三本日记，**只给用户看**的客观评价（隔离等级比个人日记更严：不注入她的
上下文、不进宿主记忆管线、**不注册任何她可调用的 LLM 工具**——她连知道这本
日记存在的渠道都没有）。纯逻辑在 `review.py`（素材统计累加/双门槛判定/
成文 prompt 组装/回复解析截断/篇目追加淘汰），模型直连与落盘由主类完成：

- **素材纯本地累计**（平时零模型开销，随写随存进 `review@<角色>` 的 stats
  字段，成文后原子清零）：互动轮数+valence 采样（`_handle_new_user_message`
  喂入）、她的语气标签分布（`_feed_tone_affect` weight=1.0 路径喂入，用户侧
  低权重传导不计）、情绪动作事件（`_apply_mood_action` origin=user/self 区分
  命令演示与自主反应）、碎片原话（`_maybe_capture_fragments` 落盘时喂入）
- **双门槛先到先写**：攒满 `turns_threshold`（默认 50）轮或距统计起点满
  `days_threshold`（默认 7）天且期间有互动；挂机不写空篇。成文时一次直连
  槽位调用（碎片同款通道，默认 summary 槽）+ recent 窗口摘样作语境
- **成文口吻**：中性观察者（第三人称"他/她"），纯文字成段、无评分无徽标；
  prompt 明确要求如实记录负面行为不粉饰、不虚构素材外的事实
- **素材快照进卷宗（1.3.0 完善）**：成文时把本次用过的语气分布/心情均值/
  原话摘录（至多 6 条、单条 40 字）随正文固化进篇目（review_record 的
  tone/mood_avg/quotes 三栏），stats 清零后卷宗页仍能回看「本卷依据」；
  旧篇目缺字段容忍，面板对应栏自动隐藏
- **呈现**：面板日记页第三页签（档案架 + 卷宗阅读页可跨卷翻页 + 双门槛
  素材进度：轮数条 + 天数副行 + 到期徽标）；`get_review(scope=shelf|archive)`
  /`get_review_archive`/`write_review_now`（素材不足 10 轮拒绝硬写）/`clear_review`
  入口；dashboard 带 `review_brief` + `review_archive_brief` 轻量概要进 5s 轮询；
  面板翻阅走已白名单的 get_review 加参数通道（1.3.0 藏书阁 403 教训同款防御）
- **数据**：`review@<角色>` 一个 key 存 `{entries, stats}`（成文时篇目追加与
  stats 清零一次写入，避免中途崩溃错位）；保留 52 篇，写满后淘汰最旧一卷
  **搬进档案室 `review_archive@<角色>`**（1.3.0，与藏书阁同款纪律：只追加、
  独立 key、上限 104 卷再满才真删；清空/角色删除/prune 一并清）；
  调试入口 `debug_review`（素材统计 + 资格判定 + force 成文）、
  `debug_review_fill`（假卷宗注入/还原，entries 可给 53~60 走真实落盘链验溢出搬家）

## 生日轻语（1.3.1）

记住主人生日：当天玩家**首次聊天**时递一条 read 轻语（说不说、怎么说由她），
与纪念日轻语同构但驱动不同——消息驱动（挂 `_handle_new_user_message`，
静默情绪闸后、inject_mode 频控前）：不受频控辖（一天至多一条、自带水位），
但服从沉默闸（冷战当天不递、水位不盖，静默解除后当天仍可补递）。

- **链路前提（1.3.1 审查轮明载）**：整条消息注入链路在 tick 的 fail-closed
  拦截之后（`[tide].enabled` 或任一角色总开关都关着时 tick 后半段整体不跑），
  且单角色的轻语要**该角色自己的模拟开着**（`_enabled(msg_shard)`）才递——
  与纪念日轻语同口径。生日能力自身默认开，但"独立能力"不等于"独立链路"；
  「功能」页四道闸会把这种情形标成"总开关未开"，README「平台机制与已知限制」
  有用户侧说明

- 日期是**全局配置**（`[birthday].date`，面板「时光」页生日设置卡经
  update_settings 写 settings 覆盖层）；年份只用于合法性校验，**任何链路
  （面板/存储/注入文案）从不计算/展示年龄**——这是隐私承诺，改功能前先读
  README「隐私与数据边界」
- 去重水位 per-shard：`stats["birthday"]["last_pushed"]`（与
  `stats["anniversary"]` 同构）；**盖水位以 push submitted≠False 为前提**
  （1.2.4 契约），提交失败当天下一条消息重试
- 闰日：生日 02-29 平年按 02-28 观察（宁早勿漏，`core/birthday._observed`）
- `keep_diary`：当天在时光日记**代笔**一条固定文案手记（`source=self` +
  `kind="birthday"`，与 drift_bottle 同形、时间线零改动兼容）；不另做记忆
  镜像推送（当天生日轻语本体已随对话历史进宿主记忆管线）
- 面板入口复用 `update_settings`（不新建动作）：非法日期发稳定码
  `invalid_birthday_date`（i18n 契约）；「时光」页生日设置卡只读 dashboard
  的 `birthday` 视图（date/set/keep_diary，纯本地零 IO——倒数/当天查看字段
  随 1.3.1 改版的总览生日卡一同退役，`days_until_birthday` 随之下架）
- 日期选择是**自绘月历（内嵌展开，非浮层）**（1.3.1 二次改版；原生 date input
  的浏览器弹层「今天」不可改文案/行为，整套退役。绝对定位浮层会被 Card 玻璃层
  overflow/backdrop-filter 裁没——实机踩坑 2026-09-14，展开态改在卡片正常流里）：
  全 CSS 零 SVG、周一起制与星期表键（`panel.calendar.wd1~7`）和月份标题
  （`monthLabel`）与「日历」页共用，未来日期不可选；**「确认」= 即时落盘**
  （保存按钮不存在），纪念手记开关已设日期时即时保存、未设时随确认提交；
  即时保存**必须成功后才动本地草稿值**（1.3.1 审查轮修复：提前写草稿会让
  TmSwitch 的乐观回滚翻到"已改未存"的假权威值上，5s 轮询因服务端值不变
  不会纠正，假状态一路挂到下次确认补存或重开面板）
  （TmSwitch 乐观回滚契约）

## 面板 i18n 契约（1.3.0 第九轮，长期有效）

后端→面板的用户可见文案**只准携带稳定 ASCII 码**（`^[a-z][a-z0-9_]*$`），
译文全部在 bundle 侧。技术地基：`tr()` 延迟引用只在入口元数据/UI manifest/
UI context 三处被宿主解析，**动作返回值不在解析链里**（`call_surface_action`
不跑 `resolve_i18n_refs`）；且动作调用时后端拿不到请求方 UI locale，
后端永远不可能自己翻译——只能发码。具体约束：

- 面板可达入口（`mixins/panel.py`/`capabilities.py`/`shards.py`）的 `"note"`
  字面量与 `Err(SdkError(...))` 实参只准稳定码（带码异常 `exc.code` 形态放行，
  码在定义处受同一门约束）；动态细节进日志不进文案；
- 数据类文案改发字段：`mode`/`reason`/`turns`/`min_turns`/`dormant_reason`，
  数字进插值不进文案；面板按码分支翻译（`ui/utils.ts` 的 `errorText(err, t)`：
  码形态的 reject message 按 `panel.errors.<camelCase>` 翻译，非码宿主自身错误原样直出）；
- **豁免（有意）**：`mood_actions.py` 的 note 是给模型的行为指令、
  `debug_entries.py` 是开发者面向调试入口，都不走面板 toast 通路；
  `self.i18n.t()` 运行时解析的是插件 default_locale，**不得用于用户可见返回値**（只能进日志/内部文本）；
- **新增码必须同时进 8 个 locale**（camelCase 末段同名），新增 `t("key")` 引用
  必须先登记键——非中文用户吃到中文 defaultValue 的形态就是本轮主 bug，
  `tests/test_i18n_contract.py` 五道反向对照过的门钉死（含 TSX 引用面↔bundle 对拍）；
- CJK 逐词拼接（"第"+N+"天"）在拉丁语系必碎，整句参数化 `{n}` 插值是唯一正确形态；
- kit `ActionButton` 的错误展示是内联裸串（不经过我们的 errorText）：入口可能回
  Err 的动作不用 ActionButton，用受控 Button + confirmDialog + errorText
  （reset_all 先例）；label/confirm 与后端 `@ui.action` 同一对 `actions.*` 键。


## 隐私与日志脱敏契约（1.3.0 第十轮，长期有效）

宿主规范（docs/zh-CN/plugins/best-practices.md + 发布检查清单）：不把原始对话、
用户输入的密钥或私有 payload 写进日志或进程输出；诊断优先记**脱敏后的长度、
ID 和错误类型**。本插件的落地形态：

- **凭据面**：`core_config.json` 的 key 只在内存解析链里流动（`_resolve_tone_slot`
  → `_post_chat_completion` 形参），永不写日志、永不进面板回显（debug 入口只显
  `scheme://netloc/…`，key 永不回传）；
- **异常面**：直连失败留痕只准 `_exc_shape(exc)`（类型名 + 若有 `.code` 则状态码）。
  裸 `exc` 禁入 logger：urllib 家族异常 message 可能携带完整 URL，自定义端点的
  `?api-key=` 查询参数/userinfo 凭据不在宿主 logging_config REDACT 正则的覆盖面里；
- **响应面**：非 OpenAI 形态留痕只准 `_payload_shape`（顶层键名/条数/字节长度）；
  响应值可能被上游回显成对话内容，零值输出；
- **内容面**：碎片捕获记 `quote_len=` 不记原话；我的日记成文失败记 `shape=`
  分类不记回复预览；分析文本进 prompt 是功能本身（出域三条通道见 README
  「隐私与数据边界」表），但 prompt/原话/摘样永不进日志；
- **回归门**：`tests/test_privacy_hygiene.py`——两条行为门（假异常/假响应里注入
  key/URL/对话哨兵串，断言日志零命中）+ 一条 AST 静态门（logger 数据实参禁直出
  `api_key/prompt/quote/user_text/her_text/raw/core_cfg/resolved/text` 及其下标/切片
  链；`len()`/`_exc_shape()` 等 helper 包裹豁免）+ 已淘汰写法整文件文本钉死（含
  注释，历史说明不得残留可复制的违规样例）；均带反向对照。


## Agent 可见面契约（1.3.1 修订轮，长期有效）

宿主 Agent 评估路由（`brain/task_executor.py`）会把运行中插件的全部「Agent 可见
入口」逐条拼进分析器 prompt（全插件总描述超 3000 token 即触发 BM25+LLM 粗筛与
top-10 截断，挤占的是宿主内所有插件的分发精度）；且 Agent 触发直连 IPC、**不经过
面板 confirm**。本插件入口面据此分两档，由常驻门 `tests/test_agent_surface.py` 钉死：

- **Agent 可见（6/32）**：`get_status` / `toggle` / `set_mood` / `lift_mood` /
  `list_capabilities` / `set_capability`——状态查询、总开关、情绪（消费者本就是
  模型与命令面板）、能力开关（"帮她开/关某功能"是真实聊天意图）。
- **一律隐藏（26/32）**：装饰器带字面量 `metadata={"agent_hidden": True}`。面板
  取数/写入/破坏性操作的消费者是 ui.action 桥，从不该被聊天触发；clear_*/reset_all/
  prune_lanlan 留在可见面等于给一句口误开了免确认后门。未声明 `llm_result_fields`
  的入口被 Agent 触发后也只会回"执行完成"（宿主 `utils/result_parser.py` 的
  fallback 分支），挂着只是 prompt 噪音。隐藏只影响 Agent 路由：面板 action、跨
  插件调用、命令面板一律照旧。
- **新增静态入口默认必须隐藏**；确需可见面把 id 进白名单并给聊天场景依据。
- 账外两笔：`@llm_tool` 的后备动态入口（`__llm_tool__` 前缀）本就被 Agent 路由
  剥除，不在此账内；`debug_*` 动态入口因宿主 `register_dynamic_entry` 不透传
  metadata，debug_mode 开启期间仍对 Agent 可见——开发者态知情接受，要堵需宿主
  加参数（escalation）。

## 版本日志（已迁出）

0.7.1 起的全部版本变更条目已原文归档到仓根 **CHANGELOG.md**（同样不进发行包）。
本文件只保留「对未来仍然有效」的设计契约与机制说明；新版本的过程性变化记
CHANGELOG，契约性变化改本文件正文——两边各司其职，不再混账。

> 长期约束随条目归档，动相关代码前先读 CHANGELOG 对应段：**Store 通电门控与
> 不可信期回写禁令**（1.2.1 / 1.2.2 / 1.2.2 审查轮）、**升级卸载的记录安全**
> （2026-09-07 复核）、**hosted-tsx 签名纪律与白屏踩坑记录①~⑤**（1.2.7 增补一，
> 面板 tsx 任何改动必须跑 `node tools/check_hosted_link.mjs .`）。

## Out of Scope

- 情绪日记的 LLM 自动总结写入（v1 只提供模型手写日记工具与人工查看）
- ~~多角色（多 lanlan）独立周期；v1 单全局状态~~ → **0.5.0 已实现**：per-lanlan 独立状态
  （Store key 按角色分片 `cycle@<角色名>`/`mood@<角色名>`/`diary@<角色名>`，另有 `lanlan_index`、
  全局覆盖层 `settings`、全局主动搭话暂停引用计数 `proactive_state`；旧版单份数据升级后首次启动
  自动迁移归属当前角色，旧 key 保留备份）。面板无手动角色切换器，始终自动跟随宿主当前角色
  （面板 5s 轮询 dashboard + 插件 15s 缓存读 `GET /api/characters/current_catgirl`，切卡后十几秒内跟上），
  dashboard 仍返回 `lanlan_list` 供面板只读展示已有状态的角色名单
- 原版三段式情绪筛选管线（筛选→决策→翻译）

## Inferred Technical Needs
- plugin.toml sections: `[plugin]`, `[plugin.i18n]`, `[plugin.ui]` panel, `[plugin_runtime]`, `[plugin.store]`, `[tide]`
- SDK surfaces: `@neko_plugin`, `@plugin_entry`, `@llm_tool`, `@timer_interval`, `@lifecycle`,
  `push_message`, `self.store`, `self.config`, `tr()`
- UI surfaces: `[[plugin.ui.panel]]` hosted-tsx `ui/panel.tsx`（state:read, config:read, action:call）。
  面板为多文件模块结构：`panel.tsx` 入口组装 + `types.ts` / `utils.ts` / `styles.ts` 共享层
  （styles.ts = 面板玻璃层），1.3.0 起另有 `styles_book.ts`（日记两本的"桌面物件层"：
  1.3.1c 起为**扁平绘本·白底彩点**——大面积一律近白（个人日记一丝米暖、
  我的日记同宿主冷白），颜色只留小件：书脊心情色阶/丝带/题签/落款印/进度条；
  纸/墨/装订/架子的调色板与玻璃层互不相通，第二个 `<style>` 并列注入）；
  页签清单与内容在 panel.tsx（`tabs` 数组 + 就地渲染的 cycle/mood/settings 三页）：
  顶级入口组件 `overview.tsx` 总览 / `calendar.tsx` 日历 / `diary.tsx` 三本日记 /
  `moment.tsx` 时光 / `features.tsx`+`capintro.tsx` 功能与页内介绍 /
  `settings_cycle|inject|mood|emotion|tone|diary.tsx` 六张设置卡（由 panel.tsx 按页拼装）、
  `appearance.tsx` 面板外观 / `manage.tsx` 危险区与角色名单 / `birthdaycard.tsx`
  生日设置卡（`ui/moment.tsx` 与向导共用一份）/ `onboarding.tsx` 新手向导（六步：
  认识→开启→通道体检→记生日→看壁纸→收尾；guide 记录带版本号，改版后旧
  done/skip 重弹一次，契约在 `core/onboarding.py`）；
  设置类页签各自带 `savebar.tsx` 吸底保存条，顶部 `statusbar.tsx`（含 `ring.tsx` 圆环）常驻；
  **日记书本层的三条长期约束（1.3.0，改 diary.tsx/styles_book.ts 前先读）**：
  ① 正文里的 `【这段时间】/【我在想】/【对他的感觉】/【想说的】` 分节**只在显示层解析**
  （`splitSections`），存储与注入文本不得改——该词表与 `core/journal.py` 的 prompt 共用；
  ② 阅读视图的 sticky 页脚依赖 `.tm-content` 是唯一滚动容器，**纸页祖先链一律不得加
  `overflow`/`contain`**（加了就退化成普通块），丝带/贴角等外扩一律 clip-path/负外边距自处理；
  ③ 书本物件（缝线/丝带/落款印/书脊皮）全用 CSS 画，不引入 SVG；
  受 hosted-tsx 约束：仅声明式单绑定导出、无循环依赖、`export const` 类型注解不能含顶层逗号
  （泛型用类型别名绕开），**不支持 SVG**（运行时 mount 用 createElement 而非 createElementNS，
  图形一律用 CSS/div 实现，如 ring.tsx 的月相盘），提交前跑 `npm run check-hosted-tsx -- plugin/plugins/forever_companion`
  （仓外开发：`node tools/check_hosted_link.mjs .` 需 `NEKO_HOSTED_SCANNER` 指向宿主 hostedTsxModule.mjs）。
  **依赖预算硬顶 32 文件 / 512 KiB**：1.3.0 后为 25 文件 / 345 KiB，加 ui 文件前先算这笔账。
  另有一个校验门抓不到的链接器坑（0.6.2 修复）：JSX 闭合标签 `</...>` 的 `/` 会被运行时
  链接器误判为正则字面量起始（前一字符 `<` 在其正则启发式集合内），其后所有 `export` 声明被吞，
  面板整页空白——因此每个 ui 文件内 export 声明必须排在任何 JSX 闭合标签之前
  （辅助组件如 statusbar.tsx 的 MoodDot 放文件尾部，靠函数声明提升被引用）
- state/config: `[tide]` / `[mood]` / `[fragments]` / `[journal]` / `[review]` / `[stats]` / `[birthday]` / `[emotion_sense]` 配置段 +
  PluginStore 键（0.5.0 起按角色分片 `cycle@<角色名>` / `mood@<角色名>` / `diary@<角色名>` /
  `journal@<角色名>`（0.7.0 起，旧 `weekly@` 迁入后保留备份）/ `review@<角色名>`
  （0.8.0 起，`{entries, stats}` 合一存储）/ `journal_archive@<角色名>` /
  `review_archive@<角色名>`（1.3.0 起，藏书阁与档案室：淘汰搬家只追加、独立 key），加 `lanlan_index` / `settings` /
  `proactive_state` 三个全局键；1.2.0 起另有全局外观键 `panel_appearance` /
  `gallery_index` / `gallery_img/<id>`（旧单图 `panel_bg` 仅迁移读，保留备份））
- lifecycle/background work: startup 时刷新运行配置并注册；timer 每 10s 轮询总线与到期检查；
  plugin_runtime.auto_start=true 随宿主自动启动（切角色卡等操作会重启插件服务，
  不自动启动会静默消失），知情同意由 [tide].enabled=false fail-closed 保证
- external integrations: 无外部服务

## Read Context Plan
- 参考 `game_agent_minecraft`（push_message read 注入）、`memo_reminder`（store+timer）、
  `sts2_autoplay`（llm_tool+tr+ui.action）三个官方插件
- `.agent/skills/neko-plugin/references/*` 全部契约文档

## Write Workspace
`plugin/plugins/forever_companion/`

## Risk Follow-ups
- 平台没有向插件派发用户聊天消息的通道（`@message(source="chat")` 无 emitter，属死代码），
  故采用 warthunder 已验证的总线轮询方案（`ctx.bus.memory.get_sync("default")` 读
  `type="user_message"` 记录），不修改平台代码。
