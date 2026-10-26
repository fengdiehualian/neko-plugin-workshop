from __future__ import annotations

import math
import re
from typing import Any

from .monitoring import describe_alerts
from .policy import PolicyError


_ORDINALS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def create_alert_watch(
    state: dict[str, Any],
    query: str,
    radius_cells: float | None = None,
    *,
    label: str | None = None,
    object_id: int | None = None,
) -> dict[str, Any]:
    """Create an exact-object or area watch.

    An omitted radius means exact-object matching when an object can be resolved.
    Area/base/marker watches require a radius because they have no object whose own
    damage events can be matched directly.
    """
    anchor = resolve_watch_anchor(state, query, object_id=object_id)
    if anchor["anchor_kind"] != "object" and radius_cells is None:
        radius_cells = 35.0
    radius = None if radius_cells is None else max(0.0, float(radius_cells))
    alerts = state.get("alerts")
    baseline = max(
        (
            int(item.get("event_id", 0))
            for item in alerts
            if isinstance(item, dict) and isinstance(item.get("event_id"), int)
        ),
        default=0,
    ) if isinstance(alerts, list) else 0
    if label and str(label).strip():
        anchor["label"] = str(label).strip()
    return {
        "query": str(query or "").strip(),
        "anchor": anchor,
        "radius_cells": radius,
        "match_mode": "exact_object" if radius is None else "radius",
        "last_event_id": baseline,
    }


def observe_alert_watch(
    state: dict[str, Any], watch: dict[str, Any]
) -> tuple[list[dict[str, Any]], int]:
    anchor = watch.get("anchor")
    if not isinstance(anchor, dict):
        return [], int(watch.get("last_event_id", 0))
    _refresh_dynamic_anchor(state, anchor)
    anchor_cell = anchor.get("cell")
    radius = watch.get("radius_cells")
    last_event_id = int(watch.get("last_event_id", 0))
    alerts = state.get("alerts")
    if not isinstance(alerts, list):
        return [], last_event_id
    new_raw = [
        item
        for item in alerts
        if isinstance(item, dict)
        and isinstance(item.get("event_id"), int)
        and item["event_id"] > last_event_id
    ]
    newest = max((item["event_id"] for item in new_raw), default=last_event_id)
    object_id = anchor.get("object_id")
    matching_ids = set()
    for item in new_raw:
        if item.get("kind") not in {"under_attack", "object_lost"}:
            continue
        if radius is None:
            matches = object_id is not None and item.get("object_id") == object_id
        else:
            matches = (
                _valid_cell(anchor_cell)
                and _valid_cell(item.get("cell"))
                and _distance(item["cell"], anchor_cell) <= float(radius)
            )
        if matches:
            matching_ids.add(item["event_id"])
    described = [
        item for item in describe_alerts(state)
        if item.get("event_id") in matching_ids
    ]
    return described, newest


def resolve_watch_anchor(
    state: dict[str, Any], query: str, *, object_id: int | None = None
) -> dict[str, Any]:
    if object_id is not None:
        entity = _entity_by_id(state, int(object_id))
        if entity is None:
            raise PolicyError(f"没有找到编号为 {object_id} 的己方对象")
        return _object_anchor(entity)

    text = str(query or "").strip()
    if not text:
        raise PolicyError("必须说明要监控的对象、区域或标记点")
    marker = _resolve_marker(state, text)
    if marker is not None:
        return marker
    entity = _resolve_owned_entity(state, text)
    if entity is not None:
        return _object_anchor(entity)
    region = resolve_region_anchor(state, text)
    region["anchor_kind"] = "region"
    return region


