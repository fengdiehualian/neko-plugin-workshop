from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_DOCTRINE: dict[str, Any] = {
    "name": "标准双矿开",
    "sequence": [
        {"query": "兵营", "count": 1},
        {"query": "矿场", "count": 1},
        {"query": "电厂", "count": 1},
        {"query": "重工", "count": 1},
        {"query": "采矿车", "count": 4, "kind": "vehicle"},
        {"query": "矿场", "count": 2},
        {"query": "电厂", "count": 2},
        {"query": "动力涡轮", "count": 1, "optional": True},
        {"query": "科技建筑", "count": 1, "repeat_unlocked": True},
    ],
    "expansion": {
        "policy": "nearest_ore",
        "distance_cells": 6,
        "ore_buffer_cells": 3,
        "power_buffer": 250,
    },
    "harvester_target": 4,
}


def _path() -> Path:
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    return Path(root or Path.home()) / "NekoMOCompanion" / "opening_doctrines.json"


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def load_doctrines() -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"default": _copy(DEFAULT_DOCTRINE)}
    if not isinstance(payload, dict):
        return {"default": _copy(DEFAULT_DOCTRINE)}
    result = {str(key): value for key, value in payload.items() if isinstance(value, dict)}
    return result or {"default": _copy(DEFAULT_DOCTRINE)}


def save_doctrine(name: str, doctrine: dict[str, Any]) -> None:
    values = load_doctrines()
    values[str(name or "default")] = _copy(doctrine)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")


def get_doctrine(name: str | None = None) -> dict[str, Any]:
    values = load_doctrines()
    return _copy(values.get(str(name or "default"), values.get("default", DEFAULT_DOCTRINE)))


def doctrine_step(
    doctrine: dict[str, Any], profiles: list[dict[str, Any]],
    owned: dict[str, int], committed: dict[str, int],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for raw in doctrine.get("sequence", []):
        if not isinstance(raw, dict):
            continue
        query = str(raw.get("query") or "").strip()
        if not query:
            continue
        matches = [profile for profile in profiles if _matches(profile, query, raw.get("kind"))]
        if not matches:
            continue
        effective = sum(
            int(owned.get(str(item.get("type_id", "")).upper(), 0))
            + int(committed.get(str(item.get("type_id", "")).upper(), 0))
            for item in matches
        )
        if effective < max(1, int(raw.get("count", 1))):
            profile = min(matches, key=lambda item: (int(item.get("tech_level") or 0), int(item.get("cost") or 0), str(item.get("type_id"))))
            return profile, raw
    return None


def _matches(profile: dict[str, Any], query: str, kind: Any = None) -> bool:
    if kind and str(profile.get("kind")) != str(kind):
        return False
    text = " ".join(str(profile.get(key, "")) for key in ("type_id", "display_name", "internal_name", "building_class", "role"))
    aliases = {
        "兵营": ("infantry_production",),
        "矿场": ("resource",),
        "电厂": ("power", "发电厂", "反应炉", "动力厂"),
        "重工": ("vehicle_production",),
        "机场": ("aircraft_production",),
        "科技建筑": ("technology",),
        "动力涡轮": ("涡轮", "turbine", "support_building"),
        "采矿车": ("harvester", "矿车", "采矿"),
    }
    needles = aliases.get(query, (query,))
    return any(needle.casefold() in text.casefold() for needle in needles)
