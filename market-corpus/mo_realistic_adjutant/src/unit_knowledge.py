from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .unit_threat import classify_unit_threat


SHORTENABLE_SUFFIXES = (
    "装甲步兵车", "重型坦克", "轻型坦克", "主战坦克", "装甲车",
    "空中战舰", "轰炸机", "战斗机", "采矿车", "基地车", "潜艇",
    "步兵", "工兵", "坦克", "战车", "战机", "士兵",
)


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, dict[str, Any]]:
    path = Path(__file__).with_name("unit_catalog.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    units = payload.get("units") if isinstance(payload, dict) else None
    return units if isinstance(units, dict) else {}


@lru_cache(maxsize=1)
def load_stereotypes() -> dict[str, str]:
    path = Path(__file__).with_name("unit_stereotypes.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    units = payload.get("units") if isinstance(payload, dict) else None
    return units if isinstance(units, dict) else {}


@lru_cache(maxsize=1)
def load_player_aliases() -> dict[str, list[str]]:
    path = Path(__file__).with_name("player_aliases.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    aliases = payload.get("aliases") if isinstance(payload, dict) else None
    if not isinstance(aliases, dict):
        return {}
    return {
        str(type_id).upper(): [str(value) for value in values if value]
        for type_id, values in aliases.items() if isinstance(values, list)
    }


def resolve_type_ids(query: str, limit: int = 12) -> list[str]:
    needle = _normalize(query)
    if not needle:
        return []
    scored = []
    exact: list[tuple[int, str]] = []
    for type_id, record in load_catalog().items():
        aliases = _human_aliases(type_id, record)
        best = 0
        for alias in aliases:
            normalized = _normalize(str(alias))
            if not normalized:
                continue
            if needle == normalized:
                best = max(best, 1000 + len(normalized))
            elif normalized in needle:
                best = max(best, 700 + len(normalized))
            elif needle in normalized:
                best = max(best, 400 + len(needle))
            elif len(needle) >= 4 and _is_ordered_subsequence(needle, normalized):
                best = max(best, 250 + len(needle))
        if best:
            scored.append((best, type_id))
            if best >= 1000:
                exact.append((best, type_id))
    # A precise type ID or catalog alias must never be diluted by fuzzy matches.
    # For example, GGI's "G.I." alias is contained in the token "GGI".
    if exact:
        exact.sort(key=lambda item: (-item[0], item[1]))
        return [type_id for _score, type_id in exact[:limit]]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [type_id for _score, type_id in scored[:limit]]


def match_visible_entities(
    state: dict[str, Any], query: str, relation: str | None = None
) -> list[dict[str, Any]]:
    type_ids = set(resolve_type_ids(query))
    needle = _normalize(query)
    entities = state.get("visible_entities")
    if not isinstance(entities, list):
        return []
    result = []
    for entity in entities:
        if not isinstance(entity, dict) or entity.get("visible") is not True:
            continue
        if relation and entity.get("relation") != relation:
            continue
        type_id = str(entity.get("type_id", "")).upper()
        display = _normalize(str(entity.get("display_name", "")))
        display_matches = needle and (
            needle in display
            or display in needle
            or (len(needle) >= 4 and _is_ordered_subsequence(needle, display))
        )
        if type_id in type_ids or display_matches:
            result.append(entity)
    return result


def unit_profile(type_id: str) -> dict[str, Any] | None:
    record = load_catalog().get(str(type_id).upper())
    if not isinstance(record, dict):
        return None
    profile = {
        key: record.get(key)
        for key in (
            "type_id", "display_name", "internal_name", "kind", "category",
            "strength", "speed", "sight", "cost", "tech_level", "armor",
            "primary_weapon", "secondary_weapon", "weapons", "has_offensive_weapon",
            "owners", "role", "passengers", "size_limit", "storage", "deploys_into",
            "undeploys_into", "movement_zone", "speed_type",
            "building_class", "build_category", "factory", "flags",
            "is_ai_only", "combat_relevant", "aibuildthis",
            "ammo", "is_plug", "powers_up_building", "powers_up_to_level",
        )
    }
    profile["stereotype"] = load_stereotypes().get(str(type_id).upper(), "")
    profile.update(classify_unit_threat(record))
    return profile


def catalog_summary(query: str) -> list[dict[str, Any]]:
    return [profile for type_id in resolve_type_ids(query) if (profile := unit_profile(type_id))]


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", value.casefold())


def _human_aliases(type_id: str, record: Any) -> set[str]:
    if not isinstance(record, dict):
        return {type_id}
    aliases = {
        type_id,
        str(record.get("display_name", "")),
        str(record.get("internal_name", "")),
        *(str(value) for value in record.get("aliases", []) if value),
        *load_player_aliases().get(type_id.upper(), ()),
    }
    display_name = str(record.get("display_name", "")).strip()
    building_class = str(record.get("building_class", ""))
    class_aliases = {
        "infantry_production": ("兵营", "步兵营", "兵工厂"),
        "vehicle_production": ("重工", "战车工厂", "坦克工厂"),
        "aircraft_production": ("机场", "空军基地", "飞机场"),
        "naval_production": ("船厂", "造船厂", "海军船厂"),
        "resource": ("资源建筑", "矿场", "炼油厂", "精炼厂"),
        "technology": ("科技建筑", "科技", "高科", "实验室"),
        "superweapon": ("超武", "超级武器"),
        "defense": ("防御建筑", "炮塔"),
        "map_object": ("地图物件", "告示牌", "地图装饰"),
    }
    aliases.update(class_aliases.get(building_class, ()))
    for suffix in SHORTENABLE_SUFFIXES:
        if display_name.endswith(suffix):
            shortened = display_name[: -len(suffix)].strip()
            if len(_normalize(shortened)) >= 2:
                aliases.add(shortened)
    return {alias for alias in aliases if alias}


def _is_ordered_subsequence(needle: str, candidate: str) -> bool:
    iterator = iter(candidate)
    return all(character in iterator for character in needle)
