from __future__ import annotations

from math import ceil
from typing import Any

from .unit_knowledge import unit_profile


COMBAT_KINDS = {"infantry", "vehicle", "aircraft"}
DEPLOYABLE_COMBAT_TYPES = {"E1", "GGI", "E1US", "GGIUS"}


def is_economic_unit(entity: dict[str, Any]) -> bool:
    """Return true for harvesters/mobile economy units that must stay native-controlled."""
    if not isinstance(entity, dict):
        return False
    profile = combat_profile(entity)
    flags = profile.get("flags") if isinstance(profile.get("flags"), dict) else {}
    role = str(profile.get("role") or entity.get("role") or "").casefold()
    type_id = str(
        profile.get("type_id") or entity.get("effective_type_id")
        or entity.get("type_id") or ""
    ).upper()
    return (
        str(entity.get("kind") or "").casefold() in {"harvester", "resource_gatherer"}
        or role in {"resource_gatherer", "harvester", "miner", "economic"}
        or flags.get("harvester") is True
        or flags.get("resourcegatherer") is True
        or type_id in {"CMIN", "HARV", "SMIN", "YMIN", "EMIN", "FMIN"}
    )


def is_deployable_combat_unit(entity: dict[str, Any]) -> bool:
    """Recognize infantry whose native weapon has a deployed firing posture."""
    if not isinstance(entity, dict) or is_economic_unit(entity):
        return False
    if entity.get("can_deploy") is True:
        return True
    type_id = str(
        entity.get("effective_type_id") or entity.get("type_id") or ""
    ).upper()
    return type_id in DEPLOYABLE_COMBAT_TYPES


def distance_sq(a: dict[str, Any], b: dict[str, Any]) -> int:
    acell, bcell = a.get("cell"), b.get("cell")
    if not isinstance(acell, dict) or not isinstance(bcell, dict):
        return 2**31 - 1
    return (int(acell["x"]) - int(bcell["x"])) ** 2 + (
        int(acell["y"]) - int(bcell["y"])
    ) ** 2


def visible_entities(state: dict[str, Any], relation: str) -> list[dict[str, Any]]:
    return [
        item for item in state.get("visible_entities", [])
        if isinstance(item, dict)
        and item.get("relation") == relation
        and item.get("alive") is True
        and item.get("visible") is True
        and isinstance(item.get("cell"), dict)
    ]


def combat_profile(entity: dict[str, Any]) -> dict[str, Any]:
    """Resolve live entities through the rules catalog, including known IFV loadouts."""
    type_id = str(
        entity.get("effective_type_id") or entity.get("type_id") or ""
    ).upper()
    profile = unit_profile(type_id) or {}
    result = dict(profile)
    for key in (
        "kind", "role", "strength", "battlefield_class", "threat_score",
        "anti_infantry", "anti_armor", "anti_air", "anti_structure",
        "area_anti_infantry",
    ):
        if entity.get(key) is not None and key not in result:
            result[key] = entity[key]
    result["type_id"] = type_id
    return result


def effective_combat_strength(entity: dict[str, Any]) -> float:
    profile = combat_profile(entity)
    strength = float(entity.get("strength") or profile.get("strength") or 100.0)
    threat = float(profile.get("threat_score") or 0.0)
    health = max(0.1, min(1.0, float(entity.get("health_ratio", 1.0))))
    return max(1.0, strength + threat * 4.0) * health


def combat_matchup_score(
    attacker: dict[str, Any], target: dict[str, Any]
) -> float:
    """Score a live attacker against a visible target using catalog weapon roles."""
    source = combat_profile(attacker)
    enemy = combat_profile(target)
    enemy_class = str(enemy.get("battlefield_class") or target.get("kind") or "")
    source_type = str(source.get("type_id") or "").upper()
    score = float(enemy.get("threat_score") or 0.0)
    score += (1.0 - max(0.0, min(1.0, float(target.get("health_ratio", 1.0))))) * 35.0
    if enemy_class == "infantry":
        score += 150.0 if source.get("anti_infantry") is True else -25.0
        if source.get("area_anti_infantry") is True:
            score += 35.0
        if source_type in {"GHOST", "SEAL", "RIOT"}:
            score += 120.0
        elif source_type == "GGI":
            score -= 60.0
    elif enemy_class == "armored":
        score += 150.0 if source.get("anti_armor") is True else -30.0
        if source_type == "GGI":
            score += 120.0
        elif source_type in {"GHOST", "SEAL", "RIOT"}:
            # A SEAL-loaded IFV uses its anti-infantry turret, not the SEAL's C4.
            score -= 140.0
    elif enemy_class == "air":
        score += 180.0 if source.get("anti_air") is True else -220.0
    role = str(enemy.get("role") or "")
    if role in {"artillery", "hero", "support_aircraft", "combat_aircraft"}:
        score += 45.0
    return score


