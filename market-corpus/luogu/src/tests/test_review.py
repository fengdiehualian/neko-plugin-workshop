"""Luogu plugin spaced-repetition tests.

The ladder widens (1/2/4/7/15/30/60 days), feedback moves along it in four
grades rather than three, and the queue must survive a store round-trip without
losing entries whose date went missing.
"""

from __future__ import annotations

import pytest
from plugin.plugins.luogu import _review as r

pytestmark = pytest.mark.plugin_unit

TODAY = "2026-09-27"


def test_interval_ladder_widens_and_clamps() -> None:
    assert r.interval_days(0) == r.REVIEW_INTERVALS[0]
    assert r.interval_days(1) > r.interval_days(0)
    assert r.interval_days(r.MAX_STAGE) == r.REVIEW_INTERVALS[-1]
    assert r.interval_days(999) == r.REVIEW_INTERVALS[-1]  # clamped, never IndexError
    assert r.interval_days(-5) == r.REVIEW_INTERVALS[0]


def test_next_stage_grades() -> None:
    assert r.next_stage(3, r.FEEDBACK_FORGOT) == 0
    assert r.next_stage(3, r.FEEDBACK_SHAKY) == 3  # repeats instead of advancing
    assert r.next_stage(3, r.FEEDBACK_OK) == 4
    assert r.next_stage(3, r.FEEDBACK_EASY) == 5
    assert r.next_stage(r.MAX_STAGE, r.FEEDBACK_EASY) == r.MAX_STAGE  # capped
    # An unrecognised grade advances, exactly like "ok": a typo anywhere in a
    # caller must not reset the queue to day one.
    assert r.next_stage(3, "") == 4
    assert r.next_stage(3, "nonsense") == 4


def test_schedule_returns_stage_and_due_date() -> None:
    stage, due = r.schedule(0, r.FEEDBACK_OK, TODAY)
    assert stage == 1
    assert due == r.add_days(TODAY, r.REVIEW_INTERVALS[1])
    forgot_stage, forgot_due = r.schedule(4, r.FEEDBACK_FORGOT, TODAY)
    assert forgot_stage == 0
    assert forgot_due == r.add_days(TODAY, r.REVIEW_INTERVALS[0])


def test_add_days_handles_bad_input() -> None:
    assert r.add_days(TODAY, 1) == "2026-09-28"
    assert r.add_days("2026-12-31", 1) == "2027-01-01"
    assert r.add_days("nonsense", 3) == ""
    assert r.add_days("", 3) == ""


def test_new_item_starts_due_after_one_interval() -> None:
    item = r.new_item("P1001", today=TODAY, title="A+B", difficulty=1, tags=("模拟",))
    assert item.pid == "P1001"
    assert item.stage == 0
    assert item.due_on == r.add_days(TODAY, 1)
    assert item.added_on == TODAY
    assert item.title == "A+B"
    assert item.tags == ("模拟",)


def test_review_item_tracks_reviews_and_lapses() -> None:
    item = r.new_item("P1001", today=TODAY)
    once = r.review_item(item, r.FEEDBACK_OK, TODAY)
    assert once.reviews == 1
    assert once.lapses == 0
    assert once.last_reviewed_on == TODAY
    twice = r.review_item(once, r.FEEDBACK_FORGOT, TODAY)
    assert twice.reviews == 2
    assert twice.lapses == 1
    assert twice.stage == 0


def test_due_selection_orders_by_due_date_and_counts() -> None:
    items = {
        "P2": r.new_item("P2", today="2026-09-25"),
        "P1": r.new_item("P1", today="2026-09-20"),
        "P3": r.new_item("P3", today=TODAY),  # due tomorrow
    }
    due = r.due_items(items, r.add_days(TODAY, 1))
    assert [item.pid for item in due] == ["P1", "P2", "P3"]
    assert r.due_count(items, TODAY) == 2
    assert [item.pid for item in r.due_items(items, r.add_days(TODAY, 1), limit=2)] == ["P1", "P2"]


def test_items_without_a_date_are_still_due() -> None:
    """A lost date must not be able to hide an entry from the queue forever."""
    items = {"P1": r.new_item("P1", today="")}
    assert r.is_due(items["P1"], TODAY)
    assert r.due_count(items, TODAY) == 1


def test_queue_round_trips_through_the_store() -> None:
    items = {
        "P1001": r.review_item(r.new_item("P1001", today=TODAY, title="A+B", difficulty=1, tags=("模拟",)), "ok", TODAY),
        "P4719": r.new_item("P4719", today=TODAY),
    }
    assert r.load_items(r.dump_items(items)) == items


def test_load_items_unwraps_a_store_result_and_tolerates_junk() -> None:
    class _Result:
        value = {"v": r.ITEMS_VERSION, "items": {"P1": {"stage": "2", "due_on": TODAY}}}

    loaded = r.load_items(_Result())
    assert loaded["P1"].stage == 2
    assert r.load_items(None) == {}
    assert r.load_items("nope") == {}
    assert r.load_items({"v": 99, "items": {"P1": {}}}) == {}
    assert r.load_items({"v": r.ITEMS_VERSION, "items": "nope"}) == {}
    assert r.load_items({"v": r.ITEMS_VERSION, "items": {"P1": {"stage": "x"}}})["P1"].stage == 0
