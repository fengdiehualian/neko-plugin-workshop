from __future__ import annotations

from typing import Any


RULES = (
    {"id": "protect_harvester_critical", "priority": 140, "trigger": "harvester", "reserve_release": 1.0, "pursuit_radius": 10, "actions": ["retreat_harvester", "intercept_attacker", "screen_resource_route"]},
    {"id": "repair_critical_building", "priority": 135, "trigger": "building_critical", "reserve_release": 0.75, "pursuit_radius": 12, "actions": ["enable_native_repair", "intercept_attacker", "hold_production_area"]},
    {"id": "repair_damaged_building", "priority": 115, "trigger": "building_damaged", "reserve_release": 0.25, "pursuit_radius": 10, "actions": ["enable_native_repair", "keep_defender_screen"]},
    {"id": "counter_air_breach", "priority": 130, "trigger": "air", "reserve_release": 1.0, "pursuit_radius": 16, "actions": ["release_mobile_anti_air", "spread_valuable_units", "focus_bomber_first"]},
    {"id": "counter_armor_breach", "priority": 125, "trigger": "armor", "reserve_release": 0.8, "pursuit_radius": 12, "actions": ["release_anti_armor", "hold_crossfire", "protect_repair_support"]},
    {"id": "counter_infantry_swarm", "priority": 120, "trigger": "infantry_swarm", "reserve_release": 0.7, "pursuit_radius": 10, "actions": ["release_area_anti_infantry", "avoid_infantry_blob", "hold_choke"]},
    {"id": "outnumbered_local_defense", "priority": 118, "trigger": "outnumbered", "reserve_release": 1.0, "pursuit_radius": 8, "actions": ["release_all_reserve", "delay_near_defense", "regroup_wounded"]},
    {"id": "balanced_local_defense", "priority": 90, "trigger": "contact", "reserve_release": 0.5, "pursuit_radius": 10, "actions": ["defenders_intercept", "reserve_hold_second_line"]},
    {"id": "quiet_base_guard", "priority": 10, "trigger": "quiet", "reserve_release": 0.0, "pursuit_radius": 8, "actions": ["guard_economy", "repair_damage", "preserve_reserve"]},
)

# These are executable contact doctrines, not aliases. Each profile changes
# force release, spacing, pursuit, retreat and recovery behaviour by situation.
TACTICAL_PROFILES = {
    "harvester": (140, 1.0, 10, ["retreat_harvester", "intercept_attacker", "screen_resource_route"]),
    "building_critical": (138, 0.85, 12, ["enable_native_repair", "intercept_attacker", "hold_production_area"]),
    "building_damaged": (116, 0.30, 10, ["enable_native_repair", "keep_defender_screen"]),
    "power_emergency": (136, 0.80, 12, ["repair_power_first", "protect_power_grid", "counter_attack_source"]),
    "production_breach": (132, 0.75, 14, ["repair_production", "hold_factory_ring", "counter_attack_source"]),
    "air": (132, 1.0, 18, ["release_mobile_anti_air", "spread_valuable_units", "focus_bomber_first"]),
    "armor": (128, 0.85, 13, ["release_anti_armor", "hold_crossfire", "protect_repair_support"]),
    "artillery": (134, 0.70, 20, ["flank_artillery", "avoid_counterbattery", "protect_frontline"]),
    "infantry_swarm": (124, 0.75, 11, ["release_area_anti_infantry", "avoid_infantry_blob", "hold_choke"]),
    "elite_infantry": (122, 0.55, 12, ["focus_elite", "screen_support_units", "deny_capture"]),
    "transport": (130, 1.0, 16, ["deny_landing_zone", "intercept_transport", "guard_economy"]),
    "stealth": (126, 0.65, 14, ["release_detectors", "protect_command_units", "focus_revealed_target"]),
    "outnumbered": (120, 1.0, 8, ["release_all_reserve", "delay_near_defense", "regroup_wounded"]),
    "contact": (92, 0.50, 11, ["defenders_intercept", "reserve_hold_second_line", "rotate_damaged_units"]),
    "quiet": (10, 0.0, 8, ["guard_economy", "repair_damage", "preserve_reserve"]),
}


