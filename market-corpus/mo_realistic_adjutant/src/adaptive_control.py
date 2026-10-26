from __future__ import annotations

from typing import Any


def valid_cell(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(axis), int) and not isinstance(value.get(axis), bool)
        for axis in ("x", "y")
    )


def distance_sq(left: dict[str, Any], right: dict[str, Any]) -> int:
    left_cell = left.get("cell") if "cell" in left else left
    right_cell = right.get("cell") if "cell" in right else right
    if not valid_cell(left_cell) or not valid_cell(right_cell):
        return 2**31 - 1
    return (int(left_cell["x"]) - int(right_cell["x"])) ** 2 + (
        int(left_cell["y"]) - int(right_cell["y"])
    ) ** 2


def find_entity(state: dict[str, Any], object_id: int) -> dict[str, Any] | None:
    entities = state.get("visible_entities")
    if not isinstance(entities, list):
        return None
    return next(
        (
            item
            for item in entities
            if isinstance(item, dict)
            and item.get("object_id") == object_id
            and item.get("alive") is True
        ),
        None,
    )


def movement_progress(
    start_cell: dict[str, int],
    current_cell: dict[str, int],
    target_cell: dict[str, int],
) -> bool:
    if not all(valid_cell(cell) for cell in (start_cell, current_cell, target_cell)):
        return False
    return (
        current_cell != start_cell
        and distance_sq(current_cell, target_cell) <= distance_sq(start_cell, target_cell)
    )


def staging_cells(
    anchor: dict[str, int],
    origin: dict[str, int] | None = None,
    *,
    slot: int = 0,
    rings: tuple[int, ...] = (2, 3, 4, 5, 7, 9, 12),
) -> list[dict[str, int]]:
    if not valid_cell(anchor):
        return []
    offsets: list[tuple[int, int]] = []
    for radius in rings:
        offsets.extend(
            (
                (-radius, 0),
                (radius, 0),
                (0, -radius),
                (0, radius),
                (-radius, -radius),
                (radius, -radius),
                (-radius, radius),
                (radius, radius),
            )
        )
    cells = [
        {"x": int(anchor["x"]) + dx, "y": int(anchor["y"]) + dy}
        for dx, dy in offsets
        if -32768 <= int(anchor["x"]) + dx <= 32767
        and -32768 <= int(anchor["y"]) + dy <= 32767
    ]
    if origin and valid_cell(origin):
        cells.sort(key=lambda cell: (distance_sq(origin, cell), cell["x"], cell["y"]))
    if cells:
        rotation = max(0, int(slot)) % len(cells)
        cells = cells[rotation:] + cells[:rotation]
    return cells


def reacquire_target(
    state: dict[str, Any],
    previous: dict[str, Any],
    *,
    damaged_only: bool = True,
) -> dict[str, Any] | None:
    object_id = previous.get("object_id")
    if isinstance(object_id, int):
        exact = find_entity(state, object_id)
        exact_health = exact.get("health_ratio") if exact is not None else None
        if exact is not None and (
            not damaged_only
            or (
                isinstance(exact_health, (int, float))
                and not isinstance(exact_health, bool)
                and float(exact_health) < 1.0
            )
        ):
            return exact
    entities = state.get("visible_entities")
    if not isinstance(entities, list):
        return None
    type_id = str(previous.get("type_id", "")).upper()
    candidates = [
        item
        for item in entities
        if isinstance(item, dict)
        and item.get("relation") == "self"
        and item.get("alive") is True
        and item.get("visible") is True
        and (not type_id or str(item.get("type_id", "")).upper() == type_id)
        and item.get("object_id") != previous.get("object_id")
    ]
    if damaged_only:
        candidates = [
            item
            for item in candidates
            if isinstance(item.get("health_ratio"), (int, float))
            and not isinstance(item.get("health_ratio"), bool)
            and float(item["health_ratio"]) < 1.0
        ]
    return min(candidates, key=lambda item: distance_sq(previous, item), default=None)


def assign_repairers(
    sources: list[dict[str, Any]], targets: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if not targets:
        return []

    def score(source: dict[str, Any], target: dict[str, Any]) -> tuple[float, int, int]:
        health = target.get("health_ratio")
        health_ratio = float(health) if isinstance(health, (int, float)) else 1.0
        return (
            health_ratio,
            distance_sq(source, target),
            int(target.get("object_id", 0)),
        )

    assignments = []
    for source in sources:
        eligible = [
            target
            for target in targets
            if target.get("object_id") != source.get("object_id")
        ]
        if eligible:
            assignments.append((source, min(eligible, key=lambda target: score(source, target))))
    return assignments
