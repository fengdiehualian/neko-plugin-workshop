"""永远的陪伴 (Forever Companion)

让猫娘拥有自己的身体节律与情绪波动。

以"潮汐"为核心意象：她的身体与情绪像潮水一样有节律地涨落，
插件只递状态、摆工具，什么时候退潮、什么时候涨潮，由她自己决定。

架构（一段话）：``@timer_interval`` 每 10 秒轮询用户消息总线
（``ctx.bus.memory.get(bucket_id="default")`` 读 ``type="user_message"`` 记录，
记录 payload 带 ``lanlan`` 字段标明归属角色；平台不会主动向插件派发聊天事件，
watcher 订阅机制只支持 messages/events/lifecycle 总线，memory 总线不可订阅，
故轮询是插件侧唯一可行通道）。0.5.0 起采用 per-lanlan 状态分片：每个角色有独立的
开关/锚点/快进天数/周期参数/情绪状态/心情手记（Store key ``cycle@<角色>``/
``mood@<角色>``/``diary@<角色>``，角色名原文进 key、不做 normalize），
注入策略、阶段提示词文案、时区等仍为全局配置（Store key ``settings`` 覆盖层）。
检测到归属某角色的新消息且该角色开启模拟时，用
``push_message(target_lanlan=<角色>, visibility=[], ai_behavior="read")``
把当前阶段的身体感受定向注入该角色的模型上下文——多会话下不指定
target_lanlan 的消息会被宿主整条丢弃。情绪系统把十个动作注册为 ``@llm_tool``
——负面梯度：冷战沉默/已读不回/敷衍应付/情绪风暴/想要被安抚；中间态：
心有涟漪（有点小情绪、一哄就好）；正面：暖流涌动（想黏人撒娇）/满潮欢喜
（开心想分享）；外加通用出口心情转晴与心情手记，
按 kwargs 里的 ``_ctx.lanlan_name`` 归因到具体角色 shard（缺省归因当前角色）；
限时动作到期后由同一个 timer 自动解除并广播恢复。宿主主动搭话的暂停是全局
开关上的引用计数（白名单制：只有重度负面动作生效才暂停，中间态/正面动作
不暂停），水位存 ``proactive_state``。旧版单角色数据（cycle_state/mood_state/mood_diary）
在启动时一次性迁移归属当前角色，旧 key 保留作备份、不再写入。

结构（1.2 拆分、1.3 能力中心，三个子包）：``core/`` 纯函数层（cycle 周期计算/state Store 布局/
affect 连续心情/fragments 碎片/journal 日记页/review 我的日记/stats 相处统计/
capabilities 能力声明表与纯解析——数据进数据出，零 SDK 依赖）；``services/`` 有状态服务（emotion_sense 语气感知/tone_slot 槽位直连）；``mixins/`` 方法层按"对外契约面"拆成八个 Mixin 组合进
主类——capabilities（能力中心：统一开关判定/功能管理面板入口/工具显隐同步）、
shards（分片基建/配置/落盘）、whisper（注入引擎/总线轮询）、senses
（语气感知/碎片/我的日记/日记邀请）、mood_actions（12 个 @llm_tool+情绪状态机）、
host_coord（宿主协调/HTTP/工具韧性）、panel（dashboard+面板入口）、debug_entries
（调试入口）。所有功能开关判定收编至 ``_cap_effective``（否决式：面板「功能管理」
只写关，打开回落既有配置默认；按角色分片 caps@<角色>/caps@*），新增功能模块在
core/capabilities.py 声明表登记一行即自动接入开关/面板/工具生命周期；
SDK 的 entry/llm_tool 发现都遍历 type(self)，Mixin 定义位置无关；
``plugin.toml`` 的 entry 仍指向本文件的 ForeverCompanionPlugin。测试的
``tm.`` 命名空间锚点（tm.time/tm.random/tm.resolve_today/tm.new_stats 等）
由本文件的再导出与 ``_today_str`` 薄方法保持不变。
"""

from __future__ import annotations

import random as random  # 再导出：tm.random 是测试猴补丁锚点（patch stdlib 模块对象属性，所有 import 者同生效）
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    Result,
    SdkError,
    lifecycle,
    neko_plugin,
    timer_interval,
)
from plugin.sdk.plugin import (
    llm_tool as llm_tool,
)
from plugin.sdk.plugin import (
    plugin_entry as plugin_entry,
)
from plugin.sdk.plugin import (
    quick_action as quick_action,
)
from plugin.sdk.plugin import (
    tr as tr,
)
from plugin.sdk.plugin import (
    ui as ui,
)

