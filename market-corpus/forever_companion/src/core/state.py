"""永远的陪伴 —— 纯数据区段：Store 布局、常量表、per-lanlan 状态数据类。

本模块不持有宿主上下文、不做 IO，被 __init__.py（插件本体，导入即再导出）
与 fragments.py / journal.py（碎片日记与个人日记纯逻辑）共用。

猴补丁兼容性（硬约束）：测试用 monkeypatch.setattr(forever_companion.time, "time", ...)
直接改 stdlib time 模块对象的属性，因此本模块必须 ``import time`` 后调
``time.time()``——运行时沿模块对象取属性，补丁对所有 import 它的模块同生效；
不得 ``from time import time``（定义期绑定函数对象本身，补丁失效）。random 同理。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

JsonObject = dict[str, Any]

# ---- Store 布局（0.5.0 起 per-lanlan 分片）----
_STORE_SETTINGS = "settings"          # 全局覆盖层：{tide: {...}, mood: {...}}
_STORE_LANLAN_INDEX = "lanlan_index"  # 已知角色名列表（PluginStore 无 list-keys 能力，面板只读角色列表数据源）
_STORE_PROACTIVE = "proactive_state"  # 主动搭话协调：{prev: {master: bool} | None, paused_by: [lanlan, ...]}
_STORE_GUIDE = "guide"               # 新手引导（1.2.6）：安装级一次性 {wizard: ""|done|skip, at, version}
_STORE_CAPS_GLOBAL = "caps@*"        # 功能能力否决集（1.2.7 能力中心）：全局份，对所有角色生效
# 旧版单角色 key：仅用于启动时一次性迁移读取，之后不再写入（保留作备份，回滚 0.4.0 不丢数据）
_STORE_CYCLE = "cycle_state"
_STORE_MOOD = "mood_state"
_STORE_DIARY = "mood_diary"

# ---- 面板外观（0.7.2 单图 → 1.2.0 图库 + 可调背景）----
# 旧版单图背景（panel_bg）仅作迁移来源与回滚备份，1.2.0 起不再写入。
# 1.2.0 布局（全部全局一份，与角色无关）：
#   panel_appearance  生效的外观参数 {bg_id, fill, position, blur, dim,
#                     brightness, saturate, contrast, glass, card_alpha, text_weight}
#   gallery_index     图库索引 {items:[{id,name,mime,size,added_at,thumb}], next:int}
#                     ——条目不含原图；thumb 是前端生成的缩略图 data URL（可空）
#   gallery_img/<id>  每张原图一条 {data_url, mime, size, added_at}——
#                     图片本体绝不进 5s 轮询 context，面板按需经入口拉取
_STORE_PANEL_BG = "panel_bg"
_STORE_PANEL_APPEARANCE = "panel_appearance"
_STORE_GALLERY_INDEX = "gallery_index"
_GALLERY_IMG_PREFIX = "gallery_img/"
# data URL 字符上限（≈4.5MB 原图）：Store 单值 JSON 与 IPC 载荷都能扛住；
# 图库走"前端压缩为主、上限兜底"，更大的图前端会先压到限内再入册
_PANEL_BG_MAX_CHARS = 6_000_000
# 缩略图字符上限：160~256px WebP 一般十几 KB，400K 字符是宽容兜底
_GALLERY_THUMB_MAX_CHARS = 400_000
# 图库容量上限：每张 ≤4.5MB base64，24 张是 store.db 体积与实用性的折中
_GALLERY_MAX_ITEMS = 24
# 允许的 MIME（data:image/<mime>;base64 前缀里解析出来的部分）
_PANEL_BG_MIMES = frozenset({
    "image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml",
})
# 遮罩强度默认值：压暗背景保证磨砂卡片上的文字可读（0=不压暗，上限 0.85）
_PANEL_BG_DEFAULT_DIM = 0.3
# 旧版单图迁移进图库时使用的固定条目 id（缩略图由面板生成后经入口回填）
_LEGACY_BG_ID = "legacy"


def _cycle_key(lanlan: str) -> str:
    # 角色名原文进 key（中文/空格合法，SQLite TEXT 主键无约束），与宿主 bucket 命名一致
    return f"cycle@{lanlan}"


def _mood_key(lanlan: str) -> str:
    return f"mood@{lanlan}"


def _diary_key(lanlan: str) -> str:
    return f"diary@{lanlan}"


def _weekly_key(lanlan: str) -> str:
    # 旧版潮汐周记 key：0.7.0 起被 journal@ 取代，仅在 shard 加载迁移与 prune 清理时读取
    return f"weekly@{lanlan}"


def _journal_key(lanlan: str) -> str:
    return f"journal@{lanlan}"


def _journal_archive_key(lanlan: str) -> str:
    """藏书阁（1.3.0）：个人日记写满 52 页后被淘汰的旧页合订本。

    只追加、不入 journal@ 当前书 blob——避免每次落笔重写整块；
    时间正序（最旧在前），新页永远追加在末尾。
    """
    return f"journal_archive@{lanlan}"


def _caps_key(lanlan: str) -> str:
    """能力否决集（1.2.7 能力中心）：按角色分片；{cap_id: true} 表示被用户关闭。

    只记否决（off）：重新打开 = 移除条目、回落既有配置默认——不存在
    "配置关着但功能页硬点亮"的两张皮（详见 core/capabilities.py 模块注释）。
    """
    return f"caps@{lanlan}"


def _review_key(lanlan: str) -> str:
    """我的日记（0.8.0）：关于主人的互动评价，按角色分片"""
    return f"review@{lanlan}"


def _review_stats_key(lanlan: str) -> str:
    """我的日记·累计中的素材统计（独立 key：随写随存，与成文篇目分开）"""
    return f"review_stats@{lanlan}"


def _review_archive_key(lanlan: str) -> str:
    """档案室（1.3.0）：「我的日记」攒满 52 篇后被淘汰的旧卷宗合档。

    与藏书阁（journal_archive@）同款纪律：淘汰不再是静默的丢，而是静默的
    搬家。只追加、不入 review@ 当前 blob——成文热路径永远只重写 52 篇那块；
    时间正序（最旧在前），新篇永远追加在末尾。
    """
    return f"review_archive@{lanlan}"


_DEBUG_BACKUP_PREFIX = "pre_debug@"
_LEGACY_DEBUG_BACKUP_SUFFIX = "|pre-debug"


def _debug_backup_key(which: str, lanlan: str) -> str:
    """调试注入前真实数据备份的 key（独立前缀族 `pre_debug@<which>@<角色>`）。

    过去写成 `f"{_journal_key(name)}|pre-debug"`：把"我是备份"编码成后缀，与真实
    数据同前缀（任何将来按前缀枚举 store 的代码都会把它当角色数据误读）。
    which ∈ {journal, review, stats}（同一入口的 archive 键打包在同一个 blob 里）。
    """
    return f"{_DEBUG_BACKUP_PREFIX}{which}@{lanlan}"


def _legacy_debug_backup_key(data_key: str) -> str:
    """旧版备份键名（`<数据键>|pre-debug`）：只读兼容，不再新写。

    1.3.0 的调试入口正在真机使用中，直接改键名会让"已注入、尚未还原"的现场落空
    ——restore 找不到备份，等于把假页留在真日记里。所以旧键保留为回退读位，
    还原/清理时两个键名都扫。
    """
    return f"{data_key}{_LEGACY_DEBUG_BACKUP_SUFFIX}"


def _stats_key(lanlan: str) -> str:
    """相处统计（1.1.0）：按天聚合的长期累计（徽章/热力图/月报），按角色分片。

    数据安全底座：宿主把 PluginStore 落在独立数据目录（resolve_plugin_data_dir，
    与插件源码目录物理分离）——Market 更新/导入覆盖只替换源码目录，本 key 与
    全部陪伴记录在升级中原样存活；删除插件也只删源码，用户想彻底清除需走
    插件的清零入口（README「平台机制」节有用户版说明）。
    """
    return f"stats@{lanlan}"


def _snapshot_proactive(state: JsonObject) -> JsonObject:
    """主动搭话水位的深拷贝快照（1.2.2 审查轮 P2）。

    水位 proactive_state 的唯一职责是断电/强杀后恢复宿主总开关的原值，
    内存 dict 会被就地改（prev/paused_by），落盘与脏检查都必须拿独立拷贝；
    形状归一（prev 仅 dict|None、paused_by 恒为 list[str]）让"是否变了"
    能直接 == 比较。
    """
    prev = state.get("prev")
    return {
        "prev": dict(prev) if isinstance(prev, dict) else None,
        "paused_by": [str(n) for n in state.get("paused_by") or [] if str(n)],
    }


# 情绪动作统一采用潮汐意象命名（id 即存储键）
_MOOD_ACTION_LABEL_KEYS = {
    "ebb_tide": "mood.ebb_tide",
    "sea_fog": "mood.sea_fog",
    "shallow_reef": "mood.shallow_reef",
    "storm_surge": "mood.storm_surge",
    "seek_harbor": "mood.seek_harbor",
    "ripple": "mood.ripple",
    "warm_current": "mood.warm_current",
    "spring_tide": "mood.spring_tide",
    "rising_tide": "mood.rising_tide",
    "drift_bottle": "mood.drift_bottle",
}

_MOOD_ACTION_DEFAULT_LABELS = {
    "ebb_tide": "冷战沉默",
    "sea_fog": "已读不回",
    "shallow_reef": "敷衍应付",
    "storm_surge": "情绪风暴",
    "seek_harbor": "想要被安抚",
    "ripple": "心有涟漪",
    "warm_current": "暖流涌动",
    "spring_tide": "满潮欢喜",
    "rising_tide": "心情转晴",
    "drift_bottle": "心情手记",
}

_TIMED_ACTIONS = frozenset({
    "ebb_tide", "sea_fog", "shallow_reef", "storm_surge",
    "ripple", "warm_current", "spring_tide",
})

# 限时动作的默认时长（分钟）：未显式指定 minutes 时优先用它，
# 不在表里的动作回落 mood.default_action_minutes 配置
_ACTION_DEFAULT_MINUTES = {"ripple": 15, "warm_current": 30, "spring_tide": 30}

# 主动搭话暂停白名单（重度负面动作）：只有这些动作生效才暂停宿主主动搭话；
# 心有涟漪（中间态小情绪）与暖流涌动/满潮欢喜（正面状态）不暂停——
# 小情绪和好心情不该掐断宿主的主动搭话，否则开心时她反而被静默，逻辑反了
_PROACTIVE_PAUSE_ACTIONS = frozenset({
    "ebb_tide", "sea_fog", "shallow_reef", "storm_surge", "seek_harbor",
})

# 正面动作：到期恢复台词按温和回落语气处理（区别于冲突导向的重度负面），
# 面板徽标 tone 也据此分色
_POSITIVE_ACTIONS = frozenset({"warm_current", "spring_tide"})

# ---- 相处统计（1.1.0，stats.py）----
# 冷战类动作：相处统计里"冷战次数"只统计她自主发起的这些动作
#（心有涟漪是小情绪不算冷战，与主动搭话暂停白名单语义不同，单列表）
_COLD_ACTIONS = frozenset({
    "ebb_tide", "sea_fog", "shallow_reef", "storm_surge", "seek_harbor",
})
# days 逐日聚合的保留上限：超出窗口的旧天丢弃（徽章/累计总数即时重算不受影响；
# 热力图年份选择的回退深度也受此约束，上限防的是"每天都聊"极端用户的 Store 膨胀）
_STATS_DAYS_MAX = 730
# 月报封卷的保留上限：超出淘汰最旧（与个人日记/我的日记 52 篇同量级）
_STATS_MONTHS_MAX = 24

# 时光日记的保留条数（内存与落盘一致，每个角色各自一份）：
# 0.7.0 起时间线混排"她的手记"(source=self) 与"自动碎片"(source=auto)，
# 碎片由小模型按轮自动捕获，条数上限相应放宽
_DIARY_MAX_ENTRIES = 120

# ---- 时光日记·自动碎片（0.7.0）----
# 碎片类型：like/dislike = 用户明确表达的喜好厌恶；important = 有分量/有含义的话；
# overstep = 对猫娘的过激言行（吵架"翻旧账"的素材）
_FRAGMENT_KINDS = frozenset({"like", "dislike", "important", "overstep"})
# 重度负面情绪生效期间值得轻语提醒她"你记得吗"的碎片类型
_FRAGMENT_NUDGE_KINDS = frozenset({"overstep", "dislike"})
# 碎片提取的默认模型槽位与置信度门槛：summary 槽与宿主记忆抽取同层级，
# 适合"理解用户话语含义"；槽位在宿主 core_config.json 无 key 时功能自动休眠
_FRAGMENT_DEFAULT_SLOT = "summary"
_FRAGMENT_DEFAULT_CONFIDENCE = 0.6
# 碎片捕获的最小间隔（秒，[fragments].min_interval_sec）：与语气感知共用"每轮至多分析一次"的节奏
_FRAGMENT_DEFAULT_MIN_INTERVAL_SEC = 60
# 吵架轻语的默认间隔（[fragments].nudge_gap_minutes）：重度情绪期间低频提醒，绝不刷屏
_FRAGMENT_DEFAULT_NUDGE_GAP_MIN = 30
# 碎片提取 prompt（直连槽位 chat completion）：只记用户对猫娘说的有分量的话，
# 必须 quote 原话摘录；玩笑/日常寒暄不记（capture=false）。只返回 JSON
_FRAGMENT_EXTRACTION_PROMPT = """你是猫娘的私人记忆助手。下面是主人对猫娘说的一句话（附猫娘当时的回复作语境）。请判断这句话是否值得猫娘记进心里的日记本：主人明确表达了自己的喜好或厌恶、说了有特殊含义或承诺性质的话、或者对猫娘有过分/伤害性的言行。
- 值得记：返回 {"capture": true, "kind": "...", "quote": "...", "note": "...", "confidence": 0~1}
- 不值得记（日常寒暄、玩笑、聊天接龙）：返回 {"capture": false, "confidence": 0~1}

