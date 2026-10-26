"""Pure-function growth analysis and daily problem-set selection.

These functions take parsed submission/record data and a candidate problem pool
and produce a structured growth report plus a deterministic daily plan. They do
an explicit ``random.Random(daily_seed)`` so a given day yields the same plan
across restarts while varying day-to-day.

Tag values here are whatever the caller parsed (numeric Luogu ids in production).
``analyze_growth`` renders them for the human-readable suggestion through the
injected ``tag_label`` callable, so this module stays free of the tag dictionary.

Weakness is reported two ways — absolute (never accepted, attempted often) and
relative (accepted less often than this user usually manages) — because they
catch different problems: the first finds a wall, the second finds a hole.

Daily selection keeps a cooldown history so tomorrow's set is not today's set
repeated; the ladder relaxes only as far as it must.

No SDK, no network, no ``_client`` — fully unit-testable.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from ._models import (
    DailySelection,
    DifficultyStat,
    GrowthReport,
    ProblemMeta,
    SubmissionRecord,
    TagStat,
    TagWeakness,
)

_WEAK_SEVERITY_SCALE = 4.0


def analyze_growth(
    submissions: Sequence[SubmissionRecord],
    *,
    weak_attempt_threshold: int = 3,
    recent_ac_span: int = 7,
    tag_label: Callable[[str], str] = str,
    min_relative_gap: float = 0.15,
) -> GrowthReport:
    """Aggregate a user's submissions into tag-level weakness insights.

    ``tag_label`` renders a tag value for the suggestion prose only; the
    structured ``GrowthReport`` keeps the caller's original tag values so the
    daily selector can still match them against candidate problems.

    Two different weakness notions are reported, because "never solved" and
    "solved less often than this user usually does" are different problems:
    ``weak_tags`` keeps the absolute rules (never accepted, attempted often
    enough) while ``relative_weak_tags`` compares each tag's AC rate against the
    user's own overall rate and flags those trailing it by ``min_relative_gap``.
    """
    # Attempt-weighted: one record can stand for many tries, which is what makes a
    # repeatedly-failed problem a strong weak-tag signal.
    attempts: Counter[str] = Counter()
    ac_count: Counter[str] = Counter()
    pending_attempts: Counter[str] = Counter()  # tag -> attempts not (yet) solved
    # Unweighted, one count per record: only rates built this way are comparable
    # with the overall AC rate, which is also one count per record.
    record_attempts: Counter[str] = Counter()
    record_ac: Counter[str] = Counter()

    total_submitted = len(submissions)
    ac_total = 0
    wa_total = 0

    for record in submissions:
        if record.solved:
            ac_total += 1
        elif record.status.upper() in {"WA", "TLE", "MLE", "RE", "CE", "OLE"}:
            wa_total += 1
        # A single record may represent a problem attempted many times; weight a
        # tag's attempt count by that so a repeatedly-failed problem surface as
        # a strong weak-tag signal.
        weight = max(1, int(record.attempt_count))
        for tag in record.tags:
            attempts[tag] += weight
            record_attempts[tag] += 1
            if record.solved:
                ac_count[tag] += 1
                record_ac[tag] += 1
            else:
                pending_attempts[tag] += weight

    # --- Weak tags: never solved but attempted often -------------------
    # ``last7_attempts`` is a proxy for "recently still fighting this tag" —
    # the count of not-yet-solved attempts. We do not currently parse Luogu's
    # per-submission timestamp into a calendar window, so this is the honest
    # available signal; the field name is kept for interface stability.
    weak_tags: list[TagWeakness] = []
    for tag, count in attempts.items():
        solved = ac_count[tag]
        if solved == 0 and count >= weak_attempt_threshold:
            severity = min(1.0, count / (count + _WEAK_SEVERITY_SCALE))
            weak_tags.append(
                TagWeakness(
                    tag=tag,
                    attempts=count,
                    ac_count=solved,
                    last7_attempts=pending_attempts[tag],
                    severity=round(severity, 4),
                )
            )
    weak_tags.sort(key=lambda item: (-item.severity, item.tag))

    # --- Never-solved tags, sorted for stable output -------------------
    untouched = sorted(
        (tag for tag in attempts if ac_count[tag] == 0),
        key=lambda t: (-attempts[t], t),
    )

    ac_rate = (ac_total / total_submitted) if total_submitted else 0.0

    # --- Rates vs the user's own average -------------------------------
    tag_stats = _tag_stats(record_attempts, record_ac, ac_rate)
    difficulty_stats = _difficulty_stats(submissions, ac_rate)
    relative_weak_tags = tuple(
        stat
        for stat in tag_stats
        if stat.attempts >= weak_attempt_threshold and stat.relative_gap >= min_relative_gap
    )

    # --- Recent AC + stubborn problems --------------------------------
    recent_ac = tuple(sorted(
        (record for record in submissions if record.solved),
        key=lambda r: r.submitted_at,
        reverse=True,
    )[:recent_ac_span])
    most_attempted_unac = tuple(sorted(
        (record for record in submissions if not record.solved),
        key=lambda r: r.attempt_count,
        reverse=True,
    )[:5])

    suggestion = _build_suggestion(
        ac_total=ac_total,
        total=total_submitted,
        ac_rate=ac_rate,
        weak_tags=weak_tags,
        stubborn=most_attempted_unac,
        tag_label=tag_label,
    )

    return GrowthReport(
        total_attempted=total_submitted,
        ac_count=ac_total,
        wa_count=wa_total,
        ac_rate=round(ac_rate, 4),
        solved_tags=frozenset(ac_count.keys()),
        weak_tags=tuple(weak_tags),
        untouched_tags_sorted=tuple(untouched),
        recent_ac=recent_ac,
        most_attempted_unac=most_attempted_unac,
        suggestion=suggestion,
        tag_stats=tag_stats,
        difficulty_stats=difficulty_stats,
        relative_weak_tags=relative_weak_tags,
    )


def _tag_stats(
    attempts: Counter[str],
    ac_count: Counter[str],
    overall_rate: float,
) -> tuple[TagStat, ...]:
    """Per-tag rates, sorted by how far each trails the user's own average."""
    stats: list[TagStat] = []
    for tag, count in attempts.items():
        rate = ac_count[tag] / count if count else 0.0
        stats.append(
            TagStat(
                tag=tag,
                attempts=count,
                ac_count=ac_count[tag],
                ac_rate=round(rate, 4),
                relative_gap=round(overall_rate - rate, 4),
            )
        )
    stats.sort(key=lambda stat: (-stat.relative_gap, -stat.attempts, stat.tag))
    return tuple(stats)


