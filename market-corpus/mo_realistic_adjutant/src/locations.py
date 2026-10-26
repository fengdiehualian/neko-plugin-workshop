from __future__ import annotations

from typing import Any

from .policy import PolicyError


FACTION_LABELS = {
    "allied": "盟军",
    "soviet": "苏军",
    "epsilon": "厄普西隆",
    "foehn": "焚风",
}

FACTION_ALIASES = {
    "allied": ("盟军", "盟盟", "allied"),
    "soviet": ("苏军", "苏联", "soviet"),
    "epsilon": ("ep", "厄普西隆", "epsilon", "心灵军团"),
    "foehn": ("焚风", "foehn"),
}

TECH_NAMES = {
    "CAOILD": "科技钻油井",
    "CAAERO": "科技机场",
    "CAACAD": "科技学院",
    "CAFHOSP": "科技医院",
    "CAMACH": "科技机械商店",
    "CASLAB": "科技秘密实验室",
    "CAHOSP": "市民医院",
    "CAPOWR": "科技发电厂",
    "CATHOSP": "科技医院",
    "CAOUTP": "科技前哨站",
}


def resolve_base(
    state: dict[str, Any], destination: str, origin: dict[str, int]
) -> dict[str, Any]:
    if not isinstance(destination, str) or not destination.strip():
        raise PolicyError("destination must describe a base, for example 盟军基地")
    text = destination.strip().casefold().replace(" ", "")
    faction = _faction_from_text(text)
    landmarks = state.get("landmarks")
    if not isinstance(landmarks, list):
        raise PolicyError("visible landmark list is missing")
    bases = [
        item
        for item in landmarks
        if isinstance(item, dict)
        and item.get("kind") in {"mobile_base", "construction_yard"}
        and item.get("alive") is True
        and _valid_cell(item.get("cell"))
        and (faction is None or item.get("faction") == faction)
    ]
    if not bases:
        qualifier = FACTION_LABELS.get(faction, "")
        raise PolicyError(f"没有发现可见的{qualifier}基地或机动建设车")

    # A mobile construction vehicle is the human player's primary base anchor.
    mobile = [item for item in bases if item.get("kind") == "mobile_base"]
    candidates = mobile or [item for item in bases if item.get("kind") == "construction_yard"]
    if any(word in text for word in ("最左", "leftmost", "最西")):
        selected = min(candidates, key=lambda item: (item["cell"]["x"], item["cell"]["y"]))
    elif any(word in text for word in ("最右", "rightmost", "最东")):
        selected = max(candidates, key=lambda item: (item["cell"]["x"], -item["cell"]["y"]))
    elif any(word in text for word in ("最上", "最北", "topmost", "north")):
        selected = min(candidates, key=lambda item: (item["cell"]["y"], item["cell"]["x"]))
    elif any(word in text for word in ("最下", "最南", "bottommost", "south")):
        selected = max(candidates, key=lambda item: (item["cell"]["y"], -item["cell"]["x"]))
    else:
        selected = min(candidates, key=lambda item: _distance_sq(item["cell"], origin))
    return selected


def nearby_cells(
    state: dict[str, Any], anchor: dict[str, int], count: int
) -> list[dict[str, int]]:
    del state  # Native CoordinatesLegal validates isometric CellStruct coordinates.
    offsets = [
        (-3, 0), (3, 0), (0, -3), (0, 3),
        (-3, -3), (3, -3), (-3, 3), (3, 3),
        (-5, 0), (5, 0), (0, -5), (0, 5),
    ]
    result = []
    for dx, dy in offsets:
        x, y = anchor["x"] + dx, anchor["y"] + dy
        if -32768 <= x <= 32767 and -32768 <= y <= 32767:
            result.append({"x": x, "y": y})
            if len(result) == count:
                return result
    if not result:
        raise PolicyError("基地附近没有可表示的目标位置")
    while len(result) < count:
        result.append(dict(result[len(result) % len(result)]))
    return result


def list_tech_buildings(state: dict[str, Any]) -> list[dict[str, Any]]:
    landmarks = state.get("landmarks")
    if not isinstance(landmarks, list):
        raise PolicyError("visible landmark list is missing")
    result = []
    for item in landmarks:
        if not isinstance(item, dict) or item.get("kind") != "tech_building":
            continue
        type_id = str(item.get("type_id", ""))
        raw_name = str(item.get("display_name", "")).strip()
        result.append(
            {
                "object_id": item.get("object_id"),
                "type_id": type_id,
                "name": TECH_NAMES.get(type_id.upper(), raw_name or type_id or "未知科技建筑"),
                "captured": item.get("captured") is True,
                "owner_house_id": item.get("owner_house_id"),
            }
        )
    return result


def base_label(item: dict[str, Any]) -> str:
    faction = FACTION_LABELS.get(str(item.get("faction")), "未知阵营")
    kind = "机动建设车" if item.get("kind") == "mobile_base" else "建造场"
    return f"{faction}{kind}"


def _faction_from_text(text: str) -> str | None:
    for faction, aliases in FACTION_ALIASES.items():
        if any(alias in text for alias in aliases):
            return faction
    return None


def _valid_cell(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(axis), int) and not isinstance(value.get(axis), bool)
        for axis in ("x", "y")
    )


def _distance_sq(a: dict[str, int], b: dict[str, int]) -> int:
    return (a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2