def resolve_region_anchor(
    state: dict[str, Any], region_query: str
) -> dict[str, Any]:
    text = _normalize(region_query)
    if not text:
        raise PolicyError("必须说明要监控的基地或区域")
    player = state.get("player")
    house_id = player.get("house_id") if isinstance(player, dict) else None
    landmarks = state.get("landmarks")
    landmarks = landmarks if isinstance(landmarks, list) else []
    owned_landmarks = [
        item for item in landmarks
        if isinstance(item, dict)
        and item.get("owner_house_id") == house_id
        and item.get("alive") is True
        and _valid_cell(item.get("cell"))
    ]
    visible = state.get("visible_entities")
    visible = visible if isinstance(visible, list) else []
    owned_buildings = [
        item for item in visible
        if isinstance(item, dict)
        and item.get("relation") == "self"
        and item.get("kind") == "building"
        and item.get("visible") is True
        and item.get("alive", True) is True
        and _valid_cell(item.get("cell"))
    ]
    owned = _deduplicate_objects([*owned_landmarks, *owned_buildings])
    bases = [
        item for item in owned
        if item.get("kind") in {"mobile_base", "construction_yard"}
    ]
    if not owned:
        raise PolicyError("没有找到与描述匹配的己方对象、标记点或基地")
    clusters = _cluster_objects(owned)
    candidates = [_cluster_anchor(cluster, bases) for cluster in clusters]
    if any(word in text for word in ("上", "北", "top", "north")):
        selected = min(candidates, key=lambda item: (item["cell"]["y"], item["cell"]["x"]))
        label = "上方基地"
    elif any(word in text for word in ("下", "南", "bottom", "south")):
        selected = max(candidates, key=lambda item: (item["cell"]["y"], -item["cell"]["x"]))
        label = "下方基地"
    elif any(word in text for word in ("左", "西", "left", "west")):
        selected = min(candidates, key=lambda item: (item["cell"]["x"], item["cell"]["y"]))
        label = "左侧基地"
    elif any(word in text for word in ("右", "东", "right", "east")):
        selected = max(candidates, key=lambda item: (item["cell"]["x"], -item["cell"]["y"]))
        label = "右侧基地"
    else:
        selected = candidates[0]
        label = "指定基地"
    return {
        "object_id": selected.get("object_id"),
        "type_id": selected.get("type_id"),
        "label": label,
        "cell": dict(selected["cell"]),
        "member_count": selected.get("member_count", 1),
    }


def alert_watch_report(watch: dict[str, Any], alert: dict[str, Any]) -> str:
    label = watch.get("anchor", {}).get("label", "监控目标")
    event = "已经损失" if alert.get("kind") == "object_lost" else "正在遭受攻击"
    return f"警报：{label}{event}，{alert.get('summary', '请立即检查战场')}"


def _refresh_dynamic_anchor(state: dict[str, Any], anchor: dict[str, Any]) -> None:
    if anchor.get("anchor_kind") == "object" and anchor.get("object_id") is not None:
        current = _entity_by_id(state, int(anchor["object_id"]))
        if current is not None and _valid_cell(current.get("cell")):
            anchor["cell"] = dict(current["cell"])
            anchor["alive"] = current.get("alive", True) is True
    elif anchor.get("anchor_kind") == "marker":
        marker = _marker_by_slot(state, anchor.get("slot"))
        if marker is not None:
            anchor["cell"] = dict(marker["cell"])
            anchor["marker_id"] = marker.get("marker_id")


def _resolve_owned_entity(state: dict[str, Any], query: str) -> dict[str, Any] | None:
    entities = state.get("visible_entities")
    if not isinstance(entities, list):
        return None
    ordinal, stripped = _extract_ordinal(query)
    needle = _normalize(stripped)
    ignored = ("监控", "观察", "保护", "附近", "周围", "受击", "情况", "目标")
    for word in ignored:
        needle = needle.replace(word, "")
    matches = []
    for item in entities:
        if not isinstance(item, dict) or item.get("relation") != "self":
            continue
        if item.get("alive", True) is not True or not _valid_cell(item.get("cell")):
            continue
        names = (_normalize(item.get("display_name")), _normalize(item.get("type_id")))
        if needle and any(needle in name or name in needle for name in names if name):
            matches.append(item)
    matches.sort(key=lambda item: (
        item["cell"]["y"], item["cell"]["x"], int(item.get("object_id", 0))
    ))
    index = (ordinal or 1) - 1
    return matches[index] if 0 <= index < len(matches) else None


