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
  - timer（tide_tick）：每 10 秒轮询用户消息总线 + 检查情绪动作到期自动解除
  - timer（diary_write_tick）：每 2 秒轮询手动日记队列；慢请求在独立宿主定时线程执行
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
  工具记录是参考，不覆盖角色性格、当前对话或回应决定；无需先调工具才允许改变语气。
  到期只清记录并递内部 read 通知，不发起回复、不宣布消气或原谅。
  道歉关键词与语气趋势只作可忽略提醒，不自动切状态。
  每角色 mood@ 中保存 origin 与最多 40 条真实事件（choose/switch/release/expire），
  dashboard 只展示最近 12 条，原因最多 200 字；旧数据不补造来源或历史。
  历史仅本地面板使用，不进入模型上下文、成文素材或日志，不增加模型调用。
  面板 lift_mood 确认传 expected_lanlan/expected_started_at，后端拒绝旧角色/旧状态；
  用户结束与自主结束分开记录，结束保留连续心情与事件，不替角色和好。
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
  （防一句话打满）；只接受有限且在 [0,1] 内、至少 0.6 的置信度；
  neutral 不主动拉动 valence（仍按时间自然衰减），角色 arousal 以四分之一步回归基线，
  用户的 neutral 不积分。阶段灵敏度仅影响筛选提醒，不降低积分与校正趋势的可靠性门槛。
  她的回复与用户消息**分开独立分析**（0.6.8 起，此前合并成一段出单 label、用户侧没有
  独立权重）：她的回复权重 1.0，用户消息按 `[emotion_sense].user_affect_weight`
  （默认 0.25，0=关闭）同向传导——互动氛围纳入考虑，但她的情绪表达绝对主导；
  用户侧先积分、角色侧最后积分，避免数值到达上限后被用户侧反向拉低；
  校正模式仍只分析她（校正看她的表现，不该被用户侧稀释）。
  默认分析间隔 20 秒，仅检测到新回复才调用模型；保留显式的间隔和抽查设置。
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
  碎片不吃概率抽样；提取必须走可自定义 prompt 的通道（宿主 /api/emotion/analysis
  只做五分类，不适用）。1.3.2 起这条通道有两种走法：`mode=host`（默认）把请求交回
  **宿主自己的模型管线**（`aget_model_api_config` + `create_chat_llm_async`，与宿主
  内置插件同一条路，零配置即用、免费路由下也可用），`mode=custom` 才由插件读
  `core_config.json` 拼端点直连；host 解析不出端点时自动回落直连。默认 summary 槽
  （宿主记忆抽取同层级），两条路都给不出端点时功能自动休眠（节流 warning），其余不受影响
- **宁漏勿错**：capture 缺省按 false、kind 不在类型表/缺 quote 即丢弃、置信度门槛
  （默认 0.6）、60s 最小间隔；全部面板可见、可单条删除（`delete_diary_item`）。
  时光日记与我的日记页面不提供整本清空按钮。
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
- **书页结构**（`journal@<角色>` key）：页列表 `{page_no, started_at, title?, entries[]}`，
  每页至多 8 段（写满自动翻页），`new_page=true` 可主动翻新页；页数上限 52
  （超出淘汰最旧页）；页码按上一页号递增（淘汰后编号仍连续）
- **整页标题（1.3.9 日记更新）**：`mood_journal_write` 可传 `title`，只接受字符串，
  空白合并为单行、最多 40 字。新页可起题，续写只补无标题页，不覆盖已有标题；
  标题随书页持久化和归档。旧页缺字段时仍兼容，不批量改写历史或额外调用模型。
- **篇目与续写显示（1.3.9 日记更新）**：存储书页在面板按「篇」呈现，一张目录卡片对应一篇；
  移除目录、页眉与页脚的写入次数。阅读页页眉和底栏用 `page_no` 显示固定日记编号，
  不派生列表位置序号，也不与现存总量拼成分数。底栏显示标题（缺失时为「未命名日记」），
  「上一篇/下一篇」切换整篇；首次 entry 标为「首次记录」、后续 entry 标为「续写」，
  各自显示完整年月日与时分。目录分数明确标注「目录」，仅用于目录分页。
- **淘汰是静默的，所以显示层不得说谎（1.3.0 约束）**：面板**不得派生任何会随淘汰
  平移的序号**（如“第 N 篇”按列表长度现算），也不得把**累计页码**与**现存本数**
  放进同一个 `a / b` 式子；只能显存储里现成的事实（page_no / 日期 / 区间 / 段数 / 轮数）