def _difficulty_stats(
    submissions: Sequence[SubmissionRecord],
    overall_rate: float,
) -> tuple[DifficultyStat, ...]:
    """Per-difficulty-band rates; unrated rows (0) are skipped, not guessed at."""
    attempts: Counter[int] = Counter()
    ac_count: Counter[int] = Counter()
    for record in submissions:
        if record.difficulty <= 0:
            continue
        attempts[record.difficulty] += 1
        if record.solved:
            ac_count[record.difficulty] += 1
    stats: list[DifficultyStat] = []
    for difficulty in sorted(attempts):
        count = attempts[difficulty]
        rate = ac_count[difficulty] / count if count else 0.0
        stats.append(
            DifficultyStat(
                difficulty=difficulty,
                attempts=count,
                ac_count=ac_count[difficulty],
                ac_rate=round(rate, 4),
                relative_gap=round(overall_rate - rate, 4),
            )
        )
    return tuple(stats)


def _build_suggestion(
    *,
    ac_total: int,
    total: int,
    ac_rate: float,
    weak_tags: Sequence[TagWeakness],
    stubborn: Sequence[SubmissionRecord],
    tag_label: Callable[[str], str] = str,
) -> str:
    parts: list[str] = []
    if total:
        parts.append(f"累计提交 {total} 次，通过 {ac_total} 题，通过率 {ac_rate:.0%}。")
    if weak_tags:
        names = "、".join(tag_label(item.tag) for item in weak_tags[:3])
        parts.append(f"薄弱标签：{names}，建议优先补强。")
    if stubborn:
        names = "、".join(f"{record.pid}({record.status})" for record in stubborn[:3])
        parts.append(f"反复未过：{names}，可以看看题解。")
    return " ".join(parts) if parts else "暂无足够数据生成成长建议。"