def _resolve_marker(state: dict[str, Any], query: str) -> dict[str, Any] | None:
    text = _normalize(query)
    if not any(word in text for word in ("标记", "信标", "beacon", "marker")):
        return None
    ordinal, _ = _extract_ordinal(query)
    markers = state.get("markers")
    if not isinstance(markers, list):
        raise PolicyError("游戏桥尚未提供玩家标记点")
    visible = [item for item in markers if isinstance(item, dict) and _valid_cell(item.get("cell"))]
    if not visible:
        raise PolicyError("当前没有可用的玩家标记点")
    if ordinal is not None:
        selected = next((item for item in visible if item.get("slot") == ordinal), None)
        if selected is None:
            raise PolicyError(f"没有找到 {ordinal} 号标记点")
    else:
        selected = max(visible, key=lambda item: int(item.get("marker_id", 0)))
    return {
        "anchor_kind": "marker",
        "slot": selected.get("slot"),
        "marker_id": selected.get("marker_id"),
        "label": f"{selected.get('slot', '')}号标记点" if selected.get("slot") else "最新标记点",
        "cell": dict(selected["cell"]),
    }


def _object_anchor(entity: dict[str, Any]) -> dict[str, Any]:
    return {
        "anchor_kind": "object",
        "object_id": int(entity["object_id"]),
        "type_id": str(entity.get("type_id", "")),
        "object_kind": str(entity.get("kind", "unknown")),
        "label": str(entity.get("display_name") or entity.get("type_id") or "指定目标"),
        "cell": dict(entity["cell"]),
        "alive": entity.get("alive", True) is True,
    }


def _entity_by_id(state: dict[str, Any], object_id: int) -> dict[str, Any] | None:
    entities = state.get("visible_entities")
    if not isinstance(entities, list):
        return None
    return next((
        item for item in entities
        if isinstance(item, dict)
        and item.get("relation") == "self"
        and item.get("object_id") == object_id
        and _valid_cell(item.get("cell"))
    ), None)


def _marker_by_slot(state: dict[str, Any], slot: Any) -> dict[str, Any] | None:
    markers = state.get("markers")
    if not isinstance(markers, list):
        return None
    return next((
        item for item in markers
        if isinstance(item, dict) and item.get("slot") == slot and _valid_cell(item.get("cell"))
    ), None)


def _extract_ordinal(value: str) -> tuple[int | None, str]:
    match = re.search(r"(?P<number>\d+|[一二三四五六七八九十])\s*号", str(value))
    if not match:
        return None, str(value)
    token = match.group("number")
    number = int(token) if token.isdigit() else _ORDINALS.get(token)
    return number, str(value)[:match.start()] + str(value)[match.end():]


def _normalize(value: Any) -> str:
    return str(value or "").strip().casefold().replace(" ", "")


def _valid_cell(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(axis), int) and not isinstance(value.get(axis), bool)
        for axis in ("x", "y")
    )


def _distance(a: dict[str, int], b: dict[str, int]) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def _deduplicate_objects(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in items:
        object_id = item.get("object_id", item.get("unit_id"))
        cell = item["cell"]
        key = ("id", object_id) if object_id is not None else (
            "cell", cell["x"], cell["y"], item.get("type_id")
        )
        unique.setdefault(key, item)
    return list(unique.values())


def _cluster_objects(
    items: list[dict[str, Any]], join_distance: float = 24.0
) -> list[list[dict[str, Any]]]:
    remaining = list(items)
    clusters: list[list[dict[str, Any]]] = []
    while remaining:
        cluster = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for item in list(remaining):
                if any(_distance(item["cell"], member["cell"]) <= join_distance for member in cluster):
                    remaining.remove(item)
                    cluster.append(item)
                    changed = True
        clusters.append(cluster)
    return clusters


def _cluster_anchor(
    cluster: list[dict[str, Any]], bases: list[dict[str, Any]]
) -> dict[str, Any]:
    base_ids = {id(item) for item in bases}
    representative = next((item for item in cluster if id(item) in base_ids), cluster[0])
    return {
        "object_id": representative.get("object_id", representative.get("unit_id")),
        "type_id": representative.get("type_id"),
        "cell": {
            "x": round(sum(item["cell"]["x"] for item in cluster) / len(cluster)),
            "y": round(sum(item["cell"]["y"] for item in cluster) / len(cluster)),
        },
        "member_count": len(cluster),
    }
