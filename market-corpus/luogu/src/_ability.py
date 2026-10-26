"""Evidence-weighted ability estimate on the Luogu difficulty scale.

Not every accepted submission says the same thing: a first-try solve of a hard
problem last week is strong evidence, while an easy problem cracked after seven
attempts two months ago is weak evidence. So each solved problem becomes a
weighted vote for "the difficulty this user handles", those votes are reduced
robustly (weighted median, with lone hard outliers damped), an AC-rate
correction nudges the result, and a bounded recalibration step keeps a single
day's practice from moving the number much.

The estimate is expressed in Luogu difficulty units (1..8) because that is the
scale the plugin's own data carries: submissions and candidate problems both
report ``difficulty`` as 0..8, never a Codeforces rating.

Everything here is arithmetic over plain records — no SDK, no network, no clock
(``now`` is a parameter).
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from ._models import AbilityEstimate, SubmissionRecord
from ._trend import parse_iso

# --- Evidence weighting -------------------------------------------------------
HALF_LIFE_DAYS = 21.0        # a solve three weeks old counts half as much
ATTEMPT_PENALTY = 0.35       # per attempt beyond the first
MIN_ATTEMPT_FACTOR = 0.4     # even a hard-won solve counts for something
UNKNOWN_AGE_FRACTION = 0.5   # records with no usable timestamp count as this fresh
# --- Robust reduction --------------------------------------------------------
MIN_SAMPLES_FOR_MEDIAN = 5   # under this a median is jumpier than a mean
OUTLIER_MARGIN = 2.0         # units above the centre before a vote gets damped
OUTLIER_FACTOR = 0.5
# --- Performance correction --------------------------------------------------
ADJUST_MIN_ATTEMPTS = 8      # the AC-rate correction needs at least this much signal
ADJUST_SCALE = 1.5           # how strongly a rate deviation moves difficulty units
ADJUST_CAP = 0.5             # never correct by more than this
# --- Scale and recalibration -------------------------------------------------
LEVEL_MIN = 1.0
LEVEL_MAX = 8.0
DEFAULT_LEVEL = 2.0          # only used when there is nothing at all to go on
CONFIDENT_SAMPLES = 20.0     # weighted samples that count as full confidence
RECALIBRATION_RATE = 0.4     # fraction of the way to a new target per update
RECALIBRATION_CAP = 0.5      # hardest single-step move, in difficulty units


def recency_weight(age_days: float, *, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """Exponential recency decay: 0.5 at one half-life, approaching 0 after."""
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (max(0.0, float(age_days)) / half_life_days)


def attempt_factor(attempts: int) -> float:
    """Weight of a solve as its attempt count grows."""
    extra = max(0, int(attempts) - 1)
    return max(MIN_ATTEMPT_FACTOR, 1.0 / (1.0 + ATTEMPT_PENALTY * extra))


def difficulty_valid(difficulty: int) -> bool:
    """Whether a difficulty can carry evidence (0 = 暂无评定, i.e. unrated)."""
    return LEVEL_MIN <= float(difficulty) <= LEVEL_MAX


def weighted_median(pairs: Sequence[tuple[float, float]]) -> float | None:
    """Value that splits the total weight in half (``None`` when there is none)."""
    items = [(value, weight) for value, weight in pairs if weight > 0]
    if not items:
        return None
    items.sort(key=lambda item: item[0])
    half = sum(weight for _, weight in items) / 2.0
    running = 0.0
    for index, (value, weight) in enumerate(items):
        running += weight
        if running >= half:
            # A split landing exactly between two votes is averaged, so an even
            # division does not snap to whichever problems happened to be listed
            # first.
            if running == half and index + 1 < len(items):
                return (value + items[index + 1][0]) / 2.0
            return value
    return items[-1][0]


def weighted_mean(pairs: Sequence[tuple[float, float]]) -> float | None:
    """Mean of the values, each counted ``weight`` times (``None`` when empty)."""
    scored = [(value, weight) for value, weight in pairs if weight > 0]
    total = sum(weight for _, weight in scored)
    if total <= 0:
        return None
    return sum(value * weight for value, weight in scored) / total


def damp_outliers(
    pairs: Sequence[tuple[float, float]],
    centre: float,
    *,
    margin: float = OUTLIER_MARGIN,
    factor: float = OUTLIER_FACTOR,
) -> list[tuple[float, float]]:
    """Halve the votes sitting ``margin`` units *above* the centre.

    One very hard problem among many easier ones should move the estimate, not
    define it. The same damping is not applied downwards: an easy solve is simply
    weak evidence, which the other votes already outvote.
    """
    return [
        (value, weight * factor if value - centre > margin else weight)
        for value, weight in pairs
    ]


def performance_adjustment(
    attempts: int,
    ac_count: int,
    *,
    min_attempts: int = ADJUST_MIN_ATTEMPTS,
) -> float:
    """Correction (difficulty units) implied by the window's AC rate.

    A rate below half suggests the estimate is optimistic and a rate above half
    that it is pessimistic. Under ``min_attempts`` submissions the rate is noise,
    so the correction is exactly 0 rather than a wild guess.
    """
    if attempts < min_attempts or attempts <= 0:
        return 0.0
    return clamp((ac_count / attempts - 0.5) * ADJUST_SCALE, -ADJUST_CAP, ADJUST_CAP)


def recalibrate(
    previous: float,
    target: float,
    weighted_samples: float,
    *,
    rate: float = RECALIBRATION_RATE,
    cap: float = RECALIBRATION_CAP,
) -> float:
    """Move ``previous`` part-way to ``target`` in one bounded step.

    The step is scaled by how much weighted evidence the window holds, and
    capped — a strong day moves the number, a single lucky problem does not.
    """
    confidence = min(1.0, weighted_samples / CONFIDENT_SAMPLES)
    step = clamp((target - previous) * rate * confidence, -cap, cap)
    return clamp(previous + step, LEVEL_MIN, LEVEL_MAX)


def clamp(value: float, low: float, high: float) -> float:
    """Constrain ``value`` to ``[low, high]``."""
    return max(low, min(high, value))


def collect_evidence(
    records: Sequence[SubmissionRecord],
    *,
    now: datetime,
    window_days: int,
) -> list[tuple[float, float]]:
    """``(difficulty, weight)`` for every rated solve inside the window."""
    evidence: list[tuple[float, float]] = []
    for record in records:
        if not record.solved or not difficulty_valid(record.difficulty):
            continue
        age = record_age_days(record.submitted_at, now, window_days)
        if age is None:
            continue
        evidence.append(
            (float(record.difficulty), recency_weight(age) * attempt_factor(record.attempt_count))
        )
    return evidence


def record_age_days(value: object, now: datetime, window_days: int) -> float | None:
    """Age of a submission in days, or ``None`` when it falls outside the window.

    A missing or unparsable timestamp does not drop the evidence — it is counted
    as half a window old, i.e. neither fresh nor stale, because "we do not know
    when" is not a reason to pretend the solve never happened.
    """
    stamp = parse_iso(value)
    if stamp is None:
        return window_days * UNKNOWN_AGE_FRACTION
    age = (now - stamp).total_seconds() / 86400.0
    if age < 0 or age > window_days:
        return None
    return age


def record_in_window(value: object, now: datetime, window_days: int) -> bool:
    """Whether a submission counts toward the window's attempt/AC totals."""
    return record_age_days(value, now, window_days) is not None


