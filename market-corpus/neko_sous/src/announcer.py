"""Turn a small set of game facts into cues for the active character."""
from __future__ import annotations

import math
from typing import cast

from .kitchen import KITCHEN_EVENTS


class Announcer:
    def __init__(self, i18n, push, logger) -> None:
        self.i18n = i18n
        self.push = push
        self.logger = logger
        self.locale = "zh-CN"
        self.enabled = True

    def text(self, event: dict) -> str | None:
        def t(translation_key: str, **params) -> str:
            return self.i18n.t(translation_key, locale=self.locale, **params)

        name = event["event"]
        if name in KITCHEN_EVENTS:
            facts = t(f"broadcast.{KITCHEN_EVENTS[name]}", count=event.get("kitchen_count", 1))
            return t("broadcast.kitchenCue", facts=facts)
        if name == "companion_chat":
            details = []
            if event.get("score") is not None:
                details.append(t("broadcast.chatScore", score=event["score"]))
            if event.get("score_change") is not None:
                details.append(t("broadcast.chatChange", change=event["score_change"]))
            remaining = event.get("remaining_seconds")
            if remaining is not None:
                details.append(t("broadcast.chatRemaining", seconds=math.ceil(cast(int | float, remaining))))
            return t("broadcast.chatCue", facts=t("broadcast.separator").join(details))
        if name in {"first_delivery", "delivery_streak", "order_problem", "combo_broken"}:
            details = []
            if event.get("other_score") is not None:
                details.append(t("broadcast.orderTeam", team=t(f"team.{event['team']}")))
            if name == "combo_broken":
                details.append(t("broadcast.comboBroken"))
            elif name == "first_delivery":
                details.append(t("broadcast.firstDelivery"))
            elif name == "delivery_streak":
                details.append(t("broadcast.deliveryStreak", count=event["delivery_streak"]))
            else:
                if event["failed_count"]:
                    details.append(t("broadcast.failedDelivery", count=event["failed_count"]))
                if event["expired_count"]:
                    details.append(t("broadcast.expiredOrder", count=event["expired_count"]))
            return t("broadcast.orderCue", facts=t("broadcast.separator").join(details))
        if name == "scene_ready":
            scene = event["scene_name"].casefold()
            if "load" in scene:
                return None
            scenes = {
                "worldmap": "worldMap", "MainMenu".casefold(): "mainMenu", "frontend": "mainMenu",
                "party_lobby": "lobby", "PartyLobby".casefold(): "lobby", "VsLobby".casefold(): "lobby",
            }
            key = scenes.get(scene)
            if key is None and event.get("timer_kind") in {"countdown", "modifiable", "unlimited"}:
                key = "kitchen"
            if key is None:
                return None
            facts = t("broadcast.scene", scene=t(f"scene.{key}"))
        elif name == "round_started":
            details = []
            duration = event.get("duration_seconds")
            if event.get("timer_kind") == "unlimited":
                details.append(t("broadcast.unlimited"))
            elif duration is not None:
                minutes, seconds = divmod(math.ceil(cast(int | float, duration)), 60)
                details.append(t("broadcast.duration", minutes=minutes, seconds=seconds))
            if event.get("recipe_count") is not None:
                details.append(t("broadcast.recipes", count=event["recipe_count"]))
            facts = t("broadcast.started", details=t("broadcast.separator").join(details))
        elif name == "time_warning":
            facts = t("broadcast.warning", seconds=event["threshold_seconds"])
        elif name == "round_finished":
            details = []
            if event.get("score") is not None:
                key = "broadcast.teamScore" if event.get("other_score") is not None else "broadcast.score"
                details.append(t(key, score=event["score"]))
            if event.get("other_score") is not None:
                details.append(t("broadcast.otherScore", score=event["other_score"]))
            if event.get("stars") is not None:
                details.append(t("broadcast.stars", stars=event["stars"]))
            facts = t("broadcast.finished", details=t("broadcast.separator").join(details))
        else:
            return None
        return t("broadcast.cue", facts=facts)

    def announce(self, event: dict) -> bool:
        if not self.enabled:
            return False
        text = self.text(event)
        if text is None:
            return False
        receipt = self.push(
            source="neko_sous", visibility=[], ai_behavior="respond",
            parts=[{"type": "text", "text": text}],
            coalesce_key=(f"neko_sous_order_{event['event_id']}"
                          if event.get("expired_count", 0) or event["event"] in {"first_delivery", "delivery_streak", *KITCHEN_EVENTS}
                          else "neko_sous_game"),
            metadata={"context_type": "game_event", "event_id": event["event_id"],
                      "event": event["event"], "round_id": event.get("round_id")},
        )
        if not isinstance(receipt, dict) or receipt.get("submitted") is not True:
            raise RuntimeError(f"宿主未接受播报消息：{receipt}")
        self.logger.debug(f"[NekoSous] 已提交播报事件：{event['event']}")
        return True
