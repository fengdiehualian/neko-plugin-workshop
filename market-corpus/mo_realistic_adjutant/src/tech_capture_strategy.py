from __future__ import annotations

from typing import Any


ENGINEER_TYPE_IDS = ("ENGINEER", "SENGINEER", "YENGINEER", "FENGINEER")
COMBAT_KINDS = {"infantry", "vehicle", "aircraft"}


def _distance_sq(a: dict[str, Any], b: dict[str, Any]) -> int:
    acell, bcell = a.get("cell"), b.get("cell")
    if not isinstance(acell, dict) or not isinstance(bcell, dict):
        return 2**31 - 1
    return (int(acell["x"]) - int(bcell["x"])) ** 2 + (
        int(acell["y"]) - int(bcell["y"])
    ) ** 2


def capturable_neutral_tech_buildings(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return visible, alive neutral tech landmarks not already owned by an ally."""
    relations = {
        item.get("object_id"): item.get("relation")
        for item in state.get("visible_entities", [])
        if isinstance(item, dict)
    }
    entities = {
        item.get("object_id"): item
        for item in state.get("visible_entities", [])
        if isinstance(item, dict)
    }
    result = []
    for landmark in state.get("landmarks", []):
        if not isinstance(landmark, dict) or landmark.get("kind") != "tech_building":
            continue
        object_id = landmark.get("object_id")
        entity = entities.get(object_id, {})
        relation = relations.get(object_id)
        if relation != "neutral" or landmark.get("alive") is not True:
            continue
        if entity and (entity.get("visible") is not True or entity.get("alive") is not True):
            continue
        result.append({**landmark, **entity, "relation": "neutral"})
    return sorted(result, key=lambda item: int(item.get("object_id", 0)))


def owned_engineers(state: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for item in state.get("visible_entities", []):
        if not isinstance(item, dict) or item.get("relation") != "self":
            continue
        if item.get("alive") is not True or item.get("controllable") is not True:
            continue
        type_id = str(item.get("type_id") or "").upper()
        if item.get("is_engineer") is True or type_id in ENGINEER_TYPE_IDS:
            result.append(item)
    return sorted(result, key=lambda item: int(item.get("object_id", 0)))


def capture_target_is_safe(
    state: dict[str, Any], target: dict[str, Any], radius: int = 10,
) -> bool:
    radius_sq = max(4, int(radius)) ** 2
    enemies = 0
    escorts = 0
    for item in state.get("visible_entities", []):
        if not isinstance(item, dict) or item.get("alive") is not True:
            continue
        if item.get("kind") not in COMBAT_KINDS or _distance_sq(item, target) > radius_sq:
            continue
        if item.get("relation") == "enemy":
            enemies += 1
        elif item.get("relation") == "self" and item.get("is_engineer") is not True:
            escorts += 1
    return enemies == 0 or escorts >= enemies * 2


def choose_capture_assignment(
    state: dict[str, Any], targets: list[dict[str, Any]],
    engineers: list[dict[str, Any]], assigned_engineers: set[int] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    used = assigned_engineers or set()
    available = [
        item for item in engineers
        if isinstance(item.get("object_id"), int) and int(item["object_id"]) not in used
    ]
    safe_targets = [item for item in targets if capture_target_is_safe(state, item)]
    if not available or not safe_targets:
        return None
    pairs = [
        (target, engineer)
        for target in safe_targets for engineer in available
    ]
    return min(
        pairs,
        key=lambda pair: (
            _distance_sq(pair[0], pair[1]),
            int(pair[0].get("object_id", 0)),
            int(pair[1].get("object_id", 0)),
        ),
    )