def _expanded_rules() -> tuple[dict[str, Any], ...]:
    result = []
    for trigger, (priority, release, pursuit, actions) in TACTICAL_PROFILES.items():
        for force_ratio, retreat, regroup in (("behind", 0.55, 15), ("even", 0.40, 10), ("ahead", 0.28, 7)):
            for terrain in ("open", "chokepoint"):
                spacing = 3 if terrain == "open" else 1
                result.append({
                    "id": f"contact_{trigger}_{force_ratio}_{terrain}",
                    "priority": priority + (12 if force_ratio == "behind" else 0) + (5 if terrain == "chokepoint" else 0),
                    "trigger": trigger,
                    "force_ratio": force_ratio,
                    "terrain": terrain,
                    "reserve_release": min(1.0, release + (0.15 if force_ratio == "behind" else 0.0)),
                    "pursuit_radius": max(5, pursuit - (2 if force_ratio == "behind" else 0)),
                    "engagement_distance_cells": 8 if terrain == "chokepoint" else 12,
                    "retreat_health_ratio": retreat,
                    "regroup_seconds": regroup,
                    "frontline_fraction": 0.68 if terrain == "chokepoint" else 0.55,
                    "anti_armor_fraction": 0.45 if trigger in {"armor", "artillery"} else 0.18,
                    "anti_air_fraction": 0.45 if trigger == "air" else 0.12,
                    "max_pursuit_without_support": 6 if force_ratio == "behind" else 12,
                    "actions": list(actions),
                    "constraints": ["do_not_abandon_harvesters", "repair_before_replacing", "reassess_after_contact", "do_not_chase_beyond_pursuit_radius"],
                })
    return tuple(result)


RULES = RULES + _expanded_rules()


def select_contact_defense_rule(
    state: dict[str, Any], roles: dict[str, Any]
) -> dict[str, Any]:
    local = roles.get("local_threats", [])
    local = local if isinstance(local, list) else []
    anchors = roles.get("anchors", [])
    anchors = anchors if isinstance(anchors, list) else []
    harvesters = state.get("harvesters", [])
    harvesters = harvesters if isinstance(harvesters, list) else []
    defenders = roles.get("defenders", [])
    defenders = defenders if isinstance(defenders, list) else []
    harvester_threat = any(
        isinstance(item, dict) and item.get("nearby_threats")
        for item in roles.get("threatened_harvesters", [])
    )
    critical = any(float(item.get("health_ratio", 1.0)) < 0.55 for item in anchors)
    damaged = any(float(item.get("health_ratio", 1.0)) < 0.98 for item in anchors)
    air = sum(item.get("kind") == "aircraft" for item in local)
    armor = sum(item.get("kind") == "vehicle" for item in local)
    infantry = sum(item.get("kind") == "infantry" for item in local)
    artillery = sum(
        str(item.get("role") or "").casefold() in {"artillery", "siege"}
        or str(item.get("building_class") or "").casefold() in {"artillery", "siege"}
        for item in local
    )
    transport = sum(
        str(item.get("role") or "").casefold() in {"transport", "amphibious", "transport_drop"}
        or item.get("transport_capacity") is True
        for item in local
    )
    stealth = sum(item.get("stealth") is True or item.get("cloaked") is True for item in local)
    power_damage = any(
        str(item.get("building_class") or "").casefold() == "power"
        and float(item.get("health_ratio", 1.0)) < 0.8
        for item in anchors
    )
    production_damage = any(
        str(item.get("building_class") or "").casefold() in {"production", "vehicle_production", "aircraft_production", "infantry_production"}
        and float(item.get("health_ratio", 1.0)) < 0.8
        for item in anchors
    )
    trigger = "quiet"
    if harvester_threat:
        trigger = "harvester"
    elif power_damage:
        trigger = "power_emergency"
    elif production_damage:
        trigger = "production_breach"
    elif critical:
        trigger = "building_critical"
    elif transport:
        trigger = "transport"
    elif stealth:
        trigger = "stealth"
    elif air >= 3:
        trigger = "air"
    elif artillery:
        trigger = "artillery"
    elif armor >= 5:
        trigger = "armor"
    elif infantry >= 10:
        trigger = "infantry_swarm"
    elif len(local) > max(1, len(defenders)):
        trigger = "outnumbered"
    elif local:
        trigger = "contact"
    elif damaged:
        trigger = "building_damaged"
    own_power = max(1, len(defenders) + len(roles.get("reserve", [])) + len(roles.get("assault", [])))
    enemy_power = len(local) + artillery + transport
    force_ratio = "behind" if enemy_power > own_power * 1.2 else "ahead" if own_power > enemy_power * 2 else "even"
    map_state = state.get("map") if isinstance(state.get("map"), dict) else {}
    terrain = "chokepoint" if map_state.get("chokepoint") is True or map_state.get("terrain") == "chokepoint" else "open"
    selected = next(
        (rule for rule in RULES if rule["trigger"] == trigger and rule.get("force_ratio") == force_ratio and rule.get("terrain") == terrain),
        next(rule for rule in RULES if rule["trigger"] == trigger),
    )
    return {
        **selected,
        "observed": {
            "local_enemy_count": len(local),
            "defender_count": len(defenders),
            "air_count": air,
            "armor_count": armor,
            "infantry_count": infantry,
            "harvester_count": len(harvesters),
            "force_ratio": force_ratio,
            "terrain": terrain,
            "artillery_count": artillery,
            "transport_count": transport,
            "stealth_count": stealth,
        },
    }
