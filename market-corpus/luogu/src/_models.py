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


__all__ = [
    "LuoguProblem",
    "ProblemMeta",
    "SubmissionRecord",
    "ContestItem",
    "TagWeakness",
    "GrowthReport",
]
