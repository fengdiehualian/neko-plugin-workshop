from __future__ import annotations

import math
from typing import Any

from .monitoring import describe_location
from .unit_knowledge import match_visible_entities


def observe_unit_watch(
    state: dict[str, Any],
    query: str,
    previous_distance: float | None = None,
    near_distance: float = 25.0,
    critical_distance: float = 12.0,
) -> dict[str, Any]:
    matches = match_visible_entities(state, query, relation="enemy")
    anchors = _own_base_cells(state)
    observations = []
    for entity in matches:
        cell = entity.get("cell")
        if not _valid_cell(cell):
            continue
        distance = min((_distance(cell, anchor) for anchor in anchors), default=None)
        observations.append(
            {
                "object_id": entity.get("object_id"),
                "type_id": entity.get("type_id"),
                "name": entity.get("display_name") or entity.get("type_id"),
                "kind": entity.get("kind"),
                "health_ratio": entity.get("health_ratio"),
                "location": describe_location(state, cell),
                "distance_to_base_cells": round(distance, 1) if distance is not None else None,
            }
        )
    observations.sort(
        key=lambda item: (
            item["distance_to_base_cells"] is None,
            item["distance_to_base_cells"] or 0,
        )
    )
    closest = observations[0] if observations else None
    distance = closest.get("distance_to_base_cells") if closest else None
    if distance is None:
        proximity = "visible_no_base_reference" if closest else "not_visible"
    elif distance <= critical_distance:
        proximity = "critical"
    elif distance <= near_distance:
        proximity = "near"
    else:
        proximity = "distant"
    if distance is None or previous_distance is None:
        trend = "unknown"
    elif distance < previous_distance - 1.0:
        trend = "approaching"
    elif distance > previous_distance + 1.0:
        trend = "moving_away"
    else:
        trend = "steady"
    return {
        "query": query,
        "visible_count": len(observations),
        "proximity": proximity,
        "trend": trend,
        "closest_distance_cells": distance,
        "closest": closest,
        "observations": observations,
        "visibility_note": "只监视当前玩家已经看见且未被隐形或伪装遮蔽的敌方单位",
    }


def watch_report_text(status: dict[str, Any]) -> str:
    query = str(status.get("query", "目标"))
    closest = status.get("closest")
    if not isinstance(closest, dict):
        return f"当前视野内没有发现{query}，黑幕区域无法监视"
    name = closest.get("name") or query
    location = closest.get("location") or "已知视野内"
    proximity = status.get("proximity")
    trend = status.get("trend")
    if proximity == "critical":
        return f"警报：{name}已接近基地，当前位置在{location}"
    if proximity == "near":
        motion = "，仍在接近" if trend == "approaching" else ""
        return f"注意：{name}正在基地外围，当前位置在{location}{motion}"
    if trend == "approaching":
        return f"{name}正在向基地接近，当前位置在{location}"
    if trend == "moving_away":
        return f"{name}正在远离基地，当前位置在{location}"
    return f"已发现{name}，当前位置在{location}，暂未进入基地警戒范围"


def watch_transition(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> tuple[str, str] | None:
    """Return the single highest-priority reportable state transition."""
    if previous is None:
        if current.get("visible_count", 0) > 0:
            return "detected", watch_report_text(current)
        return None

    was_visible = previous.get("visible_count", 0) > 0
    is_visible = current.get("visible_count", 0) > 0
    if was_visible and not is_visible:
        return "lost_visibility", (
            f"{current.get('query', '目标')}已离开当前视野，"
            "黑幕区域无法继续确认其位置，监控仍然保持"
        )
    if not was_visible and is_visible:
        return "detected", watch_report_text(current)
    if not is_visible:
        return None

    previous_proximity = previous.get("proximity")
    proximity = current.get("proximity")
    if proximity == "critical" and previous_proximity != "critical":
        return "critical", watch_report_text(current)
    if proximity == "near" and previous_proximity not in {"near", "critical"}:
        return "near", watch_report_text(current)
    if (
        current.get("trend") == "approaching"
        and previous.get("trend") != "approaching"
    ):
        return "approaching", watch_report_text(current)
    return None


def _own_base_cells(state: dict[str, Any]) -> list[dict[str, int]]:
    player = state.get("player")
    house_id = player.get("house_id") if isinstance(player, dict) else None
    landmarks = state.get("landmarks")
    if not isinstance(landmarks, list):
        return []
    return [
        item["cell"]
        for item in landmarks
        if isinstance(item, dict)
        and item.get("owner_house_id") == house_id
        and item.get("kind") in {"mobile_base", "construction_yard"}
        and _valid_cell(item.get("cell"))
    ]


def _valid_cell(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(axis), int) and not isinstance(value.get(axis), bool)
        for axis in ("x", "y")
    )


def _distance(a: dict[str, int], b: dict[str, int]) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])
