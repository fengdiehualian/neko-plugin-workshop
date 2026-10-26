from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .unit_knowledge import unit_profile


@lru_cache(maxsize=1)
def load_building_database() -> dict[str, Any]:
    path = Path(__file__).with_name("building_command_database.json")
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value.get("buildings"), dict):
        raise ValueError("building command database has no buildings map")
    return value


def building_record(type_id: Any) -> dict[str, Any] | None:
    key = str(type_id or "").strip().upper()
    value = load_building_database()["buildings"].get(key)
    if isinstance(value, dict):
        return dict(value, type_id=key)
    profile = unit_profile(key) or {}
    if profile.get("kind") != "building":
        return None
    category = str(
        profile.get("building_class") or profile.get("role") or "other"
    ).casefold()
    strategic = (
        profile.get("combat_relevant") is True
        or category not in {"map_object", "decoration", "wall", "gate"}
    ) and profile.get("is_ai_only") is not True
    priorities = {
        "defense": 110,
        "infantry_production": 90,
        "vehicle_production": 90,
        "aircraft_production": 90,
        "naval_production": 90,
        "power": 85,
        "resource": 82,
        "technology": 70,
        "construction_yard": 20,
    }
    return {
        "type_id": key,
        "name": profile.get("display_name") or key,
        "category": category,
        "tier": profile.get("tech_tier"),
        "priority": priorities.get(category, 55),
        "strategic_target": strategic,
    }


def building_query_categories(query: Any) -> set[str]:
    compact = "".join(character for character in str(query or "").casefold() if character.isalnum())
    if not compact:
        return set()
    matches: set[str] = set()
    for category, aliases in load_building_database().get("category_aliases", {}).items():
        if any(
            "".join(character for character in str(alias).casefold() if character.isalnum()) in compact
            for alias in aliases
        ):
            matches.add(str(category))
    return matches


def strategic_building_priority(entity: dict[str, Any], categories: set[str] | None = None) -> int:
    record = building_record(entity.get("type_id"))
    if not record or record.get("strategic_target") is not True:
        return -1
    if categories and record.get("category") not in categories:
        return -1
    return max(0, int(record.get("priority", 0)))


def building_category(entity: dict[str, Any]) -> str:
    live_class = str(entity.get("building_class") or "").casefold()
    if live_class:
        return live_class
    record = building_record(entity.get("type_id"))
    return str(record.get("category") or "").casefold() if record else ""


def is_defense_building(entity: dict[str, Any]) -> bool:
    category = building_category(entity)
    role = str(entity.get("role") or "").casefold()
    return category in {"defense", "base_defense", "anti_air_defense"} or role in {
        "defense", "base_defense", "anti_air_defense"
    }


def is_construction_yard(entity: dict[str, Any]) -> bool:
    return building_category(entity) == "construction_yard"


def is_production_or_technology(entity: dict[str, Any]) -> bool:
    return building_category(entity) in {
        "barracks", "war_factory", "naval_yard", "airfield",
        "infantry_production", "vehicle_production", "aircraft_production",
        "technology", "t2_technology", "t3_technology",
    }


def is_production_or_economy(entity: dict[str, Any]) -> bool:
    return building_category(entity) in {
        "barracks", "war_factory", "naval_yard", "airfield",
        "infantry_production", "vehicle_production", "aircraft_production",
        "naval_production", "power", "refinery", "resource",
    }


def is_technology_building(entity: dict[str, Any]) -> bool:
    return building_category(entity) in {
        "technology", "t2_technology", "t3_technology",
        "military_support", "economy_support", "support_building",
        "superweapon",
    }


def is_power_building(entity: dict[str, Any]) -> bool:
    return building_category(entity) == "power"


def _distance_sq_from_center(entity: dict[str, Any], x: float, y: float) -> float:
    cell = entity.get("cell")
    if not isinstance(cell, dict):
        return float("inf")
    return (int(cell["x"]) - x) ** 2 + (int(cell["y"]) - y) ** 2


