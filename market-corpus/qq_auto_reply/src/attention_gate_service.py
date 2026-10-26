"""注意力门控服务 — 每个群按**自己的**注意力决定搭不搭话

职责：
1. 每条群消息到达时，更新该群注意力、判定是否回复（点名优先，注意力垫底）
2. 本群**凉转热**时回头看它错过的消息（回溯补回，见 `_note_in_conversation`）
3. 按群维护循环：谁的群记忆有增量谁推摘要（`_maintenance_loop`）
4. 全局休眠判定（所有群分数都在最低线以下）

2026-09-29：跨群取舍（"唯一的焦点群说了算"）整体删除 —— 使用者口径「每个群自己管自己
的注意力」。原来挂在焦点切换上的机制（回溯补回、记忆摘要）全部改成**按群触发**；门控里
那道「非焦点群一律 block」的闸门换成"本群分数过没过保持线"。
同日稍后：**主动破冰 + 休眠整套删除**（使用者：「干脆不要这个先」）—— 那套的触发判据是
`icebreaker_idle_seconds`（"静默 30 分钟"），使用者要求不要用时间判断，而他选了"先不要"。

底层依赖 QQAttentionService 提供注意力分数、衰减、`is_in_conversation` 判定。
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

from . import addressing
from .dialogue_partner import DialoguePartnerTracker
from .feedback_classifier import QQFeedbackClassifier
from .pipeline_models import backlog_sender_label
from .reply_necessity import (
    ADDRESSED_ELSEWHERE_PENALTY_DEFAULT,
    DEFAULT_TRIGGER_SCORE,
    GroupSpeechTracker,
    IdleBackoff,
    NecessitySignals,
    score_necessity,
)


class GateDecision:
    """门控决策结果"""
    __slots__ = ("action", "reason", "force_reply")

    def __init__(self, action: str, reason: str = "", force_reply: bool = False):
        self.action = action      # "reply" | "ignore"
        self.reason = reason
        self.force_reply = force_reply


#: 进程内那条按群维护循环的归属者（`start_proactive_loop` 的进程级单飞闸）。
#:
#: 为什么不能只靠服务实例上那把 `_maintenance_task`：宿主启动时会把插件的初始化跑
#: **不止一遍**（收集入口 / 收集 UI 上下文 / 真正跑），每次都会调 `start_proactive_loop`。
#: 真机 2026-09-29 16:32:08 与 16:36:08 的日志里同一行启动标记都出现了两次，而且
#: 16:37:08 与 16:37:13 两个群**相差 5 秒**各破了一次冰 —— 一轮最多破一次的约束
#: 是按"一条循环"算的，两条循环就是两次，所以她同时开了两个群的话题。
#:
#: 为什么挂在 `sys` 上而不是本模块的全局：插件重载时本模块会被**重新导入**，
#: 模块全局随之重置（新模块对象 = 新的 `_MAINTENANCE_OWNER`），闸门形同不存在；
#: `sys` 每个解释器只有一份，重载不会换。
_MAINTENANCE_OWNER_ATTR = "_qq_auto_reply_maintenance_owner"


def _maintenance_owner() -> "QQAttentionGateService | None":
    return getattr(sys, _MAINTENANCE_OWNER_ATTR, None)


def _set_maintenance_owner(owner: "QQAttentionGateService | None") -> None:
    setattr(sys, _MAINTENANCE_OWNER_ATTR, owner)


class QQAttentionGateService:
    """基于注意力的多群门控 + 回溯补回（每群自己管自己）"""

    async def start_proactive_loop(self) -> None:
        """启动**按群**的维护循环（群记忆摘要）。

        这件事原来挂在**跨群焦点切换**上（`check_focus_shift`）：切换发生时给旧焦点群
        推记忆摘要。跨群取舍删掉后（2026-09-29 使用者口径：「每个群自己管自己的注意力」）
        没有"切换"这个事件可挂了，于是改成**每个群自己一个时钟**：谁的群记忆有增量谁推摘要。
        判据全部来自各群自己的状态（游标 + 时间戳），与别的群无关。

        （同一天稍后：这里原本还负责"冷场破冰"，整套已删除 —— 见模块 docstring。）

        **进程内只允许一条**（_maintenance_owner()，见模块级注释）：宿主在启动阶段会把
        插件的初始化跑不止一遍（真机 2026-09-29 16:32:08 的日志里同一行出现了两次），
        两条循环会把同一份摘要推两遍 —— 一个进程只需要一个维护者。
        """
        owner = _maintenance_owner()
        if owner is not None and owner._maintenance_task is not None and not owner._maintenance_task.done():
            self._logger.info("[Gate] 按群维护循环已由本进程的另一处启动，跳过重复启动")
            return
        task = getattr(self, "_maintenance_task", None)
        if task is not None and not task.done():
            return
        interval = self._maintenance_interval()
        # 起一行标记：这条循环是**纯后台**的（推群记忆），没有它就没有任何"我起来了"
        # 的凭据 —— 真机验收只能靠"她怎么不推摘要"来反推。
        self._logger.info(
            f"[Gate] 按群维护循环已启动（每 {interval:.0f}s 过一遍每个群："
            f"群记忆摘要每 {self._digest_interval_seconds()}s，均为**每群自己的**时钟；"
            f"主动破冰已于 2026-09-29 删除）"
        )
        self._maintenance_task = asyncio.create_task(
            self._maintenance_loop(interval),
        )
        _set_maintenance_owner(self)

    async def stop_proactive_loop(self) -> None:
        """停掉维护循环（插件停止/重载时调用）。"""
        if _maintenance_owner() is self:
            _set_maintenance_owner(None)
        task = getattr(self, "_maintenance_task", None)
        self._maintenance_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: B014 - 与隔壁 decay_loop 同款
            pass

    async def _maintenance_loop(self, interval_seconds: float) -> None:
        """按群维护循环。

        单轮异常**就地吞掉**（与 `attention_service._decay_loop` 同款理由）：它要碰
        磁盘与 memory server，一次坏 JSON / 网络抖动若把循环杀掉，群记忆会无声无息地
        永久停摆 —— 那种故障在日志里只表现为"群记忆怎么不涨了"。
        """
        while True:
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break
            try:
                await self.run_maintenance_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.warning(f"[Gate] 按群维护轮次异常，已跳过本轮: {e}")

    async def run_maintenance_tick(self) -> None:
        """走一遍所有群，按**每群自己的**节奏推群记忆摘要。

        （2026-09-29 之前这里还做第二件事：给静默够久的群"主动破冰"。破冰整套已删除 ——
        墓碑见 `attention_service` 末尾那段 + `docs/SESSION-HANDOFF.md` §42。）
        """
        attention = self.plugin.attention_service
        if not attention:
            return
        now = int(attention._current_time())
        for group_id in self._maintenance_groups(attention):
            await self._maybe_push_digest(group_id, now)
        # 这条循环同时给**管理员私聊**的主动搭话当心跳（`proactive_initiative_service`，
        # 2026-10-02）。它判的是"她说过的那件事到期没有"，不是"群静默了多久" —— 与上面
        # 已删除的破冰不是一回事。放在群循环之后：群记忆摘要关乎正确性，主动消息晚一轮无妨。
        initiative = getattr(self.plugin, "proactive_initiative_service", None)
        if initiative is not None:
            await initiative.tick()

    def _maintenance_groups(self, attention: Any) -> list[str]:
        """本服务要照看的群清单（注意力账本里出现过的群）。"""
        try:
            groups = [str(g or "").strip() for g in attention._normalized_groups()]
        except Exception:
            return []
        return [g for g in groups if g]

    def _maintenance_interval(self) -> float:
        """按群维护循环的 tick 间隔（秒，`attention_maintenance_interval_seconds`，默认 60）。

        群记忆摘要的及时性用分钟级粒度就够；间隔越小只是推得越勤，代价是每个 tick
        都要把所有群过一遍（读内存态，不落盘）。
        """
        raw = (self.plugin._qq_settings or {}).get(
            "attention_maintenance_interval_seconds", 60,
        )
        try:
            return max(5.0, float(raw))
        except (TypeError, ValueError):
            return 60.0

    def _digest_interval_seconds(self) -> int:
        """群记忆摘要的推送间隔（秒，`group_memory_digest_interval_seconds`，默认 300）。"""
        raw = (self.plugin._qq_settings or {}).get(
            "group_memory_digest_interval_seconds", 300,
        )
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 300

    async def _maybe_push_digest(self, group_id: str, now: int) -> bool:
        """群记忆摘要按**每群自己的**节奏推送（`group_memory_digest_interval_seconds`）。

        原来它挂在焦点切换上："焦点离开这个群时把它的会话增量推给 Memory Server"。
        跨群取舍删掉后没有"离开"这个事件，改成按时间推 —— 推送本身是**幂等**的
        （游标 `last_group_digest_index` 精确记录推到哪里），所以"多久推一次"只影响
        及时性，不影响正确性。
        """
        interval = self._digest_interval_seconds()
        if interval <= 0:
            return False
        if not bool((getattr(self.plugin, "_qq_settings", {}) or {}).get("group_memory_enabled", False)):
            return False
        if now - int(self._last_digest_at.get(group_id, 0)) < interval:
            return False
        self._last_digest_at[group_id] = now
        await self._push_group_digest(group_id)
        return True


    def _touch_group(self, group_id: str) -> None:
        """保留接口兼容性——冷场检测已改为焦点切换计数。"""
        pass

    def _mark_active(self, group_id: str) -> None:
        """这条消息被放行了 → 记下"这个群在聊"，并在**凉转热**那一下排一次回溯补回。

        原来的等价物是"焦点切到这个群"（dispatcher 拿 `check_focus_shift()` 的返回值再调
        `run_retroactive_review`）。跨群取舍删掉后没有切换事件，判据换成每群自己的状态
        迁移，见 `_note_in_conversation`。
        """
        if self._note_in_conversation(group_id):
            self._schedule_retro_review(str(group_id or "").strip())

    def _log_decision(self, message: str) -> None:
        """门控决策双写：**文件日志**（可事后核对、重启不丢）+ 内存环（前端实时看）。

        只给「为什么这一轮没接」这类判定用 —— 它们正是 live 验证要核对的凭据。
        历史上这些行只走 ``_emit_log``，插件一重载就没了，等于无法回查。
        """
        try:
            self._logger.info(message)
        except Exception:
            pass
        self.plugin._emit_log("INFO", message)

    def _event_now(self, timestamp: Any = None) -> float:
        """事件时刻（秒）。有 timestamp 就用它，否则用墙上时钟。

        对话对象的窗口按**事件时间**算：门控里的消息时间戳在回放/测试里是可控的，
        用它才能让"十分钟内"这类断言稳定可测。
        """
        if timestamp:
            try:
                return float(timestamp)
            except (TypeError, ValueError):
                pass
        return float(__import__("time").time())

    def is_in_dialogue_with(self, group_id: str, user_id: str, *, now: float | None = None) -> bool:
        """这个人现在算不算「正在和她对话」（见 `dialogue_partner`）。"""
        return self._dialogue.is_in_dialogue(group_id, user_id, now=now)

    def _necessity_threshold(self) -> float:
        """阈值来自设置（``reply_necessity_threshold``）；0 = 关闭这一关。

        ⚠️ 不能用 ``or`` 兜底：配置成 0 是「关闭」的合法值，``0 or 默认`` 会把它吃掉。
        """
        raw = (self.plugin._qq_settings or {}).get("reply_necessity_threshold", DEFAULT_TRIGGER_SCORE)
        return DEFAULT_TRIGGER_SCORE if raw is None else max(0.0, float(raw))

    def _evaluate_necessity(
        self, *, group_id: str, sender_id: str, message_text: str, is_reply_to_bot: bool, now: float,
        addressee: addressing.AddresseeVerdict | None = None, in_conversation: bool = False,
    ):
        """组装信号并打分。信号全部来自本进程已有的状态，不额外查库。"""
        threshold = self._necessity_threshold()
        pending = self._speech.pending_count(group_id, now=now)
        attention = self.plugin.attention_service
        frequency = 1.0
        if attention:
            try:
                frequency = min(1.0, float(attention.get_group_multiplier(group_id) or 1.0))
            except Exception:
                frequency = 1.0
        signals = NecessitySignals(
            message_text=message_text,
            is_group=True,
            # 「本群在聊」= 本群分数过保持线（`is_in_conversation`）。跨群取舍去掉后
            # 这里不再是"抢到唯一焦点"，而是每个群各自判断自己在不在状态。
            focus_active=in_conversation,
            is_reply_to_bot=is_reply_to_bot,
            pending_count=max(1, pending),
            pending_threshold=self._pending_threshold(),
            self_ratio=self._speech.self_ratio(group_id, now=now),
            idle_reached_average=self._speech.last_gap_seconds(group_id, now=now) >= 30.0,
            human_pair_streak=self._human_pair_streak.get(str(group_id or "").strip(), 0),
            addressee_kind=(addressee.kind if addressee is not None else ""),
            addressee_target=(addressee.target_id if addressee is not None else ""),
            addressee_first_at_other=bool(addressee is not None and addressee.first_at_other),
        )
        return score_necessity(
            signals,
            threshold=threshold,
            frequency=frequency,
            human_pair_penalty=self._human_pair_penalty(),
            human_pair_min_streak=self._human_pair_min_streak(),
            addressee_penalty=self._addressee_penalty(),
        )

    def _human_pair_penalty(self) -> float:
        """「人对人」惩罚分。**默认 0.0**：先出数据，不改行为（2026-09-27）。"""
        settings = self.plugin._qq_settings or {}
        try:
            return max(0.0, float(settings.get("necessity_human_pair_penalty", 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _human_pair_min_streak(self) -> int:
        settings = self.plugin._qq_settings or {}
        try:
            return max(1, int(settings.get("necessity_human_pair_min_streak", 3) or 3))
        except (TypeError, ValueError):
            return 3

    def resolve_addressee_for(
        self,
        *,
        message_text: str,
        is_at_bot: bool = False,
        is_reply_to_bot: bool = False,
        mentions_all: bool = False,
        mentioned_user_ids: list[str] | None = None,
        quoted_message_id: str = "",
        quoted_sender_id: str = "",
        segments: Any = None,
        group_id: str = "",
    ) -> addressing.AddresseeVerdict:
        """算出「这条消息在跟谁说话」。

        **唯一的入口**：`evaluate()` 与 dispatcher 都走它（后者是为了把结论带进
        `QQReplyRequest`、再喂给提示词层）。dispatcher 在 `evaluate()` **之后**再
        调一次不算浪费：那一次拿到的 `msgs_after_reply` 已经推进过，正是提示词
        要的"她刚说完之后的第一条"口径；而它是纯函数，重算一次不产生新真源。

        `self_id` 与名字清单在这里补齐（调用方不需要知道它们从哪来）。
        """
        return addressing.resolve_addressee(
            self_id=str(getattr(self.plugin.qq_client, "self_id", "") or ""),
            text=message_text,
            is_at_bot=is_at_bot,
            is_reply_to_bot=is_reply_to_bot,
            mentions_all=mentions_all,
            mentioned_user_ids=mentioned_user_ids or (),
            quoted_message_id=quoted_message_id,
            quoted_sender_id=quoted_sender_id,
            segments=segments,
            names=self._addressee_names(),
            first_reply_after_own_speech=self._first_reply_after_own_speech(group_id),
        )

    def _addressee_penalty(self) -> float:
        """「明确在跟别人说话」时扣多少分（`addressing` 的减分力度）。

        与 `_human_pair_penalty` 的默认值刻意相反：那条默认 0（先出数据），这条默认
        30（当场生效）。理由是两者的证据强度不同，见 `score_necessity` 的 docstring。
        """
        settings = self.plugin._qq_settings or {}
        try:
            return max(
                0.0,
                float(settings.get(
                    "addressee_penalty", ADDRESSED_ELSEWHERE_PENALTY_DEFAULT,
                ) or 0.0),
            )
        except (TypeError, ValueError):
            return ADDRESSED_ELSEWHERE_PENALTY_DEFAULT

    def _addressee_ignore_first_at_other(self) -> bool:
        """首段 @ 的就是别人时，是否**直接不唤醒**（AstrBot 的唤醒判据）。

        默认关：群里 @ 别人但话题确实落在她身上时，硬拦会误伤（那种消息本来还能
        靠内容分或积压压力把这一条捞回来）。想复刻 AstrBot 的"第一句 @ 别人 =
        不叫它"，把这个开关打开即可 —— 改一个配置键，不用改代码。
        """
        settings = self.plugin._qq_settings or {}
        return bool(settings.get("addressee_ignore_first_at_other", False))

    def _addressee_names(self) -> tuple[str, ...]:
        """她的名字/别名清单（本体人设里的名字 + 用户补的别名），带 TTL 缓存。

        为什么要缓存：`host_names()` 会读一次本体角色数据（JSON），而这是**每条群
        消息**都要走的路。换人格时人设会变，所以不能永久缓存 —— 60 秒足够让
        "改完名字下一分钟生效"，又不至于每条消息读一次盘。
        """
        now = time.time()
        cached = getattr(self, "_addressee_names_cache", None)
        if cached is not None and now - cached[0] < self.ADDRESSEE_NAMES_TTL_SECONDS:
            return cached[1]
        names = addressing.configured_names(
            self.plugin._qq_settings or {}, host_names=addressing.host_names(self.plugin),
        )
        self._addressee_names_cache = (now, names)
        return names

    def _first_reply_after_own_speech(self, group_id: str) -> bool:
        """她刚说完、而这是之后的第一条发言（判据在注意力服务，这里只做容错转发）。

        与 `prompt_builder._is_first_reply_after_her_own_speech` 同一个判据的同一个
        来源（`msgs_after_reply`）。提示词层还会自己再问一次 —— 两处都问不是重复：
        门控这里问是为了**打分**，那边问是为了**文案**，各自都要能在对方缺席时工作。
        取不到注意力服务（或它炸了）时返回 False：退回改动前的行为，不抛。
        """
        attention = getattr(self.plugin, "attention_service", None)
        if attention is None or not group_id:
            return False
        try:
            return bool(attention.is_first_reply_after_own_speech(group_id))
        except Exception:
            return False

    def _record_human_pair(self, group_id: str, *, addressed_to_bot: bool) -> int:
        """维护「连续多少条别人的消息没在跟她说话」。

        判据只有两条：**@ 她** 或 **引用她** → 归零；其余别人的消息 +1。

        ⚠️ 这里曾经写着「NapCat 侧要拿被引用者的 uid 得额外 `get_msg`，所以不做
        "谁回谁"的推断」——**这个前提是错的**（2026-09-27 核对）：被引用者的 uid 就在
        回复段的 `data.user_id` 里，连接器早已解析成 `quoted_sender_id`
        （`_vendor/connection_onebot/onebot_client.py:261`），不需要任何额外请求。
        逐条的"谁在跟谁说话"现在由 `addressing.resolve_addressee` 负责（它读
        `quoted_sender_id` / `mentioned_user_ids` / 段序），**本计数器仍然是粗粒度的
        结构量**（"这群人连着几条没理她"）—— 两者是不同尺度，不是一个东西的两个实现：
        前者逐条可判、当场减分；后者要攒够 streak 才有意义，且默认惩罚 0（先出数据）。
        """
        key = str(group_id or "").strip()
        if not key:
            return 0
        if addressed_to_bot:
            self._human_pair_streak[key] = 0
            return 0
        streak = int(self._human_pair_streak.get(key, 0)) + 1
        self._human_pair_streak[key] = streak
        return streak

    def _participates_in_attention(self, group_id: str) -> bool:
        """这个群参不参与注意力竞争（只有 trusted 群参与）。

        单一真源在 `attention_service.participates_in_attention()`；这里先问它，
        拿不到（单测桩 / 旧宿主）再退回权限管理器，最后兜底"参与"——
        宁可多算一个群，也别把该回的群静音。"""
        checker = getattr(self.plugin.attention_service, "participates_in_attention", None)
        if callable(checker):
            try:
                return bool(checker(group_id))
            except Exception:
                return True
        manager = getattr(self.plugin, "group_permission_mgr", None)
        if manager is None:
            return True
        try:
            return str(manager.get_group_level(group_id) or "").strip() in ("", "trusted")
        except Exception:
            return True

    def _pending_threshold(self) -> int:
        """积压压力的参照条数。跟着缓冲上限走：缓冲越容易合并，参照越高。"""
        value = (self.plugin._qq_settings or {}).get("buffer_max_count")
        base = 17 if value is None else max(1, int(value))
        return max(1, min(10, base - 1))

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._retroactive_lock = asyncio.Lock()
        #: 按群维护（冷场破冰 / 群记忆摘要）的状态。跨群取舍删掉后这两件事不再挂
        #: 焦点切换，而是各自读"这个群自己"的时钟（见 `_maintenance_loop`）。
        self._maintenance_task: asyncio.Task | None = None
        self._last_digest_at: dict[str, int] = {}       # 群 → 上次推记忆摘要的时刻
        self._last_retro_at: dict[str, int] = {}        # 群 → 上次回溯补回的时刻
        self._in_conversation: dict[str, bool] = {}     # 群 → 上一轮判定"在聊吗"
        self._retro_tasks: set[asyncio.Task] = set()    # 在跑的回溯补回任务（强引用）
        #: 群 → 连续多少条"没在跟她说话"的消息（`human_pair_streak` 的存储）。
        #: 只出数据：默认惩罚 0，日志里能看到 `人对人×N`，等真机数据再决定扣多少。
        self._human_pair_streak: dict[str, int] = {}
        # 「这句该不该接」的两个状态机（内存态，重启即失）
        self._speech = GroupSpeechTracker()
        self._backoff = IdleBackoff()
        # 「正在和她对话的人」（一来一回，见 `dialogue_partner`）：禁言反应用它判断
        # 被禁言的人是不是此刻正在跟她聊天的那个人。
        self._dialogue = DialoguePartnerTracker()
        self._logger = plugin.logger

    # ── 「回复过于频繁 → 强制静默」这道硬闸**已删除**（2026-09-27 使用者口径）──
    #
    # 原实现：`_reply_timestamps` 记她在这个群的回复时刻，窗口内条数到
    # `reply_burst_max_replies`（真机 3 条 / 60s）就整条静默（`reply_burst_limit`），
    # 只有 @ 她 / 引用她 / 关键词能绕过。
    #
    # 为什么删（使用者原话：「不要这个，有注意力控制频率了」）：
    #
    # 1. **它不看上下文**：真机 19:16 那次，她在 985066274 连发 3 条之后，使用者紧接着
    #    回了一句 —— 被这道闸静默。这与 17:37 那次「破冰完没有后续」是同一类毛病：
    #    她刚开口、这是第一条回应，却被"你太频繁了"挡住；
    # 2. **频率本来就有两处在管**：注意力（焦点竞争 + 分数消耗 + 频率增速缩放）决定她
    #    把时间花在哪个群，`pacing_hint`（软提示）在她说得偏密时提醒她收敛自己 ——
    #    两者都是"坡"；这道硬闸是断崖，且是唯一一个**不看内容只看计数**的出口；
    # 3. 真机数据：一天 17 次命中里 16 次在热闹群（那边确实刷），但**剩下那一次正好
    #    发生在一次一来一往的对话里** —— 代价与收益不成比例。
    #
    # 现在频率只剩软的那一半：`attention_service.pacing_hint()`（窗口与参考条数仍用
    # `reply_burst_*` 两个键，所以那两个键和它们的界面保留，只是不再触发静默）。
    # 看门狗 `tests/test_qq_no_burst_gate.py` 钉住"这道闸不许回来"。

    # ==========================================
    # 消息评估
    # ==========================================

    #: 「这句该不该接」的状态：近期发言窗口（积压 + 存在感）与空闲退避。
    #: 内存态、重启即失 —— 与调研里各家的同类状态一致（见 docs/GROUP-CHAT-RESPONSE-MECHANISMS.md）。
    _speech: GroupSpeechTracker
    _backoff: IdleBackoff

    #: 她的名字/别名清单的缓存时长（秒）。见 `_addressee_names`。
    ADDRESSEE_NAMES_TTL_SECONDS = 60.0

    async def evaluate(
        self,
        *,
        group_id: str,
        sender_id: str,
        is_at_bot: bool = False,
        message_text: str = "",
        message_id: str = "",
        quoted_message_id: str = "",
        sender_nickname: str = "",
        timestamp: int = 0,
        is_reply_to_bot: bool = False,
        mentioned_user_ids: list[str] | None = None,
        mentions_all: bool = False,
        quoted_sender_id: str = "",
        segments: Any = None,
    ) -> GateDecision:
        """评估群聊消息：先更新注意力，再按**本群自己**的分数决定搭不搭话，输出跳过原因。

        门控规则（点名优先，注意力垫底）：
        - @bot 直接点名 → 唯一旁路，任何群都强制回复
        - 关键词 / 引用她 → 强制回复（排在注意力闸**之前**：凉群里被点名也要答）
        - 其余消息 → 本群分数低于保持线则 block（注意力照常累计），够了就交给 LLM

        2026-09-29 之前这里还有一道"非焦点群一律 block"的跨群闸门；使用者口径
        「每个群自己管自己的注意力」之后删掉了，墓碑见下面第 0.5 步。

        后四个参数（`mentioned_user_ids` / `mentions_all` / `quoted_sender_id` /
        `segments`）是**「谁在跟谁说话」的原料**，由 dispatcher 从已规范化的消息里
        原样带下来（见 `addressing.py`）。它们全都有默认值：老调用方（单测桩、
        合成轮、旁路）不传时结论退化成"没结论"，减分与硬门控都不生效 ——
        也就是**改动前的行为**。
        """
        # 无需注意力的连接（如 QQ 开放平台）：直接回复
        if self.plugin.qq_client and not self.plugin.qq_client.needs_attention:
            return GateDecision("reply", reason="no_attention_needed", force_reply=is_at_bot)

        attention = self.plugin.attention_service
        if not attention or not attention._enabled():
            if self.plugin.permission_mgr and self.plugin.permission_mgr.get_permission_level(sender_id) == "admin":
                return GateDecision("reply", reason="admin_priority")
            if is_at_bot:
                return GateDecision("reply", reason="at_bot_fallback")
            self.plugin._emit_log("INFO", f"[Gate] 群{group_id} 忽略: 注意力未启用")
            return GateDecision("ignore", reason="attention_disabled")

        normalized_group_id = str(group_id or "").strip()
        # 只有 trusted 群参与注意力竞争（使用者 2026-09-27 拍板）。非参与者：不计分、
        # 不抢焦点、不被焦点门控拦 —— 直接放行给下游，由 reply_decision_node 决定
        # "回"还是"按概率转达给主人"。
        participates = self._participates_in_attention(normalized_group_id)

        # 0. 记录消息时间（用于主动发言检测）
        self._touch_group(normalized_group_id)

        # 0.5 墓碑（2026-09-29）：这里原来是 `focus_group = attention.get_focus_group()`，
        #    用来在 update_on_message() 之前捕获"接收时焦点"，防止当前群被 boost 后
        #    同一条非 @ 消息越过 `non_focus` 判定。跨群取舍已删除（使用者口径：
        #    「每个群自己管自己的注意力」），没有唯一焦点可捕获，取值处一并删掉。

        # 1. 消息更新注意力（每个群各自累计自己的分数）—— 只对参与竞争的群。
        if participates:
            await attention.update_on_message({
                "group_id": normalized_group_id,
                "user_id": sender_id,
                "content": message_text,
                "message_id": message_id,
                "timestamp": timestamp or attention._current_time(),
                "is_at_bot": is_at_bot,
            })

        # 连接层 is_reply_to_bot 优先（含 API 兜底），弱链缓存重算仅作兜底
        is_reply_to_bot = is_reply_to_bot or bool(
            quoted_message_id and self.plugin.qq_client
            and quoted_message_id in getattr(self.plugin.qq_client, "sent_message_ids", {})
        )

        # 1.2 「谁在跟谁说话」：结论算一次，下面三处都用它（减分 / 硬门控 / 提示词）。
        #     判据本身在 addressing.py，这里是它唯一的调用点 —— 门控、打分、文案
        #     三层共用同一份结论，各自重算一遍就会漂移。
        addressee = self.resolve_addressee_for(
            message_text=message_text,
            is_at_bot=is_at_bot,
            is_reply_to_bot=is_reply_to_bot,
            mentions_all=mentions_all,
            mentioned_user_ids=mentioned_user_ids,
            quoted_message_id=quoted_message_id,
            quoted_sender_id=quoted_sender_id,
            segments=segments,
            group_id=normalized_group_id,
        )
        if participates:
            # 只报数据不改行为的那一类也留痕：判据失效（比如段没拿到）时要看得见。
            self._log_decision(
                f"[Addressee] 群{normalized_group_id} {addressee.kind}"
                f"（{addressee.evidence or '—'}）"
            )

        # 1.5 记录到「近期发言窗口」：积压压力与存在感惩罚都吃它（也只服务 trusted 那条路）。
        if participates:
            self._speech.record(normalized_group_id, now=float(timestamp or attention._current_time()),
                                speaker=sender_id)
            # 「人对人」连击：@ 她 / 引用她 归零，其余别人的消息 +1（只出数据，见默认惩罚 0）。
            self._record_human_pair(
                normalized_group_id,
                addressed_to_bot=bool(is_at_bot or is_reply_to_bot),
            )

        # 2. @bot **或叫她的名字** → 必定回复（抢焦点 + 注意力 boost）——唯一焦点旁路。
        #    消息同时带「@」和「回复」时按回复处理，走焦点门控（用户确认）。
        #
        #    「叫名字」= `addressing` 的 `named_bot` 档（第 6 档：没人被 @、也没引用谁，
        #    但文本里有她的名字/别名）。使用者口径（2026-09-29）：「叫猫娘名字也等于@她」
        #    —— 于是它从"必要性打分里 +40（恰好压在阈值线上）"升级成**门控层的等同 @**：
        #    凉群里叫她的名字也要回头。判据顺序没变（仍在 `at_other` 之后）：既 @ 了别人、
        #    又提她名字的消息仍然算"冲着别人说的"。
        named_me = addressee is not None and addressee.kind == addressing.KIND_NAMED_BOT
        if named_me or (is_at_bot and not is_reply_to_bot):
            # 他找她 → 记进「正在和她对话的人」（与"她回他"合起来才算一来一回）。
            # 放在 `if participates` **之前**：不参与注意力竞争的群也要记 —— 反不反应
            # 是派发层的事（它另按 trusted 与冷却判断），这里只负责事实。
            self._dialogue.note_address(normalized_group_id, sender_id, now=self._event_now(timestamp))
            # 被 @ = **锁**：期内该群独占焦点，其余群不参与竞争。
            # mark_focus / wake_boost 仍保留（它们管分数与保持线），但独占语义由
            # lock 承担 —— 分数是「我多想聊这个群」，锁是「有人点名叫我」。
            # 见 docs/attention-redesign-draft.md §2「锁与归属是两条并行规则」。
            # 不参与竞争的群照旧必回，但**不上锁不抢焦点**（它本来就不在竞争里）。
            if participates:
                attention.lock_group(normalized_group_id)
                attention.mark_focus(normalized_group_id)
                attention.wake_boost(normalized_group_id)
                self._backoff.reset(normalized_group_id)   # 被点名 = 她必须回来，退避作废
            return GateDecision(
                "reply",
                reason=("named_bot" if named_me else "at_bot"),
                force_reply=True,
            )

        # 3. 黑名单 → 不处理（对**所有**群生效，含不参与竞争的群：这是全局过滤）
        label_defs = list((self.plugin._qq_settings or {}).get("backlog_labels") or [])
        if QQFeedbackClassifier.is_blacklisted(message_text, label_defs):
            return GateDecision("ignore", reason="blacklist")

        # 3.5 不参与注意力竞争的群（normal / none）：直接放行给下游。
        #     它们本来就不回复，下游 `reply_decision_node` 会决定"被 @/引用她 → 回"，
        #     其余消息按概率转达给主人（relay）。以前这里会走到第 4 步被当成
        #     non_focus 丢掉 —— relay 那条路因此永远走不到。
        if not participates:
            self._mark_active(normalized_group_id)
            self.plugin._emit_log(
                "INFO", f"[Gate] 群{normalized_group_id} 不参与注意力竞争，放行给下游（回/转达由权限层决定）",
            )
            return GateDecision("reply", reason="normal_group_passthrough")

        # 3.6 可选的硬门控：**第一句就在跟别人说话** → 这一轮不唤醒。
        #     复刻 AstrBot 的唤醒判据（首段是 At 且不是 At 她/全体 → 不唤醒），
        #     但默认**关**（`addressee_ignore_first_at_other`）：默认只走 8.5 的减分，
        #     因为"@ 了别人但话其实是问她的"在真机里并不罕见，硬拦会误伤。
        #     放在这里而不是更早：@ 她（步骤 2）与黑名单（步骤 3）都必须先判，
        #     而且只对**参与注意力竞争**的群生效 —— normal 群的 relay 语义不动。
        if addressee.first_at_other and self._addressee_ignore_first_at_other():
            self._log_decision(
                f"[Gate] 群{normalized_group_id} 首段 @ 的是别人（{addressee.first_at}），不唤醒"
            )
            return GateDecision("ignore", reason=f"addressee_first_at_other({addressee.first_at})")

        # 4. 「谁在点名」优先于注意力闸：**关键词 / 回复她** 直接放行。
        #    这两条以前排在**焦点门控之后**，于是非焦点群里的"引用她"和"关键词"
        #    会被当 non_focus 丢掉；跨群取舍去掉后（2026-09-29 使用者口径：
        #    「每个群自己管自己的注意力」）没有"非焦点"这回事，点名就该回。
        category = QQFeedbackClassifier.classify(message_text, label_defs)
        if category == "mention" and not is_at_bot:
            category = "chat"
        if category and category != "chat":
            self._dialogue.note_address(normalized_group_id, sender_id, now=self._event_now(timestamp))
            if participates:
                attention.mark_focus(normalized_group_id)
                attention.wake_boost(normalized_group_id)
                self._backoff.reset(normalized_group_id)
            return GateDecision("reply", reason=f"keyword:{category}", force_reply=True)

        if is_reply_to_bot:
            self._dialogue.note_address(normalized_group_id, sender_id, now=self._event_now(timestamp))
            if participates:
                attention.mark_focus(normalized_group_id)
                attention.wake_boost(normalized_group_id)
                self._backoff.reset(normalized_group_id)
            return GateDecision("reply", reason="reply_to_bot", force_reply=True)

        # 4.5 这个群冷漠了（静默 ≥ `dormancy_idle_seconds`，默认半小时）→ **只答点名**。
        #     点名类（@ / 关键词 / 引用她）在上面已经短路返回了，所以走到这里的必然是
        #     普通消息：这一轮不看不答。
        #
        #     ⚠️ **计分照常**：消息加成在第 1 步（`update_on_message`）就已经入账了，
        #     早于这道判定。这不是漏网，而是机制 —— "群里又聊热就醒"（`_maybe_wake_up`）
        #     正是靠这些普通消息把分数顶过 `dormancy_wake_score`。真改成"不计分"，
        #     一个睡着的群就只剩"被点名"一条路能醒（见 tests/test_qq_dormancy.py
        #     的 test_plain_chatter_still_scores_while_dormant）。
        is_dormant = False
        try:
            is_dormant = bool(attention.is_dormant(normalized_group_id))
        except Exception:
            is_dormant = False
        if is_dormant:
            self.plugin._emit_log(
                "INFO",
                f"[Gate] 群{normalized_group_id} 冷漠休眠中（静默够久），只答点名，这一轮忽略",
            )
            return GateDecision("ignore", reason="dormant")

        # 5. 没人点名 → 看**这个群自己**的注意力够不够（原来这里是"是不是焦点群"，
        #    非焦点一律 block）。低于保持线说明这个群最近没在聊，那就不搭话 ——
        #    判据从"跟别的群比"改成"看自己在不在状态"，每个群各自算。
        in_conversation = bool(attention.is_in_conversation(normalized_group_id))
        current_score = float(attention.get_state(normalized_group_id).attention_score)
        min_threshold = attention.conversation_threshold()
        if not in_conversation and current_score < min_threshold:
            self.plugin._emit_log(
                "INFO",
                f"[Gate] 群{normalized_group_id} 注意力过低({current_score:.1f}<{min_threshold:.1f})，这一轮不搭话",
            )
            return GateDecision("ignore", reason=f"low_attention({current_score:.1f})")

        # 6. 「回复过于频繁 → 强制静默」这道硬闸**已删除**（2026-09-27 使用者口径：
        #    「不要这个，有注意力控制频率了」）。原来的位置在这里，判据是"窗口内她已发
        #    够 N 条"，@ / 引用 / 关键词可绕过 —— 详见本文件 __init__ 末尾的墓碑注释。
        #    现在频率由两处软机制管：注意力（本群分数 + 消耗 + 频率增速缩放）
        #    与 `attention_service.pacing_hint()`（说得偏密时提醒她自己收敛）。

        # 6.5 「这句到底该不该接」——只作用于 trusted 群。
        #     normal 群不在这里拦：它们本来就不回复，只按概率转发给主人；
        #     若在这里返回 ignore，转发也会被 dispatcher 一起跳过（那是功能回退）。
        group_level = ""
        permission_mgr = getattr(self.plugin, "group_permission_mgr", None)
        if permission_mgr:
            group_level = str(permission_mgr.get_group_level(normalized_group_id) or "")
        if group_level == "trusted":
            now_ts = float(timestamp or attention._current_time())
            # 先看空闲退避：连续「决定不接」之后她会主动从这个群退开一段时间
            # （指数放长到 300s）。积压到 BYPASS_PENDING 条则绕过退避重新评估——
            # 否则群聊热起来她会因为退避而错过。@ / 引用她已在上面短路，不受退避影响。
            pending_now = self._speech.pending_count(normalized_group_id, now=now_ts)
            delay = self._backoff.delay_seconds(normalized_group_id, now=now_ts, pending_count=pending_now)
            if delay > 0:
                self._log_decision(
                    f"[Necessity] 群{normalized_group_id} 空闲退避中（剩余 {delay:.0f}s，积压 {pending_now}）"
                )
                return GateDecision("ignore", reason=f"necessity_backoff({delay:.0f}s)")
            verdict = self._evaluate_necessity(
                group_id=normalized_group_id,
                sender_id=sender_id,
                message_text=message_text,
                is_reply_to_bot=is_reply_to_bot,
                now=now_ts,
                addressee=addressee,
                in_conversation=in_conversation,
            )
            if verdict.decision != "trigger":
                backoff = self._backoff.record_wait(normalized_group_id, now=now_ts)
                self._log_decision(
                    f"[Necessity] 群{normalized_group_id} 本轮不接（score={verdict.score} < 阈值，"
                    f"依据={verdict.breakdown.reasons}，退避 {backoff:.0f}s）"
                )
                return GateDecision("ignore", reason=verdict.reason)
            self._backoff.reset(normalized_group_id)
            self._log_decision(
                f"[Necessity] 群{normalized_group_id} 接（score={verdict.score} ≥ 阈值，依据={verdict.breakdown.reasons}）"
            )

        # 7. 本群在聊的普通消息 → LLM 自行判断是否回复
        self._mark_active(normalized_group_id)
        self.plugin._emit_log(
            "INFO", f"[Attention] 群 {normalized_group_id} 在聊（本群分数够），消息交给 LLM 自行判断"
        )
        return GateDecision("reply", reason="in_conversation")

    # ==========================================
    # 回复后消耗 + 焦点切换检测
    # ==========================================

    async def on_reply_sent(self, group_id: str, *, user_id: str = "") -> None:
        """回复已发送 → 消耗注意力 + 记录活跃（频率交给软提示，见 pacing_hint）。

        ``user_id`` = **这条回复是对谁说的**（触发这一轮的那条消息的发送者）。
        它喂给「正在和她对话的人」：他找过她、她也回过他，才算一来一回。
        """
        attention = self.plugin.attention_service
        if attention:
            now = attention._current_time()
            await attention.update_on_reply(group_id)
            if user_id:
                self._dialogue.note_reply(group_id, user_id, now=now)
        else:
            now = int(__import__("time").time())
        self._mark_active(group_id)
        # 她说话了 → 存进「近期发言窗口」（存在感惩罚的来源），并清掉空闲退避
        self._speech.record_self(group_id, now=float(now))
        self._backoff.reset(group_id)

    def _note_in_conversation(self, group_id: str) -> bool:
        """记下"这个群在聊"，返回**这一下是不是从凉转热**（跨过保持线的那一步）。

        原来"回头看这个群错过的消息"挂在**跨群焦点切换**上（`check_focus_shift`）：
        焦点从别的群切到它 → 做一次回溯补回。跨群取舍删掉后没有切换事件，取而代之的是
        **每群自己的状态迁移**：凉了很久的群重新热起来 = 值得回头看看这段时间群里聊了什么。

        只看**本群**上一次的判定，与其它群无关 —— 这正是"每个群自己管自己的注意力"。
        """
        key = str(group_id or "").strip()
        if not key:
            return False
        attention = self.plugin.attention_service
        if not attention:
            return False
        try:
            now_in_conversation = bool(attention.is_in_conversation(key))
        except Exception:
            return False
        was = self._in_conversation.get(key)
        self._in_conversation[key] = now_in_conversation
        return bool(was is False and now_in_conversation)

    def _schedule_retro_review(self, group_id: str) -> None:
        """给刚热起来的群排一次回溯补回（异步，不阻塞这条消息）。

        用后台任务而不是 await：补回要走一遍完整的回复链路（LLM + 发送），
        让当前这条消息等它毫无道理 —— 当前消息自己已经在下游处理了。
        """
        gate = self

        async def _run_retro() -> None:
            try:
                await gate.run_retroactive_review(group_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                gate._logger.warning(f"[RetroReview] 回溯补回失败: {e}")

        try:
            task = asyncio.create_task(_run_retro())
        except RuntimeError:   # 没有运行中的事件循环（同步调用方/收尾阶段）
            return
        self._retro_tasks.add(task)

        def _on_done(done_task: asyncio.Task) -> None:
            self._retro_tasks.discard(done_task)
            if done_task.cancelled():
                return
            exc = done_task.exception()
            if exc is not None:
                self._logger.warning(f"[RetroReview] 回溯补回任务失败: {exc}")

        task.add_done_callback(_on_done)

    # ==========================================
    # 回溯补回流程
    # ==========================================

    async def run_retroactive_review(self, group_id: str) -> list[str]:
        """这个群**刚热起来**时，把它没看过的消息补回来。

        原来是"焦点切换到 group_id 后"由 dispatcher 调；跨群取舍删掉后触发点换成
        `_note_in_conversation` 检测到的**本群凉转热**（见 `_mark_active`）。
        """
        async with self._retroactive_lock:
            return await self._run_retroactive_review_locked(group_id)

    def _retro_min_unreviewed(self) -> int:
        """至少攒够多少条没看过的消息才值得回头补一次（默认 5，0 = 不设门槛）。

        为什么要有这道门槛：跨群取舍删掉后，"凉转热"比"焦点切换"频繁得多，而真去补一次
        要花一次完整的 LLM 调用。只攒了一两条（她本来就打算答当前这条）时补回毫无意义，
        放着让当前这条回复覆盖即可。
        """
        raw = (self.plugin._qq_settings or {}).get("retroactive_review_min_unreviewed", 5)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 5

    def _retro_cooldown_seconds(self) -> int:
        """同一个群两次回溯补回之间至少隔多久（默认 300 秒，0 = 不限）。"""
        raw = (self.plugin._qq_settings or {}).get("retroactive_review_cooldown_seconds", 300)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 300

    async def _run_retroactive_review_locked(self, group_id: str) -> list[str]:
        attention = self.plugin.attention_service
        if not attention:
            return []

        now = int(attention._current_time())
        cooldown = self._retro_cooldown_seconds()
        last_at = int(self._last_retro_at.get(group_id, 0))
        if cooldown > 0 and now - last_at < cooldown:
            self._logger.info(
                f"[RetroReview] 群 {group_id} 距上次回溯仅 {now - last_at}s（冷却 {cooldown}s），跳过"
            )
            return []

        # 1. 从统一 backlog_store 取出上次她认真看这个群以来的未审核消息
        since = attention.get_last_focus_at(group_id)
        if not hasattr(self.plugin, "backlog_store") or not self.plugin.backlog_store:
            self._logger.warning("[RetroReview] backlog_store 不可用，跳过回溯")
            return []
        max_messages = int((self.plugin._qq_settings or {}).get("retroactive_review_max_messages", 30) or 30)
        # 这个键此前是**死键**：默认值/保存/校验/界面全都有，提示词里却写死了"1-2 条"。
        max_reply = max(1, int((self.plugin._qq_settings or {}).get("retroactive_review_max_reply", 5) or 5))
        unreviewed = await self.plugin.backlog_store.get_unreviewed_messages_since(group_id, since_timestamp=since, limit=max_messages)
        min_unreviewed = self._retro_min_unreviewed()
        if not unreviewed or len(unreviewed) < min_unreviewed:
            self._logger.info(
                f"[RetroReview] 群 {group_id} 未审核消息 {len(unreviewed)} 条"
                f"（门槛 {min_unreviewed}），不值得补回，跳过"
            )
            try:
                await self.plugin.backlog_service.mark_group_reviewed_payload(group_id)
            except Exception:
                pass
            return []

        # 有未审消息 → 记下这次补回时刻（同群的冷却从这里算）
        self._last_retro_at[group_id] = now
        self._logger.info(f"[RetroReview] 群 {group_id} 有 {len(unreviewed)} 条未审核消息，开始回溯")
        # 本次**真正喂给模型**的消息就是"已审"的边界。标记必须收窄到这个集合，
        # 不能整群全标：模型只看得到最新 max_messages 条，超窗的旧消息若被一起
        # 标成已审，就永远不会被补回——用户既没看到、也再没有机会看到。
        reviewed_ids = {
            str(item.get("message_id") or "").strip()
            for item in unreviewed
            if str(item.get("message_id") or "").strip()
        }

        # 2. 复用缓冲链路：构造总结 prompt，让猫娘挑最多 max_reply 条用 <reply> 回应
        summary = self._build_ignored_summary(unreviewed)
        try:
            from .pipeline_models import QQReplyRequest
            request = QQReplyRequest(
                message_text=(
                    f"[系统] 你刚才没有太关注这个群，以下是这段时间群友们聊天的消息摘要。\n"
                    f"每条消息末尾都标了它的消息ID（形如 id=xxx）。请针对其中最多 {max_reply} 条你最感兴趣的，"
                    f"用 `<reply>消息ID</reply>` 引用后自然回应。不要逐条点评。\n\n"
                    f"摘要：\n{summary}"
                ),
                sender_id=self.plugin._admin_qq or "0",
                is_group=True,
                group_id=group_id,
                is_at_bot=True,
                source_kind="retroactive_review",
                group_scene_mode="group_collective",
                fallback_to_text_on_voice_failure=True,
                use_memory_context=False,
                ephemeral_session=False,
            )
            async def _run_retro():
                svc = self.plugin.session_memory_service
                before = svc.session_history_len(f"group:{group_id}")
                try:
                    return await self.plugin.reply_pipeline.run(request)
                finally:
                    svc.record_synthetic_prompt_rows(f"group:{group_id}", before)
            outcome = await self.plugin._run_with_session_lock(
                f"group:{group_id}", _run_retro,
            )
            self.plugin.runtime_service.record_pipeline_outcome(
                source=request.source_kind, request=request, outcome=outcome,
            )
            if outcome.action == "reply" and outcome.reply_text:
                self._logger.info(f"[RetroReview] 回溯回复已发送: {outcome.reply_text[:50]}...")
            else:
                self._logger.info("[RetroReview] LLM 决定不回复回溯摘要")
        except Exception as e:
            self._logger.warning(f"[RetroReview] 回溯总结失败: {e}")

        # 3. 标记已读（只标本次消费掉的那批，见上）
        attention.mark_focus(group_id)
        try:
            await self.plugin.backlog_service.mark_group_reviewed_payload(
                group_id, message_ids=reviewed_ids,
            )
            self._logger.info(f"[RetroReview] 群 {group_id} 已标记 {len(reviewed_ids)} 条为已审阅")
        except Exception as e:
            self._logger.warning(f"[RetroReview] 标记已审阅失败: {e}")
        return []

    # ==========================================
    # 回溯辅助方法
    # ==========================================

    @staticmethod
    def _build_ignored_summary(messages: list[dict[str, Any]]) -> str:
        """把被忽略的消息列表生成 LLM 可读的摘要，每条消息后携带 (id=消息ID) 供 <reply> 引用。"""
        lines: list[str] = []
        for i, msg in enumerate(messages, 1):
            # 发言人显示名收口进 `backlog_sender_label`：backlog 落盘的键是
            # `sender_name`，这里原先硬编码 `sender_nickname` —— 对 backlog 记录
            # 永远取不到，于是**回溯补回的摘要里显示的全是 QQ 号**而不是昵称。
            nickname = backlog_sender_label(msg, default="未知")
            # backlog 存储项来自 QQBacklogMessage.to_dict()，内容键是 text；
            # message_text 是旧键名（无历史数据），保留作兜底。
            text = str(msg.get("text") or msg.get("message_text") or "").strip()
            if len(text) > 100:
                text = text[:97] + "..."
            msg_id = str(msg.get("message_id") or "").strip()
            lines.append(f"[{i}] {nickname}: {text} (id={msg_id})" if msg_id else f"[{i}] {nickname}: {text}")
        return "\n".join(lines)

    async def _push_group_digest(self, group_id: str) -> None:
        """把这个群的**会话增量**推送到 Memory Server（按群，幂等）。

        触发点原来是"焦点离开这个群"（`check_focus_shift`）；跨群取舍删掉后改为
        `_maybe_push_digest` 按**每群自己的**间隔调用。推送本身由游标
        `last_group_digest_index` 精确记录推到哪里，重复调用不会重发。
        """
        try:
            if not bool((getattr(self.plugin, "_qq_settings", {}) or {}).get(
                "group_memory_enabled", False,
            )):
                return
            session_key = f"group:{group_id}"
            sessions = getattr(self.plugin, "_user_sessions", {}) or {}
            s = sessions.get(session_key)
            if not isinstance(s, dict):
                return
            async def _push_delta() -> int:
                # 锁内复检：外层 setting 检查通过后可能排队等锁，期间用户
                # 关掉群记忆——opt-out 之后不得再推送 digest。
                if not bool((getattr(self.plugin, "_qq_settings", {}) or {}).get(
                    "group_memory_enabled", False,
                )):
                    return 0
                if (getattr(self.plugin, "_user_sessions", {}) or {}).get(
                    session_key
                ) is not s:
                    # 等锁期间 finalizer/discard 可能已结算并弹出会话：
                    # 陈旧引用继续推会重发已结算历史、推进无主游标。
                    return 0
                if s.get("pending_disable_settle"):
                    # opt-out 结算未完成（快速 re-enable 会让上面的 setting
                    # 检查重新通过）：digest 不碰，交转变任务按 cutoff 结算。
                    return 0
                if s.get("pending_enable_rebase") is not None:
                    # retain 结算后、ON rebase 前的 limbo：游标还停在
                    # opt-out 区间之前，此处推送只剩 nonconsent floor 一道
                    # 防线兜着未授权行。交 rebase 任务先把游标规整过界。
                    return 0
                session = s.get("session")
                if not session or not hasattr(session, "_conversation_history"):
                    return 0
                history = getattr(session, "_conversation_history", []) or []
                if len(history) < 4:
                    return 0
                # 先旧后新分批 + 精确游标（对偶 finalize 的同名修复）：旧写法
                # `[-200:]` 会把超窗中段永久跳过、游标却跳到 len(history)，
                # 之后 finalize 也无从补救。失败即停，游标停在最后一个成功
                # 批，剩余留给下一次 digest/finalize。
                svc = self.plugin.session_memory_service
                start_index = max(0, int(s.get("last_group_digest_index", 0)))
                start_index = max(
                    start_index,
                    int(s.get("nonconsent_history_end", 0) or 0),
                )
                if start_index > len(history):
                    # 历史被重复守卫重置/收缩：钳游标，防新增轮次被
                    # 永久跳过（对偶 finalize 的同名钳制）。
                    start_index = len(history)
                    s["last_group_digest_index"] = start_index
                total_sent = 0
                # 限批：focus-shift 推送持有会话锁，慢 memory server 下
                # 无界批次会让 3 个排水群占满全局 Semaphore(3) 冻结全部
                # 消息处理。每次最多 3 批，游标精确，剩余留给下一次
                # digest / finalize（结算 of record 在 finalize，不丢）。
                remaining_batches = 3
                while remaining_batches > 0:
                    remaining_batches -= 1
                    messages, next_index = svc._slice_group_history_batch(
                        history, start_index, svc.GROUP_HISTORY_MAX_MESSAGES,
                        user_data=s, stop_at_provisional=True,
                    )
                    if not messages:
                        if next_index > start_index:
                            s["last_group_digest_index"] = next_index
                        break
                    # 拿不到群名就不带参（对偶 finalize 的 digest 调用）。
                    digest_extra = {}
                    group_display_name = svc._group_display_name(group_id)
                    if group_display_name:
                        digest_extra["display_name"] = group_display_name
                    await self.plugin.memory_bridge.post_scoped_memory_history(
                        str(s.get("her_name") or "neko"),
                        messages,
                        subject=self.plugin.memory_bridge.group_subject(group_id),
                        timeout=30.0,
                        **digest_extra,
                    )
                    s["last_group_digest_index"] = next_index
                    start_index = next_index
                    total_sent += len(messages)
                return total_sent

            sent_messages = await self.plugin._run_with_session_lock(session_key, _push_delta)
            if sent_messages:
                self._logger.info(f"[Digest] 群 {group_id} 已推送摘要到 Memory Server ({sent_messages}条)")
        except Exception as e:
            self._logger.warning(f"[Digest] 推送失败: {e}")

    # ==========================================
    # 公共查询
    # ==========================================

    def is_global_sleep(self) -> bool:
        attention = self.plugin.attention_service
        if not attention:
            return False
        return attention.is_global_sleep()

    def get_focus_group(self) -> str | None:
        attention = self.plugin.attention_service
        if not attention:
            return None
        return attention.get_focus_group()

    # ==========================================
    # 生命周期
    # ==========================================

    async def shutdown(self) -> None:
        await self.stop_proactive_loop()
        retro_tasks = list(self._retro_tasks)
        for task in retro_tasks:
            if not task.done():
                task.cancel()
        if retro_tasks:
            await asyncio.gather(*retro_tasks, return_exceptions=True)
        self._retro_tasks.clear()
        self._last_digest_at.clear()
        self._last_retro_at.clear()
        self._in_conversation.clear()
        if self.plugin.attention_service:
            await self.plugin.attention_service.stop_decay_loop()
        self._logger.info("[AttentionGate] 已关闭")
