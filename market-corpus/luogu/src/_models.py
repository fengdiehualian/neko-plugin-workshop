"""Shared immutable dataclasses for the Luogu plugin.

These are pure data containers shared by ``_parsing`` and ``_growth`` so that
both stay free of SDK / I/O and can be unit-tested in isolation. Keep every
field frozen and JSON-serialisable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LuoguProblem:
    """A problem as represented on Luogu (search result / detail)."""

    pid: str
    title: str
    difficulty: int = 0            # 0..8; 0 = unknown
    tags: tuple[str, ...] = ()     # numeric tag ids as decimal strings
    accepted: int = 0
    submitted: int = 0
    solutions: int = 0
    statement: str = ""            # aggregated problem statement text (detail only)
    url: str = ""


@dataclass(frozen=True)
class ProblemMeta:
    """Minimal problem metadata used by the growth / daily-set selector."""

    pid: str
    title: str
    difficulty: int = 0
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubmissionRecord:
    """A single user submission (login-required endpoint)."""

    pid: str
    title: str = ""
    status: str = ""               # e.g. "AC", "WA", "TLE", ...
    difficulty: int = 0
    tags: tuple[str, ...] = ()
    solved: bool = False
    attempt_count: int = 1
    submitted_at: str = ""


@dataclass(frozen=True)
class ContestItem:
    name: str
    start_time: str = ""
    duration: str = ""
    link: str = ""


@dataclass(frozen=True)
class TagWeakness:
    """A tag the user keeps attempting but has not solved enough."""

    tag: str
    attempts: int
    ac_count: int
    last7_attempts: int = 0
    severity: float = 0.0


@dataclass(frozen=True)
class TagStat:
    """Per-tag activity plus how far the tag trails the user's own mean.

    A tag is only *relatively* weak when its AC rate sits below the user's
    overall rate by a margin — a 100 % tag and a 20 % tag are both "fine" for a
    user whose own average is 20 %, and flagging the first would be noise.
    """

    tag: str
    attempts: int
    ac_count: int
    ac_rate: float = 0.0
    relative_gap: float = 0.0


@dataclass(frozen=True)
class DifficultyStat:
    """Per-difficulty activity (a band of the Luogu 1..8 scale)."""

    difficulty: int
    attempts: int
    ac_count: int
    ac_rate: float = 0.0
    relative_gap: float = 0.0


@dataclass(frozen=True)
class TrendWeek:
    """One ISO week of practice, bucketed in the user's local timezone."""

    week: str
    start_date: str
    attempts: int = 0
    ac_count: int = 0
    ac_rate: float = 0.0
    active_days: int = 0


@dataclass(frozen=True)
class TrendReport:
    """Week-by-week activity, with a coarse direction against the user's baseline."""

    weeks: tuple[TrendWeek, ...] = ()
    total_attempts: int = 0
    total_ac: int = 0
    active_weeks: int = 0
    average_ac_rate: float = 0.0
    direction: str = "unknown"
    recent_ac: float = 0.0
    baseline_ac: float = 0.0


@dataclass(frozen=True)
class AbilityEstimate:
    """Evidence-weighted estimate of the difficulty this user solves comfortably.

    Expressed in Luogu difficulty units (1..8) because that is the scale the
    plugin's data actually carries — the submissions and every candidate problem
    report 0..8, not a Codeforces rating.
    """

    level: float = 0.0
    base: float = 0.0
    adjustment: float = 0.0
    window_days: int = 0
    ac_count: int = 0
    ac_rate: float = 0.0
    weighted_samples: float = 0.0
    confidence: float = 0.0
    previous_level: float = 0.0
    recalibrated: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ReviewItem:
    """A problem scheduled for spaced re-practice.

    ``tags`` holds display names rather than the numeric ids used elsewhere: the
    queue is read by humans and by the AI, and nothing selects on it.
    """

    pid: str
    title: str = ""
    stage: int = 0
    due_on: str = ""  # local date, YYYY-MM-DD
    added_on: str = ""
    last_reviewed_on: str = ""
    reviews: int = 0
    lapses: int = 0
    difficulty: int = 0
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class DailySelection:
    """Problems chosen for one day, plus how far the selector had to relax.

    ``relaxed`` means the selector had to give something up — either widening the
    difficulty window or loosening the cooldown — and ``cooldown_days`` /
    ``difficulty_window`` say which rules actually produced the set, so a caller
    can report that instead of pretending the set was fresh and on target.
    """

    problems: tuple[ProblemMeta, ...] = ()
    requested: int = 0
    cooldown_days: int = 0
    exclude_review: bool = True
    tier_index: int = 0
    relaxed: bool = False
    difficulty_window: tuple[int, int] = ()


@dataclass(frozen=True)
class GrowthReport:
    """Aggregated growth analysis over a user's recent submissions."""

    total_attempted: int = 0
    ac_count: int = 0
    wa_count: int = 0
    ac_rate: float = 0.0
    solved_tags: frozenset[str] = frozenset()
    weak_tags: tuple[TagWeakness, ...] = ()
    untouched_tags_sorted: tuple[str, ...] = ()
    recent_ac: tuple[SubmissionRecord, ...] = ()
    most_attempted_unac: tuple[SubmissionRecord, ...] = ()
    suggestion: str = ""
    tag_stats: tuple[TagStat, ...] = ()
    difficulty_stats: tuple[DifficultyStat, ...] = ()
    relative_weak_tags: tuple[TagStat, ...] = ()


__all__ = [
    "LuoguProblem",
    "ProblemMeta",
    "SubmissionRecord",
    "ContestItem",
    "TagWeakness",
    "TagStat",
    "DifficultyStat",
    "TrendWeek",
    "TrendReport",
    "AbilityEstimate",
    "ReviewItem",
    "DailySelection",
    "GrowthReport",
]