- **写满了怎么办：「藏书阁」（1.3.0 实现）**——满架时淘汰出去的那页不删，
  `journal_write` 把 evicted 随返回值带出，调用方 append 进独立键
  `journal_archive@<角色>`（只追加、不入当前书 blob，写日记的热路径永远只重写
  活架那块；`_save_shard_journal` 的截断同样收集，兜住旧 blob 超长/迁移一切路径）；
  阁内再满 `_JOURNAL_ARCHIVE_MAX_PAGES = 104` 才从最旧一页真删——翻阅从最新归档
  开始（列表时间正序存储、显示倒序），按真实页码定位，删旧不重编，不违反
  显示层不说谎纪律；面板目录末尾的归档入口（ArchiveStack，1.3.9 日记更新起显示时段）→
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
- **手动必写（1.3.9）**：面板 `invite_journal` 直接受理后台成文，完整近期对话一轮
  或最近一次落笔之后的新时光日记记录即可写；`journal_materials` 快照最多 12 条记录
  与 8 轮摘样（当前 recent 采集返回一轮），不清空其他日记的素材。
  首人称 prompt 使用当前角色的名字、近期回复与可取到的角色设定；
  设定从宿主已有 `/api/characters` 只读取得，仅取目标角色的 `system_prompt`，最多 2000 字。
  模型使用 `[review].slot` / `[review].mode`，与我的日记共享调用预算和通道熔断，
  不要求我的日记开关开启。上述素材和设定只发送到配置的模型通道，
  生成内容仅进插件 Store，不进入主聊天或宿主记忆；日志只记失败码与形态。
  新篇使用现有标题/正文存储格式，固定另开新页；失败不改篇目和素材。
  `pending_journal_write` / `_journal_writing` 防重复点击与自主工具并发，
  在手动生成期间不递自动邀请；dashboard `journal_write.writing/last_result`
  回传进度和具体失败原因。自动邀请及调试递邀仍保留自主决定机制。
- **自动递邀节流（24h / 调试强邀 10 分钟冷却）的水位 1.3.2 起随 `cycle@<角色>` 落盘**：
  内存态时插件子进程每次重启（切角色卡/改设置/覆盖导入都算）都会给"恒 due"的角色
  重递一条首邀；只有**递成功**那次写盘，通道拒收那次不占窗口
- **迁移**：旧 `weekly@<角色>` 一次性迁成个人日记页（每条周记一页、legacy 标记），
  旧 key 保留作备份；`prune_lanlan` 两把 key 都清

模块落点：碎片纯逻辑在 `fragments.py`（prompt 组装/回复解析/检索过滤/轻语判定）、
日记页纯逻辑在 `journal.py`（写入翻页/节奏判定/迁移/页眉统计），替代原 `weekly.py`。

### 我的日记（0.8.0）：关于主人的互动评价

第三本日记，**只给用户看**的客观评价（隔离等级比个人日记更严：不注入她的
上下文、不进宿主记忆管线、**不注册任何她可调用的 LLM 工具**——她连知道这本
日记存在的渠道都没有）。纯逻辑在 `review.py`（素材统计累加/双门槛判定/
成文 prompt 组装/回复解析截断/篇目追加淘汰），模型调用与落盘由主类完成：

- **素材纯本地累计**（平时零模型开销，随写随存进 `review@<角色>` 的 stats
  字段，成文后原子清零）：互动轮数+valence 采样（`_handle_new_user_message`
  喂入）、她的语气标签分布（`_feed_tone_affect` weight=1.0 路径喂入，用户侧
  低权重传导不计）、情绪动作事件（`_apply_mood_action` origin=user/self 区分
  命令演示与自主反应）、碎片原话（`_maybe_capture_fragments` 落盘时喂入）
- **双门槛先到先写**：攒满 `turns_threshold`（默认 50）轮或距统计起点满
  `days_threshold`（默认 7）天且期间有互动；挂机不写空篇。成文时一次模型调用
  （碎片同款通道：默认 agent 槽 + `mode=host` 走宿主管线）+ recent 窗口摘样作语境
- **成文口吻**：中性观察者（第三人称"他/她"），纯文字成段、无评分无徽标；
  prompt 明确要求如实记录负面行为不粉饰、不虚构素材外的事实
- **手动直接成文（1.3.9）**：`write_review_now` 达到至少 10 轮的素材门槛即排队，
  不做随机选择。两本日记的手动路径共用 `_compose_manual_document`，
  最多两次模型请求，每次最多 45 秒、总期限 90 秒，每次请求前重查预算与熔断；失败保留素材，
  写入失败回滚。受理前与落盘前重查开关和 Store 可用性。
  独立 `diary_write_tick` 消费两本手动队列，不占用用户消息与心情分析的定时线程。
  自动成文在手动写作排队或进行中暂缓；两本日记的成功日志标明 `source=manual/auto`。
  请求超时单独回传 `request_timeout`，不将明确超时算作服务端拒绝并熔断通道。
  手动错误码区分 `request_failed/invalid_reply/channel_rejected/budget_exhausted/persist_failed`；
  无可用端点附 `dormant_reason`，避免预算或通道问题被误报为未配置模型。
