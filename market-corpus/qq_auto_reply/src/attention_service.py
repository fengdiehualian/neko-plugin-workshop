"""注意力服务（周期模型）

每个群一份独立注意力标量（0~10），按相位机推进：

- rise: 随时间增长（基础速率 + 消息/@/提问加成），情绪/疲劳调制速率
- fall: 随时间回落（夺冠蜜月结束、发言消耗、让位回落）

焦点 = 所有 attention >= 焦点线的群中最高者；发言消耗注意力；情绪可抢/让焦点。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .feedback_classifier import QQFeedbackClassifier

# ── 情绪 → 注意力速率偏移（rise 加速 / fall 减速）──
#
# 这张表是情绪的**唯一真源**：倍率符号决定该情绪属于上升侧还是回落侧，
# `_EMOTION_DECAY_ORDER` 必须按倍率单调递减排列（从最猛到最丧，中间是 calm）。
# `tests/test_qq_emotion_vocabulary.py` 强制这三条不变量，并强制它和
# `settings_schema.DEFAULT_EMOTION_MULTIPLIERS` 逐键一致。
_EMOTION_MULTIPLIER: dict[str, float] = {
    "arguing": 1.2,      # 上头死磕，涨得快跌得慢
    "proud": 0.8,        # 赢了要炫耀，猛拉注意力
    "annoyed": 0.5,      # 不爽，比正常更专注
    "playful": 0.3,      # 玩闹中，微微挂住
    "curious": 0.2,      # 被勾起兴趣
    "calm": 0.0,         # 正常
    "sad": -0.4,         # 难过，不太想说话
    "embarrassed": -0.6,  # 尴尬想溜
    "bored": -0.7,       # 没兴趣的话题，主动掉注意力去找别的群
    "sulking": -0.9,     # 赌气——基本清零，主动让出焦点
}
# 强情绪直接触发焦点切换。
#
# ⚠️ 这两个集合与 `_EMOTION_MULTIPLIER` 是**分开维护**的：往倍率表加新情绪不会
# 自动让它获得抢/让焦点行为。新增情绪时必须同时决定它属于哪一档（或都不属于），
# 否则该情绪会「能配置但无行为」。`tests/test_qq_emotion_vocabulary.py` 盯着这条
# 一致性 —— 新情绪若既不在抢焦点也不在让焦点集合里，必须显式登记到该测试的
# `_NEUTRAL_EMOTIONS` 白名单，逼作者做一次有意识的选择。
_EMOTION_FORCE_FOCUS = {"arguing", "proud"}     # 立刻抢焦点
_EMOTION_DROP_FOCUS = {"sulking", "embarrassed", "bored"}  # 立刻让出焦点
# 情绪降温阶梯：30 秒无新情绪就朝 calm 方向走一级。顺序 = 倍率从高到低，
# 于是「上升侧 = calm 之前」「回落侧 = calm 之后」与倍率符号自动一致。
_EMOTION_DECAY_ORDER = [
    "arguing", "proud", "annoyed", "playful", "curious",
    "calm",
    "sad", "embarrassed", "bored", "sulking",
]
_EMOTION_DECAY_SECONDS = 30


@dataclass(slots=True)
class QQGroupAttentionState:
    """单群注意力状态（周期模型）。

    ``attention_score`` 是唯一标量（0~10），按**本群自己的热度档**推进：

      - ``warm``（热聊中）：本群在 ``attention_heat_warm_gap_seconds`` 之内有人说过话 →
        随时间增长（消息/@ 加速；情绪调制速率）
      - ``cooling``（凉下来了）：静默超过那个窗口 → 随时间回落

    2026-09-29 之前这里是 rise/fall **相位** + 蜜月 + 让位。那套东西的前提是"同一时刻
    只有一个群能说话"（跨群焦点竞争）：rise = 攒分夺冠、蜜月 = 夺冠后的纯积累期、
    fall = "让位给别的群"。跨群取舍删除后没有对手可让、没有冠军可加冕，于是换成
    "**这个群自己热不热**"—— 判据只有本群最后一条消息的时刻与接话反馈，不看任何别的群。

    原来的第三档 ``dormant``（休眠）与**主动破冰**是同一天一起删除的：休眠的唯一触发源
    就是"她主动开口没人接"。墓碑见文件末尾 `## 已删除：主动破冰 + 休眠`。
    """

    group_id: str
    attention_score: float = 0.0      # 唯一标量 0~10
    heat: str = "warm"                # warm | cooling（每次推进按本群时间戳重算）
    # ── 时间戳 ──
    last_decay_at: int = 0            # 上次相位推进时刻（幂等推进用）
    last_message_at: int = 0
    last_reply_at: int = 0
    last_boost_at: int = 0
    last_focus_at: int = 0
    focus_acquired_at: int = 0        # 最近一次"站上档位线"的时刻（焦点快照/标记用）
    #: 锁到期时刻（`@猫娘` / 唤醒词触发）。期内该群独占焦点，其余群不参与竞争。
    #:
    #: 与分数是**两个独立信号**：分数表达「没人叫我时我自己看哪」，
    #: 锁表达「有人点名，我必须回头应对」。合成一个数就会互相污染参数
    #: （这正是本模块此前调不明白的原因，见 docs/attention-redesign-draft.md §4）。
    lock_until: int = 0
    last_focus_reason: str = ""
    # ── 情绪 ──
    emotion: str = "calm"             # 取值见 _EMOTION_MULTIPLIER（唯一真源）
    emotion_updated_at: int = 0
    emotion_display: str = "calm"     # 前端展示用标签，衰减比 logic emotion 慢
    emotion_display_until: int = 0
    # ── 接话反馈（她说完之后群里有没有人接）──
    # 一个「回复周期」= 她发言 → 群里其他人的回应 → 到点结算一次。
    # 结算状态用 ``feedback_settled_at`` 与 ``last_reply_at`` 的**时间先后**表示，
    # 不另存 pending 布尔量：重启后从存档就能自洽地判断这一轮到底结算过没有。
    msgs_after_reply: int = 0         # 她上次发言之后别人的消息条数（live，给提示词看）
    feedback_settled_at: int = 0      # 上次结算时刻
    feedback_tier: str = ""           # 上次结算结论："" | silent | quiet | warm
    feedback_msgs: int = 0            # 上次结算时计入的条数
    # ── 休眠（「这个群冷漠了」）───────────────────────────────────────
    # 触发：**这个群自己**静默 ≥ `dormancy_idle_seconds`（默认 1800 = 半小时）。
    #
    # 使用者口径（2026-09-29）：「群聊如果冷漠了就休眠，大概就是半个小时没有任何发言」。
    # 这与「破冰」是两件事：破冰是**她主动开口**（用时间判「该说话了」很蠢，已删除），
    # 休眠是**她不看了**（凉了就是凉了，不需要花任何 LLM 调用）。
    #
    # 睡下的效果（跨群取舍删除后重新定义，见 `attention_gate_service`）：
    # **这个群只答点名**（@ / 引用她 / 关键词）；其余消息这一轮不看不答，
    # 但**照常计分** —— 普通消息的加成正是"群里又聊热就醒"的燃料（见 `_maybe_wake_up`）。
    # 唤醒只有两条路，**没有「到点自动醒」**（使用者 2026-09-29：
    # 「到点自动醒也不要。注意，这个逻辑只在脚本内处理」）：
    #   ① 点名（@ / 引用她 / 关键词 → `mark_focus`）；
    #   ② 群里又热闹起来（注意力顶回 `dormancy_wake_score`，见 `_maybe_wake_up`）。
    #
    # 所以「睡没睡」就是一个布尔量：不需要 `dormant_until` 那种「到点醒」的时间戳
    # （它的存在意味着又多一条唤醒路径，而那条已经被明确否掉了）。
    dormant: bool = False             # True = 睡着（只答点名），直到被点名或群里热起来

    def dimension_dict(self) -> dict[str, float]:
        """展示用：标量 + 热度 + 情绪是否活跃。"""
        return {
            "attention": float(self.attention_score),
            "heat": 1.0 if self.heat == "warm" else 0.0,
            "emotion": 1.0 if self.emotion != "calm" else 0.0,
        }

    def dominant_dimension(self) -> str:
        """用于解释"为什么现在看这个群"：当前热度档。"""
        return self.heat

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "attention_score": float(self.attention_score),
            "heat": str(self.heat),
            "last_decay_at": int(self.last_decay_at),
            "last_message_at": int(self.last_message_at),
            "last_reply_at": int(self.last_reply_at),
            "last_boost_at": int(self.last_boost_at),
            "last_focus_at": int(self.last_focus_at),
            "focus_acquired_at": int(self.focus_acquired_at),
            "lock_until": int(self.lock_until),
            "last_focus_reason": str(self.last_focus_reason or ""),
            "emotion": str(self.emotion or "calm"),
            "emotion_updated_at": int(self.emotion_updated_at),
            "emotion_display": str(self.emotion_display or "calm"),
            "emotion_display_until": int(self.emotion_display_until),
            "msgs_after_reply": int(self.msgs_after_reply),
            "feedback_settled_at": int(self.feedback_settled_at),
            "feedback_tier": str(self.feedback_tier or ""),
            "feedback_msgs": int(self.feedback_msgs),
            "dormant": bool(self.dormant),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None, *, group_id: str) -> "QQGroupAttentionState":
        data = dict(payload or {})
        st = cls(
            group_id=group_id,
            attention_score=float(data.get("attention_score") or 0.0),
            # 旧存档里的 `phase`（rise/fall）不再读：热度是**每次推进按本群时间戳重算**的
            # 派生量，读旧值只会把"这条存档来自相位时代"这件事带进新模型。
            heat=str(data.get("heat") or "warm"),
            last_decay_at=int(data.get("last_decay_at") or 0),
            last_message_at=int(data.get("last_message_at") or 0),
            last_reply_at=int(data.get("last_reply_at") or 0),
            last_boost_at=int(data.get("last_boost_at") or 0),
            last_focus_at=int(data.get("last_focus_at") or 0),
            focus_acquired_at=int(data.get("focus_acquired_at") or data.get("last_focus_at") or 0),
            lock_until=int(data.get("lock_until") or 0),
            last_focus_reason=str(data.get("last_focus_reason") or ""),
            emotion=str(data.get("emotion") or "calm"),
            emotion_updated_at=int(data.get("emotion_updated_at") or 0),
            emotion_display=str(data.get("emotion_display") or "calm"),
            emotion_display_until=int(data.get("emotion_display_until") or 0),
            # 旧存档没有这几个键 → 0/""：等价于「她还没发过言，没有可结算的反馈」。
            msgs_after_reply=int(data.get("msgs_after_reply") or 0),
            feedback_settled_at=int(data.get("feedback_settled_at") or 0),
            feedback_tier=str(data.get("feedback_tier") or ""),
            feedback_msgs=int(data.get("feedback_msgs") or 0),
            # 休眠标记**要读**：`_load_state` 每次都经 `from_dict` 重建，不读就等于
            # 「睡下 → 下一次读状态又醒了」。
            #
            # 旧破冰时代那些标记的清理放在 `load_cached_state`（插件启动时一次性清空）：
            # 那样既不会让升级后的群被陈年标记叫睡，也不会破坏本次运行内的往返。
            # 老的 `dormant_until` / `dormant_forever` 两个键**不再读**（那个模型带「到点自动醒」）。
            dormant=bool(data.get("dormant") or False),
        )
        # 旧维度模型（四维加权分）与相位时代（rise/fall + 蜜月）的存档都能读：
        # 分数保留，热度不读旧值 —— 它由 `_advance_heat` 按本群时间戳重算。
        return st


class QQAttentionService:
    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._cache: dict[str, dict[str, Any]] = {}
        #: 群 → 最近回复时刻（**只服务"频率软提示"**，内存态、重启即失）。
        #: 门控里那个口径相同的硬闸计数器（`attention_gate_service._reply_timestamps`）
        #: **已于 2026-09-27 随硬闸一起删除**，所以现在是**唯一**一份回复时刻环。
        #: 当年刻意不复用它的理由（"硬闸的计数口径一改就会连带改提示词行为，两者绑在
        #: 一起以后没人敢动"）随硬闸一同失效 —— 现在 `pacing_hint` 就是频率的唯一机制，
        #: 口径由它自己定，不需要再与别人对齐。
        #: 唯一的写入点是 `update_on_reply`（她说话）。主动破冰已删除，不再有第二个。
        self._reply_times: dict[str, list[int]] = {}

    async def load_cached_state(self) -> None:
        if not getattr(self.plugin, "backlog_store", None):
            self._cache = {}
            return
        state = await self.plugin.backlog_store.load()
        attention_state = state.get("group_attention_state")
        self._cache = dict(attention_state) if isinstance(attention_state, dict) else {}
        self.cleanup_stale_cache()
        # 重启后重置推进时钟：last_decay_at 是停机前的旧值，直接用停机时长推进会把
        # 注意力一步推到极端（热聊顶满 / 凉透归零）。重置为当前时刻，从干净状态继续。
        #
        # 2026-09-29 起这里不再需要"补 steady_since / 重置 phase_started_at"那套迁移：
        # 热度是**按本群最后一条消息的时刻推导**的，重启后自然重新算对。
        now = self._current_time()
        for payload in self._cache.values():
            if isinstance(payload, dict):
                payload["last_decay_at"] = now
                # 启动时清一次休眠标记：旧存档里那些是**破冰时代**留下的（那时唯一的
                # 触发源是「她主动开口没人接」），回放它们等于让一批群在升级后立刻睡下、
                # 且没人叫就永远不醒。清掉之后休眠只会由「这个群静默够久」重新产生
                # （半小时后又睡回去，代价可以忽略）。
                payload["dormant"] = False

    def _current_time(self) -> int:
        return int(__import__("time").time())

    def _normalized_groups(self) -> list[str]:
        """只读：返回信任列表 + 缓存中已有的群（不含清理逻辑）。"""
        groups: set[str] = set()
        if self.plugin.group_permission_mgr:
            for item in self.plugin.group_permission_mgr.list_groups():
                gid = item.get("group_id", "") if isinstance(item, dict) else str(item or "")
                normalized = str(gid or "").strip()
                if normalized:
                    groups.add(normalized)
        for group_id in list(self._cache.keys()):
            if isinstance(group_id, str) and group_id.startswith("{"):
                continue
            normalized = str(group_id or "").strip()
            if normalized and not normalized.startswith("{"):
                groups.add(normalized)
        return sorted(groups)

    def cleanup_stale_cache(self) -> int:
        """显式清理不在信任列表中的缓存群，返回清理数。调用时机：load_cached_state / persist。"""
        trust_groups: set[str] = set()
        if self.plugin.group_permission_mgr:
            for item in self.plugin.group_permission_mgr.list_groups():
                gid = item.get("group_id", "") if isinstance(item, dict) else str(item or "")
                normalized = str(gid or "").strip()
                if normalized:
                    trust_groups.add(normalized)
        removed = 0
        for group_id in list(self._cache.keys()):
            if isinstance(group_id, str) and group_id.startswith("{"):
                del self._cache[group_id]
                removed += 1
                continue
            normalized = str(group_id or "").strip()
            if normalized and not normalized.startswith("{") and normalized not in trust_groups:
                del self._cache[group_id]
                removed += 1
        return removed

    def _load_state(self, group_id: str) -> QQGroupAttentionState:
        attention_state = self._cache.get(group_id)
        state = QQGroupAttentionState.from_dict(
            attention_state if isinstance(attention_state, dict) else None, group_id=group_id
        )
        # 新群：启动推进时钟，从 0 开始随时间爬升
        if not isinstance(attention_state, dict):
            now = self._current_time()
            state.last_decay_at = now
        return state

    def get_state(self, group_id: str) -> QQGroupAttentionState:
        return self._load_state(str(group_id or "").strip())

    def _write_state(self, state: QQGroupAttentionState) -> None:
        self._cache[state.group_id] = state.to_dict()

    def _enabled(self) -> bool:
        """注意力账本是否运行。**没有独立开关**。

        以前这里读配置键 ``enable_group_attention``，但它是个**假旋钮**：唯一让它为真
        的模式（``neko_dynamic``，也是出厂默认）下 ``_enforce_attention_for_dynamic_mode``
        会把它强制设回 True；而在 ``neko_scene`` 下门控根本不跑
        （``message_dispatcher`` 只在 neko_dynamic 下调 ``attention_gate_service``）。
        也就是说那个开关"能关的模式里不需要关、需要关的模式里关不着"。

        已删除该配置。账本恒开 —— **是否门控**由调用方按策略模式决定，不在这里。
        （老配置里残留的 ``enable_group_attention`` 键无害：它只是被原样加载与回写。）
        """
        return True

    # ── 周期模型参数（可配）──

    def _setting(self, key: str, default: Any) -> Any:
        """读配置，尊重 0 值：缺失（未设置/None）才回退 default。

        旧写法 ``get(key, default) or default`` 会把保存的 0 当成 falsy 回退
        默认——例如 attention_consume_ratio=0（禁用回复消耗）被读回默认值，
        attention_fall_rate=0 被读回 0.015，dashboard 报保存成功但运行时
        行为不变。
        """
        value = (self.plugin._qq_settings or {}).get(key)
        return default if value is None else value

    def _rise_rate(self) -> float:
        """rise 相位基础增速（/秒）。"""
        return max(0.0, float(self._setting("attention_base_rise_rate", 0.02)))

    def _message_boost(self) -> float:
        """单条消息对注意力的加成。"""
        return max(0.0, float(self._setting("attention_message_boost", 0.15)))

    def _keyword_boost_ratio(self) -> float:
        """分类命中（mention/关键词）时的额外加成倍率。"""
        return max(0.0, float(self._setting("attention_keyword_boost_ratio", 1.8)))

    def _message_gain(self) -> float:
        """批量消息计数时每条消息的注意力增益。"""
        return max(0.0, float(self._setting("attention_batch_message_gain", 0.25)))

    def _heat_warm_gap_seconds(self) -> int:
        """本群多久没消息就算「凉下来了」（秒，默认 120，0 = 永不转凉）。

        这条取代了原来的蜜月 / fall 相位：热度的判据只有"这个群最后一条消息离现在多远"，
        不看分数、更不看别的群。0 是"永远算热"的合法值（显式取值，不用 `or` 兜底）。
        """
        raw = self._setting("attention_heat_warm_gap_seconds", 120)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 120

    def _fall_rate(self) -> float:
        """凉下来之后的回落速率（/秒）。"""
        return max(0.0, float(self._setting("attention_fall_rate", 0.015)))

    def _consume_ratio(self) -> float:
        """猫娘每次发言消耗注意力的比例（0~1）。

        兜底值**必须**与 `settings_schema` 声明的默认值一致（0.05）。这里曾经漂过：
        真源是 0.3、读取端兜底 0.1，于是"只塞部分 settings 的调用方"（测试、debug
        脚本）验到的是产品不使用的行为（同 `_frequency_max_multiplier` 的旧账）。
        """
        return min(1.0, max(0.0, float(self._setting("attention_consume_ratio", 0.05))))

    def _max_attention(self) -> float:
        return float(self._setting("attention_max_score", 10.0))

    def _focus_threshold(self) -> float:
        return float(self._setting("attention_focus_threshold", 4.0))

    def _focus_send_threshold(self) -> float:
        """焦点群的发送门控线（默认 2.0）：低于焦点线、高于最低线。

        焦点线（_focus_threshold，默认 4.0）是「赢得焦点」的资格线。一旦成为
        焦点群，注意力会随回复消耗（_consume_ratio）和时间衰减；若发送门控
        也用焦点线，焦点群回一条就跌破线、立刻被门控——焦点形同虚设。这里用
        更低的「焦点保持线」作为发送门控，让焦点群在合理注意力水平上继续回应。

        2026-09-29 跨群取舍删除后，外部一律走 `conversation_threshold()`（同一个值，
        「本群还热着吗」的口径）；这个方法退成内部取值口 + 老配置键的读取点。
        """
        return float(self._setting("attention_focus_hold_threshold", 2.0))

    def conversation_threshold(self) -> float:
        """「这个群还在聊」的线（默认 2.0，键仍是 `attention_focus_hold_threshold`）。

        低于这条线 = 这个群最近凉了 → 门控的注意力闸不搭话、必要度里原来的
        ``focus_active`` 不加那 40 分、重复回声不认它。三者共用这一个入口，避免
        "一个说热、一个说凉"。

        配置键**刻意不改名**：`attention_focus_hold_threshold` 已经写进使用者的
        配置文件，改名等于让老配置静默失效（值会悄悄退回默认 2.0）。
        """
        return self._focus_send_threshold()

    def _minimum_threshold(self) -> float:
        return float(self._setting("attention_min_threshold", 1.0))

    def _at_bot_boost(self) -> float:
        """被 @ 时消息加成的倍率（最强的一路加成）。"""
        return max(0.0, float(self._setting("attention_at_bot_boost", 3.0)))

    def _question_boost(self) -> float:
        """消息是提问时的加成倍率。"""
        return max(0.0, float(self._setting("attention_question_boost", 1.5)))

    def _wake_boost_ratio(self) -> float:
        """唤醒时把分数垫到「焦点线 × 此比例」（0~1）。"""
        return min(1.0, max(0.0, float(self._setting("attention_wake_boost_ratio", 0.75))))

    def _decay_interval(self) -> float:
        """注意力衰减循环的 tick 间隔（秒）。"""
        return max(0.1, float(self._setting("attention_decay_interval_seconds", 5.0)))

    # ── 接话反馈（B5 闭环）的四个量 ──

    def _feedback_enabled(self) -> bool:
        """接话反馈开关。关掉后既不结算也不注入提示词（完全回到旧行为）。"""
        return bool(self._setting("attention_feedback_enabled", True))

    def _feedback_window_seconds(self) -> float:
        """她发言后等多久才敢下「有没有人接」的结论（秒）。

        为什么必须有这个窗口：她刚说完的那一瞬间「0 条回应」只说明大家还没打完字。
        默认 90 秒 ≈ 两三个人的打字与反应时间；比 ``attention_fall_seconds``（30s）长，
        所以一个没人接的群会**先**按正常节奏回落，再由反馈补一脚。
        """
        return max(0.0, float(self._setting("attention_feedback_window_seconds", 90.0)))

    def _feedback_silent_penalty(self) -> float:
        """没人接话时扣掉的注意力（回落加速）。"""
        return max(0.0, float(self._setting("attention_feedback_silent_penalty", 0.4)))

    def _feedback_warm_bonus(self) -> float:
        """群友接起来时加上的注意力。"""
        return max(0.0, float(self._setting("attention_feedback_warm_bonus", 0.4)))

    def _feedback_warm_count(self) -> int:
        """她发言后多少条回应算「聊起来了」。"""
        return max(1, int(self._setting("attention_feedback_warm_count", 3)))

    # ── 休眠（「这个群冷漠了」）──

    def _dormancy_enabled(self) -> bool:
        """冷场自动休眠的总开关。"""
        return bool(self._setting("dormancy_enabled", True))

    def _dormancy_idle_seconds(self) -> int:
        """这个群静默多久算「冷漠了」（秒，默认 1800 = 半小时；0 = 关掉自动休眠）。

        使用者口径（2026-09-29）：「群聊如果冷漠了就休眠，大概就是半个小时没有任何发言」。
        显式取值不用 `... or 1800`：0 是「关掉这个行为」的合法值。
        """
        raw = self._setting("dormancy_idle_seconds", 1800)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 1800

    def _dormancy_wake_score(self) -> float:
        """群又热闹到什么程度就把它叫醒（默认 2.0 = 在聊的线）。

        使用者口径（2026-09-29）：「如果群里聊得热火朝天就苏醒。这一块可以建模到注意力上」
        —— 所以苏醒**不另开计数器**，就用注意力分数：睡着期间分数只由「有人说话」推动
        （不随时间回落、也不自然增长），涨回这条线就醒。

        默认 2.0 与门控那条「本群在聊的线」同值：语义是「这个群又重新聊起来了」。
        想要更硬的「确实热火朝天」把它调到 3~4 即可（它**不影响**门控那条线）。
        """
        raw = self._setting("dormancy_wake_score", 2.0)
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return 2.0

    def _maybe_wake_up(self, state: QQGroupAttentionState, now: int) -> bool:
        """这个群又热闹起来了 → 苏醒。返回是否**这一下刚醒**。

        判据就是注意力分数（见 `_dormancy_wake_score`）。**放在 `update_on_message` 里**
        （消息刚加完分之后）：这样"把分数顶过线的那一条消息"自己就能被正常回 —— 她说的话
        不会晚 5 秒（衰减循环的间隔）才跟上，也不会有任何一条消息被白白丢掉。

        未被点名的普通闲聊也走这条路（这正是使用者要的：群里热起来就醒，不必等人叫她）。
        """
        if not self._dormancy_enabled() or not self._state_is_dormant(state):
            return False
        if float(state.attention_score) < self._dormancy_wake_score():
            return False
        state.dormant = False
        # 睡着那段时间的账不补：回溯补回的游标推到此刻（同 `wake_from_dormancy`）。
        state.last_focus_at = now
        state.last_focus_reason = "wake:lively"
        state.heat = self._heat_tier(state, now)
        return True

    #: 没有 `_dormancy_auto_wake_seconds()`：那条路已被使用者否掉（见字段区注释）。

    @staticmethod
    def _state_is_dormant(state: QQGroupAttentionState) -> bool:
        return bool(state.dormant)

    def is_dormant(self, group_id: str, *, now: int | None = None) -> bool:
        """这个群现在是不是睡着（门控据此只放行点名类消息）。

        走 `_state_is_dormant` + 总开关：开关关掉时对外也必须报「没睡」，否则这个查询与
        门控的实际行为会各说各话。
        """
        if not self._dormancy_enabled():
            return False
        _ = now      # 保留形参：调用方按「此刻」问，而判据本身与时间无关（没有到点自动醒）
        st = self._load_state(str(group_id or "").strip())
        return self._state_is_dormant(st)

    def _maybe_fall_asleep(self, state: QQGroupAttentionState, now: int) -> bool:
        """静默够久 → 睡下。返回是否**这一轮刚睡着**（用于打日志）。

        判据只有本群自己的 `last_message_at`：从没说过话的群不睡（那是「还没认识」，
        不是「冷漠了」）。分数与**焦点字段一概不动** —— 醒来时不必从零熬。
        """
        if not self._dormancy_enabled():
            return False
        idle = self._dormancy_idle_seconds()
        if idle <= 0 or self._state_is_dormant(state):
            return False
        last_message_at = int(state.last_message_at or 0)
        if last_message_at <= 0 or now - last_message_at < idle:
            return False
        state.dormant = True
        state.heat = "dormant"
        state.last_focus_reason = "dormant:idle"
        return True

    def wake_from_dormancy(self, group_id: str, *, reason: str = "at") -> bool:
        """唤醒睡着的群（点名类路径都会经过这里）。返回 True 表示刚从休眠里醒过来。

        顺手把回溯补回的游标（`last_focus_at`）推到此刻：**睡着这段时间的账不补了** ——
        一个凉了半小时的群，回来时该从当下接上，而不是把半小时前的旧消息翻出来逐条回应。
        """
        key = str(group_id or "").strip()
        if not key:
            return False
        state = self._load_state(key)
        if not self._state_is_dormant(state):
            return False
        state.dormant = False
        state.last_focus_at = self._current_time()
        state.last_focus_reason = f"wake:{reason}"
        state.heat = self._heat_tier(state, self._current_time())
        self._write_state(state)
        self.plugin._emit_log("INFO", f"[Attention] 群{key} 从休眠中唤醒（{reason}）")
        if self.plugin.logger:
            self.plugin.logger.info(f"[Attention] 群 {key} 从休眠中唤醒（{reason}），只看点名")
        getattr(self.plugin, "_maybe_push_status_event", lambda: None)()
        return True

    def _frequency_target_gap(self) -> float:
        """发言频率的目标间隔（秒）：恰好这个节奏时增速为基准 1.0×。"""
        return max(1.0, float(self._setting("attention_frequency_target_gap", 30.0)))

    def _frequency_min_multiplier(self) -> float:
        """冷群增速下限（0~1）。不为 0：冷群该涨得慢，但不该被彻底冻结。"""
        return min(1.0, max(0.0, float(self._setting("attention_frequency_min_multiplier", 0.15))))

    def _frequency_max_multiplier(self) -> float:
        """热群增速上限（>=1）。

        兜底值必须与 ``settings_schema`` 声明的默认值一致（1.8）。这里曾是 3.0 ——
        早先真源默认值也是 3.0，后来降到 1.8 时只改了真源，读取端漏改。生产里
        键恒在（`_qq_settings` 由 `default_config()` 播种）所以线上看不出，
        但**任何只塞部分 settings 的调用方（测试、debug 脚本）拿到的是 3.0**:
        用 `business_config.json` 的真实参数构造出来却是 3.0× 封顶，
        等于在验证产品不使用的行为。
        """
        return max(1.0, float(self._setting("attention_frequency_max_multiplier", 1.8)))

    def _frequency_scale(self, state: QQGroupAttentionState, now: int) -> float:
        """按本群的发言节奏缩放自然增速：``目标间隔 / 实际间隔``，再钳到 [min, max]。

        热群（间隔小于目标）涨得快，冷群（间隔大于目标）涨得慢。只用
        ``last_message_at`` 这一个既有字段算间隔 —— 没有滑动窗口、没有计数器，
        因此无状态、可复现，也不需要额外的老化机制。

        调用点必须保证 ``now`` 早于写入本条消息的 ``last_message_at``
        （``_advance_heat`` 由 ``_apply_decay`` 在 ``update_on_message`` 更新该
        字段之前调用），否则间隔恒为 0、所有群都会拿到上限倍率。
        """
        last = int(state.last_message_at or 0)
        gap = float(now - last) if last > 0 else float("inf")
        if gap <= 0.0:
            # 同一秒内连发（或时钟未前进）：按最热处理。
            return self._frequency_max_multiplier()
        scale = self._frequency_target_gap() / max(gap, 1.0)
        return min(max(scale, self._frequency_min_multiplier()), self._frequency_max_multiplier())

    def _emotion_multipliers(self) -> dict[str, float]:
        """情绪 → 升降速率倍率表（配置表 = **覆盖表**）。

        内置 ``_EMOTION_MULTIPLIER`` 是基准，用户配置只覆盖它写到的键。没写的键
        回落到内置默认值 —— 这一条是必需的，不是宽容：老配置是在旧版本存的快照，
        新版本往默认表加的**新情绪在老配置里没有键**。如果按「表里没有就 0.0」处理，
        新情绪对老用户会彻底静默失效（``set_emotion`` 会直接 return），
        表现为「升级后新情绪标记毫无反应」，而且没有任何日志。

        想关掉某个情绪的影响，请显式写 ``0.0``；删掉键表示「跟随内置默认值」。

        写入侧由 ``config_store.normalize_emotion_multipliers`` 保证合法；这里再做一次
        防御性校验，非法就整份回退内置默认 —— 这张表参与涨跌计算，半份坏数据比没有更难查。
        """
        raw = self._setting("attention_emotion_multipliers", None)
        if not isinstance(raw, dict) or not raw:
            return dict(_EMOTION_MULTIPLIER)
        out: dict[str, float] = dict(_EMOTION_MULTIPLIER)
        for key, value in raw.items():
            try:
                out[str(key)] = float(value)
            except (TypeError, ValueError):
                return dict(_EMOTION_MULTIPLIER)
        return out

    def _emotion_multiplier(self, emotion: Any) -> float:
        """单个情绪的倍率；表外情绪按 0.0（即不影响速率）。"""
        return float(self._emotion_multipliers().get(str(emotion or "calm"), 0.0))

    # ── 热度档推进 ──

    def _advance_heat(self, state: QQGroupAttentionState, now: int) -> None:
        """按**这个群自己**的热度档推进注意力。幂等：基于 last_decay_at 差分。

        两个档，判据只有本群的时间戳：

        - ``warm``：最近 ``attention_heat_warm_gap_seconds`` 之内有人说过话 → 增长；
        - ``cooling``：静默超过那个窗口 → 回落（正向情绪跌得慢）；
        - ``dormant``：静默 ≥ ``dormancy_idle_seconds``（默认半小时）→ **分数冻住**，
          等有人点名叫她、或群里又热闹起来（**没有到点自动醒**）。

        ⚠️ 这里**不再有** rise/fall 相位、蜜月、让位、以及"到线就转回落"的切换：
        那套东西的前提是"同一时刻只有一个群能说话"。跨群取舍删除后（2026-09-29）：
        没有冠军可加冕，也没有对手可让位 —— 分数只回答一个问题："这个群现在还热吗？"
        """
        last = int(state.last_decay_at or state.last_message_at or state.last_boost_at or now)
        dt = max(0, now - last)
        if dt <= 0:
            return
        state.last_decay_at = now
        state.heat = self._heat_tier(state, now)
        if state.heat == "dormant":
            # 睡着的群分数冻住：它已经凉了，再往下掉只是把「醒来时从零熬」这件事做一遍。
            return
        emo = self._emotion_multiplier(state.emotion)

        if state.heat == "cooling":
            # 回落：正向情绪跌得慢。
            rate = self._fall_rate() * max(0.05, 1.0 - emo)
            state.attention_score = max(0.0, state.attention_score - rate * dt)
            return

        # 热聊中：正向情绪涨得快。
        #
        # 自然增长的上限是 **max_score 而不是档位线**：旧写法 `min(focus_threshold, …)`
        # 把增长钉死在线上，意味着"刚到线那一刻分数恰好等于门槛、零余量"，任何一次
        # 回复消耗都会把群打到线下——使用者要的"能一直聊很久"在结构上不可能。余量得
        # 靠继续聊出来。
        #
        # 注意：高于档位线的分数**绝不砍掉**（`min(线, 高分)` 会把 @ 抢来的高注意力
        # 瞬间蒸发）。
        rate = self._rise_rate() * self._frequency_scale(state, now) * (1.0 + emo)
        if state.attention_score < self._max_attention():
            state.attention_score = min(
                self._max_attention(), state.attention_score + rate * dt,
            )
        # 站上档位线 → 记下时刻（焦点快照/标记用；不再有任何"蜜月"含义）。
        if state.attention_score >= self._focus_threshold() and int(state.focus_acquired_at or 0) <= 0:
            state.focus_acquired_at = now

    def _heat_tier(self, state: QQGroupAttentionState, now: int) -> str:
        """这个群现在属于哪一档（纯函数式判据，全部来自本群自己的状态）。

        - 先看睡没睡（`dormant` 这个布尔量）→ dormant；
        - 再判「最近有没有人说话」：窗口内 → warm，否则 cooling。

        **不看分数**：分数是热度的结果，不是判据 —— 否则会绕回"到线就转档"那套
        （那正是被删掉的相位机在做的事）。
        """
        if self._state_is_dormant(state):
            return "dormant"
        gap = self._heat_warm_gap_seconds()
        if gap <= 0:
            return "warm"
        last_message_at = int(state.last_message_at or 0)
        if last_message_at <= 0:
            # 从没说过话的群：没有"热"的依据，按凉处理（分数照常随时间涨不了）。
            return "cooling"
        return "warm" if now - last_message_at < gap else "cooling"

    # ── 焦点选择 ──

    def _current_focus_group_id(self, states: list[QQGroupAttentionState]) -> str:
        # 焦点候选资格用焦点线 _focus_threshold()（默认 4.0）而非最低线 1.0：
        # 低于焦点线的群不参与焦点竞争，避免 1.1 分的群被当成焦点绕过门控。
        focused = [
            state for state in states
            if int(state.focus_acquired_at or 0) > 0 and float(state.attention_score) >= self._focus_threshold()
        ]
        if not focused:
            return ""
        return max(
            focused,
            key=lambda item: (
                int(item.focus_acquired_at or 0),
                int(item.last_focus_at or 0),
                float(item.attention_score),
            ),
        ).group_id

    def _top_candidate_group_id(self, states: list[QQGroupAttentionState], now: int) -> str:
        candidate = self._top_candidate_state(states, now)
        return candidate.group_id if candidate else ""

    def _top_candidate_state(self, states: list[QQGroupAttentionState], now: int) -> QQGroupAttentionState | None:
        # 焦点候选资格用焦点线 _focus_threshold()（默认 4.0）而非最低线 1.0，
        # 与「焦点 = 所有 attention ≥ 焦点线的群中最高者」的语义一致。
        eligible = [state for state in states if float(state.attention_score) >= self._focus_threshold()]
        if not eligible:
            return None
        return max(
            eligible,
            key=lambda item: (
                float(item.attention_score),
                int(item.last_message_at or 0),
                int(item.focus_acquired_at or 0),
            ),
        )

    def _held_focus_state(self, states: list[QQGroupAttentionState]) -> QQGroupAttentionState | None:
        """当前被**保持**的焦点：曾夺得焦点（focus_acquired_at>0）且分数仍 >= 发送保持线
        （``_focus_send_threshold``，默认 2.0）的群。

        焦点线（``_focus_threshold``，默认 4.0）是「赢得焦点」的资格线；一旦赢得，
        只要分数不掉破发送保持线（2.0）就继续持有——否则焦点群回复一次（分数被
        消耗到 2.x）就跌出焦点线、下一条消息被当非焦点拦下，发送门控形同虚设。
        多个曾夺得焦点的群取最近夺得的那个。无则返回 None。
        """
        held = [
            state for state in states
            if int(state.focus_acquired_at or 0) > 0
            and float(state.attention_score) >= self._focus_send_threshold()
        ]
        if not held:
            return None
        return max(
            held,
            key=lambda item: (
                int(item.last_focus_at or 0),
                int(item.focus_acquired_at or 0),
                float(item.attention_score),
            ),
        )

    # ── 谁参与注意力竞争 ──

    def participates_in_attention(self, group_id: str) -> bool:
        """这个群参不参与注意力竞争 —— **只有 trusted 群参与**（使用者 2026-09-27 拍板）。

        normal / none 群本来就不回复（只按 @ / 引用她回，其余按概率转达给主人）。
        把她们计进竞争有两个副作用，一起消掉：

        1. 她们会**抢焦点**，把 trusted 群挤成 non_focus（于是该 trusted 群的消息全被
           门控拦下）；
        2. 她们自己又会被焦点门控在第 4 步丢掉，于是"按概率转达给主人"这条路
           **永远走不到**（`reply_decision_node` 里那个 normal → relay 分支成了摆设）。

        没有权限管理器时（单测桩、旧宿主）一律按**参与**处理，保持既有语义。
        """
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return False
        manager = getattr(self.plugin, "group_permission_mgr", None)
        if manager is None:
            return True
        try:
            level = str(manager.get_group_level(normalized_group_id) or "").strip()
        except Exception:
            return True
        # 空串 = 拿不到级别 → 当作参与（宁可多算一个群，也别把该回的群静音）
        return level in ("", "trusted")

    def _choose_focus_state(
        self,
        states: list[QQGroupAttentionState],
        now: int,
        *,
        stamp_transition: bool = False,
    ) -> QQGroupAttentionState | None:
        if not states:
            return None
        # 只有 trusted 群参与竞争：非参与者连**持有焦点**的资格都没有，
        # 否则被降级（trusted → normal）的群会凭残留分数继续占着焦点，
        # 而它的消息现在直接放行（不参与门控），等于把 trusted 群静音到分数自然衰减。
        participants = [state for state in states if self.participates_in_attention(state.group_id)]
        if participants:
            states = participants
        # ── 优先级 1：锁（`@猫娘` / 唤醒词）────────────────────────────
        # 锁内该群独占焦点，其余群不参与竞争。这是「有人点名叫我，我必须回头
        # 应对」；分数则表达「没人叫我时我自己看哪」。两者是独立信号，锁必须
        # 先判——否则一个刚被 @ 的群会因为分数还没涨上来而被别的群顶掉。
        for state in states:
            if int(state.lock_until or 0) > now:
                return state
        # ── 优先级 1.5：休眠群不参与竞争（破冰没人接 → 让位给其他群）──────
        # ── 优先级 2：分数仲裁（无锁时的连续归属）──────────────────────
        # 归属每 tick 重算 ⇒ 「看一眼新群，没兴趣就回旧群」是免费的：旧群只要
        # 还是最有意思的，下一个 tick 自动回去，不需要等相位走完一轮。
        #
        # 新焦点候选：必须达到焦点线（_focus_threshold，默认 4.0）
        candidate = self._top_candidate_state(states, now)
        # 焦点保持：曾夺得焦点的群只要分数 >= 发送保持线（2.0）就继续持有，
        # 即使低于焦点线；只有更高分的挑战者（>= 焦点线）才能抢走。
        held = self._held_focus_state(states)
        if held is not None:
            if candidate is None or held.attention_score >= candidate.attention_score:
                return held
        # 无持有焦点：恢复「最高分群」回退（与 get_snapshot 的 states[0] 一致）。
        # 否则低于焦点线且无持有群时 _choose_focus_state 返回 None，而 get_snapshot
        # 却把最高分群报为焦点——分裂导致 _get_top_group_id 等其它调用者拿不到焦点，
        # 门控把最高分群的后续消息判成 non_focus。
        if candidate is None:
            return max(states, key=lambda s: float(s.attention_score))
        if stamp_transition:
            candidate.focus_acquired_at = now
            candidate.last_focus_reason = "highest_attention"
        return candidate

    def _normalize_state(self, state: QQGroupAttentionState, now: int = 0) -> QQGroupAttentionState:
        """钳分数 + **重算热度档**。

        热度是派生量（判据只有本群最后一条消息的时刻），但它会被写进存档、给提示词与
        界面看。放在这里重算，是为了让**任何**写入路径上的 `heat` 都与时间戳自洽 ——
        否则"她刚回了话"这种不碰时间戳的写入会留下一个过期标签（真机表现就是
        面板上一直显示"热聊中"，而那个群其实早就凉了）。

        ``now`` 由调用方给（`_apply_decay` 用它推进用的那个时刻）；不传就取服务时钟 ——
        两处混用会让"按 now=1010 推进出来的档位"被"按当前时钟重算"覆盖掉。
        """
        max_attention = self._max_attention()
        state.attention_score = max(0.0, min(max_attention, float(state.attention_score)))
        state.heat = self._heat_tier(state, int(now or self._current_time()))
        return state

    # ── 核心：消息更新 ──

    @staticmethod
    def _detect_question(text: str) -> bool:
        """检测消息是否为问题（问号结尾或疑问词开头）。"""
        t = str(text or "").strip()
        if not t:
            return False
        if t.endswith(("?", "？")):
            return True
        question_prefixes = ("为什么", "怎么", "什么", "如何", "能不能", "可以", "有没有", "谁知道", "请问")
        return t.startswith(question_prefixes) or any(p in t for p in ("吗？", "吗?", "么？", "么?"))

    async def update_on_message(self, message: dict[str, Any]) -> dict[str, Any]:
        if not self._enabled():
            return self.get_snapshot()
        group_id = str(message.get("group_id") or "").strip()
        if not group_id:
            return self.get_snapshot()
        focus_group_id = self.get_focus_group_id()
        now = int(message.get("timestamp") or self._current_time())
        text = str(message.get("content") or message.get("text") or "").strip()
        is_at_bot = bool(message.get("is_at_bot"))

        state = self._apply_decay(self._load_state(group_id), now, is_focus=(group_id == focus_group_id))
        state.last_message_at = now

        # 消息加速增长：@ 最强，问题次之，普通消息基础加成
        boost = self._message_boost()
        if is_at_bot:
            boost *= self._at_bot_boost()
        elif self._detect_question(text):
            boost *= self._question_boost()
        # 分类命中（mention/关键词）额外加成
        category = str(message.get("category") or "").strip()
        if not category and text:
            try:
                category = QQFeedbackClassifier.classify(
                    text, list((self.plugin._qq_settings or {}).get("backlog_labels") or [])
                )
            except Exception:
                category = ""
        if category and category != "chat":
            boost *= self._keyword_boost_ratio()
            state.last_focus_reason = category
        # **消息加成不再按相位衰减**（旧写法：fall 相位乘 attention_fall_boost_attenuation）。
        #
        # 那个衰减的意图是「正在让位的群不会因继续刷屏而赖着不走」，但它的量级算错了：
        # fall 衰减是 0.015/秒，即 30 秒掉 0.45；而被压到 0.3 的加成只有
        # 0.15 × 0.3 = 0.045/条 —— 就算群友 30 秒发一条，净增速恒为 −0.36/30s。
        # 于是**任何群一旦进入 fall 就必然一路跌到底**，回血通道实际不存在，
        # 「切去别的群看看再切回来」也就无从发生。
        #
        # 去掉之后 fall 仍然在退潮（时间衰减还在，净增速约 −0.3/30s），只是不再是
        # 「致命抽干」。让位的压力交给时间衰减与焦点竞争，而不是把回血掐断。
        state.attention_score = min(self._max_attention(), state.attention_score + boost)
        state.last_boost_at = now

        # 接话反馈：别人说的每一条都算「她上次发言之后的回应」。
        # 顺手做一次**惰性结算** —— 结算本身由时间触发（见 _settle_feedback），
        # 放在这里只是让「刚好有消息在窗口之后到达」的群不必等下一个 decay tick。
        state.msgs_after_reply = max(0, int(state.msgs_after_reply or 0)) + 1
        self._settle_feedback(state, now)

        # 群里热起来就苏醒：**分数已经加完了**，所以"把分数顶过唤醒线的那一条消息"
        # 自己就能走正常流程（不会被上面那条 dormancy 规则丢掉）。
        if self._maybe_wake_up(state, now):
            self.plugin._emit_log(
                "INFO",
                f"[Attention] 群{group_id} 又热闹起来了"
                f"（注意力 {float(state.attention_score):.1f} ≥ {self._dormancy_wake_score():.1f}）"
                f" → 从休眠中苏醒",
            )
            if self.plugin.logger:
                self.plugin.logger.info(
                    f"[Attention] 群 {group_id} 又热闹起来了"
                    f"（注意力 {float(state.attention_score):.1f}），从休眠中苏醒"
                )

        self._write_state(self._normalize_state(state))
        await self._persist()
        getattr(self.plugin, "_maybe_push_status_event", lambda: None)()  # 注意力变更 → SSE 通知前端
        return self.get_snapshot()

    async def update_on_message_count(self, group_id: str, *, message_count: int = 1) -> dict[str, Any]:
        if not self._enabled():
            return self.get_snapshot()
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return self.get_snapshot()
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=(normalized_group_id == focus_group_id))
        gain = max(0, int(message_count or 0)) * self._message_gain()
        state.attention_score = min(self._max_attention(), state.attention_score + gain)
        state.last_focus_reason = "message_recovery"
        state.last_boost_at = now
        # 批量计分同样能把它叫醒（这条路径来自缓冲汇总那类"一次补一批"）。
        self._maybe_wake_up(state, now)
        self._write_state(self._normalize_state(state))
        await self._persist()
        return self.get_snapshot()

    # ── 回复消耗 ──

    async def update_on_reply(self, group_id: str, *, reply_message_id: str = "", at_user_id: str = "") -> dict[str, Any]:
        if not self._enabled():
            return self.get_snapshot()
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return self.get_snapshot()
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=(normalized_group_id == focus_group_id))
        state.last_reply_at = now
        # 猫娘发言消耗注意力：回复一次扣掉一个**绝对量**。
        #
        # **不用乘性消耗**（旧写法 `score *= 1 - ratio`）。乘性意味着扣掉的绝对量
        # 与当前分数成正比：4.0 扣 0.4、8.0 扣 0.8 —— 越是聊得起劲的群被罚得越重，
        # 与使用者要的「能一直聊很久」正好相反。改成一个常数，代价可预期。
        #
        # 数值口径：`consume_ratio` 仍按「max_score 的比例」解释
        # （0.1 × 10.0 = 1.0 分/条），所以 UI 上「回复消耗比例」的标签依然成立，
        # 且与分数高低解耦。
        #
        # **不在这里改热度**：热度只看"这个群最近有没有人说话"，由 `_advance_heat`
        # 依时间线判定，不该由"猫娘说了句话"触发 —— 否则她一回话就把群的静默计时
        # 抹掉，一个已经凉了的群会因为她的自言自语一直算热。
        cost = max(0.0, self._max_attention() * self._consume_ratio())
        state.attention_score = max(0.0, state.attention_score - cost)
        state.last_focus_reason = "reply_consume"
        # 新的回复周期从这里开始：把「她上次发言之后的回应数」清零，
        # 于是上一次周期的计数（已结算或已作废）不会漏进这一轮。
        state.msgs_after_reply = 0
        # 频率软提示用的回复时刻环（见 pacing_hint）。
        times = self._reply_times.setdefault(normalized_group_id, [])
        times.append(now)
        window = self._pacing_window_seconds()
        times[:] = [t for t in times if now - t < window][-20:]
        self._write_state(self._normalize_state(state))
        await self._persist()
        return self.get_snapshot()

    # ── 热度推进（幂等）──

    def _apply_decay(self, state: QQGroupAttentionState, now: int, *, is_focus: bool = False) -> QQGroupAttentionState:
        if now <= 0:
            now = self._current_time()
        self._advance_heat(state, now)
        return self._normalize_state(state, now)

    # ── 接话反馈结算（幂等，一轮一次）──

    def _settle_feedback(self, state: QQGroupAttentionState, now: int) -> str:
        """结算「她上一次发言之后群里有没有人接」，返回本次结论（"" = 这轮无需/不能结算）。

        三条判定：

        - ``silent``：窗口内 **0 条**回应 → 扣 ``attention_feedback_silent_penalty``
          （她插了话没人理，这个群/这个话题不值得继续占注意力）；
        - ``quiet``：1~2 条 → 不动分数（有人在应，但还看不出热度）；
        - ``warm``：≥ ``attention_feedback_warm_count`` 条 → 加 ``attention_feedback_warm_bonus``
          （话题在群里是活的，值得多待）。

        **幂等性由时间戳保证**：``feedback_settled_at >= last_reply_at`` 就说明这一轮
        已经结算过；她在 ``update_on_reply`` 里刷新 ``last_reply_at``，于是下一轮重新开始。
        这个判断同时让重复调用（decay 循环 + 消息路径各来一次）不会重复加减分。

        结算**由时间触发而不是由消息触发**：一个彻底没人说话的群永远不会有新消息，
        若只挂在消息路径上，「没人接」这种最需要被发现的场景反而永远结算不了。
        """
        if not self._feedback_enabled():
            return ""
        last_reply_at = int(state.last_reply_at or 0)
        if last_reply_at <= 0:
            return ""
        if int(state.feedback_settled_at or 0) >= last_reply_at:
            return ""
        if now - last_reply_at < self._feedback_window_seconds():
            return ""
        count = max(0, int(state.msgs_after_reply or 0))
        warm_count = self._feedback_warm_count()
        if count >= warm_count:
            tier, delta = "warm", self._feedback_warm_bonus()
        elif count >= 1:
            tier, delta = "quiet", 0.0
        else:
            tier, delta = "silent", -self._feedback_silent_penalty()
        if delta:
            state.attention_score = max(
                0.0, min(self._max_attention(), float(state.attention_score) + delta)
            )
        state.feedback_settled_at = now
        state.feedback_tier = tier
        state.feedback_msgs = count
        if delta:
            state.last_focus_reason = f"feedback:{tier}"
        return tier

    def feedback_line(self, group_id: str, *, now: int | None = None) -> str:
        """给提示词用的一句「你上次发言之后群里什么反应」。

        这一句是反馈闭环的**可见那一半**：分数调整只改变她往哪个群看，而这句话让她
        知道「刚才那句有没有被接住」。取自 Heartflow 的两句注入
        （「上次回复后群里进行了热烈讨论」/「上次回复后无人接话」），并按我们的
        三档（warm / quiet / silent）细了一档。
        """
        if not self._feedback_enabled():
            return ""
        state = self._load_state(str(group_id or "").strip())
        last_reply_at = int(state.last_reply_at or 0)
        if last_reply_at <= 0:
            return ""
        ts = int(now if now is not None else self._current_time())
        count = max(0, int(state.msgs_after_reply or 0))
        if count >= self._feedback_warm_count():
            return f"你上次发言之后，群里接着聊了 {count} 条 —— 这个话题在群里是活的。"
        if count >= 1:
            return f"你上次发言之后，群里有人接着说了 {count} 条。"
        if ts - last_reply_at < self._feedback_window_seconds():
            return "你刚才发过言，群里还没有人回应（也可能只是还没打完字）。"
        return "你上次发言之后，群里一直没人接话 —— 这个话题大概没被接住。"

    def is_first_reply_after_own_speech(self, group_id: str, *, now: int | None = None) -> bool:
        """她刚说完话，而**这一条**是之后的第一条发言 → 大概率是在接她的话。

        真机由来（2026-09-27 17:37，使用者问「为什么破冰完没有后续的回复」）：

        ```
        17:37:21  她破冰开口（38 字），并按住焦点 120s
        17:37:51  群里回了「是吗」——普通消息，没 @ 也没引用
        17:37:53  她输出 <feeling>bored</feeling>（23 字符 + 换行 = 日志里那个固定的"24 字"），
                  让出焦点，一个字都没回
        ```

        原因是提示词里那条**逐条标注**只看"有没有显式指向"：没 @、没引用 → 一律标成
        「这条消息不是冲你来的…不要每条都接」。于是"她刚开口、对方第一句回应"这种
        最该接的情况，反而被标注劝退。

        判据直接用现成的反馈闭环状态，不另存字段：`msgs_after_reply` 在
        `update_on_reply` 里清零，别人每说一条 +1 ——
        而门控在**建提示词之前**已经为当前这条 +1 过，所以它 `== 1` 恰好就是
        "她说完之后的第一条"。再加一个时间窗（复用反馈窗口，默认 90s），
        免得十分钟后冒出来的一句也被算成"接她的话"。
        """
        key = str(group_id or "").strip()
        if not key:
            return False
        state = self._load_state(key)
        last_reply_at = int(state.last_reply_at or 0)
        if last_reply_at <= 0:
            return False
        ts = int(now if now is not None else self._current_time())
        if ts - last_reply_at > self._feedback_window_seconds():
            return False
        return max(0, int(state.msgs_after_reply or 0)) == 1

    # ── 频率软提示（现在**只剩它**在管频率）────────────────────────────
    #
    # 2026-09-27：门控里那道「窗口内发够 N 条就强制静默」的硬闸已按使用者口径删除
    # （「不要这个，有注意力控制频率了」）。所以这一段的措辞也跟着改了 ——
    # 它不再说"上限 N 条"（那是闸的口径），而是"你说得比平时密"的自省提示；
    # 到了参考条数也**继续提示**（原来到点就闭嘴，因为那时由静默接管）。

    def _pacing_hint_enabled(self) -> bool:
        """频率软提示开关（默认开：关掉之后频率就只剩注意力那一层在管）。"""
        return bool(self._setting("pacing_hint_enabled", True))

    def _pacing_window_seconds(self) -> int:
        """统计窗口（秒）：`reply_burst_window_seconds` —— 与界面上的"回复频率窗口"同一个键。"""
        return max(1, int(self._setting("reply_burst_window_seconds", 60) or 60))

    def _pacing_max_replies(self) -> int:
        """参考条数：`reply_burst_max_replies`（"比这密就该收一收"，不再触发静默）。"""
        return max(1, int(self._setting("reply_burst_max_replies", 3) or 3))

    def _pacing_hint_ratio(self) -> float:
        """到参考条数的百分之多少开始提醒（默认 0.6 ≈ 3 条里的第 2 条）。"""
        return min(1.0, max(0.0, float(self._setting("pacing_hint_ratio", 0.6) or 0.0)))

    def recent_reply_count(self, group_id: str, *, now: int | None = None) -> int:
        """窗口内她在这个群回了几条（供提示词与排查用）。"""
        key = str(group_id or "").strip()
        ts = int(now if now is not None else self._current_time())
        window = self._pacing_window_seconds()
        rows = [t for t in self._reply_times.get(key, []) if ts - t < window]
        self._reply_times[key] = rows
        return len(rows)

    def pacing_hint(self, group_id: str, *, now: int | None = None) -> str:
        """「你说得有点密了」——提醒她自己收敛（**唯一的频率机制**，没有硬闸兜底了）。

        为什么要有它：频率不能只靠注意力（注意力回答的是"该看哪个群"，不是"这个群里
        该说几句"）。原先这里配的是一道硬闸：到点直接静默，用户看到的是"她突然不理我了"
        —— 而且那道闸不看上下文，真机 19:16 就把它按在一次正常的一来一往上
        （她刚发 3 条、使用者回一句 → 被静默）。使用者口径：「不要这个，有注意力控制
        频率了」。于是硬闸删除，只留这条"坡"：

        * 到参考条数的比例（默认 60%，即 3 条里的第 2 条）→ 提醒少说、说短；
        * 已经超过参考条数 → 换成更强的一档（"除非有人点名叫你，先把话说给群友"）。

        它是**提示词里的一句话**，不拦任何消息 —— 说不说最终还是她的判断。
        """
        if not self._pacing_hint_enabled():
            return ""
        count = self.recent_reply_count(group_id, now=now)
        limit = self._pacing_max_replies()
        # 触发点 = 参考条数 × 比例（3 条 + 0.6 → 第 2 条开始提醒）。
        # 夹在 [1, limit-1]：留一条缓冲，免得"第一句就提醒"；limit=1 时没有缓冲空间。
        raw = limit * self._pacing_hint_ratio()
        threshold = min(max(int(round(raw)) if raw > 0 else 0, 1), max(1, limit - 1))
        if count < threshold:
            return ""
        window = self._pacing_window_seconds()
        # 落一条文件日志：真机验收时"提示到底有没有注入"只能靠它（提示词正文不进日志）。
        try:
            self.plugin.logger.info(
                f"[Pacing] 群 {group_id} 频率软提示已注入（{count}/{limit} 条 · {window}s 窗口）"
            )
        except Exception:  # noqa: BLE001 —— 日志失败不该影响提示词
            pass
        if count >= limit:
            return (
                f"注意节奏：你最近 {window} 秒内已经说了 {count} 条，比平时密了 —— "
                "接下来除非有人点名叫你，先把话让给群友，等他们说完你再看要不要接。"
            )
        return (
            f"注意节奏：你最近 {window} 秒内已经说了 {count} 条（参考 {limit} 条），"
            "这一轮能不说就不说；要说就只说一句短的。"
        )

    # ── 排序 ──

    def _sort_states(self, states: list[QQGroupAttentionState], now: int, *, focus_group_id: str = "") -> list[QQGroupAttentionState]:
        return sorted(
            states,
            key=lambda item: (
                1 if focus_group_id and item.group_id == focus_group_id else 0,
                float(item.attention_score),
                int(item.last_message_at or 0),
            ),
            reverse=True,
        )

    # ── Snapshot ──

    def _default_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled(),
            "focus_group_id": "",
            "focus_score": 0.0,
            "focus_reason": "",
            "dominant_dimension": "",
            "dimensions": {},
            "groups": [],
        }

    def get_snapshot(self) -> dict[str, Any]:
        now = self._current_time()
        # 第一步：对所有群做相位推进（此时还不知道谁是焦点，is_focus 统一用 False）
        states: list[QQGroupAttentionState] = []
        for group_id in self._normalized_groups():
            state = self._apply_decay(self._load_state(group_id), now, is_focus=False)
            self._write_state(state)
            states.append(state)
        # 第二步：选焦点（比注意力分）
        focus_state = self._choose_focus_state(states, now, stamp_transition=True)
        focus_group_id = focus_state.group_id if focus_state else ""
        if focus_state:
            self._write_state(focus_state)
        # 排序
        states = self._sort_states(states, now, focus_group_id=focus_group_id)
        if not states:
            return self._default_snapshot()
        focus = next((state for state in states if state.group_id == focus_group_id), states[0])
        return {
            "enabled": self._enabled(),
            "focus_group_id": focus.group_id,
            "focus_score": float(focus.attention_score),
            "focus_reason": focus.last_focus_reason,
            "dominant_dimension": focus.dominant_dimension(),
            "dimensions": focus.dimension_dict(),
            "groups": [state.to_dict() for state in states],
        }

    # ── 注意力上下文注入（供 LLM prompt 使用）──

    def get_attention_context(self, group_id: str) -> str:
        """生成**这个群自己**的注意力上下文文本，注入到系统提示中。

        2026-09-29 跨群取舍删除前，这里会说"这是你当前关注的焦点群 / 这不是你当前关注的
        群" —— 而那句话在提示词里是**有后果**的：门控真的按它决定搭不搭话。取舍删掉后
        每个群各自判定，再说"你不是焦点"只会让模型以为自己不该开口（而它其实已经被放行
        到 LLM 了）。现在只报本群自己的状态：注意力多少、相位、情绪。
        """
        snapshot = self.get_snapshot()
        states = snapshot.get("groups") or []
        this_state = None
        for s in states:
            if str(s.get("group_id") or "") == str(group_id):
                this_state = s
                break

        parts: list[str] = []
        parts.append("## 当前群聊注意力状态")

        if this_state:
            heat_label = {
                "warm": "热聊中（刚还有人说话）",
                "cooling": "凉下来了（这个群安静有一会儿了）",
            }.get(str(this_state.get("heat") or ""), "未知")
            parts.append(
                f"这个群当前的注意力 {float(this_state.get('attention_score', 0)):.1f}，"
                f"状态：{heat_label}"
            )
        else:
            parts.append("此群暂无注意力数据。")

        emo = (this_state or {}).get("emotion", "calm") if this_state else "calm"
        if emo and emo != "calm":
            parts.append(f"当前情绪: {emo}")

        feedback = self.feedback_line(group_id)
        if feedback:
            parts.append(feedback)

        pacing = self.pacing_hint(group_id)
        if pacing:
            parts.append(pacing)

        return "\n".join(parts)

    # ── 兼容旧接口 ──

    def get_focus_group_id(self) -> str:
        return str(self.get_snapshot().get("focus_group_id") or "")

    def get_focus_score(self) -> float:
        try:
            snapshot = self.get_snapshot()
            focus_id = str(snapshot.get("focus_group_id") or "")
            if not focus_id:
                return 0.0
            now = self._current_time()
            state = self._apply_decay(self._load_state(focus_id), now, is_focus=True)
            return float(state.attention_score)
        except Exception:
            return 0.0

    def _effective_focus_score(self, state: QQGroupAttentionState | None, now: int) -> float:
        if state is None:
            return 0.0
        return float(state.attention_score)

    def get_group_multiplier(self, group_id: str) -> float:
        """这个群**自己**的频率增速缩放（0.8 ~ 1.65）。

        以前它按**跨群差距**算：非焦点群与焦点群差距 ≥ 焦点线就直接返回 0.0（等于封死），
        其余按差距线性压。跨群取舍去掉之后（2026-09-29 使用者口径：「每个群自己管自己的
        注意力」），这里只看**本群**分数落在哪一档 —— 原本"焦点群"那一档（≥ 焦点线 →
        最高 1.65）现在任何热起来的群都能拿到，而冷群拿到 0.8，即"说得慢一点"，
        而不是"根本不许说"。
        """
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id or not self._enabled():
            return 1.0
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=False)
        group_score = float(state.attention_score)
        if group_score >= self._focus_threshold():
            return min(1.65, 1.0 + min(0.65, group_score / max(self._focus_threshold(), 1.0) * 0.25))
        if group_score <= self._minimum_threshold():
            return 0.8
        emo = state.emotion or "calm"
        return max(0.05, 1.0 + self._emotion_multiplier(emo))

    def is_in_conversation(self, group_id: str) -> bool:
        """这个群**自己**是不是正聊着（本群分数 ≥ 保持线，默认 2.0）。

        取代原来那句"是不是**全局焦点**"（2026-09-29 起跨群取舍去掉）：判据从"跟别的群比"
        变成"看自己在不在状态"。`attention_focus_hold_threshold` 这条线原本的语义就是
        "焦点群还能不能继续回"，现在读作"这个群还热着吗" —— 门控的注意力闸、必要度里
        原来那个 ``focus_active``（+40）、以及重复回声那条都改用它，三者不会再出现
        "一个说有焦点、一个说没有"。
        """
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id or not self._enabled():
            return False
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=False)
        return float(state.attention_score) >= self.conversation_threshold()

    def should_focus_group(self, group_id: str) -> bool:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id or not self._enabled():
            return True
        focus_group_id = self.get_focus_group_id()
        if not focus_group_id or focus_group_id == normalized_group_id:
            return True
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=False)
        focus_state = self._apply_decay(self._load_state(focus_group_id), now, is_focus=True)
        focus_score = self._effective_focus_score(focus_state, now)
        return float(state.attention_score) + self._minimum_threshold() >= focus_score

    def mark_focus(self, group_id: str) -> None:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        state = self._load_state(normalized_group_id)
        now = self._current_time()
        current_id = self._current_focus_group_id([self._load_state(gid) for gid in self._normalized_groups()])
        # 被点名 = 唤醒。**点名口 = @（门控第 2 步）/ 关键词 / 引用她（第 4 步）** ——
        # 这三条都在注意力闸之前、都会走到 `mark_focus`，所以睡着的群被点到就醒；
        # 普通消息则在门控里被 dormancy 那条直接忽略（连计分都不做），自然保持睡眠。
        if self._state_is_dormant(state):
            state.dormant = False
            # 睡着那段时间的账不补：回溯补回的游标推到此刻（同 `wake_from_dormancy`）。
            state.last_focus_reason = "wake:addressed"
            self.plugin._emit_log(
                "INFO", f"[Attention] 群{normalized_group_id} 从休眠中唤醒（点名）",
            )
        state.last_focus_at = now
        if current_id != normalized_group_id or int(state.focus_acquired_at or 0) <= 0:
            state.focus_acquired_at = now
        # 被点名 = 这个群此刻一定"有人理她" → 热度重算（不再有"转入上升相位"这回事，
        # 热度只看本群最后一条消息的时刻）。
        state.heat = self._heat_tier(state, now)
        self._write_state(state)
        getattr(self.plugin, "_maybe_push_status_event", lambda: None)()  # 焦点变更 → SSE 通知前端

    def wake_boost(self, group_id: str) -> None:
        """叫醒时给一个注意力启动值，确保能突破焦点阈值。"""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        state = self._load_state(normalized_group_id)
        if state.attention_score < self._focus_threshold():
            state.attention_score = max(
                state.attention_score, self._focus_threshold() * self._wake_boost_ratio(),
            )
            state.heat = self._heat_tier(state, self._current_time())
            self._write_state(state)
            self.plugin._emit_log("INFO", f"[Attention] 唤醒 boost: 群{normalized_group_id} score={state.attention_score:.1f}")
            getattr(self.plugin, "_maybe_push_status_event", lambda: None)()  # 注意力唤醒 → SSE 通知前端

    # ── 锁（@ / 唤醒词）───────────────────────────────────────────────
    #
    # 设计意图（见 docs/attention-redesign-draft.md §2）：
    #   无锁时归属 = 分数最高的群，**随时可变** —— 这就是「看一眼新群、没兴趣
    #   就回旧群」的实现：旧群只要还是最有意思的，下一个 tick 就自动回去。
    #   有锁时该群独占，其余群不参与竞争。
    #
    # 与「让位」的分工：锁表达「有人点名叫我，我必须回头应对」；分数表达
    # 「没人叫我，我自己按兴趣看哪」。两者是不同信号，不该合成一个数。

    def lock_group(self, group_id: str, *, seconds: int | None = None, reason: str = "at") -> None:
        """把注意力锁在某个群一段时间（`@猫娘` / 唤醒词）。

        锁内 `get_focus_group()` 直接返回该群，其余群不参与竞争；到期自动解除。
        重复锁会**重置**计时（再叫一次就重新锁满）—— 这与人对重复召唤的直觉一致。

        ``seconds`` 不传就用 `attention_lock_seconds`（@ 的既有行为）。

        ⚠️ 跨群取舍删除（2026-09-29）之后，"锁"**已经不影响任何群能不能说话**了：
        门控、必要度、复读三处都按本群自己的分数判定。它现在只剩两个作用：
        ① 决定 UI 快照里显示哪个群（`_choose_focus_state` 的优先级 1）；
        ② 让按群维护循环读到的 `lock_until` 参与"这个群有人点名，别另起话题"的判断。
        那句"期内独占焦点"的日志因此是不准确的 —— 属于 §40.5 待清理的那一批。
        """
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        hold = self._lock_seconds() if seconds is None else max(0, int(seconds))
        now = self._current_time()
        state = self._load_state(normalized_group_id)
        if hold <= 0:
            return
        state.lock_until = now + hold
        state.last_focus_reason = "lock" if reason == "at" else f"lock:{reason}"
        self._write_state(state)
        self.plugin._emit_log(
            "INFO",
            f"[Attention] 群{normalized_group_id} 上锁 {hold}s"
            f"（{'@/唤醒词' if reason == 'at' else reason}），期内独占焦点",
        )
        if self.plugin.logger:
            self.plugin.logger.info(
                f"[Attention] 群 {normalized_group_id} 上锁 {hold}s（reason={reason}），期内独占焦点"
            )
        getattr(self.plugin, "_maybe_push_status_event", lambda: None)()

    def release_lock(self, group_id: str) -> None:
        """提前解除锁（如锁群被移除信任）。"""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        state = self._load_state(normalized_group_id)
        if int(state.lock_until or 0):
            state.lock_until = 0
            self._write_state(state)
            self.plugin._emit_log("INFO", f"[Attention] 群{normalized_group_id} 解锁")

    def locked_group_id(self, *, now: int | None = None) -> str:
        """当前仍在锁内的群（无 / 已过期 → 空串）。"""
        moment = self._current_time() if now is None else now
        best: tuple[int, str] = (0, "")
        for group_id in self._normalized_groups():
            state = self._load_state(group_id)
            until = int(state.lock_until or 0)
            if until > moment and until > best[0]:
                best = (until, state.group_id)
        return best[1]

    def _lock_seconds(self) -> int:
        """锁的时长（秒）。0 = 不锁（回到纯分数仲裁）。"""
        return max(0, int(self._setting("attention_lock_seconds", 90)))

    def get_last_focus_at(self, group_id: str) -> int:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return 0
        return int(self._load_state(normalized_group_id).last_focus_at)

    def _get_top_group_id(self) -> str:
        now = self._current_time()
        states = [self._load_state(gid) for gid in self._normalized_groups()]
        focus = self._choose_focus_state(states, now, stamp_transition=False)
        return focus.group_id if focus else ""

    # ── 全局休眠判定 ──

    def is_global_sleep(self) -> bool:
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        for group_id in self._normalized_groups():
            state = self._apply_decay(self._load_state(group_id), now, is_focus=(group_id == focus_group_id))
            if float(state.attention_score) >= self._minimum_threshold():
                return False
        if not self._normalized_groups():
            return False
        return True

    def get_focus_group(self) -> str | None:
        if self.is_global_sleep():
            return None
        snapshot = self.get_snapshot()
        focus_id = str(snapshot.get("focus_group_id") or "")
        return focus_id if focus_id else None

    # ── 情绪 ──

    async def set_emotion(self, group_id: str, emotion: str) -> None:
        """LLM 回复中的 <feeling> 标签更新情绪状态。

        强情绪直接触发焦点切换：``_EMOTION_FORCE_FOCUS`` 抢焦点、
        ``_EMOTION_DROP_FOCUS`` 让焦点（名字见那两个集合，不在此处重抄）。
        """
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        if emotion not in self._emotion_multipliers():
            return
        state = self._load_state(normalized_group_id)
        now = self._current_time()
        state.emotion = emotion
        state.emotion_updated_at = now
        # emotion_display 供前端展示，停留 120s 比逻辑衰减更久
        state.emotion_display = emotion
        state.emotion_display_until = now + 120

        if emotion in _EMOTION_FORCE_FOCUS:
            # 抬分：把注意力顶到档位线上（跨群取舍删除后不再有"抢焦点"这回事 ——
            # 分高的群不会因此独占说话权，她只是更愿意留在这个群）
            state.last_focus_at = now
            state.focus_acquired_at = now
            state.last_focus_reason = f"emotion:{emotion}"
            state.attention_score = max(state.attention_score, self._focus_threshold())
            self.plugin._emit_log("INFO", f"[Emotion] 群{normalized_group_id} 抬分: {emotion} score={state.attention_score:.1f}")
        elif emotion in _EMOTION_DROP_FOCUS:
            # 压分：把注意力压到目标线下（"不想聊了"的表达；热度档仍然只看群里有没有人说话）
            floor = self._minimum_threshold() if emotion == "sulking" else self._focus_threshold()
            state.attention_score = min(state.attention_score, floor)
            self.plugin._emit_log("INFO", f"[Emotion] 群{normalized_group_id} 压分: {emotion} score={state.attention_score:.1f}")
        state.heat = self._heat_tier(state, now)

        self._write_state(state)
        await self._persist()
        self.plugin._emit_log("INFO", f"[Emotion] 群{normalized_group_id} 情绪: {emotion}")
        getattr(self.plugin, "_maybe_push_status_event", lambda: None)()  # 情绪变更 → SSE 通知前端

    def _decay_emotion(self, state: QQGroupAttentionState, now: int) -> None:
        """情绪自然衰减：30秒无新情绪则向 calm 方向降温一级。

        上升侧/回落侧的判定来自 ``_EMOTION_MULTIPLIER`` 的符号，不另抄一份名单 ——
        以前这里硬编码了 ``("arguing", "annoyed", "playful", "curious")``，导致任何
        新加的正倍率情绪都会走回落侧分支、越衰减越强。
        """
        if state.emotion == "calm":
            return
        elapsed = now - state.emotion_updated_at
        if elapsed < _EMOTION_DECAY_SECONDS:
            return
        order = _EMOTION_DECAY_ORDER
        calm_idx = order.index("calm")
        idx = order.index(state.emotion) if state.emotion in order else -1
        if idx < 0:
            # 表外情绪（LLM 可能自造标签）直接归位到 calm
            state.emotion = "calm"
        elif _EMOTION_MULTIPLIER.get(state.emotion, 0.0) > 0:
            # 上升侧：朝 calm 走一级，不越界
            state.emotion = order[idx + 1] if idx + 1 < calm_idx else "calm"
        else:
            # 回落侧：同样朝 calm 走一级
            state.emotion = order[idx - 1] if idx - 1 > calm_idx else "calm"
        state.emotion_updated_at = now
        state.emotion_display = state.emotion

    # ── 手动增减注意力 ──

    async def boost_attention(self, group_id: str, amount: float, reason: str = "") -> None:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id or amount <= 0:
            return
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=(normalized_group_id == focus_group_id))
        state.attention_score = min(self._max_attention(), state.attention_score + amount)
        state.last_boost_at = now
        state.last_focus_reason = reason or "manual_boost"
        self._write_state(self._normalize_state(state))
        await self._persist()

    async def consume_attention(self, group_id: str, amount: float, reason: str = "") -> None:
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id or amount <= 0:
            return
        focus_group_id = self.get_focus_group_id()
        now = self._current_time()
        state = self._apply_decay(self._load_state(normalized_group_id), now, is_focus=(normalized_group_id == focus_group_id))
        state.attention_score = max(0.0, state.attention_score - amount)
        state.last_reply_at = now
        state.last_focus_reason = reason or "manual_consume"
        self._write_state(self._normalize_state(state))
        await self._persist()

    # ── 后台衰减循环 ──

    async def start_decay_loop(self, interval_seconds: float | None = None) -> None:
        """启动衰减循环。``interval_seconds=None`` 时读配置（attention_decay_interval_seconds）。"""
        if interval_seconds is None:
            interval_seconds = self._decay_interval()
        self._decay_task = asyncio.create_task(self._decay_loop(interval_seconds))

    async def stop_decay_loop(self) -> None:
        task = getattr(self, "_decay_task", None)
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _decay_loop(self, interval_seconds: float) -> None:
        """注意力衰减驱动。

        ``decay_all`` 会在 try **外面**执行——它碰磁盘（``_persist`` 读改写
        backlog_state.json），一次坏 JSON / 磁盘错误 / 目录被撤权就会
        直接杀掉这个循环，而注意力推进与焦点释放随之中止：UI 仍显示旧值、
        没有任何报错，只能手动停止再启动插件。异常必须在这里就地吞掉并
        记录，让下一轮继续推进。
        """
        while True:
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break
            try:
                await self.decay_all()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self.plugin.logger:
                    self.plugin.logger.warning(f"[Attention] 衰减轮次异常，已跳过本轮: {e}")

    async def decay_all(self) -> None:
        if not self._enabled():
            return
        now = self._current_time()
        old_focus_id = self._get_top_group_id()
        for group_id in self._normalized_groups():
            state = self._load_state(group_id)
            # emotion_display 到期 → 重置为 calm
            if now > state.emotion_display_until and state.emotion_display != "calm":
                state.emotion_display = "calm"
            if self._maybe_fall_asleep(state, now):
                where = "被点名或群里又聊热就醒（没有到点自动醒）"
                self.plugin._emit_log(
                    "INFO",
                    f"[Attention] 群{group_id} 已静默 {now - int(state.last_message_at or now)}s，"
                    f"冷漠了 → 休眠（{where}）；这个群此后只答点名",
                )
                if self.plugin.logger:
                    self.plugin.logger.info(
                        f"[Attention] 群 {group_id} 冷漠了 → 休眠（{where}），"
                        f"此后只答 @ / 引用她 / 关键词"
                    )
            state = self._apply_decay(state, now)
            self._decay_emotion(state, now)
            # 接话反馈：**由时间触发**的结算点（没有新消息的群也要能结算出「没人接」）。
            self._settle_feedback(state, now)
            self._write_state(state)
        # 焦点身份变化（UI 快照用）—— 2026-09-29 起不再有任何"蜜月计时"跟着它跑
        new_focus_id = self._get_top_group_id()
        if new_focus_id and new_focus_id != old_focus_id:
            new_state = self._load_state(new_focus_id)
            new_state.focus_acquired_at = now
            self._write_state(new_state)
        await self._persist()

    async def _persist(self) -> None:
        """把内存缓存里的注意力状态落盘。

        落盘走 ``backlog_store.update_group_attention_state``（锁内读改写）而
        不是自己 ``load→save``：后者与 ``append_message`` 交错时会互相覆盖
        —— 要么丢刚 append 的群消息，要么丢刚推进的注意力分数。
        """
        store = getattr(self.plugin, "backlog_store", None)
        if not store:
            # 早退语义必须保留：``cleanup_stale_cache`` 在 group_permission_mgr
            # 缺失时会把**所有**群从缓存里删掉（它按"不在信任列表里"清理），
            # 而 ``update_on_message`` 每轮都调 ``_persist``（``_KwPlugin`` 这类
            # 桩把两个依赖都设为 None）。先清理会把刚算出来的分数一起抹掉。
            return
        self.cleanup_stale_cache()
        await store.update_group_attention_state(dict(self._cache))