# 纯数据/纯函数区段的解耦抽出（第一/二批重构）：state.py = Store 布局/常量表/
# _MoodState/_LanlanShard 数据类；weekly.py = 周记资格判定；affect.py = 连续心情数学；
# tone_slot.py = 语气槽位解析与直连。这里导入即再导出——tm._MoodState、
# tm._STORE_SETTINGS、tm._TONE_* 等既有引用路径全部不变；主类只留薄委托方法。
from .core.affect import (
    _MOOD_AFFECT_IMPULSES as _MOOD_AFFECT_IMPULSES,
)
from .core.affect import (
    _apply_affect_impulse as _apply_affect_impulse,
)
from .core.affect import (
    _current_affect as _current_affect,
)
from .core.affect import (
    _feed_tone_affect as _feed_tone_affect,
)
from .core.appearance import (
    APPEARANCE_FILLS as APPEARANCE_FILLS,
)
from .core.appearance import (
    APPEARANCE_POSITIONS as APPEARANCE_POSITIONS,
)
from .core.appearance import (
    appearance_defaults as appearance_defaults,
)
from .core.appearance import (
    clamp_appearance as clamp_appearance,
)
from .core.appearance import (
    gallery_add_item as gallery_add_item,
)
from .core.appearance import (
    gallery_find as gallery_find,
)
from .core.appearance import (
    gallery_next_id as gallery_next_id,
)
from .core.appearance import (
    gallery_normalize_index as gallery_normalize_index,
)
from .core.appearance import (
    gallery_remove_item as gallery_remove_item,
)
from .core.appearance import (
    legacy_to_gallery as legacy_to_gallery,
)
from .core.appearance import (
    parse_image_data_url as parse_image_data_url,
)
from .core.cycle import (
    TideConfigError,
    build_status_payload,
    resolve_today,
)
from .core.cycle import (
    _time_bucket as _time_bucket,
)
from .core.cycle import (
    build_body_whisper as build_body_whisper,
)
from .core.cycle import (
    build_month_calendar as build_month_calendar,
)
from .core.cycle import (
    compute_phase_state as compute_phase_state,
)
from .core.cycle import (
    derive_cycle_params as derive_cycle_params,
)
from .core.cycle import (
    parse_anchor_date as parse_anchor_date,
)
from .core.cycle import (
    randomized_default_anchor as randomized_default_anchor,
)
from .core.fragments import (
    build_fragment_prompt as build_fragment_prompt,
)
from .core.fragments import (
    fragment_record as fragment_record,
)
from .core.fragments import (
    parse_fragment_response as parse_fragment_response,
)
from .core.fragments import (
    recall_fragments as recall_fragments,
)
from .core.fragments import (
    should_nudge_fight as should_nudge_fight,
)
from .core.journal import (
    archive_brief as archive_brief,
)
from .core.journal import (
    assemble_journal_entry as assemble_journal_entry,
)
from .core.journal import (
    fabricate_demo_pages as fabricate_demo_pages,
)
from .core.journal import (
    has_journal_content as has_journal_content,
)
from .core.journal import (
    journal_due as journal_due,
)
from .core.journal import (
    journal_write as journal_write,
)
from .core.journal import (
    migrate_weekly_to_pages as migrate_weekly_to_pages,
)
from .core.journal import (
    page_header as page_header,
)
from .core.onboarding import (
    _GUIDE_VERSION as _GUIDE_VERSION,
)
from .core.onboarding import (
    build_readiness as build_readiness,
)
from .core.onboarding import (
    make_guide_record as make_guide_record,
)
from .core.onboarding import (
    norm_guide_record as norm_guide_record,
)
from .core.onboarding import (
    wizard_pending as wizard_pending,
)
from .core.review import (
    append_review as append_review,
)
from .core.review import (
    build_review_prompt as build_review_prompt,
)
from .core.review import (
    can_force_write as can_force_write,
)
from .core.review import (
    elapsed_days as elapsed_days,
)
from .core.review import (
    fabricate_demo_reviews as fabricate_demo_reviews,
)
from .core.review import (
    parse_review_response as parse_review_response,
)
from .core.review import (
    record_action as record_action,
)
from .core.review import (
    record_fragment as record_fragment,
)
from .core.review import (
    record_tone as record_tone,
)
from .core.review import (
    record_turn as record_turn,
)
from .core.review import (
    review_archive_brief as review_archive_brief,
)
from .core.review import (
    review_due as review_due,
)
from .core.review import (
    review_record as review_record,
)
from .core.state import (
    _ACTION_DEFAULT_MINUTES as _ACTION_DEFAULT_MINUTES,
)
from .core.state import (
    _AFFECT_AROUSAL_TAU_SEC as _AFFECT_AROUSAL_TAU_SEC,
)
from .core.state import (
    _AFFECT_VALENCE_TAU_SEC as _AFFECT_VALENCE_TAU_SEC,
)
from .core.state import (
    _ASSIST_KEY_FIELDS as _ASSIST_KEY_FIELDS,
)
from .core.state import (
    _COLD_ACTIONS as _COLD_ACTIONS,
)
from .core.state import (
    _CORE_CONFIG_CACHE_TTL as _CORE_CONFIG_CACHE_TTL,
)
from .core.state import (
    _CURRENT_LANLAN_CACHE_TTL as _CURRENT_LANLAN_CACHE_TTL,
)
from .core.state import (
    _DIARY_MAX_ENTRIES as _DIARY_MAX_ENTRIES,
)
from .core.state import (
    _FRAGMENT_DEFAULT_CONFIDENCE as _FRAGMENT_DEFAULT_CONFIDENCE,
)
from .core.state import (
    _FRAGMENT_DEFAULT_MIN_INTERVAL_SEC as _FRAGMENT_DEFAULT_MIN_INTERVAL_SEC,
)
from .core.state import (
    _FRAGMENT_DEFAULT_NUDGE_GAP_MIN as _FRAGMENT_DEFAULT_NUDGE_GAP_MIN,
)
from .core.state import (
    _FRAGMENT_DEFAULT_SLOT as _FRAGMENT_DEFAULT_SLOT,
)
from .core.state import (
    _GALLERY_IMG_PREFIX as _GALLERY_IMG_PREFIX,
)
from .core.state import (
    _GALLERY_MAX_ITEMS as _GALLERY_MAX_ITEMS,
)
from .core.state import (
    _GALLERY_THUMB_MAX_CHARS as _GALLERY_THUMB_MAX_CHARS,
)
from .core.state import (
    _JOURNAL_ARCHIVE_MAX_PAGES as _JOURNAL_ARCHIVE_MAX_PAGES,
)
from .core.state import (
    _JOURNAL_DEFAULT_INTERVAL_DAYS as _JOURNAL_DEFAULT_INTERVAL_DAYS,
)
from .core.state import (
    _JOURNAL_INVITE_THROTTLE_SEC as _JOURNAL_INVITE_THROTTLE_SEC,
)
from .core.state import (
    _JOURNAL_MAX_PAGES as _JOURNAL_MAX_PAGES,
)
from .core.state import (
    _KNOWN_CATGIRLS_CACHE_TTL as _KNOWN_CATGIRLS_CACHE_TTL,
)
from .core.state import (
    _LEGACY_BG_ID as _LEGACY_BG_ID,
)
from .core.state import (
    _MOOD_ACTION_DEFAULT_LABELS as _MOOD_ACTION_DEFAULT_LABELS,
)
from .core.state import (
    _MOOD_ACTION_LABEL_KEYS as _MOOD_ACTION_LABEL_KEYS,
)
from .core.state import (
    _PANEL_BG_DEFAULT_DIM as _PANEL_BG_DEFAULT_DIM,
)
from .core.state import (
    _PANEL_BG_MAX_CHARS as _PANEL_BG_MAX_CHARS,
)
from .core.state import (
    _PANEL_BG_MIMES as _PANEL_BG_MIMES,
)
from .core.state import (
    _POSITIVE_ACTIONS as _POSITIVE_ACTIONS,
)
from .core.state import (
    _PROACTIVE_PAUSE_ACTIONS as _PROACTIVE_PAUSE_ACTIONS,
)
from .core.state import (
    _REVIEW_ARCHIVE_MAX_ENTRIES as _REVIEW_ARCHIVE_MAX_ENTRIES,
)
from .core.state import (
    _REVIEW_DEFAULT_DAYS as _REVIEW_DEFAULT_DAYS,
)
from .core.state import (
    _REVIEW_DEFAULT_SLOT as _REVIEW_DEFAULT_SLOT,
)
from .core.state import (
    _REVIEW_DEFAULT_TURNS as _REVIEW_DEFAULT_TURNS,
)
from .core.state import (
    _REVIEW_ENTRY_MAX_CHARS as _REVIEW_ENTRY_MAX_CHARS,
)
from .core.state import (
    _REVIEW_MAX_ENTRIES as _REVIEW_MAX_ENTRIES,
)
from .core.state import (
    _REVIEW_MIN_TURNS_FORCED as _REVIEW_MIN_TURNS_FORCED,
)
from .core.state import (
    _STORE_CYCLE as _STORE_CYCLE,
)
from .core.state import (
    _STORE_DIARY as _STORE_DIARY,
)
from .core.state import (
    _STORE_GALLERY_INDEX as _STORE_GALLERY_INDEX,
)
from .core.state import (
    _STORE_GUIDE,
    _STORE_LANLAN_INDEX,
    _STORE_PROACTIVE,
    _STORE_SETTINGS,
    _LanlanShard,
)
from .core.state import (
    _STORE_MOOD as _STORE_MOOD,
)
from .core.state import (
    _STORE_PANEL_APPEARANCE as _STORE_PANEL_APPEARANCE,
)
from .core.state import (
    _STORE_PANEL_BG as _STORE_PANEL_BG,
)
from .core.state import (
    _TIMED_ACTIONS as _TIMED_ACTIONS,
)
from .core.state import (
    _TONE_AFFECT_AROUSAL_STEP as _TONE_AFFECT_AROUSAL_STEP,
)
from .core.state import (
    _TONE_AFFECT_DIRECTIONS as _TONE_AFFECT_DIRECTIONS,
)
from .core.state import (
    _TONE_AFFECT_VALENCE_STEP as _TONE_AFFECT_VALENCE_STEP,
)
from .core.state import (
    _TONE_COLD_LABELS as _TONE_COLD_LABELS,
)
from .core.state import (
    _TONE_DIRECT_PROMPT as _TONE_DIRECT_PROMPT,
)
from .core.state import (
    _TONE_EMOTION_ALIASES as _TONE_EMOTION_ALIASES,
)
from .core.state import (
    _TONE_SCREEN_NUDGE_SEC as _TONE_SCREEN_NUDGE_SEC,
)
from .core.state import (
    _TONE_SLOT_OPTIONS_CACHE_TTL as _TONE_SLOT_OPTIONS_CACHE_TTL,
)
from .core.state import (
    _TONE_SLOT_PREFIXES as _TONE_SLOT_PREFIXES,
)
from .core.state import (
    _TONE_WARM_LABELS as _TONE_WARM_LABELS,
)
from .core.state import (
    _cfg_section as _cfg_section,
)
from .core.state import (
    _cycle_key as _cycle_key,
)
from .core.state import (
    _debug_backup_key as _debug_backup_key,
)
from .core.state import (
    _diary_key as _diary_key,
)
from .core.state import (
    _journal_archive_key as _journal_archive_key,
)
from .core.state import (
    _journal_key as _journal_key,
)
from .core.state import (
    _legacy_debug_backup_key as _legacy_debug_backup_key,
)
from .core.state import (
    _mood_key as _mood_key,
)
from .core.state import (
    _MoodState as _MoodState,
)
from .core.state import (
    _now_utc as _now_utc,
)
from .core.state import (
    _parse_iso_ts as _parse_iso_ts,
)
from .core.state import (
    _review_archive_key as _review_archive_key,
)
from .core.state import (
    _review_key as _review_key,
)
from .core.state import (
    _review_stats_key as _review_stats_key,
)
from .core.state import (  # 水位快照纯函数再导出（1.2.2 审查轮 P2，host_coord/shards 共用）
    _snapshot_proactive as _snapshot_proactive,
)
from .core.state import (
    _stats_key as _stats_key,
)
from .core.state import (
    _weekly_key as _weekly_key,
)
from .core.stats import (
    anniversary_due,
    mark_anniversary_pushed,
    record_made_up,
    record_mood_event,
    seal_due_months,
)
from .core.stats import (
    backfill_day as backfill_day,
)
from .core.stats import (
    badges_payload as badges_payload,
)
from .core.stats import (
    fabricate_demo_stats as fabricate_demo_stats,
)
from .core.stats import (
    heatmap_payload as heatmap_payload,
)
from .core.stats import (
    month_view as month_view,
)
from .core.stats import (
    new_stats as new_stats,
)
from .core.stats import (
    record_milestone as record_milestone,
)
from .core.stats import record_tone as stats_record_tone
from .core.stats import record_turn as stats_record_turn
from .core.stats import (
    summary_payload as summary_payload,
)
from .mixins.capabilities import CapabilityMixin
from .mixins.debug_entries import DebugEntriesMixin
from .mixins.host_coord import HostCoordMixin
from .mixins.mood_actions import MoodActionsMixin
from .mixins.panel import PanelEntriesMixin
from .mixins.senses import SensesMixin
from .mixins.shards import ShardsMixin
from .mixins.whisper import WhisperMixin
from .services.emotion_sense import EmotionSenseService
from .services.tone_slot import (
    _parse_tone_result as _parse_tone_result,
)
from .services.tone_slot import (
    _post_chat_completion as _post_chat_completion,
)
from .services.tone_slot import (
    _resolve_tone_slot as _resolve_tone_slot,
)
from .services.tone_slot import (
    _slot_dormancy_hint as _slot_dormancy_hint,
)
from .services.tone_slot import (
    diagnose_slot_dormancy as diagnose_slot_dormancy,
)