- **整篇标题（1.3.9 日记更新）**：同一次模型请求返回 JSON `{title, text}`，不增加模型调用。
  `parse_review_document` 解析标题与正文，兼容外层 JSON 围栏、前后说明和旧纯正文回复；
  标题共用 `normalize_journal_title`，单行、最多 40 字，非法或空标题保留有效正文。
  缺正文、标题替代正文、坏 JSON 和数组回复按成文失败处理，保留原篇目与素材。
  `review_record` 仅在标题非空时增加可选字段，标题与正文同步保存、回滚和归档；
  不为无标题旧篇补造标题，不批量改写历史。原 `parse_review_response` 保留正文兼容入口。
- **素材快照进卷宗（1.3.0 完善）**：成文时把本次用过的语气分布/心情均值/
  原话摘录（至多 6 条、单条 40 字）随正文固化进篇目（review_record 的
  tone/mood_avg/quotes 三栏），stats 清零后卷宗页仍能回看「本卷依据」；
  旧篇目缺字段容忍，面板对应栏自动隐藏
- **呈现**：面板日记页第三页签（带摘录的目录 + 阅读页可跨篇翻阅 + 双门槛
  素材进度：轮数条 + 天数副行 + 到期徽标）；`get_review(scope=shelf|archive)`
  /`get_review_archive`/`write_review_now`（素材不足 10 轮拒绝硬写）/`clear_review`
  入口；dashboard 带 `review_brief` + `review_archive_brief` 轻量概要进 5s 轮询；
  1.3.9 日记更新起 `review_brief.last_ts` 取最后成文时间，面板同时观察篇数与时间，
  使达到 52 篇后新篇替换旧篇仍会自动刷新；旧概要缺字段时按篇数判断。
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

- **凭据面**：两种 `mode` 下明文 key 都会在插件进程内存里过一遍，区别只在来源——
  `host` 由宿主的 `aget_model_api_config` 现算后交回 `resolve_host_slot` 的返回 dict
  （再进 `_host_slot_cache` 存 5 秒），`custom` 由 `_resolve_tone_slot` 从
  `core_config.json` 拼出。`host` 这一侧插件不读配置文件、不推端点、不做地域改写，
  但这不等于"不碰 key"：客户端仍由插件调 `create_chat_llm_async` 构造并由插件发出
  请求，所以**别把它写成凭据隔离**。真要插件不经手凭据，只有语气感知默认槽位那条
  宿主回环端点（prompt 交进去、模型由宿主调）。两条路共守的不变量：key 永不写
  日志、永不进面板回显（debug 入口只显 `scheme://netloc/…` 与 model 名）、永不发给
  该 key 所属服务商以外的任何地址；
- **异常面**：直连与宿主管线两条路的失败留痕都只准 `_exc_shape(exc)`（类型名 +
  若有 `.code` 则状态码）。裸 `exc` 禁入 logger：urllib 家族异常 message 可能携带
  完整 URL，自定义端点的 `?api-key=` 查询参数/userinfo 凭据不在宿主 logging_config
  REDACT 正则的覆盖面里；宿主客户端的异常同样按这条走，不因为它"出身干净"就放宽；
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


## 推送可见面契约（1.3.2，长期有效）

宿主 `push_message` 的 `visibility` 与 `ai_behavior` 是**两个互不参考的出口**
（`plugin/server/messaging/proactive_bridge.py` 明写 "visibility is NOT consulted here"）：
`visibility` 含 `"chat"` 就把 parts **原样画进聊天框**（`app/main_server/character_runtime.py`
的 `render_chat_blocks` 通道，署名取 `source`）；要不要起一轮由 `ai_behavior` 决定。
本插件的推送**全部**是给她看的内部提示（身体轻语 / 阶段开场白 / 日记邀请 / 纪念日 /
生日 / 情绪到期记录 / 语气与碎片轻语），因此：

- **`visibility` 一律 `[]`**（完全省略也等于 `[]`——宿主默认）。要她"主动说一句"靠
  `ai_behavior="respond"` 起轮，而不是把提示词贴上屏。
- 情绪到期使用 `ai_behavior="read"`，只同步记录；阶段开场白继续使用 respond。
- 常驻门 `tests/test_push_visibility.py` 三层：①②行为门真跑开场白与到期记录两条链路，
  断言 `visibility == []`，分别为 respond/read；③AST 静态门扫全插件推送出口
  （直调 `push_message` + 主类注入 services 的 `self._push` 转发口），visibility 只准
  空列表字面量、`**kwargs` 必须静态解析到字面量，解析不出即红；④八种坏形态的反向对照
  逐个必须报红（含"纯转发口的调用方泄漏"）。
