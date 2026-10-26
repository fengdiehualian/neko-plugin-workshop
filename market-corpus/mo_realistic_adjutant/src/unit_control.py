from __future__ import annotations

import re
from typing import Any

from .locations import TECH_NAMES, resolve_base
from .policy import PolicyError
from .unit_knowledge import match_visible_entities, resolve_type_ids


SPATIAL_REGION_CODES = {
    "left": 1,
    "right": 2,
    "top": 3,
    "bottom": 4,
}

SELECTION_KIND_CODES = {
    "infantry": 1,
    "vehicle": 2,
    "aircraft": 3,
}


def select_owned_units(
    state: dict[str, Any],
    query: str | None = None,
    object_ids: list[int] | None = None,
    damaged_only: bool = False,
    health_below: float = 1.0,
    include_buildings: bool = False,
    require_controllable: bool = True,
) -> list[dict[str, Any]]:
    raw = state.get("visible_entities")
    if not isinstance(raw, list):
        raise PolicyError("visible entity list is missing")
    candidates = [
        item
        for item in raw
        if isinstance(item, dict)
        and item.get("relation") == "self"
        and item.get("alive") is True
        and (not require_controllable or item.get("controllable") is True)
        and item.get("visible") is True
        and (include_buildings or item.get("kind") != "building")
    ]
    if object_ids is not None:
        wanted = set(object_ids)
        candidates = [item for item in candidates if item.get("object_id") in wanted]
    region = spatial_region(query)
    kind = selection_kind(query)
    name_query = selection_name_query(query)
    if kind:
        candidates = [item for item in candidates if item.get("kind") == kind]
    if name_query:
        matches = {
            item.get("object_id")
            for item in match_visible_entities(state, name_query, "self")
        }
        candidates = [item for item in candidates if item.get("object_id") in matches]
    candidates = select_spatial_half(candidates, region)
    threshold = max(0.01, min(float(health_below), 1.0))
    if damaged_only:
        candidates = [
            item
            for item in candidates
            if isinstance(item.get("health_ratio"), (int, float))
            and not isinstance(item.get("health_ratio"), bool)
            and float(item["health_ratio"]) < threshold
        ]
    return sorted(
        candidates,
        key=lambda item: (
            float(item.get("health_ratio", 1.0)),
            int(item.get("object_id", 0)),
        ),
    )


def spatial_region(query: str | None) -> str | None:
    text = _normalize(str(query or ""))
    if any(marker in text for marker in ("地图左边", "地图左侧", "左半边", "左半场", "左侧", "左边")):
        return "left"
    if any(marker in text for marker in ("地图右边", "地图右侧", "右半边", "右半场", "右侧", "右边")):
        return "right"
    if any(marker in text for marker in ("地图上边", "地图上方", "上半边", "上半场", "上方", "上边")):
        return "top"
    if any(marker in text for marker in ("地图下边", "地图下方", "下半边", "下半场", "下方", "下边")):
        return "bottom"
    return None


def selection_name_query(query: str | None) -> str:
    text = _normalize(str(query or ""))
    for marker in (
        "地图左边", "地图左侧", "左半边", "左半场", "左侧", "左边",
        "地图右边", "地图右侧", "右半边", "右半场", "右侧", "右边",
        "地图上边", "地图上方", "上半边", "上半场", "上方", "上边",
        "地图下边", "地图下方", "下半边", "下半场", "下方", "下边",
    ):
        text = text.replace(marker, "")
    for filler in ("地图", "我方", "己方", "所有", "全部", "可控", "单位", "部队", "的"):
        text = text.replace(filler, "")
    kind = selection_kind(query)
    if kind:
        return ""
    return text


def selection_kind(query: str | None) -> str | None:
    text = _normalize(str(query or ""))
    for filler in (
        "地图左边", "地图左侧", "左半边", "左半场", "左侧", "左边",
        "地图右边", "地图右侧", "右半边", "右半场", "右侧", "右边",
        "地图上边", "地图上方", "上半边", "上半场", "上方", "上边",
        "地图下边", "地图下方", "下半边", "下半场", "下方", "下边",
        "地图", "我方", "己方", "所有", "全部", "全体", "可控", "的",
    ):
        text = text.replace(filler, "")
    if text in {"步兵", "士兵", "人员", "人"}:
        return "infantry"
    if text in {"载具", "车辆", "战车", "地面载具"}:
        return "vehicle"
    if text in {"飞机", "空军", "飞行单位", "航空兵"}:
        return "aircraft"
    return None


