# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Decide when the character is allowed to speak, and about what.

A naive implementation of "react to the game log" produces a companion that
talks non-stop, because ``game.log`` is not an event feed tuned for conversation:
a real session writes an event-selection line every 10-30 seconds, the AI empires
contribute most of them, and the loudest real signal (a war declaration) looks
exactly like the quietest (an anomaly picked up by a neighbour).

So the engine is a *policy*, not a pipe. Three tiers plus two independent brakes:

* **urgent**   speaks on its own, but not more often than ``urgent_cooldown_s``
* **notable**  speaks when the floor is free, merged into one line if several
               arrive together
* **digest**   never speaks alone; accumulated and summarised every
               ``digest_interval_s`` so "research finished" does not become a
               stream of interruptions
* **silent**   recorded, never spoken

The brakes are a sliding-window ``max_per_minute`` cap and a hard ``min_gap_s``
floor between any two utterances. Both exist because the tiers alone cannot
bound a burst: ten urgent-looking events in five seconds is still ten events.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from .gamelog import GameEvent

# Tier assignment for goals the companion mod emits. Anything not listed is
# treated as digest-if-player-involved, silent otherwise, so an unknown or
# newly-added bridge goal degrades to "mention it later" instead of spamming.
URGENT_GOALS = frozenset({
    "war_declared_on_us",
    "crisis_arrived",
    "empire_defeated",
    "fleet_destroyed",
    "planet_in_revolt",
    "station_lost",
    "colony_ship_destroyed",
    "invasion_defeated",
    "leader_died",
})

NOTABLE_GOALS = frozenset({
    "war_declared_by_us",
    "war_ended",
    "first_contact",
    "communications_established",
    "federation_formed",
    "federation_joined",
    "colony_established",
    "megastructure_completed",
    "empire_defaulted",
    "invasion_success",
    "invasion_failed",
})

DIGEST_GOALS = frozenset({
    "research_completed",
    "construction_completed",
    "system_surveyed",
    "special_project_completed",
    "leader_hired",
    "ships_upgraded",
    "species_modified",
})

# Vanilla namespaces that are worth a mention even without the companion mod.
# Keyed by the part before the dot in the event id (e.g. "crisis.7515").
URGENT_NAMESPACES = frozenset({
    "crisis", "war_in_heaven", "fallen_empires_awakening", "fallen_machine_empire",
})
NOTABLE_NAMESPACES = frozenset({
    "galcom", "federation", "maruader", "marauder", "nomads",
})

URGENT = "urgent"
NOTABLE = "notable"
DIGEST = "digest"
SILENT = "silent"


@dataclass(frozen=True)
class PolicyConfig:
    """Tuning knobs. Defaults are deliberately conservative.

    The two rate brakes are what actually bound a session, and they are chosen
    from the player's side rather than the log's: one utterance per 30 seconds
    sustained (``max_per_minute = 2``) is about as often as a companion can talk
    before it stops feeling like company and starts feeling like a narrator.
    """

    urgent_cooldown_s: float = 45.0
    notable_cooldown_s: float = 60.0
    digest_interval_s: float = 120.0
    min_gap_s: float = 12.0
    max_per_minute: int = 2
    # Vanilla AI-empire events are the bulk of the log and none of them concern
    # the player. Dropping them is the single biggest noise reduction available.
    ignore_ai_events: bool = True
    # Cap on how many raw events are handed to the model in one utterance.
    max_context_events: int = 8
    dedupe_ttl_s: float = 300.0

    def tier_for(self, event: GameEvent) -> str:
        if event.kind == "session":
            return SILENT
        if event.kind == "bridge":
            if event.goal in URGENT_GOALS:
                return URGENT
            if event.goal in NOTABLE_GOALS:
                return NOTABLE
            if event.goal in DIGEST_GOALS:
                return DIGEST
            return DIGEST
        # Vanilla `Event ... added info about event selection` lines.
        namespace = event.goal.split(".", 1)[-1] if event.goal.startswith("vanilla.") else ""
        if namespace in URGENT_NAMESPACES:
            return URGENT
        if namespace in NOTABLE_NAMESPACES:
            return NOTABLE
        return DIGEST