- **已知残余**：纯转发口本体（`push=lambda **kw: self.push_message(**kw)`）放行——它不
  决定可见面，交给调用方；调用方只有在**也走出口名**（`push_message` / `_push`）时才被
  覆盖。新开一条自定义转发口，必须同时把它的名字进 `_OUTLETS`。
- 由来（实机 2026-09-21 20:22）：阶段开场白与恢复台词自 1.0.0 起带 `visibility=["chat"]`，
  提示词整段上屏、她随后又说一遍 → 同一段话出现两次；而文案自称"仅给你看的内部提示，
  不要复述本句"，与实际渲染行为自相矛盾。

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
  （styles.ts = 面板材质层；1.3.4 起统一中性表面、细边框，1.3.5 起默认浅蓝强调色与壁纸主题色联动，壁纸模式的顶栏、
  侧栏与保存条同源透明，不再叠加多层卡片底色），`styles_book.ts` 管两本日记的目录与阅读页：
  1.3.9 日记更新起使用浅蓝点缀、不透明纸面与独立明暗 token，第二个 `<style>` 并列注入；
  目录每页最多 8 项；两本日记卡片顶部小字显示固定记录日期，中间大字显示整篇标题，
  下方展示正文摘录；个人日记日期取 started_at，续写不改，我的日记日期取 ts。
  与记录日期相同的标题日期前缀仅在显示层省略，不改写存储；其他历史日期标题保留。
  搜索标题、完整日期和正文。
  阅读页以整页标题为主标题，日期留在元信息；两本日记的无标题旧篇与仅含记录日期的
  标题都显示「未命名日记」。筛选与目录页码由
  DiaryPane 持有，阅读返回保留、切角色清理，不改变原始条目顺序或导航范围。
  个人日记按显示层解析的标题分节，续写间以细线分隔；1.3.9 日记更新起增加首次记录/续写
  标签及完整落笔时间，底栏显示当前标题与持久化编号，导航明确切换篇目。
  我的日记每条记录为一篇完整日记，没有同篇续写；1.3.9 日记更新起底栏用当前标题和
  完整成文日期时间代替阅读位置分数，上一篇/下一篇切换整篇。两本目录页码明确标注目录。
  我的日记正文在前、参考素材附栏在后（宽窗右侧、窄窗下方），互动轮数移入
  可展开素材，目录只保留标题、记录日期和摘录，统计区间在阅读页和悬停详情中提供；
  旧篇目缺字段时隐藏对应项）；
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
  ② 阅读视图的 sticky 页脚依赖 `.tm-content` 是唯一滚动容器，**纸页祖先链不得新增
  `overflow:hidden/auto/scroll/clip` 或 `contain`**（会退化成普通块）。
  书本卡片必须在插件侧用 `overflow:visible` 覆盖宿主 Card 默认裁切；丝带等外扩一律
  clip-path/负外边距自处理；
  书页页脚消费 `--tm-save-clearance`，由 `SaveBar` 向最近 `.tm-content` 写入实测保存条高度
  加底部内边距，窗口与控件尺寸变化时重测、卸载时清理，不得让两条 sticky 栏互相遮挡；
  ③ 日记小图标使用 CSS，不引入 SVG；
  受 hosted-tsx 约束：仅声明式单绑定导出、无循环依赖、`export const` 类型注解不能含顶层逗号
  （泛型用类型别名绕开），**不支持 SVG**（运行时 mount 用 createElement 而非 createElementNS，
  图形一律用 CSS/div 实现，如 ring.tsx 的月相盘）。
  **独立仓开发只改插件，宿主与 Steam 安装目录只读**：提交前跑
  `node tools/check_hosted_link.mjs .`（需宿主同源 scanner；并排布局会自动查找），
  以及 `node tools/hosted_ui_harness.mjs --typecheck --typecheck-only`；
  隔离工具只在系统临时目录复制校验所需 scaffold，不把 probe 或依赖写进宿主。
  浏览器验收用真实 Hosted TSX runtime + sandbox iframe，运行方法与边界见 `tools/HOSTED_UI_QA.md`。
  **依赖预算硬顶 32 文件 / 512 KiB**：正式 1.3.3（开发验收标记 1.3.7）实测为 25 个运行时依赖文件 /
  370,416 字节（约 361.7 KiB，不含入口）；连同入口共链接 26 个模块，加 ui 文件前先算这笔账。
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

## 壁纸主题色联动（正式 1.3.3，开发验收标记 1.3.5）

- 1.3.6 的壁纸默认参数为 `blur=0`、`dim=0.4`、`brightness=saturate=contrast=100`、
  `glass=0`、`card_alpha=0`、`text_weight=100`。前后端归一默认保持同源；
  缺失参数回退新默认，有效的旧存储参数保持原样，不做强制迁移。
  `panel.tsx` 只在可用壁纸实际显示时应用背景材质；无壁纸或等待首张图片时，
  渲染侧使用 `glass=0`、`card_alpha=100` 保留普通卡片底色，不改写草稿或存储参数。