def select_spatial_half(
    entities: list[dict[str, Any]], region: str | None
) -> list[dict[str, Any]]:
    if region not in SPATIAL_REGION_CODES:
        return list(entities)
    horizontal = region in {"left", "right"}
    ordered = sorted(
        entities,
        key=lambda item: (
            _projected_axis(item, horizontal),
            int(item.get("object_id", 0)),
        ),
    )
    split = (len(ordered) + 1) // 2
    return ordered[:split] if region in {"left", "top"} else ordered[split:]


def _projected_axis(entity: dict[str, Any], horizontal: bool) -> int:
    cell = entity.get("cell")
    if not _valid_cell(cell):
        return 0
    return cell["x"] - cell["y"] if horizontal else cell["x"] + cell["y"]


def resolve_visible_destination(
    state: dict[str, Any], destination: str, origin: dict[str, int]
) -> dict[str, Any]:
    text = str(destination or "").strip()
    if not text:
        raise PolicyError("destination is required")
    normalized = _normalize(text)
    marker = resolve_player_marker(state, text)
    if marker is not None:
        return marker
    if any(word in normalized for word in ("基地", "建造场", "基地车", "constructionyard")):
        return resolve_base(state, text, origin)

    type_ids = set(resolve_type_ids(text))
    landmarks = state.get("landmarks")
    landmarks = landmarks if isinstance(landmarks, list) else []
    candidates = []
    for item in landmarks:
        if not isinstance(item, dict) or not _valid_cell(item.get("cell")):
            continue
        type_id = str(item.get("type_id", "")).upper()
        display = str(item.get("display_name", ""))
        known = TECH_NAMES.get(type_id, "")
        if type_id in type_ids or any(
            normalized and normalized in _normalize(value)
            for value in (display, known, type_id)
        ):
            candidates.append(item)
    if not candidates:
        candidates = match_visible_entities(state, text)
    candidates = [item for item in candidates if _valid_cell(item.get("cell"))]
    if not candidates:
        raise PolicyError(f"当前视野内没有找到目的地“{text}”")
    return min(candidates, key=lambda item: _distance_sq(item["cell"], origin))


def resolve_player_marker(
    state: dict[str, Any], destination: str
) -> dict[str, Any] | None:
    text = _normalize(str(destination or ""))
    if not any(word in text for word in ("标记", "信标", "marker", "beacon")):
        return None
    markers = state.get("markers")
    if not isinstance(markers, list) or not markers:
        raise PolicyError("当前没有读取到玩家的 B 标记点")
    match = re.search(r"(\d+|[一二三])号", text)
    ordinal_names = {"一": 1, "二": 2, "三": 3}
    slot = None
    if match:
        token = match.group(1)
        slot = int(token) if token.isdigit() else ordinal_names[token]
    candidates = [
        item for item in markers
        if isinstance(item, dict)
        and _valid_cell(item.get("cell"))
        and (slot is None or item.get("slot") == slot)
    ]
    if not candidates:
        raise PolicyError(f"没有找到{slot or ''}号玩家标记点")
    selected = max(candidates, key=lambda item: int(item.get("marker_id", 0)))
    return {
        **selected,
        "kind": "player_marker",
        "location_label": (
            f"{selected.get('slot')}号标记点" if slot is not None else "最新标记点"
        ),
    }


