from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .video_strategy_knowledge import us_convoy_roles


# The order is intentionally expressed in native type IDs. Display-name matching is
# too ambiguous for a long-running build and previously allowed prerequisite skips.
US_BUILD_ORDER: tuple[tuple[str, int], ...] = (
    ("GAPILE", 1), ("GAREFN", 1), ("GAPOWR", 1), ("GAWEAP", 1),
    ("GAREFN", 2), ("GAPOWR", 2), ("GAAIRC", 1), ("GAMERC", 1),
    ("GAPOWRUP", 1), ("GAPOWRUP", 2), ("GAPOWR", 3),
    ("GAAIRC_D", 1), ("GATECH", 1), ("GAOREP", 1),
    ("GAPOWRUP", 3), ("GADEPO", 1), ("GAPOWR", 4),
    ("GAPOWRUP", 4), ("GACLAB", 1), ("GAWEAP", 2),
    ("GAPOWR", 5), ("GAPOWRUP", 5), ("GACSPH", 1),
    ("GAWEAP", 3), ("GAPOWR", 6), ("GAPOWRUP", 6), ("GAWEAT", 1),
)

OPENING_FORCE: tuple[tuple[str, int, str], ...] = (
    ("ADOG", 5, "5 allied dogs for map scouting"),
    ("E1", 15, "15 GIs for opening defense"),
    ("CMIN", 5, "5 miners after the first war factory"),
    ("FV", 15, "15 IFVs for the Tanya strike force"),
    ("TANY", 1, "Tanya strike-force core"),
    ("ENGINEER", 4, "four fixed engineer IFVs for convoy support"),
    ("GGI", 5, "five fixed Guardian GI IFVs for anti-armor fire"),
    ("GHOST", 5, "five Navy SEAL IFVs; never substitute surplus GIs"),
    ("RIOT", 5, "external Riot Trooper screen for anti-infantry contact"),
    ("ABRM", 3, "external Abrams armored core"),
    ("ETNK", 1, "external Bulldog escort"),
    ("ROBO", 2, "external Robot Tank flank screen"),
)

ROLLING_T3: tuple[tuple[str, int], ...] = (
    ("GGI", 5), ("RIOT", 1),
    ("FV", 5), ("ABRM", 3), ("ETNK", 1), ("ROBO", 2),
)

VARIATIONS: tuple[dict[str, int], ...] = (
    {},
    {"RIOT": 1},             # anti-infantry trial
    {"ABRM": 1, "ROBO": 1}, # armored pressure trial
    {"STORM": 2},            # air-pressure trial
)

PRODUCTION_LANES: tuple[str, ...] = (
    "building", "support_upgrade", "infantry", "vehicle", "aircraft", "naval",
)

# These sidebar items use a lane independent from ordinary structures. If one
# is temporarily unavailable, it must not freeze the main T2/T3 build chain.
PARALLEL_SUPPORT_STEPS = {"GAPOWRUP", "GAAIRC_D"}


def choose_us_convoy_requirements(enemy_threat: dict[str, Any] | None = None) -> tuple[tuple[str, int, str], ...]:
    """Return the fixed pre-departure IFV loadout.

    Threat observations change target choice and external reinforcement, not
    passengers during contact.  In particular, opening-defense GIs are never
    consumed by this T3 strike force when Navy SEALs are available.
    """
    _ = enemy_threat, us_convoy_roles()
    return (
        ("TANY", 1, "command"),
        ("ENGINEER", 4, "repair_support"),
        ("GGI", 5, "anti_armor"),
        ("GHOST", 5, "anti_infantry_assault"),
    )


def choose_us_convoy_escort_requirements(
    enemy_threat: dict[str, Any] | None = None,
) -> tuple[tuple[str, int, str], ...]:
    """Return units that fight beside the IFVs and are never loaded into them."""
    threat = enemy_threat if isinstance(enemy_threat, dict) else {}
    infantry = int(threat.get("combat_infantry", 0) or 0)
    # Five is the normal screen; mass infantry receives a larger external
    # screen without disturbing the already-loaded vehicles.
    riot_count = 8 if infantry >= 25 else 5
    return (
        ("RIOT", riot_count, "anti_infantry_screen"),
        ("ABRM", 3, "armored_core"),
        ("ETNK", 1, "mobile_escort"),
        ("ROBO", 2, "flank_screen"),
    )


@dataclass(frozen=True)
class USDecision:
    type_id: str
    quantity: int
    reason: str
    category: str


def _effective(type_id: str, owned: dict[str, int], committed: dict[str, int]) -> int:
    return int(owned.get(type_id, 0)) + int(committed.get(type_id, 0))


