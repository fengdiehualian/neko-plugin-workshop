"""Spaced-repetition scheduling for problems worth re-solving.

A problem solved once is not a problem learned. The ones that took several
attempts, or that only fell after a hint, come back on a widening ladder of 1, 2,
4, 7, 15, 30 and 60 days: short steps first, because that is where a single
re-solve actually cements an idea, then weekly steps that merely keep it warm.

Feedback is deliberately four-way rather than three: forgetting drops an item
back to the shortest step, "shaky" *repeats* the current step instead of
advancing, "ok" moves one step and "easy" skips one. Advancing on a shaky recall
is how a review queue quietly turns into a pile of things you only think you
know.

Pure module: no SDK, no network, no clock (``today`` is a parameter).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from ._models import ReviewItem

# Day gaps per stage. The early doublings are the part that does the work; the
# tail stretches out for material that only needs occasional refresh.
REVIEW_INTERVALS: tuple[int, ...] = (1, 2, 4, 7, 15, 30, 60)
MAX_STAGE = len(REVIEW_INTERVALS) - 1

FEEDBACK_FORGOT = "forgot"
FEEDBACK_SHAKY = "shaky"
FEEDBACK_OK = "ok"
FEEDBACK_EASY = "easy"
FEEDBACKS: tuple[str, ...] = (FEEDBACK_FORGOT, FEEDBACK_SHAKY, FEEDBACK_OK, FEEDBACK_EASY)

ITEMS_KEY = "review_items"
ITEMS_VERSION = 1


def clamp_stage(stage: int) -> int:
    """Constrain a stage to the ladder (garbage input becomes stage 0/最大值)."""
    try:
        value = int(stage)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_STAGE, value))


def interval_days(stage: int) -> int:
    """Days until the next review at ``stage``."""
    return REVIEW_INTERVALS[clamp_stage(stage)]


def next_stage(stage: int, feedback: str) -> int:
    """Stage after a feedback grade.

    An unrecognised grade advances one step, the same as "ok": a typo in a
    caller must not silently reset somebody's whole queue to day one.
    """
    current = clamp_stage(stage)
    grade = str(feedback or "").strip().lower()
    if grade == FEEDBACK_FORGOT:
        return 0
    if grade == FEEDBACK_SHAKY:
        return current
    if grade == FEEDBACK_EASY:
        return min(current + 2, MAX_STAGE)
    return min(current + 1, MAX_STAGE)


def add_days(local_day: str, days: int) -> str:
    """``local_day`` shifted by ``days`` (``""`` when the date is unusable)."""
    try:
        start = date.fromisoformat(str(local_day))
    except (TypeError, ValueError):
        return ""
    return (start + timedelta(days=int(days))).isoformat()


def schedule(stage: int, feedback: str, today: str) -> tuple[int, str]:
    """New ``(stage, due_on)`` for an item reviewed today."""
    result_stage = next_stage(stage, feedback)
    return result_stage, add_days(today, interval_days(result_stage))


def new_item(
    pid: str,
    *,
    today: str = "",
    title: str = "",
    difficulty: int = 0,
    tags: tuple[str, ...] = (),
    stage: int = 0,
) -> ReviewItem:
    """Queue a problem for its first review one interval from ``today``."""
    return ReviewItem(
        pid=str(pid).strip(),
        title=str(title or ""),
        stage=clamp_stage(stage),
        due_on=add_days(today, interval_days(stage)),
        added_on=str(today or ""),
        difficulty=int(difficulty or 0),
        tags=tuple(str(tag) for tag in tags),
    )


def review_item(item: ReviewItem, feedback: str, today: str) -> ReviewItem:
    """Apply a feedback grade, tracking the lapse count along the way."""
    stage, due_on = schedule(item.stage, feedback, today)
    lapsed = str(feedback or "").strip().lower() == FEEDBACK_FORGOT
    return ReviewItem(
        pid=item.pid,
        title=item.title,
        stage=stage,
        due_on=due_on,
        added_on=item.added_on,
        last_reviewed_on=str(today or ""),
        reviews=item.reviews + 1,
        lapses=item.lapses + (1 if lapsed else 0),
        difficulty=item.difficulty,
        tags=item.tags,
    )


def is_due(item: ReviewItem, today: str, *, include_undated: bool = True) -> bool:
    """Whether an item is due on ``today``.

    An item with no (or an unparsable) due date counts as due: the queue must not
    be able to lose an entry just because a date went missing.
    """
    if not item.due_on:
        return include_undated
    try:
        return date.fromisoformat(item.due_on) <= date.fromisoformat(str(today))
    except (TypeError, ValueError):
        return include_undated


def due_items(items: Mapping[str, ReviewItem], today: str, *, limit: int = 0) -> list[ReviewItem]:
    """Due items, oldest due date first (then pid, so output is stable)."""
    due = [item for item in items.values() if is_due(item, today)]
    due.sort(key=lambda item: (item.due_on or "", item.pid))
    return due[:limit] if limit > 0 else due


def due_count(items: Mapping[str, ReviewItem], today: str) -> int:
    """How many items are waiting today."""
    return len(due_items(items, today))


def load_items(raw: Any) -> dict[str, ReviewItem]:
    """Rebuild the queue from a stored value, tolerating junk or an old version."""
    payload = raw
    if hasattr(payload, "value"):  # store get() wrapper
        payload = payload.value
    if not isinstance(payload, Mapping) or payload.get("v") != ITEMS_VERSION:
        return {}
    entries = payload.get("items")
    if not isinstance(entries, Mapping):
        return {}
    items: dict[str, ReviewItem] = {}
    for pid, entry in entries.items():
        if not isinstance(entry, Mapping):
            continue
        raw_tags = entry.get("tags")
        tags = tuple(str(tag) for tag in raw_tags if str(tag)) if isinstance(raw_tags, list) else ()
        items[str(pid)] = ReviewItem(
            pid=str(pid),
            title=str(entry.get("title") or ""),
            stage=clamp_stage(entry.get("stage")),
            due_on=str(entry.get("due_on") or ""),
            added_on=str(entry.get("added_on") or ""),
            last_reviewed_on=str(entry.get("last_reviewed_on") or ""),
            reviews=_safe_int(entry.get("reviews")),
            lapses=_safe_int(entry.get("lapses")),
            difficulty=_safe_int(entry.get("difficulty")),
            tags=tags,
        )
    return items


def dump_items(items: Mapping[str, ReviewItem]) -> dict[str, Any]:
    """Serialise the queue for the plugin store."""
    return {
        "v": ITEMS_VERSION,
        "items": {
            pid: {
                "title": item.title,
                "stage": item.stage,
                "due_on": item.due_on,
                "added_on": item.added_on,
                "last_reviewed_on": item.last_reviewed_on,
                "reviews": item.reviews,
                "lapses": item.lapses,
                "difficulty": item.difficulty,
                "tags": list(item.tags),
            }
            for pid, item in items.items()
        },
    }


def _safe_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


__all__ = [
    "FEEDBACKS",
    "FEEDBACK_EASY",
    "FEEDBACK_FORGOT",
    "FEEDBACK_OK",
    "FEEDBACK_SHAKY",
    "ITEMS_KEY",
    "MAX_STAGE",
    "REVIEW_INTERVALS",
    "add_days",
    "clamp_stage",
    "due_count",
    "due_items",
    "dump_items",
    "interval_days",
    "is_due",
    "load_items",
    "new_item",
    "next_stage",
    "review_item",
    "schedule",
]