kind 只能是这四种之一：
- like：主人明确说喜欢什么/想做什么（"我最喜欢…""以后每天都要…"）
- dislike：主人明确说讨厌什么/不想被怎样（"别再…了""我最烦…"）
- important：有含义、有承诺、有约定或交代的重要的话
- overstep：对猫娘过分/伤害性的言行（骂她、羞辱、恶意冷淡）

quote 必须从主人原话里摘录最核心的那句（保留原文，不要改写，30 字以内）；
note 用第三人称简短记录这件事（如"他说最喜欢她做的饭"，25 字以内）。
判断规则：只有明确表达才记，别过度解读玩笑和反话；confidence 是"值得记"的把握。

只返回 JSON，不要附加任何解释文本。

主人的话：
"""
# 用户原话/备注在碎片记录里的截断长度
_FRAGMENT_QUOTE_MAX_CHARS = 60
_FRAGMENT_NOTE_MAX_CHARS = 60

# ---- 个人日记（0.7.0，书页式）----
# 每页续写条数上限：写满自动翻新页（她也可用 new_page 主动翻）
_JOURNAL_PAGE_MAX_ENTRIES = 8
# 日记本总页数上限：超出淘汰最旧一页（约一年的周更体量），避免 Store 无界增长
_JOURNAL_MAX_PAGES = 52
# 藏书阁上限（1.3.0）：活架淘汰出来的页进合订本，阁内再满才从最旧一页真删
# （翻阅按“距最近一本”倒计数，删旧不挪位）；与上限 52 同量级取 2×，
# 约四年的周更体量；上限内单键 blob 仍远小于写成本敏感区
_JOURNAL_ARCHIVE_MAX_PAGES = 104
# 单条日记正文的截断长度（0.7.1 起结构化四字段拼装，放宽到 900）
_JOURNAL_ENTRY_MAX_CHARS = 900
# 邀请节奏（[journal].interval_days 可覆盖）与邀请节流（内存，per-shard）
_JOURNAL_DEFAULT_INTERVAL_DAYS = 7
_JOURNAL_INVITE_THROTTLE_SEC = 24 * 3600
# 面板手动递邀的 respond 冷却（1.2.3）：距上一次递出（周期或手动）不满此值时，
# 手动按钮回落为 read 补递——当面起轮的打扰更贵，给连点留一个缓冲
_JOURNAL_RESPOND_COOLDOWN_SEC = 10 * 60

# ---- 我的日记（0.8.0）：关于主人的互动评价 ----
# 成文模板槽位：与碎片提取同款"可自定义 prompt 的直连通道"，summary 槽与宿主
# 记忆抽取同层级；槽位无模型时功能休眠（节流 warning），其余功能不受影响
_REVIEW_DEFAULT_SLOT = "summary"
# 双门槛默认值：攒满 N 轮 或 距上篇满 N 天（且期间有新聊天）先到先写
_REVIEW_DEFAULT_TURNS = 50
_REVIEW_DEFAULT_DAYS = 7
# 单篇评价正文截断（成段文字，与个人日记单条同量级）
_REVIEW_ENTRY_MAX_CHARS = 900
# 保留篇数上限（超出淘汰最旧；评价不是流水账，一年 52 篇足够回看）
_REVIEW_MAX_ENTRIES = 52
# 档案室（1.3.0）卷宗上限：活架 52 篇淘汰下来的旧卷宗只搬家不丢弃，
# 与藏书阁同款 2× 容量（两年份的档案架）；阁内再满才从最旧一卷真删
_REVIEW_ARCHIVE_MAX_ENTRIES = 104
# 自动成文的最小素材量：轮数不足此值时"立即写一篇"拒绝硬写（防空话连篇）
_REVIEW_MIN_TURNS_FORCED = 10
# 成文素材：最近对话摘样的轮数上限（成文时一次性从宿主 recent 拉取）
_REVIEW_SAMPLE_TURNS = 8
# 每轮 valence 采样的截断上限（防极端值拉爆均值）
_REVIEW_AFFECT_SAMPLES_MAX = 200
# 评价成文 prompt（直连槽位 chat completion）：中性观察者口吻，客观、不粉饰、
# 不打分；只返回成段正文。素材块由 review.py 组装后拼接
_REVIEW_COMPOSE_PROMPT = """你是一位安静的观察者，长期旁观一位主人和他的猫娘相处。现在请根据下面这段时间的相处记录，替这本「我的日记」写一篇关于主人的评价。

