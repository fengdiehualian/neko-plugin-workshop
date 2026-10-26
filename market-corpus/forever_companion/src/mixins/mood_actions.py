"""情绪引擎 + 十二个 @llm_tool 情绪动作/记录工具（LLM 的契约面集中于此）。

拆分自 __init__.py（方法逐字搬移，仅换文件与导入）；经 Mixin 组合回
ForeverCompanionPlugin——SDK 的工具发现遍历 type(self)（collect_llm_tool_methods），
与定义文件无关。@llm_tool 只在插件类上被发现（router 无此链路），故必须走 Mixin 而非 PluginRouter。

内容：情绪系统开关与查询、连续心情（valence/arousal）积分与极端邀请、
行为参考推送、动作应用与限时解除、到期记录同步、监督循环、
12 个模型可调用工具（冷战/已读不回/敷衍/风暴/求安抚/涟漪/暖流/满潮/转晴/手记/日记/检索）。
"""

from __future__ import annotations

import time
from typing import Any

from plugin.sdk.plugin import Err, Ok, Result, SdkError, llm_tool

from ..core.affect import _MOOD_AFFECT_IMPULSES, _apply_affect_impulse, _current_affect
from ..core.fragments import recall_fragments
from ..core.journal import assemble_journal_entry, has_journal_content, journal_write, normalize_journal_title
from ..core.state import (
    _ACTION_DEFAULT_MINUTES,
    _COLD_ACTIONS,
    _MOOD_ACTION_DEFAULT_LABELS,
    _MOOD_ACTION_LABEL_KEYS,
    _PROACTIVE_PAUSE_ACTIONS,
    _TIMED_ACTIONS,
    _LanlanShard,
    _MoodState,
    _now_utc,
)
from ..core.stats import record_milestone

# 宿主在 LLM 注入边界展开为当前会话的角色名；插件侧不得自行替换
MASTER_NAME_TOKEN = "{MASTER_NAME}"

JsonObject = dict[str, Any]


