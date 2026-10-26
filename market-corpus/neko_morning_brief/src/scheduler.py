"""When should the brief fire?

Kept free of I/O so the logic can be tested with a plain ``datetime`` and a
``has_run`` callable.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_time(value: object) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    match = _TIME.match(value.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def normalize_times(values: object) -> tuple[str, ...]:
    """Return ``("08:00", "22:30")``-style slots; falls back to ``("08:00",)``."""

    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return ("08:00",)
    slots: set[str] = set()
    for raw in values:
        parsed = parse_time(raw)
        if parsed is None:
            continue
        slots.add(f"{parsed[0]:02d}:{parsed[1]:02d}")
    if not slots:
        return ("08:00",)
    return tuple(sorted(slots))


def slot_target(now: datetime, slot: str) -> datetime | None:
    parsed = parse_time(slot)
    if parsed is None:
        return None
    return now.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0)


def due_slot(
    now: datetime,
    times: tuple[str, ...],
    catch_up_minutes: int,
    has_run,
) -> str | None:
    """Earliest slot that is due and not yet run today, else ``None``.

    A slot becomes due at its wall-clock time and stays due for
    ``catch_up_minutes``; after that it is considered missed for the day.
    """

    window = max(0, int(catch_up_minutes)) * 60
    for slot in times:
        target = slot_target(now, slot)
        if target is None:
            continue
        delta = (now - target).total_seconds()
        if delta < 0:
            continue
        if delta > window:
            continue
        if has_run(now.strftime("%Y-%m-%d"), slot):
            continue
        return slot
    return None


def next_run_at(now: datetime, times: tuple[str, ...]) -> datetime | None:
    """Next future slot today, else tomorrow's first slot."""

    upcoming: list[datetime] = []
    for slot in times:
        target = slot_target(now, slot)
        if target is not None and target > now:
            upcoming.append(target)
    if upcoming:
        return min(upcoming)
    for slot in times:
        target = slot_target(now + timedelta(days=1), slot)
        if target is not None:
            return target
    return None


__all__ = ["due_slot", "next_run_at", "normalize_times", "parse_time", "slot_target"]
