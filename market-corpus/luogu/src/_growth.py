"""Pure-function growth analysis and daily problem-set selection.

These functions take parsed submission/record data and a candidate problem pool
and produce a structured growth report plus a deterministic daily plan. They do
an explicit ``random.Random(daily_seed)`` so a given day yields the same plan
across restarts while varying day-to-day.

No SDK, no network, no ``_client`` — fully unit-testable.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import date
from typing import Sequence

from ._models import GrowthReport, ProblemMeta, SubmissionRecord, TagWeakness

_WEAK_SEVERITY_SCALE = 4.0


def analyze_growth(
    submissions: Sequence[SubmissionRecord],
    *,
    weak_attempt_threshold: int = 3,
    recent_ac_span: int = 7,
) -> GrowthReport:
    """Aggregate a user's submissions into tag-level weakness insights."""

    attempts: Counter[str] = Counter()
    ac_count: Counter[str] = Counter()
    pending_attempts: Counter[str] = Counter()  # tag -> attempts not (yet) solved

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
            if record.solved:
                ac_count[tag] += 1
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
    )


def _build_suggestion(
    *,
    ac_total: int,
    total: int,
    ac_rate: float,
    weak_tags: Sequence[TagWeakness],
    stubborn: Sequence[SubmissionRecord],
) -> str:
    parts: list[str] = []
    if total:
        parts.append(f"累计提交 {total} 次，通过 {ac_total} 题，通过率 {ac_rate:.0%}。")
    if weak_tags:
        names = "、".join(item.tag for item in weak_tags[:3])
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


__all__ = ["analyze_growth", "select_daily_problems"]
