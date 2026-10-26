from __future__ import annotations

from typing import Any, Callable, Iterable


FACTION_OWNERS = {
    "allied": {"Europeans", "UnitedStates", "Pacific"},
    "soviet": {"USSR", "Latin", "Chinese"},
    "epsilon": {"PsiCorps", "Headquaters", "ScorpionCell"},
    "foehn": {"Guild1", "Guild2", "Guild3"},
}

AREA_ANTI_INFANTRY_TOKENS = (
    "grenade", "mortar", "flame", "fire", "riot", "sonic", "radiation",
    "desol", "toxic", "poison", "gas", "nuke", "shockwave", "thermobaric",
    "pyro", "seismic", "earthquake",
)
ANTI_AIR_TOKENS = (
    "flak", "sam", "antiair", "airburst", "heatseeker",
)


def _percent(value: Any) -> float:
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return 0.0


def _average(values: list[Any]) -> float:
    return sum(_percent(value) for value in values) / len(values) if values else 0.0


def _factions(owners: Iterable[Any]) -> list[str]:
    owner_set = {str(value) for value in owners}
    return [
        faction for faction, members in FACTION_OWNERS.items()
        if owner_set & members
    ]


def _tech_tier(tech_level: Any) -> int | None:
    if isinstance(tech_level, bool) or not isinstance(tech_level, (int, float)):
        return None
    if tech_level < 0:
        return None
    if tech_level <= 4:
        return 1
    if tech_level <= 7:
        return 2
    return 3


def _is_anti_air_weapon(weapon: dict[str, Any]) -> bool:
    values = [
        str(weapon.get(key) or "")
        for key in ("id", "projectile", "warhead")
    ]
    lowered = " ".join(values).casefold()
    if any(token in lowered for token in ANTI_AIR_TOKENS):
        return True
    for value in values:
        upper = value.upper()
        if upper.startswith("AA") or upper.endswith(("AA", "AAE")):
            return True
    return False


def classify_unit_threat(record: dict[str, Any]) -> dict[str, Any]:
    """Derive Wiki-style tier, role and threat tags from the registered rules data."""
    kind = str(record.get("kind") or "unknown").casefold()
    role = str(record.get("role") or "support_unit")
    flags = record.get("flags") if isinstance(record.get("flags"), dict) else {}
    if kind == "aircraft" or role in {"combat_aircraft", "support_aircraft"}:
        battlefield_class = "air"
    elif role in {"combat_naval", "support_naval"} or flags.get("naval") is True:
        battlefield_class = "naval"
    elif kind == "vehicle":
        battlefield_class = "armored"
    elif kind == "infantry":
        battlefield_class = "infantry"
    elif kind == "building":
        battlefield_class = "structure"
    else:
        battlefield_class = "support"

    anti_infantry = False
    anti_armor = False
    anti_structure = False
    anti_air = False
    area_anti_infantry = False
    peak_weapon_score = 0.0
    for weapon in record.get("weapons") or ():
        if not isinstance(weapon, dict) or weapon.get("offensive") is not True:
            continue
        details = weapon.get("warhead_details")
        verses = details.get("verses", []) if isinstance(details, dict) else []
        infantry_effect = _average(list(verses[:3]))
        armor_effect = _average(list(verses[3:6]))
        structure_effect = _average(list(verses[6:9]))
        anti_infantry = anti_infantry or infantry_effect >= 55.0
        anti_armor = anti_armor or armor_effect >= 55.0
        anti_structure = anti_structure or structure_effect >= 45.0
        weapon_text = " ".join(
            str(weapon.get(key) or "")
            for key in ("id", "projectile", "warhead")
        ).casefold()
        anti_air = anti_air or _is_anti_air_weapon(weapon)
        area_anti_infantry = area_anti_infantry or (
            str(weapon.get("areafire") or "").casefold() in {"yes", "true", "1"}
            or any(token in weapon_text for token in AREA_ANTI_INFANTRY_TOKENS)
        ) and infantry_effect >= 40.0
        damage = max(0.0, float(weapon.get("damage") or 0))
        burst = max(1.0, float(weapon.get("burst") or 1))
        rof = max(1.0, float(weapon.get("rof") or 60))
        attack_range = max(0.0, float(weapon.get("range") or 0))
        peak_weapon_score = max(
            peak_weapon_score,
            (damage * burst / rof) * (1.0 + min(attack_range, 20.0) / 20.0),
        )

    tier = _tech_tier(record.get("tech_level"))
    strength = max(0.0, float(record.get("strength") or 0))
    cost = max(0.0, float(record.get("cost") or 0))
    threat_score = round(
        (tier or 0) * 12.0
        + min(30.0, strength / 100.0)
        + min(18.0, cost / 250.0)
        + min(35.0, peak_weapon_score * 4.0)
        + (8.0 if area_anti_infantry else 0.0)
        + (5.0 if anti_air else 0.0),
        2,
    )
    threat_level = (
        "critical" if threat_score >= 75
        else "high" if threat_score >= 50
        else "medium" if threat_score >= 28
        else "low"
    )
    tags = [
        label for enabled, label in (
            (anti_infantry, "anti_infantry"),
            (anti_armor, "anti_armor"),
            (anti_structure, "anti_structure"),
            (anti_air, "anti_air"),
            (area_anti_infantry, "area_anti_infantry"),
        ) if enabled
    ]
    return {
        "factions": _factions(record.get("owners") or ()),
        "tech_tier": tier,
        "battlefield_class": battlefield_class,
        "threat_level": threat_level,
        "threat_score": threat_score,
        "threat_tags": tags,
        "anti_infantry": anti_infantry,
        "anti_armor": anti_armor,
        "anti_structure": anti_structure,
        "anti_air": anti_air,
        "area_anti_infantry": area_anti_infantry,
        "classification_basis": "game_rules_with_mental_omega_wiki_role_schema",
    }


def summarize_visible_enemy_threat(
    entities: Iterable[dict[str, Any]],
    profile_lookup: Callable[[str], dict[str, Any] | None],
) -> dict[str, Any]:
    summary = {
        "combat_units": 0,
        "combat_infantry": 0,
        "armored": 0,
        "air": 0,
        "area_anti_infantry": 0,
        "threat_score": 0.0,
        "by_faction": {},
    }
    for entity in entities:
        if (
            not isinstance(entity, dict)
            or entity.get("relation") != "enemy"
            or entity.get("alive") is not True
            or entity.get("visible") is not True
        ):
            continue
        profile = profile_lookup(str(entity.get("type_id") or "")) or {}
        if profile.get("combat_relevant") is not True:
            continue
        summary["combat_units"] += 1
        battlefield_class = str(profile.get("battlefield_class") or "")
        if profile.get("kind") == "infantry" and profile.get("role") == "combat_infantry":
            summary["combat_infantry"] += 1
        if battlefield_class == "armored":
            summary["armored"] += 1
        if battlefield_class == "air":
            summary["air"] += 1
        if profile.get("area_anti_infantry") is True:
            summary["area_anti_infantry"] += 1
        summary["threat_score"] += float(profile.get("threat_score") or 0.0)
        for faction in profile.get("factions") or ():
            by_faction = summary["by_faction"]
            by_faction[faction] = int(by_faction.get(faction, 0)) + 1
    summary["threat_score"] = round(summary["threat_score"], 2)
    return summary
