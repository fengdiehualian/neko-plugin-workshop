"""Weekly activity trend and daily AC counts, bucketed in local time.

Luogu submission times arrive as epoch seconds and are normalised to ISO-8601 UTC
strings while parsing, but "a day of practice" is a *local* calendar day: a
submission at 00:30 in Asia/Shanghai belongs to that day, not to the previous UTC
one. Every function here therefore takes the timezone explicitly, and ``today``
is a parameter rather than a clock read so results are reproducible.

No SDK, no network, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from statistics import fmean
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from ._models import SubmissionRecord, TrendReport, TrendWeek


@dataclass
class _Bucket:
    """Mutable accumulator for one week; converted to a frozen TrendWeek at the end."""

    start: str
    attempts: int = 0
    pids: set[str] = field(default_factory=set)
    days: set[str] = field(default_factory=set)

DEFAULT_WEEKS = 12
# How many of the newest weeks are compared against the older baseline, and the
# AC-per-week gap below which the trend is called flat rather than up or down.
RECENT_WEEKS = 2
FLAT_BAND = 1.0


def parse_iso(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp into an aware UTC datetime (``None`` if not one)."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    candidate = f"{text[:-1]}+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def local_date(value: object, tz: ZoneInfo) -> str:
    """Local calendar date (``YYYY-MM-DD``) of a timestamp, or ``""`` if unusable."""
    stamp = parse_iso(value)
    if stamp is None:
        return ""
    return stamp.astimezone(tz).date().isoformat()


def week_start(local_day: str) -> str:
    """Monday of the ISO week containing ``local_day`` (``""`` if unparsable)."""
    try:
        day = date.fromisoformat(local_day)
    except (TypeError, ValueError):
        return ""
    return (day - timedelta(days=day.weekday())).isoformat()


def week_key(local_day: str) -> str:
    """ISO week label such as ``2026-W39`` for a local date (``""`` if unparsable)."""
    try:
        iso_year, iso_week, _ = date.fromisoformat(local_day).isocalendar()
    except (TypeError, ValueError):
        return ""
    return f"{iso_year}-W{iso_week:02d}"


def daily_ac_counts(
    records: Sequence[SubmissionRecord],
    *,
    tz: ZoneInfo,
    days: int,
    today: date,
) -> dict[str, int]:
    """Distinct problems solved per local day over the last ``days`` days.

    Distinct on purpose: solving the same problem three times in a day is one
    problem of progress, so a heatmap built on this agrees with the streak count
    instead of turning retries into work.
    """
    counts: dict[str, int] = {}
    if days <= 0:
        return counts
    oldest = today - timedelta(days=days - 1)
    seen: set[tuple[str, str]] = set()
    for record in records:
        if not record.solved:
            continue
        day = local_date(record.submitted_at, tz)
        if not day:
            continue
        try:
            day_value = date.fromisoformat(day)
        except ValueError:
            continue
        if not oldest <= day_value <= today:
            continue
        marker = (day, record.pid)
        if marker in seen:
            continue
        seen.add(marker)
        counts[day] = counts.get(day, 0) + 1
    return counts


def compute_trend(
    records: Sequence[SubmissionRecord],
    *,
    tz: ZoneInfo,
    today: date,
    weeks: int = DEFAULT_WEEKS,
) -> TrendReport:
    """Bucket submissions into the last ``weeks`` ISO weeks, oldest first.

    Attempts count submissions; AC counts count *distinct* solved problems, so a
    week of retries does not read as a week of progress. Weeks without activity
    are still emitted with zeros — a gap is information, not something to hide.
    """
    if weeks <= 0:
        return TrendReport()
    current_monday = date.fromisoformat(week_start(today.isoformat()))
    buckets: dict[str, _Bucket] = {}
    order: list[str] = []
    for offset in range(weeks - 1, -1, -1):
        start = current_monday - timedelta(days=7 * offset)
        key = week_key(start.isoformat())
        order.append(key)
        buckets[key] = _Bucket(start=start.isoformat())

    for record in records:
        day = local_date(record.submitted_at, tz)
        if not day:
            continue
        bucket = buckets.get(week_key(day))
        if bucket is None:  # outside the requested window
            continue
        bucket.attempts += 1
        bucket.days.add(day)
        if record.solved:
            bucket.pids.add(record.pid)

    trend_weeks: list[TrendWeek] = []
    for key in order:
        bucket = buckets[key]
        ac_count = len(bucket.pids)
        trend_weeks.append(
            TrendWeek(
                week=key,
                start_date=bucket.start,
                attempts=bucket.attempts,
                ac_count=ac_count,
                ac_rate=round(ac_count / bucket.attempts, 4) if bucket.attempts else 0.0,
                active_days=len(bucket.days),
            )
        )

    total_attempts = sum(week.attempts for week in trend_weeks)
    total_ac = sum(week.ac_count for week in trend_weeks)
    recent = trend_weeks[-RECENT_WEEKS:]
    older = trend_weeks[:-RECENT_WEEKS]
    recent_ac = fmean(week.ac_count for week in recent) if recent else 0.0
    baseline_ac = fmean(week.ac_count for week in older) if older else 0.0
    if len(older) < 2 or total_ac == 0:
        direction = "unknown"
    elif recent_ac - baseline_ac >= FLAT_BAND:
        direction = "up"
    elif baseline_ac - recent_ac >= FLAT_BAND:
        direction = "down"
    else:
        direction = "flat"

    return TrendReport(
        weeks=tuple(trend_weeks),
        total_attempts=total_attempts,
        total_ac=total_ac,
        active_weeks=sum(1 for week in trend_weeks if week.attempts),
        average_ac_rate=round(total_ac / total_attempts, 4) if total_attempts else 0.0,
        direction=direction,
        recent_ac=round(recent_ac, 2),
        baseline_ac=round(baseline_ac, 2),
    )


def heatmap_levels(counts: Mapping[str, int]) -> dict[str, int]:
    """Intensity level per day, 1..4 for active days and 0 for empty ones.

    Levels are ranked against the user's *own* spread of active-day counts rather
    than fixed thresholds: somebody solving one to three problems a day would sit
    entirely in the palest shade under absolute buckets, and a rank-based scale
    still shows them four distinguishable shades. A history where every active day
    is identical has nothing to rank, and renders flat at the top level — that is
    the honest picture of a perfectly even week.
    """
    levels = {day: 0 for day in counts}
    unique = sorted({count for count in counts.values() if count > 0})
    if not unique:
        return levels
    if len(unique) == 1:
        return {day: (4 if count > 0 else 0) for day, count in counts.items()}
    rank = {value: index for index, value in enumerate(unique)}
    span = len(unique) - 1
    for day, count in counts.items():
        if count > 0:
            levels[day] = 1 + round(3 * rank[count] / span)
    return levels


def heatmap_grid(
    counts: Mapping[str, int],
    *,
    today: date,
    days: int,
    levels: Mapping[str, int] | None = None,
) -> list[list[dict[str, Any]]]:
    """Week columns (Monday first, seven cells each) for a contribution-style grid.

    Columns run from the Monday of the week holding the oldest day through the
    Sunday of the week holding ``today``, so the last column is padded with
    ``future`` cells and the grid stays rectangular. Days before the window are
    marked ``in_range=false`` rather than omitted for the same reason.
    """
    if days <= 0:
        return []
    oldest = today - timedelta(days=days - 1)
    resolved = levels if levels is not None else heatmap_levels(counts)
    columns: list[list[dict[str, Any]]] = []
    column: list[dict[str, Any]] = []
    cursor = date.fromisoformat(week_start(oldest.isoformat()))
    while cursor <= today or column:
        label = cursor.isoformat()
        in_range = oldest <= cursor <= today
        column.append(
            {
                "date": label,
                "count": counts.get(label, 0) if in_range else 0,
                "level": resolved.get(label, 0) if in_range else 0,
                "in_range": in_range,
                "future": cursor > today,
            }
        )
        if cursor.weekday() == 6:  # Sunday closes a column
            columns.append(column)
            column = []
        cursor += timedelta(days=1)
    if column:
        columns.append(column)
    return columns


def streak_days(counts: Mapping[str, int], today: date) -> tuple[int, int]:
    """``(current, longest)`` runs of consecutive days with at least one AC.

    The current streak counts back from ``today`` and is 0 when today itself has
    no activity: a streak that survives a missed day is not a streak. Entries with
    unparsable dates are ignored rather than crashing the whole count.
    """
    active: list[date] = []
    for day, count in counts.items():
        if count <= 0:
            continue
        try:
            active.append(date.fromisoformat(str(day)))
        except (TypeError, ValueError):
            continue
    if not active:
        return 0, 0
    active.sort()
    longest = 1
    run = 1
    for previous, current in zip(active, active[1:]):
        run = run + 1 if (current - previous).days == 1 else 1
        longest = max(longest, run)
    present = set(active)
    current_streak = 0
    cursor = today
    while cursor in present:
        current_streak += 1
        cursor -= timedelta(days=1)
    return current_streak, longest


__all__ = [
    "DEFAULT_WEEKS",
    "compute_trend",
    "daily_ac_counts",
    "heatmap_grid",
    "heatmap_levels",
    "local_date",
    "parse_iso",
    "streak_days",
    "week_key",
    "week_start",
]