def production_lane(profile: dict[str, Any]) -> str:
    """Map a buildable profile to MO's independent production lane."""
    kind = str(profile.get("kind") or "unknown").casefold()
    role = str(profile.get("role") or "").casefold()
    building_class = str(profile.get("building_class") or "").casefold()
    if kind == "building":
        if profile.get("powers_up_building"):
            return "support_upgrade"
        return "building"
    if kind == "vehicle":
        flags = profile.get("flags") if isinstance(profile.get("flags"), dict) else {}
        movement = str(profile.get("movement_zone") or "").casefold()
        if (
            "naval" in role
            or flags.get("naval") is True
            or flags.get("waterbound") is True
            or movement in {"water", "waterbeach"}
        ):
            return "naval"
        return "vehicle"
    if kind in {"infantry", "aircraft"}:
        return kind
    return kind


def profiles_for_idle_lanes(
    profiles: list[dict[str, Any]], busy_lanes: set[str]
) -> list[dict[str, Any]]:
    """Keep sidebar entries whose native production lane is currently idle."""
    blocked = set(busy_lanes)
    return [profile for profile in profiles if production_lane(profile) not in blocked]


def cooling_production_lanes(
    last_action_at: dict[str, Any], now: float, cooldown: float = 0.5
) -> set[str]:
    """Return only recently-commanded lanes; unrelated lanes remain runnable."""
    return {
        str(lane) for lane, at in last_action_at.items()
        if isinstance(at, (int, float)) and now - float(at) < cooldown
    }


