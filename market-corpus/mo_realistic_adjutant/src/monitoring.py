from __future__ import annotations

from typing import Any


TYPE_NAMES = {
    "CMIN": "超时空采矿车",
    "HARV": "武装采矿车",
    "HTK": "奴隶矿场",
    "AMCV": "盟军机动建设车",
    "SMCV": "苏军机动建设车",
    "PCV": "厄普西隆机动建设车",
    "FMCV": "焚风机动建设车",
    "GACNST": "盟军建造场",
    "NACNST": "苏军建造场",
    "YACNST": "厄普西隆建造场",
    "FACNST": "焚风建造场",
}


def describe_alerts(state: dict[str, Any]) -> list[dict[str, Any]]:
    alerts = state.get("alerts")
    if not isinstance(alerts, list):
        return []
    frame = _integer(state.get("game", {}).get("frame"), 0)
    result = []
    for alert in sorted(
        (item for item in alerts if isinstance(item, dict)),
        key=lambda item: _integer(item.get("frame"), 0),
        reverse=True,
    ):
        kind = str(alert.get("kind", "unknown"))
        name = _object_name(alert)
        location = describe_location(state, alert.get("cell"))
        age_seconds = max(0, (frame - _integer(alert.get("frame"), frame)) // 30)
        if kind == "under_attack":
            summary = f"{location}的{name}正在遭受攻击"
        elif kind == "object_lost":
            summary = f"{location}的{name}已经损失"
        elif kind == "home_ore_low":
            summary = f"{location}的基地矿区储量已经偏低"
        elif kind == "home_ore_depleted":
            summary = f"{location}的基地矿区已经枯竭"
        else:
            summary = f"{location}出现未分类战场事件"
        result.append(
            {
                "event_id": alert.get("event_id"),
                "kind": kind,
                "severity": _integer(alert.get("severity"), 0),
                "object": name,
                "location": location,
                "age_seconds": age_seconds,
                "summary": summary,
            }
        )
    return result


def describe_location(state: dict[str, Any], cell: Any) -> str:
    if not _valid_cell(cell):
        return "位置未知处"
    player = state.get("player")
    player_id = player.get("house_id") if isinstance(player, dict) else None
    landmarks = state.get("landmarks")
    own_bases = []
    if isinstance(landmarks, list):
        own_bases = [
            item
            for item in landmarks
            if isinstance(item, dict)
            and item.get("kind") in {"mobile_base", "construction_yard"}
            and item.get("owner_house_id") == player_id
            and _valid_cell(item.get("cell"))
        ]
    if own_bases:
        anchor = min(own_bases, key=lambda item: _distance_sq(cell, item["cell"]))
        reporting = state.get("reporting")
        radius = reporting.get("base_radius_cells", 40) if isinstance(reporting, dict) else 40
        if _distance_sq(cell, anchor["cell"]) > max(1, int(radius)) ** 2:
            return _map_region(state, cell)
        base_name = _base_name(anchor)
        dx = cell["x"] - anchor["cell"]["x"]
        dy = cell["y"] - anchor["cell"]["y"]
        direction = _direction(dx, dy)
        return f"{base_name}{direction}"

    map_info = state.get("map")
    if not isinstance(map_info, dict):
        return "已知视野内"
    ox = _integer(map_info.get("origin_x"), 0)
    oy = _integer(map_info.get("origin_y"), 0)
    width = max(1, _integer(map_info.get("width"), 1))
    height = max(1, _integer(map_info.get("height"), 1))
    horizontal = "左侧" if cell["x"] < ox + width / 3 else "右侧" if cell["x"] > ox + width * 2 / 3 else "中部"
    vertical = "上方" if cell["y"] < oy + height / 3 else "下方" if cell["y"] > oy + height * 2 / 3 else ""
    return f"地图{horizontal}{vertical}"


def _map_region(state: dict[str, Any], cell: dict[str, int]) -> str:
    map_info = state.get("map")
    if not isinstance(map_info, dict):
        return "地图远端区域"
    ox = _integer(map_info.get("origin_x"), 0)
    oy = _integer(map_info.get("origin_y"), 0)
    width = max(1, _integer(map_info.get("width"), 1))
    height = max(1, _integer(map_info.get("height"), 1))
    horizontal = "左侧" if cell["x"] < ox + width / 3 else "右侧" if cell["x"] > ox + width * 2 / 3 else "中部"
    vertical = "上方" if cell["y"] < oy + height / 3 else "下方" if cell["y"] > oy + height * 2 / 3 else ""
    return f"地图{horizontal}{vertical}区域"


def economy_report(state: dict[str, Any]) -> dict[str, Any]:
    economy = state.get("economy")
    home_value = economy.get("home_ore_value") if isinstance(economy, dict) else None
    ore_cells = state.get("ore_cells")
    visible_cells = len(ore_cells) if isinstance(ore_cells, list) else 0
    if not isinstance(home_value, int):
        status = "unknown"
        summary = "还没有收到基地矿区储量数据"
    elif home_value <= 0:
        status = "depleted"
        summary = "基地附近的可见矿区已经枯竭"
    else:
        status = "available"
        summary = "基地附近仍有可见矿物"
    return {
        "home_ore_status": status,
        "home_ore_value": home_value,
        "visible_ore_cells": visible_cells,
        "summary": summary,
        "visibility_note": "只统计当前玩家视野内、基地或采矿车附近已确认的矿物",
    }


def _object_name(alert: dict[str, Any]) -> str:
    display = str(alert.get("display_name", "")).strip()
    type_id = str(alert.get("type_id", "")).strip().upper()
    if display and "MISSING" not in display.upper():
        return display
    return TYPE_NAMES.get(type_id, f"己方{alert.get('object_kind', '对象')} {type_id}".strip())


def _base_name(item: dict[str, Any]) -> str:
    faction = {"allied": "盟军", "soviet": "苏军", "epsilon": "厄普西隆", "foehn": "焚风"}.get(str(item.get("faction")), "己方")
    kind = "机动基地" if item.get("kind") == "mobile_base" else "基地"
    return f"{faction}{kind}"


def _direction(dx: int, dy: int) -> str:
    if abs(dx) <= 4 and abs(dy) <= 4:
        return "附近"
    vertical = "上方" if dy < -4 else "下方" if dy > 4 else ""
    horizontal = "左侧" if dx < -4 else "右侧" if dx > 4 else ""
    return f"{vertical}{horizontal}方向"


def _valid_cell(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(axis), int) and not isinstance(value.get(axis), bool)
        for axis in ("x", "y")
    )


def _distance_sq(a: dict[str, int], b: dict[str, int]) -> int:
    return (a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2


def _integer(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default
