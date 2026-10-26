from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .video_strategy_knowledge import video_rules, video_sources
from .combat_doctrine import recommend_combat_doctrine


@lru_cache(maxsize=1)
def load_tutorial_knowledge() -> dict[str, Any]:
    path = Path(__file__).with_name("tutorial_knowledge.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"version": 1, "sources": [], "rules": [], "faction_aliases": {}}
    return value if isinstance(value, dict) else {"version": 1, "sources": [], "rules": [], "faction_aliases": {}}


def _normal(value: Any) -> str:
    return "".join(str(value).casefold().split()).replace("，", "").replace(",", "")


def tutorial_search(query: str = "", faction: str | None = None) -> list[dict[str, Any]]:
    needle = _normal(query)
    faction_needle = _normal(faction) if faction else ""
    data = load_tutorial_knowledge()
    aliases = data.get("faction_aliases", {})
    matched_faction = None
    if faction_needle:
        for name, values in aliases.items():
            if faction_needle == _normal(name) or any(faction_needle == _normal(item) for item in values):
                matched_faction = name
                break
    rows = []
    all_rules = list(data.get("rules", [])) + video_rules()
    for rule in all_rules:
        if not isinstance(rule, dict):
            continue
        tags = [str(item) for item in rule.get("tags", [])]
        haystack = _normal(rule.get("id", "")) + _normal(rule.get("reason", "")) + "".join(_normal(item) for item in tags)
        if needle and needle not in haystack and not any(needle in _normal(tag) for tag in tags):
            continue
        item = dict(rule)
        if matched_faction:
            item["faction"] = matched_faction
        rows.append(item)
    rows.sort(key=lambda item: (-int(item.get("priority", 0)), str(item.get("id", ""))))
    return rows


def tutorial_sources() -> list[dict[str, Any]]:
    return list(load_tutorial_knowledge().get("sources", [])) + video_sources()


def recommend_tactics(state: dict[str, Any], intent: str = "") -> dict[str, Any]:
    production = state.get("production") if isinstance(state, dict) else {}
    economy = state.get("economy") if isinstance(state, dict) else {}
    alerts = state.get("alerts") if isinstance(state, dict) else []
    visible = state.get("visible_entities") if isinstance(state, dict) else []
    power_surplus = economy.get("power_surplus") if isinstance(economy, dict) else None
    nearby_hostile = any(isinstance(item, dict) and item.get("relation") == "enemy" and item.get("visible") is True for item in (visible or []))
    loading = any(isinstance(item, dict) and item.get("mission") in {"enter", "load"} for item in (visible or []))
    harvesters = sum(
        1 for item in (visible or [])
        if isinstance(item, dict)
        and item.get("relation") == "self"
        and (
            str(item.get("type_id") or "").upper() in {"CMIN", "HARV", "SMIN", "YMIN", "NMIN"}
            or str(item.get("role") or "").casefold() == "harvester"
            or (
                isinstance(item.get("flags"), dict)
                and item["flags"].get("harvester") is True
            )
        )
    )
    convoy_ready = sum(
        1 for item in (visible or [])
        if isinstance(item, dict)
        and item.get("relation") == "self"
        and str(item.get("type_id") or "").upper() == "FV"
    ) >= 8
    queue = production.get("queue", []) if isinstance(production, dict) else []
    building_ready = any(
        isinstance(item, dict)
        and str(item.get("kind") or "").casefold() == "building"
        and str(item.get("status") or "").casefold() in {"ready", "completed", "awaiting_placement"}
        for item in queue if isinstance(queue, list)
    )
    context = {
        "intent": str(intent), "power_surplus": power_surplus,
        "nearby_hostile": nearby_hostile, "loading_task": loading,
        "harvester_count": harvesters, "convoy_ready": convoy_ready,
        "building_ready_to_place": building_ready,
    }
    candidates = []
    for rule in tutorial_search():
        conditions = rule.get("when", {})
        valid = True
        if "power_surplus_below" in conditions:
            valid = valid and isinstance(power_surplus, int) and power_surplus < int(conditions["power_surplus_below"])
        if "harvester_count_below" in conditions:
            valid = valid and harvesters < int(conditions["harvester_count_below"])
        if conditions.get("convoy_ready") is True:
            valid = valid and convoy_ready
        if conditions.get("building_ready_to_place") is True:
            valid = valid and building_ready
        if conditions.get("nearby_hostile") is True:
            valid = valid and nearby_hostile
        if conditions.get("loading_task") is True:
            valid = valid and loading
        if conditions.get("no_visible_hostile") is True:
            valid = valid and not nearby_hostile
        if valid:
            candidates.append(rule)
    candidates.sort(key=lambda item: -int(item.get("priority", 0)))
    selected = candidates[:8]
    actions = []
    for rule in selected:
        for action in rule.get("actions", []):
            if action not in actions:
                actions.append(action)
    combat_doctrine = recommend_combat_doctrine(state)
    doctrine_rule = combat_doctrine.get("rule") if isinstance(combat_doctrine, dict) else None
    if isinstance(doctrine_rule, dict):
        for action in doctrine_rule.get("actions", []):
            if action not in actions:
                actions.append(action)
    return {
        "context": context,
        "rules": selected,
        "actions": actions,
        "sources": tutorial_sources(),
        "combat_doctrine": combat_doctrine,
    }