def choose_us_decision(
    available_profiles: list[dict[str, Any]],
    owned: dict[str, int],
    committed: dict[str, int],
    *,
    credits: int | None,
    economy_growing: bool,
    surplus_available: bool | None = None,
    variation_index: int = 0,
    combat_doctrine_rule: dict[str, Any] | None = None,
) -> tuple[USDecision | None, str | None]:
    """Choose one exact US action and report the first blocked build step.

    A locked build step is never skipped. Unit production remains eligible while
    prerequisites or funds are being acquired.
    """
    available = {
        str(profile.get("type_id", "")).upper(): profile
        for profile in available_profiles if profile.get("type_id")
    }
    blocked_build: str | None = None
    build_decision: USDecision | None = None
    for type_id, target in US_BUILD_ORDER:
        if _effective(type_id, owned, committed) >= target:
            continue
        if type_id in available:
            build_decision = USDecision(
                type_id, 1, f"US build order: {type_id} {target}",
                str(available[type_id].get("kind") or "building"),
            )
            break
        if type_id in PARALLEL_SUPPORT_STEPS:
            # Keep looking for the next ordinary structure. The support lane
            # will fill this item as soon as its native prerequisite appears.
            continue
        blocked_build = type_id
        break

    # Main/support construction has first choice only on its own idle lane.
    # On the following polling tick that lane is filtered as busy, allowing
    # infantry, vehicle and aircraft production to proceed concurrently.
    if build_decision is not None:
        return build_decision, None

    # Opening force gates follow the user's timing rather than generic cheap-unit choice.
    gates = {
        "ADOG": owned.get("GAPILE", 0) >= 1,
        "E1": owned.get("GAPILE", 0) >= 1,
        "CMIN": owned.get("GAWEAP", 0) >= 1,
        "FV": owned.get("GAWEAP", 0) >= 1,
        "TANY": owned.get("GATECH", 0) >= 1,
        "ENGINEER": owned.get("GAWEAP", 0) >= 1,
        "GGI": owned.get("GAPILE", 0) >= 1,
        "GHOST": owned.get("GATECH", 0) >= 1,
        "RIOT": owned.get("GATECH", 0) >= 1,
        "ABRM": owned.get("GATECH", 0) >= 1,
        "ETNK": owned.get("GAWEAP", 0) >= 1,
        "ROBO": owned.get("GATECH", 0) >= 1,
    }
    for type_id, target, reason in OPENING_FORCE:
        if gates[type_id] and type_id in available and _effective(type_id, owned, committed) < target:
            deficit = target - _effective(type_id, owned, committed)
            return USDecision(type_id, min(5, deficit), reason, str(available[type_id].get("kind") or "unit")), blocked_build

    has_surplus = (
        bool(surplus_available)
        if surplus_available is not None
        else (credits is not None and credits >= 5000) or economy_growing
    )
    if not has_surplus:
        return None, blocked_build

    # Fill usable aircraft capacity and preserve one runway slot for Hummingbird support.
    air_capacity = 4 * int(owned.get("GAAIRC", 0)) + 4 * int(owned.get("GAHPAD", 0))
    if air_capacity:
        hbird_target = 1 if owned.get("GAHPAD", 0) else 0
        aircraft_total = sum(_effective(item, owned, committed) for item in ("STORM", "HBIRD"))
        if hbird_target and "HBIRD" in available and _effective("HBIRD", owned, committed) < hbird_target:
            return USDecision("HBIRD", 1, "reserve Hummingbird debuff support", "aircraft"), blocked_build
        if "STORM" in available and aircraft_total < max(0, air_capacity - hbird_target):
            return USDecision("STORM", 1, "fill available runway with Storm Child", "aircraft"), blocked_build

    if owned.get("GATECH", 0) < 1:
        # Before T3, spend surplus in the requested 2:1 GGI/GI ratio.
        candidates = (("GGI", 2), ("E1", 1))
        eligible = [(type_id, weight) for type_id, weight in candidates if type_id in available]
        if eligible:
            type_id, weight = min(
                eligible,
                key=lambda item: (_effective(item[0], owned, committed) / item[1], item[0]),
            )
            return USDecision(type_id, weight, "economic-surplus infantry ratio GGI:GI = 2:1", "infantry"), blocked_build
        return None, blocked_build

    # Translate the selected large-doctrine entry into real US build options.
    # This happens only after the fixed opening force and never serializes the
    # independent production lanes. Unknown roles are ignored instead of
    # inventing a unit ID that the native sidebar cannot build.
    rule = combat_doctrine_rule if isinstance(combat_doctrine_rule, dict) else {}
    role_units = {
        "area_anti_infantry": (("RIOT", 3),),
        "mobile_suppression": (("RIOT", 2), ("FV", 2)),
        "long_range_anti_infantry": (("GHOST", 3), ("RIOT", 2)),
        "anti_armor": (("GGI", 3), ("ABRM", 2)),
        "heavy_anti_armor": (("ABRM", 3), ("GGI", 2)),
        "heavy_armor": (("ABRM", 3),),
        "medium_armor": (("ABRM", 2), ("ROBO", 2)),
        "mobile_anti_air": (("GGI", 3), ("FV", 2)),
        "static_anti_air": (("GGI", 2),),
        "fighter": (("STORM", 2),),
        "fast_fighter": (("STORM", 2),),
        "air_strike": (("STORM", 2),),
        "fast_raider": (("FV", 3), ("ROBO", 2)),
        "fast_response": (("FV", 3), ("ROBO", 2)),
        "robotic": (("ROBO", 3),),
        "repair_support": (("FV", 2),),
        "combined_arms": (("ABRM", 3), ("FV", 3), ("GGI", 3), ("RIOT", 1)),
        "cheap_screen": (("GGI", 3), ("RIOT", 1)),
        "early_pressure": (("FV", 3), ("GGI", 3)),
        "mobile_reserve": (("ABRM", 2), ("FV", 2)),
        "screen": (("GGI", 2), ("RIOT", 1)),
        "disabler": (("HBIRD", 1),),
        "long_range": (("GGI", 3), ("ABRM", 2)),
    }
    doctrine_candidates: dict[str, int] = {}
    for role in rule.get("production_roles", []):
        for type_id, weight in role_units.get(str(role), ()):
            if type_id in available:
                doctrine_candidates[type_id] = max(doctrine_candidates.get(type_id, 0), weight)
    if doctrine_candidates:
        type_id, weight = min(
            doctrine_candidates.items(),
            key=lambda item: (_effective(item[0], owned, committed) / item[1], item[0]),
        )
        rolling_floor = weight * 2
        if _effective(type_id, owned, committed) < rolling_floor:
            return USDecision(
                type_id, min(3, rolling_floor - _effective(type_id, owned, committed)),
                f"combat doctrine {rule.get('id', 'selected')} counter role",
                str(available[type_id].get("kind") or "unit"),
            ), blocked_build

    targets = dict(ROLLING_T3)
    for type_id, extra in VARIATIONS[variation_index % len(VARIATIONS)].items():
        targets[type_id] = targets.get(type_id, 0) + extra
    eligible = [(type_id, weight) for type_id, weight in targets.items() if type_id in available]
    if not eligible:
        return None, blocked_build
    # Normalized minimum continuously starts another balanced combat group instead of
    # stopping at a fixed one-time target.
    type_id, weight = min(
        eligible,
        key=lambda item: (_effective(item[0], owned, committed) / item[1], item[0]),
    )
    return USDecision(type_id, min(5, weight), "rolling US T3 combat-group composition", str(available[type_id].get("kind") or "unit")), blocked_build