- `ui/theme.ts` 独立实现像素取色，不引入图像依赖。已解码图片按最长边 160px 缩采样，
  RGBA 采样有上限；过滤透明、极暗与极亮像素，通过 RGB 直方图与带权中位切分生成至多
  12 个候选色，按面积、饱和度与亮度选主色，再选有色相区别的辅色或派生协调色。
- 默认主色 `#409eff`、辅色 `#7ba7d1`；无背景、灰阶、无可用候选色、canvas 不可读时
  保持默认浅蓝主题。原始主辅色和各自明暗可读变体共六个 CSS 变量，只挂
  `.tm-appearance-root`，不覆盖宿主根节点，不新增配置字段、入口或 Store 键。
- 原始主辅色用于外观卡的色块与色值；实际控件使用亮度修正后的变体，对白色与
  `#28282c` 基准面的目标对比度为 7。主色控制导航、按钮、开关、滑杆与焦点，
  辅色控制进度；相处热力图使用与默认面板协调的浅蓝活动色阶（1.3.9），
  阶段、心情、成功与警告等语义色不参与联动。
  壁纸模式下着色的选中导航与主要按钮有独立不透明深底，避免遮罩与卡片透明度为 0 时
  彩色标签消失在亮壁纸上；该保护也覆盖 hover 和键盘焦点。
- `readyBg` 一次提交图片 ID、data URL 与配色。主题缓存以图片 ID 和 data URL 共同判定，
  5 秒轮询和背景滤镜变化不重复取色；草稿预览、还原、无壁纸选项与图像解码共享生命周期。
  解码失败沿用上张可用壁纸及其主题，取消或迟到解码不能反向覆盖当前选择。
- 图库请求用独立身份对象标记在飞请求，删除图片及卸载时失效；旧请求的 finally 不能
  清除同 ID 新请求，迟到图片不能把已删除或复用 ID 的缓存写回。删除同时清理图像与主题缓存。
  这些约束由像素单测与隔离 Hosted TSX 浏览器竞态验收共同覆盖。

## 无壁纸的柔和主题（正式 1.3.3，开发验收标记 1.3.7）

- 无壁纸界面独立使用 `.neko-page:not(.tm-has-bg)` 配色，不复用壁纸的高对比度
  修正色充当所有控件用色。原始默认主辅色和 `ui/theme.ts` 的取色算法不变，
  六个壁纸色变量及其对比度门保持原样。
- 正文、强调文字和控件填充分离：浅色正文 `#45515a`，强调字色 `#456f89`，
  雾蓝控件填充 `#81b5d2`；主按钮与选中项使用 `#e8f2f7` 浅底，
  hover 为 `#e0edf4`。边框、阴影和进度填充降低视觉重量，但文字、hover 和
  键盘焦点仍分别验证至少 4.5 / 4.5 / 3 的对比度。
- 深色背景为中性灰 `#272d30`，卡片 `48,54,57`，文字避免纯白硬对比；
  按钮、选中态、开关与进度采用协调的淡蓝灰色。布局、字号、间距与书页纸面不变，
  阶段、心情、成功和危险提示保持语义配色。
- 无壁纸软色变量只在 CSS 命中该状态时生效，选择壁纸后自动退出，取色与材质继续
  沿用壁纸逻辑；无新增 API、配置、存储或语言键，不覆盖用户已保存外观。

## 插件面板动态壁纸（正式版 1.3.8）

- 只修改独立插件；宿主源码、Steam 安装及参考项目只读。壁纸层只覆盖自己的
  Hosted TSX 面板，不注入宿主 DOM，不读取任意本地路径，不启动 Wallpaper Engine。
- 沿用现有图片图库及 `panel_appearance`，增加严格布尔 `motion`（缺省为 true）。
  图片和视频共用 24 项索引；导入不自动选择，外观草稿、还原和底部统一保存不变。
- `ui/wallpaper_import.ts` 只解析用户通过目录选择器授权的文件。仅支持明确声明
  `type=image/video` 的 `project.json.file` 主媒体，拒绝绝对路径、URL、路径越界
  和重名歧义，不执行 web / application，不解包 scene.pkg。
- 复用隐藏 UI 入口 `gallery_add` 的 `video_begin/video_chunk/video_commit/video_abort`
  操作和 `get_gallery_image(item_id, chunk_index=-1)`：默认视频请求返回小型元信息和
  静态封面，指定分块下标才返回对应 Base64，不通过上下文传回整段视频。
