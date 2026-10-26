"""Read-only HUD row geometry; hotkeys identify positions, not characters.

Inspired by StarRailOneDragon's per-position name/avatar separation (GPL-3.0).
No fuzzy best-name choice, controller, operation graph or game input is used.
"""

from __future__ import annotations

from typing import Any


def locate_team_rows(boxes, *, width: int, height: int) -> list[float]:
    anchors: dict[int, float] = {}
    for box in boxes:
        text = str(box.get("text") or "").strip()
        if text not in {"1", "2", "3", "4"} or float(box.get("score") or 0) < 0.7:
            continue
        x = (float(box["left"]) + float(box["right"])) / 2
        y = (float(box["top"]) + float(box["bottom"])) / 2
        if x < width * 0.955 or not height * 0.15 <= y <= height * 0.78:
            continue
        anchors[int(text)] = y
    if len(anchors) < 3:
        return []
    first, last = min(anchors), max(anchors)
    step = (anchors[last] - anchors[first]) / (last - first)
    if not height * 0.055 <= step <= height * 0.16:
        return []
    start = anchors[first] - (first - 1) * step
    rows = [start + i * step for i in range(4)]
    if not height * 0.12 < rows[0] < rows[-1] < height * 0.82:
        return []
    if any(abs(rows[slot - 1] - y) > step * 0.18 for slot, y in anchors.items()):
        return []
    return rows


def assign_team_slots(characters: list[dict[str, Any]], rows: list[float], *, height: int):
    result = [dict(item) for item in characters]
    if len(rows) != 4:
        return result
    spacing = (rows[-1] - rows[0]) / 3
    for item in result:
        if item.get("identity_scope") not in {"hud_label", "hud_portrait"}:
            continue
        region = item.get("region") or {}
        center = (float(region.get("top") or 0) + float(region.get("bottom") or 0)) * height / 2
        index = min(range(4), key=lambda i: abs(rows[i] - center))
        if abs(rows[index] - center) <= spacing * 0.4:
            item["slot"] = index + 1
    # Two identities in one position are a conflict, not two team members.
    conflicting = {
        slot for slot in range(1, 5) if len({item["entity_id"] for item in result if item.get("slot") == slot}) > 1
    }
    return [item for item in result if item.get("slot") not in conflicting]


def infer_complete_team_rows(characters: list[dict[str, Any]], *, height: int) -> list[float]:
    """Four independently recognized regular HUD rows establish their order.

    Do not sort a partial team into positions: missing slot 1 is not slot 2.
    """
    hud = [
        item
        for item in characters
        if item.get("identity_scope") in {"hud_label", "hud_portrait"} and item.get("region", {}).get("left", 0) >= 0.82
    ]
    if len(hud) != 4 or len({item.get("entity_id") for item in hud}) != 4:
        return []
    rows = sorted((item["region"]["top"] + item["region"]["bottom"]) * height / 2 for item in hud)
    gaps = [rows[i + 1] - rows[i] for i in range(3)]
    if min(gaps) < height * 0.055 or max(gaps) > height * 0.16 or max(gaps) / min(gaps) > 1.6:
        return []
    return rows