def resolve_map_region_destination(
    state: dict[str, Any], destination: str
) -> dict[str, Any] | None:
    """Resolve human map directions without requiring a visible landmark."""
    text = _normalize(str(destination or ""))
    left = any(value in text for value in ("左上", "左下", "左边", "左侧", "西北", "西南"))
    right = any(value in text for value in ("右上", "右下", "右边", "右侧", "东北", "东南"))
    top = any(value in text for value in ("左上", "右上", "上方", "上边", "北边", "西北", "东北"))
    bottom = any(value in text for value in ("左下", "右下", "下方", "下边", "南边", "西南", "东南"))
    if not any((left, right, top, bottom)) or "地图" not in text:
        return None

    ore = state.get("ore_cells")
    ore = [
        item for item in ore if isinstance(item, dict) and _valid_cell(item)
    ] if isinstance(ore, list) else []
    if "矿" in text and ore:
        horizontal = [item["x"] - item["y"] for item in ore]
        vertical = [item["x"] + item["y"] for item in ore]
        def score(item: dict[str, int]) -> tuple[int, int]:
            h, v = item["x"] - item["y"], item["x"] + item["y"]
            directional = ((h if left else -h if right else 0)
                           + (v if top else -v if bottom else 0))
            return directional, -int(item.get("value", 0))
        selected = min(ore, key=score)
        return {"cell": {"x": selected["x"], "y": selected["y"]}, "location_label": destination}

    map_info = state.get("map")
    if not isinstance(map_info, dict):
        raise PolicyError("地图边界遥测缺失")
    try:
        origin_x = int(map_info.get("origin_x", 0))
        origin_y = int(map_info.get("origin_y", 0))
        width = max(16, int(map_info["width"]))
        height = max(16, int(map_info["height"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyError("地图边界遥测无效") from exc
    # Keep a deployment-sized margin away from the hard map edge.
    x_ratio = 0.16 if left else 0.84 if right else 0.5
    y_ratio = 0.16 if top else 0.84 if bottom else 0.5
    return {
        "cell": {
            "x": round(origin_x + width * x_ratio),
            "y": round(origin_y + height * y_ratio),
        },
        "location_label": destination,
    }


def relative_move_cells(
    state: dict[str, Any], origin: dict[str, int], destination: str
) -> list[dict[str, int]]:
    text = str(destination or "").strip().casefold().replace(" ", "")
    if not is_relative_move_request(text):
        return []
    distance = 2 if any(marker in text for marker in ("两格", "二格", "一两格", "几格")) else 1
    if any(marker in text for marker in ("左", "西", "left")):
        directions = [(-1, 0), (-1, -1), (-1, 1)]
    elif any(marker in text for marker in ("右", "东", "right")):
        directions = [(1, 0), (1, -1), (1, 1)]
    elif any(marker in text for marker in ("上", "北", "north", "up")):
        directions = [(0, -1), (-1, -1), (1, -1)]
    elif any(marker in text for marker in ("下", "南", "south", "down")):
        directions = [(0, 1), (-1, 1), (1, 1)]
    else:
        directions = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)]
    result = []
    for radius in range(distance, 0, -1):
        for dx, dy in directions:
            cell = {"x": origin["x"] + dx * radius, "y": origin["y"] + dy * radius}
            # RA2's CellStruct uses an isometric diamond coordinate space. MapRect
            # width/height are not direct upper bounds for CellStruct X/Y.
            if all(-32768 <= cell[axis] <= 32767 for axis in ("x", "y")):
                result.append(cell)
    return result


def is_relative_move_request(destination: str | None) -> bool:
    text = _normalize(str(destination or ""))
    if not text:
        return False
    has_distance = any(
        marker in text
        for marker in ("一格", "两格", "二格", "一两格", "几格", "一点", "旁边", "附近", "随意")
    )
    if not has_distance:
        return False
    remainder = text
    for filler in (
        "移动", "走", "前往", "去", "到", "往", "向", "朝", "靠", "一下", "一些",
        "一两格", "一格", "两格", "二格", "几格", "一点", "旁边", "附近", "随意",
        "左边", "左侧", "左", "西边", "西",
        "右边", "右侧", "右", "东边", "东",
        "上边", "上方", "上", "北边", "北",
        "下边", "下方", "下", "南边", "南",
    ):
        remainder = remainder.replace(filler, "")
    return not remainder


def entity_label(entity: dict[str, Any]) -> str:
    return str(
        entity.get("display_name")
        or entity.get("type_id")
        or entity.get("object_id")
        or "未知对象"
    )


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", value.casefold())


def _valid_cell(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(axis), int) and not isinstance(value.get(axis), bool)
        for axis in ("x", "y")
    )


def _distance_sq(a: dict[str, int], b: dict[str, int]) -> int:
    return (a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2
