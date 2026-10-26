from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def load_video_strategy_knowledge() -> dict[str, Any]:
    path = Path(__file__).with_name("video_strategy_knowledge.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"version": 1, "sources": [], "rules": [], "us_convoy_roles": {}}
    return value if isinstance(value, dict) else {"version": 1, "sources": [], "rules": [], "us_convoy_roles": {}}


def video_rules() -> list[dict[str, Any]]:
    return [item for item in load_video_strategy_knowledge().get("rules", []) if isinstance(item, dict)]


def video_sources() -> list[dict[str, Any]]:
    return [item for item in load_video_strategy_knowledge().get("sources", []) if isinstance(item, dict)]


def us_convoy_roles() -> dict[str, dict[str, Any]]:
    value = load_video_strategy_knowledge().get("us_convoy_roles", {})
    return {str(key): dict(item) for key, item in value.items() if isinstance(item, dict)}
