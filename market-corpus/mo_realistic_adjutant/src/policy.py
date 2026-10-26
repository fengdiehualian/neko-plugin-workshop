from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ALLOWED_ACTIONS = frozenset({"mine", "return", "retreat", "stop", "move"})
TARGET_ACTIONS = frozenset({"mine", "retreat", "move"})
UNIT_ACTIONS = frozenset({
    "move", "retreat", "stop", "guard", "attack", "repair", "capture",
    "enter", "repair_building", "unload", "deploy", "explore", "assign_group",
    "auto_attack", "hunt",
})
UNIT_CELL_ACTIONS = frozenset({"move", "retreat"})
UNIT_OBJECT_ACTIONS = frozenset({"attack", "repair", "capture", "enter"})


class PolicyError(ValueError):
    pass


def select_nearest_mining_order(
    state: dict[str, Any], unit_id: int | None = None
) -> tuple[int, dict[str, int]]:
    unit_ids = None if unit_id is None else [unit_id]
    order = plan_mining_orders(state, unit_ids=unit_ids, count=1)[0]
    return order["unit_id"], order["target_cell"]


def plan_mining_orders(
    state: dict[str, Any],
    unit_ids: list[int] | None = None,
    count: int | None = None,
) -> list[dict[str, Any]]:
    player = _mapping(state.get("player"), "player")
    house_id = player.get("house_id")
    harvesters = state.get("harvesters")
    if not isinstance(harvesters, list):
        raise PolicyError("harvester list is missing")

    candidates = [
        unit
        for unit in harvesters
        if isinstance(unit, dict)
        and unit.get("owner_house_id") == house_id
        and unit.get("alive") is True
        and unit.get("controllable") is True
        and unit.get("mission_id") != -1
        and _valid_cell(unit.get("cell"))
    ]
    if unit_ids is not None:
        if not isinstance(unit_ids, list) or not unit_ids:
            raise PolicyError("unit_ids must be a non-empty list")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in unit_ids):
            raise PolicyError("every unit_id must be an integer")
        requested = list(dict.fromkeys(unit_ids))
        by_id = {unit.get("unit_id"): unit for unit in candidates}
        missing = [value for value in requested if value not in by_id]
        if missing:
            raise PolicyError(f"requested harvesters are not controllable: {missing}")
        candidates = [by_id[value] for value in requested]
    else:
        candidates.sort(
            key=lambda item: (
                _number(item.get("cargo_ratio"), 1.0),
                int(item.get("unit_id", 0)),
            )
        )
    if count is not None:
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise PolicyError("count must be a positive integer")
        candidates = candidates[:count]
    if not candidates:
        raise PolicyError("no controllable player harvester was found")

    ore_cells = state.get("ore_cells")
    if not isinstance(ore_cells, list):
        raise PolicyError("visible ore cell list is missing")
    visible_ore = [
        ore
        for ore in ore_cells
        if isinstance(ore, dict)
        and isinstance(ore.get("x"), int)
        and not isinstance(ore.get("x"), bool)
        and isinstance(ore.get("y"), int)
        and not isinstance(ore.get("y"), bool)
    ]
    if not visible_ore:
        raise PolicyError("no visible ore cell was found")

    available_ore = visible_ore.copy()
    orders = []
    for unit in candidates:
        if not available_ore:
            break
        cell = unit["cell"]
        target = min(
            available_ore,
            key=lambda ore: (
                (ore["x"] - cell["x"]) ** 2 + (ore["y"] - cell["y"]) ** 2,
                -int(ore.get("value", 0)),
            ),
        )
        available_ore.remove(target)
        orders.append(
            {
                "unit_id": int(unit["unit_id"]),
                "type_id": str(unit.get("type_id", "")),
                "target_cell": {"x": target["x"], "y": target["y"]},
            }
        )
    if len(orders) != len(candidates):
        raise PolicyError("not enough distinct visible ore cells for all harvesters")
    return orders


