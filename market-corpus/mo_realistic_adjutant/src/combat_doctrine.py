"""Indexed, state-derived access to the generated MO combat doctrine matrix."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .unit_knowledge import unit_profile
from .strategy_library import shared_strategy_library


VALID_POSTURES = {"defend", "delay", "probe", "pressure", "attack", "decisive_attack"}
REQUIRED_CONSTRAINTS = {
    "visible_information_only", "preserve_player_authority",
    "do_not_block_independent_production_lanes", "verify_native_action_progress",
}
_last_library_generation = ""


@lru_cache(maxsize=4096)
def _profile(type_id: str) -> dict[str, Any]:
    return unit_profile(str(type_id).upper()) or {}


def load_combat_doctrine() -> dict[str, Any]:
    global _last_library_generation
    library = shared_strategy_library()
    generation = library.generation()
    if generation != _last_library_generation:
        _last_library_generation = generation
        _index.cache_clear()
        _unit_response_index.cache_clear()
        _threat_names.cache_clear()
    cached = _load_library_doctrine(str(library.path), generation)
    if cached.get("entries") or cached.get("unit_responses"):
        return cached
    path = Path(__file__).with_name("combat_doctrine.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"schema": 1, "metadata": {"entry_count": 0}, "entries": []}
    return payload if isinstance(payload, dict) else {"schema": 1, "metadata": {"entry_count": 0}, "entries": []}


@lru_cache(maxsize=8)
def _load_library_doctrine(path: str, generation: str) -> dict[str, Any]:
    try:
        value = shared_strategy_library().export_combat_doctrine()
    except Exception:
        return {"schema": 1, "metadata": {"entry_count": 0}, "entries": [], "unit_responses": []}
    return value if isinstance(value, dict) else {"schema": 1, "metadata": {"entry_count": 0}, "entries": [], "unit_responses": []}


def validate_combat_doctrine(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else load_combat_doctrine()
    entries = data.get("entries") if isinstance(data.get("entries"), list) else []
    ids: set[str] = set()
    condition_keys: set[tuple[str, ...]] = set()
    invalid = 0
    for entry in entries:
        if not isinstance(entry, dict):
            invalid += 1
            continue
        entry_id = str(entry.get("id") or "")
        conditions = entry.get("conditions")
        constraints = set(entry.get("constraints") or ())
        if (
            not entry_id or entry_id in ids or not isinstance(conditions, dict)
            or entry.get("posture") not in VALID_POSTURES
            or not isinstance(entry.get("actions"), list) or len(entry["actions"]) < 5
            or not REQUIRED_CONSTRAINTS.issubset(constraints)
        ):
            invalid += 1
        ids.add(entry_id)
        if isinstance(conditions, dict):
            condition_keys.add(tuple(str(conditions.get(name) or "") for name in (
                "enemy_faction", "primary_threat", "stage", "economy", "force_ratio", "terrain"
            )))
    unit_responses = data.get("unit_responses") if isinstance(data.get("unit_responses"), list) else []
    unit_response_ids = {
        str(item.get("id") or "") for item in unit_responses if isinstance(item, dict)
    }
    invalid_unit_responses = sum(
        1 for item in unit_responses
        if not isinstance(item, dict) or not item.get("enemy_type_id")
        or not item.get("production_roles") or not item.get("target_priorities")
        or "visible_information_only" not in set(item.get("constraints") or ())
        or "preserve_player_authority" not in set(item.get("constraints") or ())
    )
    metadata = data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {}
    expected_matrix = int(metadata.get("matrix_entry_count", -1))
    expected_unit = int(metadata.get("unit_response_count", -1))
    expected_total = int(metadata.get("entry_count", -1))
    total = len(entries) + len(unit_responses)
    return {
        "entries": len(entries), "unique_ids": len(ids),
        "unique_conditions": len(condition_keys), "invalid": invalid,
        "unit_responses": len(unit_responses),
        "unique_unit_response_ids": len(unit_response_ids),
        "invalid_unit_responses": invalid_unit_responses,
        "total_entries": total,
        "metadata_matches": expected_matrix == len(entries)
        and expected_unit == len(unit_responses) and expected_total == total,
        "passed": total >= 7000 and len(unit_responses) >= 2000
        and len(ids) == len(entries) and len(condition_keys) == len(entries)
        and len(unit_response_ids) == len(unit_responses)
        and invalid == 0 and invalid_unit_responses == 0
        and expected_matrix == len(entries) and expected_unit == len(unit_responses)
        and expected_total == total,
    }


def _visible(state: dict[str, Any], relation: str) -> list[dict[str, Any]]:
    return [
        item for item in state.get("visible_entities", [])
        if isinstance(item, dict) and item.get("relation") == relation
        and item.get("visible") is True and item.get("alive") is True
    ]


def _strength(items: list[dict[str, Any]]) -> float:
    return sum(
        max(1.0, float(item.get("strength") or 100.0))
        * max(0.1, float(item.get("health_ratio") or 1.0))
        for item in items if item.get("kind") in {"infantry", "vehicle", "aircraft"}
    )


def _primary_threat(enemies: list[dict[str, Any]], profiles: list[dict[str, Any]]) -> str:
    infantry = sum(item.get("kind") == "infantry" for item in profiles)
    armor = sum(item.get("battlefield_class") == "armored" for item in profiles)
    air = sum(item.get("battlefield_class") == "air" for item in profiles)
    naval = sum(item.get("battlefield_class") == "naval" for item in profiles)
    area = sum(item.get("area_anti_infantry") is True for item in profiles)
    text = " ".join(
        " ".join(str(item.get(key) or "") for key in ("type_id", "internal_name", "role", "building_class"))
        for item in profiles
    ).casefold()
    building_classes = {str(item.get("building_class") or "") for item in enemies if item.get("kind") == "building"}
    token_rules = (
        (("superweapon" in building_classes), "superweapon"),
        (("mind" in text or "psych" in text), "mind_control"),
        (("stealth" in text or "cloak" in text), "stealth_infiltration"),
        (("transport" in text or "carryall" in text), "transport_drop"),
        (("submarine" in text or "sub " in text), "submarine"),
        (("artillery" in text or "mortar" in text), "artillery"),
        (("siege" in text or "howitzer" in text), "siege"),
        (("bomber" in text), "bomber_strike"),
        (("helicopter" in text or "chopper" in text), "helicopter_raid"),
    )
    for matched, label in token_rules:
        if matched:
            return label
    if air >= 4:
        return "air_swarm"
    if naval >= 4:
        return "naval_surface"
    if infantry >= 25:
        return "infantry_swarm"
    if area >= 3:
        return "area_anti_infantry"
    if armor >= 8:
        return "heavy_armor"
    if armor >= 3:
        return "light_armor"
    if "defense" in building_classes:
        return "static_defense"
    if not enemies:
        return "economy_expansion"
    return "balanced_force"


def classify_combat_context(
    state: dict[str, Any], *, phase: str | None = None,
    own_strength: float | None = None, enemy_strength: float | None = None,
) -> dict[str, Any]:
    own = _visible(state, "self")
    enemy = _visible(state, "enemy")
    profiles = [_profile(str(item.get("type_id") or "")) or item for item in enemy]
    faction_counts: dict[str, int] = {}
    for profile in profiles:
        for faction in profile.get("factions") or ():
            faction_counts[str(faction)] = faction_counts.get(str(faction), 0) + 1
    faction = max(faction_counts, key=faction_counts.get) if faction_counts else "unknown"
    own_value = _strength(own) if own_strength is None else float(own_strength)
    enemy_value = _strength(enemy) if enemy_strength is None else float(enemy_strength)
    ratio = own_value / max(1.0, enemy_value)
    force_ratio = "behind" if ratio < 0.8 else "ahead" if ratio > 1.3 else "even"
    economy = state.get("economy") if isinstance(state.get("economy"), dict) else {}
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    credits = player.get("credits", economy.get("credits"))
    income = economy.get("net_per_minute")
    economy_band = (
        "starved" if isinstance(credits, (int, float)) and credits < 1500
        else "surplus" if (
            isinstance(credits, (int, float)) and credits >= 8000
        ) or (isinstance(income, (int, float)) and income >= 1000)
        else "stable"
    )
    max_tier = max((int(profile.get("tech_tier") or 0) for profile in (_profile(str(item.get("type_id") or "")) for item in own)), default=0)
    game = state.get("game") if isinstance(state.get("game"), dict) else {}
    frame = int(game.get("frame") or 0)
    stage = (
        "sudden_death" if phase == "sudden_death" or frame >= 54000
        else "late" if frame >= 36000
        else "tier3" if max_tier >= 3
        else "tier2" if max_tier >= 2
        else "opening"
    )
    battlefield = state.get("battlefield") if isinstance(state.get("battlefield"), dict) else {}
    terrain = "chokepoint" if battlefield.get("terrain") == "chokepoint" or battlefield.get("choke") is True else "open"
    observed = state.get("battlefield") if isinstance(state.get("battlefield"), dict) else {}
    observed_threat = str(observed.get("primary_threat") or "")
    primary_threat = (
        observed_threat
        if observed_threat in _threat_names()
        else _primary_threat(enemy, profiles)
    )
    return {
        "enemy_faction": faction if faction in {"allied", "soviet", "epsilon", "foehn"} else "unknown",
        "primary_threat": primary_threat,
        "stage": stage, "economy": economy_band,
        "force_ratio": force_ratio, "terrain": terrain,
        "own_strength": round(own_value, 2), "enemy_strength": round(enemy_value, 2),
    }


@lru_cache(maxsize=1)
def _index() -> dict[tuple[str, str, str, str, str, str], dict[str, Any]]:
    result: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    for entry in load_combat_doctrine().get("entries", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("conditions"), dict):
            continue
        condition = entry["conditions"]
        key = tuple(str(condition.get(name) or "") for name in (
            "enemy_faction", "primary_threat", "stage", "economy", "force_ratio", "terrain"
        ))
        current = result.get(key)
        rank = (int(entry.get("priority") or 0), float(entry.get("weight") or 0.0))
        current_rank = (
            int(current.get("priority") or 0), float(current.get("weight") or 0.0)
        ) if isinstance(current, dict) else (-1, -1.0)
        if rank > current_rank:
            result[key] = entry
    return result


@lru_cache(maxsize=1)
def _threat_names() -> frozenset[str]:
    return frozenset(key[1] for key in _index())


@lru_cache(maxsize=1)
def _unit_response_index() -> dict[tuple[str, str, str], dict[str, Any]]:
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in load_combat_doctrine().get("unit_responses", []):
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("enemy_type_id") or "").upper(),
            str(item.get("force_ratio") or ""), str(item.get("terrain") or ""),
        )
        current = result.get(key)
        rank = (int(item.get("priority") or 0), float(item.get("weight") or 0.0))
        current_rank = (
            int(current.get("priority") or 0), float(current.get("weight") or 0.0)
        ) if isinstance(current, dict) else (-1, -1.0)
        if rank > current_rank:
            result[key] = item
    return result


def _unit_response_is_active(
    response: dict[str, Any], enemies: list[dict[str, Any]], type_id: str,
) -> bool:
    matching = [
        item for item in enemies
        if str(item.get("type_id") or "").upper() == str(type_id).upper()
    ]
    activation = response.get("activation") if isinstance(response.get("activation"), dict) else {}
    minimum_count = max(1, int(activation.get("minimum_count") or 1))
    minimum_threat = max(1.0, float(activation.get("minimum_total_threat") or 1.0))
    threat = sum(
        float((_profile(str(item.get("type_id") or "")) or item).get("threat_score") or 1.0)
        * max(0.1, float(item.get("health_ratio") or 1.0))
        for item in matching
    )
    return len(matching) >= minimum_count and threat >= minimum_threat


def recommend_combat_doctrine(
    state: dict[str, Any], *, phase: str | None = None,
    own_strength: float | None = None, enemy_strength: float | None = None,
) -> dict[str, Any]:
    context = classify_combat_context(
        state, phase=phase, own_strength=own_strength, enemy_strength=enemy_strength
    )
    key = tuple(str(context[name]) for name in (
        "enemy_faction", "primary_threat", "stage", "economy", "force_ratio", "terrain"
    ))
    entry = _index().get(key)
    enemies = _visible(state, "enemy")
    combat_enemies = [
        item for item in enemies
        if item.get("kind") in {"infantry", "vehicle", "aircraft", "building"}
        and item.get("type_id")
    ]
    primary_enemy = max(
        combat_enemies,
        key=lambda item: (
            float((_profile(str(item.get("type_id") or "")) or item).get("threat_score") or 0.0),
            float(item.get("strength") or 0.0),
            -int(item.get("object_id") or 0),
        ),
        default=None,
    )
    unit_response = None
    if isinstance(primary_enemy, dict):
        primary_type = str(primary_enemy.get("type_id") or "").upper()
        candidate = _unit_response_index().get((
            primary_type,
            str(context["force_ratio"]), str(context["terrain"]),
        ))
        if isinstance(candidate, dict) and _unit_response_is_active(candidate, combat_enemies, primary_type):
            unit_response = candidate
    effective = dict(entry) if isinstance(entry, dict) else None
    if isinstance(effective, dict) and isinstance(unit_response, dict):
        effective["matrix_rule_id"] = effective.get("id")
        effective["unit_response_id"] = unit_response.get("id")
        effective["id"] = f"{effective['matrix_rule_id']}+{unit_response['id']}"
        for field in ("production_roles", "target_priorities", "actions"):
            effective[field] = list(dict.fromkeys(
                list(unit_response.get(field, [])) + list(effective.get(field, []))
            ))
        effective["formation"] = unit_response.get("formation") or effective.get("formation")
        effective["engagement"] = unit_response.get("engagement") or effective.get("engagement")
        effective["tactical_controls"] = {
            **(effective.get("tactical_controls") if isinstance(effective.get("tactical_controls"), dict) else {}),
            **(unit_response.get("tactical_controls") if isinstance(unit_response.get("tactical_controls"), dict) else {}),
        }
        effective["priority"] = max(
            int(effective.get("priority") or 0), int(unit_response.get("priority") or 0),
        )
    return {
        "context": context, "rule": effective,
        "unit_response": dict(unit_response) if isinstance(unit_response, dict) else None,
    }