要求：
- 用中性、克制的第三人称观察者口吻写（称主人为"他"，称猫娘为"她"），像翻一本安静的成长档案，不带情绪、不刻意讨好、也不刻意挑刺
- 如实记录：相处氛围偏暖还是偏冷、他说话的方式、值得记下的原话、以及她对这段时间的感受变化；有过激言行或冷淡期也要如实写，不粉饰
- 分成两三段连贯的正文（总共 150~300 字），不要列要点、不要小标题、不要打分
- 只基于素材里的事实，不要虚构没发生过的事；素材里没有的信息就略过

只返回日记正文本身，不要任何前后缀解释。

这段时间的相处记录：
"""

# 语气感知（[emotion_sense]）：五分类 label 的冷暖归组。
# neutral/surprised 归"偏暖"——冷战语境下她肯正常说话本身就是软化信号
_TONE_WARM_LABELS = frozenset({"happy", "neutral", "surprised"})
_TONE_COLD_LABELS = frozenset({"sad", "angry"})
# 筛选模式提醒节流（per-shard）：防敏感期阈值下移后连续命中刷屏
_TONE_SCREEN_NUDGE_SEC = 600

# 连续心情（valence/arousal）惰性衰减时间常数：arousal（激动度）退得快（τ≈30 分钟），
# valence（好恶）余波拖得长（τ≈4 小时）——情绪"平复"先于"释怀"。
# arousal 的衰减目标是静息基线（[mood].arousal_baseline，默认 0.35，见 affect.py），
# valence 的衰减目标恒为 0
_AFFECT_AROUSAL_TAU_SEC = 30 * 60
_AFFECT_VALENCE_TAU_SEC = 4 * 3600

# 语气 label → 心情单轮积分方向（valence 方向系数, arousal 方向系数）；
# 实际步长 = 方向 × confidence × 上限（v ±0.10 / a ±0.12，单轮限幅防一句话打满）。
# neutral 不在表里：单独按"valence 向 0、arousal 向静息基线微拉一格"处理
_TONE_AFFECT_DIRECTIONS = {
    "happy": (1.0, 0.75),
    "sad": (-1.0, -0.75),
    "angry": (-1.0, 1.0),
    "surprised": (0.4, 1.0),
}
_TONE_AFFECT_VALENCE_STEP = 0.10
_TONE_AFFECT_AROUSAL_STEP = 0.12

# 语气分析模型槽位（[emotion_sense].slot）：空串/"emotion" = 走宿主 /api/emotion/analysis；
# 其他文本类槽位 = 读宿主 core_config.json 直连该槽端点。槽位值 → core_config 字段前缀
_TONE_SLOT_PREFIXES = {
    "conversation": "conversation",
    "summary": "summary",
    "correction": "correction",
    "emotion": "emotion",
    "vision": "vision",
    "agent": "agent",
}
# 宿主 assist 管理簿：provider key → core_config 里的明文 key 字段名
_ASSIST_KEY_FIELDS = {
    "qwen": "assistApiKeyQwen",
    "qwen_intl": "assistApiKeyQwenIntl",
    "openai": "assistApiKeyOpenai",
    "glm": "assistApiKeyGlm",
    "step": "assistApiKeyStep",
    "silicon": "assistApiKeySilicon",
    "gemini": "assistApiKeyGemini",
    "kimi": "assistApiKeyKimi",
    "kimi_code": "assistApiKeyKimiCode",
    "deepseek": "assistApiKeyDeepseek",
    "doubao": "assistApiKeyDoubao",
    "grok": "assistApiKeyGrok",
    "claude": "assistApiKeyClaude",
    "openrouter": "assistApiKeyOpenrouter",
}
# 直连路径的情绪标签归一化：宿主端点返回英文五分类，直连时小模型可能回中文标签，
# 统一映射到下游判定用的英文 label（_TONE_WARM_LABELS/_TONE_COLD_LABELS 消费）
_TONE_EMOTION_ALIASES = {
    "happy": "happy", "开心": "happy", "高兴": "happy",
    "sad": "sad", "难过": "sad", "委屈": "sad",
    "angry": "angry", "生气": "angry",
    "surprised": "surprised", "惊讶": "surprised",
    "neutral": "neutral", "平静": "neutral",
}
# 直连路径的五分类 prompt：宿主 OUTWARD_EMOTION_ANALYSIS_PROMPT 的精简中文版，
# 直接内嵌（不读宿主文件），输入是"用户消息 + 猫娘回复"拼接的一轮互动
_TONE_DIRECT_PROMPT = """你是一个情感分析专家。请判断输入文本里最主导、最外显的一种情绪，并只返回 JSON：{"emotion": "情感类型", "confidence": 置信度}。