- 新视频经公开 SDK `self.data_path("wallpapers")` 定位专用持久目录，
  跟随宿主数据根解析，不固定驱动器、不写安装目录、不接收客户端路径。
  原媒体二进制落盘，内部随机 ID 对应 `.part` 暂存和 `.bin` 已发布文件，
  状态由独立 `gallery_files_v1` 登记；小块 RPC/Base64 只用作传输。
  图片与小型索引继续走 Store 统一读写出口，旧 `gallery_video/<id>` 与
  `gallery_video_chunk/<内部随机ID>/<下标>` 保持双读，不首启搬迁或删除旧库。
- `gallery_video_file/<id>` 文件清单与 `gallery_files_v1` 登记使用明确的 version 1，
  不将路径/URL 等未知字段塞进会剔除字段的旧图库索引。
  未认识的更高清单版本使媒体写入 fail-closed，不能归一成空库后覆写。
  代码与数据分别回滚：保留旧库以支持旧代码读取，不承诺旧代码播放新版文件视频。
- 默认单视频 256 MiB、视频库预算 2 GiB；可调单视频 32..512 MiB、
  总库 64..16,384 MiB；单次读取硬上限 512 MiB，原始块 768 KiB。
  库存、上传预留、暂存与待回收文件均计入预算，目标卷额外保留 64 MiB。
  缩小配额仅阻止新增，不自动删除已有文件。暂存有超时，图库操作跨事件循环串行。
  面板打开或并行实例切换不会仅按 owner 不同回收仍存活的上传；只有 TTL 到期或所属进程
  已结束才清理。恢复已发布记录时必须确认清单对应的 `.bin` 文件仍存在且大小匹配，
  缺失时 fail-closed，保留索引与清单供人工清理或恢复，不把元数据静默当成成功。
  未完成清理时，图片、视频、外观和容量策略写入一律拒绝，直到清理重试成功。
  校验、文件提交和 Store 索引发布不是同一事务，需持久恢复记录与幂等清理；
  取消、超时和重启恢复只能产生完整已发布文件或可回收的未发布素材。
- 后端校验实际文件头、分块序列、长度、规范 Base64、逐块 SHA-256、内部 ID 和
  PNG/JPEG/WebP 封面；实际编码可播放性由浏览器导入前解码确认，不引入编解码库。
  Windows 底层 `os.open` 必须显式使用 `O_BINARY`；不能依赖后续 `fdopen` 的
  二进制模式，因为 CRT 文本打开已可能截去现有文件末尾的 `0x1A`。
  回归素材需包含全部字节值、不同分块内容和边界 `0x1A`，不能只用重复 ASCII。
- 新上传通过 `video_begin.chunk_receipts=true` 协商只读确认能力。
  原分块回包延迟时，前端可发一次 `video_status`，仅以同一上传 ID 的持久登记、
  有效分块号和已登记摘要确认已接收；不以文件存在或长度猜测成功。
  状态查询不写登记、不续期、不触发清理，已清空、过期或非上传状态不能确认。
  探测失败不替代原请求结果；原回包与探测只能有一个推进进度，任务取消中止二者。
  对重开面板的只读 `video_read`，单块或批量主请求等待 1 秒仍未返回时可发起一次
  独立补读，补读限时 3 秒。补读必须复用同一媒体项、起始分块号和分块数，并逐块
  通过与主读取相同的 Base64、序号和精确长度校验；任一合法结果获胜后取消另一请求。补读失败不改变主
  请求的 20 秒超时和最多三次有限重试预算，且不跨面板缓存或并发不同分块。
  未协商该能力时沿用原有有限重试，不依赖宿主私有 API 或额外媒体服务器。
- 重开面板的只读 `video_read` 默认每次请求最多 4 个连续分块（由
  `chunk_count=1..4` 明确表达），后端逐块返回并分别校验；最后一批可以少于 4 块。
  旧后端忽略该参数或只返回一个分块时，前端按单块结果继续读取，不把兼容性失败
  误判成媒体损坏。批量只减少 Hosted 往返，不改变持久格式，也不跨 iframe 保存
  Blob 或分块缓存。
- `ui/media.tsx` 使用原生 video DOM ref、静音循环、静态封面及浏览器 Blob URL。
  固定封面用于主题取色。新素材准备完成后切换，失败保留旧可用背景或使用封面；
  播放开关和系统减少动态偏好停用动态，文档或 iframe 隐藏时暂停，
  五秒上下文刷新不重置播放，切换、删除和卸载释放资源。
  未完成的播放请求去重；临时 AbortError 不标记永久故障，后续可见性检查恢复播放，
  真正的播放拒绝或媒体解码错误仍停止播放、显示封面并上报错误。
