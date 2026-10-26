from __future__ import annotations

import math
from typing import Any, Iterable


POWER_TYPE_IDS = {
    "GAPOWR", "NAPOWR", "NANRCT", "YAPOWR", "FAPOWR", "FANRCT",
}

TECH_CENTER_TYPE_IDS = {"GATECH", "NATECH", "YATECH", "FATECH"}

# Allied static defences do not have the same power cost.  NASAM is a
# late/electricity-heavy choice; GAPILL and GAGUN are the safe early choices.
ALLIED_HIGH_DRAIN_DEFENSE_IDS = {"NASAM"}
ALLIED_EARLY_DEFENSE_IDS = {"GAPILL", "GAGUN"}

ESSENTIAL_CLASS_TARGETS = {
    "power": 1,
    "infantry_production": 1,
    "resource": 2,
    "vehicle_production": 1,
    "aircraft_production": 1,
    "technology": 1,
    "repair_support": 1,
    "support_building": 1,
}

CLASS_PRIORITY = {
    "power": 0,
    "infantry_production": 10,
    "resource": 20,
    "vehicle_production": 30,
    "aircraft_production": 40,
    "technology": 50,
    "repair_support": 60,
    "support_building": 70,
    "defense": 80,
    "superweapon": 90,
}


def deduplicate_base_tasks(
    base_tasks: dict[str, dict[str, Any]],
    production_tasks: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Keep the oldest active base controller and retire duplicate children."""
    active = sorted(
        (
            task for task in base_tasks.values()
            if task.get("status") not in {
                "cancelled", "failed", "completed", "expired",
            }
        ),
        key=lambda task: (float(task.get("created_at", 0.0)), str(task.get("task_id"))),
    )
    if not active:
        return None
    canonical = active[0]
    for duplicate in active[1:]:
        duplicate["status"] = "cancelled"
        duplicate["last_reason"] = "duplicate base-development controller retired"
        duplicate_id = str(duplicate.get("task_id") or "")
        for production in production_tasks.values():
            if (
                str(production.get("owner_base_task_id") or "") == duplicate_id
                and production.get("status") not in {
                    "cancelled", "completed", "failed", "expired",
                }
            ):
                production["status"] = "cancelled"
                production["last_error"] = "duplicate base-development child retired"
    return canonical


def distance_sq(left: dict[str, Any], right: dict[str, Any]) -> int:
    return (int(left["x"]) - int(right["x"])) ** 2 + (
        int(left["y"]) - int(right["y"])
    ) ** 2


def is_power_profile(profile: dict[str, Any]) -> bool:
    type_id = str(profile.get("type_id", "")).upper()
    name = str(profile.get("display_name", ""))
    return type_id in POWER_TYPE_IDS or any(
        token in name for token in ("发电厂", "反应炉", "发电站", "动力厂")
    )


def has_established_technology_center(entities: Iterable[dict[str, Any]]) -> bool:
    """Only a live placed technology center unlocks remote ore expansion."""
    return any(
        isinstance(item, dict)
        and item.get("relation") == "self"
        and item.get("alive") is True
        and item.get("kind") == "building"
        and str(item.get("type_id") or "").upper() in TECH_CENTER_TYPE_IDS
        for item in entities
    )


def power_reserve_allows_defense(
    power_surplus: int | None, *, minimum_reserve: int = 100
) -> bool:
    """Never start a power-consuming defense with unknown or tight power."""
    return (
        isinstance(power_surplus, int)
        and not isinstance(power_surplus, bool)
        and int(power_surplus) >= max(0, int(minimum_reserve))
    )


def power_reserve_allows_nasam(
    power_surplus: int | None,
    *,
    power_stable: bool = False,
    planned_power_drain: int = 0,
    minimum_reserve: int = 300,
) -> bool:
    """Require stable telemetry and a large reserve before building NASAM.

    A single positive power snapshot is not enough: the next building or an
    already committed queue item can immediately push the house into low power.
    ``planned_power_drain`` is deliberately additive so callers can account for
    known future consumers without weakening the 300-point Allied threshold.
    """
    if power_stable is not True:
        return False
    return power_reserve_allows_defense(
        power_surplus,
        minimum_reserve=max(0, int(minimum_reserve)) + max(0, int(planned_power_drain)),
    )


def filter_defense_profiles_for_power(
    profiles: Iterable[dict[str, Any]],
    *,
    power_surplus: int | None,
    power_stable: bool = False,
    planned_power_drain: int = 0,
) -> list[dict[str, Any]]:
    """Apply Allied power gates to every defense candidate.

    The sidebar normally contains only the current faction's buildings.  The
    presence of NASAM therefore identifies the Allied defence set without
    adding a second faction/protocol field to the bridge contract.
    """
    candidates = list(profiles)
    allied_context = any(
        str(item.get("type_id") or "").upper() in ALLIED_HIGH_DRAIN_DEFENSE_IDS
        for item in candidates
    )
    if not allied_context:
        return candidates
    nasam_allowed = power_reserve_allows_nasam(
        power_surplus,
        power_stable=power_stable,
        planned_power_drain=planned_power_drain,
    )
    result = []
    for item in candidates:
        type_id = str(item.get("type_id") or "").upper()
        if type_id in ALLIED_HIGH_DRAIN_DEFENSE_IDS and not nasam_allowed:
            continue
        if not nasam_allowed and profile_class(item) == "defense":
            # Before the reserve is established, Allied production must stay on
            # the low-drain machine-gun/fixed-gun pair.
            if type_id not in ALLIED_EARLY_DEFENSE_IDS:
                continue
        result.append(item)
    return result


def profile_class(profile: dict[str, Any]) -> str:
    if is_power_profile(profile):
        return "power"
    return str(profile.get("building_class") or "other")


def usable_building_profile(profile: dict[str, Any]) -> bool:
    if profile.get("kind") != "building":
        return False
    if profile.get("is_ai_only") is True:
        return False
    if int(profile.get("tech_level") or 0) < 0:
        return False
    return profile_class(profile) not in {
        "map_object", "construction_yard", "naval_production"
    }


def placement_candidates(
    anchor: dict[str, int], *, purpose: str = "other",
    ore_cells: Iterable[dict[str, Any]] = (),
    enemy_cells: Iterable[dict[str, Any]] = (),
    occupied_cells: Iterable[dict[str, Any]] = (),
    limit: int = 512,
) -> list[dict[str, int]]:
    """Generate a deterministic, scored search wider than 200 placements."""
    anchor = {"x": int(anchor["x"]), "y": int(anchor["y"])}
    ore = [cell for cell in ore_cells if isinstance(cell, dict) and "x" in cell and "y" in cell]
    enemies = [cell for cell in enemy_cells if isinstance(cell, dict) and "x" in cell and "y" in cell]
    occupied = [cell for cell in occupied_cells if isinstance(cell, dict) and "x" in cell and "y" in cell]
    raw: list[dict[str, int]] = []
    # Every ring contributes its perimeter rather than a few cardinal points.
    # Include the tight rings required by PowersUpBuilding/plug structures.
    # Native validation still decides the final legal footprint.
    for radius in range(1, 33):
        for dx in range(-radius, radius + 1):
            raw.append({"x": anchor["x"] + dx, "y": anchor["y"] - radius})
            raw.append({"x": anchor["x"] + dx, "y": anchor["y"] + radius})
        for dy in range(-radius + 1, radius):
            raw.append({"x": anchor["x"] - radius, "y": anchor["y"] + dy})
            raw.append({"x": anchor["x"] + radius, "y": anchor["y"] + dy})

    unique = {(cell["x"], cell["y"]): cell for cell in raw}

    def score(cell: dict[str, int]) -> tuple[float, int, int]:
        anchor_distance = math.sqrt(distance_sq(cell, anchor))
        occupied_penalty = sum(
            max(0.0, 5.0 - math.sqrt(distance_sq(cell, other))) * 80.0
            for other in occupied
        )
        ore_distance = min(
            (math.sqrt(distance_sq(cell, other)) for other in ore), default=20.0
        )
        enemy_distance = min(
            (math.sqrt(distance_sq(cell, other)) for other in enemies), default=30.0
        )
        if purpose == "resource":
            value = abs(ore_distance - 6.0) * 18.0 + anchor_distance * 1.5
        elif purpose == "defense":
            # Prefer the perimeter facing a visible enemy, while remaining
            # close enough to construction range for the native validator.
            value = enemy_distance * 2.0 + abs(anchor_distance - 10.0) * 7.0
        elif purpose == "power":
            value = abs(anchor_distance - 5.0) * 8.0 + ore_distance * -0.2
        elif purpose == "technology":
            # Keep technology and superweapon footprints behind the economy
            # line and away from the next refinery/harvester route.
            ore_block_penalty = max(0.0, 9.0 - ore_distance) * 35.0
            value = abs(anchor_distance - 9.0) * 7.0 + ore_block_penalty
        elif purpose == "plug":
            value = anchor_distance * 2.0 + occupied_penalty
        else:
            value = abs(anchor_distance - 7.0) * 6.0 + ore_distance * -0.1
        return value + occupied_penalty, cell["y"], cell["x"]

    return sorted(unique.values(), key=score)[:max(1, int(limit))]


def upgrade_host_candidates(
    buildings: Iterable[dict[str, Any]],
    parent_type_id: str,
    used_host_ids: Iterable[int] = (),
) -> list[dict[str, int]]:
    """Return exact native host objects for a PowersUpBuilding placement."""
    parent = str(parent_type_id).strip().upper()
    used = {int(value) for value in used_host_ids if isinstance(value, int)}
    candidates = []
    for building in buildings:
        object_id = building.get("object_id")
        cell = building.get("cell")
        if (
            str(building.get("type_id") or "").upper() != parent
            or not isinstance(object_id, int)
            or object_id in used
            or (
                isinstance(building.get("upgrade_occupied_count"), int)
                and int(building["upgrade_occupied_count"]) >= 1
            )
            or not isinstance(cell, dict)
            or not isinstance(cell.get("x"), int)
            or not isinstance(cell.get("y"), int)
        ):
            continue
        candidates.append({
            "x": int(cell["x"]),
            "y": int(cell["y"]),
            "target_object_id": object_id,
        })
    return sorted(candidates, key=lambda item: item["target_object_id"])


def upgrade_deficit(
    profile: dict[str, Any], entities: Iterable[dict[str, Any]],
    used_host_ids: Iterable[int] = (),
) -> int | None:
    """Count visible hosts that still need this plugin.

    A missing occupied-slot field is not evidence that no upgrade is needed.
    The native bridge may publish the host object one poll before its capability
    bits, so unseen slots are treated as empty until a successful placement has
    explicitly reserved that host.
    """
    parent = str(profile.get("powers_up_building") or "").strip().upper()
    if not parent:
        return 0
    hosts = [
        item for item in entities
        if isinstance(item, dict)
        and item.get("kind") == "building"
        and str(item.get("type_id") or "").upper() == parent
    ]
    if not hosts:
        return 0
    used = {int(value) for value in used_host_ids if isinstance(value, int)}
    deficit = 0
    for host in hosts:
        occupied = host.get("upgrade_occupied_count")
        if isinstance(occupied, int):
            if occupied < 1:
                deficit += 1
        elif isinstance(host.get("object_id"), int) and host["object_id"] not in used:
            deficit += 1
    return deficit


def filter_upgrade_profiles(
    profiles: list[dict[str, Any]], entities: Iterable[dict[str, Any]],
    committed: dict[str, int],
    used_upgrade_hosts: dict[str, list[int]] | None = None,
) -> list[dict[str, Any]]:
    """Hide plug products once every currently visible host is already upgraded."""
    result = []
    used_by_type = used_upgrade_hosts or {}
    for profile in profiles:
        type_id = str(profile.get("type_id") or "").upper()
        if not profile.get("powers_up_building"):
            result.append(profile)
            continue
        deficit = upgrade_deficit(profile, entities, used_by_type.get(type_id, ()))
        if deficit is None or int(committed.get(type_id, 0)) < deficit:
            result.append(profile)
    return result


def choose_needed_upgrade(
    profiles: list[dict[str, Any]], entities: Iterable[dict[str, Any]],
    committed: dict[str, int], used_upgrade_hosts: dict[str, list[int]] | None = None,
) -> dict[str, Any] | None:
    """Choose an available plug whenever a real host slot is still unfilled."""
    used_by_type = used_upgrade_hosts or {}
    candidates = []
    for profile in profiles:
        if not profile.get("powers_up_building"):
            continue
        type_id = str(profile.get("type_id") or "").upper()
        deficit = upgrade_deficit(profile, entities, used_by_type.get(type_id, ()))
        if deficit is not None and deficit > int(committed.get(type_id, 0)):
            candidates.append((deficit, int(profile.get("cost") or 0), type_id, profile))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], -item[1], item[2]))[3]


def choose_building(
    available_profiles: list[dict[str, Any]],
    owned_counts: dict[str, int],
    committed_counts: dict[str, int],
    *, power_surplus: int | None,
    defense_target: int,
    power_stable: bool = False,
    planned_power_drain: int = 0,
) -> tuple[dict[str, Any], str] | None:
    candidates = [profile for profile in available_profiles if usable_building_profile(profile)]
    if not candidates:
        return None

    def effective(type_id: str) -> int:
        return int(owned_counts.get(type_id, 0)) + int(committed_counts.get(type_id, 0))

    power = [profile for profile in candidates if profile_class(profile) == "power"]
    effective_power = sum(
        effective(str(profile.get("type_id") or "").upper())
        for profile in power
    )
    if power and (
        (power_surplus is not None and power_surplus < 250)
        or (power_surplus is None and effective_power == 0)
    ):
        return min(power, key=lambda item: (int(item.get("cost") or 0), str(item["type_id"]))), "维持电力余量"

    for building_class, target in ESSENTIAL_CLASS_TARGETS.items():
        matching = [profile for profile in candidates if profile_class(profile) == building_class]
        if not matching:
            continue
        current = sum(effective(str(profile["type_id"]).upper()) for profile in matching)
        if current < target:
            return min(
                matching,
                key=lambda item: (int(item.get("tech_level") or 0), int(item.get("cost") or 0), str(item["type_id"])),
            ), f"补齐{building_class}"

    # After essentials, place one of every genuinely buildable structure. New
    # prerequisite unlocks are reconsidered every watcher tick.
    unbuilt = [
        profile for profile in candidates
        if profile_class(profile) != "defense"
        and effective(str(profile["type_id"]).upper()) == 0
    ]
    if unbuilt:
        return min(
            unbuilt,
            key=lambda item: (
                CLASS_PRIORITY.get(profile_class(item), 75),
                int(item.get("tech_level") or 0), str(item["type_id"]),
            ),
        ), "扩展已解锁建筑"

    defenses = [
        profile for profile in candidates
        if profile_class(profile) == "defense" and profile.get("combat_relevant") is True
    ]
    defenses = filter_defense_profiles_for_power(
        defenses,
        power_surplus=power_surplus,
        power_stable=power_stable,
        planned_power_drain=planned_power_drain,
    )
    defense_count = sum(
        effective(str(profile["type_id"]).upper()) for profile in defenses
    )
    if defenses and defense_count < max(0, int(defense_target)):
        defenses.sort(key=lambda item: (
            effective(str(item["type_id"]).upper()),
            int(item.get("cost") or 0), str(item["type_id"]),
        ))
        return defenses[0], "建立基地防御圈"
    return None


def choose_anti_infantry_defense(
    available_profiles: list[dict[str, Any]],
    owned_counts: dict[str, int],
    committed_counts: dict[str, int],
    enemy_combat_infantry: int,
    *, threshold: int = 25, power_surplus: int | None = None,
    power_stable: bool = False, planned_power_drain: int = 0,
) -> tuple[dict[str, Any], str] | None:
    """Scale Allied pillboxes against aggregate visible combat-infantry pressure."""
    pressure = max(0, int(enemy_combat_infantry))
    if pressure < max(1, int(threshold)):
        return None
    eligible = filter_defense_profiles_for_power(
        available_profiles,
        power_surplus=power_surplus,
        power_stable=power_stable,
        planned_power_drain=planned_power_drain,
    )
    pillbox = next(
        (
            profile for profile in eligible
            if str(profile.get("type_id") or "").upper() == "GAPILL"
            and profile.get("kind") == "building"
        ),
        None,
    )
    if pillbox is None:
        return None
    target = min(6, 1 + (pressure - int(threshold)) // 15)
    current = int(owned_counts.get("GAPILL", 0)) + int(
        committed_counts.get("GAPILL", 0)
    )
    if current >= target:
        return None
    return pillbox, (
        f"visible enemy combat infantry pressure {pressure} requires "
        f"{target} machine-gun pillbox(es)"
    )


def choose_adaptive_defense(
    available_profiles: list[dict[str, Any]],
    owned_counts: dict[str, int],
    committed_counts: dict[str, int],
    enemy_threat: dict[str, Any],
    *,
    under_attack: bool,
    credits: int | None,
    power_surplus: int | None = None,
    power_stable: bool = False,
    planned_power_drain: int = 0,
) -> tuple[dict[str, Any], str] | None:
    """Build real static defence under pressure and during a rich economy."""
    defenses = [
        profile for profile in available_profiles
        if usable_building_profile(profile)
        and profile_class(profile) == "defense"
        and profile.get("combat_relevant") is True
    ]
    defenses = filter_defense_profiles_for_power(
        defenses,
        power_surplus=power_surplus,
        power_stable=power_stable,
        planned_power_drain=planned_power_drain,
    )
    if not defenses:
        return None
    combat_units = max(0, int(enemy_threat.get("combat_units", 0) or 0))
    if under_attack:
        target = min(8, max(2, 1 + math.ceil(combat_units / 5)))
        trigger = "base is under attack"
    elif isinstance(credits, int) and credits >= 20000:
        target = 4
        trigger = "rich economy static-defence reserve"
    elif isinstance(credits, int) and credits >= 8000:
        target = 2
        trigger = "surplus economy static-defence reserve"
    else:
        return None
    current = sum(
        int(owned_counts.get(str(profile.get("type_id") or "").upper(), 0))
        + int(committed_counts.get(str(profile.get("type_id") or "").upper(), 0))
        for profile in defenses
    )
    if current >= target:
        return None
    infantry = max(0, int(enemy_threat.get("combat_infantry", 0) or 0))
    armor = max(0, int(enemy_threat.get("armored", 0) or 0))
    air = max(0, int(enemy_threat.get("air", 0) or 0))

    def counter_score(profile: dict[str, Any]) -> tuple[int, int, int, int, str]:
        type_id = str(profile.get("type_id") or "").upper()
        existing = int(owned_counts.get(type_id, 0)) + int(
            committed_counts.get(type_id, 0)
        )
        score = 0
        score += infantry * (4 if profile.get("anti_infantry") is True else 0)
        score += armor * (5 if profile.get("anti_armor") is True else 0)
        score += air * (6 if profile.get("anti_air") is True else 0)
        allied_core_order = {"GAPILL": 0, "GAGUN": 1}
        return (
            existing,
            -score,
            allied_core_order.get(type_id, 2),
            int(profile.get("cost") or 0),
            type_id,
        )

    selected = min(defenses, key=counter_score)
    return selected, f"{trigger}; maintain {target} adaptive defence building(s)"


def choose_defense_force(
    available_profiles: list[dict[str, Any]],
    owned_counts: dict[str, int],
    committed_counts: dict[str, int],
    targets: dict[str, int],
) -> tuple[dict[str, Any], int] | None:
    for kind in ("infantry", "vehicle", "aircraft"):
        target = max(0, int(targets.get(kind, 0)))
        profiles = [
            profile for profile in available_profiles
            if profile.get("kind") == kind
            and profile.get("combat_relevant") is True
            and profile.get("is_ai_only") is not True
            and int(profile.get("tech_level") or 0) >= 0
        ]
        if not profiles:
            continue
        current = sum(
            int(owned_counts.get(str(profile["type_id"]).upper(), 0))
            + int(committed_counts.get(str(profile["type_id"]).upper(), 0))
            for profile in profiles
        )
        if current >= target:
            continue
        profile = min(
            profiles,
            key=lambda item: (int(item.get("cost") or 0), int(item.get("tech_level") or 0), str(item["type_id"])),
        )
        return profile, min(10, target - current)
    return None
