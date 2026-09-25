import time

from .activity import PlayerActivityInference
from .memory import MinecraftShortTermMemory
from .minigame import MiniGameCompanion
from .quiet import QuietCompanionTrigger
from .suggestion import ProactiveSuggestionTrigger


class PlaymateContextManager:
    def __init__(self, plugin):
        self._plugin = plugin
        self.memory = MinecraftShortTermMemory(
            max_items=plugin._playmate_memory_items,
            max_summary_length=plugin._playmate_memory_summary_length,
        )
        self.activity = PlayerActivityInference(
            debounce_checks=plugin._playmate_activity_debounce_checks,
            cooldown_seconds=plugin._playmate_activity_cooldown,
        )
        self.quiet = QuietCompanionTrigger(
            stable_seconds=plugin._playmate_quiet_stable_seconds,
            cooldown_seconds=plugin._playmate_quiet_cooldown,
        )
        self.minigame = MiniGameCompanion(
            cooldown_seconds=plugin._playmate_minigame_feedback_cooldown,
            max_context_chars=plugin._playmate_minigame_context_chars,
        )
        self.suggestion = ProactiveSuggestionTrigger(
            cooldown_seconds=plugin._playmate_suggestion_cooldown,
        )
        self._last_observed_state = "unknown"
        self._current_goal = ""
        self._current_plan = ""

    def remember_event(self, event_type, text, priority=1):
        return self.memory.remember(event_type or "event", text, priority=priority)

    def remember_minigame_event(self, event_data, text, priority, side_effects):
        context_text = self.minigame.record(event_data, text, priority, side_effects or {}, self.memory)
        self._current_goal = self.minigame.current_goal or self._current_goal
        return context_text

    def remember_awareness(self, changes):
        for change in changes or []:
            priority = 2 if change.get("context_only") else 6 if change.get("urgent") else 3
            self.memory.remember("awareness", change.get("text", ""), priority=priority)

    async def observe_awareness(self, awareness_data):
        update = self.activity.observe(awareness_data, self.memory)
        if update:
            self._plugin.logger.info(f"[Playmate] Activity changed: {update.state} ({update.label})")
            self._plugin._playmate_debug.record("activity", state=update.state, label=update.label, text=update.text)
            self.memory.remember("activity", update.text, priority=1)
            summary = self.memory.format_summary(
                limit=self._plugin._playmate_memory_inject_items,
                max_text_length=self._plugin._playmate_memory_inject_chars,
            )
            activity_text = update.text
            ai_behavior = "read"
            priority = 1
            aggregate = True
            respond_states = ("mining", "underground_exploring", "fishing", "base_building",
                             "traveling", "idle", "nether_exploring", "end_exploring",
                             "redstone_engineering", "exploring")
            if update.state in respond_states:
                if update.state in ("mining", "underground_exploring"):
                    activity_text = f"{update.text}\n玩家像是进入了下矿/探洞节奏。请主动短短陪一句，重点是一起下去、注意照明或会陪着，不要像系统提醒。"
                ai_behavior = "respond"
                priority = 3
                aggregate = False
            self._current_goal = self._goal_for_state(update.state, update.label)
            text = self._with_shared_context(activity_text, summary)
            await self._plugin._push_minecraft_context(text, ai_behavior=ai_behavior, priority=priority, aggregate=aggregate, coalesce_key="mc_activity")
        stable_state = self.activity.stable_state
        if stable_state != self._last_observed_state:
            self._last_observed_state = stable_state
            self._plugin.logger.info(f"[Playmate] Stable state: {stable_state} ({self.activity.stable_label})")
        quiet_text = self.quiet.observe(
            stable_state,
            self.activity.stable_label,
            recent_push_count=self._plugin._minecraft_push.recent_push_count(60),
        )
        if quiet_text:
            self._plugin.logger.info(f"[Playmate] Quiet companion triggered: {self.activity.stable_label}")
            self._plugin._playmate_debug.record("quiet", state=stable_state, label=self.activity.stable_label, text=quiet_text[:160])
            self.memory.remember("quiet", quiet_text, priority=1)
            summary = self.memory.format_summary(
                limit=self._plugin._playmate_memory_inject_items,
                max_text_length=self._plugin._playmate_memory_inject_chars,
            )
            text = self._with_shared_context(quiet_text, summary)
            await self._plugin._push_minecraft_context(text, ai_behavior="respond", priority=3, aggregate=False, coalesce_key="mc_companion")
        suggestion = None
        if not quiet_text:
            suggestion = self.suggestion.observe(
                awareness_data,
                stable_state,
                recent_push_count=self._plugin._minecraft_push.recent_push_count(60),
                recent_chat_seconds=self._recent_chat_seconds(),
            )
        if suggestion:
            self._plugin.logger.info(f"[Playmate] Proactive suggestion triggered: {stable_state}")
            suggestion_text = suggestion.get("text", "")
            should_respond = bool(suggestion.get("respond"))
            self._plugin._playmate_debug.record("suggestion", state=stable_state, respond=should_respond, text=suggestion_text[:160])
            self.memory.remember("suggestion", suggestion_text, priority=1)
            summary = self.memory.format_summary(
                limit=self._plugin._playmate_memory_inject_items,
                max_text_length=self._plugin._playmate_memory_inject_chars,
            )
            text = self._with_shared_context(suggestion_text, summary)
            await self._plugin._push_minecraft_context(
                text,
                ai_behavior="respond" if should_respond else "read",
                priority=3 if should_respond else 1,
                aggregate=not should_respond,
                coalesce_key="mc_suggestion",
            )

    def _with_shared_context(self, text, summary):
        parts = [text]
        if self._current_goal:
            parts.append(f"当前共同目标：{self._current_goal}")
        if self._current_plan:
            parts.append(f"当前目标板：\n{self._current_plan}")
        if summary:
            parts.append(f"最近共同经历：\n{summary}")
        return "\n".join(parts)

    def _goal_for_state(self, state, label):
        goals = {
            "mining": "一起下矿，注意照明和安全",
            "underground_exploring": "一起探洞，留意怪物和回路",
            "base_building": "一起建家/布置据点",
            "fishing": "一起钓鱼，安静等鱼上钩",
            "traveling": "一起赶路/跑图，看看前面有什么",
            "mob_farming": "一起处理刷怪和战斗收尾",
            "redstone_engineering": "一起搞红石装置，帮忙递材料",
            "nether_exploring": "一起探索下界，注意岩浆和恶魂",
            "end_exploring": "一起探索末地，小心末影人",
            "trading": "一起和村民交易",
            "organizing": "陪玩家整理物品，尽量少打扰",
        }
        return goals.get(state, f"一起进行{label}") if state not in ("unknown", "idle", "away") else ""

    def _recent_chat_seconds(self):
        for item in reversed(self.memory.recent(12)):
            if item.kind == "chat":
                return time.time() - item.timestamp
        return None