- 1.3.10 首屏通过 `get_panel_gallery(include_background=true, prefer_direct=...)`
  在同一图库锁内附带当前壁纸，并复用已扫描的存储摘要；静态模式或系统减少动态时
  不准备播放副本。未返回 `background` 的旧后端继续走单独读取入口。
  磁盘容量数据不跨请求缓存，只合并一次工作线程内的只读扫描。
  直接路径与 Blob 路径由实际背景 video 解码，不先用临时 video 重复验证。
  新视频的首次帧呈现前保留旧背景或封面，解码失败释放候选 video 并保留旧可用项。
  `focus/pageshow` 立即同步播放；支持时使用 `requestVideoFrameCallback` 判断实际帧进度，
  否则看播放时间变化。可见状态连续 4 秒无帧进展时先 seek，再重载同一来源一次，
  再次持续停滞则报错回退；后台暂停不计入停滞。长时间隐藏或失焦后 seek 刷新画面，
  新帧呈现前不显示旧解码帧。检测不能识别所有仍在持续呈帧的编码或 GPU 马赛克。
  dashboard 仍为可见面板 5s 轮询；文档隐藏跳过、可见立即刷新，避免慢请求重叠。
- 设置页内部按外观与壁纸、通用、数据管理分组，视图通过 `hidden` 切换而不卸载，
  草稿、导入任务、目录候选与容量输入保留；底部仍只有统一保存入口。
  外层复用卡片的细描边、圆角、阴影与单层透明材质，内部保持平面分区；
  统一保存条位于外框之外，不受外框裁切或磨砂定位影响。
  壁纸库按 `kind === "video"` 区分视频与图片（旧条目缺 kind 时视为图片），
  分类只筛选列表，不触发选择、加载或保存；导入成功才切到对应类型，
  跨类型还原时显示保存项所属分类。图库增删和存储策略沿用原有即时入口。
  导入方式、背景调节与媒体存储按需展开；进度、取消与失败提示在折叠区外，
  数据重置与残留角色清理保留原有确认和当前角色保护。
- 已成功提交的新视频在当前面板可复用用户选择的本地 File 和生成的封面。
  不自动选中或保存壁纸，只保留一个待选择上传预览，避免无界持有大文件；
  删除、清空、卸载或替换时释放其 Blob URL，不持久化本地 URL 或文件路径。
  重开后优先协商下述直接读取；不支持时经动作读取持久媒体。无旧可用背景时先展示已验证格式的封面，
  显示读取进度和播放准备状态，视频验证完成后再切换动态。
  切换到另一视频失败时保留不同的旧可用背景；无旧或同一项时保留封面并显示错误。
  读取日志记录元信息/分块序号、返回大小、耗时和状态，不记录内容、文件名或路径。
- 视频缓存仅保留已显示项和正在准备项；读取按后端返回的容量/块数上限校验，
  不保留原先固定 64 块的 48 MiB 隐性门槛。兼容路径仍全段组装 Blob 后播放，
  不宣称有界浏览器解码内存。不注册原媒体或整个数据根，不新开生产服务、
  不削弱 Hosted TSX iframe 沙箱，不依赖宿主私有接口，不持久化绝对资源 URL。
- 1.3.8 的直接路径由 `get_gallery_image(prefer_direct=true)` 显式协商，
  仅适用于已验证的 file-backed 视频。通过公开 `register_static_ui` 注册
  SDK 数据根下独立的 `wallpaper_playback` 目录；其中只放空白 index 和
  完整校验后原子发布的 `.mp4/.webm` 副本。暂存位于注册目录之外，原视频、
  上传、配置与 Store 不可由该目录读取。文件名由 MIME、长度和分块摘要派生，
  不包含原文件名、存储 ID 或用户路径；内容摘要不是访问凭证，沿用宿主静态资源
  接口的访问边界，不提供额外鉴权或公开互联网媒体分享能力。
- 播放副本最多两项、总计 512 MiB，同时受图库剩余配额和磁盘空间约束，
  写副本时保留未完成上传预留及 64 MiB 磁盘余量，副本计入 `occupied_bytes`
  并单列 `playback_cache_bytes`。完整复制在一次工作线程调度内用安全二进制
  文件句柄执行，逐块校验原摘要、总长度和实际文件头，检查前后源文件签名；
  未完成的副本不出现在静态目录中。后续元信息请求在原件和副本大小、mtime、
  ctime、inode 签名仍匹配时复用副本，避免重复复制和编码。
- 静态响应采用 `private, no-cache, max-age=0, must-revalidate`；
  当前已检查的宿主 FileResponse 支持 Range，浏览器可直接按需读取。
  跨面板复用指插件数据目录中的已验证副本，不承诺浏览器持久缓存、零磁盘读取、
  原视频元素或播放位置跨 iframe 存活。宿主版本、媒体布局与编码仍影响首次播放。
  删除、清空、开始上传、调整配额和插件 shutdown 回收播放副本；首次直接注册时
  清理崩溃遗留副本。回收失败不能报告完整清理成功，不触碰原媒体以外的业务数据。