def reduce_evidence(pairs: Sequence[tuple[float, float]]) -> float:
    """Centre of the evidence: weighted median when there is enough of it."""
    if not pairs:
        raise ValueError("no evidence to reduce")
    if sum(weight for _, weight in pairs) >= MIN_SAMPLES_FOR_MEDIAN:
        median = weighted_median(pairs)
        if median is not None:
            return median
    mean = weighted_mean(pairs)
    if mean is None:
        raise ValueError("evidence carries no weight")
    return mean


def estimate_ability(
    records: Sequence[SubmissionRecord],
    *,
    now: datetime,
    window_days: int = 60,
    previous: float | None = None,
) -> AbilityEstimate:
    """Estimate the difficulty the user solves comfortably, in Luogu units.

    ``previous`` is the level estimated on an earlier run: when given, the result
    only moves part of the way toward today's target, which is what keeps the
    number stable across days.
    """
    window = [record for record in records if record_in_window(record.submitted_at, now, window_days)]
    attempts = len(window)
    ac_count = sum(1 for record in window if record.solved)
    ac_rate = round(ac_count / attempts, 4) if attempts else 0.0
    evidence = collect_evidence(window, now=now, window_days=window_days)
    weighted_samples = sum(weight for _, weight in evidence)

    if not evidence:
        starting = previous if previous is not None else DEFAULT_LEVEL
        return AbilityEstimate(
            level=round(clamp(starting, LEVEL_MIN, LEVEL_MAX), 2),
            window_days=window_days,
            ac_count=ac_count,
            ac_rate=ac_rate,
            previous_level=round(previous, 2) if previous is not None else 0.0,
            reason="窗口内没有带难度评级的通过题目，无法给出估算。",
        )

    centre = reduce_evidence(evidence)
    base = reduce_evidence(damp_outliers(evidence, centre))
    adjustment = performance_adjustment(attempts, ac_count)
    target = clamp(base + adjustment, LEVEL_MIN, LEVEL_MAX)
    recalibrated = previous is not None
    level = recalibrate(previous, target, weighted_samples) if recalibrated else target

    reason = (
        f"最近 {window_days} 天：{len(evidence)} 道已评级 AC（加权样本 {weighted_samples:.1f}）"
        f"，难度中心 {base:.2f}，通过率修正 {adjustment:+.2f}"
    )
    if recalibrated:
        reason += f"，按上一档 {previous:.2f} 缓慢校准"

    return AbilityEstimate(
        level=round(level, 2),
        base=round(base, 2),
        adjustment=round(adjustment, 2),
        window_days=window_days,
        ac_count=ac_count,
        ac_rate=ac_rate,
        weighted_samples=round(weighted_samples, 2),
        confidence=round(min(1.0, weighted_samples / CONFIDENT_SAMPLES), 2),
        previous_level=round(previous, 2) if previous is not None else 0.0,
        recalibrated=recalibrated,
        reason=reason,
    )


__all__ = [
    "DEFAULT_LEVEL",
    "LEVEL_MAX",
    "LEVEL_MIN",
    "attempt_factor",
    "clamp",
    "collect_evidence",
    "damp_outliers",
    "difficulty_valid",
    "estimate_ability",
    "performance_adjustment",
    "recalibrate",
    "recency_weight",
    "reduce_evidence",
    "weighted_mean",
    "weighted_median",
]