def select_daily_problems(
    report: GrowthReport,
    candidate_pool: Sequence[ProblemMeta],
    *,
    difficulty_range: tuple[int, int] = (2, 5),
    count: int = 3,
    exclude_pids: frozenset[str] = frozenset(),
    daily_seed: str = "",
) -> list[ProblemMeta]:
    """Pick up to ``count`` problems for a day's problem set.

    Scoring favours problems whose tags overlap the user's weak or untouched
    tags, then applies a small seeded jitter so the set is stable within a day
    but changes across days. Purely deterministic given ``daily_seed``.
    """
    seed = daily_seed or date.today().isoformat()
    rng = random.Random(seed)
    low, high = difficulty_range

    weak_tags = {item.tag for item in report.weak_tags}
    untouched_tags = set(report.untouched_tags_sorted)

    candidates: list[tuple[float, ProblemMeta]] = []
    for problem in candidate_pool:
        if problem.pid in exclude_pids:
            continue
        if not (low <= problem.difficulty <= high):
            continue
        score = 0.0
        problem_tags = set(problem.tags)
        if problem_tags & weak_tags:
            score += 0.6
        if problem_tags & untouched_tags:
            score += 0.3
        # Diversify: seeded jitter in (0, 0.1).
        score += rng.random() * 0.1
        candidates.append((score, problem))

    candidates.sort(key=lambda item: (-item[0], item[1].pid))
    return [problem for _, problem in candidates[:count]]


# ---------------------------------------------------------------------------
# Cooldown: do not offer the same problem day after day
# ---------------------------------------------------------------------------

HISTORY_KEY = "daily_history"
HISTORY_VERSION = 1


@dataclass(frozen=True)
class CooldownTier:
    """One rung of the relaxation ladder: how strictly to avoid recent picks."""

    cooldown_days: int
    exclude_review: bool = True


# Strictest first. The selector walks these until the day's set is full: prefer a
# genuinely fresh problem, repeat a recent one only when the pool cannot fill the
# set, and drop the review-queue exclusion last. An empty problem set helps
# nobody, so relaxing beats failing — but the caller is told which rung was used.
COOLDOWN_LADDER: tuple[CooldownTier, ...] = (
    CooldownTier(14),
    CooldownTier(7),
    CooldownTier(3),
    CooldownTier(0),
    CooldownTier(0, exclude_review=False),
)


def days_between(from_day: str, today: date) -> int | None:
    """Whole days from a stored local date to ``today`` (``None`` if unusable)."""
    try:
        start = date.fromisoformat(str(from_day))
    except (TypeError, ValueError):
        return None
    return (today - start).days


def suppressed_pids(
    history: Mapping[str, str],
    today: date,
    *,
    cooldown_days: int,
    review_pids: frozenset[str] = frozenset(),
) -> frozenset[str]:
    """Pids that must not be offered again yet.

    Problems recommended *today* are deliberately not suppressed: extra picks
    within one day are the refresh/rotate path's business, and blocking them here
    would make "换一批" impossible.
    """
    blocked = set(review_pids)
    if cooldown_days > 0:
        for pid, recommended_on in history.items():
            age = days_between(recommended_on, today)
            if age is not None and 1 <= age <= cooldown_days:
                blocked.add(str(pid))
    return frozenset(blocked)


def record_daily_picks(
    history: Mapping[str, str],
    problems: Sequence[ProblemMeta],
    today: date,
    *,
    keep_days: int = 120,
) -> dict[str, str]:
    """Remember what was offered today, dropping entries too old to matter.

    The history would otherwise grow forever, and a pick older than any rung of
    the ladder can never suppress anything again.
    """
    updated: dict[str, str] = {}
    for pid, day in history.items():
        age = days_between(day, today)
        if age is None or age <= keep_days:
            updated[str(pid)] = str(day)
    stamp = today.isoformat()
    for problem in problems:
        updated[str(problem.pid)] = stamp
    return updated


def load_history(raw: Any) -> dict[str, str]:
    """Rebuild the cooldown history from a stored value (junk becomes empty)."""
    payload = raw
    if hasattr(payload, "value"):  # store get() wrapper
        payload = payload.value
    if not isinstance(payload, Mapping) or payload.get("v") != HISTORY_VERSION:
        return {}
    entries = payload.get("items")
    if not isinstance(entries, Mapping):
        return {}
    return {str(pid): str(day) for pid, day in entries.items() if str(pid) and str(day)}