- 前端只允许本插件固定静态路径及 64 位十六进制文件名，用公开 `props.host.origin`
  构造临时地址，不从消息体接受任意绝对 URL。直接准备最多等待 3 秒，取消时
  释放探测视频；失败或能力不可用转为现有 Blob 路径。正常直接路径不发分块动作、
  不在 JS 中组装视频 Blob，已完成的直接探测不再重复执行面板的 Blob 解码探测。
  实际背景视频随后发生错误时，当前有效选择最多尝试一次强制 Blob 恢复。
- `get_panel_gallery.media_storage` 暴露版本、backend、容量、占用、暂存、
  待回收和可用空间等小型摘要；`gallery_add(op="video_policy")` 调整配额，
  `gallery_remove(op="clear_media", confirm=true)` 为需确认的全媒体清除。
  面板协议固定为：
  `schema_version=1, backend="files", single_limit_bytes, total_limit_bytes,
  hard_single_limit_bytes=536870912, chunk_bytes=786432, video_bytes,
  pending_bytes, reclaim_bytes, occupied_bytes, available_bytes, reserve_bytes=67108864,
  uploads, storage_reclaim_pending, cleanup_pending, playback_mode="chunked_blob"`;
  容量调整返回同一 `media_storage`，清除成功返回
  `{cleared:true,items:[],appearance,media_storage,storage_reclaim_pending}`。
  清除缺少字面量 `confirm=true` 时只返回 `video_clear_confirmation_required`，
  不删除任何媒体。
  清除旧 `panel_bg` 防止下次迁移复活，清图片/视频 Store 键、文件、封面、
  暂存和删除墓碑，解除壁纸选择；其他日记、统计与设置不删。
  前端同时取消在飞请求并释放媒体 URL、视频元素和本地缓存。
  清理失败保留 `cleanup_pending` 并可重试；已逻辑删除旧 Store 媒体时的
  `storage_reclaim_pending` 表示 SQLite 文件占用不保证立刻回收，不等同删除失败。
  不声称 SQLite 空闲页、OS 缓存或物理介质内容已安全擦除。
- 个人媒体只在运行时持久目录入库，不随 `.neko-plugin` 交付。
  打包同时排除媒体后缀和 `data/wallpapers/temp/staging/backup/published` 等运行目录。
  验收使用隔离 Store 与真实 opaque-origin iframe，
  不启动宿主、不导入 Steam、不读取真实用户日记或聊天数据。

## 数据备份与恢复（2026-10-16）

- `core/backup.py` 定义版本 1 的 `.fc-backup` ZIP：仅 `manifest.json` 与
  `data.json`，包含 SHA256、版本及计数；限制压缩格式、成员数、文件大小，
  拒绝重复 JSON 键、非有限数和不合法的业务结构，不解压到用户路径。
- `BackupMixin.data_backup` 是 `agent_hidden` 的面板动作，提供
  `export/begin/chunk/preview/apply/abort/recover`；上传分块 384 KiB，
  总量上限 256 MiB，单个暂存会话可替换，过期 30 分钟后清理。
  apply 要求与已预览文件 SHA256 对应的 token 和字面量 confirm=true。
- 遍历 SDK 公开 `store.keys()` 后按插件业务键白名单读取，保留原始角色名及
  调试前备份，不读取视频 blob、宿主凭据或整个数据库。GIF/SVG 和检测到的
  PNG/WebP 动画不进入可移植范围。配置仅携带用户覆盖及相对出厂值的差异。
- 全量替换业务键与静态图片，保留目的机视频、动画及其注册簿/原件，
  冲突 ID 重映射并更新背景引用；合计图库超过 24 项提前拒绝。
  保留目的机 proactive_state，由既有监督循环协调，不恢复来源机水位。
- `DataGate` 通过线程锁计数整个入口、timer、工具及动态调试调用，
  在同一个插件实例跨临时事件循环/线程互斥；嵌套调用通过 ContextVar 识别。
  不承诺跨进程事务。恢复前写独立备份和持久回滚日志，再写业务键、
  重建分片/配置/缓存并同步工具状态。重载时 Store 的 Err 不可静默忽略；
  日志只在写入和重载都成功后删除。失败保留保护状态，阻止日常调用。
- startup 先恢复未完成事务；受保护时 dashboard 只返回无 IO 的恢复标志，
  面板显示专用重试视图。成功恢复后刷新配置、图库、背景及统计，丢弃旧草稿。
- 文件经安全二进制句柄写入 SDK data_path 的 `backups/`，不注册到静态目录；
  使用现有 FileDownload 的路径桥打开目录，适配无 allow-downloads 的 iframe。
  导出包与暂存不进入发行 `.neko-plugin`，宿主仅作只读契约与打包工具来源。

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
