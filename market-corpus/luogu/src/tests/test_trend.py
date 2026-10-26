"""Luogu plugin trend / heatmap bucketing tests.

A submission at 00:30 in Asia/Shanghai belongs to *that* local day even though
its UTC timestamp says the day before, so these tests pin the local-day and ISO
week semantics, plus the "distinct problems" rule that keeps a day of retries
from reading as progress.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from plugin.plugins.luogu import _trend as t
from plugin.plugins.luogu._models import SubmissionRecord

pytestmark = pytest.mark.plugin_unit

TZ = ZoneInfo("Asia/Shanghai")
TODAY = date(2026, 9, 27)


def _stamp(local_day: str, hour: int = 12) -> str:
    """ISO-8601 UTC string for ``hour`` local time on ``local_day``."""
    local = datetime.fromisoformat(f"{local_day}T{hour:02d}:00:00").replace(tzinfo=TZ)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rec(pid: str, day: str, *, solved: bool = True, hour: int = 12) -> SubmissionRecord:
    return SubmissionRecord(
        pid=pid,
        title=pid,
        status="Accepted" if solved else "Wrong Answer",
        difficulty=3,
        tags=("42",),
        solved=solved,
        submitted_at=_stamp(day, hour),
    )


def test_parse_iso_accepts_z_and_naive_and_rejects_junk() -> None:
    assert t.parse_iso("2026-09-27T05:31:32Z") == datetime(2026, 9, 27, 5, 31, 32, tzinfo=timezone.utc)
    assert t.parse_iso("2026-09-27 05:31:32") == datetime(2026, 9, 27, 5, 31, 32, tzinfo=timezone.utc)
    assert t.parse_iso("") is None
    assert t.parse_iso(None) is None
    aware = datetime(2026, 9, 27, 13, 31, 32, tzinfo=TZ)
    assert t.parse_iso(aware) == aware.astimezone(timezone.utc)


def test_local_date_uses_the_local_calendar_day() -> None:
    # 16:30 UTC on the 26th is 00:30 on the 27th in Shanghai.
    assert t.local_date("2026-09-26T16:30:00Z", TZ) == "2026-09-27"
    assert t.local_date(_stamp("2026-09-27", hour=0), TZ) == "2026-09-27"
    assert t.local_date("nonsense", TZ) == ""


def test_week_start_is_the_monday_of_that_iso_week() -> None:
    day = date(2026, 9, 27)
    start = date.fromisoformat(t.week_start(day.isoformat()))
    assert start.weekday() == 0
    assert start <= day < start + timedelta(days=7)
    assert t.week_key(start.isoformat()) == t.week_key(day.isoformat())
    assert t.week_start("bad") == ""
    assert t.week_key("bad") == ""


def test_compute_trend_buckets_by_local_week_and_dedupes_ac() -> None:
    last_week = TODAY - timedelta(days=7)
    records = [
        _rec("P1", TODAY.isoformat()),
        _rec("P1", TODAY.isoformat()),  # same problem twice in a day -> one AC
        _rec("P2", TODAY.isoformat()),
        _rec("P3", TODAY.isoformat(), solved=False),
        _rec("P4", last_week.isoformat()),
        _rec("P5", "2020-01-01"),  # far outside the window
    ]
    trend = t.compute_trend(records, tz=TZ, today=TODAY, weeks=3)
    assert len(trend.weeks) == 3
    assert trend.weeks[-1].attempts == 4
    assert trend.weeks[-1].ac_count == 2  # distinct solved problems, not submissions
    assert trend.weeks[-1].active_days == 1
    assert trend.weeks[-2].ac_count == 1
    assert trend.total_attempts == 5  # the 2020 record is excluded
    assert trend.total_ac == 3
    assert trend.active_weeks == 2
    assert trend.weeks[0].attempts == 0  # an empty week is still reported


def test_compute_trend_reports_direction_against_the_baseline() -> None:
    records = [
        _rec(f"OLD{index}", (TODAY - timedelta(days=7 * (index + 3))).isoformat())
        for index in range(6)
    ] + [
        _rec(f"NEW{index}", (TODAY - timedelta(days=index)).isoformat())
        for index in range(5)
    ]
    trend = t.compute_trend(records, tz=TZ, today=TODAY, weeks=8)
    assert trend.direction == "up"
    assert trend.recent_ac > trend.baseline_ac


def test_compute_trend_is_unknown_without_comparable_weeks() -> None:
    assert t.compute_trend([], tz=TZ, today=TODAY, weeks=4).direction == "unknown"
    # A three-week window leaves a single baseline week: not enough to compare,
    # so the report says "unknown" rather than inventing a direction.
    recent = [_rec(f"P{index}", (TODAY - timedelta(days=index)).isoformat()) for index in range(3)]
    assert t.compute_trend(recent, tz=TZ, today=TODAY, weeks=3).direction == "unknown"
    # Widening the window gives it a baseline of zeroes to beat.
    assert t.compute_trend(recent, tz=TZ, today=TODAY, weeks=4).direction == "up"


def test_compute_trend_with_zero_weeks_is_empty() -> None:
    trend = t.compute_trend([_rec("P1", TODAY.isoformat())], tz=TZ, today=TODAY, weeks=0)
    assert trend.weeks == ()
    assert trend.total_attempts == 0


def test_compute_trend_ignores_submissions_without_a_usable_timestamp() -> None:
    broken = SubmissionRecord(pid="P1", status="Accepted", solved=True, submitted_at="")
    trend = t.compute_trend([broken], tz=TZ, today=TODAY, weeks=4)
    assert trend.total_attempts == 0


def test_daily_ac_counts_dedupes_and_respects_the_window() -> None:
    records = [
        _rec("P1", TODAY.isoformat()),
        _rec("P1", TODAY.isoformat()),
        _rec("P2", TODAY.isoformat()),
        _rec("P3", (TODAY - timedelta(days=1)).isoformat(), solved=False),
        _rec("P4", (TODAY - timedelta(days=10)).isoformat()),
    ]
    counts = t.daily_ac_counts(records, tz=TZ, days=7, today=TODAY)
    assert counts == {TODAY.isoformat(): 2}
    assert t.daily_ac_counts(records, tz=TZ, days=0, today=TODAY) == {}