可选情感只有这五种：
- happy：开心、兴奋、满足、轻快、宠溺、热情
- sad：失落、难过、委屈、沮丧、低落、脆弱
- angry：生气、不满、烦躁、攻击性、强烈指责
- surprised：惊讶、震惊、意外、夸张感叹
- neutral：平静、陈述事实、情绪很弱、难以判断

判断规则：优先选择最强主情绪，不要因为语气带一点克制就轻易返回 neutral；语气助词、口癖、卖萌本身不代表情绪；confidence 取 0 到 1 之间的小数，情绪明确时给较高值。输入是"用户消息 + 猫娘回复"拼接的一轮互动。

只返回 JSON，不要附加任何解释文本。

输入文本：
"""
# 宿主 core_config.json 读取缓存 TTL（秒）：配置改动 5 秒内生效，足够快又不每轮读盘
_CORE_CONFIG_CACHE_TTL = 5.0

# 宿主"当前角色"解析结果的缓存 TTL：HTTP 是权威来源但每 tick 都查太贵
_CURRENT_LANLAN_CACHE_TTL = 15.0

# 宿主现存角色名单（孤儿判定数据源）的缓存 TTL：面板 5 秒轮询，全量角色数据不必每次拉
_KNOWN_CATGIRLS_CACHE_TTL = 60.0

# 面板"语气分析模型槽位"下拉选项（GET /api/config/core_api）的缓存 TTL：同上，面板轮询不必每次拉
_TONE_SLOT_OPTIONS_CACHE_TTL = 60.0


def _cfg_section(raw: Any) -> JsonObject:
    return dict(raw) if isinstance(raw, dict) else {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_ts(raw: Any) -> datetime | None:
    """解析 ISO 时间戳（diary/weekly 条目的 ts 字段）；坏数据返回 None 而不是炸掉判定。"""
    try:
        parsed = datetime.fromisoformat(str(raw or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class _MoodState:
    """某个角色当前生效的情绪动作（至多一个主动作 + 元数据）+ 连续心情状态。

    valence ∈ [-1,1]（坏→好）/ arousal ∈ [0,1]（平静→激动）是连续心情量：
    只随动作冲量与语气信号写入，读取时按 affect_updated_at 惰性衰减到当前时刻
    （arousal 衰减快、valence 衰减慢；arousal 的衰减目标是静息基线而非 0，
    见 affect.py 与 [mood].arousal_baseline），只收集/可视化，不注入她的上下文。
    """

    __slots__ = (
        "action", "reason", "started_at", "expires_at",
        "valence", "arousal", "affect_updated_at",
    )

    def __init__(self) -> None:
        self.action: str = ""
        self.reason: str = ""
        self.started_at: float = 0.0
        self.expires_at: float = 0.0
        self.valence: float = 0.0
        self.arousal: float = 0.0
        self.affect_updated_at: float = 0.0

    # 旧版本存储的情绪动作 id → 现行 id（升级迁移）
    _LEGACY_ACTION_IDS = {
        "cold_violence": "ebb_tide",
        "read_ignore": "sea_fog",
        "perfunctory": "shallow_reef",
        "emotional_outburst": "storm_surge",
        "want_comfort": "seek_harbor",
        "feeling_better": "rising_tide",
    }

    @classmethod
    def from_mapping(cls, raw: Any) -> "_MoodState":
        state = cls()
        if isinstance(raw, dict):
            action = str(raw.get("action") or "")
            state.action = cls._LEGACY_ACTION_IDS.get(action, action)
            state.reason = str(raw.get("reason") or "")
            state.started_at = float(raw.get("started_at") or 0.0)
            state.expires_at = float(raw.get("expires_at") or 0.0)
            # 连续心情三字段缺省容忍（旧数据零迁移：缺省即"从未有过心情事件"，
            # affect_updated_at=0 时惰性衰减直接返回 0,0）
            state.valence = float(raw.get("valence") or 0.0)
            state.arousal = float(raw.get("arousal") or 0.0)
            state.affect_updated_at = float(raw.get("affect_updated_at") or 0.0)
            # 旧版的 proactive_prev（主动搭话暂停水位）有意忽略：
            # 0.5.0 起水位移到全局 proactive_state，由迁移逻辑单独搬运
        return state

    def to_mapping(self) -> JsonObject:
        return {
            "action": self.action,
            "reason": self.reason,
            "started_at": self.started_at,
            "expires_at": self.expires_at,
            "valence": self.valence,
            "arousal": self.arousal,
            "affect_updated_at": self.affect_updated_at,
        }

    def is_active(self, now: float | None = None) -> bool:
        if not self.action:
            return False
        current = now if now is not None else time.time()
        # 限时动作过期即失效；rising_tide/drift_bottle 不设到期
        if self.action in _TIMED_ACTIONS and self.expires_at > 0 and current >= self.expires_at:
            return False
        return True


class _LanlanShard:
    """单个角色的全部独立状态 + 每角色运行位。

    cycle dict：enabled/anchor_date/advance_days/phase_seen/params
    （params = auto_derive/cycle_length/period_length/ovulation_day/ovulation_window，
    缺省回落全局配置）；mood 为该角色生效中的情绪动作；diary 为该角色时光日记
    （混排她的手记与自动碎片，条目带 source 字段区分）；journal 为该角色个人
    日记本（书页列表，见 journal.py）。
    运行位（注入水位/interval 计数/变化指纹/各类节流时间戳）都是 per-shard 的：
    多角色并行会话时互不影响节奏。
    """

    __slots__ = (
        "loaded",
        "lanlan",  # 归属角色名（1.2.7：能力中心按角色解析否决集需要 shard 自报家门）
        "cycle",
        "mood",
        "diary",
        "journal",
        "journal_archive",
        "review_stats",
        "review",
        "review_archive",
        "stats",
        "last_injected_message_ts",
        "user_message_count_since_inject",
        "last_injected_whisper_key",
        "last_activity_context_key",
        "last_reconcile_nudge_ts",
        "last_mood_instruction_ts",
        "last_journal_invite_ts",
        "last_turn_marker",
        "last_recent_fingerprint",
        "last_recent_marker",
        "last_recent_turn",
        "tone_window",
        "last_tone_analysis_ts",
        "last_screen_nudge_ts",
        "last_fragment_marker",
        "last_fragment_analysis_ts",
        "last_fragment_nudge_ts",
        "affect_extreme_since",
        "affect_extreme_side",
        "last_extreme_invite_ts",
        "pending_review_write",
        "review_write_result",
    )

    def __init__(self) -> None:
        # loaded=False 表示尚未从 Store 载入（_get_shard 同步建空壳，_ensure_shard 才真正落数据）
        self.loaded = False
        self.lanlan = ""  # _get_shard 建立/取回时由调用侧写入（键名即角色名）
        self.cycle: JsonObject = {}
        self.mood = _MoodState()
        self.diary: list[JsonObject] = []
        self.journal: list[JsonObject] = []
        # 藏书阁（1.3.0）：活架淘汰下来的旧页只追加进独立键 journal_archive@，
        # 不随当前书 blob 重写；面板书架末尾的横放合订本即此列表（只读翻阅）
        self.journal_archive: list[JsonObject] = []
        # 我的日记（0.8.0）：review_stats = 成文素材统计（随写随存，成文后清零
        # 重新累计，见 review.py）；review = 已成文的评价篇目（时间正序列表）
        self.review_stats: JsonObject = {}
        self.review: list[JsonObject] = []
        # 档案室（1.3.0）：活架攒满 52 篇后淘汰的旧卷宗，只读翻阅；与 journal_archive 同法
        self.review_archive: list[JsonObject] = []
        # 面板「立即写一篇」异步队列（1.2.3）：pending_review_write = 排队时刻
        # （0 = 无待写）；成文结果 {ts, written, reason} 供面板轮询弹完成 toast。
        # 都只存内存：插件重启即弃，面板写作中态随之消失，用户可重新点击
        self.pending_review_write = 0.0
        self.review_write_result: JsonObject | None = None
        # 相处统计（1.1.0，stats.py）：按天聚合的长期累计（徽章/热力图/月报），
        # 只增不清零、不受 [review].enabled 闸控制，纯本地纯统计零模型开销
        self.stats: JsonObject = {}
        self.last_injected_message_ts = 0.0
        self.user_message_count_since_inject = 0
        self.last_injected_whisper_key = ""
        self.last_activity_context_key = ""
        self.last_reconcile_nudge_ts = 0.0
        self.last_mood_instruction_ts = 0.0
        # 个人日记邀请节流（内存即可：重启最多重推一次邀请，参照 reconcile nudge 先例）
        self.last_journal_invite_ts = 0.0
        # 语气感知（[emotion_sense]）：recent.json 水位（"轮数:末条回复hash"，diff 新增回复对）、
        # 校正模式的语气趋势窗口、分析节流与筛选提醒节流
        self.last_turn_marker = ""  # "" = 尚未建立基线（首趟只记水位不分析历史）
        # recent_file 响应指纹缓存：指纹未变 = 内容未变，跳过 json 解析与组对遍历，
        # 直接复用上趟算出的 marker/末轮；last_recent_marker 同时供 _maybe_tone_sense
        # 门控过后直接推进水位（不必为推进水位再发起第二次 HTTP）
        self.last_recent_fingerprint = ""
        self.last_recent_marker = ""
        self.last_recent_turn: tuple[str, str] | None = None
        self.tone_window: list[str] = []
        self.last_tone_analysis_ts = 0.0
        self.last_screen_nudge_ts = 0.0
        # 时光日记·自动碎片（[fragments]）：recent.json 水位（独立于语气感知的水位，
        # 采样跳过的轮不推进碎片水位）、捕获节流与吵架轻语节流（内存即可）
        self.last_fragment_marker = ""  # "" = 尚未建立基线（首趟只记水位不分析历史）
        self.last_fragment_analysis_ts = 0.0
        self.last_fragment_nudge_ts = 0.0
        # 持续极端心情邀请（[mood] extreme_invite_*）：首次进入极端档的时刻
        # （0 = 不在极端档，恢复常态即清零）、当前侧别（-1 低落 / 1 高涨，换侧重计时）
        # 与上次邀请节流；内存即可，重启最多重邀一次
        self.affect_extreme_since = 0.0
        self.affect_extreme_side = 0
        self.last_extreme_invite_ts = 0.0