def dump_history(history: Mapping[str, str]) -> dict[str, Any]:
    """Serialise the cooldown history for the plugin store."""
    return {"v": HISTORY_VERSION, "items": {str(pid): str(day) for pid, day in history.items()}}


def difficulty_band(
    level: float,
    *,
    below: int = 1,
    above: int = 2,
    low: int = 1,
    high: int = 8,
) -> tuple[int, int]:
    """Integer difficulty window around an ability level, clamped to the scale.

    Asymmetric on purpose: practice should stretch upward more than downward. A
    problem well below your level mostly rehearses what you already do, while one
    slightly above it is where the growth is, so the window reaches further up
    than down.
    """
    try:
        centre = int(round(float(level)))
    except (TypeError, ValueError):
        centre = low
    return (max(low, centre - max(0, below)), min(high, centre + max(0, above)))


def select_daily_with_suppression(
    report: GrowthReport,
    candidate_pool: Sequence[ProblemMeta],
    *,
    difficulty_range: tuple[int, int],
    count: int,
    daily_seed: str,
    history: Mapping[str, str] | None = None,
    today: date | None = None,
    review_pids: frozenset[str] = frozenset(),
    extra_exclude: frozenset[str] = frozenset(),
    ladder: Sequence[CooldownTier] = COOLDOWN_LADDER,
) -> DailySelection:
    """Pick the day's problems, relaxing the cooldown only as far as necessary."""
    day = today or date.today()
    wanted = max(1, int(count))
    for index, tier in enumerate(ladder):
        blocked = suppressed_pids(
            history or {},
            day,
            cooldown_days=tier.cooldown_days,
            review_pids=review_pids if tier.exclude_review else frozenset(),
        ) | extra_exclude
        problems = select_daily_problems(
            report,
            candidate_pool,
            difficulty_range=difficulty_range,
            count=wanted,
            exclude_pids=blocked,
            daily_seed=daily_seed,
        )
        if len(problems) >= wanted or index == len(ladder) - 1:
            return DailySelection(
                problems=tuple(problems),
                requested=wanted,
                cooldown_days=tier.cooldown_days,
                exclude_review=tier.exclude_review,
                tier_index=index,
                relaxed=index > 0,
                difficulty_window=(int(difficulty_range[0]), int(difficulty_range[1])),
            )
    return DailySelection(requested=wanted)


def select_daily_with_windows(
    report: GrowthReport,
    candidate_pool: Sequence[ProblemMeta],
    *,
    windows: Sequence[tuple[int, int]],
    count: int,
    daily_seed: str,
    history: Mapping[str, str] | None = None,
    today: date | None = None,
    review_pids: frozenset[str] = frozenset(),
    extra_exclude: frozenset[str] = frozenset(),
    ladder: Sequence[CooldownTier] = COOLDOWN_LADDER,
) -> DailySelection:
    """Pick a day's set: cooldown first, then band width, then repeats.

    The priority is deliberate — a fresh problem in a slightly wider band beats
    repeating one from yesterday's set in the perfect band — so the cooldown
    ladder is the *outer* loop and the difficulty windows the inner one. Nesting
    them the other way round lets a narrow band relax the cooldown all the way to
    zero and hand back exactly yesterday's problems, which is the behaviour the
    cooldown exists to prevent.
    """
    day = today or date.today()
    wanted = max(1, int(count))
    ordered_windows = list(windows) or [(1, 8)]
    selection: DailySelection | None = None
    for tier_index, tier in enumerate(ladder):
        best: DailySelection | None = None
        for window_index, window in enumerate(ordered_windows):
            candidate = select_daily_with_suppression(
                report,
                candidate_pool,
                difficulty_range=window,
                count=wanted,
                daily_seed=daily_seed,
                history=history,
                today=day,
                review_pids=review_pids,
                extra_exclude=extra_exclude,
                ladder=(tier,),
            )
            if best is None or len(candidate.problems) > len(best.problems):
                best = replace(
                    candidate,
                    tier_index=tier_index,
                    relaxed=(tier_index > 0 or window_index > 0),
                    difficulty_window=window,
                )
            if len(best.problems) >= wanted:
                break
        selection = best
        if selection is not None and len(selection.problems) >= wanted:
            break
    return selection if selection is not None else DailySelection(requested=wanted)


__all__ = ["analyze_growth", "select_daily_problems"]
