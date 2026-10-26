"""Luogu plugin metadata-backfill tests.

``/record/list`` carries a submission's difficulty but no tags, so these helpers
decide which problems still need a fetch, what the cache remembers across runs,
and how a fetched problem fills in a record without overwriting it.
"""

from __future__ import annotations

import pytest
from plugin.plugins.luogu import _backfill as b
from plugin.plugins.luogu._models import SubmissionRecord

pytestmark = pytest.mark.plugin_unit


def _rec(pid: str, *, difficulty: int = 0, tags: tuple[str, ...] = ()) -> SubmissionRecord:
    return SubmissionRecord(
        pid=pid,
        title=pid,
        status="AC",
        difficulty=difficulty,
        tags=tags,
        solved=True,
        submitted_at="2026-09-01T00:00:00Z",
    )


def test_is_fetchable_pid() -> None:
    assert b.is_fetchable_pid("P1001")
    assert b.is_fetchable_pid("CF1234A")
    assert b.is_fetchable_pid("AT_dp_a")   # the digit-free AT_ family is still fetchable
    assert not b.is_fetchable_pid("")
    assert not b.is_fetchable_pid(None)
    assert not b.is_fetchable_pid("P")           # too short to be a problem
    assert not b.is_fetchable_pid("1001")        # no letter prefix
    assert not b.is_fetchable_pid("luogu-123")   # mangled key from a drifted payload
    assert not b.is_fetchable_pid("P 1001")      # whitespace means it is not an id
    assert not b.is_fetchable_pid("problem")     # no digits and not an AT_ id


def test_needs_meta() -> None:
    assert b.needs_meta(_rec("P1001"))
    assert b.needs_meta(_rec("P1001", difficulty=3))            # difficulty but no tags
    assert not b.needs_meta(_rec("P1001", difficulty=3, tags=("42",)))


def test_plan_backfill_dedupes_preserves_order_and_caps() -> None:
    cache = {"P1002": b.PidMeta(difficulty=3, tags=("42",), fetched=True)}
    planned = b.plan_backfill(
        ["P1001", "P1001", "P1002", "not an id", "P1003", "P1004"],
        cache,
        limit=2,
        now=1000.0,
        backoff_seconds=300.0,
    )
    assert planned == ["P1001", "P1003"]


def test_plan_backfill_respects_backoff_then_retries() -> None:
    cache = {"P1001": b.PidMeta(failed_at=1000.0)}
    assert b.plan_backfill(["P1001"], cache, limit=5, now=1100.0, backoff_seconds=300.0) == []
    assert b.plan_backfill(["P1001"], cache, limit=5, now=1300.0, backoff_seconds=300.0) == ["P1001"]


def test_plan_backfill_with_zero_limit_does_nothing() -> None:
    assert b.plan_backfill(["P1001"], {}, limit=0, now=0.0, backoff_seconds=300.0) == []


def test_cache_round_trip() -> None:
    cache = {"P1001": b.PidMeta(difficulty=3, tags=("42", "108"), fetched=True)}
    assert b.load_cache(b.dump_cache(cache)) == cache


def test_load_cache_unwraps_a_store_result() -> None:
    class _Result:
        value = {"v": b.CACHE_VERSION, "items": {"P1": {"difficulty": 1, "fetched": True}}}

    assert b.load_cache(_Result()) == {"P1": b.PidMeta(difficulty=1, fetched=True)}


def test_load_cache_tolerates_junk_and_foreign_versions() -> None:
    assert b.load_cache(None) == {}
    assert b.load_cache("nope") == {}
    assert b.load_cache({"v": 99, "items": {"P1": {}}}) == {}
    assert b.load_cache({"v": b.CACHE_VERSION, "items": "nope"}) == {}
    loaded = b.load_cache(
        {
            "v": b.CACHE_VERSION,
            "items": {"P1": {"difficulty": "3", "tags": ["42", 108], "fetched": True}},
        }
    )
    assert loaded == {"P1": b.PidMeta(difficulty=3, tags=("42", "108"), fetched=True)}


def test_apply_meta_fills_only_missing_fields() -> None:
    meta = b.PidMeta(difficulty=7, tags=("42",), fetched=True)
    filled = b.apply_meta(_rec("P1"), meta)
    assert filled.difficulty == 7
    assert filled.tags == ("42",)
    # The record's own difficulty wins: it is the row's value, while the problem
    # page reports the problem's *current* rating.
    kept = b.apply_meta(_rec("P1", difficulty=4, tags=("108",)), meta)
    assert kept.difficulty == 4
    assert kept.tags == ("108",)
    assert b.apply_meta(_rec("P1"), None) == _rec("P1")


def test_apply_meta_returns_the_same_object_when_there_is_nothing_to_fill() -> None:
    record = _rec("P1", difficulty=4, tags=("108",))
    assert b.apply_meta(record, b.PidMeta(difficulty=7, tags=("42",), fetched=True)) is record