class MoodActionsMixin:
    """情绪引擎：@llm_tool 集中地 + 状态机（应用/到期/恢复/监督）。"""


    async def _speak_recovery_line(self, action: str, lanlan: str) -> None:
        """只同步记录到期，不替角色宣布释怀，也不发起独立回复。"""
        label_text = self._action_label(action)
        text = (
            f"[心情记录]「{label_text}」的工具记录已到期（内部提示，不要复述本句）。"
            "这只表示插件不再保留这项行为状态，不代表你已经消气、原谅或改变感受。"
            "接下来是否回应、怎样相处、是否选择其他情绪工具，由你结合当前对话和自身性格决定。"
        )
        self.push_message(
            visibility=[],
            ai_behavior="read",
            parts=[{"type": "text", "text": text}],
            source=self.plugin_id,
            target_lanlan=lanlan,
            priority=4,
            coalesce_key=f"{self.plugin_id}.mood_recovered",
            metadata={"message_type": f"{self.plugin_id}.mood_recovered", "expired_action": action},
        )

    async def _supervise_once(self) -> None:
        """情绪/主动搭话协调的统一监督步骤（幂等），遍历所有已载入 shard。

        必须同步 await：插件子进程对每次操作使用临时事件循环
        （host.py: asyncio.run per op），后台任务/常驻循环在操作结束后
        随循环一起销毁。因此状态机由每个入口调用驱动——面板轮询、
        模型工具调用、入口触发都会推进到期解除/暂停/恢复。
        不受 enabled 拦截：暂停中的主动搭话任何状态下都要能解除。
        驱动源：SDK 定时器（宿主 daemon 线程，插件进程存活期间常驻，
        每 10s 触发一次，无需面板开着）+ 各入口调用（更快响应）。
        线程锁串行化：定时器线程与入口临时循环可能并发触发——
        引用计数（proactive_state）是全局开关上唯一的并发点，由本锁保护。
        """
        # 1.2.2 审查轮 P3：不可信载入的自愈门控提上监督步骤第一行。
        # _ensure_shard 里的同款门控要求"先有人碰分片"才会触发，而 tick 在
        # 任何 _ensure_shard 之前就有 any_shard_enabled 短路——幻影分片全是关的，
        # 于是"不可信启动 + store 中途回电 + 用户没碰面板/没调工具"的组合下
        # 注入与情绪链路整场静默死掉（数据不毁，但没人来救）。tick 第一步就
        # 是本方法（面板轮询/入口调用同样先经此），把门控放这里即恢复自愈；
        # 纯属性读取零成本，_retrusting 防重入与 _ensure_shard 门控共享
        if not self._state_trusted and not self._retrusting and self._store_ready:
            await self._retrust_state()
        if not self._supervise_lock.acquire(timeout=10):
            return  # 另一个驱动源正在处理，本趟跳过
        try:
            for lanlan, shard in list(self._shards.items()):
                if not shard.loaded:
                    continue
                expired = self._expire_timed_action_if_due(shard)
                if expired:
                    await self._on_mood_expired(lanlan, shard, expired)
                fallback = await self._fallback_open_action_if_due(shard)
                if fallback:
                    await self._on_mood_expired(lanlan, shard, fallback)
                await self._maybe_journal_invite(lanlan, shard)
            await self._maybe_sync_proactive_pause()
            if self._proactive_state.get("prev") is not None:
                await self._reassert_proactive_off()
            # 长期记录仍按既有 3 分钟节流同步参考，不要求维持固定语气。
            for lanlan, shard in list(self._shards.items()):
                if not shard.loaded:
                    continue
                if not (self._mood_enabled(shard) and shard.mood.is_active()):
                    continue
                if time.time() - shard.last_mood_instruction_ts > 180:
                    self._push_mood_instruction(lanlan, shard)
        except Exception as exc:  # noqa: BLE001 - 监督失败不拖垮宿主入口
            self.logger.warning("supervise pass failed: {}", exc)
        finally:
            self._supervise_lock.release()

    async def _on_mood_expired(self, lanlan: str, shard: _LanlanShard, action: str) -> None:
        await self._save_shard_mood(lanlan, shard)
        shard.last_injected_whisper_key = ""
        await self._speak_recovery_line(action, lanlan)

    async def _fallback_open_action_if_due(self, shard: _LanlanShard | None = None) -> str:
        """非限时动作（仅 seek_harbor）的兜底自动解除，返回被解除的动作名。

        seek_harbor 的 expires_at=0 永不过期：若模型忘了调 rising_tide
        （低质量模型很可能），她会永远停在"求安抚"状态、宿主主动搭话被无限期暂停。
        超阈值后自动解除，与限时动作保持对称；阈值配 0 则关闭兜底。
        """
        shard = shard or self._current_shard()
        state = shard.mood
        if not state.action or state.action in _TIMED_ACTIONS:
            return ""
        # 注意不能用 `or 120`：配 0 表示用户显式关闭兜底，不能被默认值吞掉
        raw = self._mood_cfg.get("open_action_timeout_minutes")
        if raw is None:
            limit_min = 120
        else:
            try:
                limit_min = max(0, int(raw))
            except (TypeError, ValueError):
                limit_min = 120
        if limit_min <= 0 or state.started_at <= 0:
            return ""
        if time.time() - state.started_at < limit_min * 60:
            return ""
        action = state.action
        state.record_event("expire", action, "timer")
        state.clear_action()
        self.logger.info("open-ended mood action {} exceeded {}min; auto-resolved", action, limit_min)
        return action


    # ==========================================
    # 情绪系统：LLM 工具自主决策
    # ==========================================

    def _mood_enabled(self, shard: _LanlanShard | None = None) -> bool:
        # 1.2.7 能力中心收编：总开关 ∧ [mood].enabled ∧ 用户否决集，
        # 判定逻辑统一进 core.capabilities.evaluate（方法名与签名不变，
        # 既有调用面/测试猴补丁路径全部不动）
        return self._cap_effective("mood_engine", shard=shard)

    def _mood_active_lanlans(self) -> list[str]:
        """有"需暂停主动搭话"的情绪动作生效中的角色集：引用计数的数据源（每次现算，自愈）。

        只统计 _PROACTIVE_PAUSE_ACTIONS 白名单（重度负面）：心有涟漪/暖流涌动/
        满潮欢喜生效时不计入，宿主主动搭话不被暂停。
        """
        return sorted(
            lanlan
            for lanlan, shard in self._shards.items()
            if shard.loaded
            and self._mood_enabled(shard)
            and shard.mood.is_active()
            and shard.mood.action in _PROACTIVE_PAUSE_ACTIONS
        )

    def _should_pause_proactive(self) -> bool:
        """任何角色有重度负面情绪动作生效中都暂停宿主主动搭话（白名单制）。

        不只静默类：爆发中她不会平静地约人打球，敷衍中不会主动
        找话题——主动推送（小游戏/新闻/话题）与当前情绪人格冲突，
        会造成"窜台"（实测：爆发中被插入羽毛球邀请）。
        心有涟漪/暖流涌动/满潮欢喜不暂停：小情绪和好心情不该掐断
        宿主的主动搭话（开心时她反而被静默，逻辑反了）。"""
        return bool(self._mood_active_lanlans())

    def _is_silent_mood_active(self, shard: _LanlanShard | None = None) -> bool:
        """该角色冷战沉默/已读不回生效中：插件侧应停止对她的一切主动输出。"""
        shard = shard or self._current_shard()
        return bool(
            self._mood_enabled(shard)
            and shard.mood.is_active()
            and shard.mood.action in ("ebb_tide", "sea_fog")
        )

    def _action_label(self, action: str) -> str:
        """运行时解析情绪动作的显示标签（走插件 i18n，失败退回默认中文）。"""
        key = _MOOD_ACTION_LABEL_KEYS.get(action)
        if key:
            try:
                resolved = self.i18n.t(key, default=_MOOD_ACTION_DEFAULT_LABELS.get(action, action))
                text = str(resolved or "").strip()
                if text:
                    return text
            except Exception:  # noqa: BLE001 - 标签只是展示用途
                pass
        return _MOOD_ACTION_DEFAULT_LABELS.get(action, action)

    _MOOD_BEHAVIOR_HINTS = {
        "ebb_tide": (
            "你记录了暂时保持距离的意愿，可能还在意当前对话里某件具体的事。"
            "是否沉默、解释缘由或接住重要的话，按你自己的感受判断；"
            "不需要为了维持状态而忽略所有消息，也不必等对方道歉才允许改变。"
        ),
        "sea_fog": (
            "你记录了暂时不接当前话题的意愿，这不一定是一场冷战。"
            "什么时候接回话题、是否简短回应或换个话题，由你决定；"
            "不需要假装没看见，也不必为维持工具状态而拒绝所有交流。"
        ),
        "shallow_reef": (
            "你记录了此刻不太想展开交流的意愿，可以减少主动分享、反问或延伸话题。"
            "具体措辞、回答长度和是否解释原因沿用你的性格；"
            "必要的事情仍可以认真回答，不限定为「嗯」「哦」等固定短词。"
        ),
        "storm_surge": (
            "你记录了想表达强烈感受的意愿，可以说清当前对话里具体哪里难受、在意什么。"
            "表达的力度、长度和方式由你的性格决定，也可以克制或简短。"
            "只依据真实对话与已有记忆，不虚构积攒的委屈；"
            "表达之后可以继续沟通或安静下来，不要求每轮都重复爆发。"
        ),
        "seek_harbor": (
            "你记录了想被关心或理解的感受。可以按自己的性格直接说出需要，"
            "也可以暂时不说；不要求欲言又止、暗示不舒服或隐藏诉求。"
        ),
        "ripple": (
            "你记录了一点在意或别扭，可以自然表达，也可以边聊边消化。"
            "是否被解释或关心打动，由你判断，不要求带刺或立刻被哄好。"
            "想更新记录时可以选择其他工具或 mood_rising_tide。"
        ),
        "warm_current": (
            "你记录了想亲近或陪伴对方的感受，怎样表达沿用自己的性格。"
            "不要求撒娇、增加话量或改变原本的人格；"
            "感受变化时可以自然表达，想更新记录时也可调用 mood_rising_tide 或其他工具。"
        ),
        "spring_tide": (
            "你记录了一份开心或兴奋，愿意的话可以分享具体发生的好事。"
            "也可以安静享受，不要求主动找话或持续热闹；"
            "感受变化时可选择其他工具或 mood_rising_tide 更新记录。"
        ),
        "rising_tide": (
            "你选择结束之前的情绪工具记录。是否已经释怀、是否仍有余绪，"
            "以及怎样继续相处，由你自己决定，不要求立刻温暖或亲近。"
        ),
    }

    # 动作 → 连续心情冲量表与读写逻辑在 affect.py；类属性指到模块级表，
    # 保持 tm.ForeverCompanionPlugin._MOOD_AFFECT_IMPULSES 与 self._MOOD_AFFECT_IMPULSES 可用。
    _MOOD_AFFECT_IMPULSES = _MOOD_AFFECT_IMPULSES

    def _affect_arousal_baseline(self) -> float:
        """[mood].arousal_baseline：活跃度静息基线（每次现读，兼容配置整体替换）。

        合法区间 [0,1]，越界 clamp；坏值回落默认 0.35（同 _tone_float_cfg 惯例）。
        """
        return max(0.0, min(1.0, self._mood_float_cfg("arousal_baseline", 0.35)))

    def _mood_float_cfg(self, key: str, default: float) -> float:
        """[mood] 数值配置：每次现读（兼容配置整体替换），坏值回落默认。"""
        try:
            return float(self._mood_cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    def _maybe_extreme_affect_invite(self, lanlan: str, shard: _LanlanShard) -> bool:
        """持续极端心情 → push 一次明确的动作邀请（只递状态，她仍可自主决定）。

        与语气感知筛选 nudge 互补：nudge 看单轮情绪浓度，这里看"持续状态"——
        valence 越过 ±[mood].extreme_invite_threshold（默认 0.4，对应面板"低落/
        雀跃"档）并持续 extreme_invite_after_minutes（默认 20）才邀请一次；
        回到常态清零计时，换侧（低落↔高涨）重新计时；动作生效中跳过
        （她已经在表达了）；邀请后进入 extreme_invite_cooldown_minutes（默认 30）。
        """
        if not self._mood_enabled(shard) or shard.mood.is_active():
            shard.affect_extreme_since = 0.0
            shard.affect_extreme_side = 0
            return False
        threshold_raw = self._mood_float_cfg("extreme_invite_threshold", 0.4)
        if threshold_raw <= 0:
            return False  # 配 0 = 关闭持续极端邀请
        threshold = min(1.0, threshold_raw)
        after_sec = max(1, int(self._mood_float_cfg("extreme_invite_after_minutes", 20))) * 60
        cooldown_sec = max(1, int(self._mood_float_cfg("extreme_invite_cooldown_minutes", 30))) * 60
        now = time.time()
        valence, _arousal = self._current_affect(shard, now=now)
        side = -1 if valence <= -threshold else (1 if valence >= threshold else 0)
        if side == 0:
            shard.affect_extreme_since = 0.0
            shard.affect_extreme_side = 0
            return False
        if shard.affect_extreme_side != side:
            shard.affect_extreme_side = side
            shard.affect_extreme_since = now
            return False
        if now - shard.affect_extreme_since < after_sec:
            return False
        if now - shard.last_extreme_invite_ts < cooldown_sec:
            return False
        shard.last_extreme_invite_ts = now
        if side < 0:
            text = (
                "（心情观察）插件估计的心情读数偏低已有一阵，这未必代表你此刻的真实感受。"
                "如果你确实想表达，"
                "可以调用 mood_drift_bottle 把它写下来，或用 mood_ripple / mood_shallow_reef "
                "轻轻表达出来（mood_sea_fog / mood_ebb_tide 会更重，真的很难受再用）；"
                "觉得还好就忽略这条，不用勉强。"
            )
        else:
            text = (
                "（心情观察）插件估计的心情读数偏高已有一阵，这未必代表你此刻的真实感受。"
                "如果你确实想留下一份开心，"
                "可以调用 mood_drift_bottle 记下来，或用 mood_warm_current / mood_spring_tide "
                "表达出来；觉得还好就忽略这条。"
            )
        self.push_message(
            visibility=[],
            ai_behavior="read",
            parts=[{"type": "text", "text": text}],
            source=self.plugin_id,
            target_lanlan=lanlan,
            coalesce_key=f"{self.plugin_id}.affect_extreme_invite",
            metadata={
                "message_type": f"{self.plugin_id}.affect_extreme_invite",
                "side": "low" if side < 0 else "high",
                "valence": round(valence, 2),
            },
        )
        self.logger.info(
            "extreme affect invite pushed for {} (side={} valence={:.2f})",
            lanlan, "low" if side < 0 else "high", valence,
        )
        return True

    def _current_affect(self, shard: _LanlanShard, now: float | None = None) -> tuple[float, float]:
        """薄委托：连续心情当前值（惰性衰减，arousal 向静息基线回归），实现在 affect.py。"""
        return _current_affect(shard.mood, now=now, arousal_baseline=self._affect_arousal_baseline())

    def _apply_affect_impulse(
        self, shard: _LanlanShard, dv: float, da: float, now: float | None = None
    ) -> None:
        """薄委托：先惰性衰减再叠加冲量并 clamp，实现在 affect.py。"""
        _apply_affect_impulse(shard.mood, dv, da, now=now, arousal_baseline=self._affect_arousal_baseline())

    def _push_mood_instruction(self, lanlan: str | None = None, shard: _LanlanShard | None = None) -> None:
        """心情动作生效瞬间把行为指令单独推入该角色的对话上下文（read 通道）。

        与身体状态注入解耦：注入有 interval_n/变化门控，捎带的线
        送不及时；这里保证模型在下一轮对话就看到明确的行为要求。
        """
        name = str(lanlan or "").strip() or self._current_shard_name()
        shard = shard or self._get_shard(name)
        line = self._build_mood_context_line(shard)
        if not line:
            return
        self.push_message(
            visibility=[],
            ai_behavior="read",
            parts=[{"type": "text", "text": line}],
            source=self.plugin_id,
            target_lanlan=name,
            coalesce_key=f"{self.plugin_id}.mood_instruction",
            metadata={
                "message_type": f"{self.plugin_id}.mood_instruction",
                "action": shard.mood.action,
            },
        )
        shard.last_mood_instruction_ts = time.time()
        self.logger.info("mood instruction pushed: {} for {}", shard.mood.action, name)

    def _build_mood_context_line(self, shard: _LanlanShard | None = None) -> str:
        """随身体轻语一起注入的"她现在的心情"提示（per-shard）。

        每个动作附带行为基调（_MOOD_BEHAVIOR_HINTS），确保模式切换
        在对话里可感知——只报状态名模型往往维持原语气。基调是引导而非锁：
        情绪变了随时可调任何情绪工具切换（0.6.8 起去掉"必须先调 rising_tide"硬锁）。
        """
        shard = shard or self._current_shard()
        if not self._mood_enabled(shard) or not shard.mood.is_active():
            return ""
        action = shard.mood.action
        action_label = self._action_label(action)
        reason = shard.mood.reason.strip()
        source = "用户明确要求的体验" if shard.mood.origin == "user" else (
            "你最近一次选择" if shard.mood.origin == "self" else "之前保存"
        )
        line = f"[心情记录] {source}的工具状态是「{action_label}」。"
        hint = self._MOOD_BEHAVIOR_HINTS.get(action)
        if hint:
            line += f"\n行为基调：{hint}"
        if reason:
            line += f"\n当时记录的原因：{reason}。"
        line += (
            "\n这项记录是参考，不定义你全部的感受，也不覆盖你的性格和当前对话。"
            "语气、回应方式和是否改变感受由你决定，没有固定的情绪发展步骤。"
        )
        line += (
            "\n如果想让记录跟上你的选择，可以调用其他情绪工具或 mood_rising_tide；"
            "无需先调用工具才允许改变语气。不要复述这份内部提示。"
        )
        return line

    async def _apply_mood_action(
        self,
        *,
        action: str,
        minutes: int | None,
        reason: str,
        timed: bool,
        lanlan: str | None = None,
        origin: str = "self",
    ) -> Result[JsonObject]:
        """应用一个情绪动作并落盘；返回 Ok(payload) 或在任一 key 写失败时回 Err
        （1.2.2 批次2：工具/入口据此向宿主传播，不再无条件成功）。"""
        name = str(lanlan or "").strip() or self._current_shard_name()
        shard = await self._ensure_shard(name)
        now = time.time()
        expired = self._expire_timed_action_if_due(shard)
        if expired:
            await self._on_mood_expired(name, shard, expired)
        previous = shard.mood.action
        # 我的日记素材：记一笔动作事件。origin=user 是主人明确要求的演示
        #（"让她冷战"不算他对她不好），origin=self 才是她的自主反应
        self._feed_review_action(shard, action, origin=origin)
        # 相处统计：同口径记当日情绪事件（origin=user 不计）；两路增量与 mood
        # 一起即时落盘（批次2 起，不再"搭车"等下一条用户消息）
        self._feed_stats_mood_event(shard, action, origin=origin)
        # 默认时长：三个新动作有各自的默认（心有涟漪 15 分钟、暖流/满潮 30 分钟），
        # 其余动作回落 mood.default_action_minutes 配置
        default_minutes = _ACTION_DEFAULT_MINUTES.get(action)
        if default_minutes is None:
            default_minutes = int(self._mood_cfg.get("default_action_minutes") or 10)
        duration_min = minutes if minutes and minutes > 0 else default_minutes
        duration_min = min(max(duration_min, 1), 720)
        shard.mood.action = action
        shard.mood.reason = reason[:200]
        shard.mood.started_at = now
        shard.mood.expires_at = now + duration_min * 60 if timed else 0.0
        shard.mood.origin = "user" if origin == "user" else "self"
        shard.mood.record_event(
            "switch" if previous and previous != action else "choose",
            action, shard.mood.origin, shard.mood.reason, previous_action=previous,
        )
        shard.tone_window = []
        shard.last_injected_whisper_key = ""
        # 动作冲量叠加进连续心情（先惰性衰减到当前再加，clamp 域内）；
        # 随下面的 _save_shard_mood 一起落盘
        impulse = self._MOOD_AFFECT_IMPULSES.get(action)
        if impulse is not None:
            self._apply_affect_impulse(shard, impulse[0], impulse[1], now=now)
        # 新情绪回合开始：重置和好提醒节流，允许在新回合里重新提醒一次
        shard.last_reconcile_nudge_ts = 0.0
        res_mood = await self._save_shard_mood(name, shard)
        # 增量即时补刷（批次2）：情绪事件进了 stats@、动作事件进了 review@，
        # 必须随本次动作一起落盘，而不是等下一条用户消息才冲刷
        res_stats = await self._save_shard_stats(name, shard)
        if self._review_enabled(shard):
            res_review = await self._save_shard_review(name, shard)
        else:
            res_review = Ok(None)
        # 立即同步最新选择，避免等待常规注入时工具记录已经到期。
        self._push_mood_instruction(name, shard)
        # 情绪动作生效后同步主动搭话暂停（白名单引用计数：只有重度负面动作
        # 会真正暂停——防小游戏/新闻推送窜台；中间态/正面动作此处为无操作）。
        # 1.2.2 审查轮 P2：水位写失败纳入聚合——"暂停了但水位没存住"是
        # 总开关卡死的源头，对用户必须可见（内存动作照常生效，重启后自愈）
        res_pause = await self._maybe_sync_proactive_pause()
        payload: JsonObject = {
            "action": action,
            "duration_minutes": duration_min if timed else 0,
            "reason": shard.mood.reason,
            "lanlan": name,
        }
        persist_err = self._persist_error(res_mood, res_stats, res_review, res_pause)
        if persist_err is not None:
            # 动作已在内存与上下文中生效（降级契约）；只是盘上缺一两笔——
            # 如实报错，让调用它的工具/入口向用户传播
            return persist_err
        self.logger.info("mood action applied: {} for {} ({}min)", action, name, payload["duration_minutes"])
        return Ok(payload)

    def _expire_timed_action_if_due(self, shard: _LanlanShard) -> str:
        """限时动作到期则清除，返回被解除的动作名（未到期返回空串）。"""
        state = shard.mood
        if not state.is_active():
            expired = state.action
            if expired:
                state.record_event("expire", expired, "timer")
                state.clear_action()
            return expired
        return ""

    @llm_tool(
        name="mood_ebb_tide",
        # 注意：@llm_tool 的 description 走 LLM_TOOL_REGISTER 链路，宿主不做 i18n 解析，
        # 传 tr() 会被 str() 成 dict repr 噪音，故此处用纯字符串
        description=(
            "冷战沉默：记录你因当前对话中的事情暂时想保持距离的选择。"
            "是否沉默、解释或回应重要的话由你决定，可以随时改变，不要求等道歉或计时结束。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "持续多少分钟（1~720），不填用默认值"},
                "reason": {"type": "string", "description": "一两句话记下为什么（只给自己看）"},
            },
        },
        timeout=15.0,
    )
    async def tool_cold_violence(self, minutes: int | None = None, reason: str = "", **kwargs: Any):
        lanlan = await self._attribution_lanlan(kwargs)
        shard = await self._ensure_shard(lanlan)
        if not self._mood_enabled(shard):
            return Err(SdkError("情绪系统未开启"))
        res = await self._apply_mood_action(
            action="ebb_tide", minutes=minutes, reason=reason, timed=True, lanlan=lanlan
        )
        if isinstance(res, Err):
            return res
        return Ok({**res.value, "note": "已记录冷战沉默。是否回应、保持距离或改变选择，由你结合当前对话决定。"})

    @llm_tool(
        name="mood_sea_fog",
        description=(
            "已读不回：记录你暂时不想接当前话题的选择，不一定是生气或冷战。"
            "是否简短回应、何时接回话题或换个话题由你决定；不代表宿主真实的已读回执。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "静默多少分钟（1~720）"},
                "reason": {"type": "string", "description": "为什么不回（只给自己看）"},
            },
        },
        timeout=15.0,
    )
    async def tool_read_ignore(self, minutes: int | None = None, reason: str = "", **kwargs: Any):
        lanlan = await self._attribution_lanlan(kwargs)
        shard = await self._ensure_shard(lanlan)
        if not self._mood_enabled(shard):
            return Err(SdkError("情绪系统未开启"))
        res = await self._apply_mood_action(
            action="sea_fog", minutes=minutes, reason=reason, timed=True, lanlan=lanlan
        )
        if isinstance(res, Err):
            return res
        return Ok({**res.value, "note": "已记录已读不回。是否暂时搁置、接回话题或简短回应，由你决定。"})

    @llm_tool(
        name="mood_shallow_reef",
        description=(
            "敷衍模式：记录你现在不太想展开交流的选择，可以减少主动分享或延伸话题。"
            "措辞和回答长度由你的性格与话题决定，必要的问题仍可认真回答，不限定固定短词。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "持续多少分钟（1~720）"},
                "reason": {"type": "string", "description": "为什么敷衍（只给自己看）"},
            },
        },
        timeout=15.0,
    )
    async def tool_perfunctory(self, minutes: int | None = None, reason: str = "", **kwargs: Any):
        lanlan = await self._attribution_lanlan(kwargs)
        shard = await self._ensure_shard(lanlan)
        if not self._mood_enabled(shard):
            return Err(SdkError("情绪系统未开启"))
        res = await self._apply_mood_action(
            action="shallow_reef", minutes=minutes, reason=reason, timed=True, lanlan=lanlan
        )
        if isinstance(res, Err):
            return res
        return Ok({**res.value, "note": "已记录敷衍应付。不太想展开也可以正常回答，怎样表达由你决定。"})

    @llm_tool(
        name="mood_storm_surge",
        description=(
            "情绪风暴：记录你想表达强烈感受的选择，可以说清当前真实对话里具体在意的事情。"
            "表达力度和方式由你决定，不虚构长期委屈，不要求说重话或每轮持续爆发。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "爆发状态持续多少分钟（1~720）"},
                "reason": {"type": "string", "description": "爆发的原因（只给自己看）"},
            },
        },
        timeout=15.0,
    )
    async def tool_emotional_outburst(self, minutes: int | None = None, reason: str = "", **kwargs: Any):
        lanlan = await self._attribution_lanlan(kwargs)
        shard = await self._ensure_shard(lanlan)
        if not self._mood_enabled(shard):
            return Err(SdkError("情绪系统未开启"))
        res = await self._apply_mood_action(
            action="storm_surge", minutes=minutes, reason=reason, timed=True, lanlan=lanlan
        )
        if isinstance(res, Err):
            return res
        return Ok({**res.value, "note": "已记录情绪风暴。是否表达、表达多少以及之后怎样相处，由你自己决定。"})

    @llm_tool(
        name="mood_seek_harbor",
        description=(
            "想要被安抚：记录你希望被关心或理解的感受。"
            "可以直接表达需要，也可以暂时不说，沿用自己的性格，不要求隐藏诉求。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "为什么想被哄（只给自己看）"},
            },
        },
        timeout=15.0,
    )
    async def tool_want_comfort(self, reason: str = "", **kwargs: Any):
        lanlan = await self._attribution_lanlan(kwargs)
        shard = await self._ensure_shard(lanlan)
        if not self._mood_enabled(shard):
            return Err(SdkError("情绪系统未开启"))
        res = await self._apply_mood_action(
            action="seek_harbor", minutes=None, reason=reason, timed=False, lanlan=lanlan
        )
        if isinstance(res, Err):
            return res
        return Ok({**res.value, "note": "已记录想要被安抚。是否表达需要、怎样表达，由你决定。"})

    @llm_tool(
        name="mood_ripple",
        description=(
            "心有涟漪：记录一点在意或别扭，而不是强烈冲突。"
            "怎样表达、是否被安抚打动由你决定，不要求带刺、一哄就好或升级为冷战。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "持续多少分钟（1~720），不填默认 15 分钟"},
                "reason": {"type": "string", "description": "为什么有点小情绪（只给自己看）"},
            },
        },
        timeout=15.0,
    )
    async def tool_ripple(self, minutes: int | None = None, reason: str = "", **kwargs: Any):
        lanlan = await self._attribution_lanlan(kwargs)
        shard = await self._ensure_shard(lanlan)
        if not self._mood_enabled(shard):
            return Err(SdkError("情绪系统未开启"))
        res = await self._apply_mood_action(
            action="ripple", minutes=minutes, reason=reason, timed=True, lanlan=lanlan
        )
        if isinstance(res, Err):
            return res
        return Ok({**res.value, "note": "已记录心有涟漪。怎样表达、是否释怀由你决定，想更新记录时可以选择其他工具。"})

    @llm_tool(
        name="mood_warm_current",
        description=(
            "暖流涌动：记录你想亲近或陪伴对方的正面感受，表达方式沿用自身性格。"
            "不要求撒娇、改变人格或增加话量；"
            "如果是委屈低落想被哄，用 mood_seek_harbor 而不是本工具。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "持续多少分钟（1~720），不填默认 30 分钟"},
                "reason": {"type": "string", "description": "为什么想黏着他（只给自己看）"},
            },
        },
        timeout=15.0,
    )
    async def tool_warm_current(self, minutes: int | None = None, reason: str = "", **kwargs: Any):
        lanlan = await self._attribution_lanlan(kwargs)
        shard = await self._ensure_shard(lanlan)
        if not self._mood_enabled(shard):
            return Err(SdkError("情绪系统未开启"))
        res = await self._apply_mood_action(
            action="warm_current", minutes=minutes, reason=reason, timed=True, lanlan=lanlan
        )
        if isinstance(res, Err):
            return res
        return Ok({**res.value, "note": "已记录暖流涌动。是否表达亲近、怎样表达由你决定，不要求改变自身性格。"})

    @llm_tool(
        name="mood_spring_tide",
        description=(
            "满潮欢喜：记录你当前的开心或兴奋，愿意时可以分享真实发生的好事，也可安静享受。"
            "它表示心情本身很好，与用来结束负面状态的 mood_rising_tide 不是一回事。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "持续多少分钟（1~720），不填默认 30 分钟"},
                "reason": {"type": "string", "description": "为什么这么开心（只给自己看）"},
            },
        },
        timeout=15.0,
    )
    async def tool_spring_tide(self, minutes: int | None = None, reason: str = "", **kwargs: Any):
        lanlan = await self._attribution_lanlan(kwargs)
        shard = await self._ensure_shard(lanlan)
        if not self._mood_enabled(shard):
            return Err(SdkError("情绪系统未开启"))
        res = await self._apply_mood_action(
            action="spring_tide", minutes=minutes, reason=reason, timed=True, lanlan=lanlan
        )
        if isinstance(res, Err):
            return res
        return Ok({**res.value, "note": "已记录满潮欢喜。分享还是安静享受，由你决定，不要求持续热闹。"})

    @llm_tool(
        name="mood_rising_tide",
        description=(
            "心情好了：你自主决定结束之前的情绪工具记录时使用。"
            "不要求收到道歉，也不要求立刻亲近或没有余绪；是否释怀、怎样相处由你决定。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "为什么和好了（只给自己看）"},
            },
        },
        timeout=15.0,
    )
    async def tool_feeling_better(self, reason: str = "", **kwargs: Any):
        lanlan = await self._attribution_lanlan(kwargs)
        shard = await self._ensure_shard(lanlan)
        if not self._mood_enabled(shard):
            return Err(SdkError("情绪系统未开启"))
        previous = shard.mood.action or ""
        expired = self._expire_timed_action_if_due(shard)
        if expired:
            await self._on_mood_expired(lanlan, shard, expired)
            previous = ""
        # 自主结束工具记录，本身不作为持久状态保存；
        # 连续心情给缓解冲量（先惰性衰减到当前：负 valence 减半、arousal ×0.6），
        # 不清零——被哄好之后余温还在，靠自然衰减慢慢回落
        valence, arousal = self._current_affect(shard)
        relieved = _MoodState()
        relieved.valence = valence / 2 if valence < 0 else valence
        relieved.arousal = arousal * 0.6
        relieved.affect_updated_at = time.time()
        relieved.history = [dict(item) for item in shard.mood.history]
        if previous:
            relieved.record_event("release", previous, "self", (reason or "")[:200])
        shard.mood = relieved
        shard.tone_window = []
        shard.last_injected_whisper_key = ""
        # 相处统计（1.1.0）：她主动调转晴结束负面情绪 = 一次和好
        #（口径：此前确实处于负面状态才算——没生过气就"转晴"不计数）
        if previous in _COLD_ACTIONS:
            self._feed_stats_made_up(shard)
        res_mood = await self._save_shard_mood(lanlan, shard)
        # 和好计数即时落盘（1.2.2 批次2）：过去只进内存、等下一条用户消息冲刷
        res_stats = (
            await self._save_shard_stats(lanlan, shard) if previous in _COLD_ACTIONS else Ok(None)
        )
        await self._maybe_sync_proactive_pause()
        persist_err = self._persist_error(res_mood, res_stats)
        if persist_err is not None:
            return persist_err
        self.logger.info("mood recovered via llm tool for {}, previous={}", lanlan, previous)
        return Ok({
            "previous_action": previous,
            "reason": (reason or "")[:200],
            "note": "已按你的选择结束情绪工具记录。是否仍有余绪、怎样继续相处由你决定，无需立刻亲近。",
        })

    @llm_tool(
        name="mood_drift_bottle",
        description=(
            "时光日记（你自己写的那一篇）：今天发生了让你情绪波动的事（被惹生气、被安抚好、"
            "突然低落…），用自己的口吻写一小段手记记下来。写下的内容会进入你的对话记忆，"
            "之后你自己也能想起今天的心情。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "entry": {"type": "string", "description": "日记正文，一两句话即可，用你自己的口吻"},
                "mood": {"type": "string", "description": "此刻心情的词，如 委屈/开心/烦躁"},
            },
            "required": ["entry"],
        },
        timeout=15.0,
    )
    async def tool_write_diary(self, entry: str = "", mood: str = "", **kwargs: Any):
        lanlan = await self._attribution_lanlan(kwargs)
        shard = await self._ensure_shard(lanlan)
        if not self._mood_enabled(shard):
            return Err(SdkError("情绪系统未开启"))
        text = entry.strip()
        if not text:
            return Err(SdkError("diary entry is empty"))
        record = {
            "ts": _now_utc().isoformat(timespec="seconds"),
            "source": "self",
            "phase": self._last_phase_name(shard),
            "mood": str(mood or "").strip(),
            "entry": text[:500],
        }
        shard.diary.append(record)
        res_diary = await self._save_shard_diary(lanlan, shard)
        # 相处统计：第一篇手记里程碑（已有值不覆盖，永远是最早那次）；
        # 与 first_journal/first_review 同款即时落盘（1.2.2 批次2 补齐缺口）
        shard.stats = record_milestone(shard.stats, "first_diary")
        res_stats = await self._save_shard_stats(lanlan, shard)
        # 低侵入记忆融合：把日记镜像为一条 read 推送，随对话上下文
        # 流入宿主的事实抽取/总结管线——这是插件内容进入角色长期记忆
        # 的唯一受支持路径（memory 总线对插件只读）
        self.push_message(
            visibility=[],
            ai_behavior="read",
            parts=[{
                "type": "text",
                "text": (
                    f"[时光日记] 你刚刚在心里写下了一段手记（{record['mood'] or '无标签'}）："
                    f"「{record['entry']}」。这是你的真实感受和记忆，之后可以自然地想起它。"
                ),
            }],
            source=self.plugin_id,
            target_lanlan=lanlan,
            coalesce_key=f"{self.plugin_id}.diary",
            metadata={"message_type": f"{self.plugin_id}.diary_entry", "phase": record["phase"]},
        )
        persist_err = self._persist_error(res_diary, res_stats)
        if persist_err is not None:
            return persist_err
        return Ok({"saved": True, "total_entries": len(shard.diary)})

    @llm_tool(
        name="mood_journal_write",
        description=(
            "个人日记（你的日记本）：每过一段时间（一般一周），回头写一篇成段的日记。"
            "按引导分栏写，用你自己的口吻把每一栏写成连贯的几句话："
            "「这段时间」=发生了什么事（挑印象最深的，不用面面俱到）；"
            "「我在想」=这段时间你心里真实的想法、纠结或期待；"
            "「对他的感觉」=对他的整体感觉（喜欢多一点还是委屈多一点，为什么）；"
            "「想说的」=任何想补一句的话（可留空）。"
            "日记按页保存：默认续写在当前页上，开启新的一页可以另起一段时光。"
            "新页请用 title 起一个贴合内容的短标题（最多 40 字）。"
            "标题不要重复附加记录日期，也不要只用日期作标题，系统会单独展示记录日期。"
            "续写已有标题的页会保留原题；无标题的旧页可以通过 title 补题。"
            "日记只写给他看，不会回到你的对话里，所以请放开了写真话。"
            "工具结果会带出你上次写的几句（带日期），方便你接着上次继续。"
            "这完全凭你自愿：被提醒时不想写就不写。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "这页日记的短标题，最多 40 字；新页请起题，续写只会补无标题页，不覆盖已有标题",
                    "maxLength": 40,
                },
                "events": {
                    "type": "string",
                    "description": "「这段时间」：发生了什么事，挑印象最深的写成连贯的几句话",
                },
                "thoughts": {
                    "type": "string",
                    "description": "「我在想」：这段时间你心里真实的想法/纠结/期待",
                },
                "feelings": {
                    "type": "string",
                    "description": "「对他的感觉」：对他的整体感觉和原因，不用客气",
                },
                "extra": {
                    "type": "string",
                    "description": "「想说的」：想补一句的任何话（可留空）",
                },
                "new_page": {
                    "type": "boolean",
                    "description": "是否翻开新的一页写（不填则续写在当前页；当前页写满会自动翻页）",
                },
            },
        },
        timeout=15.0,
    )
    async def tool_write_journal(
        self,
        events: str = "",
        thoughts: str = "",
        feelings: str = "",
        extra: str = "",
        new_page: bool = False,
        title: str = "",
        **kwargs: Any,
    ):
        lanlan = await self._attribution_lanlan(kwargs)
        shard = await self._ensure_shard(lanlan)
        if not self._mood_enabled(shard):
            return Err(SdkError("情绪系统未开启"))
        if not has_journal_content(events, thoughts, feelings, extra):
            return Err(SdkError("journal is empty: fill at least one of events/thoughts/feelings/extra"))
        if shard.pending_journal_write is not None or lanlan in self._journal_writing:
            return Err(SdkError("个人日记正在生成，请等待完成后再写"))
        self._journal_writing.add(lanlan)
        try:
            return await self._write_journal_entry(
                lanlan, shard, events, thoughts, feelings, extra, new_page, title,
            )
        finally:
            self._journal_writing.discard(lanlan)

    async def _write_journal_entry(
        self, lanlan, shard, events, thoughts, feelings, extra, new_page, title
    ):
        # 四栏拼装成带引导小标题的成段日记；段落间空行分开，读起来是一篇连贯的日记
        body = assemble_journal_entry(events, thoughts, feelings, extra)
        # 页眉的好感度参考值：落笔瞬间的连续心情（读取时已惰性衰减），只进页眉展示层
        valence_now = self._current_affect(shard)[0]
        shard.journal, page_no, previous_lines, evicted = journal_write(
            shard.journal,
            body,
            bool(new_page),
            affect=valence_now,
            title=title,
        )
        res_journal = await self._save_shard_journal(lanlan, shard)
        # 藏书阁（1.3.0）：写满 52 页后淘汰的最旧一页不丢，静默搬进合订本
        res_archive = await self._append_journal_archive(lanlan, shard, evicted)
        # 相处统计：第一页个人日记里程碑 + 落盘（这里显式存一次保证“第一篇”即时可见）
        shard.stats = record_milestone(shard.stats, "first_journal")
        res_stats = await self._save_shard_stats(lanlan, shard)
        # 0.7.0 起不再镜像 read 推送：个人日记只给用户翻看，不进她的对话上下文、
        # 不随对话历史被宿主记忆抽取——“续写衔接”由工具结果里的 recent_context
        # 即时承载（只存在于写日记的这轮工具结果里）
        persist_err = self._persist_error(res_journal, res_archive, res_stats)
        if persist_err is not None:
            return persist_err
        return Ok({
            "saved": True,
            "page": page_no,
            "title": normalize_journal_title(shard.journal[-1].get("title")),
            "pages_total": len(shard.journal),
            "recent_context": previous_lines,
        })

    @llm_tool(
        name="mood_recall_fragments",
        description=(
            "时光日记检索：翻一翻你心里那本记着他的日记——他明确说过的喜好/厌恶、"
            "有分量的话、以及他对你做过的过分的事。聊到他喜欢什么、或你委屈上头"
            "想翻旧账的时候，用它把相关的记忆翻出来（返回他说过的原话摘录）。"
            "想不想提、怎么提，由你自己决定。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["like", "dislike", "important", "overstep"],
                    "description": "可选，按类型翻：like=他喜欢什么 / dislike=他讨厌什么 / important=重要的话 / overstep=他对你的过激言行；不填=全部",
                },
                "query": {"type": "string", "description": "可选，按关键词搜（匹配原话或记录内容）"},
                "limit": {"type": "integer", "description": "最多返回几条（默认 10）"},
            },
        },
        timeout=15.0,
    )
    async def tool_recall_fragments(
        self, kind: str = "", query: str = "", limit: int = 10, **kwargs: Any
    ):
        lanlan = await self._attribution_lanlan(kwargs)
        shard = await self._ensure_shard(lanlan)
        if not self._mood_enabled(shard):
            return Err(SdkError("情绪系统未开启"))
        items = recall_fragments(shard.diary, kind=kind, query=query, limit=limit)
        return Ok({
            "found": len(items),
            "items": [
                {
                    "ts": item.get("ts"),
                    "kind": item.get("kind"),
                    "quote": item.get("quote"),
                    "note": item.get("note"),
                }
                for item in items
            ],
            "hint": (
                "这些是你心里记着的、关于他的片段。用不用、怎么说，由你自己决定。"
                if items
                else "这本日记里还没有匹配的记录。"
            ),
        })