@dataclass(frozen=True)
class CommandPolicy:
    commands_enabled: bool
    session_enabled: bool
    allowed_modes: frozenset[str]
    max_snapshot_age_ms: int
    allow_network_game: bool = False

    def validate(
        self,
        state: dict[str, Any],
        unit_id: int,
        action: str,
        target_cell: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not self.commands_enabled:
            raise PolicyError("commands are disabled in plugin configuration")
        if not self.session_enabled:
            raise PolicyError("control is disabled for this N.E.K.O session")
        if action not in ALLOWED_ACTIONS:
            raise PolicyError(f"action is not allowed: {action}")

        game = _mapping(state.get("game"), "game")
        if not game.get("running"):
            raise PolicyError("Mental Omega is not in a running match")
        if game.get("network_game") is True and not self.allow_network_game:
            raise PolicyError("commands are forbidden in network games")
        mode = game.get("mode")
        if mode not in self.allowed_modes:
            raise PolicyError(f"game mode is not allowed: {mode}")

        age = state.get("snapshot_age_ms")
        if isinstance(age, bool) or not isinstance(age, (int, float)):
            raise PolicyError("snapshot age is missing")
        if age < 0 or age > self.max_snapshot_age_ms:
            raise PolicyError(f"snapshot is stale: {age} ms")

        player = _mapping(state.get("player"), "player")
        house_id = player.get("house_id")
        unit = _find_harvester(state, unit_id)
        if unit.get("owner_house_id") != house_id:
            raise PolicyError("unit is not owned by the current player")
        if unit.get("alive") is not True or unit.get("controllable") is not True:
            raise PolicyError("unit is not alive and controllable")

        normalized_target = _validate_target(state, action, target_cell)
        return {
            "unit_id": unit_id,
            "action": action,
            "target_cell": normalized_target,
        }

    def validate_unit(
        self,
        state: dict[str, Any],
        unit_id: int,
        action: str,
        target_cell: dict[str, Any] | None = None,
        target_object_id: int | None = None,
        group_number: int | None = None,
        replace_group: bool = False,
        override_team: bool = False,
    ) -> dict[str, Any]:
        self._validate_session(state)
        if action not in UNIT_ACTIONS:
            raise PolicyError(f"unit action is not allowed: {action}")
        source = _find_entity(state, unit_id)
        if source.get("relation") != "self":
            raise PolicyError("source object is not owned by the current player")
        if source.get("alive") is not True or source.get("controllable") is not True:
            raise PolicyError("source object is not alive and controllable")
        if action in UNIT_CELL_ACTIONS:
            normalized_cell = _validate_target(state, "move", target_cell)
        else:
            if target_cell is not None:
                raise PolicyError(f"{action} does not accept a target cell")
            normalized_cell = None
        if action in UNIT_OBJECT_ACTIONS:
            if isinstance(target_object_id, bool) or not isinstance(target_object_id, int):
                raise PolicyError(f"{action} requires a target object")
            target = _find_entity(state, target_object_id)
            if target.get("visible") is not True:
                raise PolicyError("target object is not currently visible")
        elif target_object_id is not None:
            raise PolicyError(f"{action} does not accept a target object")
        if action == "assign_group":
            if isinstance(group_number, bool) or not isinstance(group_number, int):
                raise PolicyError("assign_group requires an integer group_number")
            if not 0 <= group_number <= 9:
                raise PolicyError("group_number must be between 0 and 9")
            if not isinstance(replace_group, bool):
                raise PolicyError("replace_group must be a boolean")
        elif group_number is not None or replace_group is not False:
            raise PolicyError("group options require assign_group")
        if not isinstance(override_team, bool):
            raise PolicyError("override_team must be a boolean")
        if override_team and action not in {"move", "retreat", "explore"}:
            raise PolicyError("team override is only available for movement orders")
        return {
            "unit_id": unit_id,
            "action": action,
            "target_cell": normalized_cell,
            "target_object_id": target_object_id,
            "group_number": group_number,
            "replace_group": replace_group if action == "assign_group" else None,
            "override_team": override_team,
        }

    def _validate_session(self, state: dict[str, Any]) -> None:
        if not self.commands_enabled:
            raise PolicyError("commands are disabled in plugin configuration")
        if not self.session_enabled:
            raise PolicyError("control is disabled for this N.E.K.O session")
        game = _mapping(state.get("game"), "game")
        if not game.get("running"):
            raise PolicyError("Mental Omega is not in a running match")
        if game.get("network_game") is True and not self.allow_network_game:
            raise PolicyError("commands are forbidden in network games")
        mode = game.get("mode")
        if mode not in self.allowed_modes:
            raise PolicyError(f"game mode is not allowed: {mode}")
        age = state.get("snapshot_age_ms")
        if isinstance(age, bool) or not isinstance(age, (int, float)):
            raise PolicyError("snapshot age is missing")
        if age < 0 or age > self.max_snapshot_age_ms:
            raise PolicyError(f"snapshot is stale: {age} ms")


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError(f"{name} is missing from bridge state")
    return value


def _number(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _valid_cell(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return all(
        isinstance(value.get(axis), int) and not isinstance(value.get(axis), bool)
        for axis in ("x", "y")
    )


def _find_harvester(state: dict[str, Any], unit_id: int) -> dict[str, Any]:
    if isinstance(unit_id, bool) or not isinstance(unit_id, int):
        raise PolicyError("unit_id must be an integer")
    harvesters = state.get("harvesters")
    if not isinstance(harvesters, list):
        raise PolicyError("harvester list is missing")
    for unit in harvesters:
        if isinstance(unit, dict) and unit.get("unit_id") == unit_id:
            return unit
    raise PolicyError(f"harvester was not found: {unit_id}")


def _find_entity(state: dict[str, Any], object_id: int) -> dict[str, Any]:
    if isinstance(object_id, bool) or not isinstance(object_id, int):
        raise PolicyError("object_id must be an integer")
    entities = state.get("visible_entities")
    if not isinstance(entities, list):
        raise PolicyError("visible entity list is missing")
    for entity in entities:
        if isinstance(entity, dict) and entity.get("object_id") == object_id:
            return entity
    raise PolicyError(f"object was not found in current telemetry: {object_id}")


def _validate_target(
    state: dict[str, Any],
    action: str,
    target_cell: dict[str, Any] | None,
) -> dict[str, int] | None:
    if action not in TARGET_ACTIONS:
        if target_cell is not None:
            raise PolicyError(f"{action} does not accept a target cell")
        return None
    if not isinstance(target_cell, dict):
        raise PolicyError(f"{action} requires a target cell")
    x = target_cell.get("x")
    y = target_cell.get("y")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (x, y)):
        raise PolicyError("target cell coordinates must be integers")
    # MapRect describes the scenario rectangle, not the upper bounds of RA2's
    # isometric CellStruct coordinates. The native game thread is the authority
    # for CoordinatesLegal and movement-zone/passability checks.
    if not all(-32768 <= value <= 32767 for value in (x, y)):
        raise PolicyError("target cell is outside the native coordinate range")
    return {"x": x, "y": y}