def prioritized_counter_assignments(
    units: list[dict[str, Any]], enemies: list[dict[str, Any]],
    maximum_responders: int | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Assign current counters to current threats; stale targets are never retained."""
    units = [unit for unit in units if not is_economic_unit(unit)]
    if not units or not enemies:
        return []
    limit = min(
        len(units),
        max(1, int(maximum_responders))
        if maximum_responders is not None else max(2, len(enemies) * 2),
    )
    pairs = sorted(
        ((unit, enemy) for unit in units for enemy in enemies),
        key=lambda pair: (
            -combat_matchup_score(pair[0], pair[1]),
            distance_sq(pair[0], pair[1]),
            int(pair[0].get("object_id", 0)),
            int(pair[1].get("object_id", 0)),
        ),
    )
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    used_units: set[int] = set()
    target_load: dict[int, int] = {}
    per_target = max(2, ceil(limit / max(1, len(enemies))))
    for target_capacity in range(1, per_target + 1):
        for unit, enemy in pairs:
            unit_id = int(unit["object_id"])
            target_id = int(enemy["object_id"])
            if unit_id in used_units or target_load.get(target_id, 0) >= target_capacity:
                continue
            result.append((unit, enemy))
            used_units.add(unit_id)
            target_load[target_id] = target_load.get(target_id, 0) + 1
            if len(result) >= limit:
                return result
    return result


def vulnerable_anchors(state: dict[str, Any]) -> list[dict[str, Any]]:
    own = visible_entities(state, "self")
    priority_classes = {
        "resource", "construction_yard", "power", "vehicle_production",
        "aircraft_production", "infantry_production", "technology",
    }
    anchors = [
        item for item in own
        if item.get("kind") == "building"
        and (
            str(item.get("building_class") or "") in priority_classes
            or float(item.get("health_ratio", 1.0)) < 0.98
        )
    ]
    known_ids = {int(item.get("object_id", -1)) for item in anchors}
    for harvester in state.get("harvesters", []):
        if not isinstance(harvester, dict) or harvester.get("alive") is not True:
            continue
        object_id = harvester.get("unit_id")
        if not isinstance(object_id, int) or object_id in known_ids:
            continue
        anchors.append({
            "object_id": object_id,
            "type_id": harvester.get("type_id"),
            "kind": "harvester",
            "health_ratio": harvester.get("health_ratio", 1.0),
            "cell": dict(harvester.get("cell", {})),
        })
    return anchors


def local_threats(
    state: dict[str, Any], anchors: list[dict[str, Any]], radius: int = 20,
) -> list[dict[str, Any]]:
    if not anchors:
        return []
    radius_sq = max(4, int(radius)) ** 2
    return [
        item for item in visible_entities(state, "enemy")
        if item.get("kind") in COMBAT_KINDS
        and min(distance_sq(item, anchor) for anchor in anchors) <= radius_sq
    ]


def partition_force(
    state: dict[str, Any], doctrine_rule: dict[str, Any] | None = None,
    excluded_ids: set[int] | None = None,
) -> dict[str, Any]:
    rule = doctrine_rule if isinstance(doctrine_rule, dict) else {}
    controls = rule.get("tactical_controls") if isinstance(rule.get("tactical_controls"), dict) else {}
    excluded = {int(value) for value in (excluded_ids or set())}
    own = [
        item for item in visible_entities(state, "self")
        if item.get("kind") in COMBAT_KINDS
        and item.get("controllable") is True
        and not is_economic_unit(item)
        and item.get("is_engineer") is not True
        and str(item.get("type_id") or "").upper() not in {
            "ENGINEER", "SENGINEER", "YENGINEER", "FENGINEER"
        }
        and isinstance(item.get("object_id"), int)
        and int(item["object_id"]) not in excluded
    ]
    t3_established = any(
        item.get("kind") == "building"
        and int((unit_profile(str(item.get("type_id") or "")) or {}).get("tech_tier") or 0) >= 3
        for item in visible_entities(state, "self")
    )
    anchors = vulnerable_anchors(state)
    enemies = [
        item for item in visible_entities(state, "enemy")
        if item.get("kind") in COMBAT_KINDS
    ]
    pursuit_radius = int(controls.get("pursuit_radius_cells", 20))
    threats_by_id = {
        int(item["object_id"]): item
        for item in local_threats(state, anchors, pursuit_radius)
        if isinstance(item.get("object_id"), int)
    }
    # Frontline contact matters even when it is far from a base anchor. Rebuild
    # this set every poll so a second enemy immediately replaces a defeated one.
    for item in contact_enemies(own, enemies, max(8, pursuit_radius)):
        threats_by_id[int(item["object_id"])] = item
    threats = list(threats_by_id.values())
    reserve_fraction = max(0.08, min(0.30, float(controls.get("reserve_fraction", 0.18))))
    if t3_established:
        reserve_fraction = max(0.22, reserve_fraction)
        defender_floor = min(24, max(8, ceil(len(own) * 0.28)))
    else:
        defender_floor = min(12, ceil(len(own) * 0.18))
    defender_count = min(
        len(own),
        max(2 if len(own) >= 6 else 1, defender_floor, len(threats) * 2),
    )
    remaining_count = max(0, len(own) - defender_count)
    reserve_floor = min(4, remaining_count) if t3_established else 0
    reserve_count = min(
        remaining_count,
        max(reserve_floor, ceil(len(own) * reserve_fraction)),
    )
    anchor = None
    if anchors:
        anchor = max(
            anchors,
            key=lambda item: (
                sum(distance_sq(item, enemy) <= 20 * 20 for enemy in enemies),
                1.0 - float(item.get("health_ratio", 1.0)),
                -int(item.get("object_id", 0)),
            ),
        )
    elif own:
        anchor = own[0]
    healthy = sorted(
        own,
        key=lambda item: (
            distance_sq(item, anchor) if anchor else 0,
            float(item.get("health_ratio", 1.0)),
            int(item["object_id"]),
        ),
    )
    defenders = healthy[:defender_count]
    remaining = healthy[defender_count:]
    reserve = sorted(
        remaining,
        key=lambda item: (-float(item.get("health_ratio", 1.0)), int(item["object_id"])),
    )[:reserve_count]
    reserve_ids = {int(item["object_id"]) for item in reserve}
    assault = [item for item in remaining if int(item["object_id"]) not in reserve_ids]
    return {
        "defenders": defenders,
        "reserve": reserve,
        "assault": assault,
        "anchors": anchors,
        "defense_anchor": anchor,
        "local_threats": threats,
        "t3_established": t3_established,
        "excluded_ids": sorted(excluded),
        "doctrine_id": rule.get("id"),
        "tactical_controls": controls,
    }


def contact_enemies(
    units: list[dict[str, Any]], enemies: list[dict[str, Any]], radius: int,
) -> list[dict[str, Any]]:
    if not units:
        return []
    radius_sq = max(3, int(radius)) ** 2
    return [
        enemy for enemy in enemies
        if enemy.get("kind") in COMBAT_KINDS
        and min(distance_sq(unit, enemy) for unit in units) <= radius_sq
    ]


def damaged_buildings(state: dict[str, Any], threshold: float = 0.98) -> list[dict[str, Any]]:
    return sorted(
        (
            item for item in visible_entities(state, "self")
            if item.get("kind") == "building"
            and item.get("controllable") is True
            and isinstance(item.get("object_id"), int)
            and float(item.get("health_ratio", 1.0)) < threshold
        ),
        key=lambda item: (float(item.get("health_ratio", 1.0)), int(item["object_id"])),
    )


def threatened_harvesters(
    state: dict[str, Any], enemies: list[dict[str, Any]], radius: int = 8,
) -> list[dict[str, Any]]:
    radius_sq = max(4, int(radius)) ** 2
    result = []
    for item in state.get("harvesters", []):
        if not isinstance(item, dict) or item.get("alive") is not True:
            continue
        if item.get("controllable") is not True or not isinstance(item.get("cell"), dict):
            continue
        nearby = [enemy for enemy in enemies if distance_sq(item, enemy) <= radius_sq]
        # Damage alone is not an active threat. Treating permanently damaged
        # harvesters as threatened would keep replacing their native mining job.
        if nearby:
            result.append({**item, "nearby_threats": nearby})
    return result