@dataclass
class Decision:
    """What the policy wants to happen right now."""

    action: str                       # "speak" | "wait" | "drop"
    tier: str = SILENT
    events: list[GameEvent] = field(default_factory=list)
    reason: str = ""

    @property
    def should_speak(self) -> bool:
        return self.action == "speak" and bool(self.events)

    def describe(self) -> str:
        head = f"{self.action}/{self.tier}: {self.reason}"
        if self.events:
            head += " | " + "; ".join(e.describe() for e in self.events[:3])
        return head


class CommentaryPolicy:
    """Stateful gate between the log and the character's mouth."""

    def __init__(self, config: PolicyConfig | None = None):
        self.config = config or PolicyConfig()
        self._last_spoken_at: float = 0.0
        self._last_urgent_at: float = 0.0
        self._last_notable_at: float = 0.0
        self._spoken_times: deque[float] = deque()
        self._digest: list[GameEvent] = []
        self._last_digest_at: float = 0.0
        self._seen: dict[str, float] = {}
        self._dropped_ai = 0
        self._dropped_dupe = 0
        self._spoken_count = 0

    # -- bookkeeping ------------------------------------------------------- #

    def stats(self) -> dict[str, int | float]:
        """Counters for status reporting.

        The utterance counter is named ``spoken_count`` rather than ``spoken``
        because this dict gets merged into entry results that already use
        ``spoken`` for the list of prompts sent this poll; the collision silently
        replaced the list with an int.
        """
        return {
            "spoken_count": self._spoken_count,
            "dropped_ai_events": self._dropped_ai,
            "dropped_duplicates": self._dropped_dupe,
            "pending_digest": len(self._digest),
        }

    def _is_duplicate(self, event: GameEvent, now: float) -> bool:
        """True if this exact occurrence was already handled inside the TTL.

        Membership is tested explicitly rather than by comparing a ``.get``
        default against the cutoff: the previous form treated "never seen" as
        "seen at t=0", so against a small clock every event looked stale-but-
        recent and the whole stream was swallowed. In production ``now`` is a
        ``time.time()`` epoch value and the bug stayed hidden, which is precisely
        why it is worth spelling out.
        """
        cutoff = now - self.config.dedupe_ttl_s
        if len(self._seen) > 512:
            self._seen = {k: v for k, v in self._seen.items() if v >= cutoff}
        key = event.key()
        seen_at = self._seen.get(key)
        if seen_at is not None and seen_at >= cutoff:
            return True
        self._seen[key] = now
        return False

    def _rate_ok(self, now: float) -> tuple[bool, str]:
        if now - self._last_spoken_at < self.config.min_gap_s:
            return False, f"距上次说话不足 {self.config.min_gap_s:.0f}s"
        cutoff = now - 60.0
        while self._spoken_times and self._spoken_times[0] < cutoff:
            self._spoken_times.popleft()
        if len(self._spoken_times) >= self.config.max_per_minute:
            return False, f"每分钟上限 {self.config.max_per_minute} 条已满"
        return True, ""

    def note_spoken(self, now: float | None = None) -> None:
        """Record that an utterance actually went out (LLM turn started)."""
        stamp = time.time() if now is None else now
        self._last_spoken_at = stamp
        self._spoken_times.append(stamp)
        self._spoken_count += 1

    # -- intake ------------------------------------------------------------ #

    def consider(self, event: GameEvent, now: float | None = None) -> Decision:
        """Feed one event and get back what to do about it."""
        now = time.time() if now is None else now
        config = self.config

        if event.kind == "session":
            return Decision("drop", SILENT, reason="会话元信息")

        if self._is_duplicate(event, now):
            self._dropped_dupe += 1
            return Decision("drop", SILENT, reason="重复事件")

        if (config.ignore_ai_events and event.kind == "player_event"
                and event.human != 1):
            # `human` is -1 for events the player was not involved in. Those are
            # the AI empires' business and reacting to them is how a companion
            # ends up narrating someone else's game.
            self._dropped_ai += 1
            return Decision("drop", SILENT, reason="AI 国家事件")

        tier = config.tier_for(event)
        if tier == SILENT:
            return Decision("drop", SILENT, reason="静默类别")

        if tier == DIGEST:
            self._digest.append(event)
            if self._last_digest_at == 0.0:
                self._last_digest_at = now
            return Decision("wait", DIGEST, [event], reason="进入摘要队列")

        if tier == NOTABLE:
            if now - self._last_notable_at < config.notable_cooldown_s:
                self._digest.append(event)
                return Decision("wait", NOTABLE, [event], reason="重要事件冷却中，转入摘要")
            ok, why = self._rate_ok(now)
            if not ok:
                self._digest.append(event)
                return Decision("wait", NOTABLE, [event], reason=f"{why}，转入摘要")
            self._last_notable_at = now
            return Decision("speak", NOTABLE, [event], reason="重要事件")

        # URGENT
        if now - self._last_urgent_at < config.urgent_cooldown_s:
            return Decision("drop", URGENT, [event],
                            reason=f"紧急事件冷却中（{config.urgent_cooldown_s:.0f}s）")
        ok, why = self._rate_ok(now)
        if not ok:
            return Decision("drop", URGENT, [event], reason=why)
        self._last_urgent_at = now
        return Decision("speak", URGENT, [event], reason="紧急事件")

    def tick(self, now: float | None = None) -> Decision:
        """Flush the digest if enough time has passed; otherwise wait."""
        now = time.time() if now is None else now
        if not self._digest:
            return Decision("wait", DIGEST, reason="摘要队列为空")
        if now - self._last_digest_at < self.config.digest_interval_s:
            return Decision("wait", DIGEST, reason="摘要间隔未到")
        ok, why = self._rate_ok(now)
        if not ok:
            return Decision("wait", DIGEST, reason=why)

        events = self._digest[-self.config.max_context_events:]
        self._digest = []
        self._last_digest_at = now
        return Decision("speak", DIGEST, events, reason="摘要到点")

    def drain_digest(self) -> list[GameEvent]:
        """Take everything pending (used on shutdown or a manual flush)."""
        events, self._digest = self._digest, []
        return events


def build_prompt(decision: Decision) -> str:
    """Turn a decision into the text handed to the model.

    Written as an *event* rather than a task result on purpose: the SDK wraps
    ``push_message`` as "来自{插件}的新消息，请按内容回应", which is what we want
    here. Phrasing it as a report would make the character narrate that she just
    processed something.

    No ``{MASTER_NAME}`` / ``{LANLAN_NAME}`` placeholder is used: the prompt
    addresses the character directly, so there is nothing to substitute and no
    risk of a half-expanded template reaching the model.
    """
    if decision.tier == URGENT:
        lead = "刚刚发生了紧急的事，请立刻用一句话回应，语气紧张、护着玩家，不要播报数值。"
    elif decision.tier == NOTABLE:
        lead = "刚发生了一件值得提一句的事，用一句话自然地说出来，不要追问玩家要做什么。"
    else:
        lead = "这是刚才累积的一批小事，用一两句话概括成闲聊，不要逐条播报。"

    lines = [
        lead,
        "以下是游戏里真实发生的事（来自 Stellaris 的日志）：",
    ]
    lines.extend(f"- {event.describe()}" for event in decision.events)
    lines.append("用你自己的口吻回应，不要提到「日志」「插件」「数据」这些词。")
    return "\n".join(lines)