JsonObject = dict[str, Any]


# 宿主在 LLM 注入边界展开为当前会话的角色名；插件侧不得自行替换
MASTER_NAME_TOKEN = "{MASTER_NAME}"
LANLAN_NAME_TOKEN = "{LANLAN_NAME}"


# 面板背景校验与外观参数归一已收拢到 core/appearance.py（1.2.0），
# 经上方再导出暴露；此处不再保留同款复刻（旧单图时代的双份实现是漂移隐患）

# Store 布局 / key 函数 / 常量表 / 纯工具函数 / _MoodState / _LanlanShard
# 已抽出到 state.py（碎片提取在 fragments.py、个人日记页逻辑在 journal.py），
# 见顶部导入块的再导出


@neko_plugin
class ForeverCompanionPlugin(
    CapabilityMixin,
    ShardsMixin,
    WhisperMixin,
    SensesMixin,
    MoodActionsMixin,
    DebugEntriesMixin,
    HostCoordMixin,
    PanelEntriesMixin,
    NekoPluginBase,
):
    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self._tide_cfg: JsonObject = {}
        self._phases_cfg: JsonObject = {}
        self._forbidden_words: list[str] = []
        self._mood_cfg: JsonObject = {}
        self._fragments_cfg: JsonObject = {}
        self._journal_cfg: JsonObject = {}
        self._review_cfg: JsonObject = {}
        self._stats_cfg: JsonObject = {}
        self._emotion_sense_cfg: JsonObject = {}
        # 语气感知运行态缓存（_csrf_token / _last_tone_error_logged / _core_config_cache）
        # 已随 EmotionSenseService 持有（A5 第 2 批）；_csrf_token 经下方 property 代理
        # （tests 直读 p._csrf_token），另两者无外部引用点、不再代理
        # 全局设置覆盖层（Store key "settings"）：面板保存的全局字段优先于 toml 默认
        self._settings_override: JsonObject = {}
        # 能力中心（1.2.7）：[capabilities] 段合成视图（toml + settings 覆盖层）、
        # 按角色否决集（键：角色名与 "*" 全局份，_ensure_shard/_load_state 时载入）、
        # 已从宿主可见面摘除的工具名（纯运行态，重启由 startup 同步重建）
        self._caps_cfg: JsonObject = {}
        self._caps_off: dict[str, set[str]] = {}
        self._cap_hidden_tools: set[str] = set()
        # per-lanlan 状态分片：惰性从 Store 载入，见 _ensure_shard
        self._shards: dict[str, _LanlanShard] = {}
        self._lanlan_index: list[str] = []
        # 本次启动的载入是否可信（_load_state 里探测 store 是否已通电后置位）。
        # False 期间禁止任何"整体回写"：宿主构造实例时 effective config 还没就位，
        # PluginStore 会以 disabled 建出来，读写静默空转，此时读到的"空"不是真的空，
        # 若照常在 shutdown 回写就会把上次保存的开关/锚点/日记覆写掉。
        # 默认 False：startup 之前（含 startup 失败）的任何 shutdown 都不该覆写。
        self._state_trusted: bool = False
        # 重载入防重入标志：_retrust_state → _load_state → _ensure_shard 会绕回
        # 门控自身，见 shards._ensure_shard 的中途通电门控
        self._retrusting: bool = False
        # 主动搭话暂停的引用计数水位（Store key "proactive_state"）：
        # prev = 暂停前总开关原值（None = 未在暂停中），paused_by = 有生效情绪的角色集
        self._proactive_state: JsonObject = {"prev": None, "paused_by": []}
        # 水位最近一次成功落盘的快照（1.2.2 审查轮 P2）：_persist_proactive_state
        # 的脏检查参照——写失败（DB 锁/磁盘满）时内存已改而快照未动，
        # 监督循环每趟（10s）自动重试直到落稳，把"水位没存住即被强杀"的
        # 卡死窗口从"整个会话"压缩到"两次写尝试之间"
        self._proactive_persisted: JsonObject = {"prev": None, "paused_by": []}
        # 新手引导（1.2.6）：安装级一次性记录 {wizard: ""|done|skip, at, version}
        # （Store key "guide"，全局一份不按角色分片：向导是装完只经一次的事，
        # 角色级欠账由就绪清单从当前状态即时算，无持久化）
        self._guide: JsonObject = {"wizard": "", "at": "", "version": ""}
        # 当前角色解析缓存（15s TTL）；_last_resolved_lanlan 供同步路径（属性代理）回落
        self._current_lanlan_cache: tuple[str, float] = ("", -1000.0)
        self._last_resolved_lanlan = ""
        # 宿主现存角色名单缓存（60s TTL）；None = 未知（孤儿判定一律按 False 处理）
        self._known_catgirls_cache: tuple[set[str] | None, float] = (None, -1000.0)
        # 宿主 API base 缓存：端口首次解析后运行期不变（独立/测试环境每次延迟 import
        # 都会白构造一次 ImportError）；None = 未解析，config_change 时清空重解析
        self._proactive_api_base_cache: str | None = None
        # 面板"语气分析模型槽位"下拉选项缓存（60s TTL，面板 5s 轮询不必每次拉宿主）；
        # None = 未拉取过；失败不缓存（与 _fetch_known_catgirls 同例）
        self._tone_slot_options_cache: tuple[list[JsonObject] | None, float] = (None, -1000.0)
        # 面板潮汐日历缓存：key = (角色, 当天日期, 周期参数指纹)；
        # 参数/锚点/快进/跨天/切角色都会改变 key，自然失效，无需显式清理
        self._calendar_cache: tuple[tuple, list[JsonObject] | None] = ((), None)
        # 节流哨兵用 -1000.0 而非 0.0：部分平台（如 Linux）time.monotonic() 以 0 为纪元，
        # 首条日志恰在 0.0 附近时"仅一次/每 5 分钟"门控会误判为已记录而吞掉日志。
        self._last_bus_error_logged = -1000.0
        self._last_bus_heartbeat_logged = -1000.0
        # 碎片捕获"槽位未解析休眠"warning 的节流水位（同哨兵纪律）
        self._last_fragment_dormant_logged = -1000.0
        # 我的成文"槽位未解析休眠"warning 的节流水位（同上）
        self._last_review_dormant_logged = -1000.0
        # 我的日记在飞成文的角色集合（1.2.3）：同一角色成文期间不允许多条链路
        #（tick 自动 / 面板队列 / 调试入口）并发跑模型，见 _maybe_write_review
        self._review_writing: set[str] = set()
        self._supervise_lock = threading.Lock()
        self._last_tool_health_ts = 0.0
        # 调试模式（[tide].debug_mode）：活动快照覆写与调试入口注册水位；
        # 关闭时不注册任何调试入口，面向用户的表面零痕迹
        self._activity_override: JsonObject | None = None
        self._debug_registered = False
        # 语气感知服务（emotion_sense.py，A5 服务化第 1 批）：主类能力全部经
        # 延迟解析回调注入（调用时现取）——此刻 self._emotion_sense_cfg 还是 {}，
        # 且 _refresh_config/测试会整体替换它，p._proactive_http 等也会被实例级
        # monkeypatch 替换，构造期快照全部会失效；logger 同因（测试桩在构造后
        # 才注入 p.logger，此刻取 self.logger 会 AttributeError）只能延迟解析
        self._emotion_sense = EmotionSenseService(
            http=lambda *a, **k: self._proactive_http(*a, **k),
            # 与 http 同源：Origin 必须等于实际拼 URL 的 base，不能各写一份端口
            api_base=lambda: self._proactive_api_base(),
            push=lambda **kw: self.push_message(**kw),
            cfg_getter=lambda: self._emotion_sense_cfg,
            mood_enabled=lambda shard=None: self._mood_enabled(shard),
            # 能力中心接线（1.2.7）：语气感知的开关判定收编至能力层
            # （含按角色否决集）；未注入时服务回落旧公式（情绪引擎∧配置），
            # 独立测试可省略本参数
            cap_enabled=lambda cap_id, shard=None: self._cap_effective(cap_id, shard=shard),
            phase_state=lambda shard=None: self._current_phase_state(shard),
            # 语气感知收尾落盘（1.2.2 批次2）：_feed_tone_affect 除心情外还把
            # 当日语气分布喂进 stats 与我的日记素材（纯内存），过去只靠"下一条
            # 用户消息"或 shutdown 搭车冲刷——改由本回调一并即时落盘（回调签名
            # 不变，仍 (lanlan, shard)→awaitable；services 侧不动）
            save_mood=lambda lanlan, shard: self._save_tone_sense_state(lanlan, shard),
            feed_affect=lambda shard, label, conf, now=None, weight=1.0: self._feed_tone_affect(
                shard, label, conf, now=now, weight=weight
            ),
            core_config_loader=lambda: self._load_core_config(),
            # 以下四个经主类薄委托中转（tests 实例级 monkeypatch 的是主类方法）
            core_config_path=lambda: self._core_config_path(),
            resolve_slot=lambda *a, **k: self._resolve_tone_slot(*a, **k),
            post_chat=lambda *a, **k: self._post_chat_completion(*a, **k),
            parse_result=lambda *a, **k: self._parse_tone_result(*a, **k),
            # 服务内部跨方法调用回绕主类薄委托：未来对主类这几个方法做实例级
            # monkeypatch 后经 _maybe_tone_sense 间接触发也能命中 patch
            poll_recent=lambda *a, **k: self._poll_recent_turns(*a, **k),
            analyze_tone=lambda *a, **k: self._analyze_turn_tone(*a, **k),
            correction_check=lambda *a, **k: self._tone_correction_check(*a, **k),
            screen_check=lambda *a, **k: self._tone_screen_check(*a, **k),
            plugin_id=lambda: self.plugin_id,
            logger=lambda: self.logger,
        )

    def _today_str(self, cfg: JsonObject | None = None) -> Any:
        """折算"今天"（timezone 解析在 cycle.resolve_today）。

        走主包命名空间的 resolve_today（tests 猴补丁 tm.resolve_today 的契约锚点：
        调用点必须读 forever_companion 模块全局，直接 from .core.cycle import 的本地名
        收不到 patch——拆分后各 mixin 的调用统一经此薄方法转发）。
        """
        source = cfg if cfg is not None else self._tide_cfg
        return resolve_today(str(source.get("timezone") or "auto"))

    # ==========================================
    # 相处统计（1.1.0，stats.py）：按天聚合的长期累计——徽章墙/热力图/月报的
    # 唯一数据源。纯本地统计：零模型开销、不设开关、成文永不清零；只为用户
    # 可视化服务，除纪念日注入（[stats].anniversary_inject，默认开）外绝不
    # 进入她的上下文。埋点与我的日记素材共用驱动点但口径独立（不受
    # [review].enabled 影响、不清零）。
    # ==========================================

    def _stats_tz(self):
        """统计日期折算用的时区（与潮汐日历同源：[tide].timezone）。"""

        name = str((self._tide_cfg or {}).get("timezone") or "auto").strip()
        if not name or name == "auto":
            return None  # None = 系统本地
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - 坏配置回落本地时区，统计不能炸
            return None

    def _stats_today(self, ts: float | None = None) -> str:
        """把时间戳折算成统计用的本地日期字符串（YYYY-MM-DD）。

        走 time.localtime（读模块对象的属性，测试猴补丁 tm.time.time 同生效），
        不用 datetime.fromtimestamp——Windows 上对极端/负值时间戳（测试把时钟
        拨回 1970）会直接抛 OSError；localtime 对不可表示的值回落真实当前时间。
        配置了显式时区时再做一次 astimezone 折算。
        """
        from datetime import datetime

        moment = datetime.fromtimestamp(time.mktime(time.localtime(ts if ts is not None else time.time())))
        tz = self._stats_tz()
        if tz is not None:
            try:
                moment = moment.astimezone(tz)
            except (OSError, OverflowError, ValueError):
                pass  # 极端时间戳（测试拨钟/时钟错乱）折算失败即按本地日期计
        return moment.date().isoformat()

    def _feed_stats_turn(self, shard: _LanlanShard, ts: float | None = None) -> None:
        """记一轮互动进相处统计（每条新用户消息一次，纯内存，落盘由调用方决定）。"""
        valence = self._current_affect(shard)[0] if shard.mood.affect_updated_at else None
        shard.stats = stats_record_turn(shard.stats, self._stats_today(ts), valence=valence)

    def _feed_stats_tone(self, shard: _LanlanShard, label: str) -> None:
        """记一次她的语气分析结果（weight=1.0 主路径）。纯内存累加，即时落盘由
        语气感知收尾 _save_tone_sense_state 随 mood 一起补刷（1.2.2 批次2 起，
        不再等"搭车"下一条用户消息）。"""
        shard.stats = stats_record_tone(shard.stats, self._stats_today(), label)

    def _feed_stats_mood_event(self, shard: _LanlanShard, action: str, *, origin: str) -> None:
        """记一次情绪动作事件（origin=user 的演示不计，与我的日记同口径）。"""
        shard.stats = record_mood_event(shard.stats, self._stats_today(), action, origin=origin)

    def _feed_stats_made_up(self, shard: _LanlanShard) -> None:
        """记一次和好：她在负面情绪生效中主动调心情转晴（tool_feeling_better 判定）。"""
        shard.stats = record_made_up(shard.stats, self._stats_today())

    async def _maybe_anniversary_push(self, lanlan: str, shard: _LanlanShard) -> bool:
        """纪念日注入：今天恰好是相伴第 30/60/…/365/… 天时，递一条 read 轻语。

        与阶段开场白同构：只递一句"你们今天相伴 N 天了"，说不说、怎么说由她
        自己决定。能力中心 anniversary（绑定 [stats].anniversary_inject，默认开）
        可关；当天去重（盖水位在推送之后，推送失败下趟重试）。
        """
        if not self._cap_effective("anniversary", lanlan=lanlan):
            return False
        today = self._stats_today()
        due, total = anniversary_due(shard.stats, today)
        if not due:
            return False
        self.push_message(
            visibility=[],
            ai_behavior="read",
            parts=[{"type": "text", "text": (
                f"（日期感知）今天是你和主人相伴的第 {total} 天。"
                "你或许想自然地提起这个日子，也或许只是心里悄悄知道——由你自己决定，"
                "绝不要提及这条提醒本身。"
            )}],
            source=self.plugin_id,
            target_lanlan=lanlan,
            coalesce_key=f"{self.plugin_id}.anniversary",
            metadata={"message_type": f"{self.plugin_id}.anniversary", "days": total},
        )
        shard.stats = mark_anniversary_pushed(shard.stats, today)
        await self._save_shard_stats(lanlan, shard)
        self.logger.info("anniversary pushed for {} ({} days)", lanlan, total)
        return True

    async def _seal_stats_months(self, lanlan: str, shard: _LanlanShard) -> list[str]:
        """月报封卷：跨月时把上个月快照进 months（幂等，tick 驱动）。"""
        sealed: list[str] = []
        fresh, sealed = seal_due_months(shard.stats, self._stats_today(), diary=shard.diary)
        if sealed:
            shard.stats = fresh
            await self._save_shard_stats(lanlan, shard)
            self.logger.info("stats months sealed for {}: {}", lanlan, sealed)
        return sealed

    async def _save_settings(self) -> Result[None]:
        return await self._store_write(_STORE_SETTINGS, dict(self._settings_override), "settings")

    async def _save_lanlan_index(self) -> Result[None]:
        return await self._store_write(
            _STORE_LANLAN_INDEX, list(self._lanlan_index), "lanlan_index",
        )

    async def _save_proactive_state(self) -> Result[None]:
        return await self._store_write(
            _STORE_PROACTIVE, dict(self._proactive_state), "proactive_state",
        )

    async def _save_guide(self) -> Result[None]:
        return await self._store_write(_STORE_GUIDE, dict(self._guide), "guide")

    # ==========================================
    # 生命周期
    # ==========================================

    @lifecycle(id="startup")
    async def startup(self, **_: Any):
        await self._load_state()   # Store 先载入：覆盖层要参与配置解析
        await self._refresh_config()
        # 能力中心（1.2.7）：工具显隐同步在状态载入后跑一次（高级选项开着
        # 且能力关着时，刚注册的 12 工具里关着的几个当场从宿主可见面摘除）
        self._sync_tool_visibility()
        # 到期情绪清理（所有已载入 shard；重启后状态自愈交给监督循环统一处理）
        for lanlan, shard in list(self._shards.items()):
            if shard.loaded and self._expire_timed_action_if_due(shard):
                await self._save_shard_mood(lanlan, shard)
        current = self._current_shard_name()
        shard = self._get_shard(current)
        # 首次启动（store 里没有水位）时不触发开场白：刚启动就主动搭话
        # 会显得突兀，且用户手动启停插件会造成重复打扰
        if not shard.cycle.get("phase_seen"):
            try:
                shard.cycle["phase_seen"] = self._current_phase_state(shard).phase
            except TideConfigError:
                pass
        try:
            phase = self._current_phase_state(shard)
        except TideConfigError as exc:
            # 配置非法（如 Store 覆盖层存了坏参数）：明确报错，
            # 默认 startup_failure="warn" 下进程保活并标记降级，
            # 用户可在面板修正后由 config_change 恢复
            self.logger.warning("startup with invalid tide config: {}", exc)
            return Err(SdkError(f"invalid tide config: {exc}"))
        self.logger.info(
            "startup ok: lanlan={} enabled={} phase={} day={} advance_days={}",
            current, self._enabled(shard), phase.phase, phase.cycle_day,
            shard.cycle.get("advance_days", 0),
        )
        self._sync_debug_entries()
        return Ok(build_status_payload(phase, enabled=self._enabled(shard)))

    @lifecycle(id="shutdown")
    async def shutdown(self, **_: Any):
        if not self._state_trusted:
            # 本次启动没能在 store 通电后读到真实状态（见 _ensure_store_ready），
            # 内存里的分片是从"空"起步、混着本次会话改动的幻影。整体回写会把上一
            # 次真实保存的开关/锚点/快进/三本日记覆写掉——宁可本次不落盘
            # （会话内的单点写入仍各自落过），也不毁掉历史。
            self.logger.warning(
                "shutdown with untrusted startup load: skipping wholesale re-save "
                "to avoid overwriting persisted state ({} shard(s))",
                len(self._shards),
            )
            return Ok({"status": "shutdown", "saved": False})
        for lanlan, shard in list(self._shards.items()):
            if not shard.loaded:
                continue
            await self._save_shard_cycle(lanlan, shard)
            await self._save_shard_mood(lanlan, shard)
            await self._save_shard_diary(lanlan, shard)
            await self._save_shard_journal(lanlan, shard)
            # 会话末补刷（1.2.2）：shutdown 过去不涵盖 stats@/review@ 两个 key，
            # 关闭时补刷兜底（有内容才写，不给从未积累过的角色造空壳键）。
            # 批次2 起语气分布/情绪事件/和好等增量已在产生点即时落盘
            # （_save_tone_sense_state/_apply_mood_action 等），这里保留为
            # 中途写失败等残余窗口的最后一道兜底。
            if shard.stats:
                await self._save_shard_stats(lanlan, shard)
            if shard.review or shard.review_stats:
                await self._save_shard_review(lanlan, shard)
        await self._save_settings()
        await self._save_proactive_state()
        return Ok({"status": "shutdown"})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_: Any):
        await self._refresh_config()
        # [capabilities] 段可能变了（含 hide_disabled_tools）：重同步工具显隐
        self._sync_tool_visibility()
        # 按旧配置/旧端口解析出的缓存作废：宿主 API base、语气槽位下拉选项
        # （日历缓存 key 含参数指纹与当天日期，自然失效，无需清理）
        self._proactive_api_base_cache = None
        self._tone_slot_options_cache = (None, -1000.0)
        self._sync_debug_entries()
        # 锚点/周期参数变了，重新校验一次并报告（针对当前角色 shard）
        try:
            phase = self._current_phase_state()
        except TideConfigError as exc:
            return Err(SdkError(f"invalid tide config: {exc}"))
        return Ok(build_status_payload(phase, enabled=self._enabled()))


    @timer_interval(id="tide_tick", seconds=10, auto_start=True)
    async def tick(self, **_: Any):
        # 协调监督优先于 enabled 拦截：暂停中的主动搭话必须始终有人接管
        await self._supervise_once()
        # 能力中心（1.2.7）：工具显隐每趟对一次表（纯内存差集比对，无变化
        # 零通知）——覆盖"只靠对话/超时改变能力状态、面板没开"的场景；
        # 必须在 _ensure_tools_registered 之前：巡检要看到最新隐藏名单
        self._sync_tool_visibility()
        await self._ensure_tools_registered()
        # 面板「立即写一篇」的排队成文同样在 enabled 拦截之前消费（1.2.3）：
        # 入口只做受理秒回，写在这里起跑——"我的日记"只认 [review].enabled，
        # 潮汐总开关关着时队列也必须能被写掉
        await self._drain_pending_review_writes()
        # enabled 判定前移到角色解析之前：没有任何 shard 显式开启且全局默认关闭时
        # （默认安装即此态），后续步骤全部跳过——否则角色解析 HTTP（15s 缓存）
        # 在空闲期每天空转数千次。
        # 副作用：从未开启过模拟的新角色不会经 tick 注册进 lanlan_index
        # （另一注册路径是情绪工具归因），可接受——无状态可展示。
        any_shard_enabled = any(bool(shard.cycle.get("enabled")) for shard in self._shards.values())
        if not any_shard_enabled and not bool((self._tide_cfg or {}).get("enabled", False)):
            return Ok({"injected": False, "reason": "disabled"})
        current = await self._resolve_current_lanlan()
        current_shard = await self._ensure_shard(current)
        if not self._enabled(current_shard):
            return Ok({"injected": False, "reason": "disabled"})

        latest = await self._poll_latest_user_message()
        injected = False
        if latest is not None:
            ts, text, is_voice, msg_lanlan = latest
            msg_shard = await self._ensure_shard(msg_lanlan)
            if ts > msg_shard.last_injected_message_ts:
                self.logger.info("user message observed: lanlan={} is_voice={}", msg_lanlan, is_voice)
            # 消息归属角色未开启模拟时不驱动其 shard（per-shard 的 fail-closed）
            if self._enabled(msg_shard):
                injected = await self._handle_new_user_message(ts, text, msg_lanlan)
        # 阶段变化的开场白（不依赖用户消息，独立检查）：
        # 只对当前角色 shard 检查，避免对未查看的角色主动搭话
        opener_sent = False
        try:
            opener_sent = await self._maybe_phase_opener(
                self._current_phase_state(current_shard), current, current_shard
            )
        except TideConfigError as exc:
            self.logger.debug("skip phase opener, bad config: {}", exc)
        # 语气感知：diff 最近回复，按筛选/校正模式递提醒（只对当前角色，零延迟后置分析）
        tone_sensed = await self._maybe_tone_sense(current, current_shard)
        # 时光日记·自动碎片：同一轮 recent 数据上独立水位捕获主人的重要话语
        #（放在语气感知之后：语气感知已 poll 过时本轮直接复用缓存，不重复拉取）
        fragment_captured = await self._maybe_capture_fragments(current, current_shard)
        # 持续极端心情的动作邀请（看"持续状态"，与单轮浓度的语气 nudge 互补）
        extreme_invited = self._maybe_extreme_affect_invite(current, current_shard)
        # 我的日记：双门槛攒够就成文一篇（放在链路末尾——成文是一次模型调用，
        # 放前面会拖慢注入/语气感知的时序；写完只落 Store，不打扰任何人）
        review_written, _review_reason = await self._maybe_write_review(current, current_shard)
        # 相处统计：跨月时封卷上个月的月报 + 纪念日当天注入一次轻语。
        # 放在链路末尾（与 review 同理：统计副作用不挡注入时序）；
        # 纪念日不依赖消息，独立检查（与阶段开场白同例，只对当前角色）
        await self._seal_stats_months(current, current_shard)
        anniversary_pushed = await self._maybe_anniversary_push(current, current_shard)
        return Ok({
            "injected": injected,
            "opener_sent": opener_sent,
            "tone_sensed": tone_sensed,
            "fragment_captured": fragment_captured,
            "extreme_invited": extreme_invited,
            "review_written": review_written,
            "anniversary_pushed": anniversary_pushed,
        })