def select_assault_target(
    units: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    all_visible_enemies: list[dict[str, Any]] | None = None,
    base_radius: int = 16,
) -> dict[str, Any]:
    """Select a local base target without tunnelling onto the construction yard."""
    if not targets:
        raise ValueError("targets must not be empty")
    cells = [unit.get("cell") for unit in units if isinstance(unit.get("cell"), dict)]
    if cells:
        center_x = sum(int(cell["x"]) for cell in cells) / len(cells)
        center_y = sum(int(cell["y"]) for cell in cells) / len(cells)
    else:
        center_x = center_y = 0.0

    def nearest(items: list[dict[str, Any]]) -> dict[str, Any]:
        return min(
            items,
            key=lambda item: (
                _distance_sq_from_center(item, center_x, center_y),
                int(item.get("object_id", 0)),
            ),
        )

    enemy_pool = all_visible_enemies if all_visible_enemies is not None else targets
    buildings = [item for item in targets if str(item.get("kind") or "").casefold() == "building"]
    yards = [item for item in buildings if is_construction_yard(item)]
    core = nearest(yards) if yards else None
    # Priority is global across currently visible eligible enemies. Limiting
    # buildings to a construction-yard radius previously let a nearer yard
    # bypass an exposed defensive or production structure outside that circle.
    local_pool = enemy_pool

    local_combat = [
        item for item in enemy_pool
        if str(item.get("kind") or "").casefold() in {
            "infantry", "vehicle", "aircraft", "unit", "mobile_base"
        }
        and is_valid_attack_target(item)
    ]
    if local_combat:
        return nearest(local_combat)

    local_buildings = [
        item for item in local_pool
        if str(item.get("kind") or "").casefold() == "building"
        and is_valid_attack_target(item)
    ]
    defenses = [item for item in local_buildings if is_defense_building(item)]
    if defenses:
        return nearest(defenses)

    production = [
        item for item in local_buildings if is_production_or_economy(item)
    ]
    if production:
        return nearest(production)

    technology = [
        item for item in local_buildings if is_technology_building(item)
    ]
    if technology:
        return nearest(technology)

    non_core = [item for item in local_buildings if not is_construction_yard(item)]
    if non_core:
        return nearest(non_core)
    if core:
        return core
    return nearest(targets)


def is_valid_attack_target(entity: dict[str, Any]) -> bool:
    """Reject scenery and civilian objects from automatic combat targeting."""
    if not isinstance(entity, dict):
        return False
    kind = str(entity.get("kind") or "").casefold()
    if kind == "building":
        return is_defense_building(entity) or strategic_building_priority(entity) >= 0
    flags = entity.get("flags") if isinstance(entity.get("flags"), dict) else {}
    if any(flags.get(key) is True for key in ("civilian", "natural", "insignificant")):
        return False
    role = str(entity.get("role") or "").casefold()
    if role in {"civilian", "map_object", "decoration", "ambient", "wildlife"}:
        return False
    type_id = str(entity.get("type_id") or "").upper()
    if type_id.startswith("CIV"):
        return False
    if entity.get("combat_relevant") is False:
        return False
    # Some native query responses omit kind metadata. They remain eligible
    # unless positive civilian/scenery evidence above rejects them.
    if not kind:
        return True
    return kind in {"infantry", "vehicle", "aircraft", "unit", "mobile_base"}


def combat_target_priority(entity: dict[str, Any]) -> int:
    """Return a stable automatic-attack priority; negative means ineligible."""
    if not is_valid_attack_target(entity):
        return -1
    if str(entity.get("kind") or "").casefold() == "building":
        if is_defense_building(entity):
            return 110
        if is_construction_yard(entity):
            return 20
        if is_production_or_economy(entity):
            return 90
        if is_technology_building(entity):
            return 70
        return 60
    kind = str(entity.get("kind") or "").casefold()
    role = str(entity.get("role") or "").casefold()
    if kind == "aircraft":
        return 150
    if role in {"artillery", "anti_infantry", "anti_armor", "hero"}:
        return 145
    if kind == "vehicle":
        return 140
    if role in {"combat_infantry", "engineer"} or kind == "infantry":
        return 130
    return 120


def assault_target_layer(entity: dict[str, Any]) -> int:
    """Return the strict automatic assault layer requested by the commander."""
    if not is_valid_attack_target(entity):
        return -1
    if str(entity.get("kind") or "").casefold() != "building":
        return 5
    if is_defense_building(entity):
        return 4
    if is_production_or_economy(entity):
        return 3
    if is_technology_building(entity):
        return 2
    if is_construction_yard(entity):
        return 0
    return 1
